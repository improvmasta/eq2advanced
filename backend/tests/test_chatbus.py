"""The public chat box (`/chat`).

Four things are being proved, and the first is the one that would hurt — more
so since v36, when this stopped being a relay and became a stored record:

  1. **Keeping public chat did not widen redaction.** The very lines this module
     files are still refused by `keep_line`, so nothing new lands on disk FROM A
     LOG. `test_redaction.py` owns that rule; this file pins the two together,
     because the failure mode is somebody "fixing" the inconsistency by making
     redaction agree with the box.
  2. **The channel test is default-deny.** A tell, guild chat, officer chat,
     /say, Crafting and a channel invented tomorrow all store nothing — the
     numbered channel shape and an allowlisted name both have to match. The
     number itself varies per character and is not identity. This is the only
     thing between a private line and a permanent row.
  3. **One line said once.** Everybody in the zone logs the same General line;
     the box keeps it once, and the collapse is the table's so it holds across
     a restart.
  4. **The record outlives the process.** What a page opens on comes out of
     `chat_messages`, not out of the deque a restart empties, and the date
     filter reaches a window the live tail has long since dropped.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from pipeline import chatbus
from pipeline.redact import keep_line
from tools import recover_chat


def line(body: str, when: int | None = None) -> str:
    ts = int(time.time()) if when is None else when
    return f"({ts})[Sat Aug  1 20:30:42 2026] {body}"


PUBLIC = [
    '\\aPC -1 Evoxx:Evoxx\\/a tells General (7), "anyone up for Unrest"',
    # Real failure shape: LFG was `(3)` in one character's log and `(4)` in
    # another. Channel slots are per character, not server-wide identifiers.
    '\\aPC -1 Xinbuckler:Xinbuckler\\/a tells LFG (4), '
    '"Anyone doing a late night SOF run that a 70 Swashy can get in on?"',
    '\\aPC -1 Evoxx:Evoxx\\/a tells Auction (12), "WTB fabled"',
    'You tell General (9), "over here"',
]

NOT_PUBLIC = [
    '\\aPC -1 Moklok:Moklok\\/a tells you, "meet me at the docks"',
    'You tell Ellea, "don\'t tell anyone"',
    '\\aPC -1 Spades:Spades\\/a says to the guild, "guild bank is short"',
    '\\aPC -1 Spades:Spades\\/a says to the officer channel, "about that member"',
    '\\aPC 76623932 Rando:Rando\\/a says, "local chatter"',
    'You say, "Some how I dropped from the group."',
    '\\aPC -1 Aros:Aros\\/a says to the group, "pull in 5"',
    '\\aPC -1 Crafty:Crafty\\/a tells Crafting (6), "WTS adornments"',
    # A channel nobody anticipated still fails even though it has the same
    # numbered channel shape as the three public channels.
    '\\aPC -1 Nobody:Nobody\\/a tells Therapy (77), "my private business"',
]


@pytest.fixture(scope="module", autouse=True)
def tmpdb(tmp_path_factory):
    """A database, because the box is a table now. Module-scoped and applied
    before anything else here runs, so no test can reach the real one."""
    tmp = tmp_path_factory.mktemp("eq2adv-chat")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    dbmod._local.conn = None
    dbmod.init_db()
    yield tmp
    dbmod._local.conn = None
    mp.undo()


@pytest.fixture(autouse=True)
def clean(tmpdb):
    conn = dbmod.get_db()
    chatbus.reset()
    conn.execute("DELETE FROM chat_messages")
    conn.commit()
    yield
    chatbus.reset()
    # `absorb` never commits — in production it runs inside the ingest batch's
    # transaction (`live.process_batch`), and a test that walked away holding
    # that write lock would lock out the TestClient's own threads.
    conn.commit()


@pytest.fixture
def conn():
    return dbmod.get_db()


def test_public_channels_are_kept(conn):
    now = int(time.time())
    chatbus.absorb(conn, [line(b) for b in PUBLIC], "Bobby", "live", now)
    snap = chatbus.snapshot()
    assert [m["who"] for m in snap["channels"]["general"]] == ["Evoxx", "Bobby"]
    assert [m["who"] for m in snap["channels"]["lfg"]] == ["Xinbuckler"]
    assert [m["who"] for m in snap["channels"]["auction"]] == ["Evoxx"]


def test_everything_else_is_kept_nowhere(conn):
    now = int(time.time())
    assert chatbus.absorb(conn, [line(b) for b in NOT_PUBLIC], "Bobby", "live", now) == 0
    assert all(not msgs for msgs in chatbus.snapshot()["channels"].values())
    # and nothing reached the table either — the row is the thing that lasts
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0


def test_keeping_chat_did_not_widen_redaction():
    """The point of the whole design: every line the box SHOWS is still a line
    the log store refuses. The two sets are unrelated on purpose — group and
    raid chat go the other way round (kept from a log, never shown here) — so
    the invariant worth pinning is that nothing became storable FROM A LOG by
    becoming visible."""
    for body in PUBLIC:
        assert keep_line(line(body)) is False, body


def test_one_line_from_two_uploaders_is_one_message(conn):
    now = int(time.time())
    heard = line('\\aPC -1 Evoxx:Evoxx\\/a tells General (2), "anyone up"')
    assert chatbus.absorb(conn, [heard], "Bobby", "live", now) == 1
    assert chatbus.absorb(conn, [heard], "Ellea", "live", now) == 0
    assert len(chatbus.snapshot()["channels"]["general"]) == 1
    # …but both of them count as connected
    assert chatbus.snapshot()["connected"] == 2
    # the collapse is the table's now, so it holds for a third uploader
    # arriving after a restart emptied the tail
    chatbus.reset()
    assert chatbus.absorb(conn, [heard], "Twissted", "live", now) == 0
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 1


def test_two_uploaders_whose_clocks_disagree_is_one_message(conn):
    """The bug the stored box made visible. Every player's EQ2 client stamps the
    line off ITS OWN machine clock, so the same sentence lands a second or two
    apart and an exact-key dedupe keeps both copies."""
    now = int(time.time())
    body = '\\aPC -1 Jayashae:Jayashae\\/a tells General (2), "It\'s in Halas."'
    for skew in (0, 1, 2, -3):
        chatbus.absorb(conn, [line(body, when=now + skew)], f"Up{skew}", "live", now)
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 1

    # …and saying the same thing again later is NOT a duplicate: an auction
    # spammer reposting is real chat, and the window is what tells them apart
    later = now + chatbus.DEDUPE_WINDOW_S + 1
    chatbus.absorb(conn, [line(body, when=later)], "Bobby", "live", later)
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 2


def test_a_batch_is_deduped_against_itself(conn):
    """One uploader, one batch, the same line twice a second apart — the window
    read has to see rows written earlier in its own loop."""
    now = int(time.time())
    body = '\\aPC -1 Evoxx:Evoxx\\/a tells Auction (10), "WTS fabled"'
    assert chatbus.absorb(
        conn, [line(body, when=now), line(body, when=now + 1)],
        "Bobby", "live", now) == 1


def test_backfill_and_history_are_kept_nowhere(conn):
    now = int(time.time())
    body = '\\aPC -1 Evoxx:Evoxx\\/a tells General (2), "anyone up"'
    assert chatbus.absorb(conn, [line(body)], "Bobby", "backfill", now) == 0
    # a live-mode client replaying an old log: the clock says it is not now, so
    # it must not be filed under now either
    old = now - 5 * 3600
    assert chatbus.absorb(conn, [line(body, when=old)], "Bobby", "live", now) == 0
    assert chatbus.snapshot()["channels"]["general"] == []
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0


def test_original_log_recovery_is_bounded_preview_first_and_idempotent(conn, tmpdb):
    start = 1786942800  # 2026-08-17 01:00:00 EDT
    original = tmpdb / "eq2log_Ross.txt"
    original.write_text("\n".join([
        line('\\aPC -1 TooEarly:TooEarly\\/a tells LFG (4), "before"', start - 1),
        line('\\aPC -1 Xinbuckler:Xinbuckler\\/a tells LFG (4), '
             '"Anyone doing a late night SOF run that a 70 Swashy can get in on?"',
             1786943366),
        line('You tell General (8), "self line"', 1786943400),
        line('\\aPC -1 Crafty:Crafty\\/a tells Crafting (6), "private channel"',
             1786943410),
        line('\\aPC -1 Friend:Friend\\/a tells you, "private tell"', 1786943420),
    ]) + "\n")

    preview = recover_chat.recover_paths(
        conn, [original], start, start + 3600, apply=False)
    assert preview["public_candidates"] == 2
    assert preview["would_insert"] == 2
    assert preview["slots"] == {"General (8)": 1, "LFG (4)": 1}
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0

    with conn:
        applied = recover_chat.recover_paths(
            conn, [original], start, start + 3600, apply=True)
    assert applied["would_insert"] == 2
    rows = conn.execute(
        "SELECT ch,who FROM chat_messages ORDER BY ts").fetchall()
    assert [tuple(row) for row in rows] == [("lfg", "Xinbuckler"),
                                            ("general", "Ross")]

    again = recover_chat.recover_paths(
        conn, [original], start, start + 3600, apply=False)
    assert again["would_insert"] == 0
    assert again["duplicates"] == 2


def test_item_links_become_labels(conn):
    now = int(time.time())
    chatbus.absorb(conn, [line(
        '\\aPC -1 Evoxx:Evoxx\\/a tells Auction (10), '
        '"WTB \\aITEM 212446172 1534185667 0 0 0:Tunare\'s Wrath: Fingers\\/a pst"'
    )], "Bobby", "live", now)
    labels = [{"k": "t", "s": "WTB "},
              # the id is KEPT: it is the Census item id, which is what lets the
              # link wear the same examine card a chest drop does
              {"k": "item", "s": "Tunare's Wrath: Fingers", "item": 212446172},
              {"k": "t", "s": " pst"}]
    assert chatbus.snapshot()["channels"]["auction"][0]["parts"] == labels
    # the MARKUP is what was stored; the labels are made on the way out, so a
    # reader a year later gets the link and not a flattened string
    assert "\\aITEM" in conn.execute(
        "SELECT text FROM chat_messages").fetchone()["text"]
    assert chatbus.recent(conn)["channels"]["auction"][0]["parts"] == labels


def test_a_signed_item_id_is_the_census_id(conn):
    """`items.unsign` is the whole reason a linked item can be looked up: the
    log writes the Census id signed, and `pipeline/loot.py` reads it the same
    way off a chest line."""
    now = int(time.time())
    chatbus.absorb(conn, [line(
        '\\aPC -1 Evoxx:Evoxx\\/a tells Auction (10), '
        '"WTS \\aITEM -1813422462 -590025310:Hoop of War\\/a"')],
        "Bobby", "live", now)
    part = chatbus.snapshot()["channels"]["auction"][0]["parts"][-1]
    assert part == {"k": "item", "s": "Hoop of War", "item": 2481544834}


def test_trade_word_can_touch_an_item_link(conn):
    """The client treats the split itself as the boundary after WTS/WTB."""
    now = int(time.time())
    chatbus.absorb(conn, [line(
        '\\aPC -1 Evoxx:Evoxx\\/a tells Auction (10), '
        '"WTS\\aITEM 1390406218 8681493 0 0 0:Cloak of the Di\'Zok\\/a"')],
        "Bobby", "live", now)
    assert chatbus.snapshot()["channels"]["auction"][0]["parts"] == [
        {"k": "t", "s": "WTS"},
        {"k": "item", "s": "Cloak of the Di'Zok", "item": 1390406218},
    ]


def test_typed_addresses_become_url_parts(conn):
    """EQ2 leaves a typed address as text; the page makes it a link, so the
    split has to happen where every reader of the message agrees on it."""
    now = int(time.time())
    chatbus.absorb(conn, [line(
        '\\aPC -1 Evoxx:Evoxx\\/a tells General (2), '
        '"parses at https://eq2advanced.com/chat, or www.eq2advanced.com."')],
        "Bobby", "live", now)
    parts = chatbus.snapshot()["channels"]["general"][0]["parts"]
    assert parts == [
        {"k": "t", "s": "parses at "},
        {"k": "url", "s": "https://eq2advanced.com/chat"},
        # the comma and the full stop are SENTENCE, not address
        {"k": "t", "s": ", or "},
        {"k": "url", "s": "www.eq2advanced.com"},
        {"k": "t", "s": "."},
    ]


@pytest.mark.parametrize(("text", "guild"), [
    ("<Super Best Friends> EU/UK Guild are recruiting!", "Super Best Friends"),
    ("(Ctrl Alt Defeat) are recruiting for our raid force.", "Ctrl Alt Defeat"),
    ("[   Malazan  ] level 91 guild > PST for invite", "Malazan"),
    # `Is Looking` is sentence grammar with enthusiastic capitalization, not
    # part of the guild's name.
    ("Revelation Is Looking for A Coercer to Fill our Raid Force", "Revelation"),
    ("Court of Thorns is a guild. You should join.", "Court of Thorns"),
])
def test_recruiting_formats_name_the_guild(text, guild):
    assert chatbus._guild_recruit(text) == guild


@pytest.mark.parametrize("text", [
    "[Tunare's Wrath] would look good in the guild bank",
    "Revelation is looking for a healer for Unrest",
    "Raid roster is currently full but we recruit internally",
    "<Megalith> that was hilarious",
])
def test_ordinary_chat_is_not_a_guild_advert(text):
    assert chatbus._guild_recruit(text) is None


def test_recruiting_collects_one_latest_multiline_pitch_per_guild(conn):
    now = int(time.time())
    # An older repeat of the macro is collapsed under the newest one.
    put(conn, "Crusin", "<Super Best Friends> Guild recruiting!", now - 60)
    put(conn, "Crusin", "<Super Best Friends> EU/UK Guild are recruiting!", now)
    put(conn, "Crusin", "Raid roster is full; looking for backups.", now)
    put(conn, "Crusin", "Raiding Sun/Weds. PST for more info.", now)
    put(conn, "Oldtimer", "<Gone Quiet> Guild recruiting!", now - 4 * 86400)
    got = chatbus.recruiting(conn)
    assert len(got) == 1
    assert got[0]["guild"] == "Super Best Friends"
    assert len(got[0]["messages"]) == 3
    guild_parts = [p for p in got[0]["messages"][0]["parts"] if p["k"] == "guild"]
    assert guild_parts == [{"k": "guild", "s": "Super Best Friends"}]


def test_connected_expires(conn):
    now = int(time.time())
    chatbus.absorb(conn, [], "Bobby", "live", now)
    assert chatbus.snapshot()["connected"] == 1
    # nothing publishes "they stopped"; the count is recomputed on every read
    chatbus._contributors["Bobby"] = time.time() - chatbus.CONTRIB_TTL_S - 1
    assert chatbus.snapshot()["connected"] == 0


def test_since_returns_only_the_new(conn):
    now = int(time.time())
    chatbus.absorb(conn, [line('\\aPC -1 A:A\\/a tells General (2), "one"')],
                   "Bobby", "live", now)
    seq = chatbus.snapshot()["seq"]
    chatbus.absorb(conn, [line('\\aPC -1 B:B\\/a tells General (2), "two"')],
                   "Bobby", "live", now)
    fresh = chatbus.snapshot(since=seq)["channels"]["general"]
    assert [p["s"] for m in fresh for p in m["parts"]] == ["two"]


def test_the_record_outlives_the_tail(conn):
    """What a restart used to cost. `reset()` is this process losing everything
    it held in memory; the page opening afterwards still reads the night."""
    now = int(time.time())
    chatbus.absorb(conn, [line('\\aPC -1 A:A\\/a tells General (2), "still here"')],
                   "Bobby", "live", now)
    chatbus.reset()
    assert chatbus.snapshot()["channels"]["general"] == []
    got = chatbus.recent(conn)
    assert [p["s"] for m in got["channels"]["general"] for p in m["parts"]] == ["still here"]
    # and the stream carries on from the table, not from a counter that restarted
    assert got["seq"] == conn.execute("SELECT MAX(id) FROM chat_messages").fetchone()[0]


def test_history_is_the_window_the_reader_asked_for(conn):
    """The date filter's whole job: reach a day the live tail never had."""
    now = int(time.time())
    day = now - 6 * 86400
    for when, what in ((day, "old news"), (now, "tonight")):
        # absorbed as live at ITS OWN wall clock — a week ago this was now
        chatbus.absorb(
            conn, [line(f'\\aPC -1 A:A\\/a tells General (2), "{what}"', when=when)],
            "Bobby", "live", when)
    said = [p["s"] for m in chatbus.history(conn, "general", day - 60, day + 60)
            for p in m["parts"]]
    assert said == ["old news"]
    # a window with nothing in it is an empty answer, never the whole table
    assert chatbus.history(conn, "general", day + 3600, day + 7200) == []


