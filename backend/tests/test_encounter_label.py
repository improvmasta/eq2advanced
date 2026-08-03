"""Encounters are titled after the enemy the raid FOUGHT, not the one that
died — the change that makes a wipe expressible at all.

Before this, `name` came from `has killed <Named>`, so a wipe produced no kill
line, got the label "trash", and `success` had no code path that could return
0. Lindsay's Emerald Halls night therefore reported 9/9 named while two
Galiel Spirithoof wipes and a Farstride Unicorn wipe sat unnamed in the trash
list. These tests pin the replacement, including ACT's behaviour of titling a
fight after an add when the add soaked most of the damage."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

BASE_TS = 1754300000
CTIME = "Sun Aug 02 21:00:00 2026"


def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def swing(t: int, tgt: str, amount: int = 500, who: str = "YOUR Soulrot") -> str:
    verb = "hits" if who.startswith("YOUR") else "hits"
    return line(t, f"{who} {verb} {tgt} for {amount} disease damage.")


def log() -> str:
    out = [line(0, "You have entered The Emerald Halls.")]

    # 1. a wipe: the raid engages a named, everyone dies, nothing is killed
    out += [
        swing(10, "Galiel Spirithoof", 900),
        swing(14, "Galiel Spirithoof", 900),
        line(16, "Galiel Spirithoof has killed Aros."),
        line(18, "Galiel Spirithoof has killed Bobby."),
    ]
    # 2. the re-pull, killed this time
    out += [
        swing(200, "Galiel Spirithoof", 1200),
        swing(205, "Galiel Spirithoof", 1200),
        line(208, "You have killed Galiel Spirithoof."),
    ]
    # 3. plain trash, killed
    out += [
        swing(400, "a living totem", 300),
        swing(404, "a living totem", 300),
        line(406, "You have killed a living totem."),
    ]
    # 4. a named pull where an add soaked most of the damage — ACT titles this
    #    after the add, and so do we
    out += [
        swing(600, "a knotted guardian", 5000),
        swing(604, "a knotted guardian", 5000),
        swing(606, "Treah Greenroot", 400),
        line(610, "a knotted guardian has killed Bobby."),
    ]
    return "".join(out)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-labels")
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
               json={"email": "labels@x.test", "password": "hunter2hunter2"})
        c.post("/api/characters", json={"name": "Bobby"})
        yield c
    mp.undo()


@pytest.fixture(scope="module")
def encs(client):
    r = client.post("/api/uploads", files={"file": ("a.txt", log().encode())},
                    data={"character_name": "Bobby"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    for _ in range(300):
        s = client.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] == "ready":
            break
        if s["status"] == "error":
            raise AssertionError(s["error"])
        time.sleep(0.05)
    else:
        raise AssertionError("parse timed out")
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    run = next(r for r in runs if r["zone"] == "The Emerald Halls")
    detail = client.get(f"/api/zone-runs/{run['id']}").json()
    return {"run": run, "encounters": detail["encounters"]}


def test_every_fight_is_named_after_its_enemy(encs):
    """Including the trash, the way ACT lists it."""
    assert [e["name"] for e in encs["encounters"]] == [
        "Galiel Spirithoof",        # the wipe
        "Galiel Spirithoof",        # the kill
        "a living totem",
        "a knotted guardian",       # the add out-damaged Treah
    ]


def test_wipe_is_named_but_unsuccessful(encs):
    wipe, kill = encs["encounters"][0], encs["encounters"][1]
    assert wipe["is_named"] == 1 and wipe["success"] == 0
    assert kill["is_named"] == 1 and kill["success"] == 1


def test_trash_carries_a_real_success_too(encs):
    totem = encs["encounters"][2]
    assert totem["is_named"] == 0
    assert totem["success"] == 1          # it died

    guardian = encs["encounters"][3]
    assert guardian["is_named"] == 0      # articled name -> not a named mob
    assert guardian["success"] == 0       # the raid wiped on it


def test_run_counts_attempts_not_just_kills(encs):
    """The tautology this replaces: named_count used to equal success_count by
    construction, so a run could only ever read N/N."""
    run = encs["run"]
    assert run["named_count"] == 2        # two Galiel pulls
    assert run["success_count"] == 1      # one of them died
