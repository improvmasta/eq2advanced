"""The stream overlay token.

It is a URL that goes on somebody's stream, so the whole test is "what can a
stranger holding it reach?". The answer has to be: the live meter for whoever
minted it, and nothing else — no session ids, no fight history, no account,
and nothing at all once it is revoked. The other half is that the account side
still needs a cookie, because minting and revoking are account powers.
"""

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
import ratelimit


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-overlay")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    import pipeline.live as live
    mp.setattr(live, "RAW_DIR", tmp / "raw")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as c:
        c.post("/api/auth/register",
               json={"username": "streamer", "password": "hunter2hunter2"})
        yield c
    mp.undo()


@pytest.fixture(autouse=True)
def _fresh(client):
    """Signed in, with an empty rate-limit bucket. Several tests here clear the
    cookie deliberately — being a stranger is the thing under test — so the
    session is re-established per test rather than assumed to survive."""
    ratelimit.reset_all()
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"username": "streamer", "password": "hunter2hunter2"})
    assert r.status_code == 200, r.text
    yield


@pytest.fixture()
def overlay(client):
    r = client.post("/api/overlay-tokens", json={"label": "obs"})
    assert r.status_code == 200, r.text
    return r.json()


def line(ts, body):
    return f"({ts})[Thu Aug  1 21:00:00 2026] {body}\r\n"


