"""Live ingest endpoints — the frozen contract the ACT uploader DLL is built
against (plan md -> "Ingest contract"). Auth is a device token only; no cookies.

  GET  /api/ingest/hello          -> {character, server_time, session}
  POST /api/ingest/batch          -> {"accepted": N, "duplicates": M, "session_id": K}
       gzip- or plain-JSON body {"batch_id": uuid, "mode": "live"|"backfill",
       "lines": [raw verbatim lines with prefix]}; idempotent per (token, batch_id)
  POST /api/ingest/backfill/done  -> close hint; finalizes the live session

One batch in flight per token (429 + Retry-After otherwise). ~2MB gzip cap.

**A device token sends logs and does nothing else.** It cannot read a parse
back, and it cannot change who sees one: sharing is decided on the site, by
someone signed in, on the Characters page (standing auto-share) or a raid's own
Share control. v11 briefly let the ACT plugin set sharing here and v12 removed
it — see `groups.py`. Keep this file a pipe.
"""

import gzip
import json
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from auth import device_token_character
from db import get_db
from pipeline.live import finalize_live_session, process_batch

router = APIRouter(tags=["ingest"])

MAX_BODY = 2 * 1024 * 1024        # gzip (or plain) body cap
MAX_RAW = 64 * 1024 * 1024        # decompressed cap (zip-bomb guard)

_token_locks: dict[int, threading.Lock] = {}
_locks_lock = threading.Lock()


def _token_lock(token_id: int) -> threading.Lock:
    with _locks_lock:
        return _token_locks.setdefault(token_id, threading.Lock())


def require_device(request: Request):
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else None
    row = device_token_character(get_db(), token)
    if row is None:
        raise HTTPException(401, "invalid or revoked device token")
    get_db().commit()  # persist the last_seen_ts touch
    return row


def _open_session_id(char) -> int | None:
    row = get_db().execute(
        "SELECT id FROM sessions WHERE character_id=? AND source='live' "
        "AND status='receiving' ORDER BY id DESC LIMIT 1", (char["id"],)).fetchone()
    return row["id"] if row else None


@router.get("/ingest/hello")
def ingest_hello(request: Request):
    char = require_device(request)
    return {
        "character": {"id": char["id"], "name": char["name"]},
        "server_time": int(time.time()),
        "session": _open_session_id(char),
    }


@router.post("/ingest/batch")
async def ingest_batch(request: Request):
    char = await run_in_threadpool(require_device, request)

    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "batch too large")
    if request.headers.get("content-encoding", "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            raise HTTPException(400, "bad gzip body")
        if len(body) > MAX_RAW:
            raise HTTPException(413, "batch too large")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "body must be JSON")

    batch_id = payload.get("batch_id")
    mode = payload.get("mode", "live")
    lines = payload.get("lines")
    if not isinstance(batch_id, str) or not batch_id:
        raise HTTPException(422, "batch_id required")
    if mode not in ("live", "backfill"):
        raise HTTPException(422, "mode must be live or backfill")
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        raise HTTPException(422, "lines must be a list of strings")

    lock = _token_lock(char["token_id"])
    if not lock.acquire(blocking=False):
        raise HTTPException(429, "batch already in flight",
                            headers={"Retry-After": "2"})
    try:
        return await run_in_threadpool(process_batch, char, batch_id, mode, lines)
    finally:
        lock.release()


@router.post("/ingest/backfill/done")
async def ingest_done(request: Request):
    char = await run_in_threadpool(require_device, request)
    sess = get_db().execute(
        "SELECT id FROM sessions WHERE character_id=? AND source='live' "
        "AND status='receiving' ORDER BY id DESC LIMIT 1", (char["id"],)).fetchone()
    if sess is None:
        return {"ok": True, "session_id": None}
    lock = _token_lock(char["token_id"])
    if not lock.acquire(blocking=False):
        raise HTTPException(429, "batch in flight", headers={"Retry-After": "2"})
    try:
        await run_in_threadpool(finalize_live_session, sess["id"])
    finally:
        lock.release()
    return {"ok": True, "session_id": sess["id"]}