# --- the Stats panel -------------------------------------------------------
#
# Written straight to the table rather than through `absorb`, which only accepts
# a line whose clock is within two minutes of now — these are questions about a
# window, and a window has to be allowed to be last Tuesday.

def put(conn, who, text, ts, ch="general"):
    conn.execute("INSERT OR IGNORE INTO chat_messages (ts, ch, who, text) "
                 "VALUES (?, ?, ?, ?)", (ts, ch, who, text))


HOUR = 1786723200 - 1786723200 % 3600


def test_stats_counts_people_and_what_they_repeated(conn):
    for i in range(6):
        put(conn, "Spammer", "WTS fabled pst", HOUR + i * 60)
    for i in range(4):
        put(conn, "Chatty", f"thought number {i}", HOUR + i * 60)
    put(conn, "Quiet", "hello", HOUR)

    d = chatbus.stats(conn, "general")
    assert d["total"] == 11
    assert d["speakers"] == 3
    assert d["once"] == 1
    assert [r["who"] for r in d["talkers"]] == ["Spammer", "Chatty", "Quiet"]
    # A repeat is a message somebody had already sent — five of those six were.
    # Chatty said four different things and so is not on this board at all,
    # which is the distinction the panel exists to draw.
    assert d["spammers"] == [
        {"who": "Spammer", "n": 6, "unique": 1, "repeats": 5}]


