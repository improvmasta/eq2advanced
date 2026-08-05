"""Zone runs: the primary navigation surface. A run is a contiguous zone visit
(pipeline/zoneruns.py); the list powers the date-grouped home page, detail
powers the zone page's encounter rail, and /report is the raid report scoped
to the run's canonical encounters (cross-session, deduped)."""

import json
import time

from fastapi import APIRouter, Body, Depends, HTTPException

import groups as groupsmod
import memo
import raidmatch
from db import get_db, row_to_dict, rows_to_dicts
from groups import PERSONAL_RUN_IDS, SHARED_RUN_IDS, VISIBLE_RUN_IDS
from pipeline.zoneruns import encounter_fp, rebuild_zone_runs
from security import (optional_user, owned_zone_run, require_admin, require_user,
                      visible_zone_run)

router = APIRouter(tags=["zone-runs"])


def _spark(conn, run_ids: list[int]) -> tuple[dict[int, list[int]], dict[int, int]]:
    """Raid DPS per fight, in fight order, for the home page's sparklines —
    plus each run's total player damage, which over `combat_s` is the raid-wide
    DPS the list prints. One grouped query for every listed run — the shape of
    a night is cheap enough to ship with the list."""
    if not run_ids:
        return {}, {}
    ph = ",".join("?" * len(run_ids))
    out: dict[int, list[int]] = {}
    dmg: dict[int, int] = {}
    for r in conn.execute(
            "SELECT e.zone_run_id AS run_id, e.duration_s, "
            "COALESCE(SUM(CASE WHEN en.kind='player' THEN a.damage END), 0) AS dmg "
            "FROM encounters e "
            "LEFT JOIN encounter_actor_stats a ON a.encounter_id = e.id "
            "LEFT JOIN entities en ON en.id = a.entity_id "
            f"WHERE e.zone_run_id IN ({ph}) "
            "GROUP BY e.id ORDER BY e.started_ts", run_ids):
        out.setdefault(r["run_id"], []).append(
            round(r["dmg"] / max(r["duration_s"], 1)))
        dmg[r["run_id"]] = dmg.get(r["run_id"], 0) + r["dmg"]
    return out, dmg


