"""Public chat (General / LFG / Auction): relayed live AND kept.

WHAT IS KEPT, AND WHY THAT IS NOT A REDACTION CHANGE. `pipeline/redact.py` is
untouched and still governs what lands on disk from a LOG: tells, guild chat,
officer chat, /say and the public channels are all dropped before an uploaded
byte is written, exactly as before. What this module does is read the three
PUBLIC channels out of a live batch on its way past and write them to
`chat_messages` (v36) as the site's own record — a table with no user, no
character and no session in it, because a public chat line belongs to the server
rather than to whoever's plugin happened to relay it. General, LFG and Auction
are broadcast by the game to everyone in the zone, so what is kept here is what
the whole server already saw.

IT USED TO BE A RELAY AND IS NOW A RECORD. Until v36 a message sat in a bounded
deque for a few hours and a restart emptied it. That made the page a window onto
this minute only, and the point of the page is to be a window into the game — so
the box now opens on the archive and every block carries a date filter. The
deque survives as the live TAIL, which is all the SSE stream ever reads; the
table is what a reader browses.

DEFAULT-DENY, TWICE — UNCHANGED. A line reaches a channel only if it matches the
exact `tells <Name> (<n>),` shape AND the name and number are BOTH in
`CHANNELS`. A private tell is `You tell Ellea, "…"` — no `(n)` — so it cannot
match whatever someone is called; Crafting (6), guild, officer and /say match
nothing. A channel EQ2 adds later is dropped until somebody adds it here, which
is the direction an error in this file has to fail in. Storing the box made this
test MORE load-bearing, not less: it is the only thing standing between a
private line and a permanent row.

LIVE ONLY. `absorb` ignores backfill batches and anything whose log clock is
more than `MAX_LAG_S` from the wall clock, for the same reason
`live._publish_snapshot` does: a raider importing March's log must not have
March's General chat scroll past as if it were happening now — and must not file
March's chat under tonight either.

ONE LINE, SEVERAL UPLOADERS, ONE MESSAGE — AND THEIR CLOCKS DO NOT AGREE. The
`(1786724295)` a log line opens with is written by each player's own EQ2 client
off their own machine clock, so the same sentence arrives stamped 12:18:15 from
one uploader and 12:18:16 from the next. An exact (ts, ch, who, text) match
therefore does NOT collapse it — that hole was in the original relay too and
only became visible once the box stopped forgetting. The test is a WINDOW:
same channel, same speaker, same text within `DEDUPE_WINDOW_S` is one message.
`UNIQUE(ts, ch, who, text)` stays as the exact-match backstop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import re
import threading
import time
from collections import deque

from items import unsign
from parser.prefix import split_prefix

# name -> (key, the number EQ2 prints for it). Both are checked: the number is
# what makes "General" unambiguous if a player-made channel ever borrows a name.
CHANNELS = {
    "General": ("general", 2),
    "LFG": ("lfg", 3),
    "Auction": ("auction", 10),
}
CHANNEL_KEYS = ("general", "lfg", "auction")

BACKLOG = 200          # the live tail held in memory, per channel
OPENING = 300          # …and what a page opening on the archive is handed
MAX_LAG_S = 120        # log clock this far from now is history, not chat
CONTRIB_TTL_S = 90     # a character quiet this long is no longer "connected"
MAX_TEXT = 600         # a chat line is not a paragraph; truncate rather than keep it
MAX_HISTORY = 5000     # one date filter answer; a day of General is far under it
RECRUIT_DAYS = 3       # an old advert is not evidence that a guild is recruiting now
RECRUIT_N = 12         # cards in the page's narrow recruiting rail

# How far apart two identical lines can be stamped and still be ONE message.
# This is a COMPROMISE with two failure modes and no setting that avoids both:
# widen it and a genuine repeat gets eaten (an auction spammer reposting the
# same WTS every minute is the case that matters, and it is real chat); narrow
# it and unsynchronised clocks double-post. 20s is far more than the skew
# between two machines that talk to a time server at all, and far less than the
# gap anybody leaves before saying the same thing again on purpose.
#
# Skew beyond this still double-posts. `MAX_LAG_S` bounds it at 120s, so the
# remaining case is a machine minutes out of true, which is rare enough to leave
# alone rather than pay for with a per-uploader clock model.
DEDUPE_WINDOW_S = 20

# \aPC -1 Evoxx:Evoxx\/a tells Auction (10), "WTB …"
_PC_RE = re.compile(
    r'^\\aPC -?\d+ (?P<who>[^\\]*)\\/a tells (?P<chan>[A-Za-z]+) \((?P<num>\d+)\), "(?P<text>.*)"$')
# the uploader's own line, which the client writes without the speaker markup
_SELF_RE = re.compile(
    r'^You tell (?P<chan>[A-Za-z]+) \((?P<num>\d+)\), "(?P<text>.*)"$')

# EQ2 chat markup: \aITEM 212446172 1534185667 0 0 0:Tunare's Wrath: Fingers\/a
# and the same shape for every other link kind. The label after the LAST colon
# before the terminator is what the player saw, so that is what is relayed.
_LINK_RE = re.compile(r"\\a(?P<kind>[A-Z]+) (?P<args>[^:\\]*):(?P<label>[^\\]*)\\/a")

# A URL somebody typed into the channel. EQ2 does not link these — they arrive
# as plain text — so this is the page doing what the game does not. Deliberately
# blunt: a scheme (or a bare `www.`) up to the first whitespace, with trailing
# sentence punctuation handed back to the text, because "see eq2advanced.com."
# ends in a full stop that is not part of the address. Nothing here decides a
# URL is SAFE; `Chat.jsx` is where that is dealt with.
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.I)
_URL_TAIL = ".,;:!?)]}'\""

# Guild adverts on Wuoshi currently arrive in two shapes: a decorated name at
# the start (`<Super Best Friends>`, `(Ctrl Alt Defeat)`, `[ Malazan ]`) or a
# bare proper name followed by "is/are" (`Revelation Is Looking …`). Decoration
# alone is not enough — ordinary chat uses brackets too — so every match also
# needs language that says recruiting, joining, a raid roster or an invite.
_GUILD_WRAPPED_RE = re.compile(
    r"^\s*[<(\[]\s*(?P<guild>[^<>()\[\]]{2,60}?)\s*[>)\]]")
_GUILD_BARE_RE = re.compile(
    r"^\s*(?P<guild>[A-Za-z][A-Za-z' -]{1,58}?)\s+(?:is|are)\s+(?P<rest>.+)$",
    re.I)
_RECRUIT_CUE_RE = re.compile(
    r"\brecruit(?:ing|s|ment|ed)?\b|\bjoin(?:ing)?\b|"
    r"\braid (?:force|roster)\b|\bguild\b.{0,100}\binvite\b",
    re.I)
_NOT_GUILD_NAMES = frozenset({"raid roster", "raid force", "our guild", "the guild"})


def _speaker(raw: str) -> str:
    """`Evoxx:Evoxx` -> `Evoxx`. The two halves are the character and the
    display name; they differ for a mercenary or a pet, and the second is the
    one printed in the chat window."""
    return raw.split(":")[-1].strip() or raw.strip()


def _item_id(args: str) -> int | None:
    """`212446172 1534185667 0 0 0` -> the Census item id. The FIRST number is
    the item and it is written SIGNED, exactly as `pipeline/loot.py` reads it —
    which is why an item somebody links in Auction can wear the same examine
    card as one that dropped off a chest. Anything unparseable is not an id."""
    head = args.split(" ", 1)[0]
    try:
        return unsign(int(head))
    except ValueError:
        return None


def _text_parts(run: str) -> list[dict]:
    """A plain run, split again on any URL in it. EQ2 leaves a typed address as
    text; the page makes it a link, so the split has to happen somewhere and
    doing it here means every reader of a message agrees on where the address
    starts and stops."""
    out: list[dict] = []
    pos = 0
    for m in _URL_RE.finditer(run):
        url = m.group(0).rstrip(_URL_TAIL)
        if not url:
            continue
        if m.start() > pos:
            out.append({"k": "t", "s": run[pos:m.start()]})
        out.append({"k": "url", "s": url})
        pos = m.start() + len(url)
    if pos < len(run):
        out.append({"k": "t", "s": run[pos:]})
    return out


def _guild_recruit(text: str) -> str | None:
    """The advertised guild name, only for a high-confidence recruiting line.

    This is intentionally a recognizer, not a guesser. Missing an unusually
    worded advert costs the rail one card; turning arbitrary bracketed text into
    a guild link teaches the page a false fact about somebody else's words.
    """
    if not _RECRUIT_CUE_RE.search(text):
        return None
    wrapped = _GUILD_WRAPPED_RE.match(text)
    if wrapped:
        return " ".join(wrapped.group("guild").split())
    bare = _GUILD_BARE_RE.match(text)
    if not bare:
        return None
    candidate = " ".join(bare.group("guild").split())
    if candidate.lower() in _NOT_GUILD_NAMES:
        return None
    rest = bare.group("rest")
    if not (re.search(r"\brecruit", rest, re.I)
            or re.search(r"\ba guild\b.*\bjoin", rest, re.I | re.S)
            or re.search(r"\blooking for\b.*\braid (?:force|roster)\b",
                         rest, re.I | re.S)):
        return None
    return candidate


def _guild_parts(parts: list[dict], guild: str | None) -> list[dict]:
    """Turn the first visible occurrence of a known guild into a link part.

    Whitespace in `[   Malazan  ]` is presentation, so words are joined with a
    whitespace regex when locating the name but the link label is normalized.
    """
    if not guild:
        return parts
    name_re = re.compile(r"\s+".join(map(re.escape, guild.split())), re.I)
    out = []
    linked = False
    for part in parts:
        if linked or part["k"] != "t":
            out.append(part)
            continue
        match = name_re.search(part["s"])
        if not match:
            out.append(part)
            continue
        before, after = part["s"][:match.start()], part["s"][match.end():]
        if before:
            out.append({"k": "t", "s": before})
        out.append({"k": "guild", "s": guild})
        if after:
            out.append({"k": "t", "s": after})
        linked = True
    return out


def _parts(text: str, guild: str | None = None) -> list[dict]:
    """Split a message into plain runs, link labels and URLs, so an item link
    can be drawn the way the game draws it instead of as bare text.

    The MARKUP comes off — nobody wants to read `\\aITEM 212446172 …` — but the
    item's id is KEPT on the part, because it is the Census id and it is what
    lets the card that already exists for a chest drop open over a link somebody
    posted in Auction. Everything else about the markup is noise and is dropped.
    """
    out: list[dict] = []
    pos = 0
    for m in _LINK_RE.finditer(text):
        if m.start() > pos:
            out.extend(_text_parts(text[pos:m.start()]))
        if m.group("kind") == "ITEM":
            part = {"k": "item", "s": m.group("label")}
            iid = _item_id(m.group("args"))
            if iid is not None:
                part["item"] = iid
            out.append(part)
        else:
            out.append({"k": "link", "s": m.group("label")})
        pos = m.end()
    if pos < len(text):
        out.extend(_text_parts(text[pos:]))
    return _guild_parts(out or [{"k": "t", "s": text}], guild)


def parse_chat(ts: int, body: str, logger: str) -> dict | None:
    """One prefix-stripped body -> a public chat message, or None. `logger` is
    the character whose log this is, and names the speaker on their own lines.

    `text` keeps EQ2's markup: it is what gets STORED, and stripping it here
    would throw away the item links for every reader who comes later."""
    m = _PC_RE.match(body)
    who = _speaker(m.group("who")) if m else logger
    if m is None:
        m = _SELF_RE.match(body)
        if m is None:
            return None
    chan = CHANNELS.get(m.group("chan"))
    if chan is None or chan[1] != int(m.group("num")):
        return None
    text = m.group("text")
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "…"
    return {"ts": ts, "ch": chan[0], "who": who, "text": text}


def _wire(row) -> dict:
    """A stored row -> what the page reads. The markup comes off HERE, on the
    way out, so the same row can be drawn differently later."""
    guild = _guild_recruit(row["text"])
    out = {"id": row["id"], "ts": row["ts"], "ch": row["ch"],
           "who": row["who"], "parts": _parts(row["text"], guild)}
    if guild:
        out["guild"] = guild
    return out


# ---------------------------------------------------------------------------
# the record, and the live tail of it

_lock = threading.Lock()
# The tail the SSE stream reads. Every message in here is also a row; the deque
# exists so a stream tick costs nothing, not as a second source of truth.
_rooms: dict[str, deque] = {k: deque(maxlen=BACKLOG) for k in CHANNEL_KEYS}
_contributors: dict[str, float] = {}    # character name -> last live batch
_last_id = 0                            # the newest row this process has written


def note_contributor(character: str) -> None:
    """A live batch arrived from this character. Called for every live batch,
    including one that carried no chat: "connected" means uploading, not
    talking."""
    with _lock:
        _contributors[character] = time.time()


def absorb(conn, lines, character: str, mode: str, now: int) -> int:
    """Read the public channels out of one ingest batch and file them. Returns
    how many messages were NEW — a line somebody else already relayed is not.

    Called inside the ingest batch's transaction (`live.process_batch`), so a
    batch that rolls back takes its chat with it."""
    if mode != "live":
        return 0
    note_contributor(character)

    fresh: list[dict] = []
    for line in lines:
        split = split_prefix(line)
        if split is None:
            continue
        ts, body = split
        if abs(ts - now) > MAX_LAG_S:
            continue
        msg = parse_chat(ts, body, character)
        if msg is not None:
            fresh.append(msg)
    if not fresh:
        return 0

    global _last_id
    added = []
    with _lock:
        for msg in fresh:
            # The whole zone logs the same General line and their clocks
            # disagree, so the dedupe is a WINDOW rather than the exact key —
            # see DEDUPE_WINDOW_S. Rows written earlier in this same loop are
            # visible to this read, so a batch is deduped against itself too.
            if conn.execute(
                    "SELECT 1 FROM chat_messages WHERE ch=? AND ts BETWEEN ? AND ? "
                    "AND who=? AND text=? LIMIT 1",
                    (msg["ch"], msg["ts"] - DEDUPE_WINDOW_S,
                     msg["ts"] + DEDUPE_WINDOW_S, msg["who"],
                     msg["text"])).fetchone():
                continue
            # OR IGNORE against UNIQUE(ts, ch, who, text) is the exact-match
            # backstop: two uploads racing the read above still collapse.
            cur = conn.execute(
                "INSERT OR IGNORE INTO chat_messages (ts, ch, who, text) "
                "VALUES (?,?,?,?)",
                (msg["ts"], msg["ch"], msg["who"], msg["text"]))
            if not cur.rowcount:
                continue
            msg["id"] = cur.lastrowid
            wire = _wire(msg)
            _rooms[msg["ch"]].append(wire)
            _last_id = max(_last_id, wire["id"])
            added.append(wire)
        _want_items(p["item"] for m in added for p in m["parts"] if "item" in p)
    if added:
        _ring()
    return len(added)


# ---------------------------------------------------------------------------
# naming the items people link
#
# An item linked in Auction is the same Census id a chest drop is, so it can
# wear the same examine card — but only once somebody has looked it up, and a
# lookup is an HTTP round trip. The same shape and the same reasoning as
# `live._roster_worker`: the ingest thread hands the ids over and returns, the
# fetch happens on a worker with its own connection and outside any
# transaction, and failing costs a chat line its hover card and nothing else.
#
# `items` is reference data about the GAME, so an id looked up because somebody
# linked it in Auction is looked up for good, and the raid that later drops it
# asks nothing.

_items_q: "queue.Queue[list[int]]" = queue.Queue()
_items_asked: set[int] = set()
_items_worker: threading.Thread | None = None
_items_worker_lock = threading.Lock()


def _want_items(item_ids) -> None:
    """Called with `_lock` held, on the ingest thread. Does no work."""
    if os.environ.get("CENSUS_AUTO_REFRESH", "1") == "0":
        return
    fresh = sorted(set(item_ids) - _items_asked)
    if not fresh:
        return
    _items_asked.update(fresh)
    _items_q.put(fresh)
    global _items_worker
    with _items_worker_lock:
        if _items_worker is None or not _items_worker.is_alive():
            _items_worker = threading.Thread(
                target=_items_loop, name="chat-items", daemon=True)
            _items_worker.start()


def _items_loop() -> None:
    import items

    from db import get_db
    while True:
        ids = _items_q.get()
        try:
            # `get_db` is thread-local, so this is the worker's own connection
            # and `ensure` writes in its own transaction — never the ingest
            # batch's, which by now has long since committed.
            items.ensure(get_db(), ids)
        except Exception:                              # noqa: BLE001
            logging.getLogger("chat").exception("chat item lookup failed")
        finally:
            _items_q.task_done()


def _live_contributors(now: float) -> list[str]:
    return [name for name, seen in _contributors.items()
            if now - seen <= CONTRIB_TTL_S]


def snapshot(since: int | None = None) -> dict:
    """The live TAIL — what the stream pushes, read from memory so a tick costs
    no query. A reader wanting the record calls `recent` or `history`.

    `connected` is the number of characters whose plugin has sent inside
    `CONTRIB_TTL_S`, recomputed on every read because nothing publishes "they
    stopped". It is no longer an empty state for the page — the box has an
    archive in it whether or not anybody is playing right now — it is what keeps
    the SSE connection warm through a proxy."""
    now = time.time()
    with _lock:
        channels = {}
        for key, room in _rooms.items():
            channels[key] = ([m for m in room if m["id"] > since]
                             if since is not None else list(room))
        return {
            "seq": _last_id,
            "channels": channels,
            "connected": len(_live_contributors(now)),
        }


def recent(conn, limit: int = OPENING) -> dict:
    """What a page opening on `/chat` is handed: the newest `limit` per channel
    out of the record, plus the cursor the stream continues from and the span
    the date filter is allowed to reach over.

    `seq` is the table's MAX(id) rather than the newest row returned — an empty
    channel must not send the stream back over messages the other two already
    have."""
    channels = {}
    for key in CHANNEL_KEYS:
        rows = conn.execute(
            "SELECT id, ts, ch, who, text FROM chat_messages WHERE ch=? "
            "ORDER BY ts DESC, id DESC LIMIT ?", (key, limit)).fetchall()
        channels[key] = [_wire(r) for r in reversed(rows)]
    span = conn.execute(
        "SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts, MAX(id) AS seq "
        "FROM chat_messages").fetchone()
    return {
        "seq": span["seq"] or 0,
        "channels": channels,
        "first_ts": span["first_ts"],
        "last_ts": span["last_ts"],
        "connected": len(_live_contributors(time.time())),
    }


def history(conn, ch: str, start_ts: int, end_ts: int) -> list[dict]:
    """One channel over one half-open window `[start_ts, end_ts)`. The window is
    the BROWSER's: a day means a day where the reader is sitting, and the server
    has no business guessing which midnight that was."""
    rows = conn.execute(
        "SELECT id, ts, ch, who, text FROM chat_messages "
        "WHERE ch=? AND ts>=? AND ts<? ORDER BY ts, id LIMIT ?",
        (ch, start_ts, end_ts, MAX_HISTORY)).fetchall()
    return [_wire(r) for r in rows]


def recruiting(conn) -> list[dict]:
    """Newest current recruiting pitch per guild, including its companion lines.

    Players often paste a three-line advert in one batch; EQ2 stamps those lines
    with the same second. Once one line names the guild, the other lines from
    that speaker in that second belong to the same pitch. Repeated adverts are
    collapsed to the newest one, so the rail is a guild directory rather than a
    wall of one guild's hourly macro.
    """
    cutoff = int(time.time()) - RECRUIT_DAYS * 86400
    rows = conn.execute(
        "SELECT id, ts, ch, who, text FROM chat_messages "
        "WHERE ch='general' AND ts>=? ORDER BY ts DESC, id DESC LIMIT ?",
        (cutoff, MAX_HISTORY)).fetchall()
    groups: dict[tuple[int, str], list] = {}
    for row in rows:
        groups.setdefault((row["ts"], row["who"].lower()), []).append(row)

    found = []
    seen = set()
    for row in rows:
        guild = _guild_recruit(row["text"])
        key = guild.lower() if guild else None
        if not guild or key in seen:
            continue
        seen.add(key)
        pitch = sorted(groups[(row["ts"], row["who"].lower())],
                       key=lambda r: r["id"])
        found.append({"guild": guild, "ts": row["ts"], "who": row["who"],
                      "messages": [_wire(r) for r in pitch]})
        if len(found) >= RECRUIT_N:
            break
    return found


# ---------------------------------------------------------------------------
# what the channel looked like (`GET /api/chat/stats`)
#
# Everything here is arithmetic over the four columns the table has — ts, ch,
# who, text. There is nothing else to reach for: no user, no character, no zone,
# no session, by design. So a "stat" is a count of speakers, of repeats, of
# clock hours or of words, and anything that would need to know WHO A SPEAKER IS
# is not available and is not faked.
#
# THE PANEL IS A SAMPLE AND SAYS SO. The archive holds what somebody's plugin
# relayed while they were logged in, so a quiet hour can mean a quiet server or
# it can mean nobody was uploading. A leaderboard implies completeness it does
# not have, which is why `Chat.jsx` prints the caveat under the panel rather
# than leaving the numbers to speak as if they were the server's own.

SPAM_MIN = 4         # messages before a repeat count means anything about a person
TOP_N = 8            # rows in a leaderboard, which is a narrow column
CLOUD_N = 36         # words in the cloud
FAME_MIN_NAME = 4    # a shorter name is too easy for a real word to collide with

# A word for the cloud: letters, apostrophes allowed inside, three or more. The
# text is lowercased first, so this deliberately never sees case.
_WORD_RE = re.compile(r"[a-z][a-z']{2,}")

# Function words and chat plumbing. This is NOT a content filter — "wts", "lfg",
# "pst" and "grp" are what these channels are FOR and are the most interesting
# thing a cloud can say about Auction. Only words that carry no channel meaning
# at all are dropped, because a cloud of "the/and/you" is a cloud of English.
STOPWORDS = frozenset("""
about after again all also am and any are aren aint arent as ask at back be
because been before being but buy can cant come could couldnt did didnt do does
doesnt doing don dont down each even ever every for from get gets getting give
go going good got gotta had hadnt has hasnt have havent having he her here hers
him his how ill im into is isnt it its ive just know let like ll lot made make
many may maybe me mean might mine more most much must my need no nope not now of
off oh ok okay on once one only or other our out over own please put re really
right said same say says see she should shouldnt so some still such sure take
tell than thanks thank that thats the their them then there these they thing
things think this those though thought through thx to too try up us use ve very
want was wasnt way we well went were weren what when where which while who whom
why will with wont would wouldnt ya yeah yep yes yet you your youre yours
""".split())


def _dupe_key(text: str) -> str:
    """The test for "the same thing again". Case and run-length of whitespace
    are noise; EVERYTHING ELSE IS KEPT, markup included, so two different item
    links are two different messages rather than one repeat."""
    return " ".join(text.lower().split())


def _plain(text: str) -> str:
    """The words somebody TYPED. `_parts` already splits a line into runs, URLs
    and link labels; only the plain runs come back, so a cloud is what was said
    and not a list of item names — an Auction cloud built from link labels would
    be one long shout of whatever was linked most, which the messages themselves
    never said."""
    return " ".join(p["s"] for p in _parts(text) if p["k"] == "t")


def _rows(conn, ch: str, start_ts, end_ts):
    """The window's rows, ITERATED rather than fetched. `stats` walks the table
    twice — the second pass needs the speaker list the first one builds — and
    over an all-time window that is an archive that should not be held in memory
    twice, or once."""
    if start_ts is None:
        return conn.execute(
            "SELECT ts, who, text FROM chat_messages WHERE ch=?", (ch,))
    return conn.execute(
        "SELECT ts, who, text FROM chat_messages WHERE ch=? AND ts>=? AND ts<?",
        (ch, start_ts, end_ts))


def stats(conn, ch: str, start_ts: int | None = None,
          end_ts: int | None = None) -> dict:
    """What one channel looked like over one window (all time if no window).

    THE CLOCK STAYS UNIX. Hours come back as a list of `[hour_start, count]`
    pairs and nothing here bins them into a day or into a time of day, because
    both of those are questions about where the READER is sitting — the same
    rule the date filter already follows. The browser folds the hours into its
    own local two-hour blocks and its own local days, which is also the only way
    the two days a year that are not 24 hours long come out right.
    """
    said: dict[str, int] = {}
    seen: dict[str, set] = {}
    hours: dict[int, int] = {}
    total = 0

    for ts, who, text in _rows(conn, ch, start_ts, end_ts):
        total += 1
        said[who] = said.get(who, 0) + 1
        seen.setdefault(who, set()).add(_dupe_key(text))
        bucket = ts - ts % 3600
        hours[bucket] = hours.get(bucket, 0) + 1

    # Second pass, now that there is a cast list. A mention is only a mention if
    # the name belongs to somebody who spoke in this window — the alternative is
    # matching every capitalised word against nothing.
    names = {w.lower(): w for w in said if len(w) >= FAME_MIN_NAME}
    all_names = {w.lower() for w in said}
    fame: dict[str, int] = {}
    words: dict[str, int] = {}

    for _ts, who, text in _rows(conn, ch, start_ts, end_ts):
        tokens = _WORD_RE.findall(_plain(text).lower())
        speaker = who.lower()
        for token in set(tokens):
            # once per message: saying a name three times in one sentence is
            # one mention of them, not three
            named = names.get(token)
            if named is not None and token != speaker:
                fame[named] = fame.get(named, 0) + 1
        for token in tokens:
            if token in STOPWORDS or token in all_names:
                continue
            words[token] = words.get(token, 0) + 1

    def top(counts: dict, n: int = TOP_N):
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    # A repeat is a message somebody had already sent: 40 messages with 3
    # distinct texts is 37 repeats. Ranked on that COUNT rather than on a ratio,
    # so the person reposting the same WTS all night outranks somebody who said
    # "wb" twice and would otherwise score a perfect 50%.
    repeats = {w: said[w] - len(seen[w]) for w in said if said[w] >= SPAM_MIN}
    repeats = {w: r for w, r in repeats.items() if r > 0}

    return {
        "ch": ch,
        "start": start_ts,
        "end": end_ts,
        "total": total,
        "speakers": len(said),
        "once": sum(1 for n in said.values() if n == 1),
        "talkers": [{"who": w, "n": n} for w, n in top(said)],
        "spammers": [{"who": w, "n": said[w], "unique": len(seen[w]),
                      "repeats": r} for w, r in top(repeats)],
        "fame": [{"who": w, "n": n} for w, n in top(fame)],
        "words": [{"w": w, "n": n} for w, n in top(words, CLOUD_N)],
        "hours": [[h, hours[h]] for h in sorted(hours)],
    }


# The panel is a full scan of a window, and the window a reader keeps asking for
# is "all time" — the same answer every time until somebody says something. So
# it is cached against the newest row id: while that has not moved, no line has
# landed in ANY channel and every window's answer is still exactly right. A new
# message drops the lot, which costs one rescan per channel somebody has open.
_STATS_CACHE: dict[tuple, dict] = {}
_STATS_CACHE_ID = -1
_STATS_CACHE_MAX = 48


def stats_cached(conn, ch: str, start_ts=None, end_ts=None) -> dict:
    global _STATS_CACHE_ID
    key = (ch, start_ts, end_ts)
    with _lock:
        if _STATS_CACHE_ID != _last_id or len(_STATS_CACHE) > _STATS_CACHE_MAX:
            _STATS_CACHE.clear()
            _STATS_CACHE_ID = _last_id
        hit = _STATS_CACHE.get(key)
    if hit is not None:
        return hit
    answer = stats(conn, ch, start_ts, end_ts)
    with _lock:
        _STATS_CACHE[key] = answer
    return answer


# ---------------------------------------------------------------------------
# the doorbell — the same shape as pipeline/livebus.py, and for the same
# reasons: batches are absorbed on an ingest thread and the streams live in the
# event loop, so a publisher rings each waiter through its own loop. There is
# one room here rather than one per session, because everybody watching /chat
# is watching the same thing.

_waiters: set["_Waiter"] = set()
_waiters_lock = threading.Lock()


class _Waiter:
    __slots__ = ("event", "loop")

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.loop = asyncio.get_running_loop()

    def ring(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.event.set)
        except RuntimeError:
            # the loop this stream lived in is gone and so is the stream
            pass


class Subscription:
    """Subscribe around the whole read-and-yield body, not just the wait — a
    message absorbed while the stream is serializing the last one has to leave
    the bell rung, or the push drops exactly the update that arrived under
    load."""

    def __enter__(self) -> "Subscription":
        self._waiter = _Waiter()
        with _waiters_lock:
            _waiters.add(self._waiter)
        return self

    def __exit__(self, *exc) -> None:
        with _waiters_lock:
            _waiters.discard(self._waiter)
        self._waiter = None
        return None

    async def wait(self, timeout: float) -> bool:
        """True if the bell rang, False on the timeout. The timeout is not a
        formality: it is what refreshes the connected count when the last
        uploader goes quiet, which is a change no publish announces."""
        try:
            await asyncio.wait_for(self._waiter.event.wait(), timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return False
        finally:
            self._waiter.event.clear()
        return True


def subscribe() -> Subscription:
    return Subscription()


def _ring() -> None:
    with _waiters_lock:
        waiters = list(_waiters)
    for w in waiters:
        w.ring()


def reset() -> None:
    """Tests only: empty the live tail. The record is the database's and is
    cleared the way any other table is."""
    global _last_id, _STATS_CACHE_ID
    with _lock:
        for room in _rooms.values():
            room.clear()
        _contributors.clear()
        _last_id = 0
        # the cache is keyed on the newest row id, and this winds that id
        # BACKWARDS — the one move that could hand a later call a stale answer
        _STATS_CACHE.clear()
        _STATS_CACHE_ID = -1
