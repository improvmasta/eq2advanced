"""Live ingest endpoints — the frozen contract the ACT uploader DLL is built
against (plan md -> "Ingest contract"). Auth is a device token only; no cookies.

  GET  /api/ingest/hello          -> {character, server_time, session, sharing}
  POST /api/ingest/batch          -> {"accepted": N, "duplicates": M, "session_id": K}
       gzip- or plain-JSON body {"batch_id": uuid, "mode": "live"|"backfill",
       "lines": [raw verbatim lines with prefix], "share_groups": [ids]?};
       idempotent per (token, batch_id)
  POST /api/ingest/backfill/done  -> close hint; finalizes the live session
  GET  /api/ingest/shares         -> the sharing panel's state
  PUT  /api/ingest/shares         -> set the character's standing auto-share

One batch in flight per token (429 + Retry-After otherwise). ~2MB gzip cap.

Sharing (v11) is why this file knows about groups at all. The plugin has two
controls and they are deliberately different things:

  * the STANDING default — `character_shares`, every raid this character ever
    records, back catalogue included. Changing it is a real privacy decision,
    so it needs a token minted with `can_share` and lives behind PUT /shares.
  * THIS raid — `session_shares`, sent as `share_groups` on each batch, scoped
    to the session the batch opens. Also gated on `can_share`: it is narrower,
    but it still publishes a night to other people.

Both are read at query time by `groups.VISIBLE_RUN_IDS`, so nothing here writes
anything a later reparse could resurrect, and a `hide` set on the site takes
either one back.
"""

import gzip
import json
import threading
import time

from fastapi import APIRouter, Body, HTTPException, Request
from starlette.concurrency import run_in_threadpool

import groups as groupsmod
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


def _sharing_state(char) -> dict:
    """What the plugin's sharing panel draws: the owner's groups, which of them
    the character auto-shares with, and what the session in flight (if any) is
    set to. `can_share` false means render it read-only — the answer is still
    worth showing, because "who can see this" is the thing the raider actually
    wants to know before they start streaming."""
    conn = get_db()
    uid = char["owner_user_id"]
    auto = set(groupsmod.character_auto_shares(conn, char["id"]))
    session_id = _open_session_id(char)
    return {
        "can_share": bool(char["can_share"]),
        "groups": [{"group_id": g["id"], "name": g["name"], "auto": g["id"] in auto}
                   for g in groupsmod.my_groups(conn, uid)],
        "session_id": session_id,
        "session_groups": groupsmod.session_share_groups(conn, session_id)
                          if session_id else [],
    }


def _checked_share_groups(char, raw) -> set[int]:
    """Validate a share list off the wire. Unknown or not-mine group ids 404
    rather than being dropped: a plugin that silently shared with fewer people
    than its checkboxes show would be lying about the one thing here that
    matters."""
    if not bool(char["can_share"]):
        raise HTTPException(403, "this device token may not change sharing — "
                                 "mint one with sharing enabled on the site")
    if not isinstance(raw, list) or not all(isinstance(x, int) and not isinstance(x, bool)
                                            for x in raw):
        raise HTTPException(422, "share_groups must be a list of group ids")
    wanted = set(raw)
    mine = {g["id"] for g in groupsmod.my_groups(get_db(), char["owner_user_id"])}
    if wanted - mine:
        raise HTTPException(404, "no such group")
    return wanted


@router.get("/ingest/hello")
def ingest_hello(request: Request):
    char = require_device(request)
    return {
        "character": {"id": char["id"], "name": char["name"]},
        "server_time": int(time.time()),
        "session": _open_session_id(char),
        "sharing": _sharing_state(char),
    }


@router.get("/ingest/shares")
def ingest_shares(request: Request):
    return _sharing_state(require_device(request))


@router.put("/ingest/shares")
def set_ingest_shares(request: Request, payload: dict = Body(...)):
    """The STANDING default only — every raid this character records, including
    the ones already uploaded. "This raid" is `share_groups` on the batch."""
    char = require_device(request)
    wanted = _checked_share_groups(char, payload.get("auto_groups"))
    conn = get_db()
    with conn:
        groupsmod.set_character_auto_shares(conn, char["id"], char["owner_user_id"], wanted)
    return _sharing_state(char)


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
    share_groups = None
    if "share_groups" in payload:
        share_groups = await run_in_threadpool(_checked_share_groups, char,
                                               payload["share_groups"])

    lock = _token_lock(char["token_id"])
    if not lock.acquire(blocking=False):
        raise HTTPException(429, "batch already in flight",
                            headers={"Retry-After": "2"})
    try:
        return await run_in_threadpool(process_batch, char, batch_id, mode, lines,
                                       share_groups)
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
