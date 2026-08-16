"""Character <-> Census sync, snapshot history, and the spell/item caches.

sync_character() is the one entry point (manual Refresh, nightly job). It takes
the client as a parameter so tests drive it with recorded fixtures — no live
Census calls in CI.

Snapshots: we store a TRIMMED character doc (identity/stats/gear/spells/AA
points — not quests/collections/achievements, which are huge and irrelevant to
coaching) and only when Census's own last_update moved, so history rows mean
"the character actually changed" and gear diffs stay cheap.
"""

import json
import re
import time

from census import catalog
from census.effects import parse_effects
from db import json_dumps

# What a snapshot keeps from the character doc. Everything the Character page
# and future gear/spell diffs need; nothing that bloats the row.
TRIM_KEYS = (
    "id", "name", "displayname", "type", "guild", "locationdata", "playedtime",
    "stats", "resists", "spell_list", "equipmentslot_list",
    "alternateadvancements", "last_update", "crc",
)

_ROMAN = re.compile(r"^(.*\S)\s+([IVXLCDM]+)$")


def base_name(name: str) -> str:
    """'Soulrot VI' -> 'Soulrot' (damage log lines drop the numeral)."""
    m = _ROMAN.match(name or "")
    return m.group(1) if m else name


def _trim(doc: dict) -> dict:
    return {k: doc[k] for k in TRIM_KEYS if k in doc}


def _equipped_item_ids(doc: dict) -> list[int]:
    ids = []
    for slot in doc.get("equipmentslot_list") or []:
        item = slot.get("item") or {}
        item_id = item.get("id")
        if item_id:
            ids.append(item_id)
        # Lexicon's useful move is showing the named adornments beside their
        # host item.  Census puts those ids inside the slot rather than in the
        # character's main equipment list, so include them in the same bounded
        # item-cache fill as the host gear.
        ids.extend(a["id"] for a in (item.get("adornment_list") or [])
                   if a.get("id"))
    return ids


def typed_fields(rec: dict, effects: list[dict]) -> dict:
    """Queryable numbers from a spell record: timing/cost from the typed Census
    fields, damage from the parsed effect grammar (the typed fields carry no
    amounts). The primary damage effect is the one with the largest midpoint —
    a DoT's initial hit over its tick."""
    dmg = max((e for e in effects
               if e.get("kind") == "damage" and e.get("min") is not None),
              key=lambda e: e["min"] + e["max"], default=None)
    return {
        "cast_s": (rec.get("cast_secs_hundredths") or 0) / 100,
        "recast_s": rec.get("recast_secs") or 0,
        # the census field is NAMED _tenths but stores hundredths: every spell
        # carries 50, and EQ2's universal recovery is 0.5s, not 5s
        "recovery_s": (rec.get("recovery_secs_tenths") or 0) / 100,
        "duration_s": ((rec.get("duration") or {}).get("max_sec_tenths") or 0) / 10,
        "power_cost": (rec.get("cost") or {}).get("power") or 0,
        "dmg_min": dmg["min"] if dmg else None,
        "dmg_max": dmg["max"] if dmg else None,
        "dmg_dtype": dmg.get("dtype") if dmg else None,
        "dmg_period_s": dmg.get("period_s") if dmg else None,
    }


def _spell_rows(recs, now: int) -> list[tuple]:
    rows = []
    for rec in recs:
        name = rec.get("name") or ""
        classes = ",".join(sorted((rec.get("classes") or {}).keys()))
        effects = parse_effects(rec.get("effect_list"))
        t = typed_fields(rec, effects)
        rows.append((rec["id"], name, base_name(name), rec.get("crc"), classes,
                     rec.get("level"), rec.get("tier"), rec.get("tier_name"),
                     json_dumps(rec), json_dumps(effects),
                     t["cast_s"], t["recast_s"], t["recovery_s"], t["duration_s"],
                     t["power_cost"], t["dmg_min"], t["dmg_max"], t["dmg_dtype"],
                     t["dmg_period_s"], now))
    return rows


