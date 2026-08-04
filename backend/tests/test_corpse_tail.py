"""A dead mob's DoT keeps ticking, and it is not the fight.

Pinned to ACT's parse of Lindsay's Freeport pull (2026-08-04 12:31): ACT read
the fight as 28s ending on the kill and opened a second encounter for the tick
that landed 4s later, while we ran the fight to 32s — 12% off the EncDPS and
32 damage on the mob's row that ACT did not count.

The second half of this file is the regression guard that matters more: the
cut is a SUFFIX operation. Every variant that cut whenever the engaged mobs
were all dead split chain pulls down the middle and produced 74-149 encounters
on the Emerald Halls night where ACT counted 61.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

BASE_TS = 1785861081
CTIME = "Tue Aug 04 12:31:21 2026"


def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def parse(client, text: str) -> int:
    r = client.post("/api/uploads", files={"file": ("log.txt", text.encode())},
                    data={"character_name": "Bobby"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    for _ in range(400):
        s = client.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] == "ready":
            return sid
        if s["status"] == "error":
            raise AssertionError(s["error"])
        time.sleep(0.05)
    raise AssertionError("parse timed out")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-corpse")
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
               json={"username": "corpse", "password": "hunter2hunter2"})
        c.post("/api/characters", json={"name": "Bobby"})
        yield c
    mp.undo()


def encounters_of(client, sid):
    return client.get(f"/api/sessions/{sid}").json()["encounters"]


FREEPORT = "".join([
    line(0, "You have entered The City of Freeport."),
    line(1, "YOUR Lifetap hits Velna T`Kril for 20000 disease damage."),
    line(2, "Velna T`Kril's Ruin hits YOU for 31 slashing damage."),
    line(7, "YOUR Bloodcoil hits Velna T`Kril for 8634 disease damage."),
    line(12, "Velna T`Kril's Ruin hits YOU for 29 slashing damage."),
    line(13, "You have killed Velna T`Kril."),
    # the corpse is still ticking
    line(17, "Velna T`Kril's Ruin hits YOU for 32 slashing damage."),
])


def test_the_fight_ends_on_the_kill(client):
    encs = encounters_of(client, parse(client, FREEPORT))
    fight = encs[0]
    assert fight["name"] == "Velna T`Kril"
    # start to kill, not start to last tick — the 4s of corpse DoT is what put
    # our EncDPS at 894.8 against ACT's 1,022.64 on the same 28,634 damage
    assert fight["duration_s"] == 12


def test_the_tick_lands_in_its_own_encounter(client):
    """ACT's tree showed two encounters in that zone, the second [00:00]."""
    encs = encounters_of(client, parse(client, FREEPORT))
    assert len(encs) == 2
    assert encs[1]["started_ts"] - encs[0]["started_ts"] == 16
    # ACT's export of that stub is titled after the mob, not "trash"
    assert encs[1]["name"] == "Velna T`Kril"


def test_the_mobs_damage_excludes_its_posthumous_tick(client):
    sid = parse(client, FREEPORT)
    fight = encounters_of(client, sid)[0]
    rows = client.get(f"/api/encounters/{fight['id']}").json()["actors"]
    bobby = next(r for r in rows if r["name"] == "Bobby")
    velna = next(r for r in rows if r["name"] == "Velna T`Kril")
    assert bobby["damage"] == 28_634
    assert velna["damage"] == 60          # 31 + 29, not the 32 that followed
    assert bobby["damage_taken"] == 60


def test_a_chain_pull_is_still_one_fight(client):
    """The guard on the whole design: the next pull inside the silence window
    continues the encounter. Cutting at the kill instead doubled Emerald
    Halls' encounter count."""
    text = "".join([
        line(0, "You have entered The City of Freeport."),
        line(1, "YOUR Lifetap hits a Freeport conscript for 5000 disease damage."),
        line(4, "You have killed a Freeport conscript."),
        line(7, "YOUR Lifetap hits a Freeport militiaman for 5000 disease damage."),
        line(10, "You have killed a Freeport militiaman."),
    ])
    encs = encounters_of(client, parse(client, text))
    assert len(encs) == 1
    assert encs[0]["duration_s"] == 9         # first swing to the last kill


def test_a_live_add_does_not_open_a_new_encounter(client):
    """A live mob still swinging is not a corpse: its damage stays in the
    fight (one encounter, no stub). The clock still stops at the group's last
    action — ACT's export of the knotted guardian wipe reads 40s while the
    mobs go on hitting bodies for another 3s."""
    text = "".join([
        line(0, "You have entered The City of Freeport."),
        line(1, "YOUR Lifetap hits a Freeport conscript for 5000 disease damage."),
        line(3, "a Freeport militiaman hits YOU for 100 crushing damage."),
        line(4, "You have killed a Freeport conscript."),
        line(8, "a Freeport militiaman hits YOU for 100 crushing damage."),
    ])
    encs = encounters_of(client, parse(client, text))
    assert len(encs) == 1
    assert encs[0]["duration_s"] == 3        # first swing to the kill
