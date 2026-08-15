"""Account-owned chat alert rules delivered as private Discord DMs.

Discord is a DESTINATION, not an identity provider. People sign in to
EQ2Advanced as they always have, install the Discord application to themselves
(never to a server), and bridge the two with a short-lived code typed into the
bot's private `/link` command. No OAuth access token is stored and no guild is
named anywhere in this module.

Delivery is a transactional outbox. ``chatbus.absorb`` inserts a public chat
row and calls :func:`enqueue_matches` inside the same SQLite transaction; a
rollback therefore removes both. The async app loop later sends pending rows.
Cooldowns are checked at SEND time rather than enqueue time, so a burst queued
before the first HTTP request becomes one DM instead of ten.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

import siteconfig
from db import get_db, rows_to_dicts

CHANNELS = ("any", "general", "lfg", "auction")
COOLDOWNS = (60, 300, 900, 3600)
PAIR_TTL_S = 10 * 60
MAX_RULES = 20
MAX_DMS_PER_DAY = 100
MAX_ATTEMPTS = 5
API = "https://discord.com/api/v10"

log = logging.getLogger("discord-alerts")


def application_id() -> str:
    return os.environ.get("DISCORD_APPLICATION_ID", "").strip()


def public_key() -> str:
    return os.environ.get("DISCORD_PUBLIC_KEY", "").strip()


def bot_token() -> str:
    return os.environ.get("DISCORD_BOT_TOKEN", "").strip()


def configured() -> bool:
    return bool(application_id() and public_key() and bot_token())


def install_url() -> str | None:
    """The user-install link. An override accepts Discord's Developer Portal
    link verbatim; the derived form explicitly chooses USER_INSTALL (1)."""
    explicit = os.environ.get("DISCORD_INSTALL_URL", "").strip()
    if explicit:
        return explicit
    app_id = application_id()
    if not app_id:
        return None
    return ("https://discord.com/oauth2/authorize?client_id="
            f"{quote(app_id)}&integration_type=1&scope=applications.commands")


def bot_profile_url() -> str | None:
    app_id = application_id()
    return f"https://discord.com/users/{quote(app_id)}" if app_id else None


def public_config() -> dict:
    return {"configured": configured(), "install_url": install_url(),
            "bot_profile_url": bot_profile_url()}


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def new_pair_code(conn, user_id: int, now: int | None = None) -> dict:
    if not configured():
        raise RuntimeError("Discord alerts are not configured")
    now = int(time.time()) if now is None else now
    with conn:
        conn.execute("DELETE FROM discord_pair_codes WHERE expires_ts<=?", (now,))
        for _ in range(20):
            code = f"{secrets.randbelow(100_000_000):08d}"
            digest = _code_hash(code)
            if conn.execute("SELECT 1 FROM discord_pair_codes WHERE code_hash=?",
                            (digest,)).fetchone() is None:
                break
        else:  # pragma: no cover - the 100m code space cannot realistically exhaust
            raise RuntimeError("could not mint a pairing code")
        conn.execute("INSERT OR REPLACE INTO discord_pair_codes "
                     "(user_id,code_hash,created_ts,expires_ts) VALUES (?,?,?,?)",
                     (user_id, digest, now, now + PAIR_TTL_S))
    return {"code": code, "expires_ts": now + PAIR_TTL_S}


def link_from_code(conn, code: str, discord_user_id: str, dm_channel_id: str,
                   display_name: str, now: int | None = None) -> tuple[bool, str]:
    """Consume one code and bind its EQ2Advanced account to the signed Discord
    user and BOT_DM that invoked `/link`. Returns (ok, user-facing sentence)."""
    now = int(time.time()) if now is None else now
    digits = "".join(c for c in str(code) if c.isdigit())
    if len(digits) != 8:
        return False, "That pairing code is not valid. Generate a new one on EQ2Advanced."
    row = conn.execute(
        "SELECT p.user_id,p.expires_ts,u.username FROM discord_pair_codes p "
        "JOIN users u ON u.id=p.user_id WHERE p.code_hash=?",
        (_code_hash(digits),)).fetchone()
    if row is None or row["expires_ts"] <= now:
        return False, "That pairing code expired or was already used. Generate a new one on EQ2Advanced."
    other = conn.execute(
        "SELECT user_id FROM discord_links WHERE discord_user_id=?",
        (discord_user_id,)).fetchone()
    if other is not None and other["user_id"] != row["user_id"]:
        return False, "This Discord account is already linked to another EQ2Advanced account."
    display = " ".join((display_name or "Discord user").split())[:80]
    with conn:
        # A re-link is also recovery from a blocked/failed DM: it refreshes the
        # channel and clears both the pause and the last delivery error.
        conn.execute("DELETE FROM discord_links WHERE user_id=?", (row["user_id"],))
        conn.execute(
            "INSERT INTO discord_links (user_id,discord_user_id,dm_channel_id,"
            "display_name,paused,linked_ts) VALUES (?,?,?,?,0,?)",
            (row["user_id"], discord_user_id, dm_channel_id, display, now))
        conn.execute("DELETE FROM discord_pair_codes WHERE user_id=?", (row["user_id"],))
    return True, f"Connected to {row['username']} on EQ2Advanced. Chat alerts will arrive here."


def _public_link(row) -> dict | None:
    if row is None:
        return None
    return {"connected": True, "display_name": row["display_name"],
            "paused": bool(row["paused"]), "linked_ts": row["linked_ts"],
            "last_error": row["last_error"]}


def link_for(conn, user_id: int) -> dict | None:
    return _public_link(conn.execute(
        "SELECT display_name,paused,linked_ts,last_error FROM discord_links "
        "WHERE user_id=?", (user_id,)).fetchone())


def unlink(conn, user_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM discord_pair_codes WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM discord_links WHERE user_id=?", (user_id,))
        conn.execute("UPDATE chat_alert_deliveries SET status='suppressed',"
                     "error='Discord disconnected' WHERE user_id=? AND status='pending'",
                     (user_id,))


def set_paused(conn, user_id: int, paused: bool) -> bool:
    with conn:
        cur = conn.execute("UPDATE discord_links SET paused=?,last_error=NULL,"
                           "last_error_ts=NULL WHERE user_id=?",
                           (int(paused), user_id))
        if paused:
            conn.execute("UPDATE chat_alert_deliveries SET status='suppressed',"
                         "error='Discord alerts paused' WHERE user_id=? AND status='pending'",
                         (user_id,))
    return bool(cur.rowcount)


def rules_for(conn, user_id: int) -> list[dict]:
    rows = rows_to_dicts(conn.execute(
        "SELECT id,name,channel,query,exclude_query,speaker,cooldown_s,enabled,"
        "created_ts,updated_ts,last_sent_ts FROM chat_alert_rules WHERE user_id=? "
        "ORDER BY created_ts,id", (user_id,)))
    for row in rows:
        row["enabled"] = bool(row["enabled"])
    return rows


def alerts_for(conn, user_id: int) -> dict:
    return public_config() | {"discord": link_for(conn, user_id),
                              "rules": rules_for(conn, user_id)}


def _visible_text(message: dict) -> str:
    return "".join(str(part.get("s") or "") for part in message.get("parts", ()))


def _matches(rule, message: dict) -> bool:
    if rule["channel"] != "any" and rule["channel"] != message["ch"]:
        return False
    if rule["speaker"] and rule["speaker"].casefold() != message["who"].casefold():
        return False
    text = _visible_text(message).casefold()
    if rule["query"].casefold() not in text:
        return False
    return not (rule["exclude_query"]
                and rule["exclude_query"].casefold() in text)


def enqueue_matches(conn, messages: list[dict], now: int | None = None) -> int:
    """Insert one delivery per matching user/message. Called inside the chat
    insert transaction; never commits on its own."""
    if not messages:
        return 0
    rules = conn.execute(
        "SELECT r.* FROM chat_alert_rules r JOIN discord_links d ON d.user_id=r.user_id "
        "WHERE r.enabled=1 AND d.paused=0 ORDER BY r.user_id,r.id").fetchall()
    if not rules:
        return 0
    now = int(time.time()) if now is None else now
    made = 0
    for message in messages:
        by_user: dict[int, list[int]] = {}
        for rule in rules:
            if _matches(rule, message):
                by_user.setdefault(rule["user_id"], []).append(rule["id"])
        for user_id, rule_ids in by_user.items():
            cur = conn.execute(
                "INSERT OR IGNORE INTO chat_alert_deliveries "
                "(user_id,message_id,rule_ids_json,status,available_ts,created_ts) "
                "VALUES (?,?,?,'pending',?,?)",
                (user_id, message["id"], json.dumps(rule_ids), now, now))
            made += cur.rowcount
    return made


def _escape_discord(text: str) -> str:
    # Mentions are disabled in the payload too. Escaping markdown keeps a public
    # chat line from drawing a fake heading, spoiler or code block in the DM.
    for char in ("\\", "`", "*", "_", "~", "|", ">"):
        text = text.replace(char, "\\" + char)
    return text


def _payload(message: dict, rule_names: list[str]) -> dict:
    labels = {"general": "General", "lfg": "LFG", "auction": "Auction"}
    text = _escape_discord(_visible_text(message))[:3500]
    matched = ", ".join(rule_names)[:500]
    return {
        "embeds": [{
            "title": f"{labels.get(message['ch'], message['ch'])} · {message['who']}",
            "description": f"“{text}”",
            "url": siteconfig.public_base_url().rstrip("/") + "/chat",
            "timestamp": datetime.fromtimestamp(message["ts"], UTC).isoformat(),
            "footer": {"text": f"Matched: {matched}"},
        }],
        "allowed_mentions": {"parse": []},
    }


def _retry(conn, delivery_id: int, attempts: int, now: int, error: str,
           retry_after: int | None = None) -> None:
    if attempts >= MAX_ATTEMPTS:
        conn.execute("UPDATE chat_alert_deliveries SET status='failed',attempts=?,"
                     "error=? WHERE id=?", (attempts, error[:300], delivery_id))
        return
    delay = retry_after if retry_after is not None else min(15 * (2 ** attempts), 900)
    conn.execute("UPDATE chat_alert_deliveries SET attempts=?,available_ts=?,error=? "
                 "WHERE id=?", (attempts, now + max(1, delay), error[:300], delivery_id))


def deliver_pending(conn=None, client: httpx.Client | None = None,
                    now: int | None = None, limit: int = 20) -> dict:
    """Send a bounded queue pass. Network failures retry with backoff; a blocked
    bot pauses that user's link so the loop does not hammer a dead DM."""
    token = bot_token()
    if not token:
        return {"sent": 0, "suppressed": 0, "failed": 0, "configured": False}
    conn = conn or get_db()
    now = int(time.time()) if now is None else now
    rows = conn.execute(
        "SELECT q.*,d.dm_channel_id FROM chat_alert_deliveries q "
        "JOIN discord_links d ON d.user_id=q.user_id "
        "WHERE q.status='pending' AND q.available_ts<=? AND d.paused=0 "
        "ORDER BY q.id LIMIT ?", (now, max(1, min(limit, 100)))).fetchall()
    own_client = client is None
    client = client or httpx.Client(timeout=10)
    counts = {"sent": 0, "suppressed": 0, "failed": 0, "configured": True}
    try:
        for delivery in rows:
            try:
                ids = [int(x) for x in json.loads(delivery["rule_ids_json"])]
            except (TypeError, ValueError, json.JSONDecodeError):
                ids = []
            if ids:
                marks = ",".join("?" * len(ids))
                rules = conn.execute(
                    f"SELECT id,name,cooldown_s,last_sent_ts FROM chat_alert_rules "
                    f"WHERE user_id=? AND enabled=1 AND id IN ({marks}) ORDER BY id",
                    (delivery["user_id"], *ids)).fetchall()
            else:
                rules = []
            eligible = [r for r in rules if r["last_sent_ts"] is None
                        or now - r["last_sent_ts"] >= r["cooldown_s"]]
            if not eligible:
                with conn:
                    conn.execute("UPDATE chat_alert_deliveries SET status='suppressed',"
                                 "error='rule disabled or cooling down' WHERE id=?",
                                 (delivery["id"],))
                counts["suppressed"] += 1
                continue
            sent_today = conn.execute(
                "SELECT COUNT(*) FROM chat_alert_deliveries WHERE user_id=? "
                "AND status='sent' AND sent_ts>=?",
                (delivery["user_id"], now - 86400)).fetchone()[0]
            if sent_today >= MAX_DMS_PER_DAY:
                with conn:
                    conn.execute("UPDATE chat_alert_deliveries SET status='suppressed',"
                                 "error='daily Discord alert limit reached' WHERE id=?",
                                 (delivery["id"],))
                counts["suppressed"] += 1
                continue
            row = conn.execute(
                "SELECT id,ts,ch,who,text FROM chat_messages WHERE id=?",
                (delivery["message_id"],)).fetchone()
            if row is None:
                with conn:
                    conn.execute("UPDATE chat_alert_deliveries SET status='failed',"
                                 "error='chat message missing' WHERE id=?", (delivery["id"],))
                counts["failed"] += 1
                continue
            # The server's existing splitter is the one authority on what a
            # stored EQ2 link looks like to a reader. Importing here (after
            # chatbus startup) avoids a module cycle with enqueue_matches.
            from pipeline.chatbus import _wire
            message = _wire(row)
            attempts = delivery["attempts"] + 1
            try:
                response = client.post(
                    f"{API}/channels/{delivery['dm_channel_id']}/messages",
                    headers={"Authorization": f"Bot {token}"},
                    json=_payload(message, [r["name"] for r in eligible]))
            except httpx.HTTPError as exc:
                with conn:
                    _retry(conn, delivery["id"], attempts, now,
                           f"Discord network error: {type(exc).__name__}")
                counts["failed"] += 1
                continue
            if 200 <= response.status_code < 300:
                with conn:
                    conn.execute("UPDATE chat_alert_deliveries SET status='sent',"
                                 "attempts=?,sent_ts=?,error=NULL WHERE id=?",
                                 (attempts, now, delivery["id"]))
                    conn.executemany(
                        "UPDATE chat_alert_rules SET last_sent_ts=? WHERE id=? AND user_id=?",
                        [(now, r["id"], delivery["user_id"]) for r in eligible])
                    conn.execute("UPDATE discord_links SET last_error=NULL,last_error_ts=NULL "
                                 "WHERE user_id=?", (delivery["user_id"],))
                counts["sent"] += 1
                continue
            error = f"Discord HTTP {response.status_code}"
            if response.status_code == 429:
                try:
                    retry_s = max(1, int(float(response.json().get("retry_after", 1)) + 0.999))
                except (TypeError, ValueError, json.JSONDecodeError):
                    retry_s = 5
                with conn:
                    _retry(conn, delivery["id"], attempts, now, error, retry_s)
            elif response.status_code in (401, 403, 404):
                with conn:
                    conn.execute("UPDATE chat_alert_deliveries SET status='failed',"
                                 "attempts=?,error=? WHERE id=?",
                                 (attempts, error, delivery["id"]))
                    conn.execute("UPDATE discord_links SET paused=1,last_error=?,"
                                 "last_error_ts=? WHERE user_id=?",
                                 ("Discord could not deliver this DM. Reconnect the app.",
                                  now, delivery["user_id"]))
            else:
                with conn:
                    _retry(conn, delivery["id"], attempts, now, error)
            counts["failed"] += 1
    finally:
        if own_client:
            client.close()
    return counts


def send_test(conn, user_id: int, client: httpx.Client | None = None) -> None:
    token = bot_token()
    link = conn.execute("SELECT dm_channel_id FROM discord_links WHERE user_id=?",
                        (user_id,)).fetchone()
    if not token or link is None:
        raise RuntimeError("Discord is not connected")
    own_client = client is None
    client = client or httpx.Client(timeout=10)
    try:
        response = client.post(
            f"{API}/channels/{link['dm_channel_id']}/messages",
            headers={"Authorization": f"Bot {token}"},
            json={"content": "EQ2Advanced chat alerts are connected. You’re ready.",
                  "allowed_mentions": {"parse": []}})
    finally:
        if own_client:
            client.close()
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Discord could not deliver the test (HTTP {response.status_code})")
