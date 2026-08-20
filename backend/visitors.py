"""Counting readers, without keeping any (`visit_days` v37).

WHY THIS EXISTS AT ALL. The /chat link was publicized, and the question that
followed is the only question this module answers: how many people came, and
were they strangers or accounts. Everything below is cut to that question, and
the parts that are missing are missing on purpose.

A VISITOR IS A DAY, NOT A PERSON. The stored id is
`sha256(that day's salt + address + user agent)`, and the salt is generated once
per day and DELETED two days later. While the day is live the hash collapses a
person's page loads into one row; once the salt is gone it is attached to
nothing — it cannot be turned back into an address, and it cannot be lined up
against another day's hash for the same person either.

So this table can say "41 distinct people on Tuesday" and CANNOT say "31 of them
had been here before". That ceiling is the design. This site already refuses to
show who is logged in on /chat (`docs/sharing.md`), and a visitor table that
tracked people ACROSS days would be a stronger claim on a reader than the one it
declined to make about a player.

WHAT COUNTS AS A VISIT: a page load — the SPA's index.html going out. Not API
calls, not assets, not the overlay, not a bot. A raider clicking between tabs
inside the app never touches the server, so a "hit" here is somebody arriving,
which is the number worth having anyway.

WHERE THEY WENT, AND WHETHER THEY WERE REAL (v51, `visit_paths`). The two
things the paragraph above quietly gives up are the two things an admin most
wants, so the SPA reports them itself, once per route, through `note_view`:

  * The server sees an ARRIVAL and never a destination, because the SPA routes
    itself after that. Only the client knows it moved from `/` to `/plan`.
  * `is_bot` is a guess about a string anybody can set. A crawler claiming to
    be Chrome is counted as a person, and on this site that was most of the
    traffic. Running the beacon at all is the harder proof: JS ran, so a
    browser rendered the page, so `visit_days.app` is set.

`visit_paths` keeps NO visitor column — not a hash, not a flag. A view is added
to a counter keyed by day, hour and route pattern, so it can say the Planner
was busy on Tuesday evening and can never say who was reading it. That is a
stricter rule than the one `visit_days` keeps, and it is the right one: a table
of pages-per-person is a browsing history, whatever the id in it is called.

IT NEVER RAISES. Counting readers is not worth failing a page load over, so
`note` swallows everything: a visit that is not recorded is a visit that did not
happen, and that is the correct trade in the direction of serving the page.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
import time

log = logging.getLogger("eq2adv.visitors")

# The salts kept in front of the table, so the common case (a person clicking
# around today) is not a SELECT per page load. Only ever holds a day or two.
_lock = threading.Lock()
_salts: dict[str, str] = {}

SALT_KEEP_DAYS = 2      # after this the day's hashes are anchored to nothing
KEEP_DAYS = 400         # the timeline's reach; a year of days is a small table

# Obvious automation. This is a COUNTING filter and nothing else — nobody is
# blocked, nothing is refused, a false positive costs one uncounted visit. It is
# deliberately blunt, because the alternative to a blunt filter is a visitor
# graph made mostly of Googlebot.
_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|scrape|fetch|monitor|uptime|pingdom|preview|"
    r"headless|phantom|selenium|curl|wget|python-requests|httpx|go-http|"
    r"java/|okhttp|libwww|lighthouse|feedly|facebookexternalhit|whatsapp|"
    r"telegram|discord|slack",
    re.I)


def is_bot(agent: str | None) -> bool:
    """No agent at all counts as one: every browser sends one, and the things
    that do not are the things this is trying to leave out."""
    return not agent or bool(_BOT_RE.search(agent))


# A token URL is a CAPABILITY pointed at a meter, not somebody reading the site
# (`docs/live.md`). OBS and EQ2's in-game browser reload theirs whenever the
# scene changes, so counting them would put a stream's restarts in a visitor
# figure — and the person behind them is already counted for the tab they opened
# the dashboard in.
_NOT_A_READER = ("overlay/", "ingame/")


def is_reader(path: str) -> bool:
    return not path.startswith(_NOT_A_READER)


# The SPA's route table (`frontend/src/App.jsx`), as PATTERNS. This is the
# whole key space of `visit_paths`, and it is a fixed list for two reasons: an
# exact-URL table would be a browsing history rather than a page count, and a
# key space nobody controls is one a scanner can fill with junk. Anything that
# does not match is `OTHER` — including every probe for /wp-admin, which is
# then one honest row instead of two hundred.
OTHER = "(other)"

_STATIC = {
    "/", "/wiki", "/compare", "/features", "/login", "/import", "/uploads",
    "/live", "/chat", "/plan", "/guild/skill-issue", "/loot-bids",
    "/calibration", "/characters", "/groups", "/account",
}

# Longest prefix first: `/characters/` is a character and `/characters` is the
# list, and `/guild/skill-issue` is static above while `/admin/anything` all
# folds into one row.
_PARAM = (
    ("/characters/", "/characters/:id"),
    ("/encounters/", "/encounters/:id"),
    ("/sessions/", "/sessions/:id"),
    ("/zones/", "/zones/:id"),
    ("/join/", "/join/:code"),
    ("/admin", "/admin"),
)


def route_of(path: str | None) -> str:
    """The SPA path a browser reported, reduced to its route pattern.

    Defensive about its input on purpose — this is fed by a public POST, so a
    query string, a fragment, a missing slash or a kilobyte of nonsense all
    have to come out as a route name or `OTHER`, and never as a new key."""
    p = (path or "").strip()
    for cut in ("?", "#"):
        p = p.split(cut, 1)[0]
    if not p:
        # Nothing said is not the home page. A caller that sends no path has
        # told us nothing, and filing that under `/` would quietly pad the one
        # row that matters most.
        return OTHER
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 200:
        return OTHER
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    if p in _STATIC:
        return p
    for prefix, pattern in _PARAM:
        if p.startswith(prefix):
            return pattern
    return OTHER


def today(now: float | None = None) -> str:
    """The SERVER's day. Unlike a chat date — which is the reader's, because a
    reader is choosing a window — this is one admin looking at one server, so
    the server's own midnight is the honest boundary and needs no negotiating."""
    return time.strftime("%Y-%m-%d", time.localtime(now if now else time.time()))