@router.get("/zone-runs")
def list_zone_runs(scope: str = "all", roster: int = 0, user=Depends(optional_user)):
    """The raid list. `scope` is mine | shared | all (default). Signed out, the
    only runs that exist are the published ones.

    `roster=1` sends each night's roster with it, parsed — the Compare page's
    picker facets on names client-side rather than asking the server per
    keystroke. Same visibility predicate, so it reveals nothing a viewer could
    not already read fight by fight; parsed here so the client never learns how
    the roster is stored."""
    conn = get_db()
    uid = user["id"] if user else None
    if scope not in ("mine", "shared", "all"):
        raise HTTPException(422, "scope is mine, shared or all")
    if scope == "mine":
        where = "WHERE c.user_id = :uid"
    elif scope == "shared":
        where = f"WHERE z.id IN ({SHARED_RUN_IDS})"
    else:
        where = f"WHERE z.id IN ({VISIBLE_RUN_IDS})"
    rows = conn.execute(
        "SELECT z.*, c.name AS character_name, c.user_id AS owner_id, "
        "u.username AS owner_username "
        "FROM zone_runs z JOIN characters c ON c.id = z.character_id "
        "JOIN users u ON u.id = c.user_id "
        f"{where} ORDER BY z.started_ts DESC", {"uid": uid}).fetchall()
    runs = rows_to_dicts(rows)
    run_ids = [r["id"] for r in runs]
    spark, run_dmg = _spark(conn, run_ids)
    mine_ids = {r["id"] for r in runs if r["owner_id"] == uid} if uid else set()
    joined = _merged_runs(conn, {r["character_id"] for r in runs if r["id"] in mine_ids})
    shares = groupsmod.shares_for_runs(conn, sorted(mine_ids))
    # the other direction: which of MY groups bring me the runs that are not mine
    via = groupsmod.shared_via_for_runs(
        conn, uid, sorted(set(run_ids) - mine_ids)) if uid else {}
    public = {r["zone_run_id"] for r in conn.execute(
        "SELECT zone_run_id FROM public_runs")}
    # runs reaching you through a group or your own account, ignoring publishing:
    # `via_public` therefore means "the ONLY reason you can see this is that
    # somebody published it", which is exactly what the list's switch filters
    personal = {r["id"] for r in conn.execute(PERSONAL_RUN_IDS, {"uid": uid})} if uid else set()
    for r in runs:
        r["spark"] = spark.get(r["id"], [])
        r["raid_dps"] = round(run_dmg.get(r["id"], 0) / max(r["combat_s"] or 0, 1))
        r["mine"] = r["id"] in mine_ids
        r["public"] = r["id"] in public
        r["via_public"] = r["id"] in public and r["id"] not in personal
        # sharing state is the owner's business; a viewer is told nothing about
        # who else can see it — only which of their OWN groups brought it here
        r["shared_with"] = shares.get(r["id"], []) if r["mine"] else []
        r["shared_via"] = [] if r["mine"] else via.get(r["id"], [])
        # "this run only looks like one visit because you merged it" — the list
        # offers Unmerge exactly here
        r["merged"] = r["id"] in joined
    # Several people uploading one night is several runs, and a list that prints
    # them as five raids is wrong about the evening. `raid_key` groups them and
    # `primary` is the site's pick; whose parse a VIEWER opens is the browser's
    # decision, because your own always wins and the payload is shared.
    raidmatch.annotate(runs)
    for r in runs:
        raw = r.pop("roster_json", None)
        if roster:
            r["roster"] = json.loads(raw) if raw else []
    return {"zone_runs": runs, "scope": scope, "signed_in": user is not None}


def _merged_runs(conn, character_ids: set[int]) -> set[int]:
    """Run ids holding at least one `join` edit — runs made by hand rather than
    by the segmenter."""
    if not character_ids:
        return set()
    ph = ",".join("?" * len(character_ids))
    ids = list(character_ids)
    fps = {(r["character_id"], r["fp"]) for r in conn.execute(
        f"SELECT character_id, fp FROM run_edits "
        f"WHERE kind='join' AND character_id IN ({ph})", ids)}
    if not fps:
        return set()
    return {r["zone_run_id"] for r in conn.execute(
        f"SELECT e.zone_run_id, e.started_ts, e.zone, e.name, s.character_id "
        f"FROM encounters e JOIN sessions s ON s.id = e.session_id "
        f"WHERE e.zone_run_id IS NOT NULL AND s.character_id IN ({ph})", ids)
        if (r["character_id"], encounter_fp(r)) in fps}


def _run_encounters(conn, run) -> list:
    """Canonical encounters with the logger's headline numbers. The logger
    entity is per encounter's own session (entities are session-scoped)."""
    return conn.execute(
        "SELECT e.id, e.session_id, e.zone, e.name, e.is_named, e.started_ts, "
        "e.ended_ts, e.duration_s, e.success, "
        "s.damage AS logger_damage, s.dps AS logger_dps, s.heals AS logger_heals, "
        "(SELECT COUNT(*) FROM encounter_actor_stats a WHERE a.encounter_id = e.id) "
        "AS actor_count "
        "FROM encounters e "
        "LEFT JOIN entities le ON le.session_id = e.session_id "
        "  AND le.kind = 'player' AND le.name = ? "
        "LEFT JOIN encounter_actor_stats s ON s.encounter_id = e.id "
        "  AND s.entity_id = le.id "
        "WHERE e.zone_run_id = ? ORDER BY e.started_ts",
        (run["character_name"], run["id"])).fetchall()


