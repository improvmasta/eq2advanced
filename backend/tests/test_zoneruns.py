"""Zone-run linker: dedupe marking, segmentation, id stability."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import SCHEMA
from pipeline.zoneruns import ZONE_RUN_GAP_S, rebuild_zone_runs

T0 = 1_754_000_000


@pytest.fixture()
def conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # parse_version lives in init_db's ALTER path, not the base SCHEMA
    conn.execute("ALTER TABLE sessions ADD COLUMN parse_version INTEGER")
    conn.execute("INSERT INTO characters (id, name, world_id) VALUES (1, 'Bobby', 618)")
    return conn


def add_session(conn, sid, started, ended, parse_version=7):
    conn.execute(
        "INSERT INTO sessions (id, character_id, source, status, started_ts, ended_ts, "
        "parse_version, created_ts) VALUES (?, 1, 'upload', 'ready', ?, ?, ?, 0)",
        (sid, started, ended, parse_version))


def add_enc(conn, sid, zone, name, start, end, is_named=0, success=None):
    return conn.execute(
        "INSERT INTO encounters (session_id, zone, name, is_named, started_ts, ended_ts, "
        "duration_s, success) VALUES (?,?,?,?,?,?,?,?)",
        (sid, zone, name, is_named, start, end, max(end - start, 1), success)).lastrowid


def runs(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM zone_runs ORDER BY started_ts")]


def enc(conn, eid):
    return dict(conn.execute("SELECT * FROM encounters WHERE id=?", (eid,)).fetchone())


def test_zone_change_splits(conn):
    add_session(conn, 1, T0, T0 + 5000)
    add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100, is_named=1, success=1)
    add_enc(conn, 1, "Zone A", "trash", T0 + 200, T0 + 300)
    add_enc(conn, 1, "Zone B", "Boss 2", T0 + 400, T0 + 500, is_named=1)
    rebuild_zone_runs(conn, 1)
    rs = runs(conn)
    assert [(r["zone"], r["encounter_count"]) for r in rs] == [("Zone A", 2), ("Zone B", 1)]
    assert rs[0]["named_count"] == 1 and rs[0]["success_count"] == 1
    assert rs[0]["combat_s"] == 200


def test_gap_splits_same_zone(conn):
    add_session(conn, 1, T0, T0 + 20000)
    add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    add_enc(conn, 1, "Zone A", "Boss 2", T0 + 100 + ZONE_RUN_GAP_S + 1, T0 + 100 + ZONE_RUN_GAP_S + 200)
    rebuild_zone_runs(conn, 1)
    assert len(runs(conn)) == 2


def test_small_gap_does_not_split(conn):
    add_session(conn, 1, T0, T0 + 20000)
    add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    add_enc(conn, 1, "Zone A", "Boss 2", T0 + 100 + ZONE_RUN_GAP_S - 1, T0 + ZONE_RUN_GAP_S + 300)
    rebuild_zone_runs(conn, 1)
    assert len(runs(conn)) == 1


def test_null_zone_forms_own_run(conn):
    add_session(conn, 1, T0, T0 + 5000)
    add_enc(conn, 1, None, "trash", T0, T0 + 100)
    add_enc(conn, 1, "Zone A", "Boss", T0 + 200, T0 + 300)
    rebuild_zone_runs(conn, 1)
    rs = runs(conn)
    assert len(rs) == 2
    assert rs[0]["zone"] is None and rs[1]["zone"] == "Zone A"


def test_subset_file_dup_marked(conn):
    """A short log re-uploaded inside a longer one: every subset encounter is
    dup-marked to the superset session's copy and excluded from run rollups."""
    add_session(conn, 1, T0 - 100_000, T0 + 100_000)   # superset (wider coverage)
    add_session(conn, 2, T0, T0 + 400)                 # subset file
    a1 = add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100, is_named=1)
    a2 = add_enc(conn, 1, "Zone A", "Boss 2", T0 + 200, T0 + 300, is_named=1)
    b1 = add_enc(conn, 2, "Zone A", "Boss 1", T0, T0 + 100, is_named=1)
    b2 = add_enc(conn, 2, "Zone A", "Boss 2", T0 + 200, T0 + 300, is_named=1)
    rebuild_zone_runs(conn, 1)
    rs = runs(conn)
    assert len(rs) == 1 and rs[0]["encounter_count"] == 2
    assert enc(conn, b1)["dup_of"] == a1 and enc(conn, b2)["dup_of"] == a2
    assert enc(conn, b1)["zone_run_id"] is None
    assert enc(conn, a1)["zone_run_id"] == rs[0]["id"]
    assert enc(conn, a1)["dup_of"] is None


def test_mixed_parse_version_not_deduped(conn):
    add_session(conn, 1, T0 - 100_000, T0 + 100_000, parse_version=7)
    add_session(conn, 2, T0, T0 + 400, parse_version=6)
    add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    b = add_enc(conn, 2, "Zone A", "Boss 1", T0, T0 + 100)
    rebuild_zone_runs(conn, 1)
    assert enc(conn, b)["dup_of"] is None
    # both stay canonical until the reparse sweep converges versions
    assert runs(conn)[0]["encounter_count"] == 2


def test_rebuild_is_idempotent_and_ids_stable(conn):
    add_session(conn, 1, T0, T0 + 20000)
    add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    add_enc(conn, 1, "Zone B", "Boss 2", T0 + 200, T0 + 300)
    rebuild_zone_runs(conn, 1)
    before = [(r["id"], r["zone"]) for r in runs(conn)]
    rebuild_zone_runs(conn, 1)
    assert [(r["id"], r["zone"]) for r in runs(conn)] == before


def test_extending_a_run_keeps_its_id(conn):
    add_session(conn, 1, T0, T0 + 20000)
    add_enc(conn, 1, "Zone A", "Boss 2", T0 + 1000, T0 + 1100)
    rebuild_zone_runs(conn, 1)
    run_id = runs(conn)[0]["id"]
    # a later upload backfills an earlier fight in the same visit
    add_session(conn, 2, T0 - 5000, T0 + 20000)
    add_enc(conn, 2, "Zone A", "Boss 1", T0, T0 + 100)
    rebuild_zone_runs(conn, 1)
    rs = runs(conn)
    assert len(rs) == 1
    assert rs[0]["id"] == run_id
    assert rs[0]["started_ts"] == T0 and rs[0]["encounter_count"] == 2


def test_dissolved_runs_deleted(conn):
    add_session(conn, 1, T0, T0 + 5000)
    e = add_enc(conn, 1, "Zone A", "Boss", T0, T0 + 100)
    rebuild_zone_runs(conn, 1)
    assert len(runs(conn)) == 1
    conn.execute("DELETE FROM encounters WHERE id=?", (e,))
    rebuild_zone_runs(conn, 1)
    assert runs(conn) == []


def test_raider_count(conn):
    add_session(conn, 1, T0, T0 + 5000)
    e1 = add_enc(conn, 1, "Zone A", "Boss", T0, T0 + 100)
    for i, (name, kind) in enumerate(
            [("P1", "player"), ("P2", "player"), ("Mob", "mob")], start=1):
        conn.execute(
            "INSERT INTO entities (id, session_id, name, kind) VALUES (?, 1, ?, ?)",
            (i, name, kind))
        conn.execute(
            "INSERT INTO encounter_actor_stats (encounter_id, entity_id) VALUES (?, ?)",
            (e1, i))
    rebuild_zone_runs(conn, 1)
    assert runs(conn)[0]["raider_count"] == 2
