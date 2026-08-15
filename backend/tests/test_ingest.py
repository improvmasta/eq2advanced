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
# The reference night spans about two hours. Ten-minute windows still exercise
# twelve independent live batches and cross-batch encounter finalization without
# paying for 48 extra request/transaction boundaries in this already-large test.
GOLDEN_BATCH_WINDOW_S = 600


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
                   json={"username": "ingest", "password": "hunter2hunter2"})
        assert r.status_code == 200, r.text
        yield c
    mp.undo()


def mint_token(client, char_name):
    """A character plus an ACCOUNT token (v13 — the token is not bound to the
    character; batches name it). Returns (char_id, token_id, token) where the
    token is a `Tok` carrying the name the tests send with each batch."""
    r = client.post("/api/characters", json={"name": char_name})
    assert r.status_code == 200, r.text
    char_id = r.json()["id"]
    r = client.post("/api/tokens", json={"label": "test"})
    assert r.status_code == 200, r.text
    return char_id, r.json()["id"], Tok(r.json()["token"], char_name)


class Tok(str):
    """The token string, remembering which character the test is uploading as."""

    def __new__(cls, value, character):
        obj = super().__new__(cls, value)
        obj.character = character
        return obj


def send_batch(client, token, lines, batch_id=None, mode="live", gz=False,
               character=None):
    payload = {"batch_id": batch_id or str(uuid.uuid4()), "mode": mode, "lines": lines}
    name = character if character is not None else getattr(token, "character", None)
    if name:
        payload["character"] = name
    body = json.dumps(payload).encode()
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
    """v13: hello answers for the ACCOUNT. There is no character to name until a
    log turns up, which is the whole point — one pairing covers every alt."""
    _, _, token = mint_token(client, "Hellotest")
    r = client.get("/api/ingest/hello", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["account"] == "ingest"
    assert r.json()["session"] is None
    assert r.json()["receiving"] == []
    assert client.get("/api/ingest/hello").status_code == 401
    assert client.get("/api/ingest/hello",
                      headers={"Authorization": "Bearer nope"}).status_code == 401


def test_batch_must_name_a_character(client):
    """Without a name there is nobody to attribute the log to, and the parser
    cannot resolve subjects without knowing whose it is. 422, not a guess."""
    client.post("/api/characters", json={"name": "Namey"})
    token = client.post("/api/tokens", json={}).json()["token"]
    r = send_batch(client, token, FIGHT_A, character=None)
    assert r.status_code == 422, r.text


def test_one_token_serves_every_character(client):
    """The reason tokens stopped belonging to a character: alts. Two names on
    one token land in two sessions, and the second needs no setup at all."""
    _, _, token = mint_token(client, "Maintank")
    first = send_batch(client, token, FIGHT_A).json()["session_id"]
    # a name this account has never used — created on the spot
    second = send_batch(client, token, FIGHT_A, character="Altpriest").json()["session_id"]
    assert first != second
    names = {c["name"] for c in client.get("/api/characters").json()["characters"]}
    assert {"Maintank", "Altpriest"} <= names
    hello = client.get("/api/ingest/hello",
                       headers={"Authorization": f"Bearer {token}"}).json()
    assert {r["character"] for r in hello["receiving"]} >= {"Maintank", "Altpriest"}


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


def test_act_end_finalizes_the_fight_without_waiting(client):
    """`/act end` is the one thing that closes a live segment on the spot. The
    writer normally holds the last segment for CLOSE_S in case a late kill line
    joins it (the test above: fight A has no card until fight B arrives 100s
    later), and the raid saying the pull is over settles that — the card has to
    be there while everyone is still looking at the meter."""
    _, _, token = mint_token(client, "Endy")
    sid = send_batch(
        client, token,
        FIGHT_A + [line(T0 + 6, "Unknown command: 'act end'")]
    ).json()["session_id"]

    encs = client.get(f"/api/sessions/{sid}").json()["encounters"]
    assert len(encs) == 1 and encs[0]["logger_damage"] == 220
    from pipeline import live as livemod
    assert livemod.in_combat(sid) is False

    # and the cut survives the close-time rebuild from raw, so what the meter
    # showed and what the session ends up holding are the same two fights
    send_batch(client, token, [line(T0 + 8, "YOU hit a training dummy for 60 crushing damage.")])
    client.post("/api/ingest/backfill/done",
                headers={"Authorization": f"Bearer {token}"})
    detail = client.get(f"/api/sessions/{sid}").json()
    assert [e["logger_damage"] for e in detail["encounters"]] == [220, 60]


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


# ---- the in-flight view (pipeline/livemeter.py) ----
#
# The dashboard's live meter is a VIEW over the open segment: no rows, no
# encounter, nothing a later rebuild has to agree with. These pin the two
# gates that decide whether it is computed at all, and the fact that it never
# leaks into the record — `test_golden_equivalence` below is the other half of
# that promise.

def open_fight(ts_offset=0, zone=True):
    """A pull that is happening NOW, so it clears the live-lag gate. A zone
    line hard-cuts a segment, so a continuation batch must not repeat one."""
    now = int(time.time()) + ts_offset
    lines = [line(now, "You have entered The Estate of Unrest.")] if zone else []
    return lines + [
        line(now + 1, "YOU hit a knotted guardian for 1000 crushing damage."),
        line(now + 2, "YOU hit a knotted guardian for 1500 crushing damage."),
        line(now + 2, "Mendya heals YOU for 400 hit points."),
    ]


def test_no_snapshot_until_somebody_is_watching(client):
    """A snapshot costs a pass over the open fight on every batch, so a raid
    with no dashboard open pays nothing."""
    from pipeline import live as livemod
    _, _, token = mint_token(client, "Unwatched")
    sid = send_batch(client, token, open_fight()).json()["session_id"]
    assert livemod.live_snapshot(sid) is None

    livemod.mark_watched(sid)
    send_batch(client, token, open_fight(4, zone=False)).json()
    snap = livemod.live_snapshot(sid)
    assert snap is not None
    fight = snap["fight"]
    assert fight["zone"] == "The Estate of Unrest"
    assert fight["provisional_name"] == "a knotted guardian"
    assert fight["provisional_is_named"] is False
    assert fight["raid"]["damage"] == 5000       # both batches, still open
    assert fight["raid"]["heals"] == 800
    assert [a["name"] for a in fight["actors"] if a["kind"] == "player"] \
        == ["Unwatched", "Mendya"]


def test_watching_expires(client):
    from pipeline import live as livemod
    _, _, token = mint_token(client, "Expiry")
    sid = send_batch(client, token, open_fight()).json()["session_id"]
    livemod.mark_watched(sid, ttl_s=0)
    send_batch(client, token, open_fight(4, zone=False))
    assert livemod.live_snapshot(sid) is None


def test_a_backfill_is_not_a_raid_in_progress(client):
    """The plugin's own word for it: `mode=backfill` is an old log being
    caught up, and last March's raid must not flash on screen as a pull."""
    from pipeline import live as livemod
    _, _, token = mint_token(client, "Backfilly")
    sid = send_batch(client, token, open_fight(), mode="backfill").json()["session_id"]
    livemod.mark_watched(sid)
    send_batch(client, token, open_fight(4), mode="backfill")
    assert livemod.live_snapshot(sid) is None


def test_live_mode_replaying_old_log_time_is_also_not_a_raid(client):
    """The same protection from the other side — a live-mode client replaying
    history (which is what tools/simulate_live.py does without --restamp)."""
    from pipeline import live as livemod
    _, _, token = mint_token(client, "Oldy")
    sid = send_batch(client, token, FIGHT_A).json()["session_id"]   # T0: 2024
    livemod.mark_watched(sid)
    send_batch(client, token, FIGHT_B)
    assert livemod.live_snapshot(sid) is None


def test_partial_reaches_the_stream_and_writes_nothing(client):
    """The wiring end to end: a snapshot published by a batch comes out of the
    SSE endpoint as a `partial`, and the fight it describes is still not in the
    record.

    The status is flipped by hand because TestClient runs a request to
    COMPLETION before handing back a response — an endless stream would just
    hang. Finalizing properly would drop the live state along with it, which is
    the thing under test, so this closes the stream and leaves memory alone.
    """
    from pipeline import live as livemod
    _, _, token = mint_token(client, "Partly")
    sid = send_batch(client, token, open_fight()).json()["session_id"]
    livemod.mark_watched(sid)
    send_batch(client, token, open_fight(4, zone=False))

    before = client.get(f"/api/sessions/{sid}").json()["encounters"]
    conn = sqlite3.connect(dbmod.DB_PATH)
    conn.execute("UPDATE sessions SET status='ready' WHERE id=?", (sid,))
    conn.commit()
    conn.close()

    r = client.get(f"/api/sessions/{sid}/stream")
    assert "event: partial" in r.text, r.text
    payload = json.loads(r.text.split("event: partial\ndata: ")[1].split("\n\n")[0])
    assert payload["fight"]["raid"]["damage"] == 5000
    assert payload["fight"]["actors"][0]["name"] == "Partly"
    # the fight is still open: it is in the view and NOT in the record
    assert client.get(f"/api/sessions/{sid}").json()["encounters"] == before


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

    # same file streamed as live batches, cut on ten-minute log-time windows
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    from simulate_live import batches
    live_sid = None
    for batch in batches(str(GOLDEN), GOLDEN_BATCH_WINDOW_S):
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
    replayed = next(batches(str(GOLDEN), GOLDEN_BATCH_WINDOW_S))
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