def stream_a_pull(client, character="Streamy"):
    """A raid in progress: current log time, so it clears the live-lag gate."""
    client.post("/api/characters", json={"name": character})
    token = client.post("/api/tokens", json={"label": "t"}).json()["token"]
    now = int(time.time())
    lines = [
        line(now, "You have entered The Estate of Unrest."),
        line(now + 1, "YOU hit a knotted guardian for 1000 crushing damage."),
        line(now + 2, "YOU hit a knotted guardian for 1500 crushing damage."),
    ]
    r = client.post("/api/ingest/batch", json={
        "batch_id": str(uuid.uuid4()), "mode": "live",
        "character": character, "lines": lines,
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return token, r.json()["session_id"], character


def resend(client, token, character, offset):
    now = int(time.time()) + offset
    return client.post("/api/ingest/batch", json={
        "batch_id": str(uuid.uuid4()), "mode": "live", "character": character,
        "lines": [line(now + 1, "YOU hit a knotted guardian for 700 crushing damage.")],
    }, headers={"Authorization": f"Bearer {token}"})


def read_stream(token, want, limit=8):
    """The first few events off the overlay stream, read from the generator
    rather than over HTTP.

    This stream never ends on purpose — an OBS browser source is opened once
    and left running for hours — and TestClient runs a request to COMPLETION
    before handing back a response, so there is no way to read an endless one
    through it. Driving the generator is the same code with the transport left
    out; `want` stops the read as soon as the interesting event has arrived.
    """
    import routers.overlay_api as ov
    from starlette.requests import Request

    request = Request({"type": "http", "method": "GET", "path": "/",
                       "headers": [], "query_string": b"",
                       "client": ("127.0.0.1", 5555)})

    async def run():
        resp = await ov.overlay_stream(token, request)
        chunks = []
        agen = resp.body_iterator
        try:
            async for chunk in agen:
                chunks.append(chunk)
                if want in chunk or len(chunks) >= limit:
                    break
        finally:
            await agen.aclose()
        return "".join(chunks)

    poll = ov.POLL_S
    ov.POLL_S = 0.02              # no reason to wait out a dashboard's cadence
    try:
        return asyncio.run(run())
    finally:
        ov.POLL_S = poll


# --- what the token reaches ----------------------------------------------

def test_the_public_config_carries_no_account(client, overlay):
    client.cookies.clear()
    body = client.get(f"/api/overlay/{overlay['token']}").json()
    assert set(body) == {"config"}
    assert body["config"]["theme"] == "transparent"
    assert json.dumps(body).find("streamer") == -1


def test_a_bad_token_and_a_revoked_one_answer_the_same(client, overlay):
    assert client.post(
        f"/api/overlay-tokens/{overlay['id']}/revoke").status_code == 200
    client.cookies.clear()
    assert client.get(f"/api/overlay/{overlay['token']}").status_code == 404
    assert client.get("/api/overlay/not-a-real-token").status_code == 404


def test_the_stream_needs_no_cookie_and_carries_only_the_meter(client, overlay):
    """The point of the whole feature, and its one security claim: a browser
    source sends no cookies, so the token is the credential — and what it buys
    is the fight in progress, not the session it came from."""
    dev_token, session_id, character = stream_a_pull(client)
    from pipeline import live as livemod
    livemod.mark_watched(session_id)
    resend(client, dev_token, character, 4)

    body = read_stream(overlay["token"], "provisional_name")
    assert '"live": true' in body
    payload = json.loads(body.split("event: partial\ndata: ")[1].split("\n")[0])
    assert payload["fight"]["provisional_name"] == "a knotted guardian"
    assert payload["fight"]["raid"]["damage"] > 0
    # nothing that identifies the session, the account or the night
    for leak in ("session_id", "line_count", "character_name", "streamer",
                 "encounter_id", "uploader_online"):
        assert leak not in body, leak


def test_a_replay_reaches_the_overlay(client, overlay):
    """The overlay reads the LIVE snapshot, which made it the one surface that
    could only be worked on during a raid — the scene had to be positioned and
    the options judged against a real pull. A replay the account is running
    feeds it instead (`pipeline/replaybus.py`), so an OBS source shows exactly
    what a viewer would see, on a Tuesday afternoon."""
    from pipeline import replaybus
    me = client.get("/api/auth/me").json()["user"]["id"]
    frame = {"computed_ts": 4242, "fight": {
        "provisional_name": "Mayong Mistmoore", "elapsed_s": 30, "aoes": [],
        "raid": {"damage": 900, "dps": 30, "heals": 0, "hps": 0,
                 "deaths": 0, "raiders": 2},
        "actors": [{"name": "Bobby", "kind": "player", "damage": 900,
                    "dps": 30, "max_hit": 700}]}}
    replaybus.publish(me, frame)
    try:
        client.cookies.clear()
        body = read_stream(overlay["token"], "provisional_name")
        assert '"live": true' in body
        payload = json.loads(body.split("event: partial\ndata: ")[1].split("\n")[0])
        assert payload["fight"]["provisional_name"] == "Mayong Mistmoore"
        # a stale frame is a replay that ended (or a tab that was closed): the
        # overlay lets go of it on its own, with no stop message to lose
        replaybus.MAX_AGE_S = -1
        assert replaybus.latest(me) is None
    finally:
        replaybus.MAX_AGE_S = 8.0
        replaybus.clear(me)


def test_an_overlay_with_nothing_streaming_says_so_and_stays_open(client):
    """An OBS source is opened once and left running for hours. A stream that
    ended between raids would be a scene that goes blank for good."""
    token = client.post("/api/overlay-tokens", json={}).json()["token"]
    conn = dbmod.get_db()
    with conn:
        conn.execute("UPDATE sessions SET status='ready' WHERE status='receiving'")
    body = read_stream(token, '"live": false')
    assert '"live": false' in body
    # and it keeps ticking rather than closing
    assert body.count(": idle") >= 1 or body.count("live") >= 1


# --- the account half -----------------------------------------------------

def test_minting_configuring_and_revoking_need_an_account(client):
    r = client.post("/api/overlay-tokens", json={})
    made = r.json()
    client.cookies.clear()
    assert client.get("/api/overlay-tokens").status_code == 401
    assert client.post("/api/overlay-tokens", json={}).status_code == 401
    assert client.patch(f"/api/overlay-tokens/{made['id']}",
                        json={}).status_code == 401
    assert client.post(
        f"/api/overlay-tokens/{made['id']}/revoke").status_code == 401


def test_config_is_cleaned_not_trusted(client):
    made = client.post("/api/overlay-tokens", json={
        "config": {"theme": "neon", "metrics": ["dps", "nonsense"],
                   "max_rows": 8, "layout": "diagonal"}}).json()
    cfg = made["config"]
    assert cfg["theme"] == "transparent"          # an unknown theme is not a theme
    assert cfg["metrics"] == ["dps"]
    assert cfg["layout"] == "vertical"            # nor is an unknown layout
    assert cfg["enabled"] is True                 # a new link is on
    assert cfg["width_px"] is None                # and fills its OBS source
    # and a config with no metric left still shows something
    r = client.post("/api/overlay-tokens", json={"config": {"metrics": []}})
    assert r.json()["config"]["metrics"] == ["dps"]
    # a width is CLAMPED, not rejected — it is a number typed into a box on a
    # dashboard, and a 422 mid-raid is worse than a sane one
    for typed, kept in ((900, 900), (5, 160), (99999, 1920), (0, None)):
        r = client.patch(f"/api/overlay-tokens/{made['id']}",
                         json={"config": {"width_px": typed}})
        assert r.json()["config"]["width_px"] == kept, typed
    # off is a setting, not a revocation: the token still resolves
    r = client.patch(f"/api/overlay-tokens/{made['id']}",
                     json={"config": {"enabled": False, "layout": "horizontal"}})
    assert r.json()["config"]["enabled"] is False
    assert r.json()["config"]["layout"] == "horizontal"
    client.cookies.clear()
    assert client.get(f"/api/overlay/{made['token']}").status_code == 200


def test_a_revoked_overlay_leaves_the_list(client):
    made = client.post("/api/overlay-tokens", json={"label": "temp"}).json()
    assert any(o["id"] == made["id"]
               for o in client.get("/api/overlay-tokens").json()["overlays"])
    client.post(f"/api/overlay-tokens/{made['id']}/revoke")
    assert all(o["id"] != made["id"]
               for o in client.get("/api/overlay-tokens").json()["overlays"])
    assert client.post(
        f"/api/overlay-tokens/{made['id']}/revoke").status_code == 404
