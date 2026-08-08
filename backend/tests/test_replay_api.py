"""Replaying a recorded fight into the live meter.

Two gates, and the point of most of this file is that they are separate. The
ROLE gate keeps a developer tool out of an ordinary reader's dashboard; the
VISIBILITY gate is the ordinary one, and being an admin does not lift it —
"admin is operational, not omniscient" would mean nothing if replay were a
door into everybody's raids.

The rest pins what a replay IS: the same arithmetic as the recorded parse
(otherwise the meter you tune against is a fiction), paced by wall clock, and
writing nothing at all.
"""

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

CTIME = "Thu Aug  1 21:00:00 2026"
BASE_TS = 1754500000
NAMED = "The Corsolander"


def line(ts, body):
    return f"({ts})[{CTIME}] {body}\r\n"


def raid_log(base=BASE_TS):
    """One named pull: four raiders, a heal, a death, and an AoE wide enough to
    be a cast (five targets in one second).

    Assembled as (offset, line) and SORTED. An EQ2 log is written in the order
    things happen, and everything downstream — segmentation, the live tail, the
    replay's own window read — is built on that; a log stitched together out of
    order is not a smaller version of a real one, it is a different thing, and
    it segments into fights that never happened.
    """
    rows = [(0, "You have entered The Estate of Unrest.")]
    for i in range(0, 40, 4):
        t = 10 + i
        rows += [
            (t, f"YOUR Soulrot hits {NAMED} for 250 disease damage."),
            (t + 1, f"Aros hits {NAMED} for 100 crushing damage."),
            (t + 2, f"Tanky hits {NAMED} for 50 crushing damage."),
            (t + 3, "Healbot heals Tanky for 120 hit points."),
        ]
    # a raid-wide AoE: one ability, one second, five people. Three casts on a
    # 15s cycle, because ONE cast has nothing to count down to and the meter
    # deliberately drops it (livemeter._live_aoes)
    for at in (15, 30, 45):
        for who in ("Aros", "Tanky", "Healbot", "Bobby", "Cleric"):
            rows.append((at, f"{NAMED}'s War Stomp hits {who} for 300 crushing damage."))
    rows += [
        (44, f"{NAMED} hits Tanky for 9000 crushing damage."),
        (45, f"{NAMED} has killed Tanky."),
        (50, f"YOUR Soulrot hits {NAMED} for 250 disease damage."),
        (51, f"{NAMED} has been slain by Aros."),
    ]
    rows.sort(key=lambda r: r[0])
    return "".join(line(base + at, body) for at, body in rows)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-replay")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    import routers.replay_api as rp
    import routers.uploads_api as up
    # `uploads_api` binds UPLOADS_DIR at import, so in a full-suite run it keeps
    # whichever module imported `main` first. A replay READS the stored log
    # back, so the writer and the reader have to agree on where it went.
    mp.setattr(up, "UPLOADS_DIR", tmp / "uploads")
    # a replay is paced by the plugin's real cadence against the fight's own
    # clock; a test has no reason to sit through either
    mp.setattr(rp, "TICK_S", 0.01)
    mp.setattr(rp, "MAX_SPEED", 120.0)
    with TestClient(app) as c:
        # the first account bootstraps to admin, which implies curator
        c.post("/api/auth/register", json={"username": "boss", "password": "hunter2hunter2"})
        yield c
    mp.undo()


def login(client, who="boss"):
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"username": who, "password": "hunter2hunter2"})
    assert r.status_code == 200, r.text


@pytest.fixture(scope="module")
def fight(client):
    """The uploaded night, and the named encounter out of it."""
    login(client)
    r = client.post("/api/uploads",
                    files={"file": ("eq2log_Bobby.txt", raid_log().encode())},
                    data={"character_name": "Bobby"})
    assert r.status_code == 200, r.text
    session_id = r.json()["session_id"]
    for _ in range(100):
        if client.get(f"/api/sessions/{session_id}").json()["session"]["status"] == "ready":
            break
        time.sleep(0.05)
    encs = client.get(f"/api/sessions/{session_id}").json()["encounters"]
    named = [e for e in encs if e["is_named"]]
    assert named, encs
    # the pull itself, not a corpse-tick tail the segmenter split off it
    return {**max(named, key=lambda e: e["duration_s"]), "session_id": session_id}


def events_of(body):
    """The SSE frames, as (event name, data) pairs."""
    out = []
    for block in body.strip().split("\n\n"):
        m = re.match(r"event: (\w+)\ndata: (.*)", block, re.S)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def replay(client, encounter_id, speed=60):
    r = client.get(f"/api/replay/{encounter_id}/stream?speed={speed}")
    return r


# ----------------------------------------------------------------- the gates ---

def test_an_ordinary_reader_has_no_replay(client, fight):
    """The role gate. A reader who can open this very fight on the raid page
    still does not get the developer control."""
    client.post("/api/auth/register", json={"username": "reader", "password": "hunter2hunter2"})
    login(client, "reader")
    assert replay(client, fight["id"]).status_code == 403
    login(client)


