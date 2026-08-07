"""Live ingest pipeline (phase 3). Batches of raw lines arrive from a device
token; each batch is line-deduped, stored to a raw chunk file, parsed, and the
pending event tail is incrementally segmented. Encounters that can no longer
change (a newer segment started, or GAP+grace has elapsed) are finalized to the
same tables the bulk path writes, so the Live page shows fight cards seconds
after each kill.

The incremental rows are a VIEW, not the record: at session close (backfill
done, or staleness) the session is rebuilt from its raw chunks through
`parse_session` — the exact bulk code path — so a finished live session is
byte-for-byte equivalent to uploading the same file.
"""

import gzip
import hashlib
import json
import logging
import threading
import time
from pathlib import Path

from db import RAW_DIR, get_db, json_dumps
from parser import parse_lines, petnames
from parser.prefix import split_prefix
from pipeline.encounters import (GAP_S, TRAIL_GRACE_S, encounter_label,
                                 segment_events, split_trailing_corpse)
from pipeline import livemeter
from pipeline.ingest_writer import EntityResolver, _resolve_events, parse_session
from pipeline.redact import keep_line
from pipeline.statsroll import (ABILITY_INSERT, ACTOR_INSERT, ability_rows,
                                actor_rows, roll_encounter)

LIVE_IDLE_S = 30 * 60        # receiving session quiet this long -> close it (reaped, or on the next batch)
CLOSE_S = GAP_S + TRAIL_GRACE_S  # nothing can join a segment once this much log time has passed
WATCH_TTL_S = 30             # a dashboard asks again every stream poll; this outlives one gap
LIVE_LAG_S = 120             # log time this far behind the clock is history, not a raid in progress
SNAPSHOT_MIN_S = 1.0         # floor between rebuilds; the stream polls slower than this anyway


class LiveState:
    """In-memory tail for one receiving session. Lost on restart — harmless,
    because the close-time rebuild reparses everything from the raw chunks."""

    def __init__(self, session_id: int, logger: str):
        self.lock = threading.Lock()   # two device tokens can hit one session
        self.session_id = session_id
        self.logger = logger
        self.pending = []            # parsed, not yet flushed events (log order)
        self.zone: str | None = None  # zone in effect before pending[0]
        self.seq_base = 0
        self.chunk_seq = 0
        self.last_line_ts: int | None = None
        # named-pet knowledge at session start; no prescan/refine live (the
        # close-time rebuild through parse_session applies both to everything)
        self.pet_names: frozenset[str] = frozenset()
        # --- the dashboard's in-flight view (pipeline/livemeter.py) ----------
        # The open segment as `_flush` last saw it: the fight in progress, held
        # as event REFERENCES rather than indices into `pending`, because the
        # flush that computes it also renumbers that list.
        self.open_events: list = []
        self.open_start_ts: int | None = None
        self.open_zone: str | None = None
        self.roster: dict[str, str] = {}   # name_lower -> class, for the bars
        # Replaced whole, never mutated: the SSE generators read this attribute
        # from the event loop while a batch is being processed in a worker
        # thread, and swapping one reference is the only thing they can observe
        # atomically. Nobody who holds a snapshot may edit it.
        self.snapshot: dict | None = None
        self.watch_until = 0.0             # nobody watching -> nothing computed


_states: dict[int, LiveState] = {}
_states_lock = threading.Lock()


def _get_state(conn, session_id: int, logger: str) -> LiveState:
    with _states_lock:
        state = _states.get(session_id)
        if state is None:
            state = LiveState(session_id, logger)
            state.pet_names = petnames.load(conn)
            row = conn.execute(
                "SELECT COALESCE(MAX(seq)+1, 0) AS seq, "
                "(SELECT COALESCE(MAX(seq)+1, 0) FROM raw_chunks WHERE session_id=?) AS chunk "
                "FROM events WHERE session_id=?", (session_id, session_id)).fetchone()
            state.seq_base, state.chunk_seq = row["seq"], row["chunk"]
            zrow = conn.execute(
                "SELECT extra FROM events WHERE session_id=? AND type='zone' "
                "ORDER BY ts DESC, seq DESC LIMIT 1", (session_id,)).fetchone()
            if zrow and zrow["extra"]:
                state.zone = json.loads(zrow["extra"]).get("zone")
            from census.roster import known_classes
            state.roster = known_classes(conn)
            _states[session_id] = state
    return state


def drop_state(session_id: int) -> None:
    with _states_lock:
        _states.pop(session_id, None)


