"""Five Planner loadouts, local to one account (schema v45)."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod


BOBBY = {"owner_key": "wuoshi:123", "owner_name": "Bobby"}
SALLY = {"owner_key": "wuoshi:456", "owner_name": "Sally"}


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
        SALLY["owner_key"]: {"owner_key": SALLY["owner_key"], "owner_name": "Sally",
                              "updated_ts": sally[0]["updated_ts"]},
        BOBBY["owner_key"]: {"owner_key": BOBBY["owner_key"], "owner_name": "Bobby",
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