@router.get("/zone-runs/{run_id}")
def zone_run_detail(run_id: int, user=Depends(optional_user)):
    conn = get_db()
    run = visible_zone_run(conn, user, run_id)
    payload = row_to_dict(run)
    mine = user is not None and run["owner_id"] == user["id"]
    payload["mine"] = mine        # the page hides every edit control without it
    payload["public"] = conn.execute(
        "SELECT 1 FROM public_runs WHERE zone_run_id=?", (run_id,)).fetchone() is not None
    payload["shared_with"] = (
        groupsmod.shares_for_runs(conn, [run_id]).get(run_id, []) if mine else [])
    payload.pop("roster_json", None)
    # Somebody else parsed the same night: the page says so and offers the
    # switch, rather than leaving a link somewhere else as the only way across.
    payload["alternates"] = raidmatch.alternates(
        conn, VISIBLE_RUN_IDS, user["id"] if user else None, run)
    return {"zone_run": payload,
            "encounters": rows_to_dicts(_run_encounters(conn, run))}


# ---------- player search ----------
# The Compare page's player-first picker: "every parse where Bobby appears".
# roster_json stays out of the list payloads (weight); the server answers the
# question instead, against the same visibility predicate as the list. Runs
# below the roster threshold (roster_json NULL — json_each yields no rows for
# them) are simply absent here; the raid-first picker still reaches them.
# The Compare picker no longer calls these — it takes the whole list with
# `?roster=1` once and facets in the browser, which is instant and cross-narrows.
# They stay because "every parse where this name appears" is a real question a
# client can ask cheaply, and because they carry the predicate's tests.

@router.get("/players")
def search_players(q: str, user=Depends(optional_user)):
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(422, "q must be at least 2 characters")
    conn = get_db()
    uid = user["id"] if user else None
    rows = conn.execute(
        "SELECT j.value AS name, COUNT(*) AS run_count, MAX(z.started_ts) AS last_ts "
        "FROM zone_runs z, json_each(z.roster_json) j "
        f"WHERE z.id IN ({VISIBLE_RUN_IDS}) AND j.value LIKE :pat "
        "GROUP BY j.value ORDER BY run_count DESC, j.value LIMIT 20",
        {"uid": uid, "pat": f"%{q}%"}).fetchall()
    return {"players": rows_to_dicts(rows)}


@router.get("/players/{name}/runs")
def player_runs(name: str, user=Depends(optional_user)):
    """Visible runs whose roster carries this exact name (it came from the
    search above). Unknown name is an empty list, not a 404 — the visibility
    filter already hides what must be hidden."""
    conn = get_db()
    uid = user["id"] if user else None
    rows = conn.execute(
        "SELECT z.id, z.zone, z.started_ts, z.encounter_count, z.named_count, "
        "z.raider_count, c.name AS character_name "
        "FROM zone_runs z JOIN characters c ON c.id = z.character_id, "
        "json_each(z.roster_json) j "
        f"WHERE z.id IN ({VISIBLE_RUN_IDS}) AND j.value = :name "
        "ORDER BY z.started_ts DESC",
        {"uid": uid, "name": name}).fetchall()
    return {"runs": rows_to_dicts(rows)}


# ---------- sharing ----------

@router.get("/zone-runs/{run_id}/shares")
def get_run_shares(run_id: int, user=Depends(require_user)):
    """Which of MY groups this raid goes to. Every group I'm in is listed, with
    the ones it currently reaches marked, so the control is one list."""
    conn = get_db()
    owned_zone_run(conn, user, run_id)
    current = {s["group_id"]: s for s in
               groupsmod.shares_for_runs(conn, [run_id]).get(run_id, [])}
    out = []
    for g in groupsmod.my_groups(conn, user["id"]):
        s = current.get(g["id"])
        out.append({"group_id": g["id"], "name": g["name"],
                    "shared": s is not None, "auto": bool(s and s["auto"])})
    return {"groups": out, "public": conn.execute(
        "SELECT 1 FROM public_runs WHERE zone_run_id=?", (run_id,)).fetchone() is not None}


