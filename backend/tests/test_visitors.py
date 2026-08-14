"""Counting readers (`visitors.py`, `visit_days` v37).

Three things are being proved, and the first is the one that matters:

  1. **A visitor id is scoped to its day and cannot outlive it.** The salt is
     per day and is deleted, so the same reader on two days is two unrelated
     hashes — the table can count people and cannot follow one. That is the
     property the whole design exists to have, and a "fix" that made repeat
     visitors trackable would be the regression.
  2. **A page load is somebody arriving.** Bots are not counted, assets are not
     counted, and the same person clicking around all evening is ONE visitor
     with several hits.
  3. **The admin answer is a count, never a list.** There is no route from a
     visit to a person, because there is nothing in the row to be a person.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
import visitors

AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


@pytest.fixture(scope="module", autouse=True)
def tmpdb(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-visits")
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
    conn.execute("DELETE FROM visit_days")
    conn.execute("DELETE FROM visit_salts")
    conn.commit()
    visitors._salts.clear()
    visitors._swept = ""
    yield


@pytest.fixture
def conn():
    return dbmod.get_db()


def rows(conn):
    return conn.execute("SELECT * FROM visit_days ORDER BY day").fetchall()


def test_one_person_clicking_around_is_one_visitor(conn):
    for path in ("", "chat", "zones/4", "chat"):
        visitors.note(conn, "10.1.1.9", AGENT, path, False)
    got = rows(conn)
    assert len(got) == 1
    assert got[0]["hits"] == 4
    # …and the flags only ever go up: they opened /chat on the second load and
    # a later page must not erase that
    assert got[0]["chat"] == 1
    assert got[0]["signed_in"] == 0


def test_signing_in_later_does_not_unsay_the_visit(conn):
    visitors.note(conn, "10.1.1.9", AGENT, "login", False)
    visitors.note(conn, "10.1.1.9", AGENT, "", True)
    got = rows(conn)
    assert len(got) == 1 and got[0]["signed_in"] == 1


def test_different_people_are_different_rows(conn):
    visitors.note(conn, "10.1.1.9", AGENT, "chat", False)
    visitors.note(conn, "10.1.1.10", AGENT, "chat", False)
    visitors.note(conn, "10.1.1.9", AGENT + " Edge/120", "chat", False)
    assert len(rows(conn)) == 3


def test_the_address_is_never_stored(conn):
    """The row is a hash and nothing else. An admin reading this table with a
    SQL client still cannot see who came."""
    visitors.note(conn, "203.0.113.44", AGENT, "chat", False)
    row = rows(conn)[0]
    assert "203.0.113.44" not in " ".join(str(v) for v in tuple(row))
    assert len(row["visitor"]) == 64      # sha256 hex, not an address


def test_a_visitor_id_cannot_survive_its_day(conn):
    """THE point of the design. Two days, one reader, and nothing in the table
    can line the two rows up — because the salt that made Monday's hash is
    gone, so Monday's id cannot be recomputed even with the address in hand."""
    now = time.time()
    monday = visitors.today(now - 4 * 86400)
    conn.execute("INSERT INTO visit_salts (day, salt) VALUES (?, ?)",
                 (monday, "monday-salt"))
    old = visitors._visitor("monday-salt", "10.1.1.9", AGENT)
    conn.execute(
        "INSERT INTO visit_days (day, visitor, signed_in, chat, hits, "
        "first_ts, last_ts) VALUES (?, ?, 0, 1, 3, 0, 0)", (monday, old))
    conn.commit()

    visitors.note(conn, "10.1.1.9", AGENT, "chat", False)
    today_row = conn.execute("SELECT visitor FROM visit_days WHERE day=?",
                             (visitors.today(),)).fetchone()
    assert today_row["visitor"] != old

    # and the sweep takes the old salt away, which is what makes it permanent
    visitors.sweep(conn)
    assert conn.execute("SELECT COUNT(*) FROM visit_salts WHERE day=?",
                        (monday,)).fetchone()[0] == 0
    # the COUNT survives; only the ability to re-derive who it was is gone
    assert conn.execute("SELECT COUNT(*) FROM visit_days WHERE day=?",
                        (monday,)).fetchone()[0] == 1


def test_a_days_salt_is_made_once(conn):
    visitors.note(conn, "10.1.1.9", AGENT, "", False)
    visitors._salts.clear()          # a second worker, cold
    visitors.note(conn, "10.1.1.10", AGENT, "", False)
    assert conn.execute("SELECT COUNT(*) FROM visit_salts").fetchone()[0] == 1


def test_bots_are_not_people(conn):
    for agent in ("Googlebot/2.1", "curl/8.4.0", "python-requests/2.31",
                  "Mozilla/5.0 (compatible; bingbot/2.0)", None, ""):
        visitors.note(conn, "10.1.1.9", agent, "chat", False)
    assert rows(conn) == []


def test_an_overlay_is_not_a_reader(conn):
    """A token URL is a capability pointed at a meter. OBS reloads its browser
    source every time the scene changes, and those are not arrivals."""
    visitors.note(conn, "10.1.1.9", AGENT, "overlay/abc123", False)
    visitors.note(conn, "10.1.1.9", AGENT, "ingame/abc123", False)
    assert rows(conn) == []
    # the same person's actual dashboard tab still counts
    visitors.note(conn, "10.1.1.9", AGENT, "live", True)
    assert len(rows(conn)) == 1


def test_counting_never_breaks_a_page(conn):
    """A visit that cannot be recorded is a visit that did not happen — the
    page still goes out. Anything else would trade the site for its telemetry."""
    visitors.note(None, "10.1.1.9", AGENT, "chat", False)      # no connection
    assert rows(conn) == []


def test_the_timeline_is_days_newest_first(conn):
    now = time.time()
    for ago, hits in ((0, 2), (1, 5), (3, 1)):
        day = visitors.today(now - ago * 86400)
        conn.execute(
            "INSERT INTO visit_days (day, visitor, signed_in, chat, hits, "
            "first_ts, last_ts) VALUES (?, ?, ?, 1, ?, 0, 0)",
            (day, f"hash-{ago}", 1 if ago == 1 else 0, hits))
    conn.commit()

    d = visitors.timeline(conn, days=7)
    assert [r["day"] for r in d["days"]] == [
        visitors.today(now), visitors.today(now - 86400),
        visitors.today(now - 3 * 86400)]
    assert d["days"][0]["visitors"] == 1
    assert d["days"][1]["accounts"] == 1
    assert d["days"][1]["anon"] == 0
    assert d["totals"]["hits"] == 8
    # summed per-day counts, named as such: the same person on two days is two
    # rows and this table threw away what would tell them apart
    assert d["totals"]["visitor_days"] == 3

    # a window that does not reach them does not count them
    assert len(visitors.timeline(conn, days=1)["days"]) == 1


# --- the API ---------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmpdb):
    from main import app
    with TestClient(app) as c:
        yield c


def test_the_timeline_is_admin_only(client):
    assert client.get("/api/admin/visitors").status_code in (401, 403)
