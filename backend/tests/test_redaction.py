"""Private chat never reaches disk.

Two things are being proved here, and the second is the one that would hurt if it
broke silently:

  1. The right lines are dropped — and, because the classifier is DEFAULT-DENY, a
     channel nobody anticipated is dropped too.
  2. Redaction changes no parsed number. That holds by construction (redact only
     governs the set classify_body already returns None for), and
     `test_parse_is_identical_with_and_without_chat` pins it against drift.
"""

import gzip
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from pipeline.redact import Redactor, StreamRedactor, channel_of, keep_line

TS = 1722556800


def line(offset: int, body: str) -> str:
    return f"({TS + offset})[Thu Aug  1 21:00:00 2026] {body}\r\n"


PRIVATE = [
    '\\aPC -1 Moklok:Moklok\\/a tells you, "meet me at the docks"',
    "You tell Ellea, \"don't tell anyone\"",
    '\\aPC -1 Spades:Spades\\/a says to the guild, "guild bank is short"',
    '\\aPC -1 Spades:Spades\\/a says to the officer channel, "about that member"',
    '\\aPC -1 Evoxx:Evoxx\\/a tells LFG (3), "looking for group"',
    '\\aPC -1 Evoxx:Evoxx\\/a tells General (2), "anyone up"',
    '\\aPC -1 Evoxx:Evoxx\\/a tells Auction (10), "WTB fabled"',
    '\\aPC 76623932 Rando:Rando\\/a says, "local chatter"',
    'You say, "Some how I dropped from the group."',
    # a channel that does not exist today — default-deny has to catch it anyway
    '\\aPC -1 Nobody:Nobody\\/a tells Therapy (77), "my private business"',
    '\\aPC -1 Nobody:Nobody\\/a whispers something new, "unanticipated"',
]

RETAINED = [
    '\\aPC 58070227 Aros:Aros\\/a says to the group, "pull in 5"',
    '\\aPC -1 Ellea:Ellea\\/a says to the raid party, "cures on the tank"',
    'You say to the group, "lifeburn"',
    'You say to the raid party, "rezzing"',
    '\\aNPC 57166 Zylphax:Zylphax\\/a says, "Come here!"',
    '\\aNPC 57166 Zylphax:Zylphax\\/a says in Thexian, "N\'Tal!"',
    "\\aPC 68371 Ellea:Ellea\\/a blesses Spades with their ancestor's knowledge [Ancestry].",
]

COMBAT = [
    "You have entered The Estate of Unrest.",
    "YOU hit a training dummy for 100 crushing damage.",
    "You have killed a training dummy.",
    "You prepare the Teachings of the Underworld.",
    "Beaux shouts a taunt at your enemy.",
]


@pytest.mark.parametrize("body", PRIVATE)
def test_private_channels_are_dropped(body):
    assert channel_of(body) == "private"
    assert not keep_line(line(0, body))


@pytest.mark.parametrize("body", RETAINED)
def test_group_raid_and_npc_are_kept(body):
    assert channel_of(body) in ("group", "raid", "npc", "flavor")
    assert keep_line(line(0, body))


@pytest.mark.parametrize("body", COMBAT)
def test_combat_lines_are_not_governed_at_all(body):
    assert channel_of(body) is None
    assert keep_line(line(0, body))


def test_unparseable_lines_are_kept():
    """A line with no timestamp prefix is not chat, and dropping it would eat log
    damage rather than protect anyone."""
    assert keep_line("garbage with no prefix\r\n")
    assert keep_line("\r\n")


def test_stream_redactor_matches_line_redactor_across_chunk_boundaries():
    """The upload path filters a byte stream it receives in arbitrary pieces, so
    a line split across two reads must still be classified as one line."""
    lines = [line(i, b) for i, b in enumerate(PRIVATE + RETAINED + COMBAT)]
    blob = "".join(lines).encode()
    expected = "".join(x for x in lines if keep_line(x)).encode()

    for size in (1, 7, 64, 1000, len(blob)):
        r = StreamRedactor()
        out = bytearray()
        for i in range(0, len(blob), size):
            out += r.feed(blob[i:i + size])
        out += r.finish()
        assert bytes(out) == expected, f"chunk size {size}"
        assert r.dropped == len(PRIVATE)


def test_stream_redactor_handles_a_missing_final_newline():
    body = line(0, COMBAT[1]).rstrip("\r\n")
    r = StreamRedactor()
    assert r.feed(body.encode()) == b""
    assert r.finish() == body.encode()


