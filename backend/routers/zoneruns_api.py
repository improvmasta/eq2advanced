"""Zone runs: the primary navigation surface. A run is a contiguous zone visit
(pipeline/zoneruns.py); the list powers the date-grouped home page, detail
powers the zone page's encounter rail, and /report is the raid report scoped
to the run's canonical encounters (cross-session, deduped)."""

from fastapi import APIRouter, Depends, HTTPException

from db import get_db, row_to_dict, rows_to_dicts
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


@router.get("/zone-runs")
def list_zone_runs(user=Depends(require_user)):
    conn = get_db()
    where, params = ("", ()) if is_admin(user) else ("WHERE c.user_id = ?", (user["id"],))
    rows = conn.execute(
        "SELECT z.*, c.name AS character_name "
        "FROM zone_runs z JOIN characters c ON c.id = z.character_id "
        f"{where} ORDER BY z.started_ts DESC", params).fetchall()
    return {"zone_runs": rows_to_dicts(rows)}


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
    report = build_for_encounters(conn, encounters)
    report["zone_run_id"] = run_id
    report["character_name"] = run["character_name"]
    return report
