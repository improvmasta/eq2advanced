"""SQLite access + schema. WAL mode, single logical writer (parse tasks hold the
write lock per batch/transaction). Schema changes bump PRAGMA user_version and
append a migration step — never edit an existing step."""

import json
import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "eq2advanced.db"
UPLOADS_DIR = DATA_DIR / "uploads"
RAW_DIR = DATA_DIR / "raw"

_local = threading.local()

SCHEMA_VERSION = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  pw_hash BLOB NOT NULL,
  salt BLOB NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',      -- admin|user
  created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_ts INTEGER NOT NULL,
  expires_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS characters (
  id INTEGER PRIMARY KEY,
  user_id INTEGER REFERENCES users(id),
  name TEXT NOT NULL,
  world_id INTEGER NOT NULL DEFAULT 618,
  class TEXT,
  level INTEGER,
  census_character_id INTEGER,
  last_census_ts INTEGER,
  UNIQUE(name, world_id)
);
CREATE TABLE IF NOT EXISTS device_tokens (
  id INTEGER PRIMARY KEY,
  character_id INTEGER NOT NULL REFERENCES characters(id),
  token_hash TEXT UNIQUE NOT NULL,
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
  upload_sha256 TEXT UNIQUE,
  upload_name TEXT,
  line_count INTEGER,
  pinned INTEGER NOT NULL DEFAULT 0,
  pruned INTEGER NOT NULL DEFAULT 0,       -- events deleted; raid report frozen
  calibration INTEGER NOT NULL DEFAULT 0,  -- dummy-parse ground truth for the coach fit
  calib_stats_json TEXT,                   -- stat vector captured when flagged
  created_ts INTEGER NOT NULL,
  last_ingest_ts INTEGER
);
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
  dup_of INTEGER                          -- canonical encounter id when duplicated
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
  raider_count INTEGER,                   -- max distinct players in one fight
  updated_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zone_runs_char ON zone_runs(character_id, started_ts);
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
  time_dead_s INTEGER NOT NULL DEFAULT 0,
  rez_casts INTEGER NOT NULL DEFAULT 0,
  cure_count INTEGER NOT NULL DEFAULT 0,
  cure_latency_ms_avg INTEGER,
  active_s INTEGER NOT NULL DEFAULT 0,
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
  avg_delay_s REAL,
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


def init_db() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    with conn:
        conn.executescript(SCHEMA)
        # v2: checked unconditionally, not version-gated — the dev reloader can
        # restart mid-edit and stamp the version before a migration block lands
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
        enc_cols = {r[1] for r in conn.execute("PRAGMA table_info(encounters)")}
        if "zone_run_id" not in enc_cols:
            conn.execute("ALTER TABLE encounters ADD COLUMN zone_run_id INTEGER")
        if "dup_of" not in enc_cols:
            conn.execute("ALTER TABLE encounters ADD COLUMN dup_of INTEGER")
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
        for col in ("power_drain", "damage_taken"):
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
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            # migration steps go here as `if version < N:` blocks
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def json_dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
