"""Zone runs: the organizing entity above sessions. A run is a contiguous
visit to one zone by one character, derived entirely from encounter rows —
sessions (files) stay the ingest unit, runs are what the UI navigates.

`rebuild_zone_runs` is idempotent and deterministic; it owns three concerns:

1. Content dedupe — overlapping uploaded files produce byte-identical
   encounters (segmentation is deterministic per parse_version, so identical
   log bytes yield identical (started_ts, ended_ts, zone, name)). Duplicates
   are MARKED (`encounters.dup_of` -> canonical id), never deleted, so every
   parse stays complete and the marking re-derives after any reparse. Only
   sessions at the same parse_version dedupe against each other — mid-sweep,
   differing versions may segment differently; the post-sweep relink converges.

2. Segmentation — canonical encounters in time order split into runs on a
   zone change or an idle gap > ZONE_RUN_GAP_S. Encounters before any zone
   line (zone IS NULL) form their own "Unknown zone" runs.

3. Id-preserving upsert — a recomputed run reuses the existing row's id when
   zone matches and the time windows overlap, so /zones/:id URLs survive
   reparses and backfills that extend a run.
"""

import sqlite3
import time

# Split a same-zone encounter stream when combat pauses this long. Observed
# real idle gaps (zoned-out overnight etc.) are >= 70 min; real raid breaks
# stay well under ~35 min.
ZONE_RUN_GAP_S = 3600


def _dedupe(encs: list[sqlite3.Row]) -> tuple[list[sqlite3.Row], dict[int, int]]:
    """-> (canonical encounters in time order, {dup encounter id: canonical id})."""
    groups: dict[tuple, list[sqlite3.Row]] = {}
    for e in encs:
        key = (e["started_ts"], e["ended_ts"], e["zone"] or "", e["name"] or "")
        groups.setdefault(key, []).append(e)

    canonical: list[sqlite3.Row] = []
    dup_of: dict[int, int] = {}
    for members in groups.values():
        by_version: dict[int | None, list[sqlite3.Row]] = {}
        for e in members:
            by_version.setdefault(e["parse_version"], []).append(e)
        for rows in by_version.values():
            # widest raw coverage wins (the superset file); tie -> lowest session
            rows.sort(key=lambda e: (-e["coverage"], e["session_id"]))
            winner = rows[0]
            canonical.append(winner)
            for loser in rows[1:]:
                dup_of[loser["id"]] = winner["id"]
    canonical.sort(key=lambda e: (e["started_ts"], e["ended_ts"], e["session_id"]))
    return canonical, dup_of


def _segment(canonical: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    runs: list[list[sqlite3.Row]] = []
    for e in canonical:
        prev = runs[-1][-1] if runs else None
        if (prev is None or e["zone"] != prev["zone"]
                or e["started_ts"] - prev["ended_ts"] > ZONE_RUN_GAP_S):
            runs.append([e])
        else:
            runs[-1].append(e)
    return runs


def _raider_count(conn: sqlite3.Connection, enc_ids: list[int]) -> int | None:
    if not enc_ids:
        return None
    ph = ",".join("?" * len(enc_ids))
    row = conn.execute(
        f"SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM encounter_actor_stats eas "
        f"JOIN entities en ON en.id = eas.entity_id "
        f"WHERE eas.encounter_id IN ({ph}) AND en.kind='player' "
        f"GROUP BY eas.encounter_id)", enc_ids).fetchone()
    return row[0]


def rebuild_zone_runs(conn: sqlite3.Connection, character_id: int) -> None:
    """Recompute dedupe marks, run segmentation, and run rollups for one
    character. Caller owns the transaction."""
    encs = conn.execute(
        "SELECT e.id, e.session_id, e.zone, e.name, e.is_named, e.started_ts, "
        "e.ended_ts, e.duration_s, e.success, e.zone_run_id, e.dup_of, "
        "s.parse_version, COALESCE(s.ended_ts, 0) - COALESCE(s.started_ts, 0) AS coverage "
        "FROM encounters e JOIN sessions s ON s.id = e.session_id "
        "WHERE s.character_id = ? "
        "ORDER BY e.started_ts, e.ended_ts, e.session_id", (character_id,)).fetchall()

    canonical, dup_of = _dedupe(encs)
    runs = _segment(canonical)

    existing = conn.execute(
        "SELECT id, zone, started_ts, ended_ts FROM zone_runs "
        "WHERE character_id = ? ORDER BY started_ts", (character_id,)).fetchall()

    now = int(time.time())
    consumed: set[int] = set()
    run_id_of_enc: dict[int, int] = {}
    for members in runs:
        zone = members[0]["zone"]
        start, end = members[0]["started_ts"], members[-1]["ended_ts"]
        match = next(
            (ex for ex in existing
             if ex["id"] not in consumed and ex["zone"] == zone
             and start <= ex["ended_ts"] and end >= ex["started_ts"]), None)
        enc_ids = [e["id"] for e in members]
        fields = (
            zone, start, end, len(members),
            sum(1 for e in members if e["is_named"]),
            sum(1 for e in members if e["success"] == 1),
            sum(e["duration_s"] for e in members),
            _raider_count(conn, enc_ids), now)
        if match is not None:
            consumed.add(match["id"])
            run_id = match["id"]
            conn.execute(
                "UPDATE zone_runs SET zone=?, started_ts=?, ended_ts=?, "
                "encounter_count=?, named_count=?, success_count=?, combat_s=?, "
                "raider_count=?, updated_ts=? WHERE id=?", fields + (run_id,))
        else:
            run_id = conn.execute(
                "INSERT INTO zone_runs (character_id, zone, started_ts, ended_ts, "
                "encounter_count, named_count, success_count, combat_s, raider_count, "
                "updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (character_id,) + fields).lastrowid
        for eid in enc_ids:
            run_id_of_enc[eid] = run_id

    stale = [ex["id"] for ex in existing if ex["id"] not in consumed]
    if stale:
        ph = ",".join("?" * len(stale))
        conn.execute(f"DELETE FROM zone_runs WHERE id IN ({ph})", stale)

    for e in encs:
        want_run = run_id_of_enc.get(e["id"])
        want_dup = dup_of.get(e["id"])
        if e["zone_run_id"] != want_run or e["dup_of"] != want_dup:
            conn.execute("UPDATE encounters SET zone_run_id=?, dup_of=? WHERE id=?",
                         (want_run, want_dup, e["id"]))


def relink_all(conn: sqlite3.Connection) -> int:
    """Rebuild runs for every character that has encounters. -> characters seen."""
    chars = [r["character_id"] for r in conn.execute(
        "SELECT DISTINCT s.character_id AS character_id FROM sessions s "
        "JOIN encounters e ON e.session_id = s.id")]
    for cid in chars:
        with conn:
            rebuild_zone_runs(conn, cid)
    return len(chars)