def _salt(conn, day: str) -> str:
    """The day's salt, made once and shared by every visitor that day.

    `INSERT OR IGNORE` then read back, rather than checking first: two workers
    can arrive on the first request after midnight, and the loser has to end up
    using the winner's salt or the same person is counted twice."""
    with _lock:
        got = _salts.get(day)
    if got:
        return got
    conn.execute("INSERT OR IGNORE INTO visit_salts (day, salt) VALUES (?, ?)",
                 (day, secrets.token_hex(16)))
    salt = conn.execute("SELECT salt FROM visit_salts WHERE day=?",
                        (day,)).fetchone()["salt"]
    with _lock:
        _salts[day] = salt
        for stale in [d for d in _salts if d < day]:
            del _salts[stale]
    return salt


def _visitor(salt: str, address: str, agent: str) -> str:
    return hashlib.sha256(f"{salt}\n{address}\n{agent}".encode()).hexdigest()


def note(conn, address: str, agent: str | None, path: str,
         signed_in: bool) -> None:
    """One page load. Upserts the day's row for this visitor.

    `chat` and `signed_in` only ever go UP within a day: somebody who reads
    /chat and then signs in is one visitor who did both, and a later page load
    must not erase what the earlier one recorded."""
    try:
        if is_bot(agent) or not is_reader(path):
            return
        now = int(time.time())
        day = today(now)
        who = _visitor(_salt(conn, day), address, agent or "")
        conn.execute(
            "INSERT INTO visit_days (day, visitor, signed_in, chat, hits, "
            "                        first_ts, last_ts) "
            "VALUES (?, ?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(day, visitor) DO UPDATE SET "
            "  hits = hits + 1, last_ts = excluded.last_ts, "
            "  signed_in = MAX(signed_in, excluded.signed_in), "
            "  chat = MAX(chat, excluded.chat)",
            (day, who, 1 if signed_in else 0,
             1 if path.startswith("chat") else 0, now, now))
        conn.commit()
    except Exception:                                   # noqa: BLE001
        # A page is worth more than its tally mark.
        log.debug("visit not counted", exc_info=True)


