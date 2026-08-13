"""The two hand marks, on the account (schema v35).

What is worth testing here is not that a key/value store stores things. It is
the three rules the store exists for, each of which was a decision:

* **The absent row is the third state.** Yes, no and nothing-said, where
  nothing-said takes the ACT-list default. A store that could only hold a set
  of names could not express the useful one.
* **A write MERGES.** The client sends the abilities it has something to say
  about, never the world, so two tabs cannot undo each other by each PUTting
  what they last saw.
* **They are per account and reach nothing else.** A mark is an ability name;
  it is not a share, and one account's marks are invisible to another.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
import marks as marksmod


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-marks")
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
               json={"username": "marker", "password": "hunter2hunter2"})
        yield c
    mp.undo()


@pytest.fixture(autouse=True)
def _signed_in(client):
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"username": "marker", "password": "hunter2hunter2"})
    assert r.status_code == 200, r.text
    conn = dbmod.get_db()
    with conn:
        conn.execute("DELETE FROM user_marks")
    yield


def put(client, patch):
    r = client.put("/api/marks", json={"marks": patch})
    assert r.status_code == 200, r.text
    return r.json()["marks"]


def test_an_empty_account_says_nothing_rather_than_no(client):
    """Both kinds present, both empty. A caller has to be able to tell "no
    answers" from "not asked" — the first takes the ACT-list default and the
    second means the read has not landed yet."""
    assert client.get("/api/marks").json()["marks"] == {"joust": {}, "mini": {}}


def test_a_mark_is_an_answer_and_the_third_state_is_the_absent_row(client):
    """False is a real answer and is NOT the same as unmarked: an ability ACT's
    list knows defaults to jousted, so `false` is the only way to say the raid
    stands in this one, and it has to survive being written down."""
    put(client, {"joust": {"Soul Paralysis": True,
                           "Blanket of Eternal Night": False}})
    held = client.get("/api/marks").json()["marks"]
    assert held["joust"] == {"Soul Paralysis": True,
                             "Blanket of Eternal Night": False}

    # null is the way back to nothing-said, which is neither of the other two
    put(client, {"joust": {"Blanket of Eternal Night": None}})
    assert client.get("/api/marks").json()["marks"]["joust"] == {
        "Soul Paralysis": True}


def test_a_write_merges_and_the_two_kinds_are_separate(client):
    """One endpoint serves a single pill click and a browser handing over
    everything it had, and it must never be a replace: the dashboard and the
    raid page are commonly open in two tabs, and each PUTting the world as it
    last saw it would have them undoing each other."""
    put(client, {"joust": {"A": True, "B": True}, "mini": {"A": False}})
    after = put(client, {"joust": {"C": False}})
    assert after["joust"] == {"A": True, "B": True, "C": False}
    # marking for the mini panel says nothing about jousting the same ability
    assert after["mini"] == {"A": False}
    # and the reply is the whole set, not the patch — what a client adopting
    # its old localStorage marks needs to hold afterwards
    assert after == client.get("/api/marks").json()["marks"]


def test_a_kind_nobody_knows_is_not_stored(client):
    """`joust` and `mini` are the two marks that exist. This is not a settings
    service and an unknown key must not quietly become one."""
    put(client, {"hairstyle": {"Mohawk": True}})
    assert client.get("/api/marks").json()["marks"] == {"joust": {}, "mini": {}}


def test_marks_are_per_account(client):
    put(client, {"joust": {"Soul Paralysis": True}})
    client.post("/api/auth/register",
                json={"username": f"other{uuid.uuid4().hex[:8]}",
                      "password": "hunter2hunter2"})
    assert client.get("/api/marks").json()["marks"] == {"joust": {}, "mini": {}}


def test_reading_and_writing_need_an_account(client):
    client.cookies.clear()
    assert client.get("/api/marks").status_code == 401
    assert client.put("/api/marks", json={"marks": {}}).status_code == 401


def test_a_runaway_client_cannot_fill_the_table(client, monkeypatch):
    """The cap is a backstop against a loop, not a budget anybody spends — so
    what matters is which way it fails. Past it a NEW ability is refused and
    every mark already stored keeps working, including turning one OFF: an
    account at the ceiling that could not un-mark anything would be stuck with
    a countdown it could not get rid of."""
    monkeypatch.setattr(marksmod, "MAX_PER_KIND", 3)
    put(client, {"joust": {"A": True, "B": True, "C": True, "D": True}})
    held = client.get("/api/marks").json()["marks"]["joust"]
    assert len(held) == 3 and "D" not in held
    # re-answering one already held is never refused
    put(client, {"joust": {"A": False}})
    assert client.get("/api/marks").json()["marks"]["joust"]["A"] is False
    # and a request nobody could have typed is refused outright
    huge = {str(i): True for i in range(marksmod.MAX_PATCH + 1)}
    assert client.put("/api/marks",
                      json={"marks": {"joust": huge}}).status_code == 413
