"""Phase 3: live ingest contract. Device-token auth, batch idempotency,
line-level dedupe, incremental encounter finalization, and the golden
equivalence run — bobby.txt streamed in batches must produce the same rollups
as uploading the whole file."""

import gzip
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

GOLDEN = Path("/home/lindsay/bobby.txt")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-ingest")
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
                   json={"email": "ingest@test.local", "password": "hunter2hunter2"})
        assert r.status_code == 200, r.text
        yield c
    mp.undo()


def mint_token(client, char_name):
    r = client.post("/api/characters", json={"name": char_name})
    assert r.status_code == 200, r.text
    char_id = r.json()["id"]
    r = client.post(f"/api/characters/{char_id}/tokens", json={"label": "test"})
    assert r.status_code == 200, r.text
    return char_id, r.json()["id"], r.json()["token"]


def send_batch(client, token, lines, batch_id=None, mode="live", gz=False):
    body = json.dumps({"batch_id": batch_id or str(uuid.uuid4()),
                       "mode": mode, "lines": lines}).encode()
    headers = {"Authorization": f"Bearer {token}"}
    if gz:
        body = gzip.compress(body)
        headers["Content-Encoding"] = "gzip"
    return client.post("/api/ingest/batch", content=body, headers=headers)


def line(ts, body):
    return f"({ts})[Thu Aug  1 21:00:00 2026] {body}\r\n"


T0 = 1722556800
FIGHT_A = [
    line(T0, "You have entered The Estate of Unrest."),
    line(T0 + 1, "YOU hit a training dummy for 100 crushing damage."),
    line(T0 + 3, "YOU hit a training dummy for 120 crushing damage."),
    line(T0 + 4, "You have killed a training dummy."),
]
FIGHT_B = [
    line(T0 + 100, "YOU hit a sparring golem for 200 heat damage."),
    line(T0 + 102, "YOU hit a sparring golem for 250 heat damage."),
]


