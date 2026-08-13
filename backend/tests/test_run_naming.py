"""What a run is CALLED when its zone is not the event.

In an instance the zone IS the event — "The Emerald Halls" books a night, names
it and is what anybody asks about. A public zone is a place several guilds pass
through, so "Rivervale" says only where somebody was standing, and four visits
to a halfling town read as four identical rows. What happened there was the
Avatar of Mischief.

Reads the committed `refdata/zone_eras.json`, never the wiki.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import zones
from db import SCHEMA
from routers.zoneruns_api import _headline_named

T0 = 1_786_390_000


@pytest.fixture()
def conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO characters (id, user_id, name, world_id) "
                 "VALUES (1, 1, 'Bobby', 618)")
    conn.execute("INSERT INTO sessions (id, character_id, source, status, created_ts) "
                 "VALUES (1, 1, 'live', 'ready', ?)", (T0,))
    return conn


def run(conn, run_id, zone):
    conn.execute("INSERT INTO zone_runs (id, character_id, zone, started_ts, "
                 "ended_ts, updated_ts) VALUES (?, 1, ?, ?, ?, ?)",
                 (run_id, zone, T0, T0 + 500, T0))
    return {"id": run_id, "zone": zone}


def fight(conn, run_id, name, is_named=1, hidden=False, ts=T0):
    conn.execute(
        "INSERT INTO encounters (session_id, zone, name, is_named, started_ts, "
        "ended_ts, duration_s, zone_run_id, hidden_ts) VALUES (1,?,?,?,?,?,?,?,?)",
        ("Rivervale", name, is_named, ts, ts + 200, 200, run_id,
         T0 if hidden else None))


# ---------- the reference data this rests on ----------

def test_the_wiki_knows_rivervale_is_a_public_zone():
    """It was absent from this file entirely until `ZoneBox` pages were read:
    the template leaves `introduced` blank for an original EQ2 zone, and the
    old parser read that blank as a missing record rather than as an answer."""
    assert zones.is_public("Rivervale")
    assert not zones.is_raid("Rivervale")


def test_an_instance_is_not_public():
    assert zones.is_raid("The Emerald Halls")
    assert not zones.is_public("The Emerald Halls")


def test_a_zone_nobody_has_heard_of_claims_nothing():
    """Unknown is not a claim — which is what keeps the naming rule off zones
    the reference data cannot speak for."""
    assert not zones.is_public("Bobby's World")
    assert not zones.is_raid("Bobby's World")


# ---------- the rule ----------

def test_a_named_in_a_public_zone_names_the_run(conn):
    r = run(conn, 1, "Rivervale")
    fight(conn, 1, "Avatar of Mischief")
    assert _headline_named(conn, [r]) == {1: "Avatar of Mischief"}


def test_the_same_named_pulled_twice_is_still_one_name(conn):
    """Two wipes and a kill is one avatar, not three."""
    r = run(conn, 1, "Rivervale")
    fight(conn, 1, "Avatar of Mischief", ts=T0)
    fight(conn, 1, "Avatar of Mischief", ts=T0 + 300)
    assert _headline_named(conn, [r]) == {1: "Avatar of Mischief"}


def test_two_different_nameds_is_a_tour_of_the_zone(conn):
    """The zone's own name is the honest label for that."""
    r = run(conn, 1, "Rivervale")
    fight(conn, 1, "Avatar of Mischief")
    fight(conn, 1, "Avatar of Growth", ts=T0 + 300)
    assert _headline_named(conn, [r]) == {}


def test_an_instance_keeps_its_zone_name(conn):
    """The Emerald Halls is the event; Wuoshi is a fight inside it."""
    r = run(conn, 1, "The Emerald Halls")
    fight(conn, 1, "Wuoshi")
    assert _headline_named(conn, [r]) == {}


def test_trash_never_names_a_run(conn):
    r = run(conn, 1, "Rivervale")
    fight(conn, 1, "an undertow puppet", is_named=0)
    assert _headline_named(conn, [r]) == {}


def test_a_hidden_named_does_not_name_the_run(conn):
    """Hiding a fight says it does not count, and the run's own name is the
    loudest place that could contradict it."""
    r = run(conn, 1, "Rivervale")
    fight(conn, 1, "Avatar of Mischief", hidden=True)
    assert _headline_named(conn, [r]) == {}


def test_a_run_with_no_zone_is_left_alone(conn):
    r = run(conn, 1, None)
    fight(conn, 1, "Avatar of Mischief")
    assert _headline_named(conn, [r]) == {}
