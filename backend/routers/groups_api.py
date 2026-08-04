"""Groups: who you raid with, and therefore who can see your raids.

Two ways in, both deliberate: an invite addressed to a username (the invitee
accepts), or a 6-digit join code the owner hands out. The code is short because
it gets read aloud in voice chat; a million codes is not much, so joining is
failure-counted (`ratelimit`) and the owner can rotate it at any time.

Membership is the only thing stored here. What each group can SEE is decided at
read time from `character_shares` / `run_shares` — see `groups.py`.
"""

import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

import groups as g
import ratelimit
import siteconfig
from db import get_db, rows_to_dicts
from security import optional_user, require_user

router = APIRouter(tags=["groups"])

MAX_GROUPS_PER_USER = 50


class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    join_code: str | None = None    # claim the code /groups/new-code handed out


class InviteCreate(BaseModel):
    username: str


class JoinBody(BaseModel):
    code: str


def _group(conn, group_id: int, user, manage: bool = False):
    """The group row, if the caller is in it. Non-members get 404 — a group's
    existence is not public. `manage=True` additionally requires owner/admin."""
    row = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    if row is None or not g.is_member(conn, group_id, user["id"]):
        raise HTTPException(404, "no such group")
    if manage and not g.can_manage(conn, group_id, user["id"]):
        raise HTTPException(403, "only the group's owner or admins can do that")
    return row


def _clean_name(raw: str) -> str:
    name = (raw or "").strip()
    if not 2 <= len(name) <= 40:
        raise HTTPException(422, "group name is 2-40 characters")
    return name


@router.get("/groups")
def list_groups(user=Depends(require_user)):
    conn = get_db()
    invites = rows_to_dicts(conn.execute(
        "SELECT i.id, i.group_id, i.created_ts, gr.name AS group_name, "
        "u.username AS invited_by_username "
        "FROM group_invites i JOIN groups gr ON gr.id = i.group_id "
        "JOIN users u ON u.id = i.invited_by "
        "WHERE i.user_id=? AND i.status='pending' ORDER BY i.created_ts DESC",
        (user["id"],)))
    # An invite link is pasted into Discord, so it has to be the address the
    # site answers on from outside — not whatever the sender happened to have
    # in their address bar (a LAN ip:port that means nothing to the recipient).
    return {"groups": g.my_groups(conn, user["id"]), "invites": invites,
            "invite_base": siteconfig.public_base_url()}


@router.get("/groups/new-code")
def new_code(user=Depends(require_user)):
    """A free join code, so the create form can show the code and the invite
    link while the name is still being typed.

    Nothing is reserved — the code is claimed by `POST /groups` and re-minted
    there if it was taken in between. That keeps an abandoned form from burning
    codes, at the cost of a race that the create response resolves by telling
    you what the group actually ended up with."""
    return {"code": g.new_join_code(get_db())}


@router.post("/groups")
def create_group(body: GroupCreate, user=Depends(require_user)):
    conn = get_db()
    name = _clean_name(body.name)
    if len(g.my_groups(conn, user["id"])) >= MAX_GROUPS_PER_USER:
        raise HTTPException(409, "you're in too many groups")
    now = int(time.time())
    code = (body.join_code or "").strip()
    if not (len(code) == 6 and code.isdigit()) or conn.execute(
            "SELECT 1 FROM groups WHERE join_code=?", (code,)).fetchone():
        code = g.new_join_code(conn)      # never offered, or claimed meanwhile
    with conn:
        group_id = conn.execute(
            "INSERT INTO groups (name, description, owner_user_id, join_code, "
            "join_code_ts, created_ts) VALUES (?,?,?,?,?,?)",
            (name, (body.description or "").strip() or None, user["id"],
             code, now, now)).lastrowid
        g.add_member(conn, group_id, user["id"], "owner")
    return group_detail(group_id, user)


@router.get("/groups/{group_id}")
def group_detail(group_id: int, user=Depends(require_user)):
    conn = get_db()
    row = _group(conn, group_id, user)
    members = rows_to_dicts(conn.execute(
        "SELECT m.user_id, m.role, m.joined_ts, u.username FROM group_members m "
        "JOIN users u ON u.id = m.user_id WHERE m.group_id=? ORDER BY m.joined_ts",
        (group_id,)))
    manage = g.can_manage(conn, group_id, user["id"])
    out = {"id": row["id"], "name": row["name"], "description": row["description"],
           "owner_user_id": row["owner_user_id"], "created_ts": row["created_ts"],
           "my_role": g.member_role(conn, group_id, user["id"]),
           "members": members}
    if manage:
        # the code is a credential: only the people who can rotate it see it
        out["join_code"] = row["join_code"] if g.code_is_live(row) else None
        out["join_code_expires_ts"] = row["join_code_expires_ts"]
        out["pending_invites"] = rows_to_dicts(conn.execute(
            "SELECT i.id, i.created_ts, u.username FROM group_invites i "
            "JOIN users u ON u.id = i.user_id "
            "WHERE i.group_id=? AND i.status='pending' ORDER BY i.created_ts",
            (group_id,)))
    return {"group": out}