def mark_watched(session_id: int, ttl_s: int = WATCH_TTL_S) -> None:
    """A dashboard is open on this session — keep building snapshots for the
    next `ttl_s` seconds. Snapshots cost a pass over the open fight per batch,
    so a raid nobody is watching pays nothing."""
    with _states_lock:
        state = _states.get(session_id)
    if state is not None:
        state.watch_until = time.time() + ttl_s


def live_snapshot(session_id: int) -> dict | None:
    """The last in-flight view of this session, or None if there is no live
    state (never started, or the process restarted). Treat the result as
    FROZEN — it is the same dict the producer published."""
    with _states_lock:
        state = _states.get(session_id)
    return state.snapshot if state is not None else None


def open_live_session(conn, character_id: int, logger: str) -> int:
    """The character's current receiving live session; a stale one is closed
    (rebuilt from raw) and a fresh one created."""
    now = int(time.time())
    row = conn.execute(
        "SELECT id, COALESCE(last_ingest_ts, created_ts) AS seen FROM sessions "
        "WHERE character_id=? AND source='live' AND status='receiving' "
        "ORDER BY id DESC LIMIT 1", (character_id,)).fetchone()
    if row is not None:
        if now - row["seen"] <= LIVE_IDLE_S:
            return row["id"]
        finalize_live_session(row["id"])
    with conn:
        return conn.execute(
            "INSERT INTO sessions (character_id, source, status, created_ts, last_ingest_ts) "
            "VALUES (?, 'live', 'receiving', ?, ?)", (character_id, now, now)).lastrowid


def reap_idle_live_sessions(conn) -> list[int]:
    """Close every `receiving` session that has gone quiet.

    `open_live_session` only closes a stale session when the NEXT batch for that
    character arrives, and `/backfill/done` only fires when the plugin says so.
    Quit EQ2 and ACT and neither ever happens: the session sits at 'receiving'
    forever, the raid page keeps saying Live, and — because it is never rebuilt
    from raw — no parser improvement can reach it either. This is the path that
    does not depend on the client ever coming back.

    Returns the session ids it closed. Rebuilding is the expensive part, so it
    happens outside the row query.
    """
    cutoff = int(time.time()) - LIVE_IDLE_S
    rows = conn.execute(
        "SELECT id FROM sessions WHERE source='live' AND status='receiving' "
        "AND COALESCE(last_ingest_ts, created_ts) < ? ORDER BY id", (cutoff,)).fetchall()
    closed = []
    for row in rows:
        try:
            finalize_live_session(row["id"])
            closed.append(row["id"])
        except Exception:
            logging.getLogger("live").exception(
                "finalizing idle live session %d failed", row["id"])
    return closed


def finalize_live_session(session_id: int) -> None:
    """Close a live session: rebuild it from its raw chunks through the bulk
    parse path (clears the incremental rows), ending at status 'ready'."""
    drop_state(session_id)
    conn = get_db()
    paths = [Path(r["path"]) for r in conn.execute(
        "SELECT path FROM raw_chunks WHERE session_id=? ORDER BY seq", (session_id,))]
    parse_session(session_id, paths)


def _line_key(line: str, ordinal: int) -> bytes:
    return hashlib.sha256(f"{ordinal}:{line}".encode()).digest()[:16]


