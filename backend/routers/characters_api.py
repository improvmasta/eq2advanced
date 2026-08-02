"""Characters CRUD, scoped to the signed-in user (admin sees all).

Phase-1 uploads created characters with no owner (user_id NULL). Creating a
character whose (name, world) row already exists unowned CLAIMS it — the
existing sessions come along. A name owned by someone else is a 409."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_db, rows_to_dicts
from security import is_admin, owned_character, require_user

router = APIRouter(tags=["characters"])


class CharacterCreate(BaseModel):
    name: str


@router.get("/characters")
def list_characters(user=Depends(require_user)):
    conn = get_db()
    where, params = ("", ()) if is_admin(user) else ("WHERE c.user_id = ?", (user["id"],))
    rows = conn.execute(
        "SELECT c.id, c.user_id, c.name, c.world_id, c.class, c.level, "
        "(SELECT COUNT(*) FROM sessions s WHERE s.character_id = c.id) AS session_count, "
        "(SELECT COUNT(*) FROM device_tokens t WHERE t.character_id = c.id "
        " AND t.revoked_ts IS NULL) AS token_count, "
        "(SELECT MAX(t.last_seen_ts) FROM device_tokens t WHERE t.character_id = c.id "
        " AND t.revoked_ts IS NULL) AS uploader_seen_ts "
        f"FROM characters c {where} ORDER BY c.name",
        params).fetchall()
    return {"characters": rows_to_dicts(rows)}


@router.post("/characters")
def create_character(body: CharacterCreate, user=Depends(require_user)):
    name = body.name.strip().capitalize()
    if not name or " " in name or not name.isalpha():
        raise HTTPException(422, "character name is a single word (as it appears in the log)")
    conn = get_db()
    with conn:
        existing = conn.execute(
            "SELECT * FROM characters WHERE name=? AND world_id=618", (name,)).fetchone()
        if existing is None:
            char_id = conn.execute(
                "INSERT INTO characters (name, user_id, world_id) VALUES (?, ?, 618)",
                (name, user["id"])).lastrowid
            claimed = False
        elif existing["user_id"] is None:
            conn.execute("UPDATE characters SET user_id=? WHERE id=?",
                         (user["id"], existing["id"]))
            char_id, claimed = existing["id"], True
        elif existing["user_id"] == user["id"]:
            raise HTTPException(409, f"{name} is already on your account")
        else:
            raise HTTPException(409, f"{name} is already paired to another account")
    return {"id": char_id, "name": name, "claimed": claimed}


@router.delete("/characters/{character_id}")
def delete_character(character_id: int, user=Depends(require_user)):
    conn = get_db()
    char = owned_character(conn, user, character_id)
    sessions = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE character_id=?", (char["id"],)).fetchone()[0]
    if sessions:
        raise HTTPException(409, "character has sessions — delete is blocked to protect them")
    with conn:
        conn.execute("DELETE FROM device_tokens WHERE character_id=?", (char["id"],))
        conn.execute("DELETE FROM characters WHERE id=?", (char["id"],))
    return {"ok": True}
