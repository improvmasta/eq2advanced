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

import json
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException

import auth
import classtree
import groups as g
import ratelimit
import visitors
from db import UPLOADS_DIR, get_db, get_int_setting, rows_to_dicts, set_setting
from security import require_admin, require_curator

router = APIRouter(tags=["admin"])

SETTINGS_KEYS = ("upload_max_bytes", "storage_max_bytes", "registration_open")

# A parse normally takes seconds. Past this it is a thread that died with its
# process — see the startup sweep in `main.py`, which repairs exactly this.
STUCK_PARSE_S = 600


@router.get("/admin/overview")
def overview(admin=Depends(require_admin)):
    conn = get_db()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("users", "characters", "sessions", "zone_runs", "encounters",
                        "groups", "public_runs")}
    # a deleted group is still a row (it can be restored), so it is counted
    # separately rather than inflating the live number
    counts["groups_deleted"] = conn.execute(
        "SELECT COUNT(*) FROM groups WHERE deleted_ts IS NOT NULL").fetchone()[0]
    counts["groups"] -= counts["groups_deleted"]
    stored = conn.execute(
        "SELECT COALESCE(SUM(raw_bytes),0) AS raw, COALESCE(SUM(src_bytes),0) AS src "
        "FROM sessions").fetchone()
    on_disk = sum(p.stat().st_size for p in Path(UPLOADS_DIR).glob("*.txt.gz")) \
        if Path(UPLOADS_DIR).exists() else 0
    # Only what somebody has to DO something about. A `receiving` session is a
    # plugin streaming right now — the healthiest state there is — and listing
    # every non-final session made a raid night look like 24 failures. The
    # reaper (`pipeline/live.py`) closes a stream that goes quiet, so staleness
    # there is already somebody's job; what is left is a parse that errored and
    # a parse that has been running far too long to still be running.
    now = int(time.time())
    alerts = _incident_rows(conn)[:50]
    live = {"receiving": 0, "parsing": 0}
    for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM sessions "
            "WHERE status IN ('receiving','parsing') GROUP BY status"):
        live[row["status"]] = row["n"]
    # a stuck parse is already an alert; counting it as healthy work in flight
    # would be the old panel's mistake in the other direction
    live["parsing"] = max(0, live["parsing"] - sum(1 for a in alerts if a["kind"] == "stuck"))
    return {
        "counts": counts,
        "storage": {"raw_bytes": stored["raw"], "src_bytes": stored["src"],
                    "uploads_dir_bytes": on_disk},
        "alerts": alerts,
        "live": live,
        "settings": {k: get_int_setting(conn, k, 1 if k == "registration_open" else 0)
                     for k in SETTINGS_KEYS},
        "memo": __import__("memo").stats(),
    }


def _incident_row(conn, session_id: int):
    return conn.execute(
        "SELECT s.id, s.source, s.status, s.error, s.created_ts, s.last_ingest_ts, "
        "s.raw_deleted_ts, s.pruned, s.upload_sha256, c.name AS character, u.username "
        "FROM sessions s JOIN characters c ON c.id=s.character_id "
        "JOIN users u ON u.id=c.user_id WHERE s.id=?", (session_id,)).fetchone()


def _incident_rows(conn, include_acknowledged=False):
    now = int(time.time())
    sql = (
        "SELECT s.id, s.source, s.status, s.error, s.created_ts, "
        "COALESCE(s.last_ingest_ts,s.created_ts) last_seen_ts, c.name character, "
        "u.username, ia.note acknowledgement_note, ia.acknowledged_ts, "
        "au.username acknowledged_by FROM sessions s "
        "JOIN characters c ON c.id=s.character_id JOIN users u ON u.id=c.user_id "
        "LEFT JOIN incident_acknowledgements ia ON ia.session_id=s.id "
        "LEFT JOIN users au ON au.id=ia.actor_user_id "
        "WHERE (s.status='error' OR (s.status='parsing' AND "
        "COALESCE(s.last_ingest_ts,s.created_ts)<?))")
    if not include_acknowledged:
        sql += " AND ia.session_id IS NULL"
    sql += " ORDER BY s.created_ts DESC"
    rows = rows_to_dicts(conn.execute(sql, (now - STUCK_PARSE_S,)))
    for row in rows:
        row["kind"] = "error" if row["status"] == "error" else "stuck"
        row["severity"] = "high" if row["kind"] == "stuck" or row["error"] else "medium"
        row["age_s"] = max(0, now - row["last_seen_ts"])
        row["summary"] = (str(row["error"] or "Parse stopped making progress").splitlines()[-1])
        source = _incident_row(conn, row["id"])
        retryable = False
        if source and not source["pruned"] and not source["raw_deleted_ts"]:
            if source["source"] == "upload" and source["upload_sha256"]:
                retryable = (Path(UPLOADS_DIR) / f"{source['upload_sha256']}.txt.gz").exists()
            else:
                # A live session can own tens of thousands of chunk rows. The
                # previous list endpoint materialized and stat()ed every one,
                # making Dashboard take seconds. Recent chunks are the useful
                # retry signal; the detail/action path performs the exhaustive
                # source resolution only when an admin actually opens it.
                recent = conn.execute(
                    "SELECT path FROM raw_chunks WHERE session_id=? ORDER BY seq DESC LIMIT 20",
                    (row["id"],)).fetchall()
                retryable = any(Path(r["path"]).exists() for r in recent)
        row["retryable"] = retryable
        row["support_instruction"] = None if row["retryable"] else \
            "Ask the account owner to upload the original log again; no stored source remains."
    return rows


