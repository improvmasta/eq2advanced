"""The doorbell, and what the site knows about which plugin you run.

Two features that arrived together because they are the same complaint — the
live meter lagging behind ACT's own. One is the push that removed the poll
between a batch landing and the screen seeing it; the other is how the raider
running the OLD uploader ever finds out there is a faster one.

The bus is tested for the property that is easy to get wrong and impossible to
notice in a raid: an update published WHILE a stream is reading must not be
lost. Polling could not lose it (the next tick asked again); a push can, and
then the meter silently sits on a stale fight until something else happens.
"""

import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from pipeline import livebus


# ---- the bus itself: no app, no database, just the edges ----

def test_a_publish_during_the_read_is_kept_not_lost():
    """The whole reason `subscribe` wraps the read instead of the wait.

    A subscriber that registered only around its sleep would miss a snapshot
    published while it was busy, and wait the full fallback for the NEXT one —
    which is exactly the moment it matters, because "busy" means a raid."""
    async def run():
        with livebus.subscribe(7) as bell:
            # published while this "reads" — before it ever waits
            livebus.publish(7)
            await asyncio.sleep(0)      # let call_soon_threadsafe land
            assert await bell.wait(0.5) is True
    asyncio.run(run())


def test_the_bell_is_cleared_so_one_publish_wakes_one_pass():
    async def run():
        with livebus.subscribe(8) as bell:
            livebus.publish(8)
            await asyncio.sleep(0)
            assert await bell.wait(0.5) is True
            # nothing new since: this one has to time out rather than spin
            started = time.monotonic()
            assert await bell.wait(0.15) is False
            assert time.monotonic() - started >= 0.1
    asyncio.run(run())


def test_a_publish_from_another_thread_wakes_the_loop():
    """Where this actually happens: ingest is a sync handler on a worker
    thread and the streams live in the event loop."""
    import threading

    async def run():
        with livebus.subscribe(9) as bell:
            threading.Timer(0.05, livebus.publish, args=(9,)).start()
            started = time.monotonic()
            assert await bell.wait(2.0) is True
            assert time.monotonic() - started < 1.5   # woken, not timed out
    asyncio.run(run())


def test_nobody_is_woken_for_a_session_they_are_not_watching():
    async def run():
        with livebus.subscribe(10) as bell:
            livebus.publish(11)
            await asyncio.sleep(0)
            assert await bell.wait(0.1) is False
    asyncio.run(run())


def test_a_subscription_to_nothing_is_a_sleeper():
    """The overlay re-resolves its session every pass and often there isn't
    one; those passes still have to wait, without forking the loop."""
    async def run():
        with livebus.subscribe(None) as bell:
            assert await bell.wait(0.05) is False
    asyncio.run(run())


def test_leaving_unregisters_so_a_closed_stream_costs_nothing():
    async def run():
        with livebus.subscribe(12):
            assert livebus.waiting(12) == 1
        assert livebus.waiting(12) == 0
    asyncio.run(run())


def test_publishing_with_nobody_watching_is_a_no_op():
    livebus.publish(13)          # must not raise
    assert livebus.waiting(13) == 0


# ---- which plugin an account runs, and who gets offered an update ----

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-livebus")
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
               json={"username": "raider", "password": "hunter2hunter2"})
        yield c
    mp.undo()


def upload_as(client, token, agent, character="Beltron"):
    """One live batch, sent the way a plugin build would send it."""
    now = int(time.time())
    headers = {"Authorization": f"Bearer {token}"}
    if agent is not None:
        headers["User-Agent"] = agent
    return client.post("/api/ingest/batch", json={
        "batch_id": str(uuid.uuid4()), "mode": "live", "character": character,
        "lines": [f"({now})[Thu Aug  1 21:00:00 2026] "
                  f"YOU hit a knotted guardian for 1000 crushing damage.\r\n"],
    }, headers=headers)


def test_version_ordering_is_numeric_not_alphabetical():
    """`"0.10.0" < "0.9.0"` is true of strings and false of software, and
    getting it backwards hides the offer from the people a tenth release was
    for."""
    from routers.plugin_api import version_tuple

    assert version_tuple("0.10.0") > version_tuple("0.9.0")
    assert version_tuple("0.2.0") > version_tuple("0.1.9")
    assert version_tuple("bogus") is None
    assert version_tuple("") is None
    assert version_tuple(None) is None


def test_only_the_plugins_own_user_agent_counts_as_a_version():
    """This decides whether somebody is told to reinstall something. A curl, a
    browser or an invented header has to read as "no idea"."""
    from auth import client_version

    assert client_version("eq2advanced-act/0.2.0") == "0.2.0"
    assert client_version("  eq2advanced-act/0.1.0  ") == "0.1.0"
    assert client_version("Mozilla/5.0") is None
    assert client_version("curl/8.4.0") is None
    assert client_version("eq2advanced-act/") is None
    assert client_version("eq2advanced-act/0.2.0 (spoofed)") is None
    assert client_version(None) is None


def test_an_account_that_never_paired_is_never_offered_an_update(client):
    """The pill is for people who HAVE the plugin. Somebody looking at the
    install steps does not need to hear that a thing they do not own is out of
    date."""
    r = client.get("/api/plugin")
    assert r.status_code == 200, r.text
    assert r.json()["update_available"] is False
    assert r.json()["your_version"] is None


def test_an_old_uploader_is_offered_the_new_build(client, monkeypatch):
    import routers.plugin_api as pa
    monkeypatch.setattr(pa, "_version", lambda: "0.2.0")
    pa._meta_cache = None

    client.post("/api/characters", json={"name": "Beltron"})
    token = client.post("/api/tokens", json={"label": "t"}).json()["token"]
    assert upload_as(client, token, "eq2advanced-act/0.1.0").status_code == 200

    body = client.get("/api/plugin").json()
    assert body["your_version"] == "0.1.0"
    assert body["update_available"] is True
    pa._meta_cache = None


def test_the_current_build_is_not_offered_to_itself(client, monkeypatch):
    import routers.plugin_api as pa
    monkeypatch.setattr(pa, "_version", lambda: "0.2.0")
    pa._meta_cache = None

    client.post("/api/characters", json={"name": "Beltron"})
    token = client.post("/api/tokens", json={"label": "t2"}).json()["token"]
    assert upload_as(client, token, "eq2advanced-act/0.2.0").status_code == 200

    body = client.get("/api/plugin").json()
    assert body["update_available"] is False
    pa._meta_cache = None


def test_an_unrecognizable_agent_never_clears_what_we_knew(client):
    """A batch sent by something that does not name itself must not make an old
    install look like a fresh one — nor invent a version for it."""
    import auth

    client.post("/api/characters", json={"name": "Beltron"})
    token = client.post("/api/tokens", json={"label": "t3"}).json()["token"]
    assert upload_as(client, token, "eq2advanced-act/0.1.0").status_code == 200
    assert upload_as(client, token, "curl/8.4.0").status_code == 200

    conn = dbmod.get_db()
    row = conn.execute(
        "SELECT client_version FROM device_tokens WHERE token_hash=?",
        (auth._sha(token),)).fetchone()
    assert row["client_version"] == "0.1.0"
