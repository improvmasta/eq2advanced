"""Private Discord chat alerts: signed pairing, owned rules and the outbox."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

import db as dbmod
import discord_alerts
from pipeline import chatbus


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-discord")
    mp = pytest.MonkeyPatch()
    for key in ("DISCORD_APPLICATION_ID", "DISCORD_PUBLIC_KEY", "DISCORD_BOT_TOKEN",
                "DISCORD_INSTALL_URL"):
        mp.delenv(key, raising=False)
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    dbmod._local.conn = None
    from main import app
    # Start with Discord unconfigured so the real background delivery loop is
    # absent. The routes read env dynamically; fake credentials can be enabled
    # safely after lifespan startup.
    with TestClient(app) as client:
        signing = SigningKey.generate()
        mp.setenv("DISCORD_APPLICATION_ID", "123456789")
        mp.setenv("DISCORD_PUBLIC_KEY", signing.verify_key.encode().hex())
        mp.setenv("DISCORD_BOT_TOKEN", "test-bot-token")
        yield client, signing
    dbmod._local.conn = None
    mp.undo()


@pytest.fixture(autouse=True)
def clean(env):
    conn = dbmod.get_db()
    chatbus.reset()
    with conn:
        for table in ("chat_alert_deliveries", "chat_alert_rules", "discord_pair_codes",
                      "discord_links", "chat_messages", "auth_sessions", "users"):
            conn.execute(f"DELETE FROM {table}")
    env[0].cookies.clear()
    yield
    chatbus.reset()


def register(client, name="raider"):
    response = client.post("/api/auth/register", json={
        "username": name, "password": "hunter2hunter2",
        "sq_id": 1, "answer": "my pet",
    })
    assert response.status_code == 200, response.text
    return response.json()["user"]


def interaction(client, signing, payload, *, valid=True):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    stamp = str(int(time.time()))
    signature = signing.sign(stamp.encode() + raw).signature.hex()
    if not valid:
        signature = "00" * 64
    return client.post("/api/discord/interactions", content=raw, headers={
        "content-type": "application/json",
        "x-signature-ed25519": signature,
        "x-signature-timestamp": stamp,
    })


def command(name, discord_id="9001", channel_id="dm-22", options=None, context=1):
    return {
        "type": 2, "context": context, "channel_id": channel_id,
        "user": {"id": discord_id, "username": "raidfriend", "global_name": "Raid Friend"},
        "data": {"name": name, "options": options or []},
    }


def test_interactions_are_signed_and_ping_is_supported(env):
    client, signing = env
    unsigned = client.post("/api/discord/interactions", json={"type": 1})
    assert unsigned.status_code == 401
    assert interaction(client, signing, {"type": 1}, valid=False).status_code == 401
    assert interaction(client, signing, {"type": 1}).json() == {"type": 1}


def test_user_install_dm_pairing_never_needs_a_server(env):
    client, signing = env
    register(client)
    before = client.get("/api/chat/alerts").json()
    assert before["configured"] is True
    assert "integration_type=1" in before["install_url"]
    assert before["bot_profile_url"] == "https://discord.com/users/123456789"
    pair = client.post("/api/chat/alerts/pairing-code").json()
    payload = command("link", options=[{"name": "code", "type": 3,
                                         "value": pair["code"]}])
    response = interaction(client, signing, payload)
    assert response.status_code == 200
    assert "Connected to raider" in response.json()["data"]["content"]
    linked = client.get("/api/chat/alerts").json()["discord"]
    assert linked == {"connected": True, "display_name": "Raid Friend", "paused": False,
                      "linked_ts": linked["linked_ts"], "last_error": None}
    # Codes are single-use, and commands anywhere except the app's own DM are
    # refused rather than turning this into a guild/server integration.
    again = interaction(client, signing, payload).json()["data"]["content"]
    assert "expired or was already used" in again
    server = interaction(client, signing, command("status", context=0)).json()
    assert "private DM" in server["data"]["content"]


def test_rules_are_owned_and_validated(env):
    client, _ = env
    register(client)
    assert client.post("/api/chat/alerts/rules", json={"query": "x"}).status_code == 422
    made = client.post("/api/chat/alerts/rules", json={
        "query": "Krono", "channel": "auction", "speaker": "Trader",
        "exclude_query": "sold", "cooldown_s": 900,
    })
    assert made.status_code == 200, made.text
    rule = made.json()["rule"]
    assert rule["name"] == "Krono" and rule["enabled"] is True
    assert client.patch(f"/api/chat/alerts/rules/{rule['id']}",
                        json={"enabled": False}).json()["rule"]["enabled"] is False
    assert client.delete(f"/api/chat/alerts/rules/{rule['id']}").status_code == 200
    assert client.get("/api/chat/alerts").json()["rules"] == []


class FakeResponse:
    status_code = 200

    def json(self):
        return {"id": "discord-message"}


class FakeDiscord:
    def __init__(self):
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse()


def chat_line(text, now):
    return (f'({now})[Sat Aug  1 20:30:42 2026] '
            f'\\aPC -1 Trader:Trader\\/a tells Auction (10), "{text}"')


def test_matching_is_transactional_bundled_and_cooled_down(env):
    client, _ = env
    user = register(client)
    conn = dbmod.get_db()
    now = int(time.time())
    with conn:
        conn.execute("INSERT INTO discord_links (user_id,discord_user_id,dm_channel_id,"
                     "display_name,linked_ts) VALUES (?,?,?,?,?)",
                     (user["id"], "9001", "dm-22", "Raid Friend", now))
    # Two overlapping rules still make one DM for one public line.
    for query in ("krono", "WTB"):
        assert client.post("/api/chat/alerts/rules", json={
            "query": query, "channel": "auction", "cooldown_s": 300,
        }).status_code == 200
    chatbus.absorb(conn, [chat_line("WTB one Krono", now)], "Logger", "live", now)
    conn.commit()
    queued = conn.execute("SELECT * FROM chat_alert_deliveries").fetchall()
    assert len(queued) == 1 and json.loads(queued[0]["rule_ids_json"])

    fake = FakeDiscord()
    report = discord_alerts.deliver_pending(conn, fake, now=now)
    assert report["sent"] == 1 and len(fake.posts) == 1
    payload = fake.posts[0][1]["json"]
    assert payload["allowed_mentions"] == {"parse": []}
    assert "krono" in payload["embeds"][0]["footer"]["text"].lower()
    assert "WTB" in payload["embeds"][0]["description"]

    # A different matching message inside both rules' cooldown is recorded but
    # suppressed by the worker, not sent as a second burst DM.
    later = now + 30
    chatbus.absorb(conn, [chat_line("WTB two Krono", later)], "Logger", "live", later)
    conn.commit()
    report = discord_alerts.deliver_pending(conn, fake, now=later)
    assert report["suppressed"] == 1 and len(fake.posts) == 1


def test_pause_and_unlink_are_available_from_the_private_dm(env):
    client, signing = env
    user = register(client)
    conn = dbmod.get_db()
    with conn:
        conn.execute("INSERT INTO discord_links (user_id,discord_user_id,dm_channel_id,"
                     "display_name,linked_ts) VALUES (?,?,?,?,?)",
                     (user["id"], "9001", "dm-22", "Raid Friend", int(time.time())))
    assert "paused" in interaction(client, signing, command("pause")).json()["data"]["content"]
    assert conn.execute("SELECT paused FROM discord_links WHERE user_id=?",
                        (user["id"],)).fetchone()[0] == 1
    assert "resumed" in interaction(client, signing, command("resume")).json()["data"]["content"]
    assert "disconnected" in interaction(client, signing, command("unlink")).json()["data"]["content"]
    assert conn.execute("SELECT 1 FROM discord_links").fetchone() is None
