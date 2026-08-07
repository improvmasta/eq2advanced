"""Bug reports and suggestions, filed from anywhere on the site.

One text box and a kind. The page the reporter was on rides along because the
first question about "the numbers look wrong" is always "on which raid", and
asking them afterwards costs a round trip they usually don't come back for.

Signing in is the whole abuse story: `ratelimit.py` counts authentication
FAILURES, so it is the wrong instrument here, and an account-gated form on a
site where accounts are hand-made needs nothing more. The body is capped so a
paste of an entire log can't land in the table.

Nothing here reads a parse — a feedback row is a user id, a string, and a path.
"""

import time

from fastapi import APIRouter, Body, Depends, HTTPException

import groups as g
from db import get_db, rows_to_dicts
from security import require_admin, require_user

router = APIRouter(tags=["feedback"])

KINDS = ("bug", "suggestion")
STATUSES = ("open", "planned", "closed")
BODY_MAX = 4000
PAGE_MAX = 200


@router.post("/feedback")
def submit(payload: dict = Body(...), user=Depends(require_user)):
    kind = str(payload.get("kind") or "").strip()
    if kind not in KINDS:
        raise HTTPException(422, f"kind is one of {KINDS}")
    body = str(payload.get("body") or "").strip()
    if not body:
        raise HTTPException(422, "say what happened")
    if len(body) > BODY_MAX:
        raise HTTPException(422, f"keep it under {BODY_MAX} characters")
    page = (str(payload.get("page") or "").strip() or None)
    if page:
        page = page[:PAGE_MAX]
    conn = get_db()
    with conn:
        cur = conn.execute(
            "INSERT INTO feedback (user_id, kind, body, page, status, created_ts) "
            "VALUES (?,?,?,?,'open',?)",
            (user["id"], kind, body, page, int(time.time())))
    return {"id": cur.lastrowid, "ok": True}


@router.get("/admin/feedback")
def list_feedback(status: str = "", kind: str = "", limit: int = 100, offset: int = 0,
                  admin=Depends(require_admin)):
    if status and status not in STATUSES:
        raise HTTPException(422, f"status is one of {STATUSES}")
    if kind and kind not in KINDS:
        raise HTTPException(422, f"kind is one of {KINDS}")
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    where, params = [], []
    if status:
        where.append("f.status=?")
        params.append(status)
    if kind:
        where.append("f.kind=?")
        params.append(kind)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = get_db()
    items = rows_to_dicts(conn.execute(
        "SELECT f.id, f.kind, f.body, f.page, f.status, f.created_ts, f.updated_ts, "
        "u.username FROM feedback f LEFT JOIN users u ON u.id = f.user_id"
        # id breaks the tie, same reason as the audit log
        f"{clause} ORDER BY f.created_ts DESC, f.id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset)))
    total = conn.execute(f"SELECT COUNT(*) FROM feedback f{clause}", params).fetchone()[0]
    # unfiltered, so the tab badge doesn't change meaning when a filter is on
    open_count = conn.execute(
        "SELECT COUNT(*) FROM feedback WHERE status='open'").fetchone()[0]
    return {"items": items, "total": total, "open_count": open_count,
            "limit": limit, "offset": offset}


@router.patch("/admin/feedback/{feedback_id}")
def set_status(feedback_id: int, payload: dict = Body(...), admin=Depends(require_admin)):
    status = str(payload.get("status") or "").strip()
    if status not in STATUSES:
        raise HTTPException(422, f"status is one of {STATUSES}")
    conn = get_db()
    if conn.execute("SELECT 1 FROM feedback WHERE id=?", (feedback_id,)).fetchone() is None:
        raise HTTPException(404, "no such feedback")
    with conn:
        conn.execute("UPDATE feedback SET status=?, updated_ts=? WHERE id=?",
                     (status, int(time.time()), feedback_id))
        g.audit(conn, admin["id"], "feedback_status", f"feedback:{feedback_id}", status)
    return {"id": feedback_id, "status": status}


@router.delete("/admin/feedback/{feedback_id}")
def delete_feedback(feedback_id: int, admin=Depends(require_admin)):
    conn = get_db()
    if conn.execute("SELECT 1 FROM feedback WHERE id=?", (feedback_id,)).fetchone() is None:
        raise HTTPException(404, "no such feedback")
    with conn:
        conn.execute("DELETE FROM feedback WHERE id=?", (feedback_id,))
        g.audit(conn, admin["id"], "feedback_delete", f"feedback:{feedback_id}")
    return {"id": feedback_id, "deleted": True}