@router.put("/zone-runs/{run_id}/shares")
def set_run_shares(run_id: int, payload: dict = Body(...), user=Depends(require_user)):
    """Set the exact set of groups this raid reaches. A group the character
    auto-shares with is turned off with a `hide` row rather than by deleting the
    auto-share, so the rest of that character's raids keep flowing.

    Every group reaching this raid through a STANDING decision has to be counted
    in `auto` below: the delete only removes explicit `share` rows, so a
    read-time branch missing from that set would survive it and the untick would
    silently revoke nothing. There are two such branches today — the character's
    auto-share and the uploader's connected guild tag — and if a third is ever
    added to `groups.py`, it belongs here too."""
    conn = get_db()
    owned_zone_run(conn, user, run_id)
    wanted = {int(g) for g in payload.get("group_ids") or []}
    mine = {g["id"] for g in groupsmod.my_groups(conn, user["id"])}
    if wanted - mine:
        raise HTTPException(404, "no such group")
    # the guards must match the read-time predicate exactly (one definition,
    # `groups.AUTO_SHARE_REACHES`): a share that doesn't reach this run — too
    # old for its since_ts, or group content under a raids-only share — must be
    # unticked with a plain delete and no `hide`, or the row lingers and blocks
    # a later opt-in
    auto = {r["group_id"] for r in conn.execute(
        "SELECT cs.group_id FROM character_shares cs JOIN zone_runs z "
        f"ON z.character_id = cs.character_id WHERE z.id=? "
        f"AND {groupsmod.AUTO_SHARE_REACHES}", (run_id,))}
    auto |= {r["group_id"] for r in conn.execute(
        f"SELECT gs.group_id FROM zone_runs z {groupsmod.GUILD_SHARE_OWNER_MATCH} "
        f"WHERE z.id=? AND {groupsmod.GUILD_SHARE_REACHES}", (run_id,))}
    now = int(time.time())
    with conn:
        # only my own groups are rewritten: a run can also carry shares to groups
        # I have since left, and this call knows nothing about those
        if mine:
            ph = ",".join("?" * len(mine))
            conn.execute(f"DELETE FROM run_shares WHERE zone_run_id=? AND group_id IN ({ph})",
                         (run_id, *sorted(mine)))
        for gid in sorted(wanted - auto):
            conn.execute("INSERT INTO run_shares (zone_run_id, group_id, mode, created_ts) "
                         "VALUES (?,?,'share',?)", (run_id, gid, now))
        for gid in sorted(auto - wanted):
            conn.execute("INSERT INTO run_shares (zone_run_id, group_id, mode, created_ts) "
                         "VALUES (?,?,'hide',?)", (run_id, gid, now))
    return get_run_shares(run_id, user)


@router.put("/zone-runs/{run_id}/public")
def set_run_public(run_id: int, payload: dict = Body(...), user=Depends(require_admin)):
    """Publish a raid to everyone, signed in or not — the demo/testing switch.

    Admin-only, and only for the admin's OWN raids: publishing is the one action
    that removes a privacy boundary, so it must never be reachable for data that
    merely got shared with them."""
    conn = get_db()
    run = owned_zone_run(conn, user, run_id)
    public = bool(payload.get("public"))
    with conn:
        if public:
            conn.execute(
                "INSERT OR IGNORE INTO public_runs (zone_run_id, published_by, created_ts) "
                "VALUES (?,?,?)", (run_id, user["id"], int(time.time())))
        else:
            conn.execute("DELETE FROM public_runs WHERE zone_run_id=?", (run_id,))
        groupsmod.audit(conn, user["id"], "publish" if public else "unpublish",
                        f"zone_run:{run_id}", run["zone"])
    return {"zone_run_id": run_id, "public": public}


