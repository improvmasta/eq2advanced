"""Session listing + detail (zones -> encounters with the logger's headline).
Scoped to the signed-in user's characters; admin sees all. The /stream endpoint
is SSE for the Live page: it polls the DB and pushes fight cards as the live
ingest path finalizes encounters."""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from db import get_db, row_to_dict, rows_to_dicts
from security import is_admin, require_user

router = APIRouter(tags=["sessions"])

STREAM_POLL_S = 1.5
ONLINE_S = 60          # device token seen this recently = uploader online


def visible_session(conn, user, session_id: int):
    """Session row (with character_name) if the user may see it, else 404."""
    sess = conn.execute(
        "SELECT s.*, c.name AS character_name, c.user_id AS owner_id FROM sessions s "
        "JOIN characters c ON c.id = s.character_id WHERE s.id=?",
        (session_id,),
    ).fetchone()
    if sess is None or (not is_admin(user) and sess["owner_id"] != user["id"]):
        raise HTTPException(404, "no such session")
    return sess


@router.get("/sessions")
def list_sessions(user=Depends(require_user)):
    conn = get_db()
    where, params = ("", ()) if is_admin(user) else ("WHERE c.user_id = ?", (user["id"],))
    rows = conn.execute(
        "SELECT s.id, s.source, s.status, s.error, s.started_ts, s.ended_ts, s.line_count, "
        "s.upload_name, s.created_ts, s.calibration, s.pinned, s.pruned, "
        "c.name AS character_name, "
        "(SELECT COUNT(*) FROM encounters e WHERE e.session_id = s.id) AS encounter_count "
        "FROM sessions s JOIN characters c ON c.id = s.character_id "
        f"{where} ORDER BY s.created_ts DESC",
        params).fetchall()
    return {"sessions": rows_to_dicts(rows)}


@router.get("/sessions/{session_id}")
def session_detail(session_id: int, user=Depends(require_user)):
    conn = get_db()
    sess = visible_session(conn, user, session_id)

    logger_entity = conn.execute(
        "SELECT id FROM entities WHERE session_id=? AND kind='player' AND name=?",
        (session_id, sess["character_name"]),
    ).fetchone()
    logger_id = logger_entity["id"] if logger_entity else None

    encounters = conn.execute(
        "SELECT e.id, e.zone, e.name, e.is_named, e.started_ts, e.ended_ts, e.duration_s, "
        "e.success, s.damage AS logger_damage, s.dps AS logger_dps, s.heals AS logger_heals, "
        "(SELECT COUNT(*) FROM encounter_actor_stats a WHERE a.encounter_id = e.id) AS actor_count "
        "FROM encounters e "
        "LEFT JOIN encounter_actor_stats s ON s.encounter_id = e.id AND s.entity_id = ? "
        "WHERE e.session_id=? ORDER BY e.started_ts",
        (logger_id, session_id),
    ).fetchall()

    return {"session": row_to_dict(sess), "encounters": rows_to_dicts(encounters)}


def _card_hints(e: dict) -> list[str]:
    """Basic live coach hints per finalized fight card — cheap flags from the
    logger's own rollup row, not the full coach engine."""
    hints = []
    if e["logger_deaths"]:
        hints.append(f"died {e['logger_deaths']}×")
    healed = (e["logger_heals"] or 0) + (e["logger_overheal"] or 0)
    if healed and 100 * (e["logger_overheal"] or 0) / healed > 50:
        hints.append("mostly overheal")
    warded = (e["logger_wards"] or 0) + (e["logger_ward_bleed"] or 0)
    if warded and 100 * (e["logger_ward_bleed"] or 0) / warded > 25:
        hints.append("wards punched through")
    return hints


def _encounter_cards(conn, session_id: int, character_name: str, after_id: int):
    logger_entity = conn.execute(
        "SELECT id FROM entities WHERE session_id=? AND kind='player' AND name=?",
        (session_id, character_name)).fetchone()
    logger_id = logger_entity["id"] if logger_entity else None
    rows = conn.execute(
        "SELECT e.id, e.zone, e.name, e.is_named, e.started_ts, e.ended_ts, e.duration_s, "
        "e.success, s.damage AS logger_damage, s.dps AS logger_dps, s.heals AS logger_heals, "
        "s.deaths AS logger_deaths, s.overheal_est AS logger_overheal, "
        "s.wards_absorbed AS logger_wards, s.ward_bleedthrough AS logger_ward_bleed, "
        "(SELECT COUNT(*) FROM encounter_actor_stats a WHERE a.encounter_id = e.id) AS actor_count "
        "FROM encounters e "
        "LEFT JOIN encounter_actor_stats s ON s.encounter_id = e.id AND s.entity_id = ? "
        "WHERE e.session_id=? AND e.id > ? ORDER BY e.id",
        (logger_id, session_id, after_id)).fetchall()
    cards = []
    for r in rows:
        card = dict(r)
        card["hints"] = _card_hints(card)
        cards.append(card)
    return cards


@router.get("/sessions/{session_id}/stream")
async def session_stream(session_id: int, user=Depends(require_user)):
    """SSE: `encounter` events as fights finalize, `status` heartbeats while the
    session is receiving/parsing; closes once it reaches ready/error."""
    visible_session(get_db(), user, session_id)

    async def gen():
        last_enc_id = 0
        while True:
            conn = get_db()
            sess = conn.execute(
                "SELECT s.*, c.name AS character_name, c.id AS char_id FROM sessions s "
                "JOIN characters c ON c.id = s.character_id WHERE s.id=?",
                (session_id,)).fetchone()
            if sess is None:
                break
            for e in _encounter_cards(conn, session_id, sess["character_name"], last_enc_id):
                last_enc_id = e["id"]
                yield f"event: encounter\ndata: {json.dumps(dict(e))}\n\n"
            online = conn.execute(
                "SELECT 1 FROM device_tokens WHERE character_id=? AND revoked_ts IS NULL "
                "AND last_seen_ts > ?", (sess["char_id"], int(time.time()) - ONLINE_S)
            ).fetchone() is not None
            status = {
                "status": sess["status"], "line_count": sess["line_count"],
                "last_ingest_ts": sess["last_ingest_ts"], "started_ts": sess["started_ts"],
                "ended_ts": sess["ended_ts"], "uploader_online": online,
            }
            yield f"event: status\ndata: {json.dumps(status)}\n\n"
            if sess["status"] in ("ready", "error"):
                break
            await asyncio.sleep(STREAM_POLL_S)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
