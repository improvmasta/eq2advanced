"""Device tokens: one per (character, device), shown once at mint, revocable.
The QR payload is what the ACT plugin's pairing flow scans; pasting the token
works the same.

Scope is ingest-only unless the token is minted with `can_share`, which lets the
plugin also set who sees the raids it sends (`ingest_api` -> "Sharing"). It is
fixed at mint and there is no route that raises it: a token sits in a config
file on a gaming PC, so the decision has to be made by someone signed in to the
site. Widening means minting a new one and revoking the old."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import auth
import siteconfig
from db import get_db, rows_to_dicts
from security import owned_character, require_user

router = APIRouter(tags=["tokens"])


class TokenCreate(BaseModel):
    label: str | None = None
    can_share: bool = False


def _pair_payload(token: str) -> str:
    """The `host` the plugin will POST to for the life of the token, so it is
    the public address (`siteconfig`) rather than `request.base_url` — that is
    the internal host:port the proxy reached us on, which is right only by
    accident and stops being right the moment the site moves boxes."""
    host = siteconfig.public_base_url()
    return f"eq2advanced://pair?host={quote(host, safe='')}&token={token}"


@router.get("/characters/{character_id}/tokens")
def list_tokens(character_id: int, user=Depends(require_user)):
    conn = get_db()
    char = owned_character(conn, user, character_id)
    rows = conn.execute(
        "SELECT id, label, created_ts, last_seen_ts, revoked_ts, can_share "
        "FROM device_tokens WHERE character_id=? ORDER BY created_ts DESC",
        (char["id"],)).fetchall()
    return {"tokens": rows_to_dicts(rows)}


@router.post("/characters/{character_id}/tokens")
def mint_token(character_id: int, body: TokenCreate, user=Depends(require_user)):
    conn = get_db()
    char = owned_character(conn, user, character_id)
    label = (body.label or "").strip() or None
    with conn:
        token_id, token = auth.mint_device_token(conn, char["id"], label, body.can_share)
    return {"id": token_id, "token": token, "can_share": body.can_share,
            "pair_payload": _pair_payload(token)}


@router.post("/tokens/{token_id}/revoke")
def revoke_token(token_id: int, user=Depends(require_user)):
    conn = get_db()
    row = conn.execute("SELECT character_id FROM device_tokens WHERE id=?",
                       (token_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such token")
    owned_character(conn, user, row["character_id"])
    with conn:
        auth.revoke_device_token(conn, token_id)
    return {"ok": True}
