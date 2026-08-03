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

Hand edits (`run_edits`) ride on top of all three. They are keyed by encounter
FINGERPRINT, not id, because a reparse drops and recreates every encounter row
— deleting a fight or splitting a run has to mean the same thing after the next
backfill as it did when you clicked it. Three kinds:
  delete — the fight is hidden everywhere (`encounters.deleted_ts` is the
           denormalized mark; this table stays the source of truth)
  break  — always start a new run at this fight (unmerge)
  join   — never start a new run at this fight (merge with the one before)
"""

import sqlite3
import time

import memo

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


def encounter_fp(e) -> str:
    """Reparse-stable identity for one fight: when it started, where, and what
    was fought. Matches the dedupe key (minus ended_ts) so every copy of a
    duplicated fight carries the same fingerprint and one delete covers them
    all."""
    return f"{e['started_ts']}|{e['zone'] or ''}|{e['name'] or ''}"


def load_edits(conn: sqlite3.Connection, character_id: int) -> dict[str, set[str]]:
    """-> {'delete': {fp, ...}, 'break': {...}, 'join': {...}}."""
    out: dict[str, set[str]] = {"delete": set(), "break": set(), "join": set()}
    for r in conn.execute(
            "SELECT fp, kind FROM run_edits WHERE character_id=?", (character_id,)):
        out.setdefault(r["kind"], set()).add(r["fp"])
    return out


def _segment(canonical: list[sqlite3.Row],
             edits: dict[str, set[str]] | None = None) -> list[list[sqlite3.Row]]:
    breaks = (edits or {}).get("break", set())
    joins = (edits or {}).get("join", set())
    runs: list[list[sqlite3.Row]] = []
    for e in canonical:
        prev = runs[-1][-1] if runs else None
        fp = encounter_fp(e)
        natural = (prev is None or e["zone"] != prev["zone"]
                   or e["started_ts"] - prev["ended_ts"] > ZONE_RUN_GAP_S)
        # a join can never promote the very first fight into a previous run
        if fp in breaks or (natural and not (fp in joins and prev is not None)):
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
    all_encs = conn.execute(
        "SELECT e.id, e.session_id, e.zone, e.name, e.is_named, e.started_ts, "
        "e.ended_ts, e.duration_s, e.success, e.zone_run_id, e.dup_of, e.deleted_ts, "
        "s.parse_version, COALESCE(s.ended_ts, 0) - COALESCE(s.started_ts, 0) AS coverage "
        "FROM encounters e JOIN sessions s ON s.id = e.session_id "
        "WHERE s.character_id = ? "
        "ORDER BY e.started_ts, e.ended_ts, e.session_id", (character_id,)).fetchall()

    # re-apply hand deletes first: the mark is derived, run_edits is the truth,
    # so a fight deleted before a reparse comes back deleted
    edits = load_edits(conn, character_id)
    now = int(time.time())
    for e in all_encs:
        want = now if encounter_fp(e) in edits["delete"] else None
        if (e["deleted_ts"] is None) != (want is None):
            conn.execute("UPDATE encounters SET deleted_ts=? WHERE id=?", (want, e["id"]))
    encs = [e for e in all_encs if encounter_fp(e) not in edits["delete"]]

    canonical, dup_of = _dedupe(encs)
    runs = _segment(canonical, edits)

    existing = conn.execute(
        "SELECT id, zone, started_ts, ended_ts FROM zone_runs "
        "WHERE character_id = ? ORDER BY started_ts", (character_id,)).fetchall()

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
            # named KILLS, not every success: trash carries a real success flag
            # now too, and "9/9 named" must mean nine bosses killed out of nine
            # engaged — which is only a meaningful ratio because a wipe can
            # finally be recorded as success=0
            sum(1 for e in members if e["is_named"] and e["success"] == 1),
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

    # deleted rows fall out of every run (run_id_of_enc/dup_of never name them)
    for e in all_encs:
        want_run = run_id_of_enc.get(e["id"])
        want_dup = dup_of.get(e["id"])
        if e["zone_run_id"] != want_run or e["dup_of"] != want_dup:
            conn.execute("UPDATE encounters SET zone_run_id=?, dup_of=? WHERE id=?",
                         (want_run, want_dup, e["id"]))

    # this function is the funnel every write ends in — uploads, live closes,
    # reparses, deletes and hand edits — so it is where cached read payloads
    # are thrown away (see memo.py)
    memo.invalidate()


def relink_all(conn: sqlite3.Connection) -> int:
    """Rebuild runs for every character that has encounters. -> characters seen."""
    chars = [r["character_id"] for r in conn.execute(
        "SELECT DISTINCT s.character_id AS character_id FROM sessions s "
        "JOIN encounters e ON e.session_id = s.id")]
    for cid in chars:
        with conn:
            rebuild_zone_runs(conn, cid)
    return len(chars)