def process_batch(token_row, char, batch_id: str, mode: str, lines: list[str]) -> dict:
    """One ingest batch, called with the per-token lock held.

    `token_row` is the device token (account + token_id); `char` is the
    character row this batch belongs to, resolved by the router from the name
    the plugin read off the log. They are separate because one token uploads for
    every character on the account — switching alts mid-evening just lands in a
    different session."""
    conn = get_db()
    now = int(time.time())
    replay = conn.execute(
        "SELECT session_id, accepted, duplicates FROM ingest_batches "
        "WHERE token_id=? AND batch_id=?", (token_row["token_id"], batch_id)).fetchone()
    if replay is not None:
        return {"accepted": replay["accepted"], "duplicates": replay["duplicates"],
                "session_id": replay["session_id"], "replayed": True}

    logger = char["name"]
    session_id = open_live_session(conn, char["id"], logger)
    state = _get_state(conn, session_id, logger)

    with state.lock, conn:
        # line-level dedupe: key = (occurrence ordinal within this batch, line).
        # A backfill/upload overlapping what live already sent carries the same
        # per-second occurrence ordinals, so overlaps drop cleanly; legitimate
        # identical lines inside one batch get distinct ordinals and both count.
        accepted: list[str] = []
        duplicates = 0
        seen_in_batch: dict[str, int] = {}
        for raw in lines:
            line = raw.rstrip("\r\n")
            n = seen_in_batch.get(line, 0)
            seen_in_batch[line] = n + 1
            cur = conn.execute(
                "INSERT INTO ingest_lines (session_id, line_key) VALUES (?,?) "
                "ON CONFLICT DO NOTHING", (session_id, _line_key(line, n)))
            if cur.rowcount:
                accepted.append(line)
            else:
                duplicates += 1

        if accepted:
            # Only redacted lines are STORED; dedupe keys, the batch's time bounds
            # and the parse below all run on what actually arrived. That keeps the
            # rebuild-from-raw at session close identical to the live parse — chat
            # produces no events either way (see pipeline/redact.py).
            stored = [line for line in accepted if keep_line(line)]
            dropped_private = len(accepted) - len(stored)
            if dropped_private:
                conn.execute(
                    "UPDATE sessions SET redacted_lines = redacted_lines + ? WHERE id=?",
                    (dropped_private, session_id))
            chunk_dir = RAW_DIR / f"session-{session_id}"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            path = chunk_dir / f"{state.chunk_seq:06d}.txt.gz"
            with gzip.open(path, "wt", encoding="utf-8") as out:
                out.write("\r\n".join(stored) + "\r\n" if stored else "")
            first = split_prefix(accepted[0])
            last = split_prefix(accepted[-1])
            conn.execute(
                "INSERT INTO raw_chunks (session_id, seq, path, first_ts, last_ts) "
                "VALUES (?,?,?,?,?)",
                (session_id, state.chunk_seq, str(path),
                 first[0] if first else None, last[0] if last else None))
            state.chunk_seq += 1
            if last:
                state.last_line_ts = max(state.last_line_ts or 0, last[0])
            state.pending.extend(parse_lines(iter(accepted), logger, state.pet_names))

        if _flush(conn, state):
            from pipeline.zoneruns import rebuild_zone_runs
            # the CHARACTER's runs, not the token's — `token_row` used to be the
            # character row and this read `["id"]` off it
            rebuild_zone_runs(conn, char["id"])

        _publish_snapshot(state, mode, now)

        conn.execute(
            "UPDATE sessions SET line_count = COALESCE(line_count,0) + ?, last_ingest_ts=? "
            "WHERE id=?", (len(accepted), now, session_id))
        conn.execute(
            "INSERT INTO ingest_batches (token_id, batch_id, session_id, accepted, "
            "duplicates, created_ts) VALUES (?,?,?,?,?,?)",
            (token_row["token_id"], batch_id, session_id, len(accepted), duplicates, now))

    return {"accepted": len(accepted), "duplicates": duplicates, "session_id": session_id}


def _publish_snapshot(state: LiveState, mode: str, now: int) -> None:
    """Recompute the dashboard's in-flight view, if anyone is looking.

    Two gates, both about not showing a raid that is not happening. `mode` is
    the plugin's own word for it: a backfill batch is an old log being caught
    up, and a night from March must not flash on screen as a pull in progress.
    `LIVE_LAG_S` catches the same thing from the other side — a live-mode
    client replaying history, which is what `tools/simulate_live.py` does.

    Nothing here writes to the database, and the result is reachable only
    through `live_snapshot`. That is deliberate: the incremental rows have to
    stay identical to what uploading the same file produces
    (tests/test_ingest.py::test_golden_equivalence), so the fight in progress
    gets a picture, never a record.
    """
    wall = time.time()
    if wall >= state.watch_until:
        state.snapshot = None
        return
    stale = mode != "live" or (state.last_line_ts is not None
                               and now - state.last_line_ts > LIVE_LAG_S)
    if stale:
        state.snapshot = None
        return
    # The plugin sends every ~2s and the biggest measured fight (46k events,
    # nearly 7 minutes) rebuilds in 65ms, so this never fires in a raid. It is
    # here for a client sending far faster than that, where rebuilding per
    # batch would be pure waste — the stream cannot show more than it polls.
    if state.snapshot is not None and wall - state.snapshot["computed_ts"] < SNAPSHOT_MIN_S:
        return
    try:
        state.snapshot = livemeter.snapshot_payload(
            state.open_events, state.logger, state.open_zone,
            state.open_start_ts, state.roster)
    except Exception:
        # a view is never worth failing an ingest batch over
        logging.getLogger("live").exception(
            "live snapshot failed for session %d", state.session_id)
        state.snapshot = None


