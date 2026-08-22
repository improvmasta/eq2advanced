"""Five private equipment-set slots per reader and public character."""

from __future__ import annotations

import json
import time

from planner.identity import lookup_name, planner_key, validate_planner_key

SLOT_COUNT = 5
MAX_NAME = 40
MAX_OWNER_KEY = 160
MAX_OWNER_NAME = 40
MAX_PAYLOAD_BYTES = 400_000
UNGUARDED = object()


class SavedSetConflict(ValueError):
    """A guarded browser fallback tried to replace a newer server write."""


def _default(slot: int) -> dict:
    return {"slot": slot, "name": f"Set {slot}", "payload": None,
            "updated_ts": None}


def _owner(owner_key: str, owner_name: str = "") -> tuple[str, str]:
    clean_key = validate_planner_key(owner_key)
    clean_name = " ".join(str(owner_name or "").split()).strip()[:MAX_OWNER_NAME]
    if clean_name:
        world = clean_key.split(":", 1)[0]
        if planner_key(world, lookup_name(clean_name, world)) != clean_key:
            raise ValueError("saved-set character name does not match its key")
    return clean_key, clean_name


def read(conn, user_id: int, owner_key: str) -> list[dict]:
    clean_key, _ = _owner(owner_key)
    rows = {row["slot"]: row for row in conn.execute(
        "SELECT slot, name, payload_json, updated_ts FROM planner_saved_sets "
        "WHERE user_id=? AND owner_key=? ORDER BY slot",
        (user_id, clean_key)).fetchall()}
    out = []
    for slot in range(1, SLOT_COUNT + 1):
        row = rows.get(slot)
        if row is None:
            out.append(_default(slot))
            continue
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else None
        except (TypeError, json.JSONDecodeError):
            payload = None
        out.append({"slot": slot, "name": row["name"], "payload": payload,
                    "updated_ts": row["updated_ts"]})
    return out


def owners(conn, user_id: int) -> list[dict]:
    """Characters are folders, not owned identities.

    This list only says which public-character keys this account has privately
    filed builds under; another account can independently use the same keys.
    """
    out = {}
    for row in conn.execute(
            "SELECT owner_key, owner_name, updated_ts FROM planner_saved_sets "
            "WHERE user_id=? AND payload_json IS NOT NULL "
            "ORDER BY updated_ts DESC, slot DESC", (user_id,)).fetchall():
        if row["owner_key"] in out:
            continue
        world = row["owner_key"].split(":", 1)[0]
        out[row["owner_key"]] = {
            **dict(row),
            "lookup_name": lookup_name(row["owner_name"], world),
        }
    return list(out.values())


def delete(conn, user_id: int, owner_key: str, slot: int) -> None:
    if not 1 <= slot <= SLOT_COUNT:
        raise ValueError("saved-set slot must be between 1 and 5")
    clean_key, _ = _owner(owner_key)
    with conn:
        conn.execute(
            "DELETE FROM planner_saved_sets WHERE user_id=? AND owner_key=? AND slot=?",
            (user_id, clean_key, slot))


def write(conn, user_id: int, owner_key: str, owner_name: str, slot: int,
          name: str, payload: dict | None,
          expected_updated_ts=UNGUARDED) -> dict:
    if not 1 <= slot <= SLOT_COUNT:
        raise ValueError("saved-set slot must be between 1 and 5")
    clean_owner_key, clean_owner_name = _owner(owner_key, owner_name)
    if not clean_owner_name:
        raise ValueError("saved-set character name is required")
    clean_name = " ".join(name.split()).strip()[:MAX_NAME]
    if not clean_name:
        clean_name = f"Set {slot}"
    if payload is None:
        delete(conn, user_id, clean_owner_key, slot)
        return _default(slot)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("saved equipment set is too large")
    current = conn.execute(
        "SELECT updated_ts FROM planner_saved_sets "
        "WHERE user_id=? AND owner_key=? AND slot=?",
        (user_id, clean_owner_key, slot)).fetchone()
    current_ts = current["updated_ts"] if current else None
    now = max(int(time.time()), int(current_ts or 0) + 1)
    with conn:
        if expected_updated_ts is UNGUARDED:
            conn.execute(
                "INSERT INTO planner_saved_sets"
                "(user_id, owner_key, owner_name, slot, name, payload_json, updated_ts) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id, owner_key, slot) DO UPDATE SET "
                "owner_name=excluded.owner_name, "
                "name=excluded.name, payload_json=excluded.payload_json, "
                "updated_ts=excluded.updated_ts",
                (user_id, clean_owner_key, clean_owner_name, slot,
                 clean_name, encoded, now))
        elif expected_updated_ts is None:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO planner_saved_sets"
                "(user_id, owner_key, owner_name, slot, name, payload_json, updated_ts) "
                "VALUES(?,?,?,?,?,?,?)",
                (user_id, clean_owner_key, clean_owner_name, slot,
                 clean_name, encoded, now))
            if not cursor.rowcount:
                raise SavedSetConflict("saved set changed before browser fallback synced")
        else:
            expected = int(expected_updated_ts)
            now = max(now, expected + 1)
            cursor = conn.execute(
                "UPDATE planner_saved_sets SET owner_name=?, name=?, payload_json=?, "
                "updated_ts=? WHERE user_id=? AND owner_key=? AND slot=? AND updated_ts=?",
                (clean_owner_name, clean_name, encoded, now, user_id,
                 clean_owner_key, slot, expected))
            if not cursor.rowcount:
                raise SavedSetConflict("saved set changed before browser fallback synced")
    return next(row for row in read(conn, user_id, clean_owner_key)
                if row["slot"] == slot)