_SPELL_INSERT = (
    "INSERT OR REPLACE INTO census_spells "
    "(spell_id, name, base_name, crc, class, level, tier, tier_name, "
    " json, parsed_effects, cast_s, recast_s, recovery_s, duration_s, "
    " power_cost, dmg_min, dmg_max, dmg_dtype, dmg_period_s, fetched_ts) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def ensure_spells(conn, client, spell_ids: list[int]) -> int:
    """Fetch + cache any spell ids not already in census_spells. Returns count."""
    spell_ids = sorted(set(spell_ids))
    if not spell_ids:
        return 0
    have = {r[0] for r in conn.execute(
        f"SELECT spell_id FROM census_spells WHERE spell_id IN "
        f"({','.join('?' * len(spell_ids))})", spell_ids)}
    missing = [i for i in spell_ids if i not in have]
    if not missing:
        return 0
    recs = client.spells_by_ids(missing)
    rows = _spell_rows(recs, int(time.time()))
    with conn:
        conn.executemany(_SPELL_INSERT, rows)
        catalog.upsert_from_spells(conn, recs)
    return len(rows)


def ensure_spell_lines(conn, client, crcs: list[int]) -> int:
    """Cache EVERY tier of the given spell lines (crc = one spell version).
    The coach's tier-upgrade advice compares the scribed tier against tiers the
    character does not have, which ensure_spells never fetches. A settings
    marker per crc keeps this from refetching lines on every report."""
    crcs = sorted({c for c in crcs if c})
    if not crcs:
        return 0
    todo = [c for c in crcs if conn.execute(
        "SELECT 1 FROM settings WHERE key=?", (f"spell_line:{c}",)).fetchone() is None]
    if not todo:
        return 0
    now = int(time.time())
    recs = client.spells_by_crcs(todo)
    rows = _spell_rows(recs, now)
    # only mark lines that actually came back — a Census hiccup on one crc must
    # not permanently skip that line's upgrades
    got = {rec.get("crc") for rec in recs}
    with conn:
        conn.executemany(_SPELL_INSERT, rows)
        catalog.upsert_from_spells(conn, recs)
        conn.executemany(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            [(f"spell_line:{c}", str(now)) for c in todo if c in got])
    return len(rows)


# the 24 adventure classes of the EoF era (no beastlord/channeler on TLE);
# lowercase = Census's classes{} keys, all verified to return spell rows
ALL_CLASSES = (
    "assassin", "berserker", "brigand", "bruiser", "coercer", "conjuror",
    "defiler", "dirge", "fury", "guardian", "illusionist", "inquisitor",
    "monk", "mystic", "necromancer", "paladin", "ranger", "shadowknight",
    "swashbuckler", "templar", "troubador", "warden", "warlock", "wizard",
)


INGEST_PAGE_SLEEP_S = 2  # 30 on s:example — its burst limit trips mid-class


def ingest_class_spells(conn, client, cls: str, max_level: int,
                        page_sleep_s: float = INGEST_PAGE_SLEEP_S) -> dict:
    """Bulk-cache every spell record (all tiers) scribable by a class at or
    below max_level. Unlike ensure_spells/ensure_spell_lines this is proactive:
    the coach and spell browser get the full class book without waiting for a
    character to scribe things.

    Every page is persisted as it lands and the offset is stored in settings,
    because the s:example burst throttle cuts off mid-class: a restarted-from-
    zero fetch could never finish a book bigger than the burst budget. Only
    the empty page that proves the end clears the offset and writes the
    spell_line:{crc} completeness markers (for every line of the class book,
    including pages from earlier partial runs)."""
    key = f"ingest_progress:{cls}:{max_level}"
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    start = int(row[0]) if row else 0
    fetched = 0
    while True:
        recs = client.spell_page(cls, max_level, start)
        now = int(time.time())
        if not recs:
            break
        with conn:
            conn.executemany(_SPELL_INSERT, _spell_rows(recs, now))
            catalog.upsert_from_spells(conn, recs)
            conn.execute("INSERT OR REPLACE INTO settings (key, value) "
                         "VALUES (?,?)", (key, str(start + len(recs))))
        start += len(recs)
        fetched += len(recs)
        time.sleep(page_sleep_s)
    crcs = [r[0] for r in conn.execute(
        "SELECT DISTINCT crc FROM census_spells WHERE crc IS NOT NULL "
        "AND (','||class||',') LIKE ?", (f"%,{cls},%",))]
    with conn:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
        conn.executemany(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            [(f"spell_line:{c}", str(int(time.time()))) for c in sorted(crcs)])
    return {"class": cls, "spells": start, "fetched": fetched,
            "lines": len(crcs)}


def backfill_typed_columns(conn) -> int:
    """Populate the typed columns on census_spells rows cached before the
    columns existed (cast_s is never NULL on a post-migration insert)."""
    rows = conn.execute(
        "SELECT spell_id, json, parsed_effects FROM census_spells "
        "WHERE cast_s IS NULL AND json IS NOT NULL").fetchall()
    updates = []
    for r in rows:
        t = typed_fields(json.loads(r["json"]),
                         json.loads(r["parsed_effects"] or "[]"))
        updates.append((t["cast_s"], t["recast_s"], t["recovery_s"],
                        t["duration_s"], t["power_cost"], t["dmg_min"],
                        t["dmg_max"], t["dmg_dtype"], t["dmg_period_s"],
                        r["spell_id"]))
    with conn:
        conn.executemany(
            "UPDATE census_spells SET cast_s=?, recast_s=?, recovery_s=?, "
            "duration_s=?, power_cost=?, dmg_min=?, dmg_max=?, dmg_dtype=?, "
            "dmg_period_s=? WHERE spell_id=?", updates)
    return len(updates)


def ensure_items(conn, client, item_ids: list[int]) -> int:
    """Fetch + cache any equipped item ids not already in census_items."""
    item_ids = sorted(set(item_ids))
    if not item_ids:
        return 0
    have = {r[0] for r in conn.execute(
        f"SELECT item_id FROM census_items WHERE item_id IN "
        f"({','.join('?' * len(item_ids))})", item_ids)}
    missing = [i for i in item_ids if i not in have]
    if not missing:
        return 0
    now = int(time.time())
    rows = [(rec["id"], rec.get("displayname"), rec.get("tier"),
             json_dumps(rec), now) for rec in client.items_by_ids(missing)]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO census_items "
            "(item_id, displayname, tier, json, fetched_ts) VALUES (?,?,?,?,?)", rows)
    return len(rows)


