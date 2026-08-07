"""Hiding fights and whole raids.

Hiding is the reversible half of the raid list's edits, and it makes a claim
delete does not: the fight is still THE OWNER'S — listed, restorable, readable
from their own page — and is gone for everyone the raid was shared with. So the
tests are written from both sides of one share:

  * the owner's payload still carries it, flagged
  * the groupmate's does not, and neither does the aggregate endpoint if they
    guess the id — "absent from the list we sent" is not an access rule
  * it stops counting: encounter_count, combat_s and the raid report
  * a reparse re-applies it, like every other fingerprint-keyed edit
  * a raid with every fight hidden leaves the groupmate's list entirely, and
    comes back whole when the owner puts it back
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

BASE_TS = 1754200000
CTIME = "Sat Aug 02 21:00:00 2026"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-hide")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    import routers.uploads_api as uploads_api
    mp.setattr(uploads_api, "UPLOADS_DIR", tmp / "uploads")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as c:
        yield c
    mp.undo()


def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def fight(t: int, target: str) -> list[str]:
    return [
        line(t, f"YOUR Soulrot hits {target} for 250 disease damage."),
        line(t + 3, f"Aros hits {target} for 100 crushing damage."),
        line(t + 6, f"Tasrin hits {target} for 90 mental damage."),
        line(t + 10, f"You have killed {target}."),
    ]


def one_night() -> str:
    lines = [line(0, "You have entered The Estate of Unrest.")]
    lines += fight(10, "Bonesnapper the Grim")
    lines += fight(100, "Hagfiend the Vile")
    lines += fight(200, "Traininglord the Unstoppable")
    return "".join(lines)


def sign_in(c, username, fresh=False):
    c.cookies.clear()
    body = {"username": username, "password": "hunter2hunter2"}
    if fresh:
        body |= {"sq_id": 1, "answer": "pet"}
    r = c.post(f"/api/auth/{'register' if fresh else 'login'}", json=body)
    assert r.status_code == 200, r.text
    return r.json()["user"]


def upload(c, content: str, name: str) -> int:
    r = c.post("/api/uploads", files={"file": (name, content.encode())},
               data={"character_name": "Bobby"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    for _ in range(300):
        s = c.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] == "ready":
            return sid
        if s["status"] == "error":
            raise AssertionError(s["error"])
        time.sleep(0.05)
    raise AssertionError("parse timed out")


def wait_ready(c, sid: int) -> None:
    for _ in range(300):
        if c.get(f"/api/sessions/{sid}").json()["session"]["status"] == "ready":
            return
        time.sleep(0.05)
    raise AssertionError("reparse timed out")


@pytest.fixture(scope="module")
def world(client):
    """An owner's three-fight night, shared with a group a mate is in."""
    sign_in(client, "hider", fresh=True)
    session_id = upload(client, one_night(), "night.txt")
    runs = client.get("/api/zone-runs?scope=mine").json()["zone_runs"]
    assert len(runs) == 1, runs
    run = runs[0]
    group = client.post("/api/groups", json={"name": "Saturday"}).json()["group"]
    code = client.get(f"/api/groups/{group['id']}").json()["group"]["join_code"]

    sign_in(client, "mate", fresh=True)
    assert client.post("/api/groups/join", json={"code": code}).status_code == 200

    sign_in(client, "hider")
    r = client.put(f"/api/zone-runs/{run['id']}/shares", json={"group_ids": [group["id"]]})
    assert r.status_code == 200, r.text
    return {"session_id": session_id, "run_id": run["id"],
            "character_id": run["character_id"], "group": group}


def detail(c, run_id):
    return c.get(f"/api/zone-runs/{run_id}").json()


def named(encs):
    return sorted(e["name"] for e in encs)