def test_redactor_counts():
    r = Redactor()
    kept = list(r.filter(line(i, b) for i, b in enumerate(PRIVATE + RETAINED)))
    assert len(kept) == len(RETAINED)
    assert (r.kept, r.dropped) == (len(RETAINED), len(PRIVATE))


def test_parse_is_identical_with_and_without_chat():
    """The invariant the whole design rests on: chat contributes no events, so
    redacting it cannot move a number."""
    from parser import parse_lines

    combat = [line(i, b) for i, b in enumerate(COMBAT)]
    noisy = combat + [line(100 + i, b) for i, b in enumerate(PRIVATE + RETAINED)]

    clean_events = list(parse_lines(iter(combat), "Bobby"))
    noisy_events = list(parse_lines(iter(sorted(noisy)), "Bobby"))
    assert [(e.ts, e.type, e.ability, e.amount) for e in clean_events] == \
           [(e.ts, e.type, e.ability, e.amount) for e in noisy_events]


# --- end to end, through the real upload endpoint ------------------------------

LOG = "".join([
    line(0, "You have entered The Estate of Unrest."),
    line(1, PRIVATE[0]),                       # a tell, before the fight
    line(2, RETAINED[0]),                      # group chat, before the fight
    line(3, "YOU hit a training dummy for 100 crushing damage."),
    line(4, RETAINED[1]),                      # raid chat, mid fight
    line(5, PRIVATE[2]),                       # guild chat, mid fight
    line(6, "YOU hit a training dummy for 120 crushing damage."),
    line(7, "You have killed a training dummy."),
    line(10000, RETAINED[2]),                  # group chat hours later, no fight
])


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-redaction")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    import routers.uploads_api as uploads_api
    mp.setattr(uploads_api, "UPLOADS_DIR", tmp / "uploads")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as c:
        c.post("/api/auth/register", json={"username": "redact",
                                           "password": "hunter2hunter2",
                                           "sq_id": 1, "answer": "pet"})
        yield c, tmp
    mp.undo()


def wait_ready(c, sid):
    for _ in range(60):
        s = c.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] in ("ready", "error"):
            assert s["status"] == "ready", s["error"]
            return s
        time.sleep(0.1)
    raise AssertionError("parse never finished")


def test_stored_upload_holds_no_private_chat(client):
    c, tmp = client
    sid = c.post("/api/uploads", files={"file": ("a.txt", LOG.encode())},
                 data={"character_name": "Bobby"}).json()["session_id"]
    s = wait_ready(c, sid)

    stored = Path(tmp / "uploads") / f"{s['upload_sha256']}.txt.gz"
    with gzip.open(stored, "rt", encoding="utf-8") as fh:
        body = fh.read()

    assert "meet me at the docks" not in body, "a tell survived to disk"
    assert "guild bank is short" not in body, "guild chat survived to disk"
    assert "cures on the tank" in body, "raid chat during a fight was lost"
    assert "pull in 5" in body, "group chat near the fight was lost"
    assert "You have killed a training dummy." in body, "combat text was eaten"

    # the parse is unharmed by any of it
    detail = c.get(f"/api/sessions/{sid}").json()
    assert detail["encounters"], "the fight parsed"
    assert s["redacted_lines"] >= 2


def test_chat_outside_any_fight_is_trimmed(client):
    """`RETAINED[2]` is group chat hours after the last fight — the right channel,
    but not about a fight, so `trim_to_fights` takes it."""
    c, tmp = client
    conn = dbmod.get_db()
    row = conn.execute(
        "SELECT id, upload_sha256, redacted_lines FROM sessions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    stored = Path(tmp / "uploads") / f"{row['upload_sha256']}.txt.gz"
    with gzip.open(stored, "rt", encoding="utf-8") as fh:
        body = fh.read()
    assert "lifeburn" not in body, "out-of-fight group chat was retained"
    assert "cures on the tank" in body, "in-fight raid chat was trimmed by mistake"


def test_content_address_is_the_original_bytes(client):
    """Dedupe still works: the hash is of what was sent, not of what was kept, so
    two raiders uploading the same night land on one file."""
    import hashlib
    c, _ = client
    conn = dbmod.get_db()
    row = conn.execute(
        "SELECT upload_sha256 FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    assert row["upload_sha256"] == hashlib.sha256(LOG.encode()).hexdigest()
