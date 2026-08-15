"""Discord user-app pairing and account-owned chat alert rules.

The website routes use the ordinary EQ2Advanced session cookie. The one public
route is Discord's interaction callback; it accepts no cookie and trusts only
an Ed25519 signature made by the configured Discord application.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from pydantic import BaseModel

import discord_alerts
import ratelimit
from db import get_db
from security import require_user

router = APIRouter(tags=["discord-alerts"])


class RuleCreate(BaseModel):
    name: str | None = None
    channel: str = "any"
    query: str
    exclude_query: str | None = None
    speaker: str | None = None
    cooldown_s: int = 300
    enabled: bool = True


class RulePatch(BaseModel):
    name: str | None = None
    channel: str | None = None
    query: str | None = None
    exclude_query: str | None = None
    speaker: str | None = None
    cooldown_s: int | None = None
    enabled: bool | None = None


class PauseIn(BaseModel):
    paused: bool


def _clean_rule(raw: dict, current: dict | None = None) -> dict:
    merged = dict(current or {}) | raw
    query = " ".join(str(merged.get("query") or "").split())
    if len(query) < 2 or len(query) > 100:
        raise HTTPException(422, "match phrase is 2-100 characters")
    name = " ".join(str(merged.get("name") or query).split())[:60]
    channel = str(merged.get("channel") or "any").lower()
    if channel not in discord_alerts.CHANNELS:
        raise HTTPException(422, "channel is any, general, lfg or auction")
    cooldown = int(merged.get("cooldown_s") or 300)
    if cooldown not in discord_alerts.COOLDOWNS:
        raise HTTPException(422, "unsupported cooldown")
    exclude = " ".join(str(merged.get("exclude_query") or "").split())[:100] or None
    speaker = " ".join(str(merged.get("speaker") or "").split())[:40] or None
    return {"name": name, "channel": channel, "query": query,
            "exclude_query": exclude, "speaker": speaker,
            "cooldown_s": cooldown, "enabled": int(bool(merged.get("enabled", True)))}


@router.get("/chat/alerts")
def get_alerts(user=Depends(require_user)):
    return discord_alerts.alerts_for(get_db(), user["id"])


@router.post("/chat/alerts/pairing-code")
def pairing_code(user=Depends(require_user)):
    try:
        pair = discord_alerts.new_pair_code(get_db(), user["id"])
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return pair | {"install_url": discord_alerts.install_url()}


@router.patch("/chat/alerts/discord")
def pause_discord(body: PauseIn, user=Depends(require_user)):
    if not discord_alerts.set_paused(get_db(), user["id"], body.paused):
        raise HTTPException(404, "Discord is not connected")
    return {"discord": discord_alerts.link_for(get_db(), user["id"])}


@router.delete("/chat/alerts/discord")
def disconnect_discord(user=Depends(require_user)):
    discord_alerts.unlink(get_db(), user["id"])
    return {"disconnected": True}


@router.post("/chat/alerts/discord/test")
def test_discord(user=Depends(require_user)):
    try:
        discord_alerts.send_test(get_db(), user["id"])
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"sent": True}


@router.post("/chat/alerts/rules")
def create_rule(body: RuleCreate, user=Depends(require_user)):
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM chat_alert_rules WHERE user_id=?",
                         (user["id"],)).fetchone()[0]
    if count >= discord_alerts.MAX_RULES:
        raise HTTPException(409, f"up to {discord_alerts.MAX_RULES} alert rules")
    rule = _clean_rule(body.model_dump())
    now = int(time.time())
    with conn:
        cur = conn.execute(
            "INSERT INTO chat_alert_rules (user_id,name,channel,query,exclude_query,"
            "speaker,cooldown_s,enabled,created_ts,updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user["id"], rule["name"], rule["channel"], rule["query"],
             rule["exclude_query"], rule["speaker"], rule["cooldown_s"],
             rule["enabled"], now, now))
    return {"rule": next(r for r in discord_alerts.rules_for(conn, user["id"])
                         if r["id"] == cur.lastrowid)}


def _owned_rule(conn, user_id: int, rule_id: int) -> dict:
    row = conn.execute("SELECT * FROM chat_alert_rules WHERE id=? AND user_id=?",
                       (rule_id, user_id)).fetchone()
    if row is None:
        raise HTTPException(404, "no such alert rule")
    return dict(row)


@router.patch("/chat/alerts/rules/{rule_id}")
def update_rule(rule_id: int, body: RulePatch, user=Depends(require_user)):
    conn = get_db()
    current = _owned_rule(conn, user["id"], rule_id)
    patch = body.model_dump(exclude_unset=True)
    rule = _clean_rule(patch, current)
    with conn:
        conn.execute(
            "UPDATE chat_alert_rules SET name=?,channel=?,query=?,exclude_query=?,"
            "speaker=?,cooldown_s=?,enabled=?,updated_ts=? WHERE id=? AND user_id=?",
            (rule["name"], rule["channel"], rule["query"], rule["exclude_query"],
             rule["speaker"], rule["cooldown_s"], rule["enabled"], int(time.time()),
             rule_id, user["id"]))
    return {"rule": next(r for r in discord_alerts.rules_for(conn, user["id"])
                         if r["id"] == rule_id)}


@router.delete("/chat/alerts/rules/{rule_id}")
def delete_rule(rule_id: int, user=Depends(require_user)):
    conn = get_db()
    _owned_rule(conn, user["id"], rule_id)
    with conn:
        conn.execute("DELETE FROM chat_alert_rules WHERE id=? AND user_id=?",
                     (rule_id, user["id"]))
    return {"deleted": True, "id": rule_id}


def _interaction_reply(content: str) -> dict:
    return {"type": 4, "data": {"content": content,
                                  "allowed_mentions": {"parse": []}}}


def _verify_interaction(public_key: str, signature: str, stamp: str,
                        body: bytes) -> None:
    try:
        sent_at = int(stamp)
        if abs(time.time() - sent_at) > 300:
            raise ValueError("stale")
        VerifyKey(bytes.fromhex(public_key)).verify(
            stamp.encode("ascii") + body, bytes.fromhex(signature))
    except (ValueError, BadSignatureError) as exc:
        raise HTTPException(401, "invalid Discord signature") from exc


def _discord_user(payload: dict) -> dict:
    return payload.get("user") or (payload.get("member") or {}).get("user") or {}


@router.post("/discord/interactions")
async def discord_interaction(request: Request):
    """Discord's public callback. `/link` is accepted only in BOT_DM (context
    1), which is what prevents this feature from quietly becoming server-bound."""
    key = discord_alerts.public_key()
    if not key:
        raise HTTPException(503, "Discord alerts are not configured")
    body = await request.body()
    _verify_interaction(
        key, request.headers.get("x-signature-ed25519", ""),
        request.headers.get("x-signature-timestamp", ""), body)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "invalid JSON") from exc
    if payload.get("type") == 1:  # Discord's endpoint verification PING
        return {"type": 1}
    if payload.get("type") != 2:
        return _interaction_reply("That interaction is not supported.")
    data = payload.get("data") or {}
    command = str(data.get("name") or "").lower()
    user = _discord_user(payload)
    discord_id = str(user.get("id") or "")
    if not discord_id:
        return _interaction_reply("Discord did not identify this user.")
    if payload.get("context") != 1:
        return _interaction_reply("Use this command in your private DM with EQ2Advanced.")
    conn = get_db()
    if command == "link":
        wait = ratelimit.retry_after("discord_pair", discord_id)
        if wait:
            return _interaction_reply("Too many pairing attempts. Wait a few minutes and try again.")
        options = {str(o.get("name")): o.get("value") for o in data.get("options") or []}
        display = user.get("global_name") or user.get("username") or "Discord user"
        ok, message = discord_alerts.link_from_code(
            conn, str(options.get("code") or ""), discord_id,
            str(payload.get("channel_id") or ""), str(display))
        if ok:
            ratelimit.clear("discord_pair", discord_id)
        else:
            ratelimit.fail("discord_pair", discord_id)
        return _interaction_reply(message)
    link = conn.execute("SELECT user_id,paused FROM discord_links WHERE discord_user_id=?",
                        (discord_id,)).fetchone()
    if command == "unlink":
        if link is not None:
            discord_alerts.unlink(conn, link["user_id"])
        return _interaction_reply("Discord alerts disconnected from EQ2Advanced.")
    if link is None:
        return _interaction_reply("This Discord account is not linked. Start from Chat alerts on EQ2Advanced.")
    if command in ("pause", "resume"):
        discord_alerts.set_paused(conn, link["user_id"], command == "pause")
        return _interaction_reply(
            "Chat alerts paused." if command == "pause" else "Chat alerts resumed.")
    if command == "status":
        count = conn.execute("SELECT COUNT(*) FROM chat_alert_rules WHERE user_id=? "
                             "AND enabled=1", (link["user_id"],)).fetchone()[0]
        state = "paused" if link["paused"] else "on"
        return _interaction_reply(f"EQ2Advanced chat alerts are {state} with {count} active rule(s).")
    return _interaction_reply("Unknown command.")
