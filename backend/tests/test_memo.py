"""The read cache must never outlive the data.

`/zone-runs/{id}/report` and `/encounters/agg` are memoized (memo.py) because
replaying a 60-fight night's events on every zone click is what made the site
feel like it was reloading. The bug that buys is a stale total after an edit,
so these tests pin the invalidation, not the speed."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
import memo

BASE_TS = 1754500000
CTIME = "Sun Aug 02 20:00:00 2026"


def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def log() -> str:
    out = [line(0, "You have entered The Emerald Halls.")]
    for name, start in (("Sawtooth the Ancient", 10), ("Treah Greenroot", 200)):
        for i in range(3):
            out.append(line(start + i * 3,
                            f"YOUR Soulrot hits {name} for 900 disease damage."))
        out.append(line(start + 8, f"You have killed {name}."))
    return "".join(out)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-memo")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as c:
        c.post("/api/auth/register",
               json={"username": "memo", "password": "hunter2hunter2"})
        c.post("/api/characters", json={"name": "Bobby"})
        r = c.post("/api/uploads", files={"file": ("a.txt", log().encode())},
                   data={"character_name": "Bobby"})
        sid = r.json()["session_id"]
        for _ in range(300):
            s = c.get(f"/api/sessions/{sid}").json()["session"]
            if s["status"] == "ready":
                break
            if s["status"] == "error":
                raise AssertionError(s["error"])
            time.sleep(0.05)
        else:
            raise AssertionError("parse timed out")
        yield c
    mp.undo()


def test_memo_returns_the_same_object_until_a_write(client):
    run = client.get("/api/zone-runs").json()["zone_runs"][0]
    first = client.get(f"/api/zone-runs/{run['id']}/report").json()
    assert len(first["encounters"]) == 2
    again = client.get(f"/api/zone-runs/{run['id']}/report").json()
    assert again == first
    assert memo.stats()["entries"] >= 1


def test_deleting_a_fight_drops_the_cached_report(client):
    """The failure this guards: the report is cached, the fight is deleted, and
    the page keeps showing a night that no longer exists."""
    run = client.get("/api/zone-runs").json()["zone_runs"][0]
    encs = client.get(f"/api/zone-runs/{run['id']}").json()["encounters"]
    before = client.get(f"/api/zone-runs/{run['id']}/report").json()
    assert len(before["encounters"]) == 2
    epoch = memo.stats()["epoch"]

    victim = next(e for e in encs if e["name"] == "Treah Greenroot")
    assert client.post("/api/encounters/delete",
                       json={"ids": [victim["id"]]}).status_code == 200
    assert memo.stats()["epoch"] > epoch

    after = client.get(f"/api/zone-runs/{run['id']}/report").json()
    assert [e["encounter"]["name"] for e in after["encounters"]] == \
        ["Sawtooth the Ancient"]


def test_agg_is_cached_per_encounter_set(client):
    run = client.get("/api/zone-runs").json()["zone_runs"][0]
    encs = client.get(f"/api/zone-runs/{run['id']}").json()["encounters"]
    ids = ",".join(str(e["id"]) for e in encs)
    one = client.get(f"/api/encounters/agg?ids={ids}").json()
    two = client.get(f"/api/encounters/agg?ids={ids}").json()
    assert one == two
    # a different selection is a different key, not a stale hit
    single = client.get(f"/api/encounters/agg?ids={encs[0]['id']}").json()
    assert single["encounter"]["id"] == encs[0]["id"]
    assert single["encounter"]["duration_s"] <= one["encounter"]["duration_s"]
