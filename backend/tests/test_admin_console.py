"""The admin console: overview alerts, paged accounts, audit paging, feedback.

The overview used to list every session in a non-final state and call it "jobs
needing attention", which meant a healthy raid night — every plugin streaming
at once — read as two dozen problems. These tests pin the distinction: an
alert is something broken, a live stream is a count.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-admin")
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


def sign_in(client, username, password="hunter2hunter2", fresh=False):
    client.cookies.clear()
    body = {"username": username, "password": password}
    if fresh:
        body |= {"sq_id": 1, "answer": f"{username}-pet"}
    r = client.post(f"/api/auth/{'register' if fresh else 'login'}", json=body)
    assert r.status_code == 200, r.text
    return r.json()["user"]


@pytest.fixture(scope="module")
def accounts(client):
    """First account is admin (auth rule); the rest are plain users."""
    admin = sign_in(client, "boss", fresh=True)
    others = [sign_in(client, name, fresh=True) for name in ("alice", "bob", "carol")]
    return {"admin": admin, "others": others}


# ---------- overview: alerts vs live ----------

def _session_for(conn, user_id, name, status, created_ts, last_ingest_ts=None, error=None):
    cur = conn.execute("INSERT INTO characters (user_id, name) VALUES (?,?)", (user_id, name))
    char_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO sessions (character_id, source, status, error, created_ts, last_ingest_ts) "
        "VALUES (?,'live',?,?,?,?)", (char_id, status, error, created_ts, last_ingest_ts))
    return cur.lastrowid


@pytest.fixture(scope="module")
def sessions(client, accounts):
    """One of each state, written straight to the table — the point is what the
    console makes of them, not how they got there."""
    now = int(time.time())
    conn = dbmod.get_db()
    with conn:
        return {
            "broken": _session_for(conn, accounts["others"][0]["id"], "Brokey", "error",
                                   now - 300, error="Traceback\nValueError: bad line"),
            "stuck": _session_for(conn, accounts["others"][1]["id"], "Stucky", "parsing",
                                  now - 3600, last_ingest_ts=now - 3600),
            "fresh": _session_for(conn, accounts["others"][1]["id"], "Freshy", "parsing", now),
            "streaming": _session_for(conn, accounts["others"][2]["id"], "Streamy",
                                      "receiving", now),
        }


def test_overview_alerts_are_only_what_is_broken(client, accounts, sessions):
    sign_in(client, "boss")
    broken, stuck = sessions["broken"], sessions["stuck"]
    d = client.get("/api/admin/overview").json()
    assert "jobs" not in d  # the old undifferentiated list is gone
    by_id = {a["id"]: a for a in d["alerts"]}
    assert by_id[broken]["kind"] == "error"
    assert by_id[broken]["username"] == "alice" and by_id[broken]["character"] == "Brokey"
    assert "ValueError" in by_id[broken]["error"]
    assert by_id[stuck]["kind"] == "stuck" and by_id[stuck]["age_s"] >= 3600
    # a parse that started a second ago is work in flight, not a problem
    assert sessions["fresh"] not in by_id
    assert sessions["streaming"] not in by_id

    assert d["live"]["receiving"] >= 1
    assert d["live"]["parsing"] == 1  # fresh only — the stuck one is an alert instead


def test_overview_settings_carry_all_three_keys(client, accounts):
    sign_in(client, "boss")
    d = client.get("/api/admin/overview").json()
    assert set(d["settings"]) == {"upload_max_bytes", "storage_max_bytes", "registration_open"}


# ---------- accounts: search, sort, paging ----------

def test_users_search_filters(client, accounts):
    sign_in(client, "boss")
    d = client.get("/api/admin/users?q=ali").json()
    assert [u["username"] for u in d["users"]] == ["alice"]
    assert d["total"] == 1


def test_users_paging_reports_the_whole_total(client, accounts):
    sign_in(client, "boss")
    first = client.get("/api/admin/users?sort=username&dir=asc&limit=2&offset=0").json()
    second = client.get("/api/admin/users?sort=username&dir=asc&limit=2&offset=2").json()
    assert [u["username"] for u in first["users"]] == ["alice", "bob"]
    assert [u["username"] for u in second["users"]] == ["boss", "carol"]
    assert first["total"] == second["total"] == 4


def test_users_sort_is_server_side_and_whitelisted(client, accounts):
    sign_in(client, "boss")
    desc = client.get("/api/admin/users?sort=username&dir=desc").json()["users"]
    assert [u["username"] for u in desc] == ["carol", "boss", "bob", "alice"]
    assert client.get("/api/admin/users?sort=pw_hash").status_code == 422
    assert client.get("/api/admin/users?sort=username&dir=sideways").status_code == 422


def test_users_aggregates_come_from_the_grouped_joins(client, accounts, sessions):
    """The error count rides on `SUM(status='error')` — SQLite booleans are ints,
    and the whole column silently zeroes if that ever stops being true."""
    sign_in(client, "boss")
    rows = {u["username"]: u for u in client.get("/api/admin/users").json()["users"]}
    assert rows["alice"]["error_count"] == 1  # the broken session above
    assert rows["alice"]["session_count"] == 1
    assert rows["alice"]["character_count"] == 1
    assert rows["boss"]["session_count"] == 0
    assert rows["boss"]["stored_bytes"] == 0
    assert rows["boss"]["run_count"] == 0


# ---------- audit paging ----------

def test_audit_pages_and_totals(client, accounts):
    sign_in(client, "boss")
    target = accounts["others"][0]["id"]
    for role in ("curator", "user"):
        assert client.post(f"/api/admin/users/{target}/role", json={"role": role}).status_code == 200
    page = client.get("/api/admin/audit?limit=1").json()
    second = client.get("/api/admin/audit?limit=1&offset=1").json()
    assert len(page["entries"]) == 1 and len(second["entries"]) == 1
    assert page["entries"][0]["id"] != second["entries"][0]["id"]
    assert page["total"] == second["total"] >= 2


# ---------- feedback ----------

def test_feedback_needs_an_account(client):
    client.cookies.clear()
    r = client.post("/api/feedback", json={"kind": "bug", "body": "broken"})
    assert r.status_code == 401


def test_feedback_submit_and_validate(client, accounts):
    sign_in(client, "alice")
    r = client.post("/api/feedback", json={"kind": "bug", "body": "DPS looks wrong",
                                           "page": "/zones/7?tab=damage"})
    assert r.status_code == 200 and r.json()["id"]
    assert client.post("/api/feedback", json={"kind": "rant", "body": "x"}).status_code == 422
    assert client.post("/api/feedback", json={"kind": "bug", "body": "  "}).status_code == 422
    assert client.post("/api/feedback",
                       json={"kind": "bug", "body": "x" * 4001}).status_code == 422


def test_feedback_is_admin_only_to_read(client, accounts):
    sign_in(client, "alice")
    assert client.get("/api/admin/feedback").status_code == 403


def test_feedback_triage_lifecycle(client, accounts):
    sign_in(client, "bob")
    client.post("/api/feedback", json={"kind": "suggestion", "body": "dark mode for the rail"})

    sign_in(client, "boss")
    d = client.get("/api/admin/feedback").json()
    assert d["total"] >= 2 and d["open_count"] >= 2
    bug = next(i for i in d["items"] if i["kind"] == "bug")
    assert bug["username"] == "alice" and bug["page"] == "/zones/7?tab=damage"

    assert [i["kind"] for i in client.get("/api/admin/feedback?kind=suggestion")
            .json()["items"]] == ["suggestion"]

    r = client.patch(f"/api/admin/feedback/{bug['id']}", json={"status": "planned"})
    assert r.status_code == 200 and r.json()["status"] == "planned"
    moved = next(i for i in client.get("/api/admin/feedback").json()["items"]
                 if i["id"] == bug["id"])
    assert moved["status"] == "planned" and moved["updated_ts"]
    assert [i["id"] for i in client.get("/api/admin/feedback?status=open").json()["items"]] \
        != [bug["id"]]

    # every triage action lands in the audit log
    actions = [e["action"] for e in client.get("/api/admin/audit").json()["entries"]]
    assert "feedback_status" in actions

    assert client.patch(f"/api/admin/feedback/{bug['id']}",
                        json={"status": "wontfix"}).status_code == 422
    assert client.delete(f"/api/admin/feedback/{bug['id']}").status_code == 200
    assert all(i["id"] != bug["id"] for i in client.get("/api/admin/feedback").json()["items"])

    assert client.patch("/api/admin/feedback/9999", json={"status": "open"}).status_code == 404
    assert client.delete("/api/admin/feedback/9999").status_code == 404
