"""The route beacon — what the SPA tells the server about where it went.

ONE PUBLIC POST, and it exists because the server cannot see this on its own.
A visit is counted where index.html goes out (`spa.py`), and everything after
that is client-side routing: the arrival is on record and the destination is
not. So the browser says, once per route.

WHAT IT ANSWERS THAT `visit_days` COULD NOT (`visitors.py`, `db.py` v51):

  * WHERE — the route PATTERN, never the URL. The browser sends its pathname
    and `visitors.route_of` reduces it AT THE DOOR: `/zones/139710` is stored
    as `/zones/:id` and the id is never written down. Reducing here rather
    than in the SPA keeps one route table instead of two that can drift, and
    the server has to do it regardless — a client's answer about what it is
    allowed to write is not one worth trusting.
  * WHEN — the server's hour, taken here rather than trusted from the body.
  * WHETHER IT WAS A PERSON — implicitly, and this is the valuable half. The
    user-agent filter in front of the visit count is a guess about a string
    anybody can set; a crawler claiming to be Chrome sails past it. Reaching
    this endpoint at all means JS ran, so `visit_days.app` is set and the admin
    page finally has a number that is browsers rather than user-agents.

IT IS UNAUTHENTICATED, because the thing worth counting is the stranger. The
cookie is read if it happens to be there — `sendBeacon` sends it same-origin —
purely to keep the signed-in flag as accurate as the page load's was.

NOTHING IT RECEIVES IS TRUSTED. The route is matched against the SPA's own
route table and anything else becomes `(other)`, so the body cannot introduce a
key; the hour and day come from the server's clock; and a per-visitor cap
bounds what a loop can inflate. The reply is 204 with no body: `sendBeacon`
cannot read one, and there is nothing a caller should learn from this.
"""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

import auth
import siteconfig
import visitors
from db import get_db

router = APIRouter()


class VisitIn(BaseModel):
    # Length-capped at the door: `route_of` rejects long paths anyway, and a
    # 200-char field means a junk body is refused before it reaches the db.
    path: str = Field(default="/", max_length=200)
    # The first route of this visit, as opposed to a move inside the app. The
    # client knows this and the server cannot: a page load and the beacon that
    # follows it are two requests with nothing tying them together.
    entry: bool = False


@router.post("/visit", status_code=204)
def record_visit(body: VisitIn, request: Request) -> Response:
    """Never fails a caller. `visitors.note_view` swallows its own errors, and
    the two lookups in front of it are wrapped for the same reason `spa.py`
    wraps them: this runs on a reader's page and must not be able to put an
    error in their console."""
    try:
        conn = get_db()
        visitors.maybe_sweep(conn)
        visitors.note_view(
            conn,
            # The proxies falsify `request.client.host`; `siteconfig` owns the
            # real one, and a per-day visitor hash of the proxy's own address
            # would collapse everybody into one reader.
            siteconfig.client_ip(request),
            request.headers.get("user-agent"),
            body.path,
            body.entry,
            auth.session_user(conn, request.cookies.get(auth.COOKIE)) is not None,
        )
    except Exception:                                   # noqa: BLE001
        pass
    return Response(status_code=204)
