"""The raid's guild, voted by its roster (census/guilds.py).

The vote is the whole feature, so most of this file is a table against the pure
function: a wrong tag is a public claim about somebody else's guild, and the
cases that must ABSTAIN matter more than the ones that tag.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import db as dbmod
from census.guilds import (backfill_stale_guilds, known_guilds, majority_guild,
                           retag_runs)


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(dbmod, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(dbmod, "RAW_DIR", tmp_path / "raw")
    dbmod._local.conn = None
    dbmod.init_db()
    yield dbmod.get_db()
    dbmod._local.conn = None


# ---- the vote (pure) ----

FT = "Freethinkers"


def names(n, prefix="p"):
    return [f"{prefix}{i}" for i in range(n)]


def test_clear_majority():
    roster = names(10)
    guild_of = {n: FT for n in roster[:8]} | {n: None for n in roster[8:]}
    assert majority_guild(roster, guild_of) == FT


def test_guildless_count_against():
    """12 guildies and 10 pick-ups is a guild raid; 3 and 8 is a pick-up group
    that happens to carry three guildies."""
    roster = names(22)
    yes = {n: FT for n in roster[:12]} | {n: None for n in roster[12:]}
    assert majority_guild(roster, yes) == FT
    no = {n: FT for n in roster[:3]} | {n: None for n in roster[3:11]}
    # 11 of 22 known is exactly half the roster — thin, and it abstains anyway
    assert majority_guild(roster[:11], no) is None


def test_exact_half_is_not_a_majority():
    roster = names(10)
    guild_of = {n: FT for n in roster[:5]} | {n: None for n in roster[5:]}
    assert majority_guild(roster, guild_of) is None


def test_tie_abstains():
    roster = names(10)
    guild_of = ({n: FT for n in roster[:5]}
                | {n: "Ascent" for n in roster[5:]})
    assert majority_guild(roster, guild_of) is None


def test_all_guildless_is_no_guild_not_a_guild():
    roster = names(10)
    assert majority_guild(roster, {n: None for n in roster}) is None


def test_thin_evidence_abstains():
    """Six of twenty-four resolved cannot name the night, even unanimously."""
    roster = names(24)
    guild_of = {n: FT for n in roster[:6]}
    assert majority_guild(roster, guild_of) is None


def test_failed_lookups_abstain_they_are_not_guildless():
    """Half the roster unresolved plus a unanimous rest still fails the
    half-the-roster gate; resolve one more and it passes."""
    roster = names(12)
    assert majority_guild(roster, {n: FT for n in roster[:5]}) is None
    assert majority_guild(roster, {n: FT for n in roster[:6]}) == FT


def test_case_insensitive_roster_names():
    roster = ["Zooey", "TARN", "solo"] + names(4)
    guild_of = {"zooey": FT, "tarn": FT, "solo": FT, "p0": FT}
    assert majority_guild(roster, guild_of) == FT


def test_empty_roster():
    assert majority_guild([], {}) is None
    assert majority_guild(None, {}) is None


# ---- the DB level ----

def seed(conn, roster_rows, runs):
    """roster_rows: (name, guild|None, found, checked). runs: (roster, count)."""
    now = int(time.time())
    with conn:
        conn.execute("INSERT INTO users (id, username, pw_hash, salt, created_ts) "
                     "VALUES (1, 'g', x'00', x'00', ?)", (now,))
        conn.execute("INSERT INTO characters (id, user_id, name, world_id) "
                     "VALUES (1, 1, 'Bobby', 618)")
        conn.executemany(
            "INSERT INTO roster_classes (name_lower, world_id, name, class, found, "
            "checked_ts, guild_name, guild_checked) VALUES (?,618,?,'mystic',?,?,?,?)",
            [(n.lower(), n, found, now, g, checked)
             for n, g, found, checked in roster_rows])
        for i, (roster, count) in enumerate(runs, start=1):
            conn.execute(
                "INSERT INTO zone_runs (id, character_id, zone, started_ts, ended_ts, "
                "encounter_count, raider_count, roster_json, updated_ts) "
                "VALUES (?,1,'Freethinker Hideout',?,?,3,?,?,?)",
                (i, now, now + 60, count, json.dumps(roster), now))


def test_retag_tags_raids_and_returns_changed(conn):
    roster = names(10)
    seed(conn, [(n, FT, 1, 1) for n in roster], [(roster, 10)])
    with conn:
        assert retag_runs(conn) == 1
    assert conn.execute("SELECT guild FROM zone_runs WHERE id=1").fetchone()[0] == FT
    # idempotent: nothing changed, nothing reported
    with conn:
        assert retag_runs(conn) == 0


def test_retag_ignores_groups_below_the_raid_threshold(conn):
    """A guild pill on a six-man says something about the group, not the raid —
    and RAID_MIN_RAIDERS is where the rest of the app draws that line."""
    roster = names(6)
    seed(conn, [(n, FT, 1, 1) for n in roster], [(roster, 6)])
    with conn:
        retag_runs(conn)
    assert conn.execute("SELECT guild FROM zone_runs WHERE id=1").fetchone()[0] is None


def test_retag_clears_a_run_that_fell_under_the_threshold(conn):
    """A split can shrink a tagged run — the tag has to follow the roster."""
    roster = names(10)
    seed(conn, [(n, FT, 1, 1) for n in roster], [(roster, 10)])
    with conn:
        retag_runs(conn)
    with conn:
        conn.execute("UPDATE zone_runs SET raider_count=4, roster_json=? WHERE id=1",
                     (json.dumps(roster[:4]),))
        assert retag_runs(conn) == 1
    assert conn.execute("SELECT guild FROM zone_runs WHERE id=1").fetchone()[0] is None


def test_retag_scopes_to_one_character(conn):
    roster = names(10)
    seed(conn, [(n, FT, 1, 1) for n in roster], [(roster, 10), (roster, 10)])
    with conn:
        conn.execute("INSERT INTO characters (id, user_id, name, world_id) "
                     "VALUES (2, 1, 'Zooey', 618)")
        conn.execute("UPDATE zone_runs SET character_id=2 WHERE id=2")
        assert retag_runs(conn, character_id=1) == 1
    tags = dict(conn.execute("SELECT id, guild FROM zone_runs"))
    assert tags == {1: FT, 2: None}


def test_unchecked_rows_do_not_vote(conn):
    """Pre-v20 rows have a class and no guild answer. They must abstain, not
    read as guildless — otherwise a backfill in progress strips real tags."""
    roster = names(10)
    rows = [(n, FT, 1, 1) for n in roster[:6]] + [(n, None, 1, 0) for n in roster[6:]]
    seed(conn, rows, [(roster, 10)])
    with conn:
        retag_runs(conn)
    # 6 of 10 known, all Freethinkers -> tagged
    assert conn.execute("SELECT guild FROM zone_runs WHERE id=1").fetchone()[0] == FT
    assert set(known_guilds(conn)) == {n.lower() for n in roster[:6]}


def test_known_guilds_keeps_the_guildless(conn):
    """The NULLs are the evidence that counts AGAINST a guild, so the map has
    to carry them."""
    seed(conn, [("a", FT, 1, 1), ("b", None, 1, 1), ("c", None, 0, 0)], [])
    assert known_guilds(conn) == {"a": FT, "b": None}


# ---- the backfill queue ----

class QueueFake:
    def __init__(self, guild_of):
        self.guild_of = guild_of
        self.asked = []

    def character_by_name(self, name, world_id=618):
        self.asked.append(name)
        g = self.guild_of.get(name.lower())
        doc = {"id": 1, "name": {"first": name}, "type": {"class": "mystic"}}
        if g:
            doc["guild"] = {"name": g, "guildid": 7}
        return doc


def test_backfill_drains_the_queue_in_budgeted_bites(conn, monkeypatch):
    monkeypatch.setattr("census.guilds.GUILD_BACKFILL_PACE_S", 0)
    roster = names(10)
    seed(conn, [(n, None, 1, 0) for n in roster], [(roster, 10)])
    fake = QueueFake({n: FT for n in roster})

    first = backfill_stale_guilds(conn, fake, budget=4)
    assert (first["asked"], first["remaining"]) == (4, 6)
    second = backfill_stale_guilds(conn, fake, budget=100)
    assert (second["asked"], second["remaining"]) == (6, 0)
    assert len(fake.asked) == 10

    with conn:
        assert retag_runs(conn) == 1
    assert conn.execute("SELECT guild FROM zone_runs WHERE id=1").fetchone()[0] == FT


def test_backfill_skips_names_census_never_had(conn, monkeypatch):
    """`found=0` rows are mobs and pets — re-asking about Enynti every hour
    forever is the queue never draining."""
    monkeypatch.setattr("census.guilds.GUILD_BACKFILL_PACE_S", 0)
    seed(conn, [("Enynti", None, 0, 0), ("Zooey", None, 1, 0)], [])
    fake = QueueFake({"zooey": FT})
    report = backfill_stale_guilds(conn, fake, budget=10)
    assert fake.asked == ["Zooey"]
    assert report["remaining"] == 0


# ---- migration ----

def test_v20_columns_exist(conn):
    rc = {r[1] for r in conn.execute("PRAGMA table_info(roster_classes)")}
    assert {"guild_name", "guild_id", "guild_checked"} <= rc
    zr = {r[1] for r in conn.execute("PRAGMA table_info(zone_runs)")}
    assert "guild" in zr
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION
