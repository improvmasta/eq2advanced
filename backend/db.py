"""SQLite access + schema. WAL mode, single logical writer (parse tasks hold the
write lock per batch/transaction). Schema changes bump PRAGMA user_version and
append a migration step — never edit an existing step."""

import json
import os
import re
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "eq2advanced.db"
UPLOADS_DIR = DATA_DIR / "uploads"
RAW_DIR = DATA_DIR / "raw"

_local = threading.local()

SCHEMA_VERSION = 18

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL,                 -- stored lowercase; see idx_users_username
  pw_hash BLOB NOT NULL,
  salt BLOB NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',      -- admin|user (OPERATIONAL only, see security.py)
  sq_id INTEGER,                          -- security question (auth.RESET_QUESTIONS)
  sq_hash BLOB,
  sq_salt BLOB,
  disabled_ts INTEGER,
  upload_max_bytes INTEGER,               -- NULL = the global setting
  storage_max_bytes INTEGER,
  last_login_ts INTEGER,
  created_ts INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_ts INTEGER NOT NULL,
  expires_ts INTEGER NOT NULL
);
-- A claim is per USER, not per name: anyone may claim "Bobby" and it stops
-- nobody else claiming it too. Each claim is its own row with its own logs.
CREATE TABLE IF NOT EXISTS characters (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  world_id INTEGER NOT NULL DEFAULT 618,
  class TEXT,
  level INTEGER,
  census_character_id INTEGER,
  last_census_ts INTEGER,
  UNIQUE(user_id, name, world_id)
);
-- v13: a token belongs to an ACCOUNT, not a character. People play alts, and a
-- per-character token made "which character?" a question at pairing time — the
-- one moment nobody knows the answer, because the answer is "whichever one I
-- happen to log in tonight". The character is resolved per batch instead, from
-- the log ACT is reading (EQ2 names it eq2log_<Character>.txt), so switching
-- toons mid-evening just opens a different session. `character_id` survives as
-- the fallback for tokens minted before v13.
CREATE TABLE IF NOT EXISTS device_tokens (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  character_id INTEGER REFERENCES characters(id),
  token_hash TEXT UNIQUE NOT NULL,
  token_plain TEXT,       -- readable in settings (Sonarr-style); NULL on pre-v15 keys
  label TEXT,
  created_ts INTEGER NOT NULL,
  last_seen_ts INTEGER,
  revoked_ts INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  character_id INTEGER NOT NULL REFERENCES characters(id),
  source TEXT NOT NULL,                   -- upload|live
  started_ts INTEGER,
  ended_ts INTEGER,
  status TEXT NOT NULL DEFAULT 'receiving',  -- receiving|parsing|ready|error
  error TEXT,
  upload_sha256 TEXT,                     -- content address; UNIQUE per character,
                                          -- not globally — two people may upload
                                          -- the same raid log (idx_sessions_upload)
  upload_name TEXT,
  line_count INTEGER,
  pinned INTEGER NOT NULL DEFAULT 0,
  pruned INTEGER NOT NULL DEFAULT 0,       -- events deleted; raid report frozen
  calibration INTEGER NOT NULL DEFAULT 0,  -- dummy-parse ground truth for the coach fit
  calib_stats_json TEXT,                   -- stat vector captured when flagged
  retain_raw INTEGER NOT NULL DEFAULT 1,   -- 0 = parse it, then drop the log
  raw_deleted_ts INTEGER,                  -- set when a retain_raw=0 log is dropped
  src_bytes INTEGER,                       -- uncompressed upload size (quotas)
  raw_bytes INTEGER,                       -- stored gzip size
  created_ts INTEGER NOT NULL,
  last_ingest_ts INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_upload
  ON sessions(upload_sha256, character_id) WHERE upload_sha256 IS NOT NULL;
CREATE TABLE IF NOT EXISTS ingest_batches (
  token_id INTEGER NOT NULL,
  batch_id TEXT NOT NULL,
  session_id INTEGER NOT NULL,
  accepted INTEGER NOT NULL,
  duplicates INTEGER NOT NULL,
  created_ts INTEGER NOT NULL,
  PRIMARY KEY (token_id, batch_id)
);
CREATE TABLE IF NOT EXISTS raw_chunks (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  seq INTEGER NOT NULL,
  path TEXT NOT NULL,
  first_ts INTEGER,
  last_ts INTEGER,
  sha256 TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_chunks ON raw_chunks(session_id, seq);
CREATE TABLE IF NOT EXISTS ingest_lines (
  session_id INTEGER NOT NULL,
  line_key BLOB NOT NULL,
  PRIMARY KEY (session_id, line_key)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- player|own_pet|swarm_pet|mob|other
  owner_entity_id INTEGER,
  rollup_to INTEGER,                      -- player entity to credit
  class_guess TEXT,
  UNIQUE(session_id, name, kind)
);
CREATE TABLE IF NOT EXISTS encounters (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  zone TEXT,
  name TEXT,                              -- labeled from 'has killed <Named>'
  is_named INTEGER NOT NULL DEFAULT 0,
  started_ts INTEGER NOT NULL,
  ended_ts INTEGER NOT NULL,
  duration_s INTEGER NOT NULL,
  success INTEGER,
  zone_run_id INTEGER,                    -- run membership (canonical rows only)
  dup_of INTEGER,                         -- canonical encounter id when duplicated
  deleted_ts INTEGER                      -- hidden by hand (run_edits kind='delete')
);
CREATE INDEX IF NOT EXISTS idx_encounters ON encounters(session_id, started_ts);
CREATE INDEX IF NOT EXISTS idx_encounters_run ON encounters(zone_run_id);
CREATE TABLE IF NOT EXISTS zone_runs (
  id INTEGER PRIMARY KEY,
  character_id INTEGER NOT NULL REFERENCES characters(id),
  zone TEXT,                              -- NULL = encounters before any zone line
  started_ts INTEGER NOT NULL,            -- first canonical encounter start
  ended_ts INTEGER NOT NULL,              -- last canonical encounter end
  encounter_count INTEGER NOT NULL DEFAULT 0,  -- canonical (non-dup) only
  named_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  combat_s INTEGER NOT NULL DEFAULT 0,
  raider_count INTEGER,                   -- the roster's size (pipeline/zoneruns)
  roster_json TEXT,                       -- the roster itself, sorted names
  updated_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zone_runs_char ON zone_runs(character_id, started_ts);
CREATE TABLE IF NOT EXISTS run_edits (
  character_id INTEGER NOT NULL REFERENCES characters(id),
  fp TEXT NOT NULL,          -- '<started_ts>|<zone>|<name>' of the encounter
  kind TEXT NOT NULL,        -- delete | break (start a run here) | join (don't)
  created_ts INTEGER NOT NULL,
  PRIMARY KEY (character_id, fp, kind)
);
CREATE TABLE IF NOT EXISTS abilities (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  class TEXT,
  census_spell_crc INTEGER
);
CREATE TABLE IF NOT EXISTS ability_catalog (
  ability_name TEXT PRIMARY KEY,
  class TEXT,
  unit TEXT NOT NULL,                     -- player|pet
  proc INTEGER NOT NULL DEFAULT 0,        -- fires on its own (buff/item proc)
  scribed INTEGER NOT NULL DEFAULT 0,     -- `class` lists who SCRIBES it (not
                                          -- who procs it) — see catalog.py
  source TEXT                             -- census|curated|observed
);
CREATE TABLE IF NOT EXISTS pet_names (
  name TEXT PRIMARY KEY,                  -- capitalized named-pet name ("Lunar Attendant")
  source TEXT NOT NULL,                   -- curated|observed
  owner_hint TEXT,                        -- an owner it was seen under
  first_seen_session INTEGER
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL,
  encounter_id INTEGER,
  ts INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  src_entity INTEGER,
  tgt_entity INTEGER,
  ability_id INTEGER,
  amount INTEGER,
  dtype TEXT,
  flags INTEGER NOT NULL DEFAULT 0,
  extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_enc ON events(encounter_id, src_entity, ability_id);
CREATE INDEX IF NOT EXISTS idx_events_sess ON events(session_id, ts, seq);
CREATE TABLE IF NOT EXISTS encounter_actor_stats (
  encounter_id INTEGER NOT NULL,
  entity_id INTEGER NOT NULL,             -- rollup entity (player credit)
  damage INTEGER NOT NULL DEFAULT 0,
  dps REAL NOT NULL DEFAULT 0,
  heals INTEGER NOT NULL DEFAULT 0,
  overheal_est INTEGER,                   -- HP-deficit reconstruction (estimate)
  save_count INTEGER NOT NULL DEFAULT 0,  -- heals landed in a deep-deficit window
  wards_absorbed INTEGER NOT NULL DEFAULT 0,
  ward_bleedthrough INTEGER NOT NULL DEFAULT 0,
  power_fed INTEGER NOT NULL DEFAULT 0,
  power_drain INTEGER NOT NULL DEFAULT 0,
  damage_taken INTEGER NOT NULL DEFAULT 0,
  deaths INTEGER NOT NULL DEFAULT 0,
  time_dead_s INTEGER NOT NULL DEFAULT 0, -- death -> revive, clamped to the fight
  rez_casts INTEGER NOT NULL DEFAULT 0,
  intercepts INTEGER NOT NULL DEFAULT 0,  -- hits taken for someone else (count only)
  cure_count INTEGER NOT NULL DEFAULT 0,
  cure_latency_ms_avg INTEGER,
  active_s INTEGER NOT NULL DEFAULT 0,
  atk_swings INTEGER NOT NULL DEFAULT 0,  -- offensive swings incl. avoided
  atk_span_s INTEGER NOT NULL DEFAULT 0,  -- first->last swing; avg delay = span/(swings-1)
  presses INTEGER NOT NULL DEFAULT 0,     -- activations: AoE collapsed, DoT ticks dropped
  press_span_s INTEGER NOT NULL DEFAULT 0,-- first->last press; adjusted delay = span/(presses-1)
  PRIMARY KEY (encounter_id, entity_id)
);
CREATE TABLE IF NOT EXISTS encounter_ability_stats (
  encounter_id INTEGER NOT NULL,
  entity_id INTEGER NOT NULL,             -- source entity (NOT rolled up; pet rows kept)
  ability_id INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'damage',    -- damage|self|heal|ward|power|threat|detaunt
  casts INTEGER NOT NULL DEFAULT 0,
  hits INTEGER NOT NULL DEFAULT 0,
  crits INTEGER NOT NULL DEFAULT 0,
  misses INTEGER NOT NULL DEFAULT 0,
  resists INTEGER NOT NULL DEFAULT 0,
  parries INTEGER NOT NULL DEFAULT 0,
  ripostes INTEGER NOT NULL DEFAULT 0,
  dodges INTEGER NOT NULL DEFAULT 0,
  blocks INTEGER NOT NULL DEFAULT 0,
  reflects INTEGER NOT NULL DEFAULT 0,
  zero_hits INTEGER NOT NULL DEFAULT 0,   -- fully-absorbed hits (inside hits, ACT parity)
  total INTEGER NOT NULL DEFAULT 0,
  min INTEGER,
  max INTEGER,
  median REAL,
  avg_delay_s REAL,                       -- ACT's: span/(hits-1), ticks and AoE included
  presses INTEGER NOT NULL DEFAULT 0,     -- activations (see pipeline/statsroll)
  press_delay_s REAL,                     -- span/(presses-1): time between button presses
  dtypes TEXT,                            -- JSON {dtype: amount}, dual-type split
  uptime_s INTEGER,
  PRIMARY KEY (encounter_id, entity_id, ability_id, kind)
);
CREATE TABLE IF NOT EXISTS census_char_snapshots (
  id INTEGER PRIMARY KEY,
  character_id INTEGER NOT NULL REFERENCES characters(id),
  fetched_ts INTEGER NOT NULL,
  json BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS census_spells (
  spell_id INTEGER PRIMARY KEY,           -- per version x tier; character spell_list joins here
  name TEXT NOT NULL,
  base_name TEXT NOT NULL,                -- numeral-stripped
  crc INTEGER,
  class TEXT,
  level INTEGER,
  tier INTEGER,
  tier_name TEXT,
  json BLOB,
  parsed_effects TEXT,
  cast_s REAL,
  recast_s REAL,
  recovery_s REAL,
  duration_s REAL,
  power_cost REAL,
  dmg_min REAL,                           -- primary damage effect (largest midpoint)
  dmg_max REAL,
  dmg_dtype TEXT,
  dmg_period_s REAL,
  fetched_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_census_spells_base ON census_spells(base_name);
CREATE INDEX IF NOT EXISTS idx_census_spells_crc ON census_spells(crc);
CREATE TABLE IF NOT EXISTS census_items (
  item_id INTEGER PRIMARY KEY,
  displayname TEXT,
  tier TEXT,                              -- Census rarity string (LEGENDARY, FABLED...)
  json BLOB,
  fetched_ts INTEGER
);
CREATE TABLE IF NOT EXISTS spell_overrides (
  spell_id INTEGER PRIMARY KEY,
  parsed_effects TEXT NOT NULL,
  note TEXT,
  updated_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS raid_reports (
  session_id INTEGER PRIMARY KEY,         -- frozen at prune time
  generated_ts INTEGER NOT NULL,
  json BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS coach_reports (
  id INTEGER PRIMARY KEY,
  character_id INTEGER NOT NULL,
  session_id INTEGER NOT NULL,
  generated_ts INTEGER NOT NULL,
  engine_version TEXT NOT NULL,
  json BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
-- ---- groups + sharing (phase 12) ----
-- A group is who you raid with. Sharing is evaluated at READ time from these
-- tables (see groups.py) — never copied onto a run, so it survives every
-- zone-run rebuild and leaving a group revokes access immediately.
CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  owner_user_id INTEGER NOT NULL REFERENCES users(id),
  join_code TEXT UNIQUE,                  -- 6 digits; NULL = code joining is off
  join_code_ts INTEGER,
  join_code_expires_ts INTEGER,           -- NULL = no expiry
  created_ts INTEGER NOT NULL,
  deleted_ts INTEGER                      -- soft delete; an admin can restore it
);
CREATE TABLE IF NOT EXISTS group_members (
  group_id INTEGER NOT NULL REFERENCES groups(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  role TEXT NOT NULL DEFAULT 'member',    -- owner|admin|member
  joined_ts INTEGER NOT NULL,
  PRIMARY KEY (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id);
CREATE TABLE IF NOT EXISTS group_invites (
  id INTEGER PRIMARY KEY,
  group_id INTEGER NOT NULL REFERENCES groups(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  invited_by INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'pending', -- pending|accepted|declined
  created_ts INTEGER NOT NULL,
  UNIQUE(group_id, user_id)
);
CREATE TABLE IF NOT EXISTS character_shares (
  character_id INTEGER NOT NULL REFERENCES characters(id),
  group_id INTEGER NOT NULL REFERENCES groups(id),
  created_ts INTEGER NOT NULL,
  since_ts INTEGER,       -- NULL = back catalogue included; else runs started >= this
  raids_only INTEGER NOT NULL DEFAULT 1,  -- 1 = raids (7+ raiders) only
  PRIMARY KEY (character_id, group_id)
);
CREATE TABLE IF NOT EXISTS run_shares (
  zone_run_id INTEGER NOT NULL REFERENCES zone_runs(id),
  group_id INTEGER NOT NULL REFERENCES groups(id),
  mode TEXT NOT NULL,                     -- share | hide (hide beats an auto-share)
  created_ts INTEGER NOT NULL,
  PRIMARY KEY (zone_run_id, group_id)
);
CREATE TABLE IF NOT EXISTS public_runs (
  zone_run_id INTEGER PRIMARY KEY REFERENCES zone_runs(id),
  published_by INTEGER NOT NULL REFERENCES users(id),
  created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  actor_user_id INTEGER,
  action TEXT NOT NULL,
  target TEXT,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);
"""


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_db() -> sqlite3.Connection:
    """Thread-local connection (uvicorn workers + parse threads)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def _username_from_email(email: str, user_id: int, taken: set[str]) -> str:
    """v9: logins moved from email to username. Derive one from the old address
    (local part, lowercased, [a-z0-9_] only); anything unusable or already taken
    becomes `user{id}`, which is always free."""
    local = re.sub(r"[^a-z0-9_]", "", (email or "").split("@")[0].lower())[:20]
    if len(local) < 3 or local in taken:
        local = f"user{user_id}"
    return local


def _rebuild_device_tokens(conn: sqlite3.Connection) -> None:
    """v13: device_tokens gains user_id and character_id becomes optional.

    Rebuilt rather than ALTERed because SQLite cannot drop a NOT NULL. Ids are
    preserved so nothing pointing at a token breaks, and `user_id` comes from
    the character the token was bound to — every existing token was minted for
    a character on exactly one account, so the backfill is exact. Live tokens
    keep working: they still carry `character_id`, which is the fallback when a
    batch doesn't name a character."""
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("ALTER TABLE device_tokens RENAME TO device_tokens_old")
        conn.execute("""
            CREATE TABLE device_tokens (
              id INTEGER PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id),
              character_id INTEGER REFERENCES characters(id),
              token_hash TEXT UNIQUE NOT NULL,
              label TEXT,
              created_ts INTEGER NOT NULL,
              last_seen_ts INTEGER,
              revoked_ts INTEGER
            )""")
        conn.execute("""
            INSERT INTO device_tokens
              (id, user_id, character_id, token_hash, label, created_ts, last_seen_ts, revoked_ts)
            SELECT t.id, c.user_id, t.character_id, t.token_hash, t.label,
                   t.created_ts, t.last_seen_ts, t.revoked_ts
              FROM device_tokens_old t JOIN characters c ON c.id = t.character_id""")
        conn.execute("DROP TABLE device_tokens_old")
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert not bad, f"device_tokens rebuild broke referential integrity: {bad[:3]}"
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _rebuild_users(conn: sqlite3.Connection) -> None:
    """v9 migration: `users.email UNIQUE NOT NULL` -> `users.username` + security
    question columns. SQLite can't drop a constraint, so the table is rebuilt.
    Idempotent by shape (the `email` column is the trigger), not by user_version —
    the dev reloader can stamp the version mid-edit."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "email" not in cols:
        return
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    taken: set[str] = set()
    migrated = []
    for r in rows:
        name = _username_from_email(r["email"], r["id"], taken)
        taken.add(name)
        migrated.append((r["id"], name, r["pw_hash"], r["salt"], r["role"], r["created_ts"]))
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with conn:
            conn.execute("""
              CREATE TABLE users_new (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL,
                pw_hash BLOB NOT NULL, salt BLOB NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                sq_id INTEGER, sq_hash BLOB, sq_salt BLOB,
                disabled_ts INTEGER, upload_max_bytes INTEGER,
                storage_max_bytes INTEGER, last_login_ts INTEGER,
                created_ts INTEGER NOT NULL)""")
            conn.executemany(
                "INSERT INTO users_new (id, username, pw_hash, salt, role, created_ts) "
                "VALUES (?,?,?,?,?,?)", migrated)
            conn.execute("DROP TABLE users")
            conn.execute("ALTER TABLE users_new RENAME TO users")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)")
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise RuntimeError(f"users rebuild broke referential integrity: {bad[:5]}")
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _table_sql(conn, name: str) -> str:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone()
    return row["sql"] if row else ""


def _rebuild(conn, name: str, create_sql: str, after: tuple[str, ...] = (),
             fixups: tuple[str, ...] = ()) -> None:
    """Copy a table into a new definition, keeping ids and every column both
    definitions share. Used for the v9 constraint changes SQLite can't ALTER.

    `fixups` run against the OLD table before the copy (data must satisfy the new
    constraints); `after` runs against the new one."""
    old_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with conn:
            for sql in fixups:
                conn.execute(sql)
            conn.execute(create_sql.replace(f"TABLE {name}", f"TABLE {name}_new", 1))
            new_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({name}_new)")]
            shared = ", ".join(c for c in new_cols if c in old_cols)
            conn.execute(f"INSERT INTO {name}_new ({shared}) SELECT {shared} FROM {name}")
            conn.execute(f"DROP TABLE {name}")
            conn.execute(f"ALTER TABLE {name}_new RENAME TO {name}")
            for sql in after:
                conn.execute(sql)
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise RuntimeError(f"{name} rebuild broke referential integrity: {bad[:5]}")
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _rebuild_characters(conn) -> None:
    """v9: a character claim stops being exclusive. `UNIQUE(name, world_id)` +
    nullable owner become `UNIQUE(user_id, name, world_id)` + NOT NULL owner, so
    two people can each claim Bobby and keep their own logs. Ids are preserved —
    sessions, zone_runs, device_tokens, coach_reports, census snapshots and
    run_edits all hang off `characters.id`.

    Pre-accounts rows (`user_id IS NULL`, phase-1 uploads) go to the bootstrap
    admin, which is the account that uploaded them."""
    sql = _table_sql(conn, "characters")
    if not sql or "UNIQUE(user_id, name, world_id)" in sql:
        return   # fresh database, or already rebuilt
    owner = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if owner is None:
        conn.execute("DELETE FROM characters WHERE user_id IS NULL")
    _rebuild(conn, "characters", """
      CREATE TABLE characters (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL, world_id INTEGER NOT NULL DEFAULT 618,
        class TEXT, level INTEGER,
        census_character_id INTEGER, last_census_ts INTEGER,
        UNIQUE(user_id, name, world_id))""",
             fixups=((f"UPDATE characters SET user_id={owner['id']} WHERE user_id IS NULL",)
                     if owner else ()))


def _rebuild_sessions(conn) -> None:
    """v9: `upload_sha256 TEXT UNIQUE` was global, so the second person to upload
    a raid log hit a constraint on someone else's row. Uniqueness moves to
    (sha, character) via `idx_sessions_upload`; the gzip on disk stays
    content-addressed and shared."""
    if "upload_sha256 TEXT UNIQUE" not in _table_sql(conn, "sessions"):
        return   # fresh database, or already rebuilt
    _rebuild(conn, "sessions", """
      CREATE TABLE sessions (
        id INTEGER PRIMARY KEY,
        character_id INTEGER NOT NULL REFERENCES characters(id),
        source TEXT NOT NULL, started_ts INTEGER, ended_ts INTEGER,
        status TEXT NOT NULL DEFAULT 'receiving', error TEXT,
        upload_sha256 TEXT, upload_name TEXT, line_count INTEGER,
        pinned INTEGER NOT NULL DEFAULT 0, pruned INTEGER NOT NULL DEFAULT 0,
        calibration INTEGER NOT NULL DEFAULT 0, calib_stats_json TEXT,
        parse_version INTEGER, retain_raw INTEGER NOT NULL DEFAULT 1,
        raw_deleted_ts INTEGER, src_bytes INTEGER, raw_bytes INTEGER,
        created_ts INTEGER NOT NULL, last_ingest_ts INTEGER)""",
             after=("CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_upload "
                    "ON sessions(upload_sha256, character_id) "
                    "WHERE upload_sha256 IS NOT NULL",))


def init_db() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    _rebuild_users(conn)
    _rebuild_characters(conn)
    _rebuild_sessions(conn)
    with conn:
        conn.executescript(SCHEMA)
        # v2: checked unconditionally, not version-gated — the dev reloader can
        # restart mid-edit and stamp the version before a migration block lands
        user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        for col, typ in (("sq_id", "INTEGER"), ("sq_hash", "BLOB"), ("sq_salt", "BLOB"),
                         ("disabled_ts", "INTEGER"), ("upload_max_bytes", "INTEGER"),
                         ("storage_max_bytes", "INTEGER"), ("last_login_ts", "INTEGER")):
            if col not in user_cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        if "last_ingest_ts" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN last_ingest_ts INTEGER")
        if "parse_version" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN parse_version INTEGER")
        if "calibration" not in cols:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN calibration INTEGER NOT NULL DEFAULT 0")
        if "calib_stats_json" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN calib_stats_json TEXT")
        if "pruned" not in cols:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN pruned INTEGER NOT NULL DEFAULT 0")
        if "retain_raw" not in cols:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN retain_raw INTEGER NOT NULL DEFAULT 1")
        for col in ("raw_deleted_ts", "src_bytes", "raw_bytes"):
            if col not in cols:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} INTEGER")
        enc_cols = {r[1] for r in conn.execute("PRAGMA table_info(encounters)")}
        if "zone_run_id" not in enc_cols:
            conn.execute("ALTER TABLE encounters ADD COLUMN zone_run_id INTEGER")
        if "dup_of" not in enc_cols:
            conn.execute("ALTER TABLE encounters ADD COLUMN dup_of INTEGER")
        if "deleted_ts" not in enc_cols:
            conn.execute("ALTER TABLE encounters ADD COLUMN deleted_ts INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_encounters_run ON encounters(zone_run_id)")
        actor_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(encounter_actor_stats)")}
        if "save_count" not in actor_cols:
            conn.execute("ALTER TABLE encounter_actor_stats ADD COLUMN "
                         "save_count INTEGER NOT NULL DEFAULT 0")
        if "ward_bleedthrough" not in actor_cols:
            conn.execute("ALTER TABLE encounter_actor_stats ADD COLUMN "
                         "ward_bleedthrough INTEGER NOT NULL DEFAULT 0")
        for col in ("power_drain", "damage_taken", "atk_swings", "atk_span_s"):
            if col not in actor_cols:
                conn.execute(f"ALTER TABLE encounter_actor_stats ADD COLUMN "
                             f"{col} INTEGER NOT NULL DEFAULT 0")
        abil_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(encounter_ability_stats)")}
        for col, typ in (("parries", "INTEGER NOT NULL DEFAULT 0"),
                         ("ripostes", "INTEGER NOT NULL DEFAULT 0"),
                         ("dodges", "INTEGER NOT NULL DEFAULT 0"),
                         ("blocks", "INTEGER NOT NULL DEFAULT 0"),
                         ("reflects", "INTEGER NOT NULL DEFAULT 0"),
                         ("zero_hits", "INTEGER NOT NULL DEFAULT 0"),
                         ("median", "REAL"), ("avg_delay_s", "REAL"),
                         ("dtypes", "TEXT")):
            if col not in abil_cols:
                conn.execute(f"ALTER TABLE encounter_ability_stats ADD COLUMN {col} {typ}")
        spell_cols = {r[1] for r in conn.execute("PRAGMA table_info(census_spells)")}
        for col, typ in (("cast_s", "REAL"), ("recast_s", "REAL"),
                         ("recovery_s", "REAL"), ("duration_s", "REAL"),
                         ("power_cost", "REAL"), ("dmg_min", "REAL"),
                         ("dmg_max", "REAL"), ("dmg_dtype", "TEXT"),
                         ("dmg_period_s", "REAL")):
            if col not in spell_cols:
                conn.execute(f"ALTER TABLE census_spells ADD COLUMN {col} {typ}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_census_spells_crc ON census_spells(crc)")
        cat_cols = {r[1] for r in conn.execute("PRAGMA table_info(ability_catalog)")}
        if "proc" not in cat_cols:
            conn.execute(
                "ALTER TABLE ability_catalog ADD COLUMN proc INTEGER NOT NULL DEFAULT 0")
        if "source" not in cat_cols:
            conn.execute("ALTER TABLE ability_catalog ADD COLUMN source TEXT")
        if "scribed" not in cat_cols:
            conn.execute("ALTER TABLE ability_catalog ADD COLUMN "
                         "scribed INTEGER NOT NULL DEFAULT 0")
        # v10: intercepts + the press ("adjusted delay") columns. Added by
        # shape like the rest — the rows themselves are rebuilt by the
        # PARSE_VERSION sweep, so the defaults only live until the reparse.
        actor_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(encounter_actor_stats)")}
        for col in ("intercepts", "presses", "press_span_s"):
            if col not in actor_cols:
                conn.execute(f"ALTER TABLE encounter_actor_stats ADD COLUMN "
                             f"{col} INTEGER NOT NULL DEFAULT 0")
        abil_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(encounter_ability_stats)")}
        if "presses" not in abil_cols:
            conn.execute("ALTER TABLE encounter_ability_stats ADD COLUMN "
                         "presses INTEGER NOT NULL DEFAULT 0")
        if "press_delay_s" not in abil_cols:
            conn.execute(
                "ALTER TABLE encounter_ability_stats ADD COLUMN press_delay_s REAL")
        # v12 undoes v11. v11 let the ACT plugin set sharing (a `session_shares`
        # table and a `device_tokens.can_share` scope); sharing belongs on the
        # site, and the plugin only sends logs, so both are removed rather than
        # left as controls nothing can reach. Dropped by shape like everything
        # else, because a v11 database exists.
        token_cols = {r[1] for r in conn.execute("PRAGMA table_info(device_tokens)")}
        if "can_share" in token_cols:
            conn.execute("ALTER TABLE device_tokens DROP COLUMN can_share")
        conn.execute("DROP TABLE IF EXISTS session_shares")
        # v13: tokens move from a character to an account. SQLite can't relax a
        # NOT NULL, so the table is rebuilt; user_id is backfilled through the
        # character each token was bound to, and character_id is KEPT as the
        # fallback for any plugin still out there that doesn't name a character.
        if "user_id" not in token_cols:
            _rebuild_device_tokens(conn)
        # v15: the API key is readable in settings like Sonarr/Radarr's, so the
        # plaintext is stored beside the hash the ingest path looks up. Keys
        # minted before this stay NULL — shown as replaceable, never recovered.
        token_cols = {r[1] for r in conn.execute("PRAGMA table_info(device_tokens)")}
        if "token_plain" not in token_cols:
            conn.execute("ALTER TABLE device_tokens ADD COLUMN token_plain TEXT")
        # v14: the back catalogue becomes a choice per auto-share. NULL keeps
        # the pre-v14 meaning (every raid, past included); a set since_ts
        # limits the share to runs started at or after it.
        cs_cols = {r[1] for r in conn.execute("PRAGMA table_info(character_shares)")}
        if "since_ts" not in cs_cols:
            conn.execute("ALTER TABLE character_shares ADD COLUMN since_ts INTEGER")
        # v16: an auto-share carries raids by default and group content only if
        # asked. Existing rows get 0 — the pre-v16 meaning, every run — because
        # a migration must not revoke access somebody already has. New shares
        # are written with 1 (see groups.set_character_auto_shares).
        if "raids_only" not in cs_cols:
            conn.execute("ALTER TABLE character_shares "
                         "ADD COLUMN raids_only INTEGER NOT NULL DEFAULT 0")
        # v17: deleting a group is a soft delete, so an admin can put back a
        # roster somebody deleted by mistake. Existing rows stay NULL = live;
        # every read path already says so (groups.LIVE_GROUP).
        grp_cols = {r[1] for r in conn.execute("PRAGMA table_info(groups)")}
        if "deleted_ts" not in grp_cols:
            conn.execute("ALTER TABLE groups ADD COLUMN deleted_ts INTEGER")
        # v18: the roster is kept, not just counted — two people's uploads of
        # the same night are matched by who was in them (backend/raidmatch.py).
        # Existing rows stay NULL until the startup relink sweep rewrites them,
        # which it does on every boot; a NULL roster only costs the match its
        # cross-check, never a wrong merge.
        run_cols = {r[1] for r in conn.execute("PRAGMA table_info(zone_runs)")}
        if "roster_json" not in run_cols:
            conn.execute("ALTER TABLE zone_runs ADD COLUMN roster_json TEXT")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            # migration steps go here as `if version < N:` blocks
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return default if row is None or row["value"] is None else row["value"]


def get_int_setting(conn, key: str, default: int = 0) -> int:
    try:
        return int(get_setting(conn, key) or default)
    except (TypeError, ValueError):
        return default


def set_setting(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                 (key, None if value is None else str(value)))


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def json_dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
