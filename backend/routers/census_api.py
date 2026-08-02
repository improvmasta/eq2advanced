"""Census views for a character: summary, on-demand refresh, snapshot history.

All reads come from the local snapshot/caches; only /refresh (and the nightly
job) talks to Census. Client access goes through census.client.shared_client()
so tests can inject a fixture-backed fake.
"""

import time

from fastapi import APIRouter, Depends, HTTPException

from census import client as census_client
from census.sync import (character_summary, snapshot_diff, snapshot_list,
                         sync_character)
from db import get_db
from security import owned_character, require_user

router = APIRouter(tags=["census"])

REFRESH_COOLDOWN_S = 60


@router.get("/characters/{character_id}/census")
def get_census(character_id: int, user=Depends(require_user)):
    conn = get_db()
    char = owned_character(conn, user, character_id)
    return character_summary(conn, char)


@router.post("/characters/{character_id}/census/refresh")
def refresh_census(character_id: int, user=Depends(require_user)):
    conn = get_db()
    char = owned_character(conn, user, character_id)
    last = char["last_census_ts"] or 0
    if time.time() - last < REFRESH_COOLDOWN_S:
        return {"skipped": "cooldown", **sync_result_stub(char)}
    try:
        result = sync_character(conn, census_client.shared_client(), char["id"])
    except census_client.CensusError as e:
        raise HTTPException(502, str(e))
    if not result["found"]:
        raise HTTPException(404, result["error"])
    return result


def sync_result_stub(char):
    return {"found": True, "changed": False, "snapshot_id": None,
            "spells_fetched": 0, "items_fetched": 0}


@router.get("/characters/{character_id}/census/snapshots")
def list_snapshots(character_id: int, user=Depends(require_user)):
    conn = get_db()
    char = owned_character(conn, user, character_id)
    return {"snapshots": snapshot_list(conn, char["id"])}


@router.get("/characters/{character_id}/census/snapshots/{snapshot_id}/diff")
def get_snapshot_diff(character_id: int, snapshot_id: int, user=Depends(require_user)):
    conn = get_db()
    char = owned_character(conn, user, character_id)
    diff = snapshot_diff(conn, char, snapshot_id)
    if diff is None:
        raise HTTPException(404, "no such snapshot")
    return diff


@router.get("/spells/{spell_id}")
def get_spell(spell_id: int, user=Depends(require_user)):
    conn = get_db()
    row = conn.execute(
        "SELECT spell_id, name, base_name, crc, class, level, tier, tier_name, "
        "json, parsed_effects FROM census_spells WHERE spell_id=?",
        (spell_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "spell not cached")
    import json as _json
    rec = _json.loads(row["json"]) if row["json"] else {}
    return {"spell": {
        "id": row["spell_id"], "name": row["name"], "base_name": row["base_name"],
        "crc": row["crc"], "class": row["class"], "level": row["level"],
        "tier": row["tier"], "tier_name": row["tier_name"],
        "cast_s": (rec.get("cast_secs_hundredths") or 0) / 100 or None,
        "recast_s": rec.get("recast_secs"),
        "recovery_s": (rec.get("recovery_secs_tenths") or 0) / 10 or None,
        "power": (rec.get("cost") or {}).get("power"),
        "target_type": rec.get("target_type"),
        "effects": _json.loads(row["parsed_effects"]) if row["parsed_effects"] else [],
    }}
