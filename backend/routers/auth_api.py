"""Username + password sign-up and cookie login. First account bootstraps as
admin (operational only — it grants no access to anyone's parses).

There is no email in the system, so the ONE self-service recovery path is the
security question chosen at sign-up: `/auth/reset/start` names the question,
`/auth/reset/complete` answers it and sets a new password. An account with no
question (every pre-v9 account) can only be reset by an admin.

Login, both reset routes and the two routes that re-check a password before
changing a credential are failure-counted by `ratelimit` on the username AND
the client address — with no email loop and no 2FA, that counter is the only
thing standing between a weak password and a script.

The address half of that only works if the address is real: behind the proxy
every request appears to come from Zoraxy, so `siteconfig.client_ip` is what
resolves it. See that module for why the naive version was a site-wide lockout
rather than a safety net.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

import auth
import ratelimit
import siteconfig
from db import get_db, get_setting
from security import current_user, require_user

router = APIRouter(tags=["auth"])


class Credentials(BaseModel):
    username: str
    password: str
    sq_id: int | None = None
    answer: str | None = None


class PasswordChange(BaseModel):
    current: str
    new: str


class QuestionChange(BaseModel):
    password: str
    sq_id: int
    answer: str


class ResetStart(BaseModel):
    username: str


class ResetComplete(BaseModel):
    username: str
    answer: str
    new_password: str


def _set_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
        httponly=True, samesite="lax", path="/",
        secure=siteconfig.is_secure(request))


def _public(user) -> dict:
    return {"id": user["id"], "username": user["username"], "role": user["role"],
            "needs_security_question": user["sq_id"] is None}


def _client_key(request: Request) -> str:
    return siteconfig.client_ip(request)


def _keys(ident: str, request: Request) -> list[str]:
    """Identity and address, deduped — counting one failure against two buckets
    that hold the same value would halve the budget it looks like it grants."""
    return list(dict.fromkeys(k for k in (ident, _client_key(request)) if k))


def _guard(scope: str, ident: str, request: Request) -> None:
    """429 if either bucket (identity or address) is spent."""
    for key in _keys(ident, request):
        wait = ratelimit.retry_after(scope, key)
        if wait:
            raise HTTPException(429, "too many attempts — wait a few minutes",
                                headers={"Retry-After": str(wait)})


def _clean_username(raw: str) -> str:
    name = (raw or "").strip().lower()
    if not auth.USERNAME_RE.match(name):
        raise HTTPException(422, "username is 3-20 characters: letters, numbers, underscore")
    if name in auth.RESERVED_USERNAMES:
        raise HTTPException(409, "that username is reserved")
    return name


def _check_password(password: str) -> None:
    if len(password or "") < 8:
        raise HTTPException(422, "password must be at least 8 characters")


@router.get("/auth/questions")
def questions():
    """The six security questions, for the sign-up and reset forms."""
    return {"questions": [{"id": i, "text": t} for i, t in auth.RESET_QUESTIONS]}


@router.post("/auth/register")
def register(creds: Credentials, request: Request, response: Response):
    conn = get_db()
    if get_setting(conn, "registration_open", "1") != "1":
        raise HTTPException(403, "sign-ups are closed on this site")
    username = _clean_username(creds.username)
    _check_password(creds.password)
    if creds.sq_id is not None and creds.sq_id not in auth.QUESTION_TEXT:
        raise HTTPException(422, "pick one of the listed security questions")
    if creds.sq_id is not None and not auth.normalize_answer(creds.answer or ""):
        raise HTTPException(422, "answer the security question so you can reset later")
    with conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise HTTPException(409, "that username is taken")
        user_id = auth.create_user(conn, username, creds.password, creds.sq_id, creds.answer)
        token = auth.create_session(conn, user_id)
        conn.execute("UPDATE users SET last_login_ts=? WHERE id=?", (int(time.time()), user_id))
    _set_cookie(response, token, request)
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return {"user": _public(user)}


@router.post("/auth/login")
def login(creds: Credentials, request: Request, response: Response):
    username = (creds.username or "").strip().lower()
    _guard("login", username, request)
    conn = get_db()
    user = auth.verify_password(conn, username, creds.password)
    if user is None:
        for key in _keys(username, request):
            ratelimit.fail("login", key)
        raise HTTPException(401, "wrong username or password")
    for key in _keys(username, request):
        ratelimit.clear("login", key)
    with conn:
        token = auth.create_session(conn, user["id"])
        conn.execute("UPDATE users SET last_login_ts=? WHERE id=?",
                     (int(time.time()), user["id"]))
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
def change_password(change: PasswordChange, request: Request,
                    user=Depends(require_user)):
    """Counted like a login. A session cookie proves you were signed in once,
    not that you know the password now — so a borrowed browser is exactly the
    place someone would sit and guess it."""
    conn = get_db()
    _guard("reauth", user["username"], request)
    if auth.verify_password(conn, user["username"], change.current) is None:
        for key in _keys(user["username"], request):
            ratelimit.fail("reauth", key)
        raise HTTPException(401, "current password is wrong")
    _check_password(change.new)
    with conn:
        auth.set_password(conn, user["id"], change.new)
    for key in _keys(user["username"], request):
        ratelimit.clear("reauth", key)
    return {"ok": True}


@router.post("/auth/security-question")
def change_question(change: QuestionChange, request: Request,
                    user=Depends(require_user)):
    """Set or replace the recovery question. Re-checks the password, because
    this is the credential that can replace the password — and counts the
    failures for the same reason `/auth/password` does."""
    conn = get_db()
    _guard("reauth", user["username"], request)
    if auth.verify_password(conn, user["username"], change.password) is None:
        for key in _keys(user["username"], request):
            ratelimit.fail("reauth", key)
        raise HTTPException(401, "password is wrong")
    if change.sq_id not in auth.QUESTION_TEXT:
        raise HTTPException(422, "pick one of the listed security questions")
    if not auth.normalize_answer(change.answer):
        raise HTTPException(422, "the answer can't be blank")
    with conn:
        auth.set_security_question(conn, user["id"], change.sq_id, change.answer)
    for key in _keys(user["username"], request):
        ratelimit.clear("reauth", key)
    return {"ok": True, "question": auth.QUESTION_TEXT[change.sq_id]}


@router.post("/auth/reset/start")
def reset_start(body: ResetStart, request: Request):
    """Name the question for a username. This confirms an account exists, which
    is not much of a secret — group invites are by username — but it is still
    counted, so the route can't be used to enumerate accounts quickly."""
    username = (body.username or "").strip().lower()
    _guard("reset", username, request)
    conn = get_db()
    row = conn.execute("SELECT sq_id, disabled_ts FROM users WHERE username=?",
                       (username,)).fetchone()
    if row is None or row["sq_id"] is None or row["disabled_ts"] is not None:
        for key in _keys(username, request):
            ratelimit.fail("reset", key)
        raise HTTPException(404, "no reset question is set for that account — ask the admin")
    return {"username": username, "sq_id": row["sq_id"],
            "question": auth.QUESTION_TEXT[row["sq_id"]]}


@router.post("/auth/reset/complete")
def reset_complete(body: ResetComplete, request: Request):
    username = (body.username or "").strip().lower()
    _guard("reset", username, request)
    _check_password(body.new_password)
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is None or row["disabled_ts"] is not None or not auth.verify_answer(
            conn, row, body.answer):
        for key in _keys(username, request):
            ratelimit.fail("reset", key)
        raise HTTPException(401, "that answer doesn't match")
    with conn:
        auth.set_password(conn, row["id"], body.new_password)
        auth.clear_sessions(conn, row["id"])   # a reset signs out every device
    for key in _keys(username, request):
        ratelimit.clear("reset", key)
    return {"ok": True}
