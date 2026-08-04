"""Account + credential primitives (stdlib only).

Three credential kinds, all stored hashed:
  * password — PBKDF2, per-user salt. Login is `username` + password; there is no
    email anywhere in the system (phase 12 decision).
  * security question — the ONLY self-service password recovery. One of
    `RESET_QUESTIONS`, chosen at sign-up; the answer is normalized (see
    `normalize_answer`) then hashed exactly like a password.
  * auth session — browser cookie minted at login, rows in `auth_sessions`.
  * device token — per-(character, device) ingest credential in `device_tokens`,
    minted on the Characters page, shown once, revocable. The ACT plugin sends it
    as `Authorization: Bearer <token>`.

Open registration (gated by the `registration_open` setting); the FIRST account
becomes admin (bootstrap), everyone after is a plain user. Admin is an
OPERATIONAL role only — it grants no access to anyone's parse data, see
`security.py`.
"""

import hashlib
import hmac
import re
import secrets
import time

ITERATIONS = 200_000
SESSION_DAYS = 30
COOKIE = "eq2_sess"

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
RESERVED_USERNAMES = {"admin", "root", "system", "api", "support", "eq2advanced"}

# Fixed ids — never renumber, a user's `sq_id` points in here.
RESET_QUESTIONS = [
    (1, "What was the name of your first pet?"),
    (2, "What street did you grow up on?"),
    (3, "What was the name of your first EverQuest character?"),
    (4, "What was your first video game console?"),
    (5, "What is your oldest cousin's first name?"),
    (6, "What was the make of your first car?"),
]
QUESTION_TEXT = dict(RESET_QUESTIONS)


def _pw_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_answer(answer: str) -> str:
    """Security-question answers are compared case- and whitespace-insensitively —
    'Mr. Fluffy ' and 'mr.  fluffy' are the same answer."""
    return re.sub(r"\s+", " ", (answer or "").strip()).casefold()


# ---- users ----

def create_user(conn, username: str, password: str,
                sq_id: int | None = None, answer: str | None = None) -> int:
    salt = secrets.token_bytes(16)
    first = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
    user_id = conn.execute(
        "INSERT INTO users (username, pw_hash, salt, role, created_ts) VALUES (?,?,?,?,?)",
        (username, _pw_hash(password, salt), salt, "admin" if first else "user",
         int(time.time())),
    ).lastrowid
    if sq_id and answer:
        set_security_question(conn, user_id, sq_id, answer)
    return user_id


def verify_password(conn, username: str, password: str):
    """Return the user row on success, None otherwise (constant-time compare).
    A disabled account never verifies."""
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        # burn a hash anyway so a missing username times like a wrong password
        _pw_hash(password, b"\x00" * 16)
        return None
    if row["disabled_ts"] is not None:
        return None
    return row if hmac.compare_digest(_pw_hash(password, row["salt"]), row["pw_hash"]) else None


def set_password(conn, user_id: int, password: str) -> None:
    salt = secrets.token_bytes(16)
    conn.execute("UPDATE users SET pw_hash=?, salt=? WHERE id=?",
                 (_pw_hash(password, salt), salt, user_id))


# ---- security question (password recovery) ----

def set_security_question(conn, user_id: int, sq_id: int, answer: str) -> None:
    if sq_id not in QUESTION_TEXT:
        raise ValueError("unknown security question")
    salt = secrets.token_bytes(16)
    conn.execute("UPDATE users SET sq_id=?, sq_hash=?, sq_salt=? WHERE id=?",
                 (sq_id, _pw_hash(normalize_answer(answer), salt), salt, user_id))


def verify_answer(conn, user_row, answer: str) -> bool:
    if user_row is None or user_row["sq_hash"] is None:
        _pw_hash(normalize_answer(answer), b"\x00" * 16)
        return False
    return hmac.compare_digest(
        _pw_hash(normalize_answer(answer), user_row["sq_salt"]), user_row["sq_hash"])


def clear_sessions(conn, user_id: int) -> None:
    """Every browser session for a user — a password reset must not leave a
    hijacked cookie alive."""
    conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))


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
    """User row for a live session token, else None. A disabled account is signed
    out everywhere the moment it is disabled."""
    if not token:
        return None
    return conn.execute(
        "SELECT u.* FROM auth_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash=? AND s.expires_ts > ? AND u.disabled_ts IS NULL",
        (_sha(token), int(time.time()))).fetchone()


def delete_session(conn, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_sha(token),))


# ---- device tokens (per character+device; ingest scope) ----

def mint_device_token(conn, user_id: int, label: str | None) -> tuple[int, str]:
    """Create a token for one device on one ACCOUNT. Returns (row id, plaintext)
    — the plaintext is shown once and never stored. Scope is ingest, and only
    ingest: see `routers/ingest_api.py`.

    Not bound to a character (v13): pairing happens before anyone knows which
    alt they'll play, and the log itself says who it belongs to."""
    token = secrets.token_urlsafe(32)
    row_id = conn.execute(
        "INSERT INTO device_tokens (user_id, token_hash, label, created_ts) VALUES (?,?,?,?)",
        (user_id, _sha(token), label, int(time.time()))).lastrowid
    return row_id, token


def device_token_row(conn, token: str | None):
    """The live (un-revoked) device-token row, else None. Touches last_seen_ts —
    the 'uploader online' badge reads it.

    Carries `user_id` (the account), and `character_id`, which is NULL for
    v13-and-later tokens and only set on ones minted when tokens still belonged
    to a character. Callers resolve the character from the batch."""
    if not token:
        return None
    row = conn.execute(
        "SELECT t.id AS token_id, t.user_id, t.character_id, t.label "
        "FROM device_tokens t WHERE t.token_hash=? AND t.revoked_ts IS NULL",
        (_sha(token),)).fetchone()
    if row is not None:
        conn.execute("UPDATE device_tokens SET last_seen_ts=? WHERE id=?",
                     (int(time.time()), row["token_id"]))
    return row


def resolve_ingest_character(conn, token_row, name: str | None):
    """Which character row a batch belongs to.

    `name` is the logger the plugin read off the log file. It is created on the
    spot if this account hasn't used it before — exactly what an upload does —
    so an alt's first raid needs no setup at all. Falling back to the token's
    own character keeps pre-v13 pairings working.

    Returns None when there is neither, which is a 422 for the caller: the
    parser cannot resolve subjects without knowing whose log this is."""
    clean = (name or "").strip().capitalize()
    if clean and clean.isalpha():
        row = conn.execute(
            "SELECT * FROM characters WHERE user_id=? AND name=? AND world_id=618",
            (token_row["user_id"], clean)).fetchone()
        if row is not None:
            return row
        char_id = conn.execute(
            "INSERT INTO characters (name, user_id, world_id) VALUES (?, ?, 618)",
            (clean, token_row["user_id"])).lastrowid
        return conn.execute("SELECT * FROM characters WHERE id=?", (char_id,)).fetchone()
    if token_row["character_id"]:
        return conn.execute("SELECT * FROM characters WHERE id=?",
                            (token_row["character_id"],)).fetchone()
    return None


def revoke_device_token(conn, token_id: int) -> None:
    conn.execute("UPDATE device_tokens SET revoked_ts=? WHERE id=? AND revoked_ts IS NULL",
                 (int(time.time()), token_id))
