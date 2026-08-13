"""The live meter as a URL: an OBS browser source, or EQ2's own browser window.

A URL you paste into OBS. That constraint decides the whole design — a browser
source carries no cookies, and `EventSource` cannot set a header, so the token
has to ride in the path. Everything else here follows from taking that
seriously:

* **The token is a capability, and a narrow one.** It reaches the in-flight
  meter for whichever of that account's characters is streaming RIGHT NOW, and
  nothing else. No session ids, no fight cards, no history, no account name —
  a URL that ends up on a stream, in a VOD, or in somebody's OBS backup must
  not be a way into anybody's parses.
* **Which session it points at is re-resolved every tick.** A streamer who
  switches to an alt keeps streaming, without touching OBS.
* **The stream stays open when nothing is live.** An OBS source is opened once
  and left running for hours; a stream that ended between pulls would be a
  scene that goes blank and never comes back.

TWO KINDS OF TOKEN, ONE CAPABILITY (`kind`, schema v34). `overlay` is the OBS
source. `ingame` is the same page opened in EQ2's own browser window, and it is
a separate row rather than a second config block on the same one because
revoking is per URL: a link that ended up in a VOD has to be killable without
taking the window beside somebody's hotbars down with it. The public half does
not branch on kind at all — a token is a token, it reaches the fight in
progress, and which SIZE the page draws itself at is a fact about the screen it
is on. Only the config defaults and their clamps differ, and they differ hard:
a stream is watched after a downscale and an encode and wants type BIGGER than
1:1, while an in-game window is a corner of a UI that already has too much in
it.

The cookie half — minting, configuring, revoking — is ordinary account API and
lives at the bottom. The two doors share one read surface
(`pipeline/live.live_snapshot`) rather than one endpoint branching on how the
caller authenticated, because a generator that decides authorization halfway
through is a generator nobody can audit.
"""

import asyncio
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

import marks
import ratelimit
from db import get_db, json_dumps
from pipeline import live, livebus, replaybus
from security import require_user
from siteconfig import client_ip

router = APIRouter(tags=["overlay"])

# The fallback tick, not the update rate: a live `partial` arrives when the
# ingest path rings the bell (pipeline/livebus.py). A REPLAY has no bell — it
# is produced by another request's generator at its own pace — so watching one
# still comes round on this.
POLL_S = 1.5
MAX_TOKENS = 10        # a person needs one; ten is room to rotate, not a fleet

THEMES = ("transparent", "dark", "light")
METRICS = ("dps", "hps")
# Where HPS goes when it is on: under DPS, or beside it. A stream's parse is
# sized by the scene it has to fit, and the two shapes fit opposite scenes.
LAYOUTS = ("vertical", "horizontal")
# Narrower than this and a name and a rate do not fit on one row; wider than
# this is not an overlay any more.
MIN_WIDTH_PX, MAX_WIDTH_PX = 160, 1920
# Below 1 is smaller than the dock, which no stream has ever wanted; above 2.5
# a row of eight is taller than most scenes have for it.
MIN_SCALE, MAX_SCALE = 1.0, 2.5


class OverlayConfig(BaseModel):
    theme: str = "transparent"
    metrics: list[str] = Field(default_factory=lambda: ["dps"])
    max_rows: int = Field(default=8, ge=1, le=40)
    show_timers: bool = True
    layout: str = "vertical"
    # Pinned width in CSS pixels, or None for "fill the browser source". A
    # scene is built once and lived with, and a parse that reflows every time
    # the source is nudged is one nobody can line up against anything else —
    # so the number is typed rather than dragged.
    width_px: int | None = None
    # Type size, as a multiplier on the overlay's own base size (base.css:
    # `--ovl`). It defaults ABOVE 1 and that is not a stylistic preference:
    # what a viewer gets is the scene downscaled to the output resolution and
    # then encoded, so the sizes that are right on a monitor at 1:1 arrive as a
    # smear. Nothing on this end can know that chain — every scene's is
    # different — so the streamer turns it up until it reads on the stream.
    text_scale: float = 1.25
    # OFF is not REVOKED. A streamer who wants the parse off the scene for one
    # pull should not have to change anything in OBS and then re-add the source
    # afterwards, so the page keeps its connection and draws nothing.
    enabled: bool = True

    def cleaned(self) -> dict:
        metrics = [m for m in self.metrics if m in METRICS] or ["dps"]
        return {
            "theme": self.theme if self.theme in THEMES else "transparent",
            "metrics": metrics,
            "max_rows": self.max_rows,
            "show_timers": self.show_timers,
            "layout": self.layout if self.layout in LAYOUTS else "vertical",
            "enabled": self.enabled,
            # clamped rather than rejected: a width is a nudge somebody types
            # into a box on a dashboard, not an argument worth a 422
            "width_px": (None if not self.width_px or self.width_px <= 0
                         else max(MIN_WIDTH_PX, min(MAX_WIDTH_PX, self.width_px))),
            # clamped for the same reason as the width
            "text_scale": round(max(MIN_SCALE, min(MAX_SCALE, self.text_scale)), 2),
        }


