"""Zone-run linker: dedupe marking, segmentation, id stability."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import SCHEMA
from pipeline.zoneruns import ZONE_RUN_GAP_S, encounter_fp, rebuild_zone_runs

T0 = 1_754_000_000


@pytest.fixture()
def conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # parse_version lives in init_db's ALTER path, not the base SCHEMA
    conn.execute("ALTER TABLE sessions ADD COLUMN parse_version INTEGER")
    conn.execute("INSERT INTO characters (id, user_id, name, world_id) VALUES (1, 1, 'Bobby', 618)")
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


def test_mixed_parse_version_deduped_to_the_newer_parse(conn):
    """The group key IS the segmentation result, so two versions that landed in
    one group have already agreed about this fight and there is nothing for the
    reparse sweep to converge. Waiting for it anyway left permanent duplicates
    behind every session that stopped being sweepable — `_reparse_stale` walks
    `ready`/`parsing` only, so an `error` session holds its old version for
    good."""
    add_session(conn, 1, T0 - 100_000, T0 + 100_000, parse_version=7)
    add_session(conn, 2, T0, T0 + 400, parse_version=6)
    a = add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    b = add_enc(conn, 2, "Zone A", "Boss 1", T0, T0 + 100)
    rebuild_zone_runs(conn, 1)
    assert enc(conn, b)["dup_of"] == a
    assert enc(conn, a)["dup_of"] is None
    assert runs(conn)[0]["encounter_count"] == 1


def test_the_newer_parse_wins_even_from_the_narrower_file(conn):
    """Version leads coverage. Coverage decides which FILE saw more around a
    fight and is the right tiebreak between two parses of equal age; once the
    key says both versions segmented the fight identically, the only thing left
    to choose between is two analyses of it."""
    add_session(conn, 1, T0 - 100_000, T0 + 100_000, parse_version=6)   # wider
    add_session(conn, 2, T0, T0 + 400, parse_version=7)                 # newer
    a = add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    b = add_enc(conn, 2, "Zone A", "Boss 1", T0, T0 + 100)
    rebuild_zone_runs(conn, 1)
    assert enc(conn, a)["dup_of"] == b
    assert enc(conn, b)["dup_of"] is None


def test_a_fight_the_two_versions_segmented_differently_is_not_deduped(conn):
    """The case the old partition was really guarding, and the group key
    already handles it: different segmentation is a different key, so these
    never meet."""
    add_session(conn, 1, T0 - 100_000, T0 + 100_000, parse_version=7)
    add_session(conn, 2, T0, T0 + 400, parse_version=6)
    a = add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    b = add_enc(conn, 2, "Zone A", "Boss 1", T0, T0 + 150)
    rebuild_zone_runs(conn, 1)
    assert enc(conn, a)["dup_of"] is None and enc(conn, b)["dup_of"] is None
    assert runs(conn)[0]["encounter_count"] == 2


def test_an_unparsed_session_never_outranks_a_parsed_one(conn):
    add_session(conn, 1, T0, T0 + 400, parse_version=6)
    conn.execute("UPDATE sessions SET parse_version=NULL WHERE id=1")
    add_session(conn, 2, T0, T0 + 400, parse_version=6)
    a = add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    b = add_enc(conn, 2, "Zone A", "Boss 1", T0, T0 + 100)
    rebuild_zone_runs(conn, 1)
    assert enc(conn, a)["dup_of"] == b
    assert enc(conn, b)["dup_of"] is None


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


# ---------- the roster (raider_count) ----------


def add_entity(conn, eid, name, kind="player", sid=1):
    conn.execute("INSERT INTO entities (id, session_id, name, kind) VALUES (?,?,?,?)",
                 (eid, sid, name, kind))


def add_actor(conn, enc_id, entity_id, damage=1000, taken=0):
    conn.execute(
        "INSERT INTO encounter_actor_stats (encounter_id, entity_id, damage, "
        "damage_taken) VALUES (?,?,?,?)", (enc_id, entity_id, damage, taken))


def test_roster_counts_players_who_did_something(conn):
    add_session(conn, 1, T0, T0 + 5000)
    e1 = add_enc(conn, 1, "Zone A", "Boss", T0, T0 + 100)
    add_entity(conn, 1, "P1")
    add_entity(conn, 2, "P2")
    add_entity(conn, 3, "Mob", kind="mob")
    add_entity(conn, 4, "Bystander")
    add_entity(conn, 5, "Unknown")
    add_actor(conn, e1, 1)
    add_actor(conn, e1, 2)
    add_actor(conn, e1, 3)                          # mobs are not raiders
    add_actor(conn, e1, 4, damage=0, taken=9000)    # only ever got hit
    add_actor(conn, e1, 5)                          # the pooled sourceless actor
    rebuild_zone_runs(conn, 1)
    assert runs(conn)[0]["raider_count"] == 2


def test_roster_drops_the_group_that_fought_past_you(conn):
    """Castle Mistmoore, run 9: four regulars over most of the night and six
    strangers who appear in two fights as they go by."""
    add_session(conn, 1, T0, T0 + 20000)
    encs = [add_enc(conn, 1, "Zone A", f"Pull {i}", T0 + i * 200, T0 + i * 200 + 100)
            for i in range(20)]
    for i in range(1, 5):
        add_entity(conn, i, f"Regular{i}")
        for e in encs:
            add_actor(conn, e, i)
    for i in range(5, 11):
        add_entity(conn, i, f"Passerby{i}")
        for e in encs[7:9]:
            add_actor(conn, e, i)
    rebuild_zone_runs(conn, 1)
    assert runs(conn)[0]["raider_count"] == 4


def test_roster_keeps_a_real_raid_whole(conn):
    """The rule must not eat a 24-man raid: everyone who is actually there is
    there for most of it."""
    add_session(conn, 1, T0, T0 + 40000)
    encs = [add_enc(conn, 1, "Zone A", f"Pull {i}", T0 + i * 200, T0 + i * 200 + 100)
            for i in range(30)]
    for i in range(1, 25):
        add_entity(conn, i, f"Raider{i}")
        for e in encs[: 20 + (i % 10)]:
            add_actor(conn, e, i)
    rebuild_zone_runs(conn, 1)
    assert runs(conn)[0]["raider_count"] == 24


def test_roster_takes_everyone_on_a_short_run(conn):
    """Under SHORT_RUN_FIGHTS there is no attendance to read, so one fight is
    enough — the alternative is a blank column on every quick zone."""
    add_session(conn, 1, T0, T0 + 5000)
    e1 = add_enc(conn, 1, "Zone A", "Boss", T0, T0 + 100)
    e2 = add_enc(conn, 1, "Zone A", "Boss 2", T0 + 200, T0 + 300)
    add_entity(conn, 1, "P1")
    add_entity(conn, 2, "P2")
    add_actor(conn, e1, 1)
    add_actor(conn, e2, 2)
    rebuild_zone_runs(conn, 1)
    assert runs(conn)[0]["raider_count"] == 2


def test_roster_counts_people_not_entity_rows(conn):
    """Entities are session-scoped; a run spanning two logs must not count the
    same raider twice."""
    add_session(conn, 1, T0, T0 + 1000)
    add_session(conn, 2, T0 + 1000, T0 + 2000)
    e1 = add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    e2 = add_enc(conn, 2, "Zone A", "Boss 2", T0 + 200, T0 + 300)
    add_entity(conn, 1, "P1", sid=1)
    add_entity(conn, 2, "P1", sid=2)
    add_actor(conn, e1, 1)
    add_actor(conn, e2, 2)
    rebuild_zone_runs(conn, 1)
    rs = runs(conn)
    assert len(rs) == 1 and rs[0]["raider_count"] == 1


# ---------- hand edits (delete / merge / unmerge) ----------


def add_edit(conn, fp, kind):
    conn.execute(
        "INSERT INTO run_edits (character_id, fp, kind, created_ts) VALUES (1,?,?,0)",
        (fp, kind))


def test_delete_hides_fight_and_survives_reparse(conn):
    add_session(conn, 1, T0, T0 + 5000)
    add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100, is_named=1, success=1)
    keep = add_enc(conn, 1, "Zone A", "Boss 2", T0 + 200, T0 + 300, is_named=1, success=1)
    rebuild_zone_runs(conn, 1)
    assert runs(conn)[0]["encounter_count"] == 2

    add_edit(conn, encounter_fp({"started_ts": T0, "zone": "Zone A", "name": "Boss 1"}), "delete")
    rebuild_zone_runs(conn, 1)
    rs = runs(conn)
    assert rs[0]["encounter_count"] == 1 and rs[0]["named_count"] == 1
    assert rs[0]["started_ts"] == T0 + 200
    assert enc(conn, keep)["deleted_ts"] is None

    # a reparse drops and recreates every row: the edit is keyed by fingerprint,
    # so the deleted fight must come back deleted under its new id
    conn.execute("DELETE FROM encounters")
    add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100, is_named=1, success=1)
    again = add_enc(conn, 1, "Zone A", "Boss 2", T0 + 200, T0 + 300, is_named=1, success=1)
    rebuild_zone_runs(conn, 1)
    assert runs(conn)[0]["encounter_count"] == 1
    assert enc(conn, again)["deleted_ts"] is None


def test_delete_covers_every_duplicate_copy(conn):
    add_session(conn, 1, T0, T0 + 5000)
    add_session(conn, 2, T0, T0 + 9000)
    add_enc(conn, 1, "Zone A", "Boss", T0, T0 + 100)
    add_enc(conn, 2, "Zone A", "Boss", T0, T0 + 100)
    add_edit(conn, encounter_fp({"started_ts": T0, "zone": "Zone A", "name": "Boss"}), "delete")
    rebuild_zone_runs(conn, 1)
    assert runs(conn) == []
    assert all(e["deleted_ts"] is not None
               for e in conn.execute("SELECT deleted_ts FROM encounters"))


def test_join_merges_two_runs(conn):
    add_session(conn, 1, T0, T0 + 5000)
    add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    add_enc(conn, 1, "Zone B", "Boss 2", T0 + 200, T0 + 300)
    rebuild_zone_runs(conn, 1)
    assert len(runs(conn)) == 2

    add_edit(conn, encounter_fp({"started_ts": T0 + 200, "zone": "Zone B", "name": "Boss 2"}), "join")
    rebuild_zone_runs(conn, 1)
    rs = runs(conn)
    assert len(rs) == 1
    assert rs[0]["zone"] == "Zone A" and rs[0]["encounter_count"] == 2


def test_break_splits_one_run(conn):
    add_session(conn, 1, T0, T0 + 5000)
    add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    add_enc(conn, 1, "Zone A", "Boss 2", T0 + 200, T0 + 300)
    add_enc(conn, 1, "Zone A", "Boss 3", T0 + 400, T0 + 500)
    rebuild_zone_runs(conn, 1)
    assert len(runs(conn)) == 1

    add_edit(conn, encounter_fp({"started_ts": T0 + 200, "zone": "Zone A", "name": "Boss 2"}), "break")
    rebuild_zone_runs(conn, 1)
    assert [r["encounter_count"] for r in runs(conn)] == [1, 2]


def test_join_on_first_fight_is_ignored(conn):
    """Nothing to merge backwards into — the run must still exist."""
    add_session(conn, 1, T0, T0 + 5000)
    add_enc(conn, 1, "Zone A", "Boss 1", T0, T0 + 100)
    add_edit(conn, encounter_fp({"started_ts": T0, "zone": "Zone A", "name": "Boss 1"}), "join")
    rebuild_zone_runs(conn, 1)
    assert len(runs(conn)) == 1
