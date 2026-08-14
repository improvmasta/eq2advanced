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
def list_feedback(status: str = "", kind: str = "", q: str = "", assignee: str = "",
                  limit: int = 100, offset: int = 0,
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
    if q:
        where.append("(f.body LIKE ? OR f.page LIKE ? OR f.admin_note LIKE ?)")
        params.extend([f"%{q}%"] * 3)
    if assignee == "unassigned":
        where.append("f.assignee_user_id IS NULL")
    elif assignee:
        where.append("au.username=?"); params.append(assignee)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = get_db()
    items = rows_to_dicts(conn.execute(
        "SELECT f.id, f.kind, f.body, f.page, f.status, f.created_ts, f.updated_ts, "
        "f.admin_note, u.username, au.username assignee FROM feedback f "
        "LEFT JOIN users u ON u.id = f.user_id "
        "LEFT JOIN users au ON au.id = f.assignee_user_id"
        # id breaks the tie, same reason as the audit log
        f"{clause} ORDER BY f.created_ts DESC, f.id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset)))
    total = conn.execute("SELECT COUNT(*) FROM feedback f LEFT JOIN users au ON "
                         f"au.id=f.assignee_user_id{clause}", params).fetchone()[0]
    # unfiltered, so the tab badge doesn't change meaning when a filter is on
    open_count = conn.execute(
        "SELECT COUNT(*) FROM feedback WHERE status='open'").fetchone()[0]
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status,COUNT(*) n FROM feedback GROUP BY status")}
    admins = [r[0] for r in conn.execute(
        "SELECT username FROM users WHERE role='admin' AND disabled_ts IS NULL ORDER BY username")]
    return {"items": items, "total": total, "open_count": open_count,
            "counts": {s: counts.get(s, 0) for s in STATUSES}, "admins": admins,
            "limit": limit, "offset": offset}


@router.patch("/admin/feedback/{feedback_id}")
def set_status(feedback_id: int, payload: dict = Body(...), admin=Depends(require_admin)):
    status = str(payload.get("status") or "").strip()
    if status and status not in STATUSES:
        raise HTTPException(422, f"status is one of {STATUSES}")
    conn = get_db()
    if conn.execute("SELECT 1 FROM feedback WHERE id=?", (feedback_id,)).fetchone() is None:
        raise HTTPException(404, "no such feedback")
    with conn:
        fields, params = [], []
        if status:
            fields.append("status=?"); params.append(status)
        if "admin_note" in payload:
            fields.append("admin_note=?"); params.append(str(payload.get("admin_note") or "").strip() or None)
        if "assignee" in payload:
            name = str(payload.get("assignee") or "").strip()
            uid = None
            if name:
                row = conn.execute("SELECT id FROM users WHERE username=? AND role='admin'", (name,)).fetchone()
                if row is None:
                    raise HTTPException(422, "assignee must be an admin")
                uid = row[0]
            fields.append("assignee_user_id=?"); params.append(uid)
        if not fields:
            raise HTTPException(422, "nothing to update")
        fields.append("updated_ts=?"); params.append(int(time.time()))
        conn.execute(f"UPDATE feedback SET {','.join(fields)} WHERE id=?", (*params, feedback_id))
        g.audit(conn, admin["id"], "feedback_status" if status else "feedback_update",
                f"feedback:{feedback_id}", status or ", ".join(fields[:-1]))
    return {"id": feedback_id, "status": status or None}


@router.delete("/admin/feedback/{feedback_id}")
def delete_feedback(feedback_id: int, admin=Depends(require_admin)):
    conn = get_db()
    if conn.execute("SELECT 1 FROM feedback WHERE id=?", (feedback_id,)).fetchone() is None:
        raise HTTPException(404, "no such feedback")
    with conn:
        conn.execute("DELETE FROM feedback WHERE id=?", (feedback_id,))
        g.audit(conn, admin["id"], "feedback_delete", f"feedback:{feedback_id}")
    return {"id": feedback_id, "deleted": True}
