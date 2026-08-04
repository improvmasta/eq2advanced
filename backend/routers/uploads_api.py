"""Drag-drop log upload -> stored gzipped raw -> background parse. Sign-in
required; the named character is created on the uploader's own account if they
haven't claimed it yet. Claims are not exclusive, so nothing here can be refused
because of someone else's character.

The gzip is content-addressed and therefore SHARED: two people who were on the
same raid upload the same bytes and get one file with two sessions pointing at
it. Deleting a session only unlinks the file when it was the last pointer."""

import gzip
import hashlib
import threading
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile

from db import UPLOADS_DIR, get_db, get_int_setting
from pipeline.ingest_writer import parse_session
from security import require_user
from siteconfig import edge_max_bytes

router = APIRouter(tags=["uploads"])

CHUNK = 1 << 20


def effective_limits(conn, user) -> dict:
    """Per-user override, else the site setting, 0 = unlimited. Ships as 0/0 —
    the machinery exists so a limit can be turned on later without a migration."""
    return {
        "upload_max_bytes": user["upload_max_bytes"]
        if user["upload_max_bytes"] is not None
        else get_int_setting(conn, "upload_max_bytes", 0),
        "storage_max_bytes": user["storage_max_bytes"]
        if user["storage_max_bytes"] is not None
        else get_int_setting(conn, "storage_max_bytes", 0),
    }


def stored_bytes(conn, user_id: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(s.raw_bytes),0) FROM sessions s "
        "JOIN characters c ON c.id = s.character_id WHERE c.user_id=?",
        (user_id,)).fetchone()[0]


@router.get("/uploads/limits")
def upload_limits(request: Request, user=Depends(require_user)):
    conn = get_db()
    limits = effective_limits(conn, user)
    # `edge_max_bytes` is not ours and there is no deal to offer around it: a
    # body over it is refused by the proxy and this app never hears about it,
    # so the browser has to check the size itself before sending.
    return {**limits, "stored_bytes": stored_bytes(conn, user["id"]),
            "edge_max_bytes": edge_max_bytes(request)}


def resolve_character(conn, user, name: str) -> int:
    """Character id for an upload: the uploader's own row for that name, created
    on the spot if this is the first time they've used it."""
    row = conn.execute(
        "SELECT id FROM characters WHERE user_id=? AND name=? AND world_id=618",
        (user["id"], name)).fetchone()
    if row is not None:
        return row["id"]
    return conn.execute(
        "INSERT INTO characters (name, user_id, world_id) VALUES (?, ?, 618)",
        (name, user["id"])).lastrowid


@router.post("/uploads")
async def upload_log(file: UploadFile, character_name: str = Form(...),
                     retain_raw: int = Form(1), user=Depends(require_user)):
    """`retain_raw=0` parses the log and then throws the bytes away — the deal
    offered when a file is over the size limit. It costs the ability to reparse
    that session when the parser improves, which the UI says out loud."""
    character_name = character_name.strip().capitalize()
    if not character_name or " " in character_name:
        raise HTTPException(422, "character_name must be the single-word first name from the log")

    conn = get_db()
    limits = effective_limits(conn, user)
    cap = limits["upload_max_bytes"]
    keep = bool(retain_raw)
    quota = limits["storage_max_bytes"]
    if keep and quota and stored_bytes(conn, user["id"]) >= quota:
        raise HTTPException(413, "you're out of storage — delete a log, or upload "
                                 "this one without keeping it",
                            headers={"X-Parse-Only-Allowed": "1"})

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    src_bytes = 0
    tmp = UPLOADS_DIR / f".incoming-{time.time_ns()}.txt.gz"
    try:
        with gzip.open(tmp, "wb") as out:
            while chunk := await file.read(CHUNK):
                sha.update(chunk)
                src_bytes += len(chunk)
                # counted as it streams: an oversized upload must never finish
                # landing on disk before anyone objects
                if cap and keep and src_bytes > cap:
                    raise HTTPException(
                        413, f"that log is over the {cap // (1 << 20)} MB limit — "
                             "upload it without keeping the file instead",
                        headers={"X-Parse-Only-Allowed": "1"})
                out.write(chunk)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    digest = sha.hexdigest()
    final = UPLOADS_DIR / f"{digest}.txt.gz"
    raw_bytes = tmp.stat().st_size

    with conn:
        char_id = resolve_character(conn, user, character_name)
        # dedupe is per character: the same night uploaded by two raiders is two
        # sessions, the same file uploaded twice by one of them is not
        existing = conn.execute(
            "SELECT id, status FROM sessions WHERE upload_sha256=? AND character_id=?",
            (digest, char_id)).fetchone()
        if existing:
            tmp.unlink(missing_ok=True)
            return {"session_id": existing["id"], "status": existing["status"],
                    "duplicate": True}
        if final.exists():
            tmp.unlink(missing_ok=True)      # someone already stored these bytes
        else:
            tmp.rename(final)
        session_id = conn.execute(
            "INSERT INTO sessions (character_id, source, status, upload_sha256, upload_name, "
            "src_bytes, raw_bytes, retain_raw, created_ts) "
            "VALUES (?, 'upload', 'parsing', ?, ?, ?, ?, ?, ?)",
            (char_id, digest, file.filename, src_bytes, raw_bytes, int(keep),
             int(time.time())),
        ).lastrowid

    threading.Thread(target=parse_session, args=(session_id, final), daemon=True).start()
    return {"session_id": session_id, "status": "parsing", "duplicate": False}
