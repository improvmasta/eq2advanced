"""Request auth dependencies and the two access questions.

**Ownership** — may this user CHANGE this thing? Only ever the owner. Deleting,
merging, splitting, reparsing, minting tokens, running the coach: owner or 404.

**Visibility** — may this caller SEE this raid? The owner, anyone in a group it
is shared with, and — for a published raid — *anyone at all, signed in or not*
(`groups.VISIBLE_RUN_IDS`). Read routes therefore take `optional_user`, and a
caller of None resolves to exactly the published set: every other branch of the
predicate compares against a NULL user id and matches nothing.

Admin is deliberately absent from both. `role='admin'` is an OPERATIONAL role —
it gates the admin console (users, storage, quotas, job health) and nothing
else. An admin cannot read a stranger's parses; support means asking them to
share the raid. `require_admin` therefore guards routes, never rows.

Foreign ids 404 rather than 403, so existence doesn't leak either way.
"""

from fastapi import Depends, HTTPException, Request

from auth import COOKIE, session_user
from db import get_db
from groups import VISIBLE_UNHIDDEN_RUN_IDS


def current_user(request: Request):
    return session_user(get_db(), request.cookies.get(COOKIE))


def require_user(request: Request):
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "not signed in")
    return user


def optional_user(request: Request):
    """For the read routes that also serve published raids to the public. Returns
    the user row or None; None sees published raids and nothing else."""
    return current_user(request)


def _uid(user) -> int | None:
    return user["id"] if user is not None else None


def require_admin(user=Depends(require_user)):
    """Operational routes only — see the module docstring."""
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    return user


def require_curator(user=Depends(require_user)):
    """The Abilities console — GAME knowledge, not site state.

    A separate role because the two jobs are unrelated: deciding that `Fae
    Fires` is a fury's spell proc needs somebody who knows EQ2, and running
    the site needs somebody with the disk. Handing out `admin` to get the
    first would hand over user management, storage limits and the audit log
    with it, which is a much bigger key than the task deserves.

    A curator sees ability names, site-wide counts and class names — the
    Abilities payload carries no player name, no entity and no row from
    anybody's parse — so this widens nothing about who can read a raid. Admin
    implies curator; the reverse is deliberately false."""
    if user["role"] not in ("admin", "curator"):
        raise HTTPException(403, "curator only")
    return user


def owned_character(conn, user, character_id: int):
    """Character row if the user owns it, else 404."""
    char = conn.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone()
    if char is None or char["user_id"] != user["id"]:
        raise HTTPException(404, "no such character")
    return char


def owned_session(conn, user, session_id: int):
    """Session row if the user owns it, else 404. Uploads are private even when
    a raid out of them is shared — a shared night is derived stats, not the log
    file and not the other fights that happen to sit in the same upload."""
    sess = conn.execute(
        "SELECT s.*, c.name AS character_name, c.user_id AS owner_id FROM sessions s "
        "JOIN characters c ON c.id = s.character_id WHERE s.id=?", (session_id,)).fetchone()
    if sess is None or sess["owner_id"] != user["id"]:
        raise HTTPException(404, "no such session")
    return sess


def visible_zone_run(conn, user, run_id: int):
    """Zone-run row (with character and owner) if the user may SEE it, else 404.
    Says nothing about whether they may change it — use `owned_zone_run`."""
    run = conn.execute(
        "SELECT z.*, c.name AS character_name, c.user_id AS owner_id, u.username AS owner_username "
        "FROM zone_runs z JOIN characters c ON c.id = z.character_id "
        "JOIN users u ON u.id = c.user_id WHERE z.id=?", (run_id,)).fetchone()
    if run is None or run_id not in _visible_of(conn, user, [run_id]):
        raise HTTPException(404, "no such zone run")
    return run


def owned_zone_run(conn, user, run_id: int):
    """Same row, but only for the owner — every edit goes through here. A shared
    raid is read-only to everyone it was shared with, including admins."""
    run = visible_zone_run(conn, user, run_id)
    if run["owner_id"] != _uid(user):
        raise HTTPException(403, "this raid belongs to someone else")
    return run


def visible_encounters(conn, user, encs) -> dict[int, dict]:
    """Authorize a set of encounter rows, then hand back {session_id: session
    row} for the callers that need session metadata (status, pruned).

    An encounter is visible when its session is yours OR its zone run is shared
    with you. Session-level authorization would be wrong here: a viewer cleared
    for one shared run would be cleared for every other fight in the same
    uploaded file, which is exactly the leak sharing must not open.

    A HIDDEN fight is the owner's alone, and this is the only place that has to
    know it: every ?ids= endpoint comes through here, so a viewer who worked out
    an id from a neighbouring one is refused the same way a stranger is. The
    payloads simply never name them (`zoneruns_api._run_encounters`), but "not
    in the list we sent" is not an access rule."""
    if not encs:
        raise HTTPException(422, "ids is empty")
    session_ids = sorted({e["session_id"] for e in encs})
    ph = ",".join("?" * len(session_ids))
    sess_of = {r["id"]: r for r in conn.execute(
        f"SELECT s.*, c.name AS character_name, c.user_id AS owner_id FROM sessions s "
        f"JOIN characters c ON c.id = s.character_id WHERE s.id IN ({ph})", session_ids)}
    if len(sess_of) != len(session_ids):
        raise HTTPException(404, "no such encounter")

    run_ids = sorted({e["zone_run_id"] for e in encs if e["zone_run_id"]})
    shared = _visible_of(conn, user, run_ids)
    uid = _uid(user)
    for e in encs:
        if uid is not None and sess_of[e["session_id"]]["owner_id"] == uid:
            continue
        if e["zone_run_id"] in shared and e["hidden_ts"] is None:
            continue
        raise HTTPException(404, "no such encounter")
    return sess_of


def _visible_of(conn, user, run_ids: list[int]) -> set[int]:
    """The visible subset of `run_ids`. `user=None` (signed out) leaves every
    ownership and membership clause comparing against NULL, so only the
    published runs come back."""
    if not run_ids:
        return set()
    params = {f"r{i}": v for i, v in enumerate(run_ids)}
    named = ",".join(f":r{i}" for i in range(len(run_ids)))
    return {r["id"] for r in conn.execute(
        f"SELECT z.id FROM zone_runs z WHERE z.id IN ({named}) "
        f"AND z.id IN ({VISIBLE_UNHIDDEN_RUN_IDS})", {**params, "uid": _uid(user)})}
