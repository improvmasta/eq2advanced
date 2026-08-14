"""The public chat box (`/chat`). `pipeline/chatbus.py` says what it keeps and
why keeping it is not a redaction change.

  GET /api/chat/recent            -> the archive's newest, and the span it covers
  GET /api/chat/history?ch&start&end -> one channel over one window (the date filter)
  GET /api/chat/recruiting         -> newest current pitch from each recruiting guild
  GET /api/chat/stream[?since=N]  -> SSE: `chat` for new messages, `status` as
                                     the keepalive

**Open to anybody, signed in or not.** It used to need an account and that was
the wrong shape for what this is: the record has no user in it, every line was
broadcast to a whole server by the game, and there is nothing here to gate —
none of these routes reaches a parse, a session or an account. What an account
still decides is who FILLS it, which is unchanged: the only way to say something
in this box is to say it in the game with your plugin running. There is no POST.
"""

import json
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from db import get_db
from pipeline import chatbus

router = APIRouter(tags=["chat"])

# The bell makes a message fast; this is what keeps the connection warm when
# nobody is talking, which through a proxy is the difference between an idle
# stream and a dropped one.
POLL_S = 5.0
STATUS_HEARTBEAT_S = 20.0

# The date filter asks for a day. The cap is not about cost — it is so a
# hand-built URL cannot ask for the whole table in one answer.
MAX_WINDOW_S = 31 * 86400


@router.get("/chat/recent")
def chat_recent():
    return chatbus.recent(get_db())


@router.get("/chat/history")
def chat_history(ch: str = Query(...), start: int = Query(...),
                 end: int = Query(...)):
    """One channel between two unix seconds. The bounds come from the BROWSER,
    which is the only place that knows where the reader's midnight is."""
    if ch not in chatbus.CHANNEL_KEYS:
        raise HTTPException(404, "no such channel")
    if end <= start or end - start > MAX_WINDOW_S:
        raise HTTPException(400, "bad window")
    return {"ch": ch, "start": start, "end": end,
            "messages": chatbus.history(get_db(), ch, start, end)}


@router.get("/chat/status")
def chat_status():
    """Is anybody relaying RIGHT NOW. One number, so the header can carry a
    light on every page without pulling three channels of messages to find out —
    `/chat/recent` answers this too, and costs 900 messages to do it.

    `connected` is uploaders, not readers: the box goes dark when nobody is
    playing with the plugin running, which is the state the light is for."""
    return {"connected": chatbus.snapshot()["connected"]}


@router.get("/chat/recruiting")
def chat_recruiting():
    """Current guild adverts collected from General; still the same public
    messages, only grouped by guild and with repeated macros collapsed."""
    return {"guilds": chatbus.recruiting(get_db())}


@router.get("/chat/stats")
def chat_stats(ch: str = Query(...), start: int | None = None,
               end: int | None = None):
    """What one channel looked like — leaderboards, the clock profile and the
    word cloud. No window means ALL TIME, which is what the box shows when it is
    live: the live tail is the last few hundred lines and counting those would
    answer a question nobody asked.

    Same 31-day cap as `history` when a window IS given, and for the same
    reason. All-time has no cap because it is one scan of one channel and the
    answer is cached until somebody says something."""
    if ch not in chatbus.CHANNEL_KEYS:
        raise HTTPException(404, "no such channel")
    if (start is None) != (end is None):
        raise HTTPException(400, "bad window")
    if start is not None and (end <= start or end - start > MAX_WINDOW_S):
        raise HTTPException(400, "bad window")
    return chatbus.stats_cached(get_db(), ch, start, end)


@router.get("/chat/stream")
async def chat_stream(request: Request, since: int | None = None):
    async def gen():
        last_id = since
        last_status = None
        status_at = 0.0
        with chatbus.subscribe() as bell:
            while True:
                if await request.is_disconnected():
                    break
                snap = chatbus.snapshot(since=last_id)
                fresh = sorted(
                    (m for msgs in snap["channels"].values() for m in msgs),
                    key=lambda m: m["id"])
                if fresh:
                    last_id = fresh[-1]["id"]
                    yield f"event: chat\ndata: {json.dumps(fresh)}\n\n"
                elif last_id is None:
                    last_id = snap["seq"]

                status = {"connected": snap["connected"]}
                now = time.monotonic()
                if status != last_status or now - status_at >= STATUS_HEARTBEAT_S:
                    last_status = status
                    status_at = now
                    yield f"event: status\ndata: {json.dumps(status)}\n\n"
                await bell.wait(POLL_S)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
