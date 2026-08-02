"""Coach + Raid Report endpoints (phase 5), plus the calibration flag.

Reports are per (character, session): GET returns the latest persisted report,
POST regenerates. The raid report is computed on demand — it is a view over
stored events, cheap enough to build per request."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from coach import advisor, raidreport
from db import get_db
from routers.sessions_api import visible_session
from security import require_user

router = APIRouter(tags=["coach"])


def _ready_session(conn, user, session_id: int):
    sess = visible_session(conn, user, session_id)
    if sess["status"] != "ready":
        raise HTTPException(409, f"session is {sess['status']}, not ready")
    return sess


@router.get("/sessions/{session_id}/raid-report")
def get_raid_report(session_id: int, user=Depends(require_user)):
    import json as _json

    conn = get_db()
    sess = _ready_session(conn, user, session_id)
    if sess["pruned"]:
        row = conn.execute("SELECT json FROM raid_reports WHERE session_id=?",
                           (session_id,)).fetchone()
        if row is None:
            raise HTTPException(410, "session events were pruned and no frozen "
                                     "raid report exists")
        report = _json.loads(row["json"])
        report["frozen"] = True
    else:
        report = raidreport.build(conn, session_id)
    report["character_name"] = sess["character_name"]
    return report


@router.get("/sessions/{session_id}/coach")
def get_coach(session_id: int, user=Depends(require_user)):
    conn = get_db()
    visible_session(conn, user, session_id)
    return {"report": advisor.latest(conn, session_id)}


@router.post("/sessions/{session_id}/coach")
def generate_coach(session_id: int, user=Depends(require_user)):
    conn = get_db()
    sess = _ready_session(conn, user, session_id)
    if sess["pruned"]:
        raise HTTPException(409, "session events were pruned — the persisted "
                                 "coach report is final (GET returns it)")
    char = conn.execute("SELECT * FROM characters WHERE id=?",
                        (sess["character_id"],)).fetchone()
    report = advisor.generate(conn, char, session_id)
    advisor.persist(conn, report)
    return {"report": report}


class CalibrationSet(BaseModel):
    calibration: bool


@router.post("/sessions/{session_id}/calibration")
def set_calibration(session_id: int, body: CalibrationSet,
                    user=Depends(require_user)):
    """Mark a session as a dummy-parse calibration run (ground truth for the
    fit). Calibration auto-pins so the events pruning job never eats it, and
    captures the character's CURRENT stat vector — two-point cap solving needs
    the abmod each dummy parse actually ran at."""
    from census.sync import _snapshot_doc
    from coach.fit import snapshot_stats
    from db import json_dumps

    conn = get_db()
    sess = visible_session(conn, user, session_id)
    stats = None
    with conn:
        if body.calibration:
            _, doc = _snapshot_doc(conn, sess["character_id"])
            stats = snapshot_stats(doc) if doc else None
            conn.execute(
                "UPDATE sessions SET calibration=1, pinned=1, calib_stats_json=? "
                "WHERE id=?",
                (json_dumps(stats) if stats else None, session_id))
        else:
            conn.execute("UPDATE sessions SET calibration=0 WHERE id=?",
                         (session_id,))
    return {"ok": True, "calibration": body.calibration, "captured_stats": stats}
