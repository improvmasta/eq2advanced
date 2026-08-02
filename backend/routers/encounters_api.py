"""Encounter detail: actor table (rolled up to players) + per-ability breakdown
with pet rows kept visible under their owner."""

from fastapi import APIRouter, Depends, HTTPException

from db import get_db, row_to_dict, rows_to_dicts
from routers.sessions_api import visible_session
from security import require_user

router = APIRouter(tags=["encounters"])


@router.get("/encounters/{encounter_id}")
def encounter_detail(encounter_id: int, user=Depends(require_user)):
    conn = get_db()
    enc = conn.execute("SELECT * FROM encounters WHERE id=?", (encounter_id,)).fetchone()
    if enc is None:
        raise HTTPException(404, "no such encounter")
    visible_session(conn, user, enc["session_id"])

    actors = conn.execute(
        "SELECT a.*, e.name, e.kind FROM encounter_actor_stats a "
        "JOIN entities e ON e.id = a.entity_id "
        "WHERE a.encounter_id=? ORDER BY a.damage DESC",
        (encounter_id,),
    ).fetchall()

    abilities = conn.execute(
        "SELECT s.entity_id, ent.name AS source_name, ent.kind AS source_kind, "
        "ent.rollup_to, ab.name AS ability, s.kind, s.casts, s.hits, s.crits, "
        "s.misses, s.resists, s.total, s.min, s.max "
        "FROM encounter_ability_stats s "
        "JOIN entities ent ON ent.id = s.entity_id "
        "JOIN abilities ab ON ab.id = s.ability_id "
        "WHERE s.encounter_id=? ORDER BY s.total DESC",
        (encounter_id,),
    ).fetchall()

    return {
        "encounter": row_to_dict(enc),
        "actors": rows_to_dicts(actors),
        "abilities": rows_to_dicts(abilities),
    }
