"""Playing a recorded fight back into the live meter.

The dashboard's hardest surface to work on is the one that only exists during
a raid. `partial` events are built from an open segment held in memory, so
tuning a bar, a countdown, or the empty state between pulls meant waiting for
the next raid night and then getting one pass at it.

A replay is that same picture, taken from a fight that already happened. It
reads the encounter's RAW LINES back off disk and parses them with the same
`parse_lines` the live path calls, then walks a cursor through the result in
wall-clock time. What the page receives is the shape a real raid sends, at the
cadence a real raid sends it — because it IS that code, fed from a file
instead of a socket.

It writes NOTHING: no session, no encounter, no rows, and it never touches
`LiveState`. A replay is a second reader of `pipeline/livemeter.py`, which is
already defined as a view over events nobody stores. That is also why it can
be pointed at any fight in the back catalogue without consequences — the worst
a bad replay can do is draw a bad picture.

`backend/tools/simulate_live.py` is the other half of this idea and stays: it
pushes a log through the real ingest endpoint, which parses, dedupes, writes
chunks and creates a session. Use that one to test INGEST. Use this one to
test the SCREEN.

Who may run one is two questions, deliberately kept apart. `require_curator`
(admin implies curator) gates the TOOL — a developer control does not belong
in everybody's dashboard. `visible_encounters` gates the FIGHT, exactly as
every other read does, so replaying is never a way to see a raid you could not
already open. Widening the second along with the first is the mistake this
comment exists to prevent.
"""

import asyncio
import json
import time
from bisect import bisect_right
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from db import get_db
from parser import parse_lines, petnames
from parser.prefix import split_prefix
from pipeline import livemeter, replaybus
from pipeline.live import snapshot_context
from pipeline.refine import roster_prescan
# `_iter_lines` is private by convention and shared here rather than copied:
# a second gzip-and-decode loop is a second place for the encoding argument to
# drift, and this one is what the parser itself reads through.
from pipeline.ingest_writer import _iter_lines, session_raw_paths
from security import require_curator, visible_encounters

router = APIRouter()

# The plugin's own send cadence (`Settings.CadenceSeconds`). A replay that
# refreshed faster than that would be a smoother meter than any raid can
# produce, and the point of this is to tune against what really arrives.
TICK_S = 2.0
MIN_SPEED, MAX_SPEED = 0.25, 8.0
# How far past the fight to keep reading before calling the rest of the night
# irrelevant. An EQ2 log is written in order, so this only has to cover jitter
# — but the failure mode of being wrong is a SILENTLY short replay, so the
# margin is generous rather than tight.
TAIL_GRACE_S = 300


def _raw_paths(conn, session_id: int, start_ts: int, end_ts: int) -> list[Path]:
    """Only the raw the fight can be inside.

    A live session is stored as a chunk per ingest batch, each with its own
    time bounds, so the fourth pull of the night does not have to decompress
    the three hours in front of it — the difference on a real raid was six
    seconds of staring at nothing. An upload is one file and has no such
    shortcut; the fallback is the whole session, which is also what a session
    whose chunks have been cleaned up gets.
    """
    rows = conn.execute(
        "SELECT path FROM raw_chunks WHERE session_id=? "
        "AND (last_ts IS NULL OR last_ts >= ?) AND (first_ts IS NULL OR first_ts <= ?) "
        "ORDER BY seq", (session_id, start_ts, end_ts + TAIL_GRACE_S)).fetchall()
    paths = [Path(r["path"]) for r in rows if Path(r["path"]).exists()]
    return paths or session_raw_paths(conn, session_id)