@router.get("/admin/incidents")
def list_incidents(state: str = "open", type: str = "", severity: str = "",
                   age: int = 0, admin=Depends(require_admin)):
    if state not in ("open", "acknowledged", "all"):
        raise HTTPException(422, "state is open, acknowledged or all")
    rows = _incident_rows(get_db(), include_acknowledged=state != "open")
    if state == "acknowledged":
        rows = [r for r in rows if r["acknowledged_ts"]]
    if type:
        rows = [r for r in rows if r["kind"] == type]
    if severity:
        rows = [r for r in rows if r["severity"] == severity]
    if age:
        rows = [r for r in rows if r["age_s"] >= age]
    return {"items": rows, "total": len(rows), "state": state}


@router.post("/admin/incidents/{session_id}/acknowledge")
def acknowledge_incident(session_id: int, payload: dict = Body(...),
                         admin=Depends(require_admin)):
    note = str(payload.get("note") or "").strip()
    if not note:
        raise HTTPException(422, "an acknowledgement note is required")
    conn = get_db()
    if _incident_row(conn, session_id) is None:
        raise HTTPException(404, "no such incident")
    now = int(time.time())
    with conn:
        conn.execute(
            "INSERT INTO incident_acknowledgements(session_id,note,actor_user_id,acknowledged_ts) "
            "VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET note=excluded.note, "
            "actor_user_id=excluded.actor_user_id, acknowledged_ts=excluded.acknowledged_ts",
            (session_id, note, admin["id"], now))
        g.audit(conn, admin["id"], "acknowledge_incident", f"session:{session_id}", note)
    return {"session_id": session_id, "acknowledged": True}


@router.get("/admin/incidents/{session_id}")
def incident_detail(session_id: int, admin=Depends(require_admin)):
    """Operational metadata and sanitized parser evidence; never log content."""
    conn = get_db()
    row = _incident_row(conn, session_id)
    if row is None:
        raise HTTPException(404, "no such incident")
    now = int(time.time())
    stuck = row["status"] == "parsing" and \
        (row["last_ingest_ts"] or row["created_ts"]) < now - STUCK_PARSE_S
    if row["status"] != "error" and not stuck:
        raise HTTPException(404, "that session is not an incident")
    from pipeline.ingest_writer import session_raw_paths
    retryable = not row["pruned"] and not row["raw_deleted_ts"] \
        and bool(session_raw_paths(conn, session_id))
    return {**dict(row), "kind": "stuck" if stuck else "error",
            "age_s": max(0, now - (row["last_ingest_ts"] or row["created_ts"])),
            "retryable": retryable,
            "support_instruction": None if retryable else
            "Ask the account owner to upload the original log again; no stored source remains."}


@router.post("/admin/incidents/{session_id}/retry")
def retry_incident(session_id: int, admin=Depends(require_admin)):
    """Claim and rerun a failed or abandoned parse from its stored source."""
    from pipeline.ingest_writer import parse_session, session_raw_paths

    conn = get_db()
    row = _incident_row(conn, session_id)
    if row is None:
        raise HTTPException(404, "no such incident")
    now = int(time.time())
    stuck = row["status"] == "parsing" and \
        (row["last_ingest_ts"] or row["created_ts"]) < now - STUCK_PARSE_S
    if row["status"] != "error" and not stuck:
        raise HTTPException(409, f"session is {row['status']}")
    if row["pruned"] or row["raw_deleted_ts"]:
        raise HTTPException(409, "no stored source remains; ask the account owner to upload the original log again")
    paths = session_raw_paths(conn, session_id)
    if not paths:
        raise HTTPException(409, "no stored source remains; ask the account owner to upload the original log again")
    # `queued` is a short-lived claim. It makes two concurrent clicks unable to
    # launch two destructive rebuilds; parse_session immediately changes it to
    # `parsing` in its own connection.
    expected = row["status"]
    with conn:
        claimed = conn.execute(
            "UPDATE sessions SET status='queued', error=NULL WHERE id=? AND status=?",
            (session_id, expected)).rowcount
        if not claimed:
            raise HTTPException(409, "that incident is already being retried")
        g.audit(conn, admin["id"], "retry_parse" if expected == "error" else "restart_parse",
                f"session:{session_id}")
    threading.Thread(target=parse_session, args=(session_id, paths), daemon=True).start()
    return {"session_id": session_id, "status": "parsing"}


@router.get("/admin/visitors")
def visitor_timeline(days: int = 30, admin=Depends(require_admin)):
    """How many people came, by day (`visitors.py`).

    Still a COUNT and nothing else, which is what keeps it inside this file's
    rule: there is no route here that turns a visit into a person, because the
    table it reads threw that away the day after it was written.

    WHERE and WHEN ride along in the same answer (v51). They come from
    `visit_paths`, which has no visitor column at all, so they cannot be
    crossed with the timeline above to single anybody out — "the Planner was
    busy at 9pm" and "41 people came" are two counts of the same evening and
    not two halves of a profile. One request because the page shows all three
    together and a second round trip would buy nothing."""
    conn = get_db()
    return {**visitors.timeline(conn, days),
            "destinations": visitors.destinations(conn, days),
            "arrivals": visitors.arrivals(conn, days)}


