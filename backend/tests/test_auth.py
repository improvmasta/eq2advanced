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


def sign_in(client, username, password="hunter2hunter2", fresh=False, sq=True):
    client.cookies.clear()
    body = {"username": username, "password": password}
    if fresh and sq:
        body |= {"sq_id": 1, "answer": f"{username}-pet"}
    r = client.post(f"/api/auth/{'register' if fresh else 'login'}", json=body)
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
    u1 = sign_in(client, "one", fresh=True)
    assert u1["role"] == "admin"
    u2 = sign_in(client, "two", fresh=True)
    assert u2["role"] == "user"


def test_login_logout_roundtrip(client):
    sign_in(client, "one")
    assert client.get("/api/auth/me").json()["user"]["username"] == "one"
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": "one", "password": "wrong-wrong"})
    assert r.status_code == 401


def test_per_user_isolation(client):
    sign_in(client, "one")
    sid = upload(client, "Alpha", LOG_A)
    assert any(s["id"] == sid for s in client.get("/api/sessions").json()["sessions"])

    # user two sees none of it
    sign_in(client, "two")
    assert client.get("/api/sessions").json()["sessions"] == []
    assert client.get(f"/api/sessions/{sid}").status_code == 404

    # ...but claiming the SAME NAME is allowed and takes nothing away from them
    assert client.post("/api/characters", json={"name": "Alpha"}).status_code == 200
    assert client.get("/api/sessions").json()["sessions"] == []
    two_sid = upload(client, "Alpha", LOG_B)
    assert two_sid != sid
    assert [s["id"] for s in client.get("/api/sessions").json()["sessions"]] == [two_sid]
    client.delete(f"/api/sessions/{two_sid}")
    alpha2 = next(c for c in client.get("/api/characters").json()["characters"]
                  if c["name"] == "Alpha")
    client.delete(f"/api/characters/{alpha2['id']}")

    # their own character + upload works, and stays theirs
    upload(client, "Beta", LOG_B)
    mine = client.get("/api/sessions").json()["sessions"]
    assert [s["character_name"] for s in mine] == ["Beta"]

    # user one still has exactly their own session on their own Alpha
    sign_in(client, "one")
    alpha1 = next(c for c in client.get("/api/characters").json()["characters"]
                  if c["name"] == "Alpha")
    assert alpha1["id"] != alpha2["id"] and alpha1["session_count"] == 1


def test_same_log_uploaded_by_two_people(client):
    """Two raiders were on the same night and both have the file. One gzip on
    disk, one session each, and deleting one keeps the other readable."""
    sign_in(client, "one")
    sid = next(s["id"] for s in client.get("/api/sessions").json()["sessions"])
    sign_in(client, "twin", fresh=True)
    twin_sid = upload(client, "Alpha", LOG_A)
    assert twin_sid != sid
    assert client.get(f"/api/sessions/{twin_sid}").status_code == 200
    # re-uploading the same bytes on the same character IS a duplicate
    r = client.post("/api/uploads", files={"file": ("log.txt", LOG_A.encode())},
                    data={"character_name": "Alpha"})
    assert r.status_code == 200 and r.json()["duplicate"] is True
    assert r.json()["session_id"] == twin_sid
    assert client.delete(f"/api/sessions/{twin_sid}").status_code == 200
    sign_in(client, "one")
    assert client.get(f"/api/sessions/{sid}").status_code == 200
    assert client.get(f"/api/sessions/{sid}").json()["encounters"]


def test_admin_sees_nobody_elses_data(client):
    """Admin is an operational role. It runs the site; it does not read the
    site. The only way in is a share."""
    sign_in(client, "two")
    beta_id = upload(client, "Beta", LOG_A)   # a real fight, so there is a run
    beta_run = client.get("/api/zone-runs?scope=mine").json()["zone_runs"][0]
    beta_enc = client.get(f"/api/zone-runs/{beta_run['id']}").json()["encounters"][0]

    sign_in(client, "one")
    assert client.get("/api/auth/me").json()["user"]["role"] == "admin"
    assert {s["character_name"] for s in client.get("/api/sessions").json()["sessions"]} == {"Alpha"}
    assert client.get(f"/api/sessions/{beta_id}").status_code == 404
    assert client.get(f"/api/zone-runs/{beta_run['id']}").status_code == 404
    assert client.get(f"/api/zone-runs/{beta_run['id']}/report").status_code == 404
    assert client.get(f"/api/encounters/{beta_enc['id']}").status_code == 404
    assert client.get(f"/api/encounters/agg?ids={beta_enc['id']}").status_code == 404
    assert client.get(f"/api/encounters/timeline?ids={beta_enc['id']}").status_code == 404
    assert client.get(f"/api/encounters/deaths?ids={beta_enc['id']}").status_code == 404
    assert client.get(f"/api/sessions/{beta_id}/coach").status_code == 404
    assert client.get(f"/api/sessions/{beta_id}/raid-report").status_code == 404
    assert client.delete(f"/api/zone-runs/{beta_run['id']}").status_code == 404


