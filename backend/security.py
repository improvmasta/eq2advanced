"""Request auth dependencies. Per-user isolation rule: a user sees only data
hanging off their own characters; admin sees everything."""

from fastapi import Depends, HTTPException, Request

from auth import COOKIE, session_user
from db import get_db


def current_user(request: Request):
    return session_user(get_db(), request.cookies.get(COOKIE))


def require_user(request: Request):
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "not signed in")
    return user


def require_admin(user=Depends(require_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    return user


def is_admin(user) -> bool:
    return user["role"] == "admin"


def owned_character(conn, user, character_id: int):
    """Character row if the user may act on it (owner or admin), else 404 —
    a foreign character id shouldn't leak existence."""
    char = conn.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone()
    if char is None or (not is_admin(user) and char["user_id"] != user["id"]):
        raise HTTPException(404, "no such character")
    return char
