"""EQ2 Lexicon as a bounded fallback for character and equipped-item data.

Census remains authoritative when it answers. Lexicon is asked for a character
only when a typed planner lookup has no cached Census snapshot and Census is
unreachable. It is also asked when a Census character document contains an
equipped item/adornment id whose item record is absent. Item results live in
their own cache table, and character documents carry explicit provenance, so
the two sources can never silently overwrite one another.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
import re
import time
from urllib.parse import quote

import httpx

from db import json_dumps

BASE = os.environ.get("EQ2LEXICON_URL", "https://wuoshi.eq2lexicon.com").rstrip("/")
MAX_EQUIPPED_IDS = 64
INCOMPLETE_TTL_S = 6 * 3600

_REPEATED_SLOTS = {
    "Charm": ("activate1", "activate2"),
    "Ear": ("ears", "ears2"),
    "Finger": ("left_ring", "right_ring"),
    "Wrist": ("left_wrist", "right_wrist"),
}


class LexiconError(Exception):
    pass


class LexiconClient:
    def __init__(self, timeout: float = 12.0):
        self._http = httpx.Client(timeout=timeout, follow_redirects=True,
                                  headers={"User-Agent": "eq2advanced/lexicon-fallback"})

    def close(self):
        self._http.close()

    def _get(self, path: str) -> dict:
        try:
            response = self._http.get(f"{BASE}{path}")
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LexiconError(f"Lexicon request failed: {exc}") from exc
        if not isinstance(body, dict):
            raise LexiconError("Lexicon returned a non-object response")
        return body

    def character(self, name: str) -> dict:
        return self._get(f"/api/character/{quote(name, safe='')}")

    def item(self, item_id: int) -> dict:
        return self._get(f"/api/item/{item_id}")

    def items_by_ids(self, item_ids: list[int]) -> list[dict]:
        """Resolve a bounded worn set concurrently; individual misses are okay."""
        ids = list(dict.fromkeys(item_ids))[:MAX_EQUIPPED_IDS]
        if not ids:
            return []
        found = []
        with ThreadPoolExecutor(max_workers=min(6, len(ids))) as pool:
            jobs = {pool.submit(self.item, item_id): item_id for item_id in ids}
            for future in as_completed(jobs):
                try:
                    row = future.result()
                except LexiconError:
                    continue
                if row.get("id") is not None:
                    found.append(row)
        return found


_shared: LexiconClient | None = None


def shared_client() -> LexiconClient:
    global _shared
    if _shared is None:
        _shared = LexiconClient()
    return _shared


def _int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _equipment_summaries(character: dict) -> dict[int, dict]:
    """Flatten Lexicon's character gear into its item endpoint's vocabulary."""
    out = {}
    for worn in character.get("equipment") or []:
        item_id = _int(worn.get("item_id"))
        if item_id:
            out[item_id] = {
                "id": str(item_id), "name": worn.get("name"),
                "quality": (worn.get("tier") or "").lower() or None,
                "icon_id": worn.get("icon_id"), "slot_type": worn.get("slot"),
                "stats": [], "effects": [], "adornment_slots": [],
                "flags": [], "set_bonuses": [],
            }
        for adorn in worn.get("adorn_slots") or []:
            adorn_id = _int(adorn.get("adorn_id"))
            if not adorn_id:
                continue
            color = adorn.get("color")
            out[adorn_id] = {
                "id": str(adorn_id), "name": adorn.get("adorn_name"),
                "quality": None, "icon_id": None,
                "slot_type": worn.get("slot"),
                "armor_type": f"{color} Adornment" if color else "Adornment",
                "stats": [], "effects": [], "adornment_slots": [],
                "flags": [], "set_bonuses": [],
            }
    return out


def _equipped_ids(census_doc: dict) -> list[int]:
    wanted = []
    for slot in census_doc.get("equipmentslot_list") or []:
        item = slot.get("item") or {}
        if item.get("id"):
            wanted.append(int(item["id"]))
        wanted.extend(int(a["id"]) for a in item.get("adornment_list") or []
                      if a.get("id"))
    return list(dict.fromkeys(wanted))[:MAX_EQUIPPED_IDS]


def as_census_character(record: dict) -> dict:
    """Translate Lexicon's public character payload into our display shape.

    This is intentionally the narrow shape consumed by the planner, not a
    claim that a Lexicon record is a Census record. ``_source`` survives in
    the cached document and is echoed to the UI as provenance.
    """
    stats = record.get("stats") or {}
    nested_stats = {
        "health": {"max": stats.get("health_max")},
        "power": {"max": stats.get("power_max")},
        "str": {"effective": stats.get("str_eff")},
        "sta": {"effective": stats.get("sta_eff")},
        "agi": {"effective": stats.get("agi_eff")},
        "wis": {"effective": stats.get("wis_eff")},
        "int": {"effective": stats.get("int_eff")},
        "defense": {"armor": stats.get("armor")},
        "combat": {
            "abilitymod": stats.get("ability_mod"),
            "basemodifier": stats.get("potency"),
            "critchance": stats.get("crit_chance"),
            "doubleattackchance": stats.get("double_attack"),
            "dps": stats.get("dps"),
            "attackspeed": stats.get("attack_speed"),
            "flurry": stats.get("flurry"),
            "aeautoattackchance": stats.get("ae_autoattack"),
            "hategainmod": stats.get("hate_gain"),
            "strikethrough": stats.get("strikethrough"),
            "blockchance": stats.get("block_chance"),
            "accuracy": stats.get("accuracy"),
        },
        "ability": {
            "spelltimecastpct": stats.get("casting_speed"),
            "spelltimereusepct": stats.get("reuse_speed"),
            "spelltimerecoverypct": stats.get("recovery_speed"),
        },
    }

    seen: dict[str, int] = {}
    equipment = []
    for position, worn in enumerate(record.get("equipment") or []):
        display = worn.get("slot") or "Unknown"
        repeated = _REPEATED_SLOTS.get(display)
        if repeated:
            occurrence = seen.get(display, 0)
            key = repeated[min(occurrence, len(repeated) - 1)]
            seen[display] = occurrence + 1
        else:
            key = display.lower().replace(" ", "_")
        item_id = _int(worn.get("item_id"))
        adornments = [{"id": _int(adorn.get("adorn_id")),
                       "color": str(adorn.get("color") or "").lower() or None}
                      for adorn in worn.get("adorn_slots") or []]
        equipment.append({
            "id": position, "name": key, "displayname": display,
            "item": {"id": item_id, "adornment_list": adornments},
        })

    return {
        "_source": "lexicon",
        "id": _int(record.get("id")),
        "name": {"first": record.get("name")},
        "displayname": record.get("name"),
        "type": {"class": record.get("cls"), "level": _int(record.get("level")),
                 "ts_class": record.get("ts_class"),
                 "ts_level": _int(record.get("ts_level"))},
        "guild": {"name": record.get("guild_name")},
        "stats": nested_stats,
        "resists": {},
        "spell_list": [_int(value) for value in record.get("spell_ids") or []
                       if _int(value) is not None],
        "equipmentslot_list": equipment,
        "alternateadvancements": {"spentpoints": _int(record.get("aa_count"))},
        "last_update": _int(record.get("fetched_at")),
    }


def enrich_equipment(conn, name: str, census_doc: dict,
                     client: LexiconClient | None = None,
                     now: int | None = None) -> int:
    """Cache Lexicon detail for worn ids that Census has not resolved.

    The Census character document supplies the allow-list.  A mismatched or
    stale Lexicon character can therefore fail to enrich an id, but it can
    never introduce an item Census did not say was equipped.
    """
    wanted = _equipped_ids(census_doc)
    if not wanted:
        return 0

    marks = ",".join("?" * len(wanted))
    census_have = {r[0] for r in conn.execute(
        f"SELECT item_id FROM census_items WHERE item_id IN ({marks})", wanted)}
    at = int(time.time() if now is None else now)
    fallback_have = {r[0] for r in conn.execute(
        f"SELECT item_id FROM lexicon_items WHERE item_id IN ({marks}) "
        "AND (complete=1 OR fetched_ts>=?)", (*wanted, at - INCOMPLETE_TTL_S))}
    missing = [item_id for item_id in wanted
               if item_id not in census_have and item_id not in fallback_have]
    if not missing:
        return 0

    client = client or shared_client()
    character = client.character(name)
    if str(character.get("name") or "").lower() != name.lower():
        raise LexiconError("Lexicon character response did not match the requested name")
    summaries = _equipment_summaries(character)
    ask = [item_id for item_id in missing if item_id in summaries]
    detailed = {_int(row.get("id")): row for row in client.items_by_ids(ask)}
    rows = []
    for item_id in ask:
        full = detailed.get(item_id)
        record = full or summaries[item_id]
        rows.append((item_id, record.get("name"),
                     (record.get("quality") or "").upper() or None,
                     json_dumps(record), 1 if full else 0, at))
    with conn:
        conn.executemany(
            "INSERT INTO lexicon_items (item_id,name,tier,json,complete,fetched_ts) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET "
            "name=excluded.name,tier=excluded.tier,json=excluded.json,"
            "complete=excluded.complete,fetched_ts=excluded.fetched_ts", rows)
    return len(rows)


def maybe_enrich_equipment(conn, name: str, census_doc: dict,
                           client: LexiconClient | None = None) -> int:
    """Best-effort boundary used by character reads and refreshes."""
    enriched = 0
    try:
        enriched = enrich_equipment(conn, name, census_doc, client)
    except Exception as exc:  # noqa: BLE001 — fallback never fails the page
        logging.getLogger("census.lexicon").warning(
            "Lexicon equipment fallback failed for %s: %s", name, exc)
    # Census's old image endpoint no longer serves these icons. Resolve the
    # icon ids already admitted by the equipped-id allow-list through EQ2i and
    # keep using the app's local public icon route. This is bounded by the same
    # 64 worn ids, and `ensure_icons` is a no-op when every file is cached.
    try:
        import items
        wanted = _equipped_ids(census_doc)
        if wanted:
            marks = ",".join("?" * len(wanted))
            iconids = []
            for row in conn.execute(
                    f"SELECT json FROM census_items WHERE item_id IN ({marks})",
                    wanted):
                iconids.append(_int((json.loads(row["json"] or "{}") or {}).get("iconid")))
            for row in conn.execute(
                    f"SELECT json FROM lexicon_items WHERE item_id IN ({marks})",
                    wanted):
                iconids.append(_int((json.loads(row["json"] or "{}") or {}).get("icon_id")))
            items.ensure_icons(iconids)
    except Exception as exc:  # noqa: BLE001 — pictures never fail the summary
        logging.getLogger("census.lexicon").warning(
            "Equipped icon fallback failed for %s: %s", name, exc)
    return enriched


_STAT = {
    "Primary Attributes": ("strength", "attribute"),
    "Stamina": ("stamina", "attribute"),
    "Strength": ("strength", "attribute"),
    "Agility": ("agility", "attribute"),
    "Wisdom": ("wisdom", "attribute"),
    "Intelligence": ("intelligence", "attribute"),
    "Combat Skills": ("combatskills", "skill"),
    "Ability Mod": ("all", "normalizedmod"),
    "Potency": ("basemodifier", "modifyproperty"),
    "Crit Chance": ("critchance", "modifyproperty"),
    "Casting Speed": ("spelltimecastpct", "modifyproperty"),
    "Reuse Speed": ("spelltimereusepct", "modifyproperty"),
    "DPS": ("dps", "modifyproperty"),
    "DPS Mod": ("dps", "modifyproperty"),
    "Haste": ("attackspeed", "modifyproperty"),
    "Attack Speed": ("attackspeed", "modifyproperty"),
    "Multi Attack": ("doubleattackchance", "modifyproperty"),
    "Flurry": ("flurry", "modifyproperty"),
    "AE Autoattack": ("aeautoattackchance", "modifyproperty"),
    "AE Auto Attack": ("aeautoattackchance", "modifyproperty"),
    "Hate Gain": ("hategainmod", "modifyproperty"),
    "Block Chance": ("blockchance", "modifyproperty"),
    "Strikethrough": ("strikethrough", "modifyproperty"),
}


def as_census_item(record: dict) -> dict:
    """Translate Lexicon item JSON into the narrow Census shape we display."""
    modifiers = {}
    for stat in record.get("stats") or []:
        label = stat.get("display_name")
        key, kind = _STAT.get(label, (
            re.sub(r"[^a-z0-9]+", "", (label or "stat").lower()),
            "attribute" if stat.get("stat_group") == "primary" else "modifyproperty"))
        modifiers[key] = {"displayname": label, "value": stat.get("value"),
                          "type": kind}
    armor_type = record.get("armor_type") or ""
    typeinfo = {}
    if "adornment" in armor_type.lower():
        color = armor_type.split()[0].lower() if armor_type else None
        typeinfo = {
            "name": "adornment", "color": color,
            "slot_list": ([{"displayname": record.get("slot_type")}]
                          if record.get("slot_type") else []),
        }
    bonuses = []
    for bonus in record.get("set_bonuses") or []:
        row = {"requireditems": bonus.get("required_items"),
               "effect": bonus.get("effect")}
        for i, line in enumerate(bonus.get("lines") or [], 1):
            row[f"descriptiontag_{i}"] = line
        bonuses.append(row)
    flags = {re.sub(r"[^a-z]", "", flag.lower()): {"value": 1}
             for flag in record.get("flags") or []}
    effect_list = []
    for effect in record.get("effects") or []:
        if effect.get("trigger"):
            effect_list.append({"description": effect["trigger"],
                                "indentation": 0})
        effect_list.extend({
            "description": line.get("text"),
            "indentation": line.get("indentation", 1),
        } for line in (effect.get("lines") or []) if line.get("text"))
    return {
        "id": _int(record.get("id")), "displayname": record.get("name"),
        "tier": (record.get("quality") or "").upper() or None,
        "type": record.get("armor_type"), "itemlevel": record.get("item_level"),
        "iconid": _int(record.get("icon_id")), "modifiers": modifiers,
        "flags": flags, "typeinfo": typeinfo, "setbonus_list": bonuses,
        "effect_list": effect_list,
        "adornmentslot_list": [
            {"color": str(color).lower()} for color in record.get("adornment_slots") or []],
    }
