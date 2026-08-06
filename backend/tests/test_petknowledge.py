"""Phase 1 attribution: the named-pet knowledge base (prescan evidence,
cross-session learning, kill-victim guard), target-side pet unification, and
behavioral mob reclassification (refine)."""

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-petknow")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    import routers.uploads_api as uploads_api
    mp.setattr(uploads_api, "UPLOADS_DIR", tmp / "uploads")
    import pipeline.live as live
    mp.setattr(live, "RAW_DIR", tmp / "raw")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as c:
        r = c.post("/api/auth/register",
                   json={"username": "petknow", "password": "hunter2hunter2"})
        assert r.status_code == 200, r.text
        yield c
    mp.undo()


def line(ts, body):
    return f"({ts})[Thu Aug  1 21:00:00 2026] {body}\r\n"


T0 = 1722556800


def upload(client, name, lines, char="Bobby"):
    content = "".join(lines).encode()
    r = client.post("/api/uploads", files={"file": (name, content)},
                    data={"character_name": char})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    for _ in range(100):
        s = client.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] in ("ready", "error"):
            assert s["status"] == "ready", s["error"]
            return sid
        time.sleep(0.1)
    raise AssertionError("parse never finished")


def db():
    conn = sqlite3.connect(dbmod.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def entity(conn, sid, name):
    return conn.execute(
        "SELECT e.*, r.name AS rollup_name FROM entities e "
        "LEFT JOIN entities r ON r.id = e.rollup_to "
        "WHERE e.session_id=? AND e.name=?", (sid, name)).fetchall()


def test_prescan_learns_and_attributes_backwards(client):
    # the pet acts BEFORE its death evidence; prescan applies it from line 1
    sid = upload(client, "a.txt", [
        line(T0, "You have entered The Estate of Unrest."),
        line(T0 + 1, "YOU hit a training dummy for 100 crushing damage."),
        line(T0 + 2, "Ellea's Warden of Woe's Woeful Smash hits a training dummy for 300 magic damage."),
        line(T0 + 3, "Ellea's Warden of Woe hits a training dummy for 50 crushing damage."),
        line(T0 + 5, "You have killed a training dummy."),
        line(T0 + 8, "Alas, Ellea's Warden of Woe has died from pain and suffering."),
    ])
    conn = db()
    rows = entity(conn, sid, "Ellea's Warden of Woe")
    assert [r["kind"] for r in rows] == ["named_pet"]
    assert rows[0]["rollup_name"] == "Ellea"
    # the composite-garbage form must not exist
    assert not conn.execute("SELECT 1 FROM abilities WHERE name LIKE ?",
                            ("Warden of Woe%",)).fetchall()
    # The ability the pet cast is recorded as EVIDENCE and does not become a
    # label. A sighting is only as good as the entity classification behind it,
    # and taking the label straight from one is what put 108 Census-scribed
    # player spells — `Ice Comet`, `Harm Touch`, `Raging Blow` — in the pet
    # catalog off bare names the refiner had guessed at.
    cat = conn.execute(
        "SELECT unit, proc, pet_seen, source FROM ability_catalog WHERE ability_name=?",
        ("Woeful Smash",)).fetchone()
    assert cat["unit"] == "player"        # NOT 'pet' — see census/catalog.py
    assert cat["pet_seen"] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM ability_pet_sightings WHERE ability_name=? AND session_id=?",
        ("Woeful Smash", sid)).fetchone()[0] == 1
    # so nothing wears a pet badge on this evidence alone
    from census.catalog import pet_ability_names
    assert "Woeful Smash" not in pet_ability_names(conn)
    # the name itself is in the global knowledge base
    assert conn.execute("SELECT source FROM pet_names WHERE name=?",
                        ("Warden of Woe",)).fetchone()["source"] == "observed"
    conn.close()


def test_sightings_survive_a_reparse_without_inflating(client):
    """`pet_seen` counts RAIDS, not passes. The PARSE_VERSION sweep reparses
    every session, and a counter would have said "seen in 9 raids" for one."""
    lines = [
        line(T0, "You have entered The Estate of Unrest."),
        line(T0 + 2, "Ellea's Warden of Woe's Woeful Smash hits a training dummy for 300 magic damage."),
        line(T0 + 8, "Alas, Ellea's Warden of Woe has died from pain and suffering."),
    ]
    sid = upload(client, "c.txt", lines)
    conn = db()
    conn.isolation_level = None
    before = conn.execute("SELECT pet_seen FROM ability_catalog WHERE ability_name=?",
                          ("Woeful Smash",)).fetchone()["pet_seen"]
    # the same evidence stated again, which is what a reparse does
    from census.catalog import observe_pet_abilities
    with conn:
        observe_pet_abilities(conn, {"Woeful Smash"}, sid)
        observe_pet_abilities(conn, {"Woeful Smash"}, sid)
    after = conn.execute("SELECT pet_seen FROM ability_catalog WHERE ability_name=?",
                         ("Woeful Smash",)).fetchone()["pet_seen"]
    assert after == before
    # a DIFFERENT raid seeing it is real corroboration and does count
    with conn:
        observe_pet_abilities(conn, {"Woeful Smash"}, sid + 1000)
    assert conn.execute("SELECT pet_seen FROM ability_catalog WHERE ability_name=?",
                        ("Woeful Smash",)).fetchone()["pet_seen"] == before + 1
    conn.close()