# The in-game window is EQ2's own browser, which means it is a corner of a UI
# that is already full: it wants FEWER rows, SMALLER type and no scene geometry
# at all. Width and layout are absent on purpose — the window is the width, and
# nobody arranges two stacks side by side in something that size.
INGAME_MIN_SCALE, INGAME_MAX_SCALE = 0.5, 1.6


class IngameConfig(BaseModel):
    # Dark rather than transparent: OBS composites a transparent page over the
    # game, EQ2's browser puts it in a window, and a page painting nothing
    # there is a page you cannot read.
    theme: str = "dark"
    metrics: list[str] = Field(default_factory=lambda: ["dps"])
    # Enough to see the top of the raid without pushing the window past the
    # part of the screen anybody is willing to give up.
    max_rows: int = Field(default=6, ge=1, le=40)
    show_timers: bool = True
    # THE ONE THING THE OBS OVERLAY DOES NOT HAVE. A stream is watched by
    # people who cannot act on a warning; the player looking at this window is
    # the person the card is for, which is the whole reason this screen exists
    # rather than pointing the in-game browser at the overlay URL.
    notify: bool = True
    # Defaults BELOW 1, and that is the opposite of the overlay's default for
    # the opposite reason: this is read at 1:1 on the same monitor as the game,
    # with no encoder in between, and every pixel it takes is a pixel of raid.
    # 0.73 is 11px once the page rounds it to whole pixels, and it has to match
    # a chip in `OverlayOptions.INGAME_SCALES` or the panel opens with none of
    # them lit.
    text_scale: float = 0.73
    enabled: bool = True

    def cleaned(self) -> dict:
        metrics = [m for m in self.metrics if m in METRICS] or ["dps"]
        return {
            "theme": self.theme if self.theme in THEMES else "dark",
            "metrics": metrics,
            "max_rows": self.max_rows,
            "show_timers": self.show_timers,
            "notify": self.notify,
            "enabled": self.enabled,
            "text_scale": round(max(INGAME_MIN_SCALE,
                                    min(INGAME_MAX_SCALE, self.text_scale)), 2),
        }


# `kind` decides which settings a row HAS, so it is fixed at creation and never
# patched: the two configs do not share a shape, and a row that changed kind
# would be a URL that quietly started drawing something else.
CONFIGS = {"overlay": OverlayConfig, "ingame": IngameConfig}
KINDS = tuple(CONFIGS)


def _clean_config(kind: str, config: dict | None) -> str:
    model = CONFIGS.get(kind, OverlayConfig)
    try:
        return json_dumps(model(**(config or {})).cleaned())
    except ValidationError:
        # a settings panel sent something the model will not take — that is a
        # bad request, not a 500, and never a half-written config
        raise HTTPException(422, "bad overlay config")