@router.get("/zone-runs/{run_id}/report")
def zone_run_report(run_id: int, user=Depends(optional_user)):
    from coach.raidreport import build_for_encounters

    conn = get_db()
    run = visible_zone_run(conn, user, run_id)
    encounters = conn.execute(
        "SELECT id, session_id, zone, name, is_named, started_ts, ended_ts, "
        "duration_s, success FROM encounters WHERE zone_run_id=? ORDER BY started_ts",
        (run_id,)).fetchall()
    # a 60-fight night replays every stored event to build this; the answer is
    # the same until something is written, so it is memoized (memo.py) and the
    # shared payload is never mutated in place
    report = memo.get_or_build(
        ("run-report", run_id), lambda: build_for_encounters(conn, encounters))
    return {**report, "zone_run_id": run_id,
            "character_name": run["character_name"]}


# ---------- hand edits: delete fights, merge and unmerge runs ----------
#
# Every edit is a `run_edits` row keyed by encounter fingerprint, then the
# character's runs are rebuilt from scratch. Nothing is destroyed and nothing
# is patched in place: the rebuild is the only thing that ever writes run
# membership, so an edit means the same thing after the next reparse.


def _apply(conn, character_id: int, adds: list[tuple[str, str]],
           removes: list[tuple[str, str]]) -> None:
    now = int(time.time())
    with conn:
        for fp, kind in removes:
            conn.execute("DELETE FROM run_edits WHERE character_id=? AND fp=? AND kind=?",
                         (character_id, fp, kind))
        for fp, kind in adds:
            conn.execute(
                "INSERT OR IGNORE INTO run_edits (character_id, fp, kind, created_ts) "
                "VALUES (?,?,?,?)", (character_id, fp, kind, now))
        rebuild_zone_runs(conn, character_id)


def _empty_sessions(conn, character_id: int) -> list[dict]:
    """Uploads that have no fights left after an edit — the UI offers to delete
    the log itself, which is the only way the raw file ever goes away."""
    return rows_to_dicts(conn.execute(
        "SELECT s.id, s.upload_name, s.source, s.created_ts FROM sessions s "
        "WHERE s.character_id=? AND s.status='ready' AND EXISTS "
        "  (SELECT 1 FROM encounters e WHERE e.session_id=s.id) "
        "AND NOT EXISTS "
        "  (SELECT 1 FROM encounters e WHERE e.session_id=s.id AND e.deleted_ts IS NULL)",
        (character_id,)).fetchall())


def _own_encounters(conn, user, enc_ids: list[int]) -> tuple[int, list[str]]:
    """-> (character_id, fingerprints). Every id must belong to one character
    the user owns; a mixed or foreign selection is a 404 like anything else."""
    if not enc_ids:
        raise HTTPException(422, "no encounter ids")
    ph = ",".join("?" * len(enc_ids))
    rows = conn.execute(
        f"SELECT e.started_ts, e.zone, e.name, s.character_id, c.user_id "
        f"FROM encounters e JOIN sessions s ON s.id = e.session_id "
        f"JOIN characters c ON c.id = s.character_id WHERE e.id IN ({ph})",
        enc_ids).fetchall()
    if len(rows) != len(set(enc_ids)):
        raise HTTPException(404, "no such encounter")
    chars = {r["character_id"] for r in rows}
    if len(chars) != 1:
        raise HTTPException(422, "encounters span more than one character")
    if any(r["user_id"] != user["id"] for r in rows):
        raise HTTPException(404, "no such encounter")
    return chars.pop(), sorted({encounter_fp(r) for r in rows})


@router.post("/encounters/delete")
def delete_encounters(payload: dict = Body(...), user=Depends(require_user)):
    """Hide fights. Duplicated fights share a fingerprint, so one delete covers
    every copy — otherwise the 'deleted' fight would reappear from the other
    overlapping file."""
    conn = get_db()
    ids = [int(x) for x in payload.get("ids") or []]
    character_id, fps = _own_encounters(conn, user, ids)
    _apply(conn, character_id, [(fp, "delete") for fp in fps], [])
    return {"deleted": len(fps), "fingerprints": fps,
            "empty_sessions": _empty_sessions(conn, character_id)}


