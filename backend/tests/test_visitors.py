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

v51 adds two more, and they are the two questions v37 could not answer:

  4. **A route view says WHERE and WHEN, and never who.** `visit_paths` has no
     visitor column, so a page count cannot be crossed with a visitor row. The
     route is a PATTERN — a test below proves the id in `/zones/139710` is not
     written down — and anything outside the app's routes is one `(other)`.
  5. **A beacon proves a browser.** The user-agent filter is a guess about a
     string anybody can set; running JS is not. `app` is that answer, and the
     beacon must never inflate `hits`, which still means arrivals.
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
    conn.execute("DELETE FROM visit_paths")
    conn.commit()
    visitors._salts.clear()
    visitors._swept = ""
    visitors._views.clear()
    visitors._views_day = ""
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


# --- where they went, and when (v51) ---------------------------------------

def paths(conn):
    return conn.execute(
        "SELECT * FROM visit_paths ORDER BY route").fetchall()


def test_the_route_is_a_pattern_and_the_id_is_not_stored(conn):
    """A page count, not a browsing history. Which PAGE was read is the
    question; WHICH RUN was read is a fact about one reader's evening, and the
    table is not allowed to hold it."""
    visitors.note_view(conn, "10.1.1.9", AGENT, "/zones/139710", True, False)
    got = paths(conn)
    assert len(got) == 1
    assert got[0]["route"] == "/zones/:id"
    assert "139710" not in " ".join(str(v) for v in tuple(got[0]))


def test_a_scanner_cannot_grow_the_table(conn):
    """The key space is the app's own routes. Two hundred WordPress probes are
    one honest `(other)` row, not two hundred keys somebody else chose."""
    for junk in ("/wp-admin/install.php", "/.env", "/xmlrpc.php",
                 "/" + "a" * 500, "", "/nope?x=1#y"):
        visitors.note_view(conn, "10.1.1.9", AGENT, junk, False, False)
    got = paths(conn)
    assert len(got) == 1
    assert got[0]["route"] == visitors.OTHER
    assert got[0]["views"] == 6


def test_route_patterns_cover_the_app(conn):
    assert visitors.route_of("/") == "/"
    assert visitors.route_of("/plan?era=rok") == "/plan"
    assert visitors.route_of("/plan/") == "/plan"
    assert visitors.route_of("plan") == "/plan"            # no leading slash
    assert visitors.route_of("/characters") == "/characters"
    assert visitors.route_of("/characters/12") == "/characters/:id"
    assert visitors.route_of("/guild/skill-issue") == "/guild/skill-issue"
    # every admin screen is one row: the question is whether an admin was in
    # there, not which tab they were on
    assert visitors.route_of("/admin") == "/admin"
    assert visitors.route_of("/admin/visitors") == "/admin"
    assert visitors.route_of(None) == visitors.OTHER


def test_the_beacon_says_browser_without_saying_who(conn):
    """The valuable half. A user-agent is a string anybody can set and this one
    claims Chrome throughout; running the beacon is the part a scraper does not
    do, and it lands on the visit as a flag and nowhere else."""
    visitors.note(conn, "10.1.1.9", AGENT, "", False)           # the page load
    visitors.note_view(conn, "10.1.1.9", AGENT, "/", True, False)
    got = rows(conn)
    assert len(got) == 1 and got[0]["app"] == 1
    # …and `visit_paths` still has nothing that could be a person in it
    assert "visitor" not in paths(conn)[0].keys()


def test_a_crawler_that_never_runs_js_stays_visible_but_unconfirmed(conn):
    """The point of the column. The page load is still counted — that is what
    it always was — and it simply never gets its `app` flag, so the admin page
    shows the gap instead of a number nobody can read."""
    visitors.note(conn, "203.0.113.7", AGENT, "", False)
    got = rows(conn)
    assert len(got) == 1 and got[0]["hits"] == 1 and got[0]["app"] == 0
    assert paths(conn) == []