class OverlayIn(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    kind: str = "overlay"
    # Free-form on the way in and validated per KIND below, because the two
    # kinds do not share a config shape and one endpoint cannot declare both.
    config: dict = Field(default_factory=dict)


def _row(row, with_token=False) -> dict:
    kind = (row["kind"] if "kind" in row.keys() else None) or "overlay"
    d = {"id": row["id"], "label": row["label"], "kind": kind,
         "created_ts": row["created_ts"], "revoked_ts": row["revoked_ts"],
         "config": json.loads(row["config_json"] or "{}")}
    if with_token:
        d["token"] = row["token"]
        d["url"] = f"/{'ingame' if kind == 'ingame' else 'overlay'}/{row['token']}"
    return d


def _resolve(token: str, request):
    """A usable overlay token, or a 404.

    Revoked and never-existed get the same answer on purpose — whether a token
    was ever real is not a question an unauthenticated endpoint answers. The
    token is the only credential here, so a wrong one is a failed guess and is
    counted like any other (`ratelimit.py` counts failures, not requests, so an
    overlay reconnecting all night costs nothing).

    A CORRECT TOKEN IS NOT A GUESS, so it is looked up BEFORE the limiter is
    consulted and can never be answered with a 429. The other order — check the
    bucket, then resolve — is what a rate limiter normally wants, and it was
    wrong here for a reason specific to this endpoint: the bucket is keyed by
    client ADDRESS, and every screen in this feature re-asks the same question
    every five seconds forever. One revoked link left open in a browser somebody
    forgot about is twelve `fail()`s a minute, and after five of them the same
    machine's VALID overlay went dark too — the limiter never reached the
    lookup, so the success that would have cleared the bucket could not happen.
    An IP-wide, self-inflicted, self-sustaining lockout on the whole feature.

    Resolving first cannot weaken the brake it is there to be: enumeration is
    made of MISSES, and a miss still costs a failure and is still refused once
    the bucket is full. What changes is only that holding the right token is
    always enough.

    AND A HIT NO LONGER CLEARS THE BUCKET, which it used to. `clear()` on
    success is written for the login route, where the person failing and the
    person succeeding are the same person and the kindness is obvious. It does
    not survive being moved ahead of the limiter: this bucket is per ADDRESS,
    the token holder and a guesser can be two different parties behind one of
    those, and a working overlay polling every five seconds would have wiped
    the counter often enough that nobody could ever be locked out at all. The
    honest holder does not need the clear any more — they are answered before
    the limiter is consulted — so misses now simply age out on their own
    fifteen-minute window, which is the only thing that should retire them."""
    key = client_ip(request)
    row = None
    if token and len(token) <= 128:
        row = get_db().execute(
            "SELECT * FROM overlay_tokens WHERE token=? AND revoked_ts IS NULL",
            (token,)).fetchone()
    if row is not None:
        return row
    wait = ratelimit.retry_after("overlay", key)
    if wait:
        raise HTTPException(429, "too many attempts — wait a few minutes",
                            headers={"Retry-After": str(wait)})
    ratelimit.fail("overlay", key)
    raise HTTPException(404, "no such overlay")


def _streaming_session(conn, user_id: int):
    """Whichever of this account's characters is receiving right now.

    Resolved per tick rather than once, so switching to an alt mid-stream
    follows on its own — the overlay is pointed at a PERSON, not a session."""
    return conn.execute(
        "SELECT s.id FROM sessions s JOIN characters c ON c.id = s.character_id "
        "WHERE c.user_id=? AND s.source='live' AND s.status='receiving' "
        "ORDER BY s.last_ingest_ts DESC, s.id DESC LIMIT 1", (user_id,)).fetchone()


# ---- the public half: token in the path, no cookie ----

@router.get("/overlay/{token}")
def overlay_config(token: str, request: Request):
    """What the page needs to draw itself — never who owns it.

    THE HAND MARKS RIDE ALONG HERE, and this poll is the reason they can.
    Which AoEs get a countdown on a strip this size, and which one owns the
    burn window, are the two things about this page that no log can supply
    (`backend/marks.py`): they are marked by hand on the dashboard, and until
    v35 they lived in the marking browser's localStorage. EQ2's own browser is
    a different browser, so the window beside somebody's hotbars inherited none
    of it — it drew whatever their ACT spell-timer list defaulted to and nothing
    they had said. Same hole on the stream overlay, unnoticed because nobody
    reads their own stream.

    The page cannot be reloaded and holds no cookie, so it re-reads this every
    few seconds anyway to pick up a setting changed on the dashboard. Marks
    take that ride: a pill toggled mid-raid reaches the game window on the next
    tick. It costs one extra query on a request that is already resolving a
    token, and it needs no second endpoint for a screen that could not
    authenticate to one.

    It is still config only. What goes over is a set of ABILITY names — facts
    about the game, of the same kind as the countdowns already on screen — with
    no account, no character and no raid attached, so a token that ends up in a
    VOD reaches exactly what it reached before."""
    row = _resolve(token, request)
    return {"config": json.loads(row["config_json"] or "{}"),
            "marks": marks.read(get_db(), row["user_id"])}


@router.get("/overlay/{token}/stream")
async def overlay_stream(token: str, request: Request):
    """The live meter, and only the live meter.

    Emits `partial` (the in-flight view) and a minimal `status` saying whether
    anything is streaming. Deliberately NOT the session stream: that one
    carries fight cards, ids and line counts, none of which a stream overlay
    has any business holding.

    A REPLAY this account is running wins over its live session, the way the
    dashboard makes the two exclusive: a replay is somebody deliberately
    driving the meter, and it is the only way to see what a viewer sees
    without waiting for a raid (`pipeline/replaybus.py`). The frame arrives
    without its `replay` block — what fight it is and which night it came from
    are dashboard facts, and this token is not allowed to hold them."""
    user_id = _resolve(token, request)["user_id"]

    async def gen():
        last_partial = None
        was_live = None
        while True:
            replayed = replaybus.latest(user_id)
            sess = None if replayed is not None else _streaming_session(
                get_db(), user_id)
            # Which session an overlay is watching is decided per pass — a
            # raider can start a new one mid-scene — so the bell is subscribed
            # per pass, HERE, before this pass reads the snapshot. Subscribing
            # after the read would drop a snapshot published while it read.
            # A replay (or nothing streaming) has no bell to ring: `None` makes
            # this a plain sleeper, and the loop reads the same either way.
            with livebus.subscribe(None if replayed is not None or sess is None
                                   else sess["id"]) as bell:
                if replayed is not None:
                    if was_live is not True:
                        was_live = True
                        yield 'event: status\ndata: {"live": true}\n\n'
                    if replayed["computed_ts"] != last_partial:
                        last_partial = replayed["computed_ts"]
                        yield f"event: partial\ndata: {json.dumps(replayed)}\n\n"
                elif sess is None:
                    if was_live is not False:
                        was_live = False
                        last_partial = None
                        yield 'event: status\ndata: {"live": false}\n\n'
                    else:
                        # a comment line: keeps the connection warm through a break
                        # between raids without saying anything
                        yield ": idle\n\n"
                else:
                    if was_live is not True:
                        was_live = True
                        yield 'event: status\ndata: {"live": true}\n\n'
                    live.mark_watched(sess["id"])
                    snap = live.live_snapshot(sess["id"])
                    if snap is not None and snap["computed_ts"] != last_partial:
                        last_partial = snap["computed_ts"]
                        yield f"event: partial\ndata: {json.dumps(snap)}\n\n"
                # A wake-up, never the contract: the loop still comes round on
                # its own to refresh `mark_watched` and to notice a session
                # starting, ending or being replaced.
                await bell.wait(POLL_S)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---- the account half: cookie, like everything else ----

@router.get("/overlay-tokens")
def list_tokens(user=Depends(require_user)):
    conn = get_db()
    return {"overlays": [_row(r, with_token=True) for r in conn.execute(
        "SELECT * FROM overlay_tokens WHERE user_id=? AND revoked_ts IS NULL "
        "ORDER BY created_ts DESC", (user["id"],))]}


@router.post("/overlay-tokens")
def create_token(body: OverlayIn, user=Depends(require_user)):
    conn = get_db()
    live_count = conn.execute(
        "SELECT COUNT(*) FROM overlay_tokens WHERE user_id=? AND revoked_ts IS NULL",
        (user["id"],)).fetchone()[0]
    if live_count >= MAX_TOKENS:
        raise HTTPException(409, "revoke one of your overlays first")
    kind = body.kind if body.kind in KINDS else "overlay"
    token = secrets.token_urlsafe(24)
    with conn:
        cur = conn.execute(
            "INSERT INTO overlay_tokens (user_id, token, label, kind, "
            "config_json, created_ts) VALUES (?,?,?,?,?,?)",
            (user["id"], token, (body.label or "").strip() or None, kind,
             _clean_config(kind, body.config), int(time.time())))
    return _row(conn.execute("SELECT * FROM overlay_tokens WHERE id=?",
                             (cur.lastrowid,)).fetchone(), with_token=True)


@router.patch("/overlay-tokens/{token_id}")
def update_token(token_id: int, body: OverlayIn, user=Depends(require_user)):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM overlay_tokens WHERE id=? AND user_id=? AND revoked_ts IS NULL",
        (token_id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "no such overlay")
    # The row's own kind, never the body's: a link that changed what it draws
    # under somebody's OBS source is not a setting, it is a different feature.
    kind = (row["kind"] if "kind" in row.keys() else None) or "overlay"
    with conn:
        conn.execute("UPDATE overlay_tokens SET config_json=?, label=? WHERE id=?",
                     (_clean_config(kind, body.config),
                      (body.label or "").strip() or row["label"], token_id))
    return _row(conn.execute("SELECT * FROM overlay_tokens WHERE id=?",
                             (token_id,)).fetchone(), with_token=True)


@router.post("/overlay-tokens/{token_id}/revoke")
def revoke_token(token_id: int, user=Depends(require_user)):
    """Revoked, not deleted: the row is what makes a URL already out in the
    world stop working, and it is worth being able to see that it did."""
    conn = get_db()
    with conn:
        cur = conn.execute(
            "UPDATE overlay_tokens SET revoked_ts=? WHERE id=? AND user_id=? "
            "AND revoked_ts IS NULL", (int(time.time()), token_id, user["id"]))
    if not cur.rowcount:
        raise HTTPException(404, "no such overlay")
    return {"id": token_id, "revoked": True}