def test_fame_is_being_named_by_somebody_else(conn):
    put(conn, "Zylphax", "zylphax is the best zylphax", HOUR)
    put(conn, "Evoxx", "zylphax carried that pull", HOUR + 1)
    put(conn, "Bobby", "agreed, Zylphax Zylphax Zylphax", HOUR + 2)
    # once per MESSAGE, and never your own name: saying it three times in one
    # sentence is one mention, and talking about yourself is not fame
    assert chatbus.stats(conn, "general")["fame"] == [{"who": "Zylphax", "n": 2}]


def test_the_cloud_is_words_and_not_names_or_links(conn):
    put(conn, "Evoxx", "WTB \\aITEM 212446172 0 0 0 0:Tunare's Wrath: Fingers"
                       "\\/a for the guild bank", HOUR)
    put(conn, "Evoxx", "wtb another one for the bank", HOUR + 60)
    put(conn, "Bobby", "evoxx has the bank covered", HOUR + 120)

    words = {w["w"]: w["n"] for w in chatbus.stats(conn, "general")["words"]}
    assert words["bank"] == 3
    # the channel's own vocabulary is the most interesting thing a cloud can
    # say about Auction, so it is never filtered
    assert words["wtb"] == 2
    assert "the" not in words       # a cloud of English is not a cloud
    assert "evoxx" not in words     # a person is the fame board, not a word
    assert "tunare" not in words    # a link label is a link, not something said


