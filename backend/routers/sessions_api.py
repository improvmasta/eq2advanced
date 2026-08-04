"""Session listing + detail (zones -> encounters with the logger's headline).

A session is an uploaded FILE, and files are private — strictly owner-only, with
no group-share and no admin path. Sharing operates on zone runs (the raids you
were in), so a shared night exposes those fights' derived stats and never the
log, the other fights in the same file, or its parse plumbing.

The /stream endpoint is SSE for the Live page: it polls the DB and pushes fight
cards as the live ingest path finalizes encounters."""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from db import get_db, row_to_dict, rows_to_dicts
from security import owned_session, require_user

router = APIRouter(tags=["sessions"])

STREAM_POLL_S = 1.5
ONLINE_S = 60          # device token seen this recently = uploader online


# kept as a name because half the app imports it; ownership IS the rule now
visible_session = owned_session


@router.get("/sessions")
def list_sessions(user=Depends(require_user)):
    conn = get_db()
    where, params = "WHERE c.user_id = ?", (user["id"],)
    rows = conn.execute(
        "SELECT s.id, s.source, s.status, s.error, s.started_ts, s.ended_ts, s.line_count, "
        "s.upload_name, s.created_ts, s.calibration, s.pinned, s.pruned, "
        "c.name AS character_name, "
        "(SELECT COUNT(*) FROM encounters e WHERE e.session_id = s.id "
        " AND e.deleted_ts IS NULL) AS encounter_count "
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
        "WHERE e.session_id=? AND e.deleted_ts IS NULL ORDER BY e.started_ts",
        (logger_id, session_id),
    ).fetchall()

    return {"session": row_to_dict(sess), "encounters": rows_to_dicts(encounters)}


@router.post("/sessions/{session_id}/reparse")
def reparse_session(session_id: int, user=Depends(require_user)):
    """Re-run the parse from stored raw — picks up parser fixes and newly
    learned pet knowledge. Refused for pruned sessions (frozen by design)."""
    import threading

    from pipeline.ingest_writer import parse_session, session_raw_paths

    conn = get_db()
    sess = visible_session(conn, user, session_id)
    if sess["pruned"]:
        raise HTTPException(409, "session is pruned; its report is frozen")
    if sess["status"] not in ("ready", "error"):
        raise HTTPException(409, f"session is {sess['status']}")
    if sess["raw_deleted_ts"]:
        raise HTTPException(409, "this log was parsed without being kept — there is "
                                 "nothing left to reparse")
    paths = session_raw_paths(conn, session_id)
    if not paths:
        raise HTTPException(409, "no stored raw log for this session")
    threading.Thread(target=parse_session, args=(session_id, paths), daemon=True).start()
    return {"session_id": session_id, "status": "parsing"}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, user=Depends(require_user)):
    """Delete an uploaded log for good: derived rows, ingest bookkeeping, the
    frozen reports, and the raw bytes. The stored upload is content-addressed,
    so the file only goes when the last session pointing at it does."""
    from pathlib import Path

    from db import UPLOADS_DIR
    from pipeline.ingest_writer import clear_derived
    from pipeline.zoneruns import encounter_fp, rebuild_zone_runs

    conn = get_db()
    sess = visible_session(conn, user, session_id)
    if sess["status"] in ("parsing", "receiving"):
        raise HTTPException(409, f"session is {sess['status']}")

    chunk_paths = [r["path"] for r in conn.execute(
        "SELECT path FROM raw_chunks WHERE session_id=?", (session_id,))]
    # deleting the log forgets its hand edits too — otherwise re-uploading the
    # same file would come back with every fight you had deleted still hidden,
    # and nothing on screen to explain why
    own_fps = {encounter_fp(r) for r in conn.execute(
        "SELECT started_ts, zone, name FROM encounters WHERE session_id=?",
        (session_id,))}
    with conn:
        clear_derived(conn, session_id)
        for table in ("ingest_lines", "ingest_batches", "raw_chunks",
                      "raid_reports", "coach_reports"):
            conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        survivors = {encounter_fp(r) for r in conn.execute(
            "SELECT e.started_ts, e.zone, e.name FROM encounters e "
            "JOIN sessions s ON s.id = e.session_id WHERE s.character_id=?",
            (sess["character_id"],))}
        for fp in own_fps - survivors:
            conn.execute("DELETE FROM run_edits WHERE character_id=? AND fp=?",
                         (sess["character_id"], fp))
        rebuild_zone_runs(conn, sess["character_id"])

    for path in chunk_paths:
        Path(path).unlink(missing_ok=True)
    if sess["source"] == "upload" and sess["upload_sha256"]:
        shared = conn.execute("SELECT 1 FROM sessions WHERE upload_sha256=? LIMIT 1",
                              (sess["upload_sha256"],)).fetchone()
        if shared is None:
            (UPLOADS_DIR / f"{sess['upload_sha256']}.txt.gz").unlink(missing_ok=True)
    return {"deleted": session_id}


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