# A public POST can be sent in a loop. It cannot grow `visit_paths` — the keys
# are the app's own routes — so the only thing a flood can do is inflate a
# counter, and this bounds that to a number no real reading session reaches.
# Per process and in memory, because it is a nuisance guard and not a security
# boundary: the cost of being wrong is one over-counted page.
VIEW_CAP = 400
_views: dict[str, int] = {}
_views_day = ""


def _under_cap(day: str, who: str) -> bool:
    global _views_day
    with _lock:
        if _views_day != day:
            _views.clear()
            _views_day = day
        n = _views.get(who, 0)
        if n >= VIEW_CAP:
            return False
        _views[who] = n + 1
    return True


def note_view(conn, address: str, agent: str | None, path: str,
              entry: bool, signed_in: bool) -> None:
    """One ROUTE, reported by the browser that rendered it (`POST /api/visit`).

    Two writes, and they are deliberately different shapes. `visit_paths` gets
    a counter bumped and is told nothing about who sent it. `visit_days` gets
    `app = 1` — the visit was rendered by something that runs JS, which is the
    only honest answer this site has to "was that a person".

    IT DOES NOT TOUCH `hits`. A hit is an arrival and `spa.py` already counted
    this one when index.html went out; counting it again here would double
    every visit and turn in-app tab changes into arrivals, which is exactly the
    distinction the module is built around. Route views live in `visit_paths`,
    where they say what they are.

    Like `note`, it never raises: a telemetry beacon must not be able to make a
    reader's browser show an error."""
    try:
        if is_bot(agent) or not is_reader(path.lstrip("/")):
            return
        now = int(time.time())
        day = today(now)
        who = _visitor(_salt(conn, day), address, agent or "")
        if not _under_cap(day, who):
            return
        route = route_of(path)
        conn.execute(
            "INSERT INTO visit_paths (day, hour, route, views, entries) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(day, hour, route) DO UPDATE SET "
            "  views = views + 1, entries = entries + excluded.entries",
            (day, int(time.strftime("%H", time.localtime(now))), route,
             1 if entry else 0))
        # The row is normally already here (index.html was served first), so
        # this is usually the UPDATE arm. The INSERT arm covers the browser
        # that restored a tab from cache without asking for a page: it is a
        # real reader either way, and `hits` staying 0 is the truth about it.
        conn.execute(
            "INSERT INTO visit_days (day, visitor, signed_in, chat, hits, app, "
            "                        first_ts, last_ts) "
            "VALUES (?, ?, ?, ?, 0, 1, ?, ?) "
            "ON CONFLICT(day, visitor) DO UPDATE SET "
            "  app = 1, last_ts = excluded.last_ts, "
            "  signed_in = MAX(signed_in, excluded.signed_in), "
            "  chat = MAX(chat, excluded.chat)",
            (day, who, 1 if signed_in else 0, 1 if route == "/chat" else 0,
             now, now))
        conn.commit()
    except Exception:                                   # noqa: BLE001
        log.debug("view not counted", exc_info=True)


def sweep(conn, now: float | None = None) -> None:
    """Drop the old salts, then the old days. Runs at startup and after that
    whenever the day rolls over under a live process (`_maybe_sweep`).

    The salts go first and go early: while a salt exists the day's hashes are
    re-derivable by anybody who can see the table AND guess an address, and
    there is no reason to leave that standing once the day is over."""
    day = today(now)
    keep_salt = time.strftime(
        "%Y-%m-%d",
        time.localtime((now or time.time()) - SALT_KEEP_DAYS * 86400))
    keep_days = time.strftime(
        "%Y-%m-%d", time.localtime((now or time.time()) - KEEP_DAYS * 86400))
    conn.execute("DELETE FROM visit_salts WHERE day < ?", (keep_salt,))
    conn.execute("DELETE FROM visit_days WHERE day < ?", (keep_days,))
    conn.execute("DELETE FROM visit_paths WHERE day < ?", (keep_days,))
    conn.commit()
    with _lock:
        for stale in [d for d in _salts if d < day]:
            del _salts[stale]


_swept = ""


def maybe_sweep(conn) -> None:
    """Once per day, off whatever request first notices the date changed. This
    server is not busy enough to justify a timer thread for a DELETE."""
    global _swept
    day = today()
    if _swept == day:
        return
    _swept = day
    try:
        sweep(conn)
    except Exception:                                   # noqa: BLE001
        log.debug("visit sweep failed", exc_info=True)


