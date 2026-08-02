"""Phase 2: auth role matrix, per-user isolation, device-token revocation.
Runs the real app via TestClient against a throwaway DATA_DIR."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-auth")
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


def sign_in(client, email, password="hunter2hunter2", fresh=False):
    client.cookies.clear()
    r = client.post(f"/api/auth/{'register' if fresh else 'login'}",
                    json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def upload(client, name, content):
    r = client.post("/api/uploads", files={"file": ("log.txt", content.encode())},
                    data={"character_name": name})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    for _ in range(50):
        s = client.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] in ("ready", "error"):
            assert s["status"] == "ready", s["error"]
            return sid
        time.sleep(0.1)
    raise AssertionError("parse never finished")


LOG_A = (
    "(1722556800)[Thu Aug  1 21:00:00 2026] You have entered The Estate of Unrest.\r\n"
    "(1722556801)[Thu Aug  1 21:00:01 2026] YOU hit a training dummy for 100 crushing damage.\r\n"
    "(1722556803)[Thu Aug  1 21:00:03 2026] YOU hit a training dummy for 120 crushing damage.\r\n"
    "(1722556804)[Thu Aug  1 21:00:04 2026] You have killed a training dummy.\r\n"
)
LOG_B = "(1722556900)[Thu Aug  1 21:01:40 2026] You have entered Freethinker Hideout.\r\n"


def test_anonymous_is_locked_out(client):
    client.cookies.clear()
    for path in ("/api/sessions", "/api/characters"):
        assert client.get(path).status_code == 401
    r = client.post("/api/uploads", files={"file": ("x.txt", b"y")},
                    data={"character_name": "Nobody"})
    assert r.status_code == 401
    assert client.get("/api/auth/me").json()["user"] is None


def test_first_account_is_admin_then_user(client):
    u1 = sign_in(client, "one@x.test", fresh=True)
    assert u1["role"] == "admin"
    u2 = sign_in(client, "two@x.test", fresh=True)
    assert u2["role"] == "user"


def test_login_logout_roundtrip(client):
    sign_in(client, "one@x.test")
    assert client.get("/api/auth/me").json()["user"]["email"] == "one@x.test"
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": "one@x.test", "password": "wrong-wrong"})
    assert r.status_code == 401


def test_per_user_isolation(client):
    sign_in(client, "one@x.test")
    sid = upload(client, "Alpha", LOG_A)
    assert any(s["id"] == sid for s in client.get("/api/sessions").json()["sessions"])

    # user two sees none of it
    sign_in(client, "two@x.test")
    assert client.get("/api/sessions").json()["sessions"] == []
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    # can't take the character either — by pairing or by upload
    assert client.post("/api/characters", json={"name": "Alpha"}).status_code == 409
    r = client.post("/api/uploads", files={"file": ("log.txt", LOG_B.encode())},
                    data={"character_name": "Alpha"})
    assert r.status_code == 409

    # their own character + upload works, and stays theirs
    upload(client, "Beta", LOG_B)
    mine = client.get("/api/sessions").json()["sessions"]
    assert [s["character_name"] for s in mine] == ["Beta"]


def test_admin_sees_everything(client):
    sign_in(client, "one@x.test")
    sessions = client.get("/api/sessions").json()["sessions"]
    assert {s["character_name"] for s in sessions} == {"Alpha", "Beta"}
    beta = next(s for s in sessions if s["character_name"] == "Beta")
    assert client.get(f"/api/sessions/{beta['id']}").status_code == 200


def test_encounter_isolation(client):
    # encounters hang off sessions; a foreign user gets 404 even with a valid id
    sign_in(client, "one@x.test")
    conn = dbmod.get_db()
    row = conn.execute(
        "SELECT e.id FROM encounters e JOIN sessions s ON s.id = e.session_id "
        "JOIN characters c ON c.id = s.character_id "
        "JOIN users u ON u.id = c.user_id WHERE u.email='one@x.test' LIMIT 1").fetchone()
    if row is None:
        pytest.skip("tiny fixture produced no encounters")
    sign_in(client, "two@x.test")
    assert client.get(f"/api/encounters/{row['id']}").status_code == 404


def test_device_token_lifecycle(client):
    import auth as authmod

    sign_in(client, "two@x.test")
    chars = client.get("/api/characters").json()["characters"]
    beta = next(c for c in chars if c["name"] == "Beta")

    r = client.post(f"/api/characters/{beta['id']}/tokens", json={"label": "raid PC"})
    assert r.status_code == 200
    minted = r.json()
    assert minted["pair_payload"].startswith("eq2advanced://pair?host=")
    assert minted["token"] in minted["pair_payload"]

    # plaintext resolves to the character, and touches last_seen
    conn = dbmod.get_db()
    resolved = authmod.device_token_character(conn, minted["token"])
    conn.commit()  # the last_seen touch, else this test connection blocks the app's
    assert resolved["name"] == "Beta"
    tokens = client.get(f"/api/characters/{beta['id']}/tokens").json()["tokens"]
    assert tokens[0]["label"] == "raid PC" and tokens[0]["last_seen_ts"] is not None

    # someone else's character: tokens are invisible and unmintable
    sign_in(client, "one2@x.test", fresh=True)
    assert client.get(f"/api/characters/{beta['id']}/tokens").status_code == 404
    assert client.post(f"/api/characters/{beta['id']}/tokens", json={}).status_code == 404

    # revoke kills it
    sign_in(client, "two@x.test")
    assert client.post(f"/api/tokens/{minted['id']}/revoke").status_code == 200
    assert authmod.device_token_character(conn, minted["token"]) is None
    assert authmod.device_token_character(conn, "not-a-token") is None
    conn.commit()


def test_character_claim_and_delete(client):
    sign_in(client, "two@x.test")
    # an unowned phase-1 row is claimable
    conn = dbmod.get_db()
    with conn:
        conn.execute("INSERT INTO characters (name, world_id) VALUES ('Legacy', 618)")
    r = client.post("/api/characters", json={"name": "Legacy"})
    assert r.status_code == 200 and r.json()["claimed"] is True
    # re-adding your own is a 409; deleting a session-less character works
    assert client.post("/api/characters", json={"name": "Legacy"}).status_code == 409
    legacy = next(c for c in client.get("/api/characters").json()["characters"]
                  if c["name"] == "Legacy")
    assert client.delete(f"/api/characters/{legacy['id']}").status_code == 200
    # a character with sessions refuses deletion
    beta = next(c for c in client.get("/api/characters").json()["characters"]
                if c["name"] == "Beta")
    assert client.delete(f"/api/characters/{beta['id']}").status_code == 409


def test_password_change(client):
    sign_in(client, "two@x.test")
    r = client.post("/api/auth/password", json={"current": "wrong-wrong", "new": "newpass-newpass"})
    assert r.status_code == 401
    r = client.post("/api/auth/password",
                    json={"current": "hunter2hunter2", "new": "newpass-newpass"})
    assert r.status_code == 200
    sign_in(client, "two@x.test", password="newpass-newpass")
    # put it back so ordering doesn't matter on rerun within the module
    client.post("/api/auth/password", json={"current": "newpass-newpass", "new": "hunter2hunter2"})
