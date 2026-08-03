"""Engage = the gap between the pull and a raider's FIRST ACTION.

Before this, only hostile actions anchored, so a templar who warded and healed
from the first second of a pull was reported as never having engaged, and a
wizard whose opening swing missed was dated to the next spell that landed. On
Sawtooth the Ancient (Emerald Halls, 2026-08-02) that read as Tragedy engaging
at +13s when the log shows a heal at +2s. These tests pin the anchor kinds."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

BASE_TS = 1754400000
CTIME = "Sun Aug 02 22:00:00 2026"
NAMED = "Sawtooth the Ancient"


def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def log() -> str:
    """One named pull, t=10 to t=30. Each raider's first action is a different
    KIND of action. Damage lands at most 6s apart throughout — the segmenter
    cuts on 7s of combat silence, and heals do NOT hold a fight open."""
    hit = f"YOUR Soulrot hits {NAMED} for 900 disease damage."
    out = [line(0, "You have entered The Emerald Halls.")]
    for t in (10, 12, 18, 24, 28):
        out.append(line(t, hit))
    # a healer whose only action all fight is healing — engaged at +4s
    out.append(line(14, "Tragedy's Supplicant's Prayer heals Gabriel for 1,213 hit points."))
    # a warder: the absorb line prints when the mob swings, so it never
    # anchors; their cure at +12s does
    out.append(line(15, "Kthxbye's Shield of Faith absorbs 1,279 points of damage "
                        "from being done to Gabriel. (2935 points remaining)"))
    out.append(line(22, "Kthxbye's Cure relieves Slashing Talon from Gabriel."))
    # a melee whose opening swing MISSES at +6s and only lands at +9s
    out.append(line(16, f"Ahaz tries to pierce {NAMED}, but misses."))
    out.append(line(19, f"Ahaz hits {NAMED} for 1,401 heat damage."))
    out.append(line(30, f"You have killed {NAMED}."))
    out.sort(key=lambda l: int(l[1:11]))
    return "".join(out)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-engage")
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
               json={"email": "engage@x.test", "password": "hunter2hunter2"})
        c.post("/api/characters", json={"name": "Bobby"})
        yield c
    mp.undo()


@pytest.fixture(scope="module")
def players(client):
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
    rep = client.get(f"/api/sessions/{sid}/raid-report").json()
    enc = next(e for e in rep["encounters"] if e["encounter"]["name"] == NAMED)
    return {p["name"]: p for p in enc["players"]}, rep


def test_a_heal_is_an_action(players):
    p, _ = players
    assert p["Tragedy"]["engage_delay_s"] == 4
    assert p["Tragedy"]["engage_anchor"] == "heal"
    assert p["Tragedy"]["engage_confidence"] == "high"


def test_a_ward_absorb_is_not_an_action_but_a_cure_is(players):
    """The absorb prints when the MOB swings — dating engagement from it would
    credit the warder for the pre-pull cast."""
    p, _ = players
    assert p["Kthxbye"]["engage_delay_s"] == 12
    assert p["Kthxbye"]["engage_anchor"] == "cure"


def test_a_missed_swing_still_counts(players):
    """It is the swing that is deliberate, not the roll."""
    p, _ = players
    assert p["Ahaz"]["engage_delay_s"] == 6
    assert p["Ahaz"]["engage_anchor"] == "autoattack"


def test_night_rollup_carries_the_anchor_mix(players):
    """A 6s average made of heals reads differently from one made of swings, so
    the rollup keeps the counts."""
    _, rep = players
    night = {n["name"]: n for n in rep["night"]}
    assert night["Tragedy"]["engage_anchors"] == {"heal": 1}
    assert night["Ahaz"]["engage_anchors"] == {"autoattack": 1}