def sync_character(conn, client, character_id: int) -> dict:
    char = conn.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone()
    if char is None:
        return {"found": False, "error": "no such character"}
    doc = client.character_by_name(char["name"], char["world_id"])
    now = int(time.time())
    if doc is None:
        with conn:
            conn.execute("UPDATE characters SET last_census_ts=? WHERE id=?",
                         (now, character_id))
        return {"found": False,
                "error": f"{char['name']} not found in Census (character privacy "
                         "must be set visible in-game)"}
    trimmed = _trim(doc)
    ctype = doc.get("type") or {}
    last = conn.execute(
        "SELECT id, json FROM census_char_snapshots WHERE character_id=? "
        "ORDER BY fetched_ts DESC, id DESC LIMIT 1", (character_id,)).fetchone()
    changed = True
    if last is not None:
        prev = json.loads(last["json"])
        changed = prev.get("last_update") != trimmed.get("last_update")
    snapshot_id = last["id"] if last is not None else None
    with conn:
        conn.execute(
            "UPDATE characters SET class=?, level=?, census_character_id=?, "
            "last_census_ts=? WHERE id=?",
            (ctype.get("class"), ctype.get("level"), doc.get("id"), now, character_id))
        if changed:
            snapshot_id = conn.execute(
                "INSERT INTO census_char_snapshots (character_id, fetched_ts, json) "
                "VALUES (?,?,?)",
                (character_id, now, json_dumps(trimmed))).lastrowid
    spells_fetched = ensure_spells(conn, client, doc.get("spell_list") or [])
    items_fetched = ensure_items(conn, client, _equipped_item_ids(doc))
    return {"found": True, "changed": changed, "snapshot_id": snapshot_id,
            "spells_fetched": spells_fetched, "items_fetched": items_fetched}


def refresh_stale(conn, client, max_age_s: int = 86400) -> int:
    """Sync every OWNED character whose last sync is older than max_age_s.
    The nightly loop calls this hourly; errors on one character don't stop the
    rest. Returns how many characters were synced."""
    cutoff = int(time.time()) - max_age_s
    rows = conn.execute(
        "SELECT id FROM characters WHERE user_id IS NOT NULL AND "
        "(last_census_ts IS NULL OR last_census_ts < ?)", (cutoff,)).fetchall()
    synced = 0
    for row in rows:
        try:
            sync_character(conn, client, row["id"])
            synced += 1
        except Exception:
            import logging
            logging.getLogger("census").exception("nightly sync failed for character %s",
                                                  row["id"])
        time.sleep(1)  # be polite to Census between characters
    return synced


