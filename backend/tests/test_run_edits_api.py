"""Hand edits from the raid list: merge, unmerge, delete fights, delete a log.

The edits are fingerprint-keyed rows that the run rebuild honors, so these
tests assert through the API the way the UI drives it — including the one case
that matters most, a reparse re-applying a delete."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

BASE_TS = 1754000000
CTIME = "Fri Aug 01 20:00:00 2026"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-edits")
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
               json={"username": "edits", "password": "hunter2hunter2"})
        c.post("/api/characters", json={"name": "Bobby"})
        yield c
    mp.undo()


def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def fight(t: int, target: str) -> list[str]:
    return [
        line(t, f"YOUR Soulrot hits {target} for 250 disease damage."),
        line(t + 5, f"Aros hits {target} for 100 crushing damage."),
        line(t + 12, f"You have killed {target}."),
    ]


def log_two_zones() -> str:
    lines = [line(0, "You have entered Castle Mistmoore.")]
    lines += fight(10, "Traininglord the Unstoppable")
    lines += fight(100, "Hagfiend the Vile")
    lines += [line(200, "You have entered The Estate of Unrest.")]
    lines += fight(210, "Bonesnapper the Grim")
    return "".join(lines)


def upload(client, content: str, name: str) -> int:
    r = client.post("/api/uploads", files={"file": (name, content.encode())},
                    data={"character_name": "Bobby"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    for _ in range(300):
        s = client.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] == "ready":
            return sid
        if s["status"] == "error":
            raise AssertionError(s["error"])
        time.sleep(0.05)
    raise AssertionError("parse timed out")


def wait_ready(client, sid: int) -> None:
    for _ in range(300):
        if client.get(f"/api/sessions/{sid}").json()["session"]["status"] == "ready":
            return
        time.sleep(0.05)
    raise AssertionError("reparse timed out")


def zone_runs(client):
    return client.get("/api/zone-runs").json()["zone_runs"]


@pytest.fixture(scope="module")
def session_id(client):
    return upload(client, log_two_zones(), "night.txt")


def test_merge_then_split_round_trips(client, session_id):
    runs = zone_runs(client)
    assert len(runs) == 2

    ids = [r["id"] for r in runs]
    assert client.post("/api/zone-runs/merge", json={"ids": ids}).status_code == 200
    merged = zone_runs(client)
    assert len(merged) == 1
    assert merged[0]["encounter_count"] == 3
    # the surviving run keeps the earlier zone's label and the whole window
    assert merged[0]["zone"] == "Castle Mistmoore"

    encs = client.get(f"/api/zone-runs/{merged[0]['id']}").json()["encounters"]
    unrest = next(e for e in encs if e["name"] == "Bonesnapper the Grim")
    r = client.post(f"/api/zone-runs/{merged[0]['id']}/split",
                    json={"encounter_id": unrest["id"]})
    assert r.status_code == 200, r.text
    split = zone_runs(client)
    assert [s["encounter_count"] for s in sorted(split, key=lambda x: x["started_ts"])] == [2, 1]


def test_unmerge_undoes_a_merge(client, session_id):
    runs = zone_runs(client)
    assert [r["merged"] for r in runs] == [False, False]
    client.post("/api/zone-runs/merge", json={"ids": [r["id"] for r in runs]})
    merged = zone_runs(client)
    assert len(merged) == 1 and merged[0]["merged"] is True

    assert client.post(f"/api/zone-runs/{merged[0]['id']}/unmerge").status_code == 200
    back = zone_runs(client)
    assert len(back) == 2 and not any(r["merged"] for r in back)


def test_merge_needs_two_runs(client, session_id):
    runs = zone_runs(client)
    assert client.post("/api/zone-runs/merge", json={"ids": [runs[0]["id"]]}).status_code == 422


def test_delete_fight_shrinks_run_and_survives_reparse(client, session_id):
    runs = sorted(zone_runs(client), key=lambda r: r["started_ts"])
    mist = runs[0]
    encs = client.get(f"/api/zone-runs/{mist['id']}").json()["encounters"]
    victim = next(e for e in encs if e["name"] == "Hagfiend the Vile")

    r = client.post("/api/encounters/delete", json={"ids": [victim["id"]]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 1 and body["empty_sessions"] == []

    after = client.get(f"/api/zone-runs/{mist['id']}").json()["encounters"]
    assert [e["name"] for e in after] == ["Traininglord the Unstoppable"]
    # the file still lists, with the deleted fight no longer counted
    sess = next(s for s in client.get("/api/sessions").json()["sessions"]
                if s["id"] == session_id)
    assert sess["encounter_count"] == 2

    client.post(f"/api/sessions/{session_id}/reparse")
    wait_ready(client, session_id)
    reparsed = client.get(f"/api/zone-runs/{mist['id']}").json()["encounters"]
    assert [e["name"] for e in reparsed] == ["Traininglord the Unstoppable"]

    # and it comes back on restore
    r = client.post("/api/encounters/restore",
                    json={"fingerprints": body["fingerprints"],
                          "character_id": mist["character_id"]})
    assert r.status_code == 200, r.text
    assert len(client.get(f"/api/zone-runs/{mist['id']}").json()["encounters"]) == 2


def test_delete_whole_run_then_the_log(client, session_id):
    for run in zone_runs(client):
        r = client.delete(f"/api/zone-runs/{run['id']}")
        assert r.status_code == 200, r.text
    assert zone_runs(client) == []
    # last delete reports the now-empty upload; the raw file is still there
    empty = r.json()["empty_sessions"]
    assert [s["id"] for s in empty] == [session_id]

    assert client.delete(f"/api/sessions/{session_id}").status_code == 200
    assert client.get("/api/sessions").json()["sessions"] == []
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_edits_are_per_owner(client, session_id):
    # deleting the log forgot its edits, so the same file re-uploads clean
    sid = upload(client, log_two_zones(), "night2.txt")
    runs = zone_runs(client)
    assert [r["encounter_count"] for r in sorted(runs, key=lambda x: x["started_ts"])] == [2, 1]
    c2 = TestClient(client.app)
    c2.post("/api/auth/register",
            json={"username": "nosy", "password": "hunter2hunter2"})
    assert c2.delete(f"/api/zone-runs/{runs[0]['id']}").status_code == 404
    assert c2.delete(f"/api/sessions/{sid}").status_code == 404
    encs = client.get(f"/api/zone-runs/{runs[0]['id']}").json()["encounters"]
    assert c2.post("/api/encounters/delete",
                   json={"ids": [encs[0]["id"]]}).status_code == 404
