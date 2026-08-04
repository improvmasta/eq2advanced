"""Upload size cap and the parse-only escape hatch.

The site ships with both limits at 0 (unlimited) — these tests turn them on to
prove the machinery works, because the point of building it now is that Lindsay
can flip it later without a migration.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

LOG = (
    "(1722556800)[Thu Aug  1 21:00:00 2026] You have entered The Estate of Unrest.\r\n"
    "(1722556801)[Thu Aug  1 21:00:01 2026] YOU hit a training dummy for 100 crushing damage.\r\n"
    "(1722556803)[Thu Aug  1 21:00:03 2026] YOU hit a training dummy for 120 crushing damage.\r\n"
    "(1722556804)[Thu Aug  1 21:00:04 2026] You have killed a training dummy.\r\n"
)
PADDING = "(1722556802)[Thu Aug  1 21:00:02 2026] Somebody says something long.\r\n"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-limits")
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
        c.post("/api/auth/register", json={"username": "limits",
                                           "password": "hunter2hunter2",
                                           "sq_id": 1, "answer": "pet"})
        yield c, tmp
    mp.undo()


def wait_ready(c, sid):
    for _ in range(60):
        s = c.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] in ("ready", "error"):
            assert s["status"] == "ready", s["error"]
            return s
        time.sleep(0.1)
    raise AssertionError("parse never finished")


def test_unlimited_by_default(client):
    c, _ = client
    assert c.get("/api/uploads/limits").json()["upload_max_bytes"] == 0
    sid = c.post("/api/uploads", files={"file": ("a.txt", LOG.encode())},
                 data={"character_name": "Bobby"}).json()["session_id"]
    s = wait_ready(c, sid)
    assert s["retain_raw"] == 1 and s["raw_deleted_ts"] is None
    assert c.get("/api/uploads/limits").json()["stored_bytes"] > 0


def test_over_the_cap_is_refused_but_offers_parse_only(client):
    c, tmp = client
    body = (LOG + PADDING * 500).encode()
    conn = dbmod.get_db()
    with conn:
        dbmod.set_setting(conn, "upload_max_bytes", len(body) // 2)

    r = c.post("/api/uploads", files={"file": ("big.txt", body)},
               data={"character_name": "Bobby"})
    assert r.status_code == 413
    assert r.headers.get("X-Parse-Only-Allowed") == "1"
    assert "without keeping the file" in r.json()["detail"]
    # nothing of the refused upload is left behind
    assert not list(Path(tmp / "uploads").glob(".incoming-*"))

    # the same file goes through when it isn't being kept
    r = c.post("/api/uploads", files={"file": ("big.txt", body)},
               data={"character_name": "Bobby", "retain_raw": "0"})
    assert r.status_code == 200
    s = wait_ready(c, r.json()["session_id"])
    assert s["retain_raw"] == 0 and s["raw_deleted_ts"] is not None
    detail = c.get(f"/api/sessions/{s['id']}").json()
    assert detail["encounters"], "the parse survived even though the log did not"

    # the bytes are gone, and it can never be reparsed
    assert not (Path(tmp / "uploads") / f"{s['upload_sha256']}.txt.gz").exists()
    rr = c.post(f"/api/sessions/{s['id']}/reparse")
    assert rr.status_code == 409 and "nothing left to reparse" in rr.json()["detail"]
    with conn:
        dbmod.set_setting(conn, "upload_max_bytes", 0)


def test_parse_only_keeps_a_file_another_session_still_needs(client):
    """The gzip is content-addressed and shared. Dropping one session's copy
    must not delete the bytes out from under someone else's session."""
    c, tmp = client
    keep = c.post("/api/uploads", files={"file": ("shared.txt", LOG.encode())},
                  data={"character_name": "Keeper"}).json()["session_id"]
    kept = wait_ready(c, keep)
    drop = c.post("/api/uploads", files={"file": ("shared.txt", LOG.encode())},
                  data={"character_name": "Dropper", "retain_raw": "0"}).json()["session_id"]
    wait_ready(c, drop)
    assert (Path(tmp / "uploads") / f"{kept['upload_sha256']}.txt.gz").exists()
    assert c.post(f"/api/sessions/{keep}/reparse").status_code == 200


def test_storage_quota_blocks_keeping_but_not_parsing(client):
    c, _ = client
    conn = dbmod.get_db()
    with conn:
        dbmod.set_setting(conn, "storage_max_bytes", 1)
    r = c.post("/api/uploads", files={"file": ("more.txt", (LOG + PADDING).encode())},
               data={"character_name": "Bobby"})
    assert r.status_code == 413 and r.headers.get("X-Parse-Only-Allowed") == "1"
    r = c.post("/api/uploads", files={"file": ("more.txt", (LOG + PADDING).encode())},
               data={"character_name": "Bobby", "retain_raw": "0"})
    assert r.status_code == 200
    wait_ready(c, r.json()["session_id"])
    with conn:
        dbmod.set_setting(conn, "storage_max_bytes", 0)


def test_limits_report_no_edge_cap_off_the_proxy(client):
    """A request that didn't come through Cloudflare isn't told about its cap.
    The header rule itself is pinned in test_auth.py."""
    c, _ = client
    assert c.get("/api/uploads/limits").json()["edge_max_bytes"] == 0


def test_admin_can_set_the_knobs(client):
    c, _ = client
    r = c.put("/api/admin/settings", json={"upload_max_bytes": 5 << 20})
    assert r.status_code == 200 and r.json()["upload_max_bytes"] == 5 << 20
    assert c.put("/api/admin/settings", json={"nonsense": 1}).status_code == 422
    users = c.get("/api/admin/users").json()["users"]
    me = next(u for u in users if u["username"] == "limits")
    assert me["stored_bytes"] > 0 and me["session_count"] >= 3
    assert c.post(f"/api/admin/users/{me['id']}/limits",
                  json={"upload_max_bytes": 0}).json()["upload_max_bytes"] == 0
    # the per-user override wins over the site setting
    assert c.get("/api/uploads/limits").json()["upload_max_bytes"] == 0
    c.put("/api/admin/settings", json={"upload_max_bytes": 0})
