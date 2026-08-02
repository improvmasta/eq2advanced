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
        item_id = (slot.get("item") or {}).get("id")
        if item_id:
            ids.append(item_id)
    return ids


def _spell_rows(recs, now: int) -> list[tuple]:
    rows = []
    for rec in recs:
        name = rec.get("name") or ""
        classes = ",".join(sorted((rec.get("classes") or {}).keys()))
        rows.append((rec["id"], name, base_name(name), rec.get("crc"), classes,
                     rec.get("level"), rec.get("tier"), rec.get("tier_name"),
                     json_dumps(rec), json_dumps(parse_effects(rec.get("effect_list"))),
                     now))
    return rows


_SPELL_INSERT = (
    "INSERT OR REPLACE INTO census_spells "
    "(spell_id, name, base_name, crc, class, level, tier, tier_name, "
    " json, parsed_effects, fetched_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)")


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


def _snapshot_doc(conn, character_id: int, snapshot_id: int | None = None):
    q = ("SELECT * FROM census_char_snapshots WHERE character_id=? "
         + ("AND id=? " if snapshot_id else "")
         + "ORDER BY fetched_ts DESC, id DESC LIMIT 1")
    params = (character_id, snapshot_id) if snapshot_id else (character_id,)
    row = conn.execute(q, params).fetchone()
    return (row, json.loads(row["json"])) if row is not None else (None, None)


def _gear(conn, doc: dict) -> list[dict]:
    slots = doc.get("equipmentslot_list") or []
    ids = _equipped_item_ids(doc)
    names = {r["item_id"]: r for r in conn.execute(
        f"SELECT item_id, displayname, tier FROM census_items WHERE item_id IN "
        f"({','.join('?' * len(ids))})", ids)} if ids else {}
    out = []
    for slot in sorted(slots, key=lambda s: s.get("id", 0)):
        item = slot.get("item") or {}
        cached = names.get(item.get("id"))
        out.append({
            "slot": slot.get("displayname") or slot.get("name"),
            "item_id": item.get("id"),
            "name": cached["displayname"] if cached else None,
            "tier": cached["tier"] if cached else None,
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
                          "world": "Wuoshi", "last_census_ts": char["last_census_ts"]}}
    if doc is None:
        return {**base, "synced": False}
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
        "snapshot": {"id": row["id"], "fetched_ts": row["fetched_ts"]},
        "guild": guild,
        "key_stats": key_stats, "attributes": attributes, "vitals": vitals,
        "resists": resists, "aa_spent": aa.get("spentpoints"),
        "gear": _gear(conn, doc),
        "spells": _spells(conn, doc, char["class"]),
    }


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
