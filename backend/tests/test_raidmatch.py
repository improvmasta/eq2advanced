"""One raid, several uploaders.

Everyone in a raid runs their own ACT, so a night shared into a group arrives
as several parses of the same evening. The rules under test:

  * two people's runs of the same night are recognised as ONE raid — same zone,
    overlapping windows, the same people in them
  * two different nights are not, and neither are two guilds in the same zone
    at the same hour
  * the site picks one parse for everyone (`primary`) — the one that covers the
    most of the night — and the pick does not depend on who is looking
  * a raid page names the other uploaders' parses of the same night, and only
    the ones the viewer could already open
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
import raidmatch

# the raid, minus the two people whose logs these are — each of them is YOU in
# their own file and a name in the other's roster
RAIDERS = ["Tragedy", "Ramms", "Squigs", "Sorzi", "Shaly", "Zooey"]


CTIME = "Fri Aug 01 21:00:00 2026"


def line(ts, body):
    return f"({ts})[{CTIME}] {body}\r\n"


def fight(ts, mob="a knotted guardian", hits=3):
    """One pull: the logger and the rest of the raid on one mob, then a kill."""
    out = []
    for i in range(hits):
        out.append(line(ts + i, f"YOU hit {mob} for {200 + i} crushing damage."))
        for j, name in enumerate(RAIDERS):
            out.append(line(ts + i, f"{name} hits {mob} for {300 + j} slashing damage."))
    out.append(line(ts + hits, f"You have killed {mob}."))
    return "".join(out)


def night(start, zone, fights):
    """A zone line and `fights` pulls, twenty minutes apart — well under the
    hour that would segment them into separate runs."""
    out = [line(start - 1, f"You have entered {zone}.")]
    for i in range(fights):
        out.append(fight(start + i * 1200))
    return "".join(out)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-raidmatch")
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


def sign_in(c, username, fresh=False):
    c.cookies.clear()
    body = {"username": username, "password": "hunter2hunter2"}
    if fresh:
        body |= {"sq_id": 1, "answer": "pet"}
    r = c.post(f"/api/auth/{'register' if fresh else 'login'}", json=body)
    assert r.status_code == 200, r.text
    return r.json()["user"]


def upload(c, name, content):
    r = c.post("/api/uploads", files={"file": ("log.txt", content.encode())},
               data={"character_name": name})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    for _ in range(80):
        s = c.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] in ("ready", "error"):
            assert s["status"] == "ready", s["error"]
            return sid
        time.sleep(0.1)
    raise AssertionError("parse never finished")


# ---------- the rules, on their own ----------

def run(rid, zone="Emerald Halls", start=1000, end=9000, roster=RAIDERS,
        fights=10, combat=600, raiders=None):
    import json
    return {"id": rid, "zone": zone, "started_ts": start, "ended_ts": end,
            "roster_json": json.dumps(list(roster)) if roster is not None else None,
            "encounter_count": fights, "combat_s": combat,
            "raider_count": raiders if raiders is not None else len(roster or [])}


def test_same_night_from_two_seats_is_one_raid():
    a = run(1, roster=["Bobby", *RAIDERS])
    # the other logger sees themselves instead of Bobby, and their clock is 40s
    # out; every other name in the raid is the same
    b = run(2, start=1040, end=9040, roster=RAIDERS)
    assert raidmatch.same_raid(a, b)


def test_a_different_zone_a_different_hour_and_a_different_guild_are_not():
    a = run(1)
    assert not raidmatch.same_raid(a, run(2, zone="Veeshan's Peak"))
    # ended before the other started, by more than the clock slack
    assert not raidmatch.same_raid(a, run(2, start=20_000, end=30_000))
    # same instance zone, same hour, nobody in common: another guild's night
    assert not raidmatch.same_raid(a, run(2, roster=["Aaa", "Bbb", "Ccc", "Ddd"]))


def test_an_unknown_zone_needs_the_roster_to_speak_for_it():
    """A run whose log began mid-zone can still be matched — but only by who was
    in it, because the place is exactly what it cannot state."""
    a = run(1, zone=None)
    assert raidmatch.same_raid(a, run(2, zone=None))
    assert not raidmatch.same_raid(a, run(2, zone=None, roster=None))
    # a named zone plus an overlapping window stands on its own: a run written
    # before schema v18 has no roster until the next relink sweep
    assert raidmatch.same_raid(run(1, roster=None), run(2, roster=None))


def test_the_site_picks_the_parse_that_covers_the_most_of_the_night():
    """Someone who zoned in for the last two pulls has a real parse of two
    pulls. A stranger should land on the one that holds the evening."""
    partial = run(1, fights=2, combat=90, start=8000)
    whole = run(2, fights=10, combat=600)
    also_whole = run(3, fights=10, combat=600)
    runs = [partial, whole, also_whole]
    raidmatch.annotate(runs)
    assert {r["raid_key"] for r in runs} == {1}
    assert [r["parses"] for r in runs] == [3, 3, 3]
    assert [r["id"] for r in runs if r["primary"]] == [2]   # ties -> first upload


def test_a_run_that_matches_nothing_is_its_own_raid():
    runs = [run(4), run(9, zone="Veeshan's Peak", start=90_000, end=99_000)]
    raidmatch.annotate(runs)
    assert [r["raid_key"] for r in runs] == [4, 9]
    assert all(r["primary"] and r["parses"] == 1 for r in runs)


# ---------- end to end ----------

@pytest.fixture(scope="module")
def world(client):
    """Two accounts upload the same raid; a third is in the group both share it
    with and has no parse of their own."""
    zone, start = "Emerald Halls", 1_722_556_800
    sign_in(client, "bobby", fresh=True)
    upload(client, "Bobby", night(start, zone, 3))
    bobby_run = client.get("/api/zone-runs?scope=mine").json()["zone_runs"][0]
    group = client.post("/api/groups", json={"name": "Tuesday Raid"}).json()["group"]
    code = client.get(f"/api/groups/{group['id']}").json()["group"]["join_code"]

    # the same night from another seat: the same fights plus one the first log
    # missed, so its parse covers more of the evening
    sign_in(client, "zyl", fresh=True)
    client.post("/api/groups/join", json={"code": code})
    upload(client, "Zylphax", night(start + 40, zone, 4))
    zyl_run = client.get("/api/zone-runs?scope=mine").json()["zone_runs"][0]
    client.put(f"/api/zone-runs/{zyl_run['id']}/shares", json={"group_ids": [group["id"]]})

    sign_in(client, "bobby")
    client.put(f"/api/zone-runs/{bobby_run['id']}/shares", json={"group_ids": [group["id"]]})

    sign_in(client, "mate", fresh=True)
    client.post("/api/groups/join", json={"code": code})
    return {"group": group, "bobby": bobby_run["id"], "zyl": zyl_run["id"]}


def test_the_two_uploads_are_one_raid_with_one_default(client, world):
    """A group member with no parse of their own sees ONE night, not two, and
    lands on the parse that holds more of it."""
    sign_in(client, "mate")
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    assert {r["id"] for r in runs} == {world["bobby"], world["zyl"]}
    # matched on the roster, not on the zone-and-clock fallback: both parses
    # counted the same seven people (the logger plus six)
    assert [r["raider_count"] for r in runs] == [7, 7]
    assert all("roster_json" not in r for r in runs)
    assert len({r["raid_key"] for r in runs}) == 1
    assert all(r["parses"] == 2 for r in runs)
    assert [r["id"] for r in runs if r["primary"]] == [world["zyl"]]

    # ...and the same pick for the other uploader, whose own parse is the
    # shorter one: precedence is the browser's to apply, not the payload's
    sign_in(client, "bobby")
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    assert [r["id"] for r in runs if r["primary"]] == [world["zyl"]]
    assert [r["id"] for r in runs if r["mine"]] == [world["bobby"]]


def test_the_raid_page_offers_the_other_uploaders_parse(client, world):
    sign_in(client, "mate")
    detail = client.get(f"/api/zone-runs/{world['zyl']}").json()["zone_run"]
    alts = detail["alternates"]
    assert [a["id"] for a in alts] == [world["bobby"]]
    assert alts[0]["character_name"] == "Bobby" and alts[0]["owner_username"] == "bobby"
    assert alts[0]["mine"] is False
    # your own parse of the night sorts to the front of the switch
    sign_in(client, "bobby")
    alts = client.get(f"/api/zone-runs/{world['zyl']}").json()["zone_run"]["alternates"]
    assert [(a["id"], a["mine"]) for a in alts] == [(world["bobby"], True)]


def test_a_parse_you_cannot_see_is_never_offered(client, world):
    """The switch re-sorts raids the viewer was already allowed to open. It is
    not a directory of who else parsed the night."""
    sign_in(client, "stranger", fresh=True)
    assert client.get(f"/api/zone-runs/{world['zyl']}").status_code == 404

    # bobby unshares his: the group keeps the night, with one parse of it
    sign_in(client, "bobby")
    client.put(f"/api/zone-runs/{world['bobby']}/shares", json={"group_ids": []})
    sign_in(client, "mate")
    detail = client.get(f"/api/zone-runs/{world['zyl']}").json()["zone_run"]
    assert detail["alternates"] == []
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    assert [r["parses"] for r in runs] == [1]