def test_admin_cannot_replay_a_raid_it_cannot_read(client, fight):
    """The visibility gate, and the one that matters most: being an admin is
    operational, not omniscient. A fight belonging to somebody else answers
    exactly as a fight that never existed."""
    client.post("/api/auth/register",
                json={"username": "stranger", "password": "hunter2hunter2"})
    login(client, "stranger")
    r = client.post("/api/uploads",
                    files={"file": ("eq2log_Mine.txt", raid_log(BASE_TS + 9000).encode())},
                    data={"character_name": "Mine"})
    sid = r.json()["session_id"]
    for _ in range(100):
        if client.get(f"/api/sessions/{sid}").json()["session"]["status"] == "ready":
            break
        time.sleep(0.05)
    theirs = [e for e in client.get(f"/api/sessions/{sid}").json()["encounters"]
              if e["is_named"]][0]

    login(client)                      # admin, and a stranger to that upload
    assert client.get("/api/auth/me").json()["user"]["role"] == "admin"
    assert replay(client, theirs["id"]).status_code == 404


def test_an_encounter_that_never_existed_is_a_404(client):
    login(client)
    assert replay(client, 999999).status_code == 404


# ------------------------------------------------------------------ the play ---

def test_a_replay_plays_the_fight_and_stops(client, fight):
    login(client)
    r = replay(client, fight["id"])
    assert r.status_code == 200
    frames = events_of(r.text)
    kinds = [k for k, _ in frames]
    assert kinds[0] == "replay"                    # the head, before any picture
    assert kinds.count("partial") >= 2             # it MOVED rather than arriving whole
    head = json.loads(frames[0][1])
    assert head["name"] == NAMED and head["is_named"] is True
    assert head["span_s"] == fight["duration_s"]     # the recorded fight, whole

    last = json.loads(frames[-1][1])
    assert last["replay"]["done"] is True
    assert last["replay"]["elapsed_s"] == head["span_s"]
    # and the frames before it are not already finished — that is the difference
    # between playing a fight and posting the result of one
    assert json.loads(frames[1][1])["replay"]["done"] is False


def test_the_meter_agrees_with_the_recorded_parse(client, fight):
    """A replay is the live view over the same events, so the final frame has
    to land on what the parse recorded. A meter that measured differently would
    be a tuning surface for a fight that never happened."""
    login(client)
    frames = events_of(replay(client, fight["id"]).text)
    final = json.loads(frames[-1][1])["fight"]

    agg = client.get(f"/api/encounters/agg?ids={fight['id']}").json()
    recorded = {a["name"]: a["damage"] for a in agg["actors"] if a.get("damage")}
    played = {a["name"]: a["damage"] for a in final["actors"] if a["damage"]}
    for name, dmg in recorded.items():
        if name in played:                 # the meter credits by NAME, no resolver
            assert played[name] == dmg, name
    assert final["raid"]["deaths"] == 1
    assert any(a["deaths"] for a in final["actors"] if a["name"] == "Tanky")


def test_the_aoe_countdown_survives_the_round_trip(client, fight):
    """Five people in one second is a cast — the same anchor the recorded AoE
    tab uses. It is the reason a replay is worth watching."""
    login(client)
    frames = events_of(replay(client, fight["id"]).text)
    final = json.loads(frames[-1][1])["fight"]
    assert any(a["ability"] == "War Stomp" for a in final["aoes"]), final["aoes"]


def test_a_replay_writes_nothing(client, fight):
    """The whole design rests on this: no session, no encounter, no rows."""
    conn = dbmod.get_db()
    before = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("sessions", "encounters", "events", "encounter_actor_stats")]
    login(client)
    assert replay(client, fight["id"]).status_code == 200
    after = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("sessions", "encounters", "events", "encounter_actor_stats")]
    assert before == after


def test_a_replay_feeds_the_stream_overlay(client, fight):
    """The other reader. A replay publishes its frames so an OBS source can
    show them (`pipeline/replaybus.py`) — otherwise the overlay could only ever
    be positioned and tuned during a raid. What it publishes is the LIVE
    payload: the `replay` block names the fight and the night it came from, and
    an overlay token is not allowed to hold either."""
    from pipeline import replaybus
    login(client)
    me = client.get("/api/auth/me").json()["user"]["id"]
    replaybus.clear(me)
    assert replay(client, fight["id"]).status_code == 200
    frame = replaybus.latest(me)
    assert frame is not None
    assert "replay" not in frame
    assert frame["fight"]["provisional_name"]
    replaybus.clear(me)


def test_speed_is_clamped(client, fight):
    """A speed of a million would hand back the finished parse in one frame and
    call it a replay."""
    login(client)
    head = json.loads(events_of(replay(client, fight["id"], speed=999).text)[0][1])
    import routers.replay_api as rp
    assert head["speed"] == rp.MAX_SPEED


def test_a_night_without_its_raw_log_says_so(client, fight):
    """Stats alone cannot be replayed — the lines are the input. A dropped log
    is a 409 rather than an empty meter that looks like a bug."""
    login(client)
    conn = dbmod.get_db()
    sha = conn.execute("SELECT upload_sha256 FROM sessions WHERE id=?",
                       (fight["session_id"],)).fetchone()["upload_sha256"]
    path = dbmod.UPLOADS_DIR / f"{sha}.txt.gz"
    moved = path.with_suffix(".hidden")
    path.rename(moved)
    try:
        assert replay(client, fight["id"]).status_code == 409
    finally:
        moved.rename(path)