@router.patch("/groups/{group_id}")
def update_group(group_id: int, payload: dict = Body(...), user=Depends(require_user)):
    conn = get_db()
    _group(conn, group_id, user, manage=True)
    with conn:
        if "name" in payload:
            conn.execute("UPDATE groups SET name=? WHERE id=?",
                         (_clean_name(payload["name"]), group_id))
        if "description" in payload:
            conn.execute("UPDATE groups SET description=? WHERE id=?",
                         ((payload["description"] or "").strip() or None, group_id))
    return group_detail(group_id, user)


@router.delete("/groups/{group_id}")
def remove_group(group_id: int, user=Depends(require_user)):
    """Only the owner, and it takes the shares with it — raids shared nowhere
    else go back to being private."""
    conn = get_db()
    row = _group(conn, group_id, user)
    if row["owner_user_id"] != user["id"]:
        raise HTTPException(403, "only the group's owner can delete it")
    with conn:
        g.delete_group(conn, group_id)
    return {"deleted": group_id}


# ---- joining ----

@router.post("/groups/{group_id}/code/rotate")
def rotate_code(group_id: int, payload: dict = Body(default={}),
                user=Depends(require_user)):
    """New code, old one dead immediately. `enabled: false` turns code joining
    off entirely; `expires_in_days` bounds the new one."""
    conn = get_db()
    _group(conn, group_id, user, manage=True)
    enabled = payload.get("enabled", True)
    days = payload.get("expires_in_days")
    now = int(time.time())
    with conn:
        conn.execute(
            "UPDATE groups SET join_code=?, join_code_ts=?, join_code_expires_ts=? WHERE id=?",
            (g.new_join_code(conn) if enabled else None, now,
             now + int(days) * 86400 if enabled and days else None, group_id))
    return group_detail(group_id, user)


def _resolve_code(conn, code: str, request: Request, ident: str):
    """Look a join code up, counting failures on the identity and the address.
    A 6-digit space is small enough to walk, so this counter is the security —
    the code itself only has to be sayable out loud.

    The keys are deduped: an anonymous caller's identity IS their address, and
    counting one failure twice would silently halve their budget."""
    keys = list(dict.fromkeys(k for k in (ident, _addr(request)) if k))
    for key in keys:
        wait = ratelimit.retry_after("groupjoin", key)
        if wait:
            raise HTTPException(429, "too many tries — wait a few minutes",
                                headers={"Retry-After": str(wait)})
    code = (code or "").strip()
    row = g.group_by_code(conn, code) if code.isdigit() and len(code) == 6 else None
    for key in keys:
        (ratelimit.fail if row is None else ratelimit.clear)("groupjoin", key)
    if row is None:
        raise HTTPException(404, "that invitation is no longer valid")
    return row


def _addr(request: Request) -> str:
    """The real caller, not the proxy in front of it — one shared bucket for
    every anonymous visitor would let one script lock everybody out of the
    preview route. See `siteconfig.client_ip`."""
    return siteconfig.client_ip(request)


@router.get("/groups/preview/{code}")
def preview_code(code: str, request: Request, user=Depends(optional_user)):
    """What an invite link resolves to, BEFORE the visitor has an account —
    the landing page has to be able to say which group is inviting them.

    Deliberately thin: a name and a headcount, never the roster or anything
    shared with the group. Holding the code is the whole authorization, so it
    is failure-counted exactly like joining."""
    conn = get_db()
    row = _resolve_code(conn, code, request, str(user["id"]) if user else _addr(request))
    return {"group": {
        "id": row["id"], "name": row["name"], "description": row["description"],
        "member_count": conn.execute(
            "SELECT COUNT(*) FROM group_members WHERE group_id=?", (row["id"],)).fetchone()[0],
        "member": user is not None and g.is_member(conn, row["id"], user["id"]),
    }}


@router.post("/groups/join")
def join_by_code(body: JoinBody, request: Request, user=Depends(require_user)):
    conn = get_db()
    row = _resolve_code(conn, body.code, request, str(user["id"]))
    with conn:
        g.add_member(conn, row["id"], user["id"])
        conn.execute("UPDATE group_invites SET status='accepted' "
                     "WHERE group_id=? AND user_id=? AND status='pending'",
                     (row["id"], user["id"]))
    return group_detail(row["id"], user)