SORT_COLS = {"username": "u.username", "created_ts": "u.created_ts",
             "last_login_ts": "u.last_login_ts", "stored_bytes": "stored_bytes",
             "character_count": "character_count", "session_count": "session_count",
             "error_count": "error_count", "run_count": "run_count"}


@router.get("/admin/users")
def list_users(q: str = "", sort: str = "stored_bytes", dir: str = "desc",
               limit: int = 50, offset: int = 0, admin=Depends(require_admin)):
    """One row per account: who they are, how much they're storing, whether
    anything is broken. No route from here to what any of it contains.

    Searched, sorted and paged on the SERVER. The counts are grouped joins
    rather than the correlated subqueries this used to run — those were five
    scans per user row, so the page got slower with every account.

    `q` is a substring match, and `%`/`_` in it act as LIKE wildcards. Nobody
    is being kept out of anything by this box, so that is a feature at worst."""
    if sort not in SORT_COLS:
        raise HTTPException(422, f"sort is one of {sorted(SORT_COLS)}")
    if dir not in ("asc", "desc"):
        raise HTTPException(422, "dir is asc or desc")
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    conn = get_db()
    rows = rows_to_dicts(conn.execute(
        "SELECT u.id, u.username, u.role, u.created_ts, u.last_login_ts, u.disabled_ts, "
        "u.upload_max_bytes, u.storage_max_bytes, (u.sq_id IS NOT NULL) AS has_question, "
        "COALESCE(cc.n, 0) AS character_count, "
        "COALESCE(ss.n, 0) AS session_count, "
        "COALESCE(ss.errs, 0) AS error_count, "
        "COALESCE(ss.bytes, 0) AS stored_bytes, "
        "COALESCE(zr.n, 0) AS run_count "
        "FROM users u "
        "LEFT JOIN (SELECT user_id, COUNT(*) AS n FROM characters GROUP BY user_id) cc "
        "  ON cc.user_id = u.id "
        "LEFT JOIN (SELECT c.user_id, COUNT(*) AS n, "
        "                  SUM(s.status='error') AS errs, "
        "                  COALESCE(SUM(s.raw_bytes),0) AS bytes "
        "             FROM sessions s JOIN characters c ON c.id = s.character_id "
        "            GROUP BY c.user_id) ss ON ss.user_id = u.id "
        "LEFT JOIN (SELECT c.user_id, COUNT(*) AS n "
        "             FROM zone_runs z JOIN characters c ON c.id = z.character_id "
        "            GROUP BY c.user_id) zr ON zr.user_id = u.id "
        "WHERE (? = '' OR u.username LIKE '%' || ? || '%') "
        f"ORDER BY {SORT_COLS[sort]} {dir.upper()}, u.username "
        "LIMIT ? OFFSET ?", (q, q, limit, offset)))
    total = conn.execute(
        "SELECT COUNT(*) FROM users u WHERE (? = '' OR u.username LIKE '%' || ? || '%')",
        (q, q)).fetchone()[0]
    defaults = {k: get_int_setting(conn, k, 0)
                for k in ("upload_max_bytes", "storage_max_bytes")}
    for row in rows:
        for key, default in defaults.items():
            row[f"effective_{key}"] = default if row[key] is None else row[key]
            row[f"{key}_source"] = "site default" if row[key] is None else "account override"
    return {"users": rows, "total": total, "limit": limit, "offset": offset}


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