def test_encounter_isolation(client):
    # encounters hang off sessions; a foreign user gets 404 even with a valid id
    sign_in(client, "one")
    conn = dbmod.get_db()
    row = conn.execute(
        "SELECT e.id FROM encounters e JOIN sessions s ON s.id = e.session_id "
        "JOIN characters c ON c.id = s.character_id "
        "JOIN users u ON u.id = c.user_id WHERE u.username='one' LIMIT 1").fetchone()
    if row is None:
        pytest.skip("tiny fixture produced no encounters")
    sign_in(client, "two")
    assert client.get(f"/api/encounters/{row['id']}").status_code == 404


def test_device_token_lifecycle(client):
    import auth as authmod

    sign_in(client, "two")
    chars = client.get("/api/characters").json()["characters"]
    beta = next(c for c in chars if c["name"] == "Beta")

    r = client.post("/api/tokens", json={"label": "raid PC"})
    assert r.status_code == 200
    minted = r.json()
    assert minted["pair_payload"].startswith("eq2advanced://pair?host=")
    assert minted["token"] in minted["pair_payload"]

    # plaintext resolves to the ACCOUNT (v13 — not to a character), and the
    # character is whatever the batch names
    conn = dbmod.get_db()
    resolved = authmod.device_token_row(conn, minted["token"])
    conn.commit()  # the last_seen touch, else this test connection blocks the app's
    assert resolved["character_id"] is None
    assert authmod.resolve_ingest_character(conn, resolved, "Beta")["id"] == beta["id"]
    # a name this account has never used is created on the spot
    made = authmod.resolve_ingest_character(conn, resolved, "Freshalt")
    assert made["name"] == "Freshalt" and made["user_id"] == resolved["user_id"]
    conn.commit()
    tokens = client.get("/api/tokens").json()["tokens"]
    assert tokens[0]["label"] == "raid PC" and tokens[0]["last_seen_ts"] is not None

    # someone else's account: their tokens are invisible and unrevokable
    sign_in(client, "one2", fresh=True)
    assert client.get("/api/tokens").json()["tokens"] == []

    # revoke kills it
    sign_in(client, "two")
    assert client.post(f"/api/tokens/{minted['id']}/revoke").status_code == 200
    assert authmod.device_token_row(conn, minted["token"]) is None
    assert authmod.device_token_row(conn, "not-a-token") is None
    conn.commit()


def test_character_claim_and_delete(client):
    sign_in(client, "two")
    r = client.post("/api/characters", json={"name": "Legacy"})
    assert r.status_code == 200
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
    sign_in(client, "two")
    r = client.post("/api/auth/password", json={"current": "wrong-wrong", "new": "newpass-newpass"})
    assert r.status_code == 401
    r = client.post("/api/auth/password",
                    json={"current": "hunter2hunter2", "new": "newpass-newpass"})
    assert r.status_code == 200
    sign_in(client, "two", password="newpass-newpass")
    # put it back so ordering doesn't matter on rerun within the module
    client.post("/api/auth/password", json={"current": "newpass-newpass", "new": "hunter2hunter2"})


# ---- phase 12: usernames + security-question recovery ----

def test_username_rules(client):
    import ratelimit
    ratelimit.reset_all()
    client.cookies.clear()
    for bad in ("ab", "a" * 21, "Has Space", "no-dashes", "üñî"):
        r = client.post("/api/auth/register",
                        json={"username": bad, "password": "hunter2hunter2"})
        assert r.status_code == 422, f"{bad!r} was accepted"
    # reserved, and taken (case-folded: 'One' is 'one')
    assert client.post("/api/auth/register",
                       json={"username": "admin", "password": "hunter2hunter2"}
                       ).status_code == 409
    assert client.post("/api/auth/register",
                       json={"username": "One", "password": "hunter2hunter2"}
                       ).status_code == 409
    # and a username logs in case-insensitively
    r = client.post("/api/auth/login", json={"username": "ONE", "password": "hunter2hunter2"})
    assert r.status_code == 200 and r.json()["user"]["username"] == "one"


