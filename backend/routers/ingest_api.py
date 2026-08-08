"""Live ingest endpoints — the frozen contract the ACT uploader DLL is built
against (plan md -> "Ingest contract"). Auth is a device token only; no cookies.

  GET  /api/ingest/hello          -> {account, server_time, session}
  POST /api/ingest/batch          -> {"accepted": N, "duplicates": M, "session_id": K}
       gzip- or plain-JSON body {"batch_id": uuid, "mode": "live"|"backfill",
       "character": "Bobby", "lines": [raw verbatim lines with prefix]};
       idempotent per (token, batch_id)
  POST /api/ingest/backfill/done  -> close hint; finalizes the live session
       optional {"character": "Bobby"} to say WHICH one to close

One batch in flight per token (429 + Retry-After otherwise). ~2MB gzip cap.

**A device token sends logs and does nothing else.** It cannot read a parse
back, and it cannot change who sees one: sharing is decided on the site, by
someone signed in. v11 briefly let the ACT plugin set sharing here and v12
removed it — see `groups.py`. Keep this file a pipe.

**A token belongs to an ACCOUNT, not a character** (v13). Each batch names the
character it came from — the plugin reads that off the log file ACT is tailing —
and a name this account hasn't used before is created on the spot. So playing an
alt needs no setup, and one pairing covers every character. Tokens minted before
v13 still carry a character and are used as the fallback.
"""

import gzip
import json
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from auth import device_token_row, resolve_ingest_character
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
    # The User-Agent is how the site knows which plugin build a raider is on —
    # it is the only thing the plugin volunteers about itself, and an old one
    # is exactly who the update pill is for (auth.client_version).
    row = device_token_row(get_db(), token, request.headers.get("user-agent"))
    if row is None:
        raise HTTPException(401, "invalid or revoked device token")
    get_db().commit()  # persist the last_seen_ts touch
    return row


def _character(token_row, name):
    """The character a request is about, created if this account hasn't used the
    name before."""
    if name is not None and not isinstance(name, str):
        raise HTTPException(422, "character must be a string")
    conn = get_db()
    char = resolve_ingest_character(conn, token_row, name)
    if char is None:
        raise HTTPException(422, "character required — send the name from the log "
                                 "(eq2log_<name>.txt) with the batch")
    conn.commit()
    return char


@router.get("/ingest/hello")
def ingest_hello(request: Request):
    """Pairing check. Answers for the ACCOUNT — there is no character to name
    until a log turns up — and lists any sessions currently receiving, which is
    what the plugin shows as "uploading as"."""
    token_row = require_device(request)
    conn = get_db()
    user = conn.execute("SELECT username FROM users WHERE id=?",
                        (token_row["user_id"],)).fetchone()
    open_rows = conn.execute(
        "SELECT s.id, c.name FROM sessions s JOIN characters c ON c.id = s.character_id "
        "WHERE c.user_id=? AND s.source='live' AND s.status='receiving' "
        "ORDER BY s.id DESC", (token_row["user_id"],)).fetchall()
    return {
        "account": user["username"] if user else None,
        "label": token_row["label"],
        "server_time": int(time.time()),
        "session": open_rows[0]["id"] if open_rows else None,
        "receiving": [{"session_id": r["id"], "character": r["name"]} for r in open_rows],
    }


@router.post("/ingest/batch")
async def ingest_batch(request: Request):
    token_row = await run_in_threadpool(require_device, request)

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
    char = await run_in_threadpool(_character, token_row, payload.get("character"))

    lock = _token_lock(token_row["token_id"])
    if not lock.acquire(blocking=False):
        raise HTTPException(429, "batch already in flight",
                            headers={"Retry-After": "2"})
    try:
        return await run_in_threadpool(process_batch, token_row, char, batch_id, mode, lines)
    finally:
        lock.release()


@router.post("/ingest/backfill/done")
async def ingest_done(request: Request):
    """Close a receiving session. With a `character` it closes THAT one; without,
    it closes the account's most recent — which is what a plugin finishing an
    import wants, and what a pre-v13 plugin (whose token names one character)
    gets by falling back to it."""
    token_row = await run_in_threadpool(require_device, request)
    try:
        payload = json.loads(await request.body() or b"{}")
    except ValueError:
        payload = {}
    name = payload.get("character") if isinstance(payload, dict) else None

    conn = get_db()
    if name or token_row["character_id"]:
        char = await run_in_threadpool(_character, token_row, name)
        sess = conn.execute(
            "SELECT id FROM sessions WHERE character_id=? AND source='live' "
            "AND status='receiving' ORDER BY id DESC LIMIT 1", (char["id"],)).fetchone()
    else:
        sess = conn.execute(
            "SELECT s.id FROM sessions s JOIN characters c ON c.id = s.character_id "
            "WHERE c.user_id=? AND s.source='live' AND s.status='receiving' "
            "ORDER BY s.id DESC LIMIT 1", (token_row["user_id"],)).fetchone()
    if sess is None:
        return {"ok": True, "session_id": None}

    lock = _token_lock(token_row["token_id"])
    if not lock.acquire(blocking=False):
        raise HTTPException(429, "batch in flight", headers={"Retry-After": "2"})
    try:
        await run_in_threadpool(finalize_live_session, sess["id"])
    finally:
        lock.release()
    return {"ok": True, "session_id": sess["id"]}