def test_a_hidden_fight_is_the_owners_alone(client, world):
    run_id = world["run_id"]
    sign_in(client, "hider")
    before = detail(client, run_id)
    assert len(before["encounters"]) == 3
    assert before["zone_run"]["encounter_count"] == 3
    victim = next(e for e in before["encounters"] if e["name"] == "Hagfiend the Vile")

    r = client.post("/api/encounters/hide", json={"ids": [victim["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["hidden"] is True and r.json()["count"] == 1

    # the owner still has it, flagged, and the run says how much is held back
    mine = detail(client, run_id)
    assert len(mine["encounters"]) == 3
    assert [e["hidden"] for e in mine["encounters"]] == [0, 1, 0]
    assert mine["zone_run"]["encounter_count"] == 2
    assert mine["zone_run"]["hidden_count"] == 1
    assert mine["zone_run"]["combat_s"] < before["zone_run"]["combat_s"]
    # ...and can still read it: hiding is about who else sees it
    assert client.get(f"/api/encounters/agg?ids={victim['id']}").status_code == 200

    # the groupmate is never told it exists — not in the payload, and not
    # readable by id either
    sign_in(client, "mate")
    theirs = detail(client, run_id)
    assert named(theirs["encounters"]) == [
        "Bonesnapper the Grim", "Traininglord the Unstoppable"]
    assert theirs["zone_run"]["encounter_count"] == 2
    # how much the owner held back is the owner's business
    assert theirs["zone_run"]["hidden_count"] == 0
    assert client.get(f"/api/encounters/agg?ids={victim['id']}").status_code == 404
    assert client.get(f"/api/encounters/{victim['id']}").status_code == 404
    # the Compare picker tells the same story: its named-mob facet rides
    # `?roster=1`, so a hidden pull is not a boss the groupmate can search for
    def picker_named(c):
        runs = c.get("/api/zone-runs?roster=1").json()["zone_runs"]
        return [n["name"] for r in runs if r["id"] == run_id for n in r["named"]]
    assert "Hagfiend the Vile" not in picker_named(client)
    sign_in(client, "hider")
    assert "Hagfiend the Vile" in picker_named(client)
    # and it is out of the raid report for BOTH of them — hiding a pull is
    # exactly a claim that it should stop counting
    for who in ("mate", "hider"):
        sign_in(client, who)
        report = client.get(f"/api/zone-runs/{run_id}/report").json()
        assert sorted(r["encounter"]["name"] for r in report["encounters"]) == [
            "Bonesnapper the Grim", "Traininglord the Unstoppable"]


def test_hiding_survives_a_reparse_and_undoes_cleanly(client, world):
    run_id, session_id = world["run_id"], world["session_id"]
    sign_in(client, "hider")
    client.post(f"/api/sessions/{session_id}/reparse")
    wait_ready(client, session_id)

    mine = detail(client, run_id)
    assert len(mine["encounters"]) == 3
    hidden = [e for e in mine["encounters"] if e["hidden"]]
    assert [e["name"] for e in hidden] == ["Hagfiend the Vile"]
    assert mine["zone_run"]["encounter_count"] == 2

    r = client.post("/api/encounters/hide",
                    json={"ids": [hidden[0]["id"]], "hidden": False})
    assert r.status_code == 200, r.text
    back = detail(client, run_id)
    assert [e["hidden"] for e in back["encounters"]] == [0, 0, 0]
    assert back["zone_run"]["encounter_count"] == 3
    assert back["zone_run"]["hidden_count"] == 0


def test_hiding_a_whole_raid_takes_it_off_the_shared_list(client, world):
    run_id = world["run_id"]
    sign_in(client, "hider")
    assert client.post(f"/api/zone-runs/{run_id}/hide", json={}).status_code == 200

    # the owner keeps it — with nothing counted, which is the state the page
    # offers "Show raid" from
    mine = detail(client, run_id)
    assert len(mine["encounters"]) == 3
    assert all(e["hidden"] for e in mine["encounters"])
    assert mine["zone_run"]["encounter_count"] == 0
    assert mine["zone_run"]["hidden_count"] == 3
    listed = client.get("/api/zone-runs").json()["zone_runs"]
    assert [r["id"] for r in listed] == [run_id]
    assert listed[0]["hidden"] is True
    # It still describes the KIND of night it was. `raider_count` is what
    # partitions the list into Raids and Solo/Group, and the window is what
    # sorts it — blank either one and a hidden raid falls off its owner's own
    # list, which leaves no way back to the switch that un-hides it.
    assert listed[0]["raider_count"] == 3
    assert listed[0]["ended_ts"] > listed[0]["started_ts"]

    # for the groupmate the raid simply is not there, by any door
    sign_in(client, "mate")
    assert client.get("/api/zone-runs").json()["zone_runs"] == []
    assert client.get("/api/zone-runs?scope=shared").json()["zone_runs"] == []
    assert client.get(f"/api/zone-runs/{run_id}").status_code == 404
    assert client.get(f"/api/zone-runs/{run_id}/report").status_code == 404

    sign_in(client, "hider")
    r = client.post(f"/api/zone-runs/{run_id}/hide", json={"hidden": False})
    assert r.status_code == 200, r.text
    sign_in(client, "mate")
    given_back = detail(client, run_id)
    assert len(given_back["encounters"]) == 3
    assert given_back["zone_run"]["encounter_count"] == 3


def test_only_the_owner_may_hide(client, world):
    run_id = world["run_id"]
    sign_in(client, "hider")
    enc = detail(client, run_id)["encounters"][0]

    # a groupmate can READ this raid and still cannot change it
    sign_in(client, "mate")
    assert client.post("/api/encounters/hide",
                       json={"ids": [enc["id"]]}).status_code == 404
    assert client.post(f"/api/zone-runs/{run_id}/hide", json={}).status_code == 403
    # signed out is the same answer
    client.cookies.clear()
    assert client.post("/api/encounters/hide",
                       json={"ids": [enc["id"]]}).status_code == 401
