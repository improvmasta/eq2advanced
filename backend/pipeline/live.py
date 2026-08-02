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
import threading
import time
from pathlib import Path

from db import RAW_DIR, get_db, json_dumps
from parser import parse_lines
from parser.prefix import split_prefix
from pipeline.encounters import GAP_S, TRAIL_GRACE_S, segment_events
from pipeline.ingest_writer import EntityResolver, _resolve_events, parse_session
from pipeline.statsroll import ACTOR_INSERT, actor_rows, roll_encounter

LIVE_IDLE_S = 30 * 60        # receiving session quiet this long -> close it, next batch starts fresh
CLOSE_S = GAP_S + TRAIL_GRACE_S  # nothing can join a segment once this much log time has passed


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


_states: dict[int, LiveState] = {}
_states_lock = threading.Lock()


def _get_state(conn, session_id: int, logger: str) -> LiveState:
    with _states_lock:
        state = _states.get(session_id)
        if state is None:
            state = LiveState(session_id, logger)
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
            _states[session_id] = state
    return state


def drop_state(session_id: int) -> None:
    with _states_lock:
        _states.pop(session_id, None)


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


def process_batch(token_row, batch_id: str, mode: str, lines: list[str]) -> dict:
    """One ingest batch, called with the per-token lock held. token_row is the
    character row (+token_id) from device_token_character."""
    conn = get_db()
    now = int(time.time())
    replay = conn.execute(
        "SELECT session_id, accepted, duplicates FROM ingest_batches "
        "WHERE token_id=? AND batch_id=?", (token_row["token_id"], batch_id)).fetchone()
    if replay is not None:
        return {"accepted": replay["accepted"], "duplicates": replay["duplicates"],
                "session_id": replay["session_id"], "replayed": True}

    logger = token_row["name"]
    session_id = open_live_session(conn, token_row["id"], logger)
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
            chunk_dir = RAW_DIR / f"session-{session_id}"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            path = chunk_dir / f"{state.chunk_seq:06d}.txt.gz"
            with gzip.open(path, "wt", encoding="utf-8") as out:
                out.write("\r\n".join(accepted) + "\r\n")
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
            state.pending.extend(parse_lines(iter(accepted), logger))

        _flush(conn, state)

        conn.execute(
            "UPDATE sessions SET line_count = COALESCE(line_count,0) + ?, last_ingest_ts=? "
            "WHERE id=?", (len(accepted), now, session_id))
        conn.execute(
            "INSERT INTO ingest_batches (token_id, batch_id, session_id, accepted, "
            "duplicates, created_ts) VALUES (?,?,?,?,?,?)",
            (token_row["token_id"], batch_id, session_id, len(accepted), duplicates, now))

    return {"accepted": len(accepted), "duplicates": duplicates, "session_id": session_id}


def _flush(conn, state: LiveState, force: bool = False) -> None:
    """Write out every event that can no longer change segment: all events in
    closed segments, plus segment-less events older than CLOSE_S. The still-hot
    tail stays in memory for the next batch."""
    events = state.pending
    if not events:
        return
    latest = max(state.last_line_ts or 0, events[-1].ts)
    segs = segment_events(events, state.logger, initial_zone=state.zone)

    n_closed = len(segs)
    if not force and segs and latest - segs[-1].end_ts < CLOSE_S:
        n_closed -= 1
    closed, open_seg = segs[:n_closed], segs[n_closed] if n_closed < len(segs) else None
    open_first = open_seg.event_indices[0] if open_seg else None

    enc_of_idx: dict[int, int] = {}          # event index -> encounter id
    seg_rows = []
    for seg in closed:
        cur = conn.execute(
            "INSERT INTO encounters (session_id, zone, name, is_named, started_ts, "
            "ended_ts, duration_s, success) VALUES (?,?,?,?,?,?,?,?)",
            (state.session_id, seg.zone, seg.name, int(seg.is_named), seg.start_ts,
             seg.end_ts, max(seg.end_ts - seg.start_ts, 1), seg.success))
        enc_id = cur.lastrowid
        seg_rows.append((seg, enc_id))
        for i in seg.event_indices:
            enc_of_idx[i] = enc_id

    flush_ts = latest - CLOSE_S
    flush_idx = [
        i for i, ev in enumerate(events)
        if i in enc_of_idx
        or ((open_first is None or i < open_first)
            and (force or ev.ts <= flush_ts))
    ]
    if not flush_idx and not closed:
        return

    res = EntityResolver(conn, state.session_id, state.logger)
    flushed = [events[i] for i in flush_idx]
    resolved = _resolve_events(flushed, res)
    pos = {orig: k for k, orig in enumerate(flush_idx)}

    for seg, enc_id in seg_rows:
        seg_events = [resolved[pos[i]] for i in seg.event_indices]
        actor_stats, ability_stats = roll_encounter(
            seg_events, max(seg.end_ts - seg.start_ts, 1))
        conn.executemany(ACTOR_INSERT, actor_rows(enc_id, actor_stats))
        conn.executemany(
            "INSERT INTO encounter_ability_stats (encounter_id, entity_id, ability_id, "
            "kind, casts, hits, crits, misses, resists, total, min, max) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(enc_id, src, res.ability_id(name), kind, st["casts"], st["hits"],
              st["crits"], st["misses"], st["resists"], st["total"], st["min"], st["max"])
             for (src, name, kind), st in ability_stats.items()])

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