@router.post("/admin/users/{user_id}/username")
def rename_user(user_id: int, payload: dict = Body(...), admin=Depends(require_admin)):
    """Rename an account. Nothing else stores the username — characters, raids,
    groups and shares all point at the user id — so this is a relabel, not a
    move, and the account stays signed in.

    Same rules as sign-up (`auth.USERNAME_RE`, lower case), because login,
    invites and password reset all look an account up by exactly that string."""
    conn = get_db()
    row = conn.execute("SELECT id, username FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such user")
    name = str(payload.get("username") or "").strip().lower()
    if not auth.USERNAME_RE.match(name):
        raise HTTPException(422, "username is 3-20 characters: letters, numbers, underscore")
    if name in auth.RESERVED_USERNAMES:
        raise HTTPException(409, "that username is reserved")
    if name != row["username"] and conn.execute(
            "SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
        raise HTTPException(409, "that username is taken")
    with conn:
        conn.execute("UPDATE users SET username=? WHERE id=?", (name, user_id))
        g.audit(conn, admin["id"], "rename_user", f"user:{row['username']}", name)
    return {"user_id": user_id, "username": name}


ROLES = ("user", "curator", "admin")


@router.post("/admin/users/{user_id}/role")
def set_role(user_id: int, payload: dict = Body(...), admin=Depends(require_admin)):
    """Promote or demote. `curator` opens the Abilities console and nothing
    else — it is EQ2 knowledge, not site access, and the payload behind that
    page carries no player name, entity or parse row (`security.require_curator`).
    `admin` implies curator.

    You cannot change your own role: the site would otherwise be one misclick
    away from having no admin at all, and there is no route back in."""
    conn = get_db()
    row = conn.execute("SELECT id, username, role FROM users WHERE id=?",
                       (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such user")
    if row["id"] == admin["id"]:
        raise HTTPException(409, "you can't change your own role")
    role = str(payload.get("role") or "").strip()
    if role not in ROLES:
        raise HTTPException(422, f"role is one of {ROLES}")
    with conn:
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
        g.audit(conn, admin["id"], "set_role", f"user:{row['username']}",
                f"{row['role']} -> {role}")
    return {"user_id": user_id, "role": role}


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


def _audit_label(row):
    action = row["action"].replace("_", " ")
    who = row.get("actor") or "System"
    target = (row.get("target") or "").replace(":", " ")
    detail = row.get("detail") or ""
    return f"{who} {action}{' ' + target if target else ''}{' — ' + detail if detail else ''}"


@router.get("/admin/audit")
def audit_log(limit: int = 200, offset: int = 0, q: str = "", actor: str = "",
              family: str = "", date_from: int = 0, date_to: int = 0,
              admin=Depends(require_admin)):
    conn = get_db()
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    where, params = [], []
    if q:
        where.append("(a.action LIKE ? OR a.target LIKE ? OR a.detail LIKE ? OR u.username LIKE ?)")
        params += [f"%{q}%"] * 4
    if actor:
        where.append("u.username=?"); params.append(actor)
    if family:
        where.append("a.action LIKE ?"); params.append(f"{family}%")
    if date_from:
        where.append("a.ts>=?"); params.append(date_from)
    if date_to:
        where.append("a.ts<=?"); params.append(date_to)
    clause = " WHERE " + " AND ".join(where) if where else ""
    entries = rows_to_dicts(conn.execute(
        "SELECT a.*, u.username AS actor FROM audit_log a "
        "LEFT JOIN users u ON u.id = a.actor_user_id "
        # id breaks the tie: several actions land in the same second and the
        # order they happened in is the whole point of a log
        f"{clause} ORDER BY a.ts DESC, a.id DESC LIMIT ? OFFSET ?", (*params, limit, offset)))
    total = conn.execute(
        "SELECT COUNT(*) FROM audit_log a LEFT JOIN users u ON u.id=a.actor_user_id" + clause,
        params).fetchone()[0]
    for entry in entries:
        entry["label"] = _audit_label(entry)
    return {"entries": entries, "total": total, "limit": limit, "offset": offset}


@router.get("/admin/dashboard")
def dashboard(admin=Depends(require_admin)):
    """Small decision-oriented summaries; workspaces fetch their own detail."""
    conn = get_db()
    now, since = int(time.time()), int(time.time()) - 30 * 86400
    visits = visitors.timeline(conn, 30)
    usage = {
        # Both, because they answer different questions and the difference
        # between them IS the finding: `visitor_days` is everything the
        # user-agent filter let through, `browser_days` is what ran the app
        # (v51, `visitors.py`). On this site the first has been mostly
        # crawlers, so a dashboard that showed only it was reading as growth.
        "visitor_days": visits["totals"]["visitor_days"],
        "browser_days": visits["totals"]["browser_days"],
        "signed_in_visitor_days": visits["totals"]["visitor_days"] - visits["totals"]["anon_days"],
        "uploads": conn.execute("SELECT COUNT(*) FROM sessions WHERE created_ts>=?", (since,)).fetchone()[0],
        "completed_raids": conn.execute("SELECT COUNT(*) FROM zone_runs WHERE started_ts>=?", (since,)).fetchone()[0],
        "active_accounts": conn.execute(
            "SELECT COUNT(*) FROM users WHERE last_login_ts>=?", (since,)).fetchone()[0],
        "storage_bytes": conn.execute("SELECT COALESCE(SUM(raw_bytes),0) FROM sessions").fetchone()[0],
        "storage_growth_bytes": conn.execute(
            "SELECT COALESCE(SUM(raw_bytes),0) FROM sessions WHERE created_ts>=?", (since,)).fetchone()[0],
    }
    failures = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE status='error' AND created_ts>=?", (now - 86400,)).fetchone()[0]
    oldest = conn.execute(
        "SELECT MIN(created_ts) FROM sessions WHERE status IN ('queued','parsing')").fetchone()[0]
    audit = audit_log(limit=5, offset=0, admin=admin)["entries"]
    feedback_open = conn.execute("SELECT COUNT(*) FROM feedback WHERE status='open'").fetchone()[0]
    # The full review gather scans aggregate combat evidence and belongs to the
    # workbench. Doing it for a dashboard badge added ~5 seconds to every admin
    # landing. This is the cheap actionable floor: tracked catalog candidates
    # with no human ruling. The workbench supplies the exact confidence-aware
    # total once somebody chooses that job.
    abilities_open = conn.execute(
        "SELECT COUNT(*) FROM ability_catalog ac LEFT JOIN ability_rulings ar "
        "ON ar.ability_name=ac.ability_name WHERE ar.ability_name IS NULL "
        "AND (ac.pet_seen>0 OR ac.proc_candidate>0)").fetchone()[0]
    reference = conn.execute("SELECT MAX(fetched_ts) FROM wiki_abilities").fetchone()[0]
    census = conn.execute("SELECT MAX(fetched_ts) FROM census_spells").fetchone()[0]
    security = ratelimit.security_stats()
    stuck = [row for row in _incident_rows(conn) if row["kind"] == "stuck"]
    return {
        "status": {"ingest": {"state": "active" if conn.execute(
            "SELECT 1 FROM sessions WHERE status='receiving' LIMIT 1").fetchone() else "quiet"},
            "parsing": {"state": "degraded" if failures else "healthy", "failures_24h": failures,
                        "oldest_job_age_s": max(0, now - oldest) if oldest else None},
            "storage": {"state": "healthy", "used_bytes": usage["storage_bytes"]},
            "processing": {"state": "stuck" if stuck else "healthy",
                           "stuck_count": len(stuck),
                           "oldest_stuck_age_s": max((r["age_s"] for r in stuck), default=None)},
            "security": {"state": "attention" if security["blocked_buckets"] else "quiet",
                         **security},
            "reference": {"state": "healthy" if reference or census else "quiet",
                          "wiki_refreshed_ts": reference, "census_refreshed_ts": census}},
        "actions": {"incidents": _incident_rows(conn)[:5], "feedback_open": feedback_open,
                    "abilities_open": abilities_open},
        "usage": usage, "recent_changes": audit,
    }


@router.get("/admin/groups")
def deleted_groups(admin=Depends(require_admin)):
    """Groups somebody deleted, and what would come back with each one.

    This is the one support request the metadata-only admin can answer: a
    delete is soft, so putting a roster back is a row update, not a rebuild.
    Still no route from here into anything the group could see."""
    return {"groups": g.deleted_groups(get_db())}


@router.post("/admin/groups/{group_id}/restore")
def restore_group(group_id: int, admin=Depends(require_admin)):
    conn = get_db()
    row = conn.execute("SELECT id, name, deleted_ts FROM groups WHERE id=?",
                       (group_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such group")
    if row["deleted_ts"] is None:
        raise HTTPException(409, "that group isn't deleted")
    with conn:
        g.restore_group(conn, group_id)
        g.audit(conn, admin["id"], "restore_group", f"group:{row['name']}")
    return {"group_id": group_id, "restored": True}


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


# ---------- abilities: what is a pet's, what is a proc, and what grants it ----
#
# The one page here that edits GAME knowledge rather than site state, and it
# stays inside the console's promise: an ability name is not somebody's parse,
# and the evidence beside it is site-wide sums and class names — never a player
# name, an entity or a row from anyone's raid (`census/abilityreview.py`).
#
# It exists because both labels used to be inferred and both were wrong at
# scale: one bare name mistaken for a dumbfire took a whole shadowknight
# spellbook into the pet catalog, and Census's "may cast X" grammar flagged
# `Berserk` and `Dragon Stance`, which are the class's own buttons. Inference
# now stops at "here is the evidence, how sure am I", and a person answers the
# rest.

GRANT_KINDS = ("spell", "aa", "item", "deity", "pet", "unknown")
UNITS = ("player", "pet")
FIRES = ("cast", "proc")
# Only `high` and a hand-written ruling count as settled; everything softer is
# what the page is FOR.
OPEN_CONFIDENCE = ("medium", "low")


def _shape(d: dict) -> dict:
    """One ability, as the page reads it. Internals stay out of the payload."""
    r = d.get("ruling")
    return {
        "ability": d["ability"],
        "suggest": d["suggest"], "confidence": d["confidence"], "why": d["why"],
        "classes": d["classes"],
        "scribed_by": d["scribed_by"],
        "grant_kind": d["grant_kind"], "grant_name": d["grant_name"],
        "grant_class": d["grant_class"], "trigger": d["trigger"],
        "curated_pet": d["curated_pet"], "curated_proc": d["curated_proc"],
        "pet_definite": d["pet_definite"], "pet_own": d["pet_own"],
        "pet_guess": d["pet_guess"], "pet_sessions": d["pet_sessions"],
        "player_casts": d["player_casts"], "mob_casts": d["mob_casts"],
        "distinct_players": d["distinct_players"],
        "prepare_lines": d["prepare_lines"], "logger_hits": d["logger_hits"],
        "total_damage": d["total_damage"],
        "player_classes": d["player_classes"],
        # what the EQ2 wiki has on the ability itself — `activated` is the one
        # the log cannot supply (gamewiki.py)
        "wiki_kind": d["wiki_kind"], "wiki_tiers": d["wiki_tiers"],
        "wiki_line": d["wiki_line"], "activated": d["activated"],
        "recast_s": d["recast_s"],
        "ruling": ({"unit": r["unit"], "fires": r["fires"],
                    "grant_kind": r["grant_kind"], "grant_name": r["grant_name"],
                    "grant_class": r["grant_class"], "note": r["note"],
                    "decided_ts": r["decided_ts"]} if r else None),
    }


@router.get("/admin/abilities")
def list_abilities(q: str = "", scope: str = "open", status: str = "",
                   suggestion: str = "", confidence: str = "", class_name: str = "",
                   evidence: str = "", sort: str = "damage",
                   admin=Depends(require_curator)):
    """`scope=open` is the work queue — everything under full confidence and
    not yet ruled on. `scope=all` (or any `q`) reaches every ability ever
    tracked, which is how a wrong answer gets fixed later.

    Grouped by class, and an ability lands under EVERY class that might own it
    — who scribes it, whose buff fires it, who was seen using it. One thing
    appearing three times is correct until somebody rules on it; `unclassed`
    holds the ones no class claims, which is where the gear and AA procs are.
    """
    from census.abilityreview import gather
    if scope not in ("open", "all"):
        raise HTTPException(422, "scope is open or all")
    rows = gather(get_db())
    q = q.strip().lower()
    if q:
        picked = [d for d in rows.values() if q in d["ability"].lower()]
    elif scope == "all":
        picked = list(rows.values())
    else:
        picked = [d for d in rows.values()
                  if not d["ruling"] and d["confidence"] in OPEN_CONFIDENCE]
    if status:
        if status == "unreviewed": picked = [d for d in picked if not d["ruling"]]
        elif status == "ruled": picked = [d for d in picked if d["ruling"]]
        elif status == "curated": picked = [d for d in picked if d["confidence"] == "curated"]
        elif status != "all": raise HTTPException(422, "unknown ability status")
    if suggestion:
        picked = [d for d in picked if d["suggest"] == suggestion]
    if confidence:
        picked = [d for d in picked if d["confidence"] == confidence]
    if class_name:
        picked = [d for d in picked if class_name in d["classes"] or
                  (class_name == "unclassed" and not d["classes"])]
    if evidence:
        tests = {
            "conflicting": lambda d: d["pet_definite"] and d["player_casts"],
            "prepare": lambda d: d["prepare_lines"],
            "no_reference": lambda d: not d["scribed_by"] and not d["wiki_kind"],
        }
        if evidence not in tests: raise HTTPException(422, "unknown evidence filter")
        picked = [d for d in picked if tests[evidence](d)]
    sorters = {
        "damage": lambda d: (-d["total_damage"], d["ability"]),
        "alphabetical": lambda d: (d["ability"].lower(),),
        "most_evidence": lambda d: (-(d["pet_definite"] + d["player_casts"] + d["logger_hits"]), d["ability"]),
        "least_evidence": lambda d: ((d["pet_definite"] + d["player_casts"] + d["logger_hits"]), d["ability"]),
    }
    if sort not in sorters: raise HTTPException(422, "unknown sort")
    picked.sort(key=sorters[sort])

    conn = get_db()
    actors = {r["ability_name"]: r["username"] for r in conn.execute(
        "SELECT ar.ability_name,u.username FROM ability_rulings ar "
        "LEFT JOIN users u ON u.id=ar.decided_by")}

    by_class: dict[str, list] = {}
    unclassed = []
    flat = []
    for d in picked:
        shaped = _shape(d)
        if shaped["ruling"]:
            shaped["ruling"]["decided_by"] = actors.get(d["ability"])
        flat.append(shaped)
        if not d["classes"]:
            unclassed.append(shaped)
        for cls in d["classes"]:
            by_class.setdefault(cls, []).append(shaped)
    return {
        "scope": scope, "q": q,
        "items": flat,
        "classes": [{"class": c, "abilities": by_class[c]} for c in sorted(by_class)],
        "unclassed": unclassed,
        "total": len(picked),
        "open_count": sum(1 for d in rows.values()
                          if not d["ruling"] and d["confidence"] in OPEN_CONFIDENCE),
        "tracked": len(rows),
        "reviewed_today": conn.execute(
            "SELECT COUNT(*) FROM ability_rulings WHERE decided_ts>=?", (int(time.time()) - 86400,)).fetchone()[0],
        "confidence_counts": {c: sum(1 for d in rows.values() if d["confidence"] == c)
                              for c in ("ruled", "curated", "high", "medium", "low")},
        "grant_kinds": list(GRANT_KINDS),
        # who a grant can be recorded against, widest tier first, each saying
        # who it covers — "is this a Predator AA or a Ranger one" is the
        # question, and it cannot be asked with a flat list of 26 subclasses
        "grant_targets": [
            {"name": t, "label": classtree.label(t),
             "tier": ("archetype" if t in classtree.ARCHETYPES
                      else "class" if t in classtree.CLASSES else "subclass"),
             "covers": sorted(classtree.expand(t))}
            for t in classtree.GRANT_TARGETS],
    }


@router.put("/admin/abilities/{ability_name:path}")
def rule_ability(ability_name: str, payload: dict = Body(...),
                 admin=Depends(require_curator)):
    """Settle one ability. This beats the curated seed and everything the
    parser learns — see `census/catalog.pet_ability_names`."""
    unit = (payload.get("unit") or "player").strip()
    fires = (payload.get("fires") or "cast").strip()
    kind = (payload.get("grant_kind") or "").strip() or None
    if unit not in UNITS:
        raise HTTPException(422, f"unit is one of {UNITS}")
    if fires not in FIRES:
        raise HTTPException(422, f"fires is one of {FIRES}")
    if kind is not None and kind not in GRANT_KINDS:
        raise HTTPException(422, f"grant_kind is one of {GRANT_KINDS}")
    name = ability_name.strip()
    if not name:
        raise HTTPException(422, "ability name is empty")
    # A grant target is any tier of EQ2's tree, because AAs are granted at any
    # tier — `predator` reaches ranger and assassin, `scout` all seven. Stored
    # normalized so a ruling only ever holds targets `expand` can honour; a
    # name the tree does not know is rejected rather than silently dropped,
    # since "predatr" would otherwise save as a grant reaching nobody.
    typed = (payload.get("grant_class") or "").strip()
    grant_class = classtree.normalize(typed) or None
    if typed and not grant_class:
        raise HTTPException(
            422, f"unknown class or group: {typed!r} — expected any of "
                 f"{', '.join(classtree.GRANT_TARGETS)}")
    row = (name, unit, fires, kind,
           (payload.get("grant_name") or "").strip() or None,
           grant_class,
           (payload.get("note") or "").strip() or None,
           admin["id"], int(time.time()))
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT INTO ability_rulings (ability_name, unit, fires, grant_kind, "
            "grant_name, grant_class, note, decided_by, decided_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(ability_name) DO UPDATE SET "
            "unit=excluded.unit, fires=excluded.fires, grant_kind=excluded.grant_kind, "
            "grant_name=excluded.grant_name, grant_class=excluded.grant_class, "
            "note=excluded.note, decided_by=excluded.decided_by, "
            "decided_ts=excluded.decided_ts", row)
        g.audit(conn, admin["id"], "rule_ability", f"ability:{name}",
                f"{unit}/{fires}/{kind or '-'}")
    # the labels are read per request from this table, so the raid pages are
    # already right — nothing to reparse
    return {"ability": name, "ruled": True}


@router.delete("/admin/abilities/{ability_name:path}")
def unrule_ability(ability_name: str, admin=Depends(require_curator)):
    """Take the hand-written answer back off, returning the ability to whatever
    the evidence says."""
    conn = get_db()
    with conn:
        cur = conn.execute("DELETE FROM ability_rulings WHERE ability_name=?",
                           (ability_name,))
        if cur.rowcount:
            g.audit(conn, admin["id"], "unrule_ability", f"ability:{ability_name}")
    return {"ability": ability_name, "ruled": False}


# ---------- AoE timers: pooled game evidence and reversible curator rulings --

@router.get("/admin/timer-mechanics")
def timer_mechanics(admin=Depends(require_curator)):
    from pipeline import aoes
    conn = get_db()
    curated = {(r["kind"], r["name"]): dict(r) for r in conn.execute(
        "SELECT tm.*,u.username decided_by_name FROM timer_mechanics tm "
        "LEFT JOIN users u ON u.id=tm.decided_by")}
    rows = []
    for kind, source in (("reuse_debuff", aoes.reuse_debuffs()),
                         ("reflect_window", aoes.reflect_windows())):
        for name, config in source.items():
            ruling = curated.get((kind, name))
            rows.append({"kind": kind, "name": name, "config": config,
                         "curated": bool(ruling), "note": ruling["note"] if ruling else None,
                         "decided_by": ruling["decided_by_name"] if ruling else None})
    return {"items": sorted(rows, key=lambda r: (r["kind"], r["name"]))}


@router.put("/admin/timer-mechanics/{kind}/{name}")
def set_timer_mechanic(kind: str, name: str, payload: dict = Body(...),
                       admin=Depends(require_curator)):
    if kind not in ("reuse_debuff", "reflect_window"):
        raise HTTPException(422, "kind is reuse_debuff or reflect_window")
    note = str(payload.get("note") or "").strip()
    config = payload.get("config")
    if not note or not isinstance(config, dict):
        raise HTTPException(422, "config object and note are required")
    if any(not isinstance(v, (str, int, float, bool, list, dict, type(None))) for v in config.values()):
        raise HTTPException(422, "config contains an unsupported value")
    required = ("duration_s", "recast_s") if kind == "reuse_debuff" else ("window_s",)
    for field in required:
        try: value = float(config.get(field))
        except (TypeError, ValueError): raise HTTPException(422, f"{field} must be a number")
        if value <= 0 or value > 3600: raise HTTPException(422, f"{field} is out of range")
    conn = get_db(); now = int(time.time())
    with conn:
        conn.execute("INSERT INTO timer_mechanics(kind,name,config_json,note,decided_by,decided_ts) "
                     "VALUES(?,?,?,?,?,?) ON CONFLICT(kind,name) DO UPDATE SET "
                     "config_json=excluded.config_json,note=excluded.note,"
                     "decided_by=excluded.decided_by,decided_ts=excluded.decided_ts",
                     (kind, name, json.dumps(config, separators=(",", ":")), note, admin["id"], now))
        g.audit(conn, admin["id"], "rule_timer_mechanic", f"{kind}:{name}", note)
    from pipeline import aoes
    aoes.reuse_debuffs.cache_clear(); aoes.reflect_windows.cache_clear()
    return {"kind": kind, "name": name, "ruled": True}


@router.delete("/admin/timer-mechanics/{kind}/{name}")
def clear_timer_mechanic(kind: str, name: str, admin=Depends(require_curator)):
    conn = get_db()
    with conn:
        changed = conn.execute("DELETE FROM timer_mechanics WHERE kind=? AND name=?",
                               (kind, name)).rowcount
        if changed: g.audit(conn, admin["id"], "clear_timer_mechanic", f"{kind}:{name}")
    from pipeline import aoes
    aoes.reuse_debuffs.cache_clear(); aoes.reflect_windows.cache_clear()
    return {"kind": kind, "name": name, "ruled": False}

def _timer_rows(conn):
    from pipeline import aoelearn, aoes
    learned = aoelearn.learn(conn)
    reported = aoes.reported_timers()
    rows = []
    for (mob, ability), row in learned.items():
        rep = (reported.get(ability) or {}).get("timer_s")
        ruling = row.get("ruling")
        effective, source = aoelearn.timer_for(learned, mob, ability, rep)
        if ruling and ruling["excluded"]:
            state = "excluded"
        elif ruling and ruling["override_s"] is not None:
            state = "overridden"
        elif row["several_bodies"]:
            state = "learning_blocked"
        elif row["clean_s"] and rep and abs(row["clean_s"] - rep) >= max(5, rep * .15):
            state = "disagreement"
        elif row["base_s"]:
            state = "healthy"
        else:
            state = "learning"
        last = conn.execute(
            "SELECT MAX(cast_ts) FROM aoe_cycles WHERE source_name=? AND ability=?",
            (mob, ability)).fetchone()[0]
        rows.append({**row, "mob": mob, "reported_s": rep, "effective_s": effective,
                     "effective_source": source, "state": state, "last_observation_ts": last,
                     "ruling": dict(ruling) if ruling else None})
    return rows


@router.get("/admin/timers")
def list_timers(q: str = "", state: str = "needs_review", limit: int = 100,
                offset: int = 0, admin=Depends(require_curator)):
    rows = _timer_rows(get_db())
    needle = q.strip().lower()
    if needle:
        rows = [r for r in rows if needle in r["mob"].lower() or needle in r["ability"].lower()]
    if state == "needs_review":
        rows = [r for r in rows if r["state"] in
                ("disagreement", "learning_blocked", "learning")]
    elif state != "all":
        rows = [r for r in rows if r["state"] == state]
    rows.sort(key=lambda r: ({"disagreement": 0, "learning_blocked": 1,
                              "learning": 2}.get(r["state"], 9), r["mob"], r["ability"]))
    total = len(rows); limit = max(1, min(limit, 500)); offset = max(0, offset)
    return {"items": rows[offset:offset + limit], "total": total, "limit": limit,
            "offset": offset}


@router.get("/admin/timers/{mob}/{ability}")
def timer_detail(mob: str, ability: str, admin=Depends(require_curator)):
    conn = get_db()
    row = next((r for r in _timer_rows(conn)
                if r["mob"] == mob and r["ability"] == ability), None)
    if row is None:
        raise HTTPException(404, "no such timer evidence")
    cycles = rows_to_dicts(conn.execute(
        "SELECT gap_s, swiped FROM aoe_cycles WHERE source_name=? AND ability=? "
        "ORDER BY gap_s", (mob, ability)))
    row["clean_intervals"] = [r["gap_s"] for r in cycles if not r["swiped"]]
    row["swiped_intervals"] = [r["gap_s"] for r in cycles if r["swiped"]]
    row["thresholds"] = {"minimum_agreeing": __import__("pipeline.aoelearn", fromlist=[""]).MIN_AGREE,
                         "minimum_pulls": __import__("pipeline.aoelearn", fromlist=[""]).MIN_FIGHTS}
    row["reason"] = ("Excluded by curator." if row["state"] == "excluded" else
                     "A curator override is authoritative." if row["state"] == "overridden" else
                     f"Multiple bodies block adoption ({row['several_bodies']})." if row["several_bodies"] else
                     "Measured evidence is still below the adoption threshold." if not row["base_s"] else
                     "Clean intervals across distinct pulls meet the adoption threshold.")
    return row


@router.put("/admin/timers/{mob}/{ability}")
def rule_timer(mob: str, ability: str, payload: dict = Body(...),
               admin=Depends(require_curator)):
    conn = get_db()
    evidence = next((r for r in _timer_rows(conn)
                     if r["mob"] == mob and r["ability"] == ability), None)
    if evidence is None:
        raise HTTPException(404, "no such timer evidence")
    note = str(payload.get("note") or "").strip()
    if not note:
        raise HTTPException(422, "a curator note is required")
    accept = bool(payload.get("accept_measured"))
    override = payload.get("override_s")
    if accept:
        override = evidence["clean_s"]
        if override is None:
            raise HTTPException(409, "there is no measured timer to accept")
    if override not in (None, ""):
        override = float(override)
        if override <= 0 or override > 3600:
            raise HTTPException(422, "override_s must be between 0 and 3600")
    else:
        override = None
    excluded = int(bool(payload.get("excluded")))
    split = payload.get("split_mob")
    split = None if split is None else int(bool(split))
    now = int(time.time())
    with conn:
        conn.execute(
            "INSERT INTO timer_rulings(source_name,ability,override_s,accepted_measured,"
            "excluded,split_mob,note,decided_by,decided_ts) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source_name,ability) DO UPDATE SET override_s=excluded.override_s,"
            "accepted_measured=excluded.accepted_measured,excluded=excluded.excluded,"
            "split_mob=excluded.split_mob,note=excluded.note,decided_by=excluded.decided_by,"
            "decided_ts=excluded.decided_ts",
            (mob, ability, override, int(accept), excluded, split, note, admin["id"], now))
        g.audit(conn, admin["id"], "rule_timer", f"timer:{mob}|{ability}",
                f"{override if override is not None else '-'}s; {note}")
    return {"mob": mob, "ability": ability, "ruled": True}


@router.delete("/admin/timers/{mob}/{ability}")
def clear_timer_ruling(mob: str, ability: str, admin=Depends(require_curator)):
    conn = get_db()
    with conn:
        changed = conn.execute(
            "DELETE FROM timer_rulings WHERE source_name=? AND ability=?", (mob, ability)).rowcount
        if changed:
            g.audit(conn, admin["id"], "clear_timer_ruling", f"timer:{mob}|{ability}")
    return {"mob": mob, "ability": ability, "ruled": False}
