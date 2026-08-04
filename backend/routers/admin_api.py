"""The admin console — running the site without reading it.

Everything here is a COUNT, a SUM, a status or a setting. There is deliberately
no route that returns an encounter, an actor row, an ability row or a log line,
and no flag anywhere that would let one appear: `security.py` keeps `role` out
of every visibility decision, so even a mistake in this file could not widen
what an admin can see. Support for a broken parse is "ask them to share the
raid", and the numbers below are enough to answer "is the site healthy" and
"who is using all the disk".

Admin mutations are written to `audit_log` and served back by `/admin/audit` —
a promise nobody can check is not worth much.
"""

import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException

import auth
import groups as g
from db import UPLOADS_DIR, get_db, get_int_setting, rows_to_dicts, set_setting
from security import require_admin

router = APIRouter(tags=["admin"])

SETTINGS_KEYS = ("upload_max_bytes", "storage_max_bytes", "registration_open")


@router.get("/admin/overview")
def overview(admin=Depends(require_admin)):
    conn = get_db()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("users", "characters", "sessions", "zone_runs", "encounters",
                        "groups", "public_runs")}
    stored = conn.execute(
        "SELECT COALESCE(SUM(raw_bytes),0) AS raw, COALESCE(SUM(src_bytes),0) AS src "
        "FROM sessions").fetchone()
    on_disk = sum(p.stat().st_size for p in Path(UPLOADS_DIR).glob("*.txt.gz")) \
        if Path(UPLOADS_DIR).exists() else 0
    jobs = rows_to_dicts(conn.execute(
        "SELECT id, character_id, status, error, created_ts FROM sessions "
        "WHERE status IN ('parsing','receiving','error') ORDER BY created_ts DESC LIMIT 50"))
    return {
        "counts": counts,
        "storage": {"raw_bytes": stored["raw"], "src_bytes": stored["src"],
                    "uploads_dir_bytes": on_disk},
        "jobs": jobs,
        "settings": {k: get_int_setting(conn, k, 1 if k == "registration_open" else 0)
                     for k in SETTINGS_KEYS},
        "memo": __import__("memo").stats(),
    }


@router.get("/admin/users")
def list_users(admin=Depends(require_admin)):
    """One row per account: who they are, how much they're storing, whether
    anything is broken. No route from here to what any of it contains."""
    conn = get_db()
    rows = rows_to_dicts(conn.execute(
        "SELECT u.id, u.username, u.role, u.created_ts, u.last_login_ts, u.disabled_ts, "
        "u.upload_max_bytes, u.storage_max_bytes, (u.sq_id IS NOT NULL) AS has_question, "
        "(SELECT COUNT(*) FROM characters c WHERE c.user_id = u.id) AS character_count, "
        "(SELECT COUNT(*) FROM sessions s JOIN characters c ON c.id = s.character_id "
        "  WHERE c.user_id = u.id) AS session_count, "
        "(SELECT COUNT(*) FROM sessions s JOIN characters c ON c.id = s.character_id "
        "  WHERE c.user_id = u.id AND s.status='error') AS error_count, "
        "(SELECT COALESCE(SUM(s.raw_bytes),0) FROM sessions s "
        "  JOIN characters c ON c.id = s.character_id WHERE c.user_id = u.id) AS stored_bytes, "
        "(SELECT COUNT(*) FROM zone_runs z JOIN characters c ON c.id = z.character_id "
        "  WHERE c.user_id = u.id) AS run_count "
        "FROM users u ORDER BY u.username"))
    return {"users": rows}