def test_security_question_reset(client):
    import ratelimit
    ratelimit.reset_all()
    u = sign_in(client, "forgetful", fresh=True)          # answer = "forgetful-pet"
    assert u["needs_security_question"] is False

    # start names the question without leaking anything else
    client.cookies.clear()
    r = client.post("/api/auth/reset/start", json={"username": "forgetful"})
    assert r.status_code == 200
    assert r.json()["question"] == auth_questions(client)[1]
    assert "answer" not in r.text and "hash" not in r.text

    # wrong answer is a 401; the right one ignores case and stray whitespace
    assert client.post("/api/auth/reset/complete", json={
        "username": "forgetful", "answer": "nope", "new_password": "brandnew-pass"
    }).status_code == 401
    assert client.post("/api/auth/reset/complete", json={
        "username": "forgetful", "answer": "  FORGETFUL-Pet ", "new_password": "brandnew-pass"
    }).status_code == 200

    sign_in(client, "forgetful", password="brandnew-pass")
    # the old password is dead, and an account with no question can't self-reset
    client.cookies.clear()
    assert client.post("/api/auth/login", json={
        "username": "forgetful", "password": "hunter2hunter2"}).status_code == 401
    sign_in(client, "noquestion", fresh=True, sq=False)
    client.cookies.clear()
    assert client.post("/api/auth/reset/start",
                       json={"username": "noquestion"}).status_code == 404
    assert client.post("/api/auth/reset/start",
                       json={"username": "ghost"}).status_code == 404


def test_reset_signs_out_every_device(client):
    """A reset exists because someone else may have the password — leaving live
    cookies alive would defeat the point."""
    import ratelimit
    ratelimit.reset_all()
    sign_in(client, "hijacked", fresh=True)
    assert client.get("/api/auth/me").json()["user"] is not None
    stolen = dict(client.cookies)

    client.cookies.clear()
    assert client.post("/api/auth/reset/complete", json={
        "username": "hijacked", "answer": "hijacked-pet", "new_password": "afterreset-pw"
    }).status_code == 200

    client.cookies.clear()
    for k, v in stolen.items():
        client.cookies.set(k, v)
    assert client.get("/api/auth/me").json()["user"] is None


def test_login_rate_limit(client):
    import ratelimit
    ratelimit.reset_all()
    client.cookies.clear()
    for _ in range(ratelimit.MAX_FAILURES):
        assert client.post("/api/auth/login", json={
            "username": "one", "password": "wrong-wrong"}).status_code == 401
    # spent: even the RIGHT password is refused until the window passes
    r = client.post("/api/auth/login", json={"username": "one", "password": "hunter2hunter2"})
    assert r.status_code == 429 and r.headers["Retry-After"]
    ratelimit.reset_all()
    assert client.post("/api/auth/login", json={
        "username": "one", "password": "hunter2hunter2"}).status_code == 200


def test_security_question_change_needs_password(client):
    sign_in(client, "two")
    assert client.post("/api/auth/security-question", json={
        "password": "wrong-wrong", "sq_id": 2, "answer": "x"}).status_code == 401
    assert client.post("/api/auth/security-question", json={
        "password": "hunter2hunter2", "sq_id": 99, "answer": "x"}).status_code == 422
    assert client.post("/api/auth/security-question", json={
        "password": "hunter2hunter2", "sq_id": 2, "answer": "Elm Street"}).status_code == 200
    client.cookies.clear()
    assert client.post("/api/auth/reset/start",
                       json={"username": "two"}).json()["sq_id"] == 2


def test_reauth_rate_limit(client):
    """Changing a password re-checks the old one, so it is a password oracle
    and is counted like login. A live cookie is not a licence to guess."""
    import ratelimit
    ratelimit.reset_all()
    sign_in(client, "two")
    for _ in range(ratelimit.MAX_FAILURES):
        assert client.post("/api/auth/password", json={
            "current": "wrong-wrong", "new": "irrelevant-pw"}).status_code == 401
    r = client.post("/api/auth/password", json={
        "current": "hunter2hunter2", "new": "irrelevant-pw"})
    assert r.status_code == 429 and r.headers["Retry-After"]
    # the security-question route verifies the same password, same bucket
    assert client.post("/api/auth/security-question", json={
        "password": "hunter2hunter2", "sq_id": 2, "answer": "x"}).status_code == 429
    ratelimit.reset_all()


def auth_questions(client) -> dict:
    return {q["id"]: q["text"] for q in client.get("/api/auth/questions").json()["questions"]}


# ---- behind the proxy (2026-08-03) ----

def _fake_request(peer, headers=None, scheme="http"):
    from types import SimpleNamespace

    from starlette.datastructures import Headers
    return SimpleNamespace(client=SimpleNamespace(host=peer) if peer else None,
                           headers=Headers(headers or {}),
                           url=SimpleNamespace(scheme=scheme))


