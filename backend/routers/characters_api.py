"""Characters CRUD, scoped to the signed-in user.

**A claim is not exclusive.** Anyone may claim any name — your "Bobby" is your
row with your logs, and it neither blocks nor reveals anyone else's Bobby. The
only conflict left is claiming the same name twice on one account."""


from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

import groups as groupsmod
from db import get_db, rows_to_dicts
from security import owned_character, require_user

router = APIRouter(tags=["characters"])


class CharacterCreate(BaseModel):
    name: str


@router.get("/characters")
def list_characters(user=Depends(require_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT c.id, c.user_id, c.name, c.world_id, c.class, c.level, "
        "(SELECT COUNT(*) FROM sessions s WHERE s.character_id = c.id) AS session_count, "
        # v13: tokens are per ACCOUNT, so these are the account's — every
        # character is uploadable the moment any device is paired.
        "(SELECT COUNT(*) FROM device_tokens t WHERE t.user_id = c.user_id "
        " AND t.revoked_ts IS NULL) AS token_count, "
        "(SELECT MAX(t.last_seen_ts) FROM device_tokens t WHERE t.user_id = c.user_id "
        " AND t.revoked_ts IS NULL) AS uploader_seen_ts "
        "FROM characters c WHERE c.user_id = ? ORDER BY c.name",
        (user["id"],)).fetchall()
    return {"characters": rows_to_dicts(rows)}


@router.post("/characters")
def create_character(body: CharacterCreate, user=Depends(require_user)):
    name = body.name.strip().capitalize()
    if not name or " " in name or not name.isalpha():
        raise HTTPException(422, "character name is a single word (as it appears in the log)")
    conn = get_db()
    with conn:
        existing = conn.execute(
            "SELECT id FROM characters WHERE user_id=? AND name=? AND world_id=618",
            (user["id"], name)).fetchone()
        if existing is not None:
            raise HTTPException(409, f"{name} is already on your account")
        char_id = conn.execute(
            "INSERT INTO characters (name, user_id, world_id) VALUES (?, ?, 618)",
            (name, user["id"])).lastrowid
    return {"id": char_id, "name": name, "claimed": False}


@router.delete("/characters/{character_id}")
def delete_character(character_id: int, user=Depends(require_user)):
    conn = get_db()
    char = owned_character(conn, user, character_id)
    sessions = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE character_id=?", (char["id"],)).fetchone()[0]
    if sessions:
        raise HTTPException(409, "character has sessions — delete is blocked to protect them")
    with conn:
        # a token outlives any one character now; just unbind a legacy one
        conn.execute("UPDATE device_tokens SET character_id=NULL WHERE character_id=?",
                     (char["id"],))
        conn.execute("DELETE FROM character_shares WHERE character_id=?", (char["id"],))
        conn.execute("DELETE FROM characters WHERE id=?", (char["id"],))
    return {"ok": True}


@router.get("/characters/{character_id}/shares")
def get_character_shares(character_id: int, user=Depends(require_user)):
    """Auto-share: groups that get every raid this character records, including
    the ones uploaded later. Evaluated at read time, so turning it off closes
    the back catalogue too."""
    conn = get_db()
    owned_character(conn, user, character_id)
    on = {r["group_id"]: r for r in conn.execute(
        "SELECT group_id, since_ts, raids_only FROM character_shares "
        "WHERE character_id=?", (character_id,))}
    # history: does the share include the back catalogue (since_ts unset)?
    # group_content: does it carry runs under a full raid's roster?
    return {"groups": [{"group_id": g["id"], "name": g["name"],
                        "shared": g["id"] in on,
                        "history": g["id"] in on and on[g["id"]]["since_ts"] is None,
                        "group_content": g["id"] in on and not on[g["id"]]["raids_only"]}
                       for g in groupsmod.my_groups(conn, user["id"])]}


@router.put("/characters/{character_id}/shares")
def set_character_shares(character_id: int, payload: dict = Body(...),
                         user=Depends(require_user)):
    conn = get_db()
    owned_character(conn, user, character_id)
    # `shares` [{group_id, history, group_content}] is the full form; a bare
    # `group_ids` list still works and means history included, raids only
    if payload.get("shares") is not None:
        wanted = {int(s["group_id"]): {"history": bool(s.get("history", True)),
                                       "group_content": bool(s.get("group_content"))}
                  for s in payload["shares"]}
    else:
        # the legacy form keeps its pre-v16 meaning exactly: everything this
        # character records, past included. Only the explicit form narrows.
        wanted = {int(x): {"history": True, "group_content": True}
                  for x in payload.get("group_ids") or []}
    mine = {g["id"] for g in groupsmod.my_groups(conn, user["id"])}
    if set(wanted) - mine:
        raise HTTPException(404, "no such group")
    with conn:
        groupsmod.set_character_auto_shares(conn, character_id, user["id"], wanted)
    return get_character_shares(character_id, user)