def test_hello_and_bad_tokens(client):
    _, _, token = mint_token(client, "Hellotest")
    r = client.get("/api/ingest/hello", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["character"]["name"] == "Hellotest"
    assert r.json()["session"] is None
    assert client.get("/api/ingest/hello").status_code == 401
    assert client.get("/api/ingest/hello",
                      headers={"Authorization": "Bearer nope"}).status_code == 401


def test_batch_incremental_finalization(client):
    _, _, token = mint_token(client, "Incy")
    r = send_batch(client, token, FIGHT_A, gz=True)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["accepted"] == 4 and d["duplicates"] == 0
    sid = d["session_id"]

    # fight A is still the open segment — no encounter yet
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["session"]["status"] == "receiving"
    assert detail["encounters"] == []

    # damage 100s later closes fight A and finalizes it incrementally
    r = send_batch(client, token, FIGHT_B)
    assert r.json()["accepted"] == 2
    encs = client.get(f"/api/sessions/{sid}").json()["encounters"]
    assert len(encs) == 1
    assert encs[0]["zone"] == "The Estate of Unrest"
    assert encs[0]["logger_damage"] == 220

    # hello now reports the receiving session
    r = client.get("/api/ingest/hello", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["session"] == sid

    # done: rebuild from raw -> ready, both fights present, totals intact
    r = client.post("/api/ingest/backfill/done",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.json()["session_id"] == sid
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["session"]["status"] == "ready"
    assert detail["session"]["line_count"] == 6
    assert detail["session"]["started_ts"] == T0
    assert [e["logger_damage"] for e in detail["encounters"]] == [220, 450]


def test_batch_idempotency_and_line_dedupe(client):
    _, _, token = mint_token(client, "Dupey")
    batch_id = str(uuid.uuid4())
    lines = [
        line(T0, "You have entered The Estate of Unrest."),
        line(T0 + 1, "YOU hit a training dummy for 100 crushing damage."),
        # a legitimate identical hit in the same second must count twice
        line(T0 + 1, "YOU hit a training dummy for 100 crushing damage."),
    ]
    d1 = send_batch(client, token, lines, batch_id=batch_id).json()
    assert d1["accepted"] == 3 and d1["duplicates"] == 0

    # same batch_id replayed -> stored response, nothing re-ingested
    d2 = send_batch(client, token, lines, batch_id=batch_id).json()
    assert d2["accepted"] == 3 and d2["session_id"] == d1["session_id"]
    assert d2.get("replayed") is True

    # same lines under a NEW batch_id -> all line-level duplicates
    d3 = send_batch(client, token, lines).json()
    assert d3["accepted"] == 0 and d3["duplicates"] == 3

    sess = client.get(f"/api/sessions/{d1['session_id']}").json()["session"]
    assert sess["line_count"] == 3


def test_revoked_token_locked_out(client):
    _, token_id, token = mint_token(client, "Revoky")
    assert send_batch(client, token, FIGHT_A).status_code == 200
    assert client.post(f"/api/tokens/{token_id}/revoke").status_code == 200
    assert send_batch(client, token, FIGHT_B).status_code == 401
    assert client.get("/api/ingest/hello",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_batch_validation(client):
    _, _, token = mint_token(client, "Validy")
    headers = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/ingest/batch", content=b"not json", headers=headers)
    assert r.status_code == 400
    r = client.post("/api/ingest/batch",
                    content=json.dumps({"mode": "live", "lines": []}).encode(),
                    headers=headers)
    assert r.status_code == 422
    r = client.post("/api/ingest/batch",
                    content=json.dumps({"batch_id": "x", "mode": "bulk",
                                        "lines": []}).encode(),
                    headers=headers)
    assert r.status_code == 422


def test_stream_of_finished_session_closes(client):
    _, _, token = mint_token(client, "Streamy")
    sid = send_batch(client, token, FIGHT_A).json()["session_id"]
    client.post("/api/ingest/backfill/done",
                headers={"Authorization": f"Bearer {token}"})
    r = client.get(f"/api/sessions/{sid}/stream")
    assert r.status_code == 200
    assert "event: encounter" in r.text
    assert '"status": "ready"' in r.text


# ---- golden equivalence: streamed batches == whole-file upload ----

@pytest.mark.skipif(not GOLDEN.exists(), reason="golden fixture not present")
def test_golden_equivalence(client):
    # character + token first: the upload then resolves to the same owned row
    _, _, token = mint_token(client, "Bobby")

    # whole file through the upload path
    with GOLDEN.open("rb") as fh:
        r = client.post("/api/uploads", files={"file": ("bobby.txt", fh)},
                        data={"character_name": "Bobby"})
    assert r.status_code == 200, r.text
    upload_sid = r.json()["session_id"]
    for _ in range(600):
        s = client.get(f"/api/sessions/{upload_sid}").json()["session"]
        if s["status"] in ("ready", "error"):
            assert s["status"] == "ready", s["error"]
            break
        time.sleep(0.5)
    else:
        raise AssertionError("upload parse never finished")

    # same file streamed as live batches, cut on 120s log-time windows
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    from simulate_live import batches
    live_sid = None
    for batch in batches(str(GOLDEN), 120):
        d = send_batch(client, token, batch, gz=True).json()
        assert d["duplicates"] == 0
        live_sid = d["session_id"]

    conn = sqlite3.connect(dbmod.DB_PATH)
    conn.row_factory = sqlite3.Row

    def encounter_rows(sid):
        return [tuple(r) for r in conn.execute(
            "SELECT zone, name, is_named, started_ts, ended_ts, duration_s, success "
            "FROM encounters WHERE session_id=? ORDER BY started_ts", (sid,))]

    def actor_rows(sid):
        return sorted(tuple(r) for r in conn.execute(
            "SELECT e.started_ts, n.name, s.damage, s.dps, s.heals, s.wards_absorbed, "
            "s.power_fed, s.deaths, s.rez_casts, s.active_s "
            "FROM encounter_actor_stats s "
            "JOIN encounters e ON e.id = s.encounter_id "
            "JOIN entities n ON n.id = s.entity_id WHERE e.session_id=?", (sid,)))

    def ability_rows(sid):
        return sorted(tuple(r) for r in conn.execute(
            "SELECT e.started_ts, n.name, n.kind, a.name, s.kind, s.casts, s.hits, "
            "s.crits, s.misses, s.resists, s.total, s.min, s.max "
            "FROM encounter_ability_stats s "
            "JOIN encounters e ON e.id = s.encounter_id "
            "JOIN entities n ON n.id = s.entity_id "
            "JOIN abilities a ON a.id = s.ability_id WHERE e.session_id=?", (sid,)))

    # pre-done: every encounter the incremental path finalized must already
    # match the upload parse exactly (the tail segment is still open)
    live_encs = encounter_rows(live_sid)
    upload_encs = encounter_rows(upload_sid)
    assert live_encs == upload_encs[: len(live_encs)]
    assert len(upload_encs) - len(live_encs) <= 1

    # replaying a batch with a fresh batch_id -> pure line-level duplicates
    replayed = next(batches(str(GOLDEN), 120))
    d = send_batch(client, token, replayed, gz=True).json()
    assert d["accepted"] == 0 and d["duplicates"] == len(replayed)
    assert d["session_id"] == live_sid
    assert encounter_rows(live_sid) == live_encs

    # close the session -> full equality on every table
    r = client.post("/api/ingest/backfill/done",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.json()["session_id"] == live_sid
    sess = client.get(f"/api/sessions/{live_sid}").json()["session"]
    assert sess["status"] == "ready", sess["error"]

    assert encounter_rows(live_sid) == upload_encs
    assert actor_rows(live_sid) == actor_rows(upload_sid)
    assert ability_rows(live_sid) == ability_rows(upload_sid)

    up = client.get(f"/api/sessions/{upload_sid}").json()["session"]
    assert sess["line_count"] == up["line_count"]
    assert (sess["started_ts"], sess["ended_ts"]) == (up["started_ts"], up["ended_ts"])
    conn.close()