@router.post("/groups/{group_id}/invites")
def invite(group_id: int, body: InviteCreate, user=Depends(require_user)):
    conn = get_db()
    _group(conn, group_id, user, manage=True)
    target = conn.execute("SELECT id, username FROM users WHERE username=?",
                          ((body.username or "").strip().lower(),)).fetchone()
    if target is None:
        raise HTTPException(404, "no account with that username")
    if g.is_member(conn, group_id, target["id"]):
        raise HTTPException(409, f"{target['username']} is already in this group")
    with conn:
        conn.execute(
            "INSERT INTO group_invites (group_id, user_id, invited_by, status, created_ts) "
            "VALUES (?,?,?, 'pending', ?) "
            "ON CONFLICT(group_id, user_id) DO UPDATE SET status='pending', "
            "invited_by=excluded.invited_by, created_ts=excluded.created_ts",
            (group_id, target["id"], user["id"], int(time.time())))
    return {"invited": target["username"]}


@router.post("/invites/{invite_id}/{decision}")
def answer_invite(invite_id: int, decision: str, user=Depends(require_user)):
    if decision not in ("accept", "decline"):
        raise HTTPException(422, "decision is accept or decline")
    conn = get_db()
    inv = conn.execute(
        "SELECT * FROM group_invites WHERE id=? AND user_id=? AND status='pending'",
        (invite_id, user["id"])).fetchone()
    if inv is None:
        raise HTTPException(404, "no such invite")
    with conn:
        conn.execute("UPDATE group_invites SET status=? WHERE id=?",
                     ("accepted" if decision == "accept" else "declined", invite_id))
        if decision == "accept":
            g.add_member(conn, inv["group_id"], user["id"])
    return {"group_id": inv["group_id"], "status": decision}


# ---- leaving ----

@router.post("/groups/{group_id}/leave")
def leave_group(group_id: int, user=Depends(require_user)):
    """Leaving revokes what you could see through this group on the next
    request — nothing was ever copied to you."""
    conn = get_db()
    row = _group(conn, group_id, user)
    if row["owner_user_id"] == user["id"]:
        raise HTTPException(409, "hand the group to someone else first, or delete it")
    with conn:
        conn.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?",
                     (group_id, user["id"]))
        # my auto-shares into this group go too, or rejoining would silently
        # reopen everything I had pointed at it
        conn.execute(
            "DELETE FROM character_shares WHERE group_id=? AND character_id IN "
            "(SELECT id FROM characters WHERE user_id=?)", (group_id, user["id"]))
    return {"left": group_id}


@router.delete("/groups/{group_id}/members/{member_id}")
def remove_member(group_id: int, member_id: int, user=Depends(require_user)):
    conn = get_db()
    row = _group(conn, group_id, user, manage=True)
    if member_id == row["owner_user_id"]:
        raise HTTPException(409, "the owner can't be removed")
    if member_id != user["id"] and g.member_role(conn, group_id, member_id) == "admin" \
            and row["owner_user_id"] != user["id"]:
        raise HTTPException(403, "only the owner can remove an admin")
    with conn:
        conn.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?",
                     (group_id, member_id))
        conn.execute(
            "DELETE FROM character_shares WHERE group_id=? AND character_id IN "
            "(SELECT id FROM characters WHERE user_id=?)", (group_id, member_id))
    return {"removed": member_id}


@router.post("/groups/{group_id}/members/{member_id}/role")
def set_role(group_id: int, member_id: int, payload: dict = Body(...),
             user=Depends(require_user)):
    """Owner only. `owner` hands the group over — the old owner stays as admin,
    which is what makes leaving possible."""
    conn = get_db()
    row = _group(conn, group_id, user)
    if row["owner_user_id"] != user["id"]:
        raise HTTPException(403, "only the group's owner can change roles")
    role = payload.get("role")
    if role not in ("member", "admin", "owner"):
        raise HTTPException(422, "role is member, admin or owner")
    if not g.is_member(conn, group_id, member_id):
        raise HTTPException(404, "not a member of this group")
    with conn:
        if role == "owner":
            conn.execute("UPDATE groups SET owner_user_id=? WHERE id=?", (member_id, group_id))
            conn.execute("UPDATE group_members SET role='admin' WHERE group_id=? AND user_id=?",
                         (group_id, user["id"]))
        conn.execute("UPDATE group_members SET role=? WHERE group_id=? AND user_id=?",
                     (role, group_id, member_id))
    return group_detail(group_id, user)
