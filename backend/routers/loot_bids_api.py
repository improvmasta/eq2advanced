"""Private Skill Issue portal API for live loot bidding."""

import time

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response

import auth
import lootbids
import siteconfig
from db import get_db
from security import optional_user

router = APIRouter(tags=["loot-bids"])


def _token(x_loot_bid_token: str | None) -> str:
    if not x_loot_bid_token:
        raise HTTPException(401, "Skill Issue portal token required")
    return x_loot_bid_token


def _call(fn, *args):
    try:
        return fn(get_db(), *args)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(409, str(e))


@router.post("/loot-bids/enroll")
def enroll(body: dict = Body(...)):
    try:
        token, board = lootbids.enroll(
            get_db(), body.get("name"), body.get("invite_code"))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"token": token, "board": board}


@router.get("/loot-bids/account-access")
def account_access(user=Depends(optional_user)):
    result = lootbids.account_access(get_db(), user)
    if result is None:
        raise HTTPException(404, "this eq2advanced account is not linked to the portal")
    token, board = result
    return {"token": token, "board": board}


@router.patch("/loot-bids/profile")
def profile(body: dict = Body(...), x_loot_bid_token: str | None = Header(None)):
    return _call(lootbids.update_profile, _token(x_loot_bid_token), body.get("name"))


@router.patch("/loot-bids/members/{member_id}")
def member_role(member_id: int, body: dict = Body(...),
                x_loot_bid_token: str | None = Header(None)):
    return _call(lootbids.set_member_role, _token(x_loot_bid_token), member_id,
                 bool(body.get("officer")))


@router.post("/loot-bids/convert-account")
def convert_account(request: Request, response: Response, body: dict = Body(...),
                    x_loot_bid_token: str | None = Header(None)):
    conn = get_db()
    token = _token(x_loot_bid_token)
    who = lootbids.participant(conn, token, touch=True)
    if who is None:
        raise HTTPException(403, "this portal token is not valid")
    if who["user_id"] is not None:
        raise HTTPException(409, "this member already has an eq2advanced account")
    username = (body.get("username") or "").strip().lower()
    password = body.get("password") or ""
    if not auth.USERNAME_RE.fullmatch(username):
        raise HTTPException(422, "username is 3-20 characters: letters, numbers, underscore")
    if username in auth.RESERVED_USERNAMES:
        raise HTTPException(409, "that username is reserved")
    if len(password) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    with conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise HTTPException(409, "that username is taken")
        user_id = auth.create_user(conn, username, password)
        # Portal conversion never bootstraps operational site administration,
        # even in an empty development database.
        conn.execute("UPDATE users SET role='user' WHERE id=?", (user_id,))
        conn.execute("UPDATE loot_bid_participants SET user_id=? WHERE token_hash=?",
                     (user_id, who["token_hash"]))
        session = auth.create_session(conn, user_id)
        conn.execute("UPDATE users SET last_login_ts=? WHERE id=?",
                     (int(time.time()), user_id))
    response.set_cookie(
        auth.COOKIE, session, max_age=auth.SESSION_DAYS * 86400,
        httponly=True, samesite="lax", path="/", secure=siteconfig.is_secure(request))
    return lootbids.state(conn, token)


@router.get("/loot-bids/state")
def board(x_loot_bid_token: str | None = Header(None)):
    return _call(lootbids.state, _token(x_loot_bid_token))


@router.post("/loot-bids/items/{item_row_id}/bid")
def bid(item_row_id: int, body: dict = Body(...),
        x_loot_bid_token: str | None = Header(None)):
    return _call(lootbids.put_bid, _token(x_loot_bid_token), item_row_id,
                 body.get("bid"))


@router.post("/loot-bids/items/{item_row_id}/award")
def award(item_row_id: int, body: dict = Body(default={}),
          x_loot_bid_token: str | None = Header(None)):
    return _call(lootbids.finalize, _token(x_loot_bid_token), item_row_id,
                 body.get("awards"))


@router.post("/loot-bids/test-chest")
def test_chest(x_loot_bid_token: str | None = Header(None)):
    return _call(lootbids.open_test_chest, _token(x_loot_bid_token))


@router.post("/loot-bids/items/{item_row_id}/test-link")
def test_link(item_row_id: int, x_loot_bid_token: str | None = Header(None)):
    return _call(lootbids.link_test_item, _token(x_loot_bid_token), item_row_id)


@router.post("/loot-bids/officer/events")
def officer_events(body: dict = Body(...),
                   x_loot_bid_token: str | None = Header(None)):
    return _call(lootbids.relay_events, _token(x_loot_bid_token),
                 body.get("lines"), body.get("logger"), body.get("mob"),
                 body.get("zone"))