def timeline(conn, days: int = 30) -> dict:
    """The admin page's answer: one row per day, newest first, plus the totals
    over the same span.

    `visitors` is the honest headline — distinct people that day — and it is the
    one number that CANNOT be summed across rows, because the same person on two
    days is two rows and this table has thrown away what would tell them apart.
    So the totals carry a summed `hits` and a summed per-day visitor count under
    a name that says what it is (`visitor_days`), and never a "unique visitors
    this month" figure, which would be a lie the data cannot support.

    `browsers` is the same count with the beacon's answer applied: of those
    visitors, how many rendered a page in something that runs JS (v51). It is
    the number to read when asking how many PEOPLE came — `visitors` includes
    every crawler that set a browser user-agent, and most of them do."""
    days = max(1, min(days, 400))
    since = time.strftime("%Y-%m-%d",
                          time.localtime(time.time() - (days - 1) * 86400))
    rows = conn.execute(
        "SELECT day, "
        "  COUNT(*) AS visitors, "
        "  SUM(CASE WHEN app=1 THEN 1 ELSE 0 END) AS browsers, "
        "  SUM(CASE WHEN signed_in=0 THEN 1 ELSE 0 END) AS anon, "
        "  SUM(CASE WHEN signed_in=1 THEN 1 ELSE 0 END) AS accounts, "
        "  SUM(CASE WHEN chat=1 THEN 1 ELSE 0 END) AS chat, "
        "  SUM(hits) AS hits "
        "FROM visit_days WHERE day >= ? GROUP BY day ORDER BY day DESC",
        (since,)).fetchall()
    out = [dict(r) for r in rows]
    return {
        "since": since,
        "days": out,
        "totals": {
            "visitor_days": sum(r["visitors"] for r in out),
            "browser_days": sum(r["browsers"] for r in out),
            "anon_days": sum(r["anon"] for r in out),
            "chat_days": sum(r["chat"] for r in out),
            "hits": sum(r["hits"] for r in out),
            "days_counted": len(out),
        },
    }


def destinations(conn, days: int = 30) -> dict:
    """WHERE they went: route patterns over the window, busiest first.

    `entries` is the route a visit STARTED on and `views` is every time the
    route was opened, in-app moves included, and the gap between the two is the
    useful part. A route with entries and no views beyond them is a link people
    arrive on and bounce from; a route with many views and few entries is where
    the app actually sends people once they are inside it.

    Both are view counts and neither is a people count, because this table has
    no visitor in it (`db.py` v51). Two hundred Planner views can be four
    raiders on a Tuesday, and there is deliberately no way to tell from here."""
    days = max(1, min(days, 400))
    since = time.strftime("%Y-%m-%d",
                          time.localtime(time.time() - (days - 1) * 86400))
    rows = conn.execute(
        "SELECT route, SUM(views) AS views, SUM(entries) AS entries "
        "FROM visit_paths WHERE day >= ? "
        "GROUP BY route ORDER BY views DESC, route", (since,)).fetchall()
    out = [dict(r) for r in rows]
    return {
        "since": since,
        "routes": out,
        "totals": {
            "views": sum(r["views"] for r in out),
            "entries": sum(r["entries"] for r in out),
        },
    }


def arrivals(conn, days: int = 30) -> dict:
    """WHEN they came: 24 hours of the SERVER's clock, summed over the window.

    Every hour is present even when it is empty, because a reader is comparing
    the shape of a day and a missing 4am is not the same as a quiet one.

    The hour is the server's, like `today` and for the same reason: one admin
    looking at one box. It follows that this is a picture of when the site is
    BUSY in local terms, and not of when any particular reader's evening is."""
    days = max(1, min(days, 400))
    since = time.strftime("%Y-%m-%d",
                          time.localtime(time.time() - (days - 1) * 86400))
    got = {r["hour"]: r for r in conn.execute(
        "SELECT hour, SUM(views) AS views, SUM(entries) AS entries "
        "FROM visit_paths WHERE day >= ? GROUP BY hour", (since,)).fetchall()}
    hours = [{"hour": h,
              "views": got[h]["views"] if h in got else 0,
              "entries": got[h]["entries"] if h in got else 0}
             for h in range(24)]
    return {"since": since, "hours": hours}