def test_the_beacon_never_inflates_arrivals(conn):
    """`hits` means somebody arrived. A person who lands once and then clicks
    through four tabs is ONE arrival and five route views, and confusing the
    two would undo the distinction the whole module is built on."""
    visitors.note(conn, "10.1.1.9", AGENT, "", False)
    for route in ("/", "/plan", "/compare", "/chat", "/plan"):
        visitors.note_view(conn, "10.1.1.9", AGENT, route, route == "/", False)
    got = rows(conn)
    assert len(got) == 1
    assert got[0]["hits"] == 1
    assert sum(r["views"] for r in paths(conn)) == 5
    assert sum(r["entries"] for r in paths(conn)) == 1
    # navigating to /chat inside the app counts as opening it, which a page
    # load alone could never see
    assert got[0]["chat"] == 1


def test_a_beacon_flood_is_bounded(conn):
    for _ in range(visitors.VIEW_CAP + 50):
        visitors.note_view(conn, "10.1.1.9", AGENT, "/plan", False, False)
    assert sum(r["views"] for r in paths(conn)) == visitors.VIEW_CAP


def test_an_overlay_beacon_is_not_a_reader(conn):
    visitors.note_view(conn, "10.1.1.9", AGENT, "/overlay/abc123", True, False)
    visitors.note_view(conn, "10.1.1.9", AGENT, "/ingame/abc123", True, False)
    assert paths(conn) == []
    assert rows(conn) == []


def test_bots_do_not_get_a_route_either(conn):
    visitors.note_view(conn, "10.1.1.9", "Googlebot/2.1", "/plan", True, False)
    assert paths(conn) == []


def test_the_beacon_never_breaks_a_page(conn):
    visitors.note_view(None, "10.1.1.9", AGENT, "/plan", True, False)
    assert paths(conn) == []


def test_destinations_are_busiest_first(conn):
    for route, n in (("/plan", 5), ("/", 9), ("/chat", 2)):
        for _ in range(n):
            visitors.note_view(conn, "10.1.1.9", AGENT, route, False, False)
    d = visitors.destinations(conn, days=7)
    assert [r["route"] for r in d["routes"]] == ["/", "/plan", "/chat"]
    assert d["totals"]["views"] == 16


def test_arrivals_keep_the_empty_hours(conn):
    """A missing 4am is not the same as a quiet one, and a reader comparing the
    shape of a day needs the difference."""
    visitors.note_view(conn, "10.1.1.9", AGENT, "/", True, False)
    d = visitors.arrivals(conn, days=7)
    assert [h["hour"] for h in d["hours"]] == list(range(24))
    assert sum(h["entries"] for h in d["hours"]) == 1


def test_the_sweep_takes_the_old_counters_too(conn):
    old_day = visitors.today(time.time() - (visitors.KEEP_DAYS + 5) * 86400)
    conn.execute("INSERT INTO visit_paths (day, hour, route, views, entries) "
                 "VALUES (?, 20, '/plan', 40, 3)", (old_day,))
    conn.commit()
    visitors.sweep(conn)
    assert paths(conn) == []


# --- the API ---------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmpdb):
    from main import app
    with TestClient(app) as c:
        yield c


def test_the_timeline_is_admin_only(client):
    assert client.get("/api/admin/visitors").status_code in (401, 403)


def test_the_beacon_needs_no_account(client, conn):
    """The stranger is the thing worth counting, so the endpoint is public. It
    answers 204 with no body: `sendBeacon` cannot read a reply and there is
    nothing a caller should learn from this."""
    r = client.post("/api/visit", json={"path": "/plan", "entry": True},
                    headers={"user-agent": AGENT})
    assert r.status_code == 204 and r.content == b""
    got = paths(conn)
    assert len(got) == 1 and got[0]["route"] == "/plan"


def test_the_beacon_refuses_to_be_a_write_primitive(client, conn):
    """Nothing in the body chooses a key. A path the app does not have becomes
    `(other)`, an oversized one is refused by the model before it arrives, and
    neither can add a row somebody else named."""
    assert client.post("/api/visit", json={"path": "/wp-admin/setup.php"},
                       headers={"user-agent": AGENT}).status_code == 204
    assert client.post("/api/visit", json={"path": "/" + "x" * 400},
                       headers={"user-agent": AGENT}).status_code == 422
    assert client.post("/api/visit", json={},
                       headers={"user-agent": AGENT}).status_code == 204
    assert {r["route"] for r in paths(conn)} == {visitors.OTHER, "/"}
