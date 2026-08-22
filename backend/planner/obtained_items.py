"""Additive equipped-item observations for Planner acquisition progress."""

from __future__ import annotations

import re
import time
import unicodedata

from planner.identity import validate_planner_key

MAX_ITEMS = 96
MAX_ITEM_KEY = 200
MAX_ITEM_NAME = 160
MAX_SOURCE = 40

_CENSUS_KEY = re.compile(r"^census:([1-9][0-9]{0,19})$")
_SOURCE = re.compile(r"^[a-z][a-z0-9:_-]{0,39}$")


def canonical_name(value: str | None) -> str:
    clean = unicodedata.normalize("NFKC", str(value or ""))
    clean = " ".join(clean.split()).strip().casefold()
    if not clean or len(clean) > MAX_ITEM_NAME or any(ord(char) < 32 for char in clean):
        raise ValueError("obtained item name is invalid")
    return clean


def _observation(row: dict, now: int) -> tuple[str, str, str, int, int]:
    item_name = " ".join(str(row.get("item_name") or "").split()).strip()
    if not item_name or len(item_name) > MAX_ITEM_NAME:
        raise ValueError("obtained item name is required")
    item_key = str(row.get("item_key") or "").strip().casefold()
    if _CENSUS_KEY.fullmatch(item_key):
        pass
    elif item_key.startswith("name:"):
        item_key = f"name:{canonical_name(item_key[5:])}"
        if item_key != f"name:{canonical_name(item_name)}":
            raise ValueError("fallback item key must match its exact name")
    else:
        raise ValueError("obtained item key must be census:<id> or name:<exact name>")
    if len(item_key) > MAX_ITEM_KEY:
        raise ValueError("obtained item key is too long")
    source = str(row.get("source") or "").strip().casefold()
    if not _SOURCE.fullmatch(source):
        raise ValueError("obtained item source is invalid")
    try:
        first_seen = int(row.get("first_seen_ts") or now)
        last_seen = int(row.get("last_seen_ts") or now)
    except (TypeError, ValueError) as exc:
        raise ValueError("obtained item timestamps are invalid") from exc
    first_seen = max(1, min(first_seen, now))
    last_seen = max(first_seen, min(last_seen, now))
    return item_key, item_name, source, first_seen, last_seen


def read(conn, user_id: int, owner_key: str) -> list[dict]:
    clean_owner = validate_planner_key(owner_key)
    return [dict(row) for row in conn.execute(
        "SELECT item_key, item_name, first_seen_ts, last_seen_ts, source "
        "FROM planner_obtained_items WHERE user_id=? AND owner_key=? "
        "ORDER BY first_seen_ts, item_name, item_key",
        (user_id, clean_owner)).fetchall()]


def reconcile(conn, user_id: int, owner_key: str,
              observations: list[dict], now: int | None = None) -> dict:
    clean_owner = validate_planner_key(owner_key)
    if len(observations) > MAX_ITEMS:
        raise ValueError(f"at most {MAX_ITEMS} equipped identities may be reconciled")
    seen_at = int(now or time.time())
    unique: dict[str, tuple[str, str, int, int]] = {}
    for raw in observations:
        item_key, item_name, source, first_seen, last_seen = _observation(raw, seen_at)
        unique[item_key] = (item_name, source, first_seen, last_seen)
    existing = {row["item_key"] for row in conn.execute(
        "SELECT item_key FROM planner_obtained_items "
        "WHERE user_id=? AND owner_key=?", (user_id, clean_owner)).fetchall()}
    with conn:
        conn.executemany(
            "INSERT INTO planner_obtained_items"
            "(user_id, owner_key, item_key, item_name, first_seen_ts, last_seen_ts, source) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id, owner_key, item_key) "
            "DO UPDATE SET item_name=excluded.item_name, "
            "first_seen_ts=MIN(planner_obtained_items.first_seen_ts, excluded.first_seen_ts), "
            "last_seen_ts=MAX(planner_obtained_items.last_seen_ts, excluded.last_seen_ts), "
            "source=excluded.source",
            [(user_id, clean_owner, key, name, first_seen, last_seen, source)
             for key, (name, source, first_seen, last_seen) in unique.items()])
    return {
        "items": read(conn, user_id, clean_owner),
        "added": sorted(set(unique) - existing),
    }
