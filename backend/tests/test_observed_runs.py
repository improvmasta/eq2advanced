"""Runs the logger WATCHED rather than fought in.

Standing near somebody else's pull to gather data is a real way to use this —
an avatar in a contested zone is parsed by whoever is in range, not by whoever
is in the raid. The parse that comes back is a good one and says nothing about
the person who took it, and without a word for that their raid list reads as if
they spent the evening in 28-person fights.

The trap this file mostly exists for: "did nothing" is not "did no damage".
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import SCHEMA
from routers.zoneruns_api import _observed_runs

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
    conn.execute("INSERT INTO zone_runs (id, character_id, zone, started_ts, "
                 "ended_ts, updated_ts) VALUES (1, 1, 'Rivervale', ?, ?, ?)",
                 (T0, T0 + 500, T0))
    conn.execute("INSERT INTO encounters (id, session_id, zone, name, is_named, "
                 "started_ts, ended_ts, duration_s, zone_run_id) "
                 "VALUES (1, 1, 'Rivervale', 'Avatar of Mischief', 1, ?, ?, 200, 1)",
                 (T0, T0 + 200))
    return conn


def acted(conn, name, **stats):
    eid = conn.execute(
        "INSERT INTO entities (session_id, name, kind) VALUES (1, ?, 'player')",
        (name,)).lastrowid
    cols = ("damage", "heals", "wards_absorbed", "cure_count", "damage_taken")
    conn.execute(
        f"INSERT INTO encounter_actor_stats (encounter_id, entity_id, "
        f"{', '.join(cols)}) VALUES (1, ?, ?, ?, ?, ?, ?)",
        (eid, *(stats.get(c, 0) for c in cols)))


def test_a_run_the_logger_never_swung_in_is_observed(conn):
    acted(conn, "Someone", damage=4_000_000)
    acted(conn, "Bobby")
    assert _observed_runs(conn, [1]) == {1}


def test_a_run_the_logger_fought_in_is_not(conn):
    acted(conn, "Someone", damage=4_000_000)
    acted(conn, "Bobby", damage=19_904_036)
    assert _observed_runs(conn, [1]) == set()


def test_a_healer_is_not_an_observer(conn):
    """The reason the test is four columns and not one. A templar deals no
    damage all night and did every bit of the work."""
    acted(conn, "Someone", damage=4_000_000)
    acted(conn, "Bobby", heals=8_000_000)
    assert _observed_runs(conn, [1]) == set()


def test_a_warder_is_not_an_observer(conn):
    """A defiler's output is wards, which are not heals and are not damage."""
    acted(conn, "Someone", damage=4_000_000)
    acted(conn, "Bobby", wards_absorbed=3_000_000)
    assert _observed_runs(conn, [1]) == set()


def test_a_cure_bot_is_not_an_observer(conn):
    """An inquisitor carrying a cure assignment can do a fight's real work with
    neither damage nor healing worth the name."""
    acted(conn, "Someone", damage=4_000_000)
    acted(conn, "Bobby", cure_count=31)
    assert _observed_runs(conn, [1]) == set()


def test_being_hit_by_the_avatar_is_not_fighting_it(conn):
    """Damage TAKEN is deliberately out of the test: a raid-wide AoE reaches
    whoever is standing there, and standing there is the whole scenario."""
    acted(conn, "Someone", damage=4_000_000)
    acted(conn, "Bobby", damage_taken=250_000)
    assert _observed_runs(conn, [1]) == {1}


def test_a_run_with_no_actor_rows_is_not_claimed_either_way(conn):
    """No evidence is not evidence of watching — GROUP BY drops the run, and
    the badge is simply not drawn."""
    assert _observed_runs(conn, [1]) == set()