def test_stats_leaves_the_clock_alone(conn):
    """Hours come back as unix hours and nothing folds them into a day or a
    time of day, because both are questions about where the READER is sitting —
    the rule the date filter already follows."""
    put(conn, "A", "one", HOUR + 5)
    put(conn, "A", "two", HOUR + 10)
    put(conn, "B", "three", HOUR + 7200 + 5)
    assert chatbus.stats(conn, "general")["hours"] == [[HOUR, 2], [HOUR + 7200, 1]]


def test_stats_counts_one_channel_over_one_half_open_window(conn):
    put(conn, "A", "general line", HOUR)
    put(conn, "A", "auction line", HOUR, ch="auction")
    put(conn, "A", "the next hour", HOUR + 3600)
    assert chatbus.stats(conn, "auction")["total"] == 1
    assert chatbus.stats(conn, "general")["total"] == 2
    # `end` is exclusive, the same as `history` — the later line belongs to the
    # next window and must not be counted in both
    assert chatbus.stats(conn, "general", HOUR, HOUR + 3600)["total"] == 1


def test_the_count_is_cached_only_until_somebody_speaks(conn):
    """The cache is keyed on the newest row id, so a live box that is counting
    ALL TIME cannot go on showing last minute's answer."""
    now = int(time.time())
    chatbus.absorb(conn, [line('\\aPC -1 A:A\\/a tells General (2), "one"')],
                   "Bobby", "live", now)
    assert chatbus.stats_cached(conn, "general")["total"] == 1
    chatbus.absorb(conn, [line('\\aPC -1 B:B\\/a tells General (2), "two"')],
                   "Bobby", "live", now)
    assert chatbus.stats_cached(conn, "general")["total"] == 2