def test_client_ip_trusts_forwarding_only_from_the_proxy():
    import siteconfig
    fwd = {"cf-connecting-ip": "203.0.113.7", "x-forwarded-for": "198.51.100.9, 172.16.0.1"}

    # a direct client speaks only for itself, however loudly it claims otherwise
    assert siteconfig.client_ip(_fake_request("192.0.2.50", fwd)) == "192.0.2.50"
    # from Zoraxy, Cloudflare's visitor header wins; XFF is the fallback
    assert siteconfig.client_ip(_fake_request("10.1.1.4", fwd)) == "203.0.113.7"
    assert siteconfig.client_ip(_fake_request(
        "10.1.1.4", {"x-forwarded-for": "198.51.100.9, 172.16.0.1"})) == "198.51.100.9"
    # nothing forwarded, or junk forwarded: fall back to the peer, never blank
    assert siteconfig.client_ip(_fake_request("10.1.1.4", {})) == "10.1.1.4"
    assert siteconfig.client_ip(_fake_request(
        "10.1.1.4", {"cf-connecting-ip": "not-an-ip"})) == "10.1.1.4"


def test_edge_body_cap_applies_only_to_requests_that_came_through_cloudflare():
    """The 100 MB ceiling belongs to the proxy, not to us: a friend on the
    internet has to hear about it before they upload, a browser on the LAN
    would only be lied to. `CF-Ray` is the evidence, believed from Zoraxy."""
    import siteconfig
    cap = siteconfig.CLOUDFLARE_MAX_BODY_BYTES

    assert siteconfig.edge_max_bytes(_fake_request("10.1.1.4", {"cf-ray": "a25a6f19"})) == cap
    assert siteconfig.edge_max_bytes(
        _fake_request("10.1.1.4", {"cf-connecting-ip": "203.0.113.7"})) == cap
    # Zoraxy alone (proxy off at Cloudflare), and a direct LAN client: no cap
    assert siteconfig.edge_max_bytes(_fake_request("10.1.1.4", {})) == 0
    assert siteconfig.edge_max_bytes(_fake_request("192.0.2.50", {"cf-ray": "a25a6f19"})) == 0


def test_cookie_is_secure_when_the_browser_is_on_https():
    import siteconfig
    https = {"x-forwarded-proto": "https"}
    assert siteconfig.is_secure(_fake_request("10.1.1.4", https)) is True
    assert siteconfig.is_secure(_fake_request("10.1.1.4", {})) is False
    # a direct client cannot talk us into Secure on a plain http connection
    assert siteconfig.is_secure(_fake_request("192.0.2.50", https)) is False
    assert siteconfig.is_secure(_fake_request("192.0.2.50", {}, scheme="https")) is True


def test_one_attacker_cannot_lock_out_the_whole_site(client):
    """The regression this exists for: every public request arrives from
    Zoraxy, so keying the address bucket on the peer gave the entire internet
    ONE counter — five wrong guesses by anybody and nobody can sign in."""
    import ratelimit
    from fastapi.testclient import TestClient

    import main
    ratelimit.reset_all()
    attacker = TestClient(main.app, client=("10.1.1.4", 40000),
                          headers={"cf-connecting-ip": "203.0.113.7"})
    for _ in range(ratelimit.MAX_FAILURES):
        assert attacker.post("/api/auth/login", json={
            "username": "victim-who-does-not-exist", "password": "wrong-wrong"
        }).status_code == 401
    assert attacker.post("/api/auth/login", json={
        "username": "another-name", "password": "wrong-wrong"}).status_code == 429

    victim = TestClient(main.app, client=("10.1.1.4", 40001),
                        headers={"cf-connecting-ip": "198.51.100.9"})
    r = victim.post("/api/auth/login", json={"username": "one", "password": "hunter2hunter2"})
    assert r.status_code == 200, "a stranger's failures locked out an unrelated visitor"
    ratelimit.reset_all()


def test_public_links_use_the_public_address(client, monkeypatch):
    """Invite links and the device-pairing host are handed to someone else, so
    they must be the address the site answers on — not this request's."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://eq2advanced.example.org/")
    sign_in(client, "one")
    assert client.get("/api/groups").json()["invite_base"] == "https://eq2advanced.example.org"

    assert client.post("/api/characters", json={"name": "Linky"}).status_code == 200
    char = next(c for c in client.get("/api/characters").json()["characters"]
                if c["name"] == "Linky")
    payload = client.post("/api/tokens", json={}).json()["pair_payload"]
    assert payload.startswith("eq2advanced://pair?host=https%3A%2F%2Feq2advanced.example.org&token=")
