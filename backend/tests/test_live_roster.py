"""Asking Census about a stranger WHILE the pull is happening.

`snapshot_context` is read once, when the session's `LiveState` is created, and
everything in it comes from parses that already finished. That is right for the
people you raid with every week and empty for anybody else: stand next to
another guild's avatar pull and every name in the meter is one this app has
never parsed, so the whole raid sat there with no class — and the damage and
heal bars ARE the class. Census does not need a spellbook to answer that, only
the name, so the live path can have the same ground truth the recorded one gets.

What these tests hold is the shape of that, because it runs beside a raid: the
names asked about are the ones ON SCREEN with no class, nobody is asked twice,
the HTTP never happens on the ingest thread, and a Census outage costs a retry
rather than a hammering.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import SCHEMA
from pipeline import live

WORLD = 618


class FakeCensus:
    """`character_by_name` as Census answers it: a doc, or None for a name that
    is not a character on this world."""

    def __init__(self, classes: dict, fail: set | None = None):
        self.classes = {k.lower(): v for k, v in classes.items()}
        self.fail = {n.lower() for n in (fail or set())}
        self.asked: list[str] = []

    def character_by_name(self, name, world_id=WORLD):
        self.asked.append(name)
        if name.lower() in self.fail:
            raise RuntimeError("census is having a day")
        cls = self.classes.get(name.lower())
        if cls is None:
            return None
        return {"id": 1, "name": {"first": name},
                "type": {"class": cls, "level": 125}}


@pytest.fixture()
def conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@pytest.fixture(autouse=True)
def wired(conn, monkeypatch):
    """The worker's own `get_db()` is thread-local; point it at this one. The
    auto-refresh guard is off across the suite (conftest) so that nothing can
    reach the real Census — these tests turn it back on with a fake client.

    The background thread is stubbed out and `drain` stands in for it. Not for
    tidiness: a real worker started here would race the test for the queue and
    win it with the REAL client in its hand, which is the one thing no test in
    this suite may do."""
    monkeypatch.setattr(live, "get_db", lambda: conn)
    monkeypatch.setattr(live, "_ensure_roster_worker", lambda: None)
    monkeypatch.setenv("CENSUS_AUTO_REFRESH", "1")
    yield
    live._states.clear()
    while not live._roster_q.empty():
        live._roster_q.get()


def state(session_id=1, roster=None):
    st = live.LiveState(session_id, "Bobby")
    st.world_id = WORLD
    st.roster = dict(roster or {})
    return st


def drain(monkeypatch, census):
    """Run the queue the way the worker thread would, in this thread — the
    thread itself is the one thing a test cannot assert on."""
    from census import client as census_client
    monkeypatch.setattr(census_client, "shared_client", lambda: census)
    while not live._roster_q.empty():
        st, names = live._roster_q.get()
        live._lookup_roster(st, names)


# ---------- which names get asked about ----------

def test_only_the_players_on_screen_with_no_class_are_asked_about():
    """The snapshot is where every one of those words has already been decided
    — `Names` has ruled out the mobs and the pets, and the meter has already
    coloured whoever it could."""
    fight = {"actors": [
        {"name": "Bobby", "kind": "player", "class": "mystic"},
        {"name": "Stranger", "kind": "player", "class": None},
        {"name": "Wuoshi", "kind": "mob", "class": None},
        {"name": "Knyi", "kind": "swarm_pet", "class": None},
    ]}
    assert live._unclassed(fight) == ["Stranger"]


def test_between_pulls_there_is_nobody_to_ask_about():
    """`snapshot_payload` sends `fight: None` when no segment is open."""
    assert live._unclassed(None) == []


def test_a_name_is_asked_about_once_a_session(monkeypatch):
    """The plugin sends twice a second and the same unclassed row is in every
    payload. Marked when QUEUED, not when answered, or a slow lookup queues
    itself twenty more times while it runs."""
    st = state()
    live._queue_roster_lookup(st, ["Stranger"])
    live._queue_roster_lookup(st, ["Stranger"])
    live._queue_roster_lookup(st, ["Stranger", "Another"])
    queued = []
    while not live._roster_q.empty():
        queued += live._roster_q.get()[1]
    assert queued == ["Stranger", "Another"]


def test_nothing_is_asked_when_the_census_guard_is_off(monkeypatch):
    monkeypatch.setenv("CENSUS_AUTO_REFRESH", "0")
    live._queue_roster_lookup(state(), ["Stranger"])
    assert live._roster_q.empty()


# ---------- what comes back ----------

def test_a_stranger_gets_their_class_mid_pull(conn, monkeypatch):
    st = state()
    census = FakeCensus({"Stranger": "Defiler"})
    live._queue_roster_lookup(st, ["Stranger"])
    drain(monkeypatch, census)
    assert st.roster["stranger"] == "defiler"
    # and it is CACHED, so the next session, the next parse and the rebuild all
    # start with the answer instead of asking again
    assert conn.execute(
        "SELECT class FROM roster_classes WHERE name_lower='stranger'"
    ).fetchone()["class"] == "defiler"


def test_a_found_name_is_also_proof_of_personhood(monkeypatch):
    """`refine_known_mobs` reads a boss killing eight raiders as eight mobs,
    and `known_players` is the veto. Census answering for a name is the
    strongest form of that evidence there is."""
    st = state()
    live._queue_roster_lookup(st, ["Stranger"])
    drain(monkeypatch, FakeCensus({"Stranger": "Defiler"}))
    assert "Stranger" in st.known_players


def test_an_answer_already_on_disk_reaches_a_second_session(conn, monkeypatch):
    """`roster.resolve` asks about the STALE names only, so a name the parse
    path (or another uploader's session, or last week) already resolved comes
    back `found: 0` — it was never asked about. The merge reads the TABLE."""
    census = FakeCensus({"Stranger": "Defiler"})
    first = state(1)
    live._queue_roster_lookup(first, ["Stranger"])
    drain(monkeypatch, census)
    assert census.asked == ["Stranger"]

    second = state(2)
    live._queue_roster_lookup(second, ["Stranger"])
    drain(monkeypatch, census)
    assert census.asked == ["Stranger"]           # not asked twice
    assert second.roster["stranger"] == "defiler"  # and still coloured


def test_a_name_census_does_not_have_leaves_no_class(conn, monkeypatch):
    """A miss is an answer and is cached as one (`roster.MISS_TTL_S`) — a
    dumbfire named like a raider must not be re-asked every raid."""
    st = state()
    live._queue_roster_lookup(st, ["Knyi"])
    drain(monkeypatch, FakeCensus({}))
    assert "knyi" not in st.roster
    assert conn.execute(
        "SELECT found FROM roster_classes WHERE name_lower='knyi'").fetchone()["found"] == 0


def test_a_census_outage_is_a_retry_not_a_hammering(monkeypatch):
    """A network failure is not an answer and was not cached, so the name goes
    back on the table — behind a cooldown, so an outage is retried at that rate
    rather than at the plugin's."""
    st = state()
    live._queue_roster_lookup(st, ["Stranger"])
    drain(monkeypatch, FakeCensus({"Stranger": "Defiler"}, fail={"Stranger"}))
    assert "stranger" not in st.roster
    assert st.roster_quiet_until > time.time()

    live._queue_roster_lookup(st, ["Stranger"])
    assert live._roster_q.empty()          # still inside the cooldown

    st.roster_quiet_until = 0
    live._queue_roster_lookup(st, ["Stranger"])
    assert live._roster_q.qsize() == 1     # and it is asked again after it
    drain(monkeypatch, FakeCensus({"Stranger": "Defiler"}))
    assert st.roster["stranger"] == "defiler"
