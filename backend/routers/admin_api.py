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
import classtree
import groups as g
from db import UPLOADS_DIR, get_db, get_int_setting, rows_to_dicts, set_setting
from security import require_admin, require_curator

router = APIRouter(tags=["admin"])

SETTINGS_KEYS = ("upload_max_bytes", "storage_max_bytes", "registration_open")


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


@router.get("/admin/audit")
def audit_log(limit: int = 200, admin=Depends(require_admin)):
    conn = get_db()
    return {"entries": rows_to_dicts(conn.execute(
        "SELECT a.*, u.username AS actor FROM audit_log a "
        "LEFT JOIN users u ON u.id = a.actor_user_id "
        # id breaks the tie: several actions land in the same second and the
        # order they happened in is the whole point of a log
        "ORDER BY a.ts DESC, a.id DESC LIMIT ?", (max(1, min(limit, 1000)),)))}


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
def list_abilities(q: str = "", scope: str = "open", admin=Depends(require_curator)):
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

    by_class: dict[str, list] = {}
    unclassed = []
    for d in sorted(picked, key=lambda x: (-x["total_damage"], x["ability"])):
        shaped = _shape(d)
        if not d["classes"]:
            unclassed.append(shaped)
        for cls in d["classes"]:
            by_class.setdefault(cls, []).append(shaped)
    return {
        "scope": scope, "q": q,
        "classes": [{"class": c, "abilities": by_class[c]} for c in sorted(by_class)],
        "unclassed": unclassed,
        "total": len(picked),
        "open_count": sum(1 for d in rows.values()
                          if not d["ruling"] and d["confidence"] in OPEN_CONFIDENCE),
        "tracked": len(rows),
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