# ---- display payloads (no Census calls — cache/snapshot reads only) ----

KEY_STATS = (
    # (label, path, is_pct) — the coaching-era stat panel; no fervor/critbonus on TLE
    ("Ability Mod", ("combat", "abilitymod"), False),
    ("Base Modifier", ("combat", "basemodifier"), True),
    ("Crit Chance", ("combat", "critchance"), True),
    ("Cast Speed", ("ability", "spelltimecastpct"), True),
    ("Reuse Speed", ("ability", "spelltimereusepct"), True),
    ("Recovery", ("ability", "spelltimerecoverypct"), True),
    ("DPS Mod", ("combat", "dps"), True),
    ("Haste", ("combat", "attackspeed"), True),
)


# Census's character totals and item modifiers use a different vocabulary from
# the planner catalog.  This is the exact bridge between them: the character
# window starts with Census's totals, then the planner can subtract the item in
# one equipment slot and add the candidate in the same keys.  Keep this narrow
# to additive item numbers.  Procs, set bonuses, adornments and caps are not
# arithmetic and the page says so rather than pretending they are.
_ITEM_PLAN_KEYS = {
    "basemodifier": "potency",
    "critchance": "crit",
    "all": "abmod",                 # Census's name for Ability Mod on items
    "doubleattackchance": "multi",
    "dps": "dps",
    "attackspeed": "aspeed",
    "spelltimecastpct": "acspeed",
    "spelltimereusepct": "arspeed",
    "flurry": "flurry",
    "aeautoattackchance": "aeauto",
    "hategainmod": "hategain",
    "strikethrough": "strike",
    "blockchance": "bchance",
    "maxhpperc": "maxhealth",
    "armormitigationincrease": "mitinc",
    "accuracy": "accuracy",
    "strength": "str",
    "stamina": "sta",
    "agility": "agi",
    "wisdom": "wis",
    "intelligence": "int",
    "combatskills": "comskills",
    "elemental": "vselemental",
    "arcane": "vsarcane",
    "noxious": "vsnoxious",
}


def planner_item_stats(rec: dict) -> dict[str, float]:
    """One cached Census item in the planner's stat vocabulary.

    The item document is already fetched as part of character sync.  This is a
    pure read/translation and deliberately does not widen the planner request
    path into a Census request.
    """
    out: dict[str, float] = {}
    for census_key, mod in (rec.get("modifiers") or {}).items():
        key = _ITEM_PLAN_KEYS.get(census_key)
        value = mod.get("value") if isinstance(mod, dict) else None
        if key and isinstance(value, (int, float)) and value:
            out[key] = float(value)
    # Flat mitigation lives on the armour type rather than in `modifiers`.
    # It is the same number EquipInformation calls `mit`.
    mit = (rec.get("typeinfo") or {}).get("maxarmorclass")
    if isinstance(mit, (int, float)) and mit:
        out["mit"] = float(mit)
    return out


def planner_character_stats(doc: dict) -> dict[str, float]:
    """Census totals in the same keys as :func:`planner_item_stats`."""
    stats = doc.get("stats") or {}
    combat = stats.get("combat") or {}
    ability = stats.get("ability") or {}
    defense = stats.get("defense") or {}
    resists = doc.get("resists") or {}
    paths = {
        "potency": combat.get("basemodifier"),
        "crit": combat.get("critchance"),
        "abmod": combat.get("abilitymod"),
        "multi": combat.get("doubleattackchance"),
        "dps": combat.get("dps"),
        "aspeed": combat.get("attackspeed"),
        "acspeed": ability.get("spelltimecastpct"),
        "arspeed": ability.get("spelltimereusepct"),
        "flurry": combat.get("flurry"),
        "aeauto": combat.get("aeautoattackchance"),
        "hategain": combat.get("hategainmod"),
        "strike": combat.get("strikethrough"),
        "bchance": combat.get("blockchance"),
        "accuracy": combat.get("accuracy"),
        "mit": defense.get("armor"),
        "health": (stats.get("health") or {}).get("max"),
        "power": (stats.get("power") or {}).get("max"),
        "str": (stats.get("str") or {}).get("effective"),
        "sta": (stats.get("sta") or {}).get("effective"),
        "agi": (stats.get("agi") or {}).get("effective"),
        "wis": (stats.get("wis") or {}).get("effective"),
        "int": (stats.get("int") or {}).get("effective"),
        "vselemental": (resists.get("elemental") or {}).get("effective"),
        "vsarcane": (resists.get("arcane") or {}).get("effective"),
        "vsnoxious": (resists.get("noxious") or {}).get("effective"),
    }
    return {key: round(float(value), 2) for key, value in paths.items()
            if isinstance(value, (int, float))}


