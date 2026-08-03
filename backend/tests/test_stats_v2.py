"""Phase 2 stats engine: avoid breakdown, melee split, median/avg-delay,
damage-type rollup, cast attachment, crits on non-damage kinds, mob + Unknown
actor rows, damage_taken/power_drain/cure_count, and the agg endpoint."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from parser.events import F_AUTOATTACK, F_CRIT, F_MULTI, F_ZERO
from pipeline.statsroll import roll_encounter


def rev(ts, type_, *, src=1, src_roll=1, src_kind="player", tgt=99, tgt_roll=None,
        tgt_kind="mob", ability=None, amount=None, dtype=None, flags=0, extra=None):
    return {"ts": ts, "seq": ts, "type": type_, "src_entity": src,
            "src_rollup": src_roll, "src_kind": src_kind, "tgt_entity": tgt,
            "tgt_rollup": tgt_roll, "tgt_kind": tgt_kind, "ability": ability,
            "amount": amount, "dtype": dtype, "flags": flags, "extra": extra}


def test_avoid_kinds_route_to_columns():
    events = [rev(0, "damage", ability="Stab", amount=100, dtype="piercing")]
    for i, how in enumerate(("miss", "parry", "riposte", "dodge", "block",
                             "reflect", "resist")):
        events.append(rev(i + 1, "avoid", ability="Stab", extra={"how": how}))
    _, abil = roll_encounter(events, 10)
    st = abil[(1, "Stab", "damage")]
    assert (st["misses"], st["parries"], st["ripostes"], st["dodges"],
            st["blocks"], st["reflects"], st["resists"]) == (1, 1, 1, 1, 1, 1, 1)
    assert st["hits"] == 1


def test_melee_split_and_zero_hits():
    events = [
        rev(0, "damage", amount=100, dtype="crushing", flags=F_AUTOATTACK),
        rev(1, "damage", amount=50, dtype="crushing", flags=F_AUTOATTACK | F_MULTI),
        rev(2, "damage", amount=0, flags=F_AUTOATTACK | F_ZERO),
    ]
    _, abil = roll_encounter(events, 10)
    melee = abil[(1, "(melee)", "damage")]
    assert melee["hits"] == 2 and melee["zero_hits"] == 1
    assert melee["min"] == 100 and melee["max"] == 100      # zero excluded
    assert abil[(1, "(multi attack)", "damage")]["total"] == 50


def test_median_avg_delay_and_dtypes():
    events = [
        rev(0, "damage", ability="Bolt", amount=100, dtype="magic"),
        rev(4, "damage", ability="Bolt", amount=300, dtype="magic"),
        rev(8, "damage", ability="Bolt", amount=1000, dtype="magic",
            extra={"components": [[700, "magic"], [300, "disease"]]}),
    ]
    _, abil = roll_encounter(events, 10)
    st = abil[(1, "Bolt", "damage")]
    assert st["median"] == 300
    assert st["avg_delay_s"] == 4.0
    assert json.loads(st["dtypes"]) == {"magic": 1100, "disease": 300}


def test_casts_attach_to_busiest_row_not_phantom_damage():
    events = [
        rev(0, "cast_flavor", ability="Mending"),
        rev(1, "heal", ability="Mending", amount=500, tgt=2, tgt_roll=2,
            tgt_kind="player"),
    ]
    _, abil = roll_encounter(events, 10)
    assert abil[(1, "Mending", "heal")]["casts"] == 1
    assert (1, "Mending", "damage") not in abil


def test_ward_crit_counted():
    events = [rev(0, "ward", ability="Aegis", amount=400, tgt=2, tgt_roll=2,
                  tgt_kind="player", flags=F_CRIT)]
    _, abil = roll_encounter(events, 10)
    assert abil[(1, "Aegis", "ward")]["crits"] == 1


def test_threat_detaunt_split():
    events = [
        rev(0, "threat", ability="Taunt", amount=1000),
        rev(1, "threat", ability="Evade", amount=-800),
    ]
    _, abil = roll_encounter(events, 10)
    assert abil[(1, "Taunt", "threat")]["total"] == 1000
    assert abil[(1, "Evade", "detaunt")]["total"] == 800


def test_mob_actor_rows_and_damage_taken():
    events = [
        # mob (no rollup) hits the player; player hits back
        rev(0, "damage", src=99, src_roll=None, src_kind="mob", tgt=1, tgt_roll=1,
            tgt_kind="player", amount=700, dtype="slashing", flags=F_AUTOATTACK),
        rev(1, "damage", src=1, src_roll=1, src_kind="player", tgt=99, tgt_roll=None,
            tgt_kind="mob", ability="Stab", amount=400, dtype="piercing"),
    ]
    actors, _ = roll_encounter(events, 10)
    assert actors[99]["damage"] == 700 and actors[99]["damage_taken"] == 400
    assert actors[1]["damage"] == 400 and actors[1]["damage_taken"] == 700


def test_power_drain_and_cures():
    events = [
        rev(0, "power_drain", ability="Manatap", amount=300),
        rev(1, "dispel", ability="Cure", tgt=2, tgt_roll=2, tgt_kind="player"),
        rev(2, "dispel", ability="Dispel Magic", tgt=99, tgt_roll=None, tgt_kind="mob"),
    ]
    actors, _ = roll_encounter(events, 10)
    assert actors[1]["power_drain"] == 300
    # ACT counts every relieve/dispel line as a cure, any target kind —
    # verified against the Emerald Halls Cures column (Stymie 4 = 2 relieves
    # + 2 mob-buff strips)
    assert actors[1]["cure_count"] == 2


# ---- API level: Unknown pooling + agg endpoint ----

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-statsv2")
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
                   json={"email": "statsv2@test.local", "password": "hunter2hunter2"})
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


TWO_FIGHTS = [
    line(T0, "You have entered The Estate of Unrest."),
    line(T0 + 1, "YOU hit a training dummy for 100 crushing damage."),
    line(T0 + 3, "YOU hit a training dummy for a critical of 300 crushing damage."),
    line(T0 + 4, "Xalithra is hit for 250 heat damage."),
    line(T0 + 5, "You have killed a training dummy."),
    # 30s gap -> second fight
    line(T0 + 40, "YOU hit a sparring golem for 200 heat damage."),
    line(T0 + 42, "YOU try to crush a sparring golem, but a sparring golem parries."),
    line(T0 + 44, "a sparring golem hits YOU for 500 slashing damage."),
    line(T0 + 46, "You have killed a sparring golem."),
]


def test_unknown_pool_and_swings_via_api(client):
    sid = upload(client, "s1.txt", TWO_FIGHTS)
    encs = client.get(f"/api/sessions/{sid}").json()["encounters"]
    assert len(encs) == 2
    d1 = client.get(f"/api/encounters/{encs[0]['id']}").json()
    names = {a["name"]: a for a in d1["actors"]}
    assert "Unknown" in names and names["Unknown"]["damage"] == 250
    assert "a training dummy" in names       # mobs get actor rows now
    d2 = client.get(f"/api/encounters/{encs[1]['id']}").json()
    melee = next(a for a in d2["abilities"]
                 if a["ability"] == "(melee)" and a["source_kind"] == "player")
    assert melee["hits"] == 1 and melee["parries"] == 1
    assert melee["swings"] == 2 and melee["to_hit_pct"] == 50.0
    golem = next(a for a in d2["actors"] if a["name"] == "a sparring golem")
    assert golem["damage"] == 500 and golem["damage_taken"] == 200
    bobby = next(a for a in d2["actors"] if a["name"] == "Bobby")
    assert bobby["damage_taken"] == 500


def test_agg_sums_and_validates(client):
    sid = upload(client, "s2.txt", TWO_FIGHTS)
    encs = client.get(f"/api/sessions/{sid}").json()["encounters"]
    ids = f"{encs[0]['id']},{encs[1]['id']}"
    agg = client.get(f"/api/encounters/agg?ids={ids}").json()
    assert agg["encounter_ids"] == sorted([encs[0]["id"], encs[1]["id"]])
    bobby = next(a for a in agg["actors"] if a["name"] == "Bobby")
    assert bobby["damage"] == 600                        # 400 + 200
    dur = agg["encounter"]["duration_s"]
    assert dur == max(encs[0]["duration_s"], 1) + max(encs[1]["duration_s"], 1)
    assert bobby["dps"] == round(600 / dur, 1)
    melee = next(a for a in agg["abilities"]
                 if a["ability"] == "(melee)" and a["source_kind"] == "player")
    assert melee["hits"] == 3 and melee["parries"] == 1 and melee["swings"] == 4
    assert melee["median"] == 200                        # 100, 300, 200
    assert melee["dtypes"] == {"crushing": 400, "heat": 200}

    # single id passthrough matches encounter detail
    single = client.get(f"/api/encounters/agg?ids={encs[0]['id']}").json()
    detail = client.get(f"/api/encounters/{encs[0]['id']}").json()
    assert single["actors"] == detail["actors"]

    # ids across two sessions merge by name (zone runs are cross-file)
    sid2 = upload(client, "s3.txt", [
        line(T0 + 900, "You have entered The Estate of Unrest."),
        line(T0 + 901, "YOU hit a training dummy for 10 crushing damage."),
        line(T0 + 903, "You have killed a training dummy."),
    ])
    other = client.get(f"/api/sessions/{sid2}").json()["encounters"][0]["id"]
    x = client.get(f"/api/encounters/agg?ids={encs[0]['id']},{other}").json()
    assert x["session_ids"] == sorted([sid, sid2])
    xbobby = next(a for a in x["actors"] if a["name"] == "Bobby")
    assert xbobby["damage"] == 410 and len(xbobby["entity_ids"]) == 2
    assert client.get("/api/encounters/agg?ids=abc").status_code == 422
    assert client.get("/api/encounters/agg?ids=999999").status_code == 404
