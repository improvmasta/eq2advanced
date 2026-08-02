"""Account + credential primitives (stdlib only).

Two credential kinds, both stored hashed:
  * auth session — browser cookie minted at login, rows in `auth_sessions`.
  * device token — per-(character, device) ingest credential in `device_tokens`,
    minted on the Characters page, shown once, revocable. The ACT plugin sends it
    as `Authorization: Bearer <token>` (ingest itself lands in phase 3).

Open registration; the FIRST account becomes admin (bootstrap), everyone after
is a plain user.
"""

import hashlib
import hmac
import secrets
import time

ITERATIONS = 200_000
SESSION_DAYS = 30
COOKIE = "eq2_sess"


def _pw_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- users ----

def create_user(conn, email: str, password: str) -> int:
    salt = secrets.token_bytes(16)
    first = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
    return conn.execute(
        "INSERT INTO users (email, pw_hash, salt, role, created_ts) VALUES (?,?,?,?,?)",
        (email, _pw_hash(password, salt), salt, "admin" if first else "user",
         int(time.time())),
    ).lastrowid


def verify_password(conn, email: str, password: str):
    """Return the user row on success, None otherwise (constant-time compare)."""
    row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if row is None:
        # burn a hash anyway so a missing email times like a wrong password
        _pw_hash(password, b"\x00" * 16)
        return None
    return row if hmac.compare_digest(_pw_hash(password, row["salt"]), row["pw_hash"]) else None


def set_password(conn, user_id: int, password: str) -> None:
    salt = secrets.token_bytes(16)
    conn.execute("UPDATE users SET pw_hash=?, salt=? WHERE id=?",
                 (_pw_hash(password, salt), salt, user_id))


# ---- auth sessions (browser cookie) ----

def create_session(conn, user_id: int, days: int = SESSION_DAYS) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, created_ts, expires_ts) VALUES (?,?,?,?)",
        (_sha(token), user_id, now, now + days * 86400))
    conn.execute("DELETE FROM auth_sessions WHERE expires_ts < ?", (now,))
    return token


def session_user(conn, token: str | None):
    """User row for a live session token, else None."""
    if not token:
        return None
    return conn.execute(
        "SELECT u.* FROM auth_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash=? AND s.expires_ts > ?",
        (_sha(token), int(time.time()))).fetchone()


def delete_session(conn, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_sha(token),))


# ---- device tokens (per character+device; ingest scope) ----

def mint_device_token(conn, character_id: int, label: str | None) -> tuple[int, str]:
    """Create a token for one (character, device). Returns (row id, plaintext) —
    the plaintext is shown once and never stored."""
    token = secrets.token_urlsafe(32)
    row_id = conn.execute(
        "INSERT INTO device_tokens (character_id, token_hash, label, created_ts) VALUES (?,?,?,?)",
        (character_id, _sha(token), label, int(time.time()))).lastrowid
    return row_id, token


def device_token_character(conn, token: str | None):
    """Character row for a live (un-revoked) device token, else None. Touches
    last_seen_ts — the Characters page's 'uploader online' badge reads it."""
    if not token:
        return None
    row = conn.execute(
        "SELECT c.*, t.id AS token_id FROM device_tokens t "
        "JOIN characters c ON c.id = t.character_id "
        "WHERE t.token_hash=? AND t.revoked_ts IS NULL",
        (_sha(token),)).fetchone()
    if row is not None:
        conn.execute("UPDATE device_tokens SET last_seen_ts=? WHERE id=?",
                     (int(time.time()), row["token_id"]))
    return row


def revoke_device_token(conn, token_id: int) -> None:
    conn.execute("UPDATE device_tokens SET revoked_ts=? WHERE id=? AND revoked_ts IS NULL",
                 (int(time.time()), token_id))