def _snapshot_doc(conn, character_id: int, snapshot_id: int | None = None):
    q = ("SELECT * FROM census_char_snapshots WHERE character_id=? "
         + ("AND id=? " if snapshot_id else "")
         + "ORDER BY fetched_ts DESC, id DESC LIMIT 1")
    params = (character_id, snapshot_id) if snapshot_id else (character_id,)
    row = conn.execute(q, params).fetchone()
    return (row, json.loads(row["json"])) if row is not None else (None, None)


def _gear(conn, doc: dict) -> list[dict]:
    from items import stat_block

    slots = doc.get("equipmentslot_list") or []
    ids = _equipped_item_ids(doc)
    names = {r["item_id"]: r for r in conn.execute(
        f"SELECT item_id, displayname, tier, json FROM census_items WHERE item_id IN "
        f"({','.join('?' * len(ids))})", ids)} if ids else {}
    known_adorns = {r["item_id"]: r for r in conn.execute(
        f"SELECT item_id, name, tier, type, level, iconid, icon_ok, stats_json "
        f"FROM items WHERE item_id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    out = []
    for slot in sorted(slots, key=lambda s: s.get("id", 0)):
        item = slot.get("item") or {}
        cached = names.get(item.get("id"))
        rec = json.loads(cached["json"]) if cached and cached["json"] else {}
        host_stats = stat_block(rec) if rec else None
        adornments = []
        for adorn in item.get("adornment_list") or []:
            arow = names.get(adorn.get("id"))
            fallback = known_adorns.get(adorn.get("id"))
            arec = json.loads(arow["json"]) if arow and arow["json"] else {}
            display_stats = (stat_block(arec) if arec else
                             json.loads(fallback["stats_json"])
                             if fallback and fallback["stats_json"] else None)
            adornments.append({
                "id": adorn.get("id"), "color": adorn.get("color"),
                "name": (arow["displayname"] if arow else
                         fallback["name"] if fallback else None),
                "tier": (arow["tier"] if arow else
                         fallback["tier"] if fallback else None),
                "icon": (arec.get("iconid") if arec else
                         fallback["iconid"] if fallback and fallback["icon_ok"] else None),
                "level": (arec.get("itemlevel") if arec else
                          fallback["level"] if fallback else None),
                "type": (arec.get("type") if arec else
                         fallback["type"] if fallback else None),
                "planner_stats": planner_item_stats(arec),
                "stats": display_stats,
            })
        out.append({
            "key": slot.get("name"),
            "slot": slot.get("displayname") or slot.get("name"),
            "item_id": item.get("id"),
            "name": cached["displayname"] if cached else None,
            "tier": cached["tier"] if cached else None,
            "level": rec.get("itemlevel"),
            "icon": rec.get("iconid"),
            "planner_stats": planner_item_stats(rec),
            "card": ({
                "name": cached["displayname"],
                "rarity": (cached["tier"] or "").title() or None,
                "icon": rec.get("iconid"), "type": rec.get("type"),
                "slot": slot.get("displayname") or slot.get("name"),
                "level": rec.get("itemlevel"), "stats": host_stats,
                "effects": None,
            } if cached else None),
            "adornments": adornments,
            "adorns": sum(1 for a in item.get("adornment_list") or [] if a.get("id")),
        })
    return out


def _spells(conn, doc: dict, char_class: str | None) -> dict:
    ids = doc.get("spell_list") or []
    rows = conn.execute(
        f"SELECT spell_id, name, base_name, crc, class, level, tier, tier_name "
        f"FROM census_spells WHERE spell_id IN ({','.join('?' * len(ids))})",
        ids).fetchall() if ids else []
    cls = (char_class or "").lower()
    scribed, other = [], 0
    for r in rows:
        if cls and cls in (r["class"] or ""):
            scribed.append({"id": r["spell_id"], "name": r["name"],
                            "base_name": r["base_name"], "level": r["level"],
                            "tier": r["tier"], "tier_name": r["tier_name"]})
        else:
            other += 1
    scribed.sort(key=lambda s: (-(s["level"] or 0), s["name"]))
    return {"scribed": scribed, "other_count": other,
            "uncached": len(ids) - len(rows)}


def character_summary(conn, char) -> dict:
    row, doc = _snapshot_doc(conn, char["id"])
    base = {"character": {"id": char["id"], "name": char["name"],
                          "class": char["class"], "level": char["level"],
                          "world": "Wuoshi", "last_census_ts": char["last_census_ts"],
                          "census_id": char["census_character_id"]}}
    if doc is None:
        return {**base, "synced": False}
    return _summary_of(conn, doc, base,
                       {"id": row["id"], "fetched_ts": row["fetched_ts"]},
                       char["class"])


def _summary_of(conn, doc: dict, base: dict, snapshot: dict | None,
                char_class: str | None) -> dict:
    """One Census document as the page's summary.

    Split out of `character_summary` so the by-name lookup on /plan renders the
    IDENTICAL shape (`planner_api.plan_character`). A reader trying gear on a
    toon they did not sign in for must get the same window as one who did —
    two builders would drift, and the difference would be invisible until a
    field went missing on the path nobody uses."""
    stats = doc.get("stats") or {}
    key_stats = []
    for label, (grp, key), is_pct in KEY_STATS:
        val = (stats.get(grp) or {}).get(key)
        if val is not None:
            key_stats.append({"label": label, "value": round(val, 1), "pct": is_pct})
    attributes = {k: (stats.get(k) or {}).get("effective")
                  for k in ("str", "agi", "sta", "int", "wis")}
    vitals = {"health": (stats.get("health") or {}).get("max"),
              "power": (stats.get("power") or {}).get("max")}
    resists = {k: (v or {}).get("effective")
               for k, v in (doc.get("resists") or {}).items()}
    aa = doc.get("alternateadvancements") or {}
    guild = (doc.get("guild") or {}).get("name")
    return {
        **base, "synced": True,
        "snapshot": snapshot,
        "guild": guild,
        "key_stats": key_stats, "attributes": attributes, "vitals": vitals,
        "resists": resists, "aa_spent": aa.get("spentpoints"),
        "planner_stats": planner_character_stats(doc),
        "gear": _gear(conn, doc),
        "spells": _spells(conn, doc, char_class),
    }


# How long a by-name lookup is served from the cache before Census is asked
# again. A character's gear changes when they raid, not when they refresh a
# page, and the cache is also what keeps this working at all while Census is
# unavailable — which is normal and comes and goes by time of day.
LOOKUP_TTL_S = 6 * 3600


def lookup_by_name(conn, client, name: str, world_id: int = 618,
                   refresh: bool = False) -> dict | None:
    """A public character by NAME -> the same summary an owned one produces.

    **Cache first, and cache the MISS too.** Census intermittency is normal and
    is not an outage, so a lookup that cannot reach it falls back to whatever
    was last seen rather than failing: a reader planning gear does not care
    that the record is six hours old. A name Census does not know is stored
    with a NULL document so a typo is not re-asked every time it is typed.

    Returns None when the name is unknown and nothing is cached. Writes no
    account state — see the `plan_characters` comment in `db.py`."""
    key = base_name(name).strip()
    if not key:
        return None
    lower = key.lower()
    row = conn.execute(
        "SELECT * FROM plan_characters WHERE name_lower=? AND world_id=?",
        (lower, world_id)).fetchone()
    fresh = row and (time.time() - row["fetched_ts"]) < LOOKUP_TTL_S
    if row and fresh and not refresh:
        return _lookup_out(conn, row)

    try:
        doc = client.character_by_name(key, world_id)
    except Exception:                      # noqa: BLE001 — one failure mode
        # Census is unreachable. Stale is better than nothing and much better
        # than an error page on a tab that needs no account.
        return _lookup_out(conn, row) if row else None
    if doc:
        doc = _trim(doc)
        ensure_items(conn, client, _equipped_item_ids(doc))
    now = int(time.time())
    with conn:
        conn.execute(
            "INSERT INTO plan_characters (name_lower, world_id, name, doc_json, "
            "fetched_ts) VALUES (?,?,?,?,?) "
            "ON CONFLICT(name_lower, world_id) DO UPDATE SET name=excluded.name, "
            "doc_json=excluded.doc_json, fetched_ts=excluded.fetched_ts",
            (lower, world_id, (doc or {}).get("displayname") or key,
             json.dumps(doc, separators=(",", ":")) if doc else None, now))
    return _lookup_out(conn, conn.execute(
        "SELECT * FROM plan_characters WHERE name_lower=? AND world_id=?",
        (lower, world_id)).fetchone())


# A cached MISS is re-asked far less often than a hit. "Census has no such
# character" is usually a typo and stays true forever; occasionally it is a
# name that did not exist yet. A week is the compromise — it costs one request
# per dead name per week and it means a new alt is not invisible for good.
MISS_RECHECK_S = 7 * 86400


def refresh_cached_lookups(conn, client, world_id: int = 618, *,
                           limit: int = 40, older_than_s: int = 12 * 3600,
                           now: float | None = None) -> dict:
    """Re-ask Census about the by-name lookups people have already searched.

    **A LOOKUP CACHE THAT IS NEVER REFRESHED IS A CACHE THAT GOES STALE IN ONE
    DIRECTION.** `lookup_by_name` fills a row when somebody types a name and
    serves it for `LOOKUP_TTL_S` after that — so a character nobody re-types
    keeps whatever gear they had the first time anyone looked, and a name typed
    for the first time during a Census outage answers nothing at all because
    there is no row to fall back to. This is the other half: on the schedule
    that already probes Census (`scripts/scheduled-sync.sh census`), refresh
    the stalest rows so the cache is CURRENT for the next reader, signed in or
    not.

    Bounded on purpose. `limit` rows per run, oldest first, and only rows past
    `older_than_s` — the point is a trickle that keeps up with a table people
    are adding to, not a full re-read every half hour of somebody else's
    service. A row is refreshed through `lookup_by_name`, so refreshing also
    caches the character's ITEM records, which is what makes the gear window
    and its icons work for the next reader.

    Stops at the first `CensusError`: Census going down mid-run is normal, the
    probe will find it up again, and hammering it while it is unhappy is how
    you get rate limited."""
    from census.client import CensusError

    at = int(time.time() if now is None else now)
    # ASK BY `name_lower`, NEVER BY `name`. `name` is Census's own displayname
    # ("Bobby (Wuoshi)") and asking for that finds nobody; `name_lower` is the
    # key the original lookup was made on and is what Census answers to.
    rows = conn.execute(
        "SELECT name_lower, fetched_ts FROM plan_characters "
        "WHERE world_id = ? AND fetched_ts < ? "
        "AND (doc_json IS NOT NULL OR fetched_ts < ?) "
        "ORDER BY fetched_ts LIMIT ?",
        (world_id, at - older_than_s, at - MISS_RECHECK_S, limit)).fetchall()

    out = {"checked": 0, "found": 0, "still_missing": 0, "stopped": False}
    for row in rows:
        try:
            got = lookup_by_name(conn, client, row["name_lower"], world_id,
                                 refresh=True)
        except CensusError:
            out["stopped"] = True
            break
        # `lookup_by_name` SWALLOWS an unreachable Census on purpose — the page
        # needs a stale answer more than it needs an error — so a raised
        # `CensusError` is not the only way this run can be failing. The row's
        # stamp is: a fetch that did not happen did not move it. Stopping on
        # that is the difference between one wasted request and forty of them
        # against a service that is already unhappy.
        stamped = conn.execute(
            "SELECT fetched_ts FROM plan_characters WHERE name_lower = ? "
            "AND world_id = ?", (row["name_lower"], world_id)).fetchone()
        if stamped and stamped["fetched_ts"] == row["fetched_ts"]:
            out["stopped"] = True
            break
        out["checked"] += 1
        out["found" if got else "still_missing"] += 1
    out["queued"] = conn.execute(
        "SELECT COUNT(*) FROM plan_characters WHERE world_id = ? AND fetched_ts < ?",
        (world_id, at - older_than_s)).fetchone()[0]
    return out


def _lookup_out(conn, row) -> dict | None:
    if row is None or not row["doc_json"]:
        return None
    doc = json.loads(row["doc_json"])
    ctype = doc.get("type") or {}
    cls = ctype.get("class")
    # `public: True` is how the page knows this is a looked-up record rather
    # than one of yours: no snapshot history, no refresh button, nothing to own.
    base = {"character": {
        "id": None, "name": row["name"], "class": cls,
        "level": ctype.get("level"), "world": "Wuoshi",
        "last_census_ts": row["fetched_ts"], "census_id": doc.get("id"),
        "public": True}}
    return _summary_of(conn, doc, base, None, cls)


def snapshot_list(conn, character_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, fetched_ts FROM census_char_snapshots WHERE character_id=? "
        "ORDER BY fetched_ts DESC, id DESC", (character_id,))]


def snapshot_diff(conn, char, snapshot_id: int) -> dict | None:
    """Changes between a snapshot and the one before it (stats/gear/spells)."""
    row, doc = _snapshot_doc(conn, char["id"], snapshot_id)
    if doc is None:
        return None
    prev_row = conn.execute(
        "SELECT id FROM census_char_snapshots WHERE character_id=? AND "
        "(fetched_ts, id) < ((SELECT fetched_ts FROM census_char_snapshots WHERE id=?), ?) "
        "ORDER BY fetched_ts DESC, id DESC LIMIT 1",
        (char["id"], snapshot_id, snapshot_id)).fetchone()
    if prev_row is None:
        return {"snapshot_id": snapshot_id, "first": True}
    _, prev = _snapshot_doc(conn, char["id"], prev_row["id"])

    def stat_changes():
        out = []
        for label, (grp, key), is_pct in KEY_STATS:
            a = ((prev.get("stats") or {}).get(grp) or {}).get(key)
            b = ((doc.get("stats") or {}).get(grp) or {}).get(key)
            if a is not None and b is not None and round(a, 1) != round(b, 1):
                out.append({"label": label, "from": round(a, 1), "to": round(b, 1),
                            "pct": is_pct})
        return out

    def gear_changes():
        def slot_map(d):
            return {(s.get("displayname") or s.get("name")): (s.get("item") or {}).get("id")
                    for s in d.get("equipmentslot_list") or []}
        old, new = slot_map(prev), slot_map(doc)
        changed_ids = [i for i in set(old.values()) | set(new.values()) if i]
        names = {r["item_id"]: r["displayname"] for r in conn.execute(
            f"SELECT item_id, displayname FROM census_items WHERE item_id IN "
            f"({','.join('?' * len(changed_ids))})", changed_ids)} if changed_ids else {}
        return [{"slot": slot, "from": names.get(old.get(slot)),
                 "to": names.get(new.get(slot))}
                for slot in sorted(set(old) | set(new))
                if old.get(slot) != new.get(slot)]

    def spell_changes():
        old, new = set(prev.get("spell_list") or []), set(doc.get("spell_list") or [])
        delta = list((old ^ new))
        rows = {r["spell_id"]: r for r in conn.execute(
            f"SELECT spell_id, name, base_name, tier_name FROM census_spells "
            f"WHERE spell_id IN ({','.join('?' * len(delta))})", delta)} if delta else {}
        removed = {rows[i]["base_name"]: rows[i] for i in old - new if i in rows}
        out = []
        for i in new - old:
            r = rows.get(i)
            if r is None:
                continue
            was = removed.pop(r["base_name"], None)
            out.append({"name": r["name"],
                        "from_tier": was["tier_name"] if was else None,
                        "to_tier": r["tier_name"]})
        out += [{"name": r["name"], "from_tier": r["tier_name"], "to_tier": None}
                for r in removed.values()]
        return sorted(out, key=lambda s: s["name"])

    return {"snapshot_id": snapshot_id, "prev_snapshot_id": prev_row["id"],
            "fetched_ts": row["fetched_ts"], "stats": stat_changes(),
            "gear": gear_changes(), "spells": spell_changes()}
