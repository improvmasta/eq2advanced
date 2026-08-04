"""Device tokens: one per device, ingest-only scope, shown once at mint,
revocable. The QR payload is what the ACT plugin's pairing flow scans; pasting
the token works the same.

**A token belongs to an ACCOUNT** (v13), not a character. Asking "which
character?" at pairing time was a question with no good answer — people play
alts, and the one they'll log in tonight isn't decided yet. The character comes
off the log instead (`ingest_api`), created on first sight, so one pairing covers
every character forever.

The scope really is ingest and nothing else — a token sends logs, and who can
see the resulting raids is decided on the site by someone signed in.
"""

import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import auth
import siteconfig
from db import get_db, rows_to_dicts
from security import require_user

router = APIRouter(tags=["tokens"])


class TokenCreate(BaseModel):
    label: str | None = None


def _pair_payload(token: str) -> str:
    """The `host` the plugin will POST to for the life of the token, so it is
    the public address (`siteconfig`) rather than `request.base_url` — that is
    the internal host:port the proxy reached us on, which is right only by
    accident and stops being right the moment the site moves boxes."""
    host = siteconfig.public_base_url()
    return f"eq2advanced://pair?host={quote(host, safe='')}&token={token}"


@router.get("/tokens")
def list_tokens(user=Depends(require_user)):
    """The account's keys, newest first. A live key's plaintext rides along —
    the Import page shows it behind a Show button, Sonarr-style; the owner is
    the only one who can reach this route. A revoked key's never does, and a
    pre-v15 key has none to give (token_plain NULL)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT t.id, t.label, t.created_ts, t.last_seen_ts, t.revoked_ts, "
        "t.token_plain, c.name AS character_name "
        "FROM device_tokens t LEFT JOIN characters c ON c.id = t.character_id "
        "WHERE t.user_id=? ORDER BY t.created_ts DESC", (user["id"],)).fetchall()
    out = []
    for r in rows_to_dicts(rows):
        r["token"] = r.pop("token_plain") if not r["revoked_ts"] else None
        out.append(r)
    return {"tokens": out}


@router.post("/tokens")
def mint_token(body: TokenCreate, user=Depends(require_user)):
    conn = get_db()
    label = (body.label or "").strip() or None
    with conn:
        token_id, token = auth.mint_device_token(conn, user["id"], label)
    return {"id": token_id, "token": token, "pair_payload": _pair_payload(token)}


@router.post("/tokens/refresh")
def refresh_token(body: TokenCreate, user=Depends(require_user)):
    """Sonarr's refresh: one API key per account — every live key is revoked
    and one new one takes its place. A device with the old key stops uploading
    until the new one is pasted in, which is the point of refreshing."""
    conn = get_db()
    with conn:
        conn.execute("UPDATE device_tokens SET revoked_ts=? "
                     "WHERE user_id=? AND revoked_ts IS NULL",
                     (int(time.time()), user["id"]))
        token_id, token = auth.mint_device_token(
            conn, user["id"], (body.label or "").strip() or None)
    return {"id": token_id, "token": token, "pair_payload": _pair_payload(token)}


@router.post("/tokens/{token_id}/revoke")
def revoke_token(token_id: int, user=Depends(require_user)):
    conn = get_db()
    row = conn.execute("SELECT user_id FROM device_tokens WHERE id=?",
                       (token_id,)).fetchone()
    if row is None or row["user_id"] != user["id"]:
        raise HTTPException(404, "no such token")
    with conn:
        auth.revoke_device_token(conn, token_id)
    return {"ok": True}