# --- the API ---------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmpdb):
    from main import app
    with TestClient(app) as c:
        yield c


def test_chat_needs_no_account(client):
    """It used to be 401 without one. The record has no user in it and every
    line was broadcast to a whole server by the game, so there is nothing here
    to gate — what an account decides is who FILLS it."""
    body = client.get("/api/chat/recent").json()
    assert body["connected"] == 0
    assert set(body["channels"]) == set(chatbus.CHANNEL_KEYS)
    assert body["first_ts"] is None      # an empty archive has no span to pick in


def test_history_is_open_but_the_window_is_not(client):
    day = int(time.time()) - 86400
    assert client.get(f"/api/chat/history?ch=general&start={day}&end={day + 86400}"
                      ).status_code == 200
    # a channel nobody keeps is not a channel you can read
    assert client.get(f"/api/chat/history?ch=officer&start={day}&end={day + 1}"
                      ).status_code == 404
    # and one request cannot ask for the whole table
    assert client.get("/api/chat/history?ch=general&start=0&end=9999999999"
                      ).status_code == 400


def test_status_is_the_light_and_nothing_else(client):
    """What the header polls on every page. One number, so a light on a door
    does not cost three channels of messages to ask for."""
    assert client.get("/api/chat/status").json() == {"connected": 0}


def test_stats_is_open_but_its_window_is_not(client):
    # no window is a real argument and means all time
    assert client.get("/api/chat/stats?ch=general").status_code == 200
    assert client.get("/api/chat/stats?ch=officer").status_code == 404
    assert client.get("/api/chat/stats?ch=general&start=0&end=9999999999"
                      ).status_code == 400
    # half a window is not a window
    day = int(time.time()) - 86400
    assert client.get(f"/api/chat/stats?ch=general&start={day}").status_code == 400


def test_an_item_card_is_a_read_and_needs_no_account(client):
    """The hover behind a linked item. Unresolved answers `null` rather than
    reaching for Census on a request thread — resolving is the worker's job."""
    body = client.get("/api/items/212446172/card").json()
    assert body == {"card": None}
