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
    this month" figure, which would be a lie the data cannot support."""
    days = max(1, min(days, 400))
    since = time.strftime("%Y-%m-%d",
                          time.localtime(time.time() - (days - 1) * 86400))
    rows = conn.execute(
        "SELECT day, "
        "  COUNT(*) AS visitors, "
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
            "anon_days": sum(r["anon"] for r in out),
            "chat_days": sum(r["chat"] for r in out),
            "hits": sum(r["hits"] for r in out),
            "days_counted": len(out),
        },
    }
