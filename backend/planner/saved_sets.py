"""Five private equipment-set slots per reader and public character."""

from __future__ import annotations

import json
import time

SLOT_COUNT = 5
MAX_NAME = 40
MAX_OWNER_KEY = 160
MAX_OWNER_NAME = 40
MAX_PAYLOAD_BYTES = 400_000


def _default(slot: int) -> dict:
    return {"slot": slot, "name": f"Set {slot}", "payload": None,
            "updated_ts": None}


def _owner(owner_key: str, owner_name: str = "") -> tuple[str, str]:
    clean_key = str(owner_key or "").strip().lower()[:MAX_OWNER_KEY]
    clean_name = " ".join(str(owner_name or "").split()).strip()[:MAX_OWNER_NAME]
    if not clean_key:
        raise ValueError("saved-set character key is required")
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
    return [dict(row) for row in conn.execute(
        "SELECT owner_key, owner_name, MAX(updated_ts) AS updated_ts "
        "FROM planner_saved_sets WHERE user_id=? "
        "GROUP BY owner_key, owner_name ORDER BY updated_ts DESC, owner_name",
        (user_id,)).fetchall()]


def write(conn, user_id: int, owner_key: str, owner_name: str, slot: int,
          name: str, payload: dict | None) -> dict:
    if not 1 <= slot <= SLOT_COUNT:
        raise ValueError("saved-set slot must be between 1 and 5")
    clean_owner_key, clean_owner_name = _owner(owner_key, owner_name)
    if not clean_owner_name:
        raise ValueError("saved-set character name is required")
    clean_name = " ".join(name.split()).strip()[:MAX_NAME]
    if not clean_name:
        clean_name = f"Set {slot}"
    encoded = None
    if payload is not None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("saved equipment set is too large")
    now = int(time.time())
    with conn:
        conn.execute(
            "INSERT INTO planner_saved_sets"
            "(user_id, owner_key, owner_name, slot, name, payload_json, updated_ts) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id, owner_key, slot) DO UPDATE SET "
            "owner_name=excluded.owner_name, "
            "name=excluded.name, payload_json=excluded.payload_json, "
            "updated_ts=excluded.updated_ts",
            (user_id, clean_owner_key, clean_owner_name, slot, clean_name, encoded, now))
    return next(row for row in read(conn, user_id, clean_owner_key)
                if row["slot"] == slot)