def test_a_ruling_beats_the_curated_seed(client):
    """`ability_rulings` is the top of the ladder — it is how a human fixes
    both a missing label and a wrong one (routers/admin_api.rule_ability)."""
    from census.catalog import pet_ability_names, proc_ability_names
    conn = db()
    curated_pet = sorted(pet_ability_names(conn))[0]
    with conn:
        # take a curated pet ability back off
        conn.execute(
            "INSERT INTO ability_rulings (ability_name, unit, fires, decided_ts) "
            "VALUES (?, 'player', 'cast', 0)", (curated_pet,))
        # and promote something the seed never claimed
        conn.execute(
            "INSERT INTO ability_rulings (ability_name, unit, fires, grant_kind, "
            "grant_name, grant_class, decided_ts) "
            "VALUES ('Woeful Smash', 'pet', 'cast', 'pet', 'Warden of Woe', 'fury', 0)")
    assert curated_pet not in pet_ability_names(conn)
    assert "Woeful Smash" in pet_ability_names(conn)
    assert "Woeful Smash" not in proc_ability_names(conn)
    with conn:
        conn.execute("DELETE FROM ability_rulings")
    assert curated_pet in pet_ability_names(conn)   # back to the seed
    conn.close()


def test_learned_name_applies_to_later_sessions(client):
    # no death evidence in THIS file — knowledge carried from the previous one
    sid = upload(client, "b.txt", [
        line(T0 + 100, "You have entered The Estate of Unrest."),
        line(T0 + 101, "YOU hit a training dummy for 100 crushing damage."),
        line(T0 + 102, "Ellea's Warden of Woe hits a training dummy for 55 crushing damage."),
        line(T0 + 104, "You have killed a training dummy."),
    ])
    conn = db()
    rows = entity(conn, sid, "Ellea's Warden of Woe")
    assert [r["kind"] for r in rows] == ["named_pet"]
    conn.close()


def test_kill_victim_guard_rejects_named_adds(client):
    # "Garanel's Shade" dies via a player kill line -> a mob add, not a pet
    sid = upload(client, "c.txt", [
        line(T0 + 200, "You have entered The Estate of Unrest."),
        line(T0 + 201, "Garanel's Shade hits YOU for 500 magic damage."),
        line(T0 + 202, "YOU hit Garanel's Shade for 900 crushing damage."),
        line(T0 + 204, "You have killed Garanel's Shade."),
        line(T0 + 208, "Alas, Garanel's Shade has died from pain and suffering."),
    ])
    conn = db()
    assert conn.execute("SELECT 1 FROM pet_names WHERE name=?",
                        ("Shade",)).fetchone() is None
    assert not entity(conn, sid, "Garanel's Shade") or all(
        r["kind"] != "named_pet" for r in entity(conn, sid, "Garanel's Shade"))
    conn.close()


def test_target_side_pet_resolves_to_same_entity(client):
    # damage dealt BY and taken BY a swarm pet lands on one entity row
    sid = upload(client, "d.txt", [
        line(T0 + 300, "You have entered The Estate of Unrest."),
        line(T0 + 301, "Ellea's blighted horde hits a training dummy for 200 cold damage."),
        line(T0 + 302, "a training dummy hits Ellea's blighted horde for 80 crushing damage."),
        line(T0 + 303, "YOU hit a training dummy for 10 crushing damage."),
        line(T0 + 305, "You have killed a training dummy."),
    ])
    conn = db()
    rows = entity(conn, sid, "Ellea's blighted horde")
    assert len(rows) == 1 and rows[0]["kind"] == "swarm_pet"
    assert rows[0]["rollup_name"] == "Ellea"
    conn.close()


def test_behavioral_mob_reclassification_and_label(client):
    # single-token capitalized boss: kill-victim evidence -> mob + named fight
    sid = upload(client, "e.txt", [
        line(T0 + 400, "You have entered Vault of Ssraeshza."),
        line(T0 + 401, "Venekor hits YOU for 1,000 slashing damage."),
        line(T0 + 402, "Venekor hits Sorengail for 900 slashing damage."),
        line(T0 + 403, "YOU hit Venekor for 500 crushing damage."),
        line(T0 + 404, "Sorengail heals YOU for 400 hit points."),
        line(T0 + 405, "Shaly's Backstab hits Venekor for 700 piercing damage."),
        line(T0 + 406, "Sorengail's Smite hits Venekor for 300 divine damage."),
        line(T0 + 408, "Shaly has killed Venekor."),
    ])
    conn = db()
    rows = entity(conn, sid, "Venekor")
    assert [r["kind"] for r in rows] == ["mob"]
    enc = conn.execute("SELECT name, is_named FROM encounters WHERE session_id=?",
                       (sid,)).fetchone()
    assert enc["name"] == "Venekor" and enc["is_named"] == 1
    conn.close()
