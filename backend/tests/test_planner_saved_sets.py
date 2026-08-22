"""Character-scoped Planner loadouts and acquisition progress (schema v52)."""

import json
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod


BOBBY = {"owner_key": "wuoshi:bobby", "owner_name": "Bobby (Wuoshi)"}
SALLY = {"owner_key": "wuoshi:sally", "owner_name": "Sally (Wuoshi)"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-planner-saved")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as test_client:
        test_client.post("/api/auth/register",
                         json={"username": "planner", "password": "hunter2hunter2"})
        yield test_client
    mp.undo()


@pytest.fixture(autouse=True)
def signed_in(client):
    client.cookies.clear()
    response = client.post("/api/auth/login",
                           json={"username": "planner", "password": "hunter2hunter2"})
    assert response.status_code == 200
    with dbmod.get_db():
        dbmod.get_db().execute("DELETE FROM planner_saved_sets")
        dbmod.get_db().execute("DELETE FROM planner_obtained_items")
    yield


def test_every_account_starts_with_five_named_empty_slots(client):
    response = client.get("/api/plan/saved-sets", params={"owner_key": BOBBY["owner_key"]})
    assert response.status_code == 200
    assert response.json()["sets"] == [
        {"slot": slot, "name": f"Set {slot}", "payload": None,
         "updated_ts": None}
        for slot in range(1, 6)
    ]


def test_a_slot_can_be_renamed_and_replaced(client):
    payload = {"version": 1, "shortlist": {"items": [{"name": "Hat"}]}}
    response = client.put("/api/plan/saved-sets/3",
                          json={**BOBBY, "name": "  Raid   build  ", "payload": payload})
    assert response.status_code == 200
    assert response.json()["set"]["name"] == "Raid build"
    assert response.json()["set"]["payload"] == payload
    assert client.get("/api/plan/saved-sets",
                      params={"owner_key": BOBBY["owner_key"]}).json()["sets"][2]["payload"] == payload


def test_each_character_gets_an_independent_five_slots(client):
    client.put("/api/plan/saved-sets/1",
               json={**BOBBY, "name": "Bobby raid", "payload": {"version": 1}})
    client.put("/api/plan/saved-sets/1",
               json={**SALLY, "name": "Sally solo", "payload": {"version": 1}})
    bobby = client.get("/api/plan/saved-sets",
                       params={"owner_key": BOBBY["owner_key"]}).json()["sets"]
    sally = client.get("/api/plan/saved-sets",
                       params={"owner_key": SALLY["owner_key"]}).json()["sets"]
    assert bobby[0]["name"] == "Bobby raid"
    assert sally[0]["name"] == "Sally solo"
    owners = {row["owner_key"]: row
              for row in client.get("/api/plan/saved-set-owners").json()["characters"]}
    assert owners == {
        SALLY["owner_key"]: {"owner_key": SALLY["owner_key"],
                              "owner_name": "Sally (Wuoshi)",
                              "lookup_name": "Sally",
                              "updated_ts": sally[0]["updated_ts"]},
        BOBBY["owner_key"]: {"owner_key": BOBBY["owner_key"],
                              "owner_name": "Bobby (Wuoshi)",
                              "lookup_name": "Bobby",
                              "updated_ts": bobby[0]["updated_ts"]},
    }


def test_same_public_character_has_independent_sets_per_account_and_requires_login(client):
    client.put("/api/plan/saved-sets/1",
               json={**BOBBY, "name": "Mine", "payload": {"version": 1}})
    client.post("/api/auth/register",
                json={"username": f"other{uuid.uuid4().hex[:8]}",
                      "password": "hunter2hunter2"})
    assert client.get("/api/plan/saved-sets",
                      params={"owner_key": BOBBY["owner_key"]}).json()["sets"][0]["payload"] is None
    client.cookies.clear()
    assert client.get("/api/plan/saved-sets",
                      params={"owner_key": BOBBY["owner_key"]}).status_code == 401
    assert client.put("/api/plan/saved-sets/1",
                      json={**BOBBY, "name": "No", "payload": None}).status_code == 401


def test_slot_bounds_and_payload_size_are_enforced(client):
    assert client.put("/api/plan/saved-sets/6",
                      json={**BOBBY, "name": "No", "payload": None}).status_code == 400
    huge = {"value": "x" * 400_001}
    assert client.put("/api/plan/saved-sets/1",
                      json={**BOBBY, "name": "No", "payload": huge}).status_code == 400


def test_delete_removes_the_row_and_last_real_owner_folder(client):
    client.put("/api/plan/saved-sets/1",
               json={**BOBBY, "name": "Raid", "payload": {"version": 3}})
    assert client.delete("/api/plan/saved-sets/1",
                         params={"owner_key": BOBBY["owner_key"]}).status_code == 200
    assert client.get("/api/plan/saved-set-owners").json()["characters"] == []
    assert client.get("/api/plan/saved-sets",
                      params={"owner_key": BOBBY["owner_key"]}).json()["sets"][0]["payload"] is None


def test_legacy_null_put_also_deletes_instead_of_leaving_an_empty_folder(client):
    client.put("/api/plan/saved-sets/2",
               json={**BOBBY, "name": "Solo", "payload": {"version": 3}})
    response = client.put("/api/plan/saved-sets/2",
                          json={**BOBBY, "name": "Set 2", "payload": None})
    assert response.status_code == 200 and response.json()["set"]["payload"] is None
    assert client.get("/api/plan/saved-set-owners").json()["characters"] == []


def test_saved_set_identity_must_be_canonical_and_match_the_display_name(client):
    assert client.put("/api/plan/saved-sets/1", json={
        "owner_key": "wuoshi:123", "owner_name": "Bobby (Wuoshi)",
        "name": "No", "payload": {"version": 3},
    }).status_code == 400
    assert client.put("/api/plan/saved-sets/1", json={
        "owner_key": "wuoshi:bobby", "owner_name": "Sally (Wuoshi)",
        "name": "No", "payload": {"version": 3},
    }).status_code == 400


def test_stale_browser_adoption_cannot_overwrite_a_newer_user_save(client):
    first = client.put("/api/plan/saved-sets/1", json={
        **BOBBY, "name": "First", "payload": {"version": 3, "value": 1},
    }).json()["set"]
    newer = client.put("/api/plan/saved-sets/1", json={
        **BOBBY, "name": "Newer", "payload": {"version": 3, "value": 2},
    }).json()["set"]
    assert newer["updated_ts"] > first["updated_ts"]
    stale = client.put("/api/plan/saved-sets/1", json={
        **BOBBY, "name": "Stale fallback", "payload": {"version": 3, "value": 0},
        "base_updated_ts": first["updated_ts"],
    })
    assert stale.status_code == 409
    current = client.get("/api/plan/saved-sets",
                         params={"owner_key": BOBBY["owner_key"]}).json()["sets"][0]
    assert current["name"] == "Newer" and current["payload"]["value"] == 2


def test_canonical_key_migration_keeps_newest_and_recovers_collision(client):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE planner_saved_sets (
        user_id INTEGER, owner_key TEXT, owner_name TEXT, slot INTEGER,
        name TEXT, payload_json TEXT, updated_ts INTEGER,
        PRIMARY KEY(user_id, owner_key, slot));
      CREATE TABLE planner_saved_set_recovery (
        id INTEGER PRIMARY KEY, user_id INTEGER, owner_key TEXT, owner_name TEXT,
        slot INTEGER, name TEXT, payload_json TEXT, updated_ts INTEGER,
        reason TEXT, recovered_ts INTEGER,
        UNIQUE(user_id, owner_key, slot, payload_json, reason));
    """)
    old = {"version": 2, "shortlist": {"owner": {
        "key": "wuoshi:123", "name": "Bobby"}, "items": ["Old"]}}
    new = {"version": 3, "shortlist": {"owner": {
        "key": "wuoshi:bobby", "lookup_name": "Bobby",
        "name": "Bobby (Wuoshi)"}, "items": ["New"]}}
    conn.executemany("INSERT INTO planner_saved_sets VALUES(?,?,?,?,?,?,?)", [
        (1, "wuoshi:123", "Bobby", 1, "Old", json.dumps(old), 10),
        (1, "wuoshi:bobby", "Bobby (Wuoshi)", 1, "New", json.dumps(new), 20),
    ])
    dbmod._canonicalize_planner_saved_sets(conn)
    row = conn.execute("SELECT * FROM planner_saved_sets").fetchone()
    assert row["owner_key"] == "wuoshi:bobby" and row["name"] == "New"
    assert json.loads(row["payload_json"])["shortlist"]["owner"]["key"] == \
        "wuoshi:bobby"
    assert conn.execute("SELECT COUNT(*) FROM planner_saved_set_recovery").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM planner_saved_sets_v51").fetchone()[0] == 2
    # The shape marker must not reintroduce or duplicate anything on restart.
    dbmod._canonicalize_planner_saved_sets(conn)
    assert conn.execute("SELECT COUNT(*) FROM planner_saved_sets").fetchone()[0] == 1


def _reconcile_body(items):
    return {
        "owner_key": BOBBY["owner_key"], "lookup_name": "Bobby",
        "display_name": BOBBY["owner_name"], "world": "Wuoshi",
        "items": items,
    }


def test_obtained_items_are_additive_after_the_character_unequips_them(client):
    first = client.post("/api/plan/obtained-items/reconcile", json=_reconcile_body([
        {"item_key": "census:12345", "item_name": "Raid Signet",
         "source": "equipped:census"},
    ]))
    assert first.status_code == 200
    assert first.json()["added"] == ["census:12345"]

    empty = client.post("/api/plan/obtained-items/reconcile",
                        json=_reconcile_body([]))
    assert empty.status_code == 200 and empty.json()["added"] == []
    saved = client.get("/api/plan/obtained-items",
                       params={"owner_key": BOBBY["owner_key"]}).json()["items"]
    assert [row["item_key"] for row in saved] == ["census:12345"]


def test_obtained_item_validation_is_bounded_and_private(client):
    bad = client.post("/api/plan/obtained-items/reconcile", json=_reconcile_body([
        {"item_key": "fuzzy:hat", "item_name": "Hat", "source": "equipped:census"},
    ]))
    assert bad.status_code == 400
    client.cookies.clear()
    assert client.get("/api/plan/obtained-items",
                      params={"owner_key": BOBBY["owner_key"]}).status_code == 401