def _flush(conn, state: LiveState, force: bool = False) -> bool:
    """Write out every event that can no longer change segment: all events in
    closed segments, plus segment-less events older than CLOSE_S. The still-hot
    tail stays in memory for the next batch. -> True when encounters landed."""
    events = state.pending
    if not events:
        state.open_events, state.open_start_ts, state.open_zone = [], None, None
        return False
    latest = max(state.last_line_ts or 0, events[-1].ts)
    segs = segment_events(events, state.logger, initial_zone=state.zone)

    n_closed = len(segs)
    if not force and segs and latest - segs[-1].end_ts < CLOSE_S:
        n_closed -= 1
    closed, open_seg = segs[:n_closed], segs[n_closed] if n_closed < len(segs) else None
    open_first = open_seg.event_indices[0] if open_seg else None

    # The fight in progress, for the dashboard (pipeline/livemeter.py). Kept as
    # references because `pending` is rebuilt at the end of this function, and
    # published here rather than recomputed later so the segmentation the view
    # shows is the one the writer just made.
    state.open_events = [events[i] for i in open_seg.event_indices] if open_seg else []
    state.open_start_ts = open_seg.start_ts if open_seg else None
    state.open_zone = open_seg.zone if open_seg else None

    enc_of_idx: dict[int, int] = {}          # event index -> encounter id
    for seg in closed:
        for i in seg.event_indices:
            enc_of_idx[i] = -1               # placeholder: ids assigned below

    flush_ts = latest - CLOSE_S
    flush_idx = [
        i for i, ev in enumerate(events)
        if i in enc_of_idx
        or ((open_first is None or i < open_first)
            and (force or ev.ts <= flush_ts))
    ]
    if not flush_idx and not closed:
        return False

    res = EntityResolver(conn, state.session_id, state.logger, state.pet_names)
    flushed = [events[i] for i in flush_idx]
    resolved = _resolve_events(flushed, res)
    pos = {orig: k for k, orig in enumerate(flush_idx)}

    # a closed segment can still be carrying a dead mob's last ticks; only
    # closed ones, since the open segment may yet grow (pipeline.encounters)
    closed = [
        piece for seg in closed
        for piece in split_trailing_corpse(
            seg, [resolved[pos[i]] for i in seg.event_indices])
    ]

    # encounters are inserted AFTER resolution: naming them after the enemy
    # fought needs to know which target is a mob (pipeline.encounters)
    seg_rows = []
    for seg in closed:
        seg_events = [resolved[pos[i]] for i in seg.event_indices]
        name, is_named, success = encounter_label(
            seg_events, res.name_of, state.logger)
        cur = conn.execute(
            "INSERT INTO encounters (session_id, zone, name, is_named, started_ts, "
            "ended_ts, duration_s, success) VALUES (?,?,?,?,?,?,?,?)",
            (state.session_id, seg.zone, name, int(is_named), seg.start_ts,
             seg.end_ts, max(seg.end_ts - seg.start_ts, 1), success))
        enc_id = cur.lastrowid
        seg_rows.append((seg, enc_id))
        for i in seg.event_indices:
            enc_of_idx[i] = enc_id

    from census.catalog import press_inputs
    periods, proc_names = press_inputs(conn)
    for seg, enc_id in seg_rows:
        seg_events = [resolved[pos[i]] for i in seg.event_indices]
        actor_stats, ability_stats = roll_encounter(
            seg_events, max(seg.end_ts - seg.start_ts, 1), periods, proc_names)
        conn.executemany(ACTOR_INSERT, actor_rows(enc_id, actor_stats))
        conn.executemany(
            ABILITY_INSERT, ability_rows(enc_id, ability_stats, res.ability_id))

    conn.executemany(
        "INSERT INTO events (session_id, encounter_id, ts, seq, type, src_entity, "
        "tgt_entity, ability_id, amount, dtype, flags, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(state.session_id, enc_of_idx.get(orig), r["ts"], state.seq_base + k, r["type"],
          r["src_entity"], r["tgt_entity"],
          res.ability_id(r["ability"]) if r["ability"] else None,
          r["amount"], r["dtype"], r["flags"],
          json_dumps(r["extra"]) if r["extra"] else None)
         for k, (orig, r) in enumerate(zip(flush_idx, resolved))])
    state.seq_base += len(flush_idx)

    for ev in flushed:
        if ev.type == "zone":
            state.zone = ev.extra.get("zone")

    conn.execute(
        "UPDATE sessions SET started_ts = COALESCE(started_ts, ?), ended_ts = ? WHERE id=?",
        (flushed[0].ts, flushed[-1].ts, state.session_id))

    keep = set(flush_idx)
    state.pending = [ev for i, ev in enumerate(events) if i not in keep]
    return bool(closed)
