"""Drag-drop log upload -> stored gzipped raw -> background parse. Sign-in
required; the named character is created on (or claimed by) the uploader's
account, and a name paired to a different account is refused."""

import gzip
import hashlib
import threading
import time

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from db import UPLOADS_DIR, get_db
from pipeline.ingest_writer import parse_session
from security import is_admin, require_user

router = APIRouter(tags=["uploads"])

CHUNK = 1 << 20


def resolve_character(conn, user, name: str) -> int:
    """Character id for an upload: the user's own, a claimed unowned row, or a
    fresh row on their account. Someone else's character is a 409."""
    row = conn.execute(
        "SELECT id, user_id FROM characters WHERE name=? AND world_id=618", (name,)).fetchone()
    if row is None:
        return conn.execute(
            "INSERT INTO characters (name, user_id, world_id) VALUES (?, ?, 618)",
            (name, user["id"])).lastrowid
    if row["user_id"] is None:
        conn.execute("UPDATE characters SET user_id=? WHERE id=?", (user["id"], row["id"]))
        return row["id"]
    if row["user_id"] != user["id"] and not is_admin(user):
        raise HTTPException(409, f"{name} is already paired to another account")
    return row["id"]


@router.post("/uploads")
async def upload_log(file: UploadFile, character_name: str = Form(...),
                     user=Depends(require_user)):
    character_name = character_name.strip().capitalize()
    if not character_name or " " in character_name:
        raise HTTPException(422, "character_name must be the single-word first name from the log")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    tmp = UPLOADS_DIR / f".incoming-{time.time_ns()}.txt.gz"
    with gzip.open(tmp, "wb") as out:
        while chunk := await file.read(CHUNK):
            sha.update(chunk)
            out.write(chunk)
    digest = sha.hexdigest()
    final = UPLOADS_DIR / f"{digest}.txt.gz"

    conn = get_db()
    existing = conn.execute(
        "SELECT s.id, s.status, c.user_id FROM sessions s "
        "JOIN characters c ON c.id = s.character_id WHERE s.upload_sha256=?",
        (digest,)).fetchone()
    if existing:
        tmp.unlink(missing_ok=True)
        if existing["user_id"] != user["id"] and not is_admin(user):
            raise HTTPException(409, "that log is already uploaded on another account")
        return {"session_id": existing["id"], "status": existing["status"], "duplicate": True}
    tmp.rename(final)

    with conn:
        char_id = resolve_character(conn, user, character_name)
        session_id = conn.execute(
            "INSERT INTO sessions (character_id, source, status, upload_sha256, upload_name, created_ts) "
            "VALUES (?, 'upload', 'parsing', ?, ?, ?)",
            (char_id, digest, file.filename, int(time.time())),
        ).lastrowid

    threading.Thread(target=parse_session, args=(session_id, final), daemon=True).start()
    return {"session_id": session_id, "status": "parsing", "duplicate": False}