def _load_fight(session_id: int, logger: str, start_ts: int, end_ts: int):
    """The fight's raw lines, parsed the way a live batch is parsed.

    Runs in a worker thread and opens its own connection (`get_db` is
    thread-local). Returns `(events, proven_players)`, or None when the raw
    source is gone — a session can be uploaded parse-only, or have had its log
    dropped, and stats alone cannot be replayed. The second half is what a live
    session accumulates over the night (`live.LiveState.proven_players`); one
    fight's worth of lines is less evidence, which is the honest amount a
    replay of one fight has.
    """
    conn = get_db()
    paths = _raw_paths(conn, session_id, start_ts, end_ts)
    if not paths:
        return None
    lines, entered = [], False
    for raw in _iter_lines(paths):
        stamped = split_prefix(raw)
        if not stamped:
            continue
        ts = stamped[0]
        if ts < start_ts:
            continue
        if ts > end_ts:
            # an EQ2 log is chronological, so the rest of the night is not this
            # fight; stopping keeps a replay off a whole 60 MB read
            if entered and ts > end_ts + TAIL_GRACE_S:
                break
            continue
        entered = True
        lines.append(raw.rstrip("\r\n"))
    # the same pet knowledge the live path starts a session with.
    # `parse_lines` is a generator; the cursor walks the result repeatedly, so
    # it has to be a sequence
    return (list(parse_lines(iter(lines), logger, petnames.load(conn))),
            roster_prescan(lines, logger))


@router.get("/replay/{encounter_id}/stream")
async def replay_stream(encounter_id: int, speed: float = Query(1.0),
                        user=Depends(require_curator)):
    """SSE: `partial` events for a fight that already happened, paced in real
    time. The payload is the live one plus a `replay` block (where the cursor
    is, how long the fight runs, whether it has finished), so the dashboard can
    render it through exactly the same component.
    """
    conn = get_db()
    enc = conn.execute(
        "SELECT * FROM encounters WHERE id=? AND deleted_ts IS NULL",
        (encounter_id,)).fetchone()
    if enc is None:
        raise HTTPException(404, "no such encounter")
    sess = visible_encounters(conn, user, [enc])[enc["session_id"]]
    if sess["pruned"]:
        raise HTTPException(409, "that night was pruned — its raw log is gone")

    speed = max(MIN_SPEED, min(MAX_SPEED, speed))
    logger = sess["character_name"]
    t0, end = enc["started_ts"], enc["ended_ts"]
    loaded = await asyncio.to_thread(
        _load_fight, enc["session_id"], logger, t0, end)
    if loaded is None:
        raise HTTPException(409, "the raw log for that night is no longer stored")
    events, proven = loaded

    # the same knowledge a live session starts with, so a replay draws what the
    # raid would have seen rather than a better-informed hindsight version
    zone = enc["zone"]
    roster, mobs, players, pets = snapshot_context(conn)
    know = livemeter.Knowledge(mobs, players | proven, pets, petnames.load(conn))
    span = max(end - t0, 1)
    stamps = [ev.ts for ev in events]
    head = {
        "encounter_id": encounter_id, "name": enc["name"], "zone": zone,
        "is_named": bool(enc["is_named"]), "started_ts": t0,
        "span_s": span, "speed": speed, "character": logger,
        "events": len(events),
    }

    async def gen():
        yield f"event: replay\ndata: {json.dumps(head)}\n\n"
        started = time.monotonic()
        while True:
            elapsed = (time.monotonic() - started) * speed
            # every tick rebuilds the snapshot over the whole prefix, which is
            # what the live path does to its open segment on every batch —
            # replaying the cost as well as the shape is the point
            window = events[:bisect_right(stamps, t0 + elapsed)]
            done = elapsed >= span
            payload = livemeter.snapshot_payload(
                window, logger, zone, t0 if window else None, roster, know,
                # the cursor IS this replay's log clock, so a replayed fight
                # ends on screen where the real one did
                now_ts=t0 + int(elapsed))
            # The stream overlay reads the live snapshot, which is why it could
            # only ever be worked on during a raid. Publishing the frame here
            # (before the `replay` block, which names the fight and the session
            # it came from and is dashboard-only) lets an OBS source show a
            # replay exactly as a viewer would see the real thing.
            replaybus.publish(user["id"], dict(payload))
            payload["replay"] = {**head, "elapsed_s": min(int(elapsed), span),
                                 "done": done}
            yield f"event: partial\ndata: {json.dumps(payload)}\n\n"
            if done:
                break
            await asyncio.sleep(TICK_S)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
