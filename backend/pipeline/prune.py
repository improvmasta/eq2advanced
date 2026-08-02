"""Events pruning (retention rule 2): typed events are by far the biggest
table and are only needed to GENERATE reports; rollups live forever and raw
gzips are the reprocessing safety net. After PRUNE_DAYS a session's raid
report is frozen into raid_reports, then its events are deleted and the
session is marked pruned. Pinned sessions (calibration auto-pins) are never
touched. Reparsing raw brings events back if a pruned session ever needs a
fresh report.
"""

import logging
import time

from coach.raidreport import build as build_raid_report
from db import json_dumps

log = logging.getLogger("prune")


def prune_once(conn, days: int) -> int:
    """Prune every ready, unpinned session that ended more than `days` ago.
    Returns how many sessions were pruned."""
    cutoff = int(time.time()) - days * 86400
    rows = conn.execute(
        "SELECT id FROM sessions WHERE status='ready' AND pinned=0 AND pruned=0 "
        "AND ended_ts IS NOT NULL AND ended_ts < ?", (cutoff,)).fetchall()
    n = 0
    for row in rows:
        sid = row["id"]
        try:
            report = build_raid_report(conn, sid)
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO raid_reports (session_id, generated_ts, "
                    "json) VALUES (?,?,?)", (sid, int(time.time()), json_dumps(report)))
                conn.execute("DELETE FROM events WHERE session_id=?", (sid,))
                conn.execute("UPDATE sessions SET pruned=1 WHERE id=?", (sid,))
            n += 1
        except Exception:
            log.exception("pruning session %s failed", sid)
    if n:
        log.info("pruned events for %d sessions (>%dd old)", n, days)
    return n
