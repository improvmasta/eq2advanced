"""Open sign-up + cookie login. First account bootstraps as admin."""

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

import auth
from db import get_db
from security import current_user, require_user

router = APIRouter(tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str
    password: str


class PasswordChange(BaseModel):
    current: str
    new: str


def _set_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
        httponly=True, samesite="lax", path="/",
        secure=request.url.scheme == "https")


def _public(user) -> dict:
    return {"id": user["id"], "email": user["email"], "role": user["role"]}


@router.post("/auth/register")
def register(creds: Credentials, request: Request, response: Response):
    email = creds.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "enter a valid email address")
    if len(creds.password) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    conn = get_db()
    with conn:
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "an account with that email already exists")
        user_id = auth.create_user(conn, email, creds.password)
        token = auth.create_session(conn, user_id)
    _set_cookie(response, token, request)
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return {"user": _public(user)}


@router.post("/auth/login")
def login(creds: Credentials, request: Request, response: Response):
    conn = get_db()
    user = auth.verify_password(conn, creds.email.strip().lower(), creds.password)
    if user is None:
        raise HTTPException(401, "wrong email or password")
    with conn:
        token = auth.create_session(conn, user["id"])
    _set_cookie(response, token, request)
    return {"user": _public(user)}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    conn = get_db()
    with conn:
        auth.delete_session(conn, request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request):
    user = current_user(request)
    return {"user": _public(user) if user else None}


@router.post("/auth/password")
def change_password(change: PasswordChange, user=Depends(require_user)):
    conn = get_db()
    if auth.verify_password(conn, user["email"], change.current) is None:
        raise HTTPException(401, "current password is wrong")
    if len(change.new) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    with conn:
        auth.set_password(conn, user["id"], change.new)
    return {"ok": True}
