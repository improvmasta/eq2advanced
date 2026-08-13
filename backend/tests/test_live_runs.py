"""Which run wears the Live pill.

A live session is one ACT process left running, and it keeps appending: one
session is a whole evening, and everything it passed through — Freeport, the
Poet's Palace, the Emerald Halls, an avatar in Rivervale — is a run inside it.
So "belongs to a receiving session" lights the whole list, and the newest
encounter alone lights the last thing that FOUGHT, which is not the same as
where the character is now.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import SCHEMA
from routers.zoneruns_api import _live_runs

T0 = 1_786_390_000


@pytest.fixture()
def conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO characters (id, user_id, name, world_id) "
                 "VALUES (1, 1, 'Bobby', 618)")
    conn.execute("INSERT INTO sessions (id, character_id, source, status, created_ts) "
                 "VALUES (1, 1, 'live', 'receiving', ?)", (T0,))
    return conn


def run(conn, run_id, zone, started):
    conn.execute("INSERT INTO zone_runs (id, character_id, zone, started_ts, ended_ts, "
                 "updated_ts) VALUES (?, 1, ?, ?, ?, ?)",
                 (run_id, zone, started, started + 300, started))
    return run_id


def fought(conn, run_id, zone, started, session_id=1):
    conn.execute(
        "INSERT INTO encounters (session_id, zone, name, is_named, started_ts, "
        "ended_ts, duration_s, zone_run_id) VALUES (?,?,?,1,?,?,?,?)",
        (session_id, zone, "Avatar of Mischief", started, started + 200, 200, run_id))


def zoned_to(conn, zone, ts, session_id=1):
    conn.execute(
        "INSERT INTO events (session_id, seq, ts, type, extra) VALUES (?,?,?,'zone',?)",
        (session_id, ts, ts, json.dumps({"zone": zone})))


def test_only_the_newest_run_of_a_session_is_live(conn):
    """A plugin left running all evening puts every zone it passed through in
    one session; nine of those ten rows finished hours ago."""
    run(conn, 1, "The Emerald Halls", T0)
    run(conn, 2, "Rivervale", T0 + 4000)
    fought(conn, 1, "The Emerald Halls", T0)
    fought(conn, 2, "Rivervale", T0 + 4000)
    zoned_to(conn, "Rivervale", T0 + 3990)
    assert _live_runs(conn, [1, 2]) == {2}


def test_a_run_in_a_zone_you_have_left_is_not_live(conn):
    """The bug this test exists for: an encounter is the last thing that
    FOUGHT, and standing in a city produces none. The avatar kill in Rivervale
    sat live for half an hour while the log said The City of Freeport 4 —
    the lines are arriving in Freeport, so Freeport is where the session is."""
    run(conn, 1, "Rivervale", T0)
    fought(conn, 1, "Rivervale", T0)
    zoned_to(conn, "Rivervale", T0 - 10)
    assert _live_runs(conn, [1]) == {1}

    zoned_to(conn, "The City of Freeport 4", T0 + 2000)
    assert _live_runs(conn, [1]) == set()


def test_a_session_with_no_zone_line_keeps_the_encounters_answer(conn):
    """ACT attached mid-zone: the log never said where this is. A missing fact
    must not cost a real live session its pill."""
    run(conn, 1, "Rivervale", T0)
    fought(conn, 1, "Rivervale", T0)
    assert _live_runs(conn, [1]) == {1}


def test_a_finished_session_is_never_live(conn):
    run(conn, 1, "Rivervale", T0)
    fought(conn, 1, "Rivervale", T0)
    zoned_to(conn, "Rivervale", T0 - 10)
    conn.execute("UPDATE sessions SET status='ready' WHERE id=1")
    assert _live_runs(conn, [1]) == set()


def test_re_zoning_into_a_fresh_instance_does_not_keep_the_old_run_lit(conn):
    """Compared RAW rather than by `zones.base_name`: a second lockout of the
    same raid zone is a new run, and the old one must not hold the pill until
    the new one has fought."""
    run(conn, 1, "Castle Mistmoore", T0)
    fought(conn, 1, "Castle Mistmoore", T0)
    zoned_to(conn, "Castle Mistmoore 2", T0 + 2000)
    assert _live_runs(conn, [1]) == set()