@router.post("/admin/users/{user_id}/disabled")
def set_disabled(user_id: int, payload: dict = Body(...), admin=Depends(require_admin)):
    """Disabling signs the account out everywhere and refuses new logins. It
    does not touch their data — an admin cannot read it and cannot delete it."""
    conn = get_db()
    row = conn.execute("SELECT id, username, role FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such user")
    if row["id"] == admin["id"]:
        raise HTTPException(409, "you can't disable your own account")
    disabled = bool(payload.get("disabled"))
    with conn:
        conn.execute("UPDATE users SET disabled_ts=? WHERE id=?",
                     (int(time.time()) if disabled else None, user_id))
        if disabled:
            auth.clear_sessions(conn, user_id)
        g.audit(conn, admin["id"], "disable" if disabled else "enable",
                f"user:{row['username']}")
    return {"user_id": user_id, "disabled": disabled}


@router.post("/admin/users/{user_id}/password")
def reset_password(user_id: int, payload: dict = Body(...), admin=Depends(require_admin)):
    """The fallback for an account with no security question. The new password
    is shown once, here, and the user is signed out everywhere."""
    conn = get_db()
    row = conn.execute("SELECT id, username FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such user")
    new = str(payload.get("password") or "")
    if len(new) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    with conn:
        auth.set_password(conn, user_id, new)
        auth.clear_sessions(conn, user_id)
        g.audit(conn, admin["id"], "reset_password", f"user:{row['username']}")
    return {"user_id": user_id, "ok": True}


@router.post("/admin/users/{user_id}/limits")
def set_limits(user_id: int, payload: dict = Body(...), admin=Depends(require_admin)):
    """Per-user overrides. NULL means "use the site default", 0 means unlimited."""
    conn = get_db()
    if conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
        raise HTTPException(404, "no such user")
    fields = {}
    for key in ("upload_max_bytes", "storage_max_bytes"):
        if key in payload:
            v = payload[key]
            fields[key] = None if v in (None, "") else max(0, int(v))
    if not fields:
        raise HTTPException(422, "nothing to set")
    with conn:
        for key, value in fields.items():
            conn.execute(f"UPDATE users SET {key}=? WHERE id=?", (value, user_id))
        g.audit(conn, admin["id"], "set_limits", f"user:{user_id}", str(fields))
    return {"user_id": user_id, **fields}


@router.put("/admin/settings")
def update_settings(payload: dict = Body(...), admin=Depends(require_admin)):
    """Site-wide knobs. `upload_max_bytes` and `storage_max_bytes` are 0 =
    unlimited, which is how the site ships."""
    conn = get_db()
    unknown = set(payload) - set(SETTINGS_KEYS)
    if unknown:
        raise HTTPException(422, f"unknown setting: {sorted(unknown)[0]}")
    with conn:
        for key, value in payload.items():
            set_setting(conn, key, max(0, int(value)))
        g.audit(conn, admin["id"], "settings", None, str(payload))
    return {k: get_int_setting(conn, k, 1 if k == "registration_open" else 0)
            for k in SETTINGS_KEYS}


@router.get("/admin/audit")
def audit_log(limit: int = 200, admin=Depends(require_admin)):
    conn = get_db()
    return {"entries": rows_to_dicts(conn.execute(
        "SELECT a.*, u.username AS actor FROM audit_log a "
        "LEFT JOIN users u ON u.id = a.actor_user_id "
        # id breaks the tie: several actions land in the same second and the
        # order they happened in is the whole point of a log
        "ORDER BY a.ts DESC, a.id DESC LIMIT ?", (max(1, min(limit, 1000)),)))}


@router.get("/admin/public-runs")
def public_runs(admin=Depends(require_admin)):
    """Published raids — visible to the whole internet, so the list of them is
    worth having in one place. Zone and date only; the raid itself is only
    readable here if it's the admin's own."""
    conn = get_db()
    return {"runs": rows_to_dicts(conn.execute(
        "SELECT p.zone_run_id, p.created_ts, z.zone, z.started_ts, z.raider_count, "
        "u.username AS publisher, (c.user_id = ?) AS mine "
        "FROM public_runs p JOIN zone_runs z ON z.id = p.zone_run_id "
        "JOIN characters c ON c.id = z.character_id "
        "LEFT JOIN users u ON u.id = p.published_by "
        "ORDER BY p.created_ts DESC", (admin["id"],)))}