@router.post("/encounters/restore")
def restore_encounters(payload: dict = Body(...), user=Depends(require_user)):
    """Undo a delete from the fingerprints the delete call handed back."""
    conn = get_db()
    fps = [str(x) for x in payload.get("fingerprints") or []]
    character_id = int(payload.get("character_id") or 0)
    row = conn.execute("SELECT user_id FROM characters WHERE id=?", (character_id,)).fetchone()
    if row is None or row["user_id"] != user["id"]:
        raise HTTPException(404, "no such character")
    _apply(conn, character_id, [], [(fp, "delete") for fp in fps])
    return {"restored": len(fps)}


@router.delete("/zone-runs/{run_id}")
def delete_zone_run(run_id: int, user=Depends(require_user)):
    """Delete a whole night: every fight in it, in one edit."""
    conn = get_db()
    run = owned_zone_run(conn, user, run_id)
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM encounters WHERE zone_run_id=?", (run_id,))]
    character_id, fps = _own_encounters(conn, user, ids)
    _apply(conn, character_id, [(fp, "delete") for fp in fps], [])
    return {"deleted": len(fps), "fingerprints": fps,
            "character_id": run["character_id"],
            "empty_sessions": _empty_sessions(conn, character_id)}


@router.post("/zone-runs/merge")
def merge_zone_runs(payload: dict = Body(...), user=Depends(require_user)):
    """Fold two or more runs into one. The edit is a `join` on the first fight
    of every run after the earliest — that is exactly the boundary the
    segmenter would otherwise break at."""
    conn = get_db()
    ids = [int(x) for x in payload.get("ids") or []]
    if len(ids) < 2:
        raise HTTPException(422, "merge needs at least two runs")
    runs = sorted((owned_zone_run(conn, user, i) for i in ids),
                  key=lambda r: r["started_ts"])
    if len({r["character_id"] for r in runs}) != 1:
        raise HTTPException(422, "runs belong to different characters")
    character_id = runs[0]["character_id"]
    adds, removes = [], []
    for run in runs[1:]:
        first = conn.execute(
            "SELECT started_ts, zone, name FROM encounters WHERE zone_run_id=? "
            "ORDER BY started_ts LIMIT 1", (run["id"],)).fetchone()
        if first is None:
            continue
        fp = encounter_fp(first)
        adds.append((fp, "join"))
        removes.append((fp, "break"))
    _apply(conn, character_id, adds, removes)
    return {"merged": len(runs)}


@router.post("/zone-runs/{run_id}/unmerge")
def unmerge_zone_run(run_id: int, user=Depends(require_user)):
    """Undo every merge inside this run — it falls back to whatever the
    segmenter would have made of the same fights."""
    conn = get_db()
    run = owned_zone_run(conn, user, run_id)
    fps = {encounter_fp(r) for r in conn.execute(
        "SELECT started_ts, zone, name FROM encounters WHERE zone_run_id=?", (run_id,))}
    _apply(conn, run["character_id"], [], [(fp, "join") for fp in fps])
    return {"unmerged": run_id}


@router.post("/zone-runs/{run_id}/split")
def split_zone_run(run_id: int, payload: dict = Body(...), user=Depends(require_user)):
    """Unmerge: the named fight starts a run of its own from here on."""
    conn = get_db()
    run = owned_zone_run(conn, user, run_id)
    enc = conn.execute(
        "SELECT started_ts, zone, name FROM encounters WHERE id=? AND zone_run_id=?",
        (int(payload.get("encounter_id") or 0), run_id)).fetchone()
    if enc is None:
        raise HTTPException(404, "no such fight in this run")
    fp = encounter_fp(enc)
    _apply(conn, run["character_id"], [(fp, "break")], [(fp, "join")])
    return {"split_at": fp}
