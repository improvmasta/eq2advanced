"""Zone runs: the primary navigation surface. A run is a contiguous zone visit
(pipeline/zoneruns.py); the list powers the date-grouped home page, detail
powers the zone page's encounter rail, and /report is the raid report scoped
to the run's canonical encounters (cross-session, deduped)."""

import time

from fastapi import APIRouter, Body, Depends, HTTPException

import memo
from db import get_db, row_to_dict, rows_to_dicts
from pipeline.zoneruns import encounter_fp, rebuild_zone_runs
from security import is_admin, require_user

router = APIRouter(tags=["zone-runs"])


def visible_zone_run(conn, user, run_id: int):
    """Zone-run row (with character_name) if the user may see it, else 404."""
    run = conn.execute(
        "SELECT z.*, c.name AS character_name, c.user_id AS owner_id "
        "FROM zone_runs z JOIN characters c ON c.id = z.character_id WHERE z.id=?",
        (run_id,)).fetchone()
    if run is None or (not is_admin(user) and run["owner_id"] != user["id"]):
        raise HTTPException(404, "no such zone run")
    return run


def _spark(conn, run_ids: list[int]) -> dict[int, list[int]]:
    """Raid DPS per fight, in fight order, for the home page's sparklines. One
    grouped query for every listed run — the shape of a night is cheap enough
    to ship with the list, and it is decoration next to numbers that are also
    written out."""
    if not run_ids:
        return {}
    ph = ",".join("?" * len(run_ids))
    out: dict[int, list[int]] = {}
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
    return out


@router.get("/zone-runs")
def list_zone_runs(user=Depends(require_user)):
    conn = get_db()
    where, params = ("", ()) if is_admin(user) else ("WHERE c.user_id = ?", (user["id"],))
    rows = conn.execute(
        "SELECT z.*, c.name AS character_name "
        "FROM zone_runs z JOIN characters c ON c.id = z.character_id "
        f"{where} ORDER BY z.started_ts DESC", params).fetchall()
    runs = rows_to_dicts(rows)
    spark = _spark(conn, [r["id"] for r in runs])
    joined = _merged_runs(conn, {r["character_id"] for r in runs})
    for r in runs:
        r["spark"] = spark.get(r["id"], [])
        # "this run only looks like one visit because you merged it" — the list
        # offers Unmerge exactly here
        r["merged"] = r["id"] in joined
    return {"zone_runs": runs}


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
def zone_run_detail(run_id: int, user=Depends(require_user)):
    conn = get_db()
    run = visible_zone_run(conn, user, run_id)
    return {"zone_run": row_to_dict(run),
            "encounters": rows_to_dicts(_run_encounters(conn, run))}


@router.get("/zone-runs/{run_id}/report")
def zone_run_report(run_id: int, user=Depends(require_user)):
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
    if not is_admin(user) and any(r["user_id"] != user["id"] for r in rows):
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
    if row is None or (not is_admin(user) and row["user_id"] != user["id"]):
        raise HTTPException(404, "no such character")
    _apply(conn, character_id, [], [(fp, "delete") for fp in fps])
    return {"restored": len(fps)}


@router.delete("/zone-runs/{run_id}")
def delete_zone_run(run_id: int, user=Depends(require_user)):
    """Delete a whole night: every fight in it, in one edit."""
    conn = get_db()
    run = visible_zone_run(conn, user, run_id)
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
    runs = sorted((visible_zone_run(conn, user, i) for i in ids),
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
    run = visible_zone_run(conn, user, run_id)
    fps = {encounter_fp(r) for r in conn.execute(
        "SELECT started_ts, zone, name FROM encounters WHERE zone_run_id=?", (run_id,))}
    _apply(conn, run["character_id"], [], [(fp, "join") for fp in fps])
    return {"unmerged": run_id}


@router.post("/zone-runs/{run_id}/split")
def split_zone_run(run_id: int, payload: dict = Body(...), user=Depends(require_user)):
    """Unmerge: the named fight starts a run of its own from here on."""
    conn = get_db()
    run = visible_zone_run(conn, user, run_id)
    enc = conn.execute(
        "SELECT started_ts, zone, name FROM encounters WHERE id=? AND zone_run_id=?",
        (int(payload.get("encounter_id") or 0), run_id)).fetchone()
    if enc is None:
        raise HTTPException(404, "no such fight in this run")
    fp = encounter_fp(enc)
    _apply(conn, run["character_id"], [(fp, "break")], [(fp, "join")])
    return {"split_at": fp}
