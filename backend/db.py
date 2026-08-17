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
# Re-encoded screenshots behind imported parses. Separate from `uploads/`,
# which is content-addressed raw logs and is reasoned about very differently.
PARSESHOTS_DIR = DATA_DIR / "parseshots"
# Screenshots attached to raid notes. Its own directory rather than a shared
# one: these are the raid's evidence, those are claims about a parse, and a
# retention decision about either should never sweep up the other.
NOTESHOTS_DIR = DATA_DIR / "noteshots"
# Item icons, keyed by Census `iconid` — reference data about the GAME, not
# about anybody's raid, so one file serves every account forever and a prune
# of somebody's log can never reach it.
ICONS_DIR = DATA_DIR / "icons"

_local = threading.local()

SCHEMA_VERSION = 47

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL,                 -- stored lowercase; see idx_users_username
  pw_hash BLOB NOT NULL,
  salt BLOB NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',      -- admin|curator|user. OPERATIONAL only
                                          -- (security.py): none of them reads a
                                          -- raid. `curator` opens the Abilities
                                          -- console — EQ2 knowledge, not access.
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
  revoked_ts INTEGER,
  client_version TEXT     -- the plugin version this token last sent (v30), off
                          -- its User-Agent; NULL until one does, which is what
                          -- keeps the update pill quiet for people who have
                          -- never paired
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
  redacted_lines INTEGER NOT NULL DEFAULT 0,  -- private chat dropped before storage
                                          -- (pipeline/redact.py) — shown on Import
                                          -- so the promise is a number, not a claim
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
  deleted_ts INTEGER,                     -- dropped by hand (run_edits kind='delete')
  hidden_ts INTEGER                       -- hidden by hand (run_edits kind='hide'):
                                          -- still the owner's to read, gone for
                                          -- everyone else and out of every total
);
CREATE INDEX IF NOT EXISTS idx_encounters ON encounters(session_id, started_ts);
CREATE INDEX IF NOT EXISTS idx_encounters_run ON encounters(zone_run_id);
CREATE TABLE IF NOT EXISTS zone_runs (
  id INTEGER PRIMARY KEY,
  character_id INTEGER NOT NULL REFERENCES characters(id),
  zone TEXT,                              -- NULL = encounters before any zone line
  started_ts INTEGER NOT NULL,            -- first canonical encounter start
  ended_ts INTEGER NOT NULL,              -- last canonical encounter end
  encounter_count INTEGER NOT NULL DEFAULT 0,  -- canonical (non-dup), VISIBLE only
                                          -- 0 with hidden_count > 0 = the whole
                                          -- run is hidden (see groups.py)
  hidden_count INTEGER NOT NULL DEFAULT 0,     -- canonical fights hidden by hand
  named_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  combat_s INTEGER NOT NULL DEFAULT 0,
  raider_count INTEGER,                   -- the roster's size (pipeline/zoneruns)
  roster_json TEXT,                       -- the roster itself, sorted names
  is_raid INTEGER,                        -- raid CONTENT, from zone/target reference;
                                          -- NULL only until the startup relink
  guild TEXT,                             -- majority guild of the roster, or NULL
                                          -- when no majority holds (census/guilds.py)
  updated_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zone_runs_char ON zone_runs(character_id, started_ts);
CREATE TABLE IF NOT EXISTS run_edits (
  character_id INTEGER NOT NULL REFERENCES characters(id),
  fp TEXT NOT NULL,          -- '<started_ts>|<zone>|<name>' of the encounter
  kind TEXT NOT NULL,        -- delete | hide | break (start a run here) | join (don't)
  created_ts INTEGER NOT NULL,
  PRIMARY KEY (character_id, fp, kind)
);
CREATE TABLE IF NOT EXISTS abilities (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  class TEXT,
  census_spell_crc INTEGER
);
-- `unit` and `proc` are VERDICTS and only a human sets them (source='curated').
-- Everything the machine works out is a CANDIDATE beside them, because both
-- questions were being answered by evidence that cannot carry them: a name
-- seen acting like a pet once, and Census's "may cast X on ..." grammar, which
-- flags a class's own combat art the moment any buff anywhere references it.
-- A candidate is what the review export ranks; it never reaches a badge.
CREATE TABLE IF NOT EXISTS ability_catalog (
  ability_name TEXT PRIMARY KEY,
  class TEXT,
  unit TEXT NOT NULL,                     -- player|pet — VERDICT (curated only)
  proc INTEGER NOT NULL DEFAULT 0,        -- fires on its own — VERDICT (curated only)
  scribed INTEGER NOT NULL DEFAULT 0,     -- `class` lists who SCRIBES it (not
                                          -- who procs it) — see catalog.py
  pet_seen INTEGER NOT NULL DEFAULT 0,    -- times a pet-kind entity cast it (evidence)
  proc_candidate INTEGER NOT NULL DEFAULT 0,  -- Census grammar says something casts it
  proc_class TEXT,                        -- classes whose buff/item fires it
  source TEXT                             -- census|curated|observed
);
-- Game reference data from the EQ2 wiki (gamewiki.py) — the abilities Census
-- does not carry. Census's spell collection is the better source for SPELLS
-- and stays authoritative for them; what it has never been asked for is AAs
-- (256 incidental rows against the wiki's 1215) and items, which is most of
-- what a raid log names and nothing can currently explain.
--
-- `activated` is the field that earns this table on its own. `You prepare <X>`
-- prints for spells and combat arts and NOT for AA activations, so an
-- activated AA is indistinguishable from a proc in the log — 11 confirmed
-- (Lifeburn, Mana Flow, Counterblade …) and 45 rows resting on that same
-- signature. A recast timer is proof it is pressed, and it exists nowhere else.
--
-- `era` is why the ingest is safe on a TLE server: the wiki separates the AA
-- trees by expansion with zero overlap, so a level-70 EoF server takes the
-- Class/Subclass trees and never sees Heroic (RoK), Shadows (TSO) or Dragon
-- (DoV) abilities that would otherwise label raids with content that does not
-- exist here.
-- Keyed by (name, KIND), because one name really can be two abilities: the
-- fury spell `Tempest` and Karana's miracle `Tempest (Miracle)` both print as
-- "Tempest" in a log, and 37 AA names collide with a blessing or miracle the
-- same way. A single-column key let the deity sync silently overwrite them —
-- and a name that is genuinely two things is an AMBIGUITY to report, not a
-- race to win (`gamewiki.by_name` marks it and `suggest` refuses to be
-- confident about it).
CREATE TABLE IF NOT EXISTS wiki_abilities (
  name TEXT NOT NULL,                     -- ability name as a log would print it
  page_title TEXT NOT NULL,               -- the wiki page (may carry a "(AA)" suffix)
  kind TEXT NOT NULL,                     -- aa|spell|item
  era TEXT,                               -- eof|rok|tso|dov — which expansion
  tiers TEXT,                             -- classtree grant targets, comma-joined
  line TEXT,                              -- the AA line ("Rotting", "Intelligence")
  activated INTEGER,                      -- 1 = has a recast/cost, so it is PRESSED
  recast_s REAL,
  duration_s REAL,                        -- how long the effect LASTS. The only source
                                          -- for it: a hostile debuff prints a line when
                                          -- it lands and nothing when it fades, so this
                                          -- is what makes a window out of that line
                                          -- (refdata/reuse_debuffs.json)
  power TEXT,
  target TEXT,
  descr TEXT,
  effects TEXT,                           -- the raw effect bullets, verbatim
  fetched_ts INTEGER NOT NULL,
  PRIMARY KEY (name, kind)
);
CREATE INDEX IF NOT EXISTS idx_wiki_abilities_kind ON wiki_abilities(kind, era);

-- A HUMAN's answer about one ability, and the top of the precedence ladder:
-- ruling > curated seed > nothing. Nothing else may write here — this is the
-- table the Abilities admin page fills in, and its whole point is that it
-- cannot be overwritten by the next parse.
--
-- `grant_kind` is what fires it (spell/aa/item/deity/pet), `grant_name` the
-- thing itself ("Fae Fire", "Overclocked Lifestone", a deity) and `grant_class`
-- who owns that thing. The last one is what makes SELF vs GRANTED answerable:
-- Fae Fires on a fury is their own buff, on the warlock beside them it is the
-- fury's — same ability, different answer, decided per row against this class.
CREATE TABLE IF NOT EXISTS ability_rulings (
  ability_name TEXT PRIMARY KEY,
  unit TEXT NOT NULL,                     -- player|pet
  fires TEXT NOT NULL,                    -- cast|proc
  grant_kind TEXT,                        -- spell|aa|item|deity|pet|unknown
  grant_name TEXT,
  grant_class TEXT,
  note TEXT,
  decided_by INTEGER,
  decided_ts INTEGER NOT NULL
);
-- Human authority over learned enemy timers. Observations stay in aoe_cycles;
-- this table only records reversible decisions and their provenance.
CREATE TABLE IF NOT EXISTS timer_rulings (
  source_name TEXT NOT NULL,
  ability TEXT NOT NULL,
  override_s REAL,
  accepted_measured INTEGER NOT NULL DEFAULT 0,
  excluded INTEGER NOT NULL DEFAULT 0,
  split_mob INTEGER,
  note TEXT NOT NULL,
  decided_by INTEGER,
  decided_ts INTEGER NOT NULL,
  PRIMARY KEY (source_name, ability)
);
CREATE TABLE IF NOT EXISTS timer_mechanics (
  kind TEXT NOT NULL,                       -- reuse_debuff|reflect_window
  name TEXT NOT NULL,
  config_json TEXT NOT NULL,
  note TEXT NOT NULL,
  decided_by INTEGER,
  decided_ts INTEGER NOT NULL,
  PRIMARY KEY (kind, name)
);
-- Which sessions saw a pet-KIND entity cast an ability. Evidence for the pet
-- review, keyed by session so a reparse re-states it rather than inflating it.
CREATE TABLE IF NOT EXISTS ability_pet_sightings (
  ability_name TEXT NOT NULL,
  session_id INTEGER NOT NULL,
  PRIMARY KEY (ability_name, session_id)
);
CREATE TABLE IF NOT EXISTS pet_names (
  name TEXT PRIMARY KEY,                  -- capitalized named-pet name ("Lunar Attendant")
  source TEXT NOT NULL,                   -- curated|observed
  owner_hint TEXT,                        -- an owner it was seen under
  first_seen_session INTEGER
);
-- Every raider's class, straight from Census, whether or not they have an
-- account here. Inference reads a spellbook and guesses; this is the game
-- answering. `found=0` is a real answer too — it is cached so a mob or a
-- deleted name is not re-queried on every parse (census/roster.py).
CREATE TABLE IF NOT EXISTS roster_classes (
  name_lower TEXT NOT NULL,
  world_id INTEGER NOT NULL,
  name TEXT NOT NULL,                     -- as Census spells it
  class TEXT,                             -- lowercase; NULL when not found
  level INTEGER,
  census_character_id INTEGER,
  found INTEGER NOT NULL DEFAULT 0,
  checked_ts INTEGER NOT NULL,
  guild_name TEXT,                        -- NULL + guild_checked=1 means GUILDLESS
  guild_id INTEGER,
  guild_checked INTEGER NOT NULL DEFAULT 0,  -- 0 = never asked, so it abstains
  PRIMARY KEY (name_lower, world_id)
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
  deaths_inferred INTEGER NOT NULL DEFAULT 0, -- of those, ones the log never
                                          -- printed (pipeline/downs.py)
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
-- What a chest gave the raid. NOT a combat event and deliberately not in
-- `events`: a looter is a NAME on a line, and resolving one into `entities`
-- would put somebody who only walked past the chest into the fight's roster.
-- See pipeline/loot.py.
CREATE TABLE IF NOT EXISTS loot_drops (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  encounter_id INTEGER,                   -- the fight the chest belonged to; NULL
                                          -- when none could be named (see `attribution`)
  ts INTEGER NOT NULL,
  chest TEXT NOT NULL,                    -- 'Exquisite Chest' | 'Treasure Chest' | 'Small Chest'
  mob TEXT NOT NULL,                      -- whose chest, verbatim off the line
  item_id INTEGER NOT NULL,               -- the log's link id as UNSIGNED = the Census item id
  item_name TEXT NOT NULL,
  qty INTEGER NOT NULL DEFAULT 1,         -- 'wins the lotto for 4 <ITEM>' is one row, not four
  looter TEXT NOT NULL,                   -- 'You' already mapped to the logger
  method TEXT NOT NULL,                   -- 'lotto' (rolled for) | 'loot' (taken outright)
  rarity TEXT,                            -- off the paired `looted the <Rarity>` line
  confirmed INTEGER NOT NULL DEFAULT 0,   -- a `looted` line paired: they really took it
  rolls_json TEXT,                        -- who rolled what for it: the lotto's NEED/GREED
                                          -- block, or the /random dice when the raid used
                                          -- those instead (pipeline/loot.py)
  attribution TEXT NOT NULL,              -- name | entity | nearest | none
  UNIQUE(session_id, ts, item_id, looter, mob)
);
CREATE INDEX IF NOT EXISTS idx_loot_encounter ON loot_drops(encounter_id);
CREATE INDEX IF NOT EXISTS idx_loot_session ON loot_drops(session_id, ts);
-- One enemy AoE recast we WATCHED: the gap between two casts, and whether the
-- cast that started that recast was made while a reuse debuff was on the mob
-- (pipeline/aoes.py owns both definitions; pipeline/aoecycles.py writes here).
--
-- An OBSERVATION table, not an answer. What the timer for a (mob, ability)
-- actually is, and whether a swipe moves it, is derived from these rows across
-- every raid on the site — see pipeline/aoelearn.py. Storing the cycles rather
-- than the conclusion is what lets the conclusion be recomputed when the
-- thresholds change, and what lets a fight be re-parsed without the site
-- forgetting what it learned from the fights either side of it.
--
-- Rows are per ENCOUNTER and are cleared with the rest of a session's derived
-- data (clear_derived), so a rebuild replaces them rather than double-counting.
-- The mob is keyed by NAME because that is the only identity that survives a
-- reparse and the only one that is comparable between two guilds' logs — which
-- is also why `instances` is carried: six mobs sharing a name read as one mob
-- casting six times as often, and that is a fact about the row, not the mob.
CREATE TABLE IF NOT EXISTS aoe_cycles (
  encounter_id INTEGER NOT NULL REFERENCES encounters(id),
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  source_name TEXT NOT NULL,              -- the mob, by name
  ability TEXT NOT NULL,
  cast_ts INTEGER NOT NULL,               -- the cast that STARTED this recast
  gap_s INTEGER NOT NULL,                 -- to the next cast of the same ability
  swiped INTEGER NOT NULL,                -- was a reuse debuff up at cast_ts
  is_named INTEGER NOT NULL,              -- a named is one mob; trash may be many
  PRIMARY KEY (encounter_id, source_name, ability, cast_ts)
);
CREATE INDEX IF NOT EXISTS idx_aoe_cycles_ability ON aoe_cycles(source_name, ability);
CREATE INDEX IF NOT EXISTS idx_aoe_cycles_session ON aoe_cycles(session_id);

-- The display record for an item the log named: Census answers what it IS, the
-- wiki answers what it LOOKS like and where to read about it. Reference data
-- about the game — one row serves every account, and it is never per-raid.
CREATE TABLE IF NOT EXISTS items (
  item_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  iconid INTEGER,
  tier TEXT,                              -- Census rarity (FABLED, LEGENDARY, TREASURED...)
  type TEXT,
  slot TEXT,
  level INTEGER,
  wiki_title TEXT,                        -- the EQ2i page that is NOT a disambig, or NULL
  icon_ok INTEGER,                        -- 1 = cached in ICONS_DIR; 0 = the wiki has no
                                          -- File:Item <iconid>.png; NULL = never asked
  effects_json TEXT,                      -- the item's own effect: name, tier and the
                                          -- indented description, off the wiki page's
                                          -- EquipInformation template (Census has no
                                          -- field for it — see docs/census-abilities.md)
  stats_json TEXT,                        -- the examine window: Census `modifiers` grouped
                                          -- by its own type, + adornment slots + set flags.
                                          -- Stored rendered-ready so the hover card is a
                                          -- read, never a fetch (backend/items.py)
  census_ts INTEGER,                      -- when each source last answered; NULL = never
  wiki_ts INTEGER
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
-- A user's standing rule: uploads I own while this character-guild tag is on
-- my uploader go to this group. Matched on the UPLOADER's character's guild
-- (roster_classes, Census-derived), never the run's majority vote — sharing
-- stays a per-user decision about their own uploads.
CREATE TABLE IF NOT EXISTS guild_shares (
  user_id INTEGER NOT NULL REFERENCES users(id),
  group_id INTEGER NOT NULL REFERENCES groups(id),
  guild_name TEXT NOT NULL COLLATE NOCASE,   -- as Census spells it
  created_ts INTEGER NOT NULL,
  since_ts INTEGER,       -- NULL = back catalogue included; else runs started >= this
  raids_only INTEGER NOT NULL DEFAULT 1,  -- 1 = raids (7+ raiders) only
  PRIMARY KEY (user_id, group_id, guild_name)
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
-- A raid somebody shared with you that you don't want on your list (v31).
-- The READER's own decision about their own list, and nothing else: it revokes
-- no access, tells the owner nothing, and never touches the share it came from
-- (only the owner can do that). Kept out of `run_shares` deliberately — that
-- table is the OWNER's audience, and a row of somebody else's in it would be
-- read as a share by every one of the four standing-branch query sites.
CREATE TABLE IF NOT EXISTS run_dismissals (
  zone_run_id INTEGER NOT NULL REFERENCES zone_runs(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_ts INTEGER NOT NULL,
  PRIMARY KEY (zone_run_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_run_dismissals_user ON run_dismissals(user_id);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  actor_user_id INTEGER,
  action TEXT NOT NULL,
  target TEXT,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);
CREATE TABLE IF NOT EXISTS incident_acknowledgements (
  session_id INTEGER PRIMARY KEY REFERENCES sessions(id),
  note TEXT NOT NULL,
  actor_user_id INTEGER,
  acknowledged_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL,                     -- bug|suggestion
  body TEXT NOT NULL,
  page TEXT,                              -- SPA path it was filed from
  status TEXT NOT NULL DEFAULT 'open',    -- open|planned|closed
  assignee_user_id INTEGER REFERENCES users(id),
  admin_note TEXT,
  created_ts INTEGER NOT NULL,
  updated_ts INTEGER                      -- last status change
);
CREATE INDEX IF NOT EXISTS idx_feedback ON feedback(status, created_ts DESC);
-- A parse that only ever existed as a screenshot (pipeline/actshot.py). It is
-- NOT a session and must never become one: no entities, no encounters, no zone
-- run, nothing that reaches a rollup, a ranking or raidmatch. It exists to be
-- put beside a real parse on /compare and nowhere else, which is why the rows
-- live as JSON here rather than in encounter_ability_stats — the moment they
-- share a table with parsed numbers, something will average the two together.
-- The image IS kept, but never the original: a re-encoded copy plus a
-- thumbnail, so the picture can be put beside the numbers it produced. That is
-- the whole reason to keep one — some columns can't be verified by arithmetic,
-- and the screenshot is the only other evidence there is. It stays as private
-- as the row, and re-encoding means what lands on disk is an image this app
-- wrote rather than the file somebody was handed.
CREATE TABLE IF NOT EXISTS imported_parses (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT,                             -- the ACT title bar, as read
  zone TEXT,
  encounter TEXT,
  character_name TEXT,
  kind TEXT NOT NULL DEFAULT 'damage',    -- damage|heal, from the title's view
  duration_s INTEGER,
  when_text TEXT,                         -- as printed; undated shots have none
  decimal_mark TEXT,                      -- which mark this client used
  columns_json TEXT NOT NULL,             -- the columns THIS shot carried
  total_json TEXT,                        -- ACT's `All` line
  rows_json TEXT NOT NULL,
  notes_json TEXT,                        -- what was recomputed or dropped
  source TEXT NOT NULL DEFAULT 'screenshot',
  image_name TEXT,                        -- webp under PARSESHOTS_DIR, or NULL
  thumb_name TEXT,
  image_w INTEGER,                        -- of the stored copy, for the viewer
  image_h INTEGER,
  image_bytes INTEGER,
  created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_imported_parses
  ON imported_parses(user_id, created_ts DESC);

-- v28: raid notes — what you write down DURING a raid, from the dashboard.
--
-- The key is (user, zone, mob), and that is the whole idea: a note filed while
-- the raid is on trash belongs to the ZONE (mob_name NULL) and one filed on a
-- named belongs to that NAMED, so six months of "watch the adds on the third
-- tick" pile up under the boss they are about instead of under the night they
-- were typed on. That pile is the raid outline this is meant to grow into.
--
-- `encounter_id` / `zone_run_id` are PROVENANCE and nothing else. A live
-- session is rebuilt from raw when it closes and its encounter ids all change,
-- so a note that identified itself by one would lose its subject overnight.
-- Never JOIN them to decide what a note is about.
--
-- Private to whoever wrote it, with no group predicate — the same rule
-- `imported_parses` keeps. Sharing lives in groups.py or it does not exist.
CREATE TABLE IF NOT EXISTS raid_notes (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  zone TEXT NOT NULL,
  mob_name TEXT,                          -- NULL = a zone note (trash)
  body TEXT NOT NULL DEFAULT '',
  encounter_id INTEGER,                   -- provenance only, see above
  zone_run_id INTEGER,
  created_ts INTEGER NOT NULL,
  updated_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raid_notes_key
  ON raid_notes(user_id, zone, mob_name, created_ts DESC);

-- A screenshot attached to a note. Stored the way parse shots are (see
-- routers/parseshots_api.py): re-encoded to webp under NOTESHOTS_DIR, never
-- the uploaded bytes, and served by an owner-checked endpoint rather than a
-- static mount.
CREATE TABLE IF NOT EXISTS raid_note_shots (
  id INTEGER PRIMARY KEY,
  note_id INTEGER NOT NULL REFERENCES raid_notes(id),
  image_name TEXT NOT NULL,
  thumb_name TEXT,
  image_w INTEGER,
  image_h INTEGER,
  image_bytes INTEGER,
  created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raid_note_shots ON raid_note_shots(note_id, id);

-- v29: stream overlay tokens — a URL you paste into OBS as a browser source.
--
-- A capability, not an account: the token IS the authorization, because a
-- browser source carries no cookies and EventSource cannot set a header. So
-- the token is deliberately narrow — it reaches the live meter for whichever
-- of that user's characters is streaming right now, and nothing else. No
-- session ids, no history, no account. Revoking is a row update, which is the
-- only reason it is a row rather than a signed string: a URL that is on
-- somebody's stream needs to be killable without changing anything else.
--
-- v34: `kind` — the same capability, two screens. `overlay` is the OBS browser
-- source; `ingame` is EQ2's own browser window, which is the same page read at
-- a completely different size (a stream is watched after a downscale and an
-- encode and wants type BIGGER than 1:1; the in-game window is a corner of
-- somebody's UI and wants it smaller). Separate ROWS rather than one row with
-- two config blocks, for one reason that matters: revoking is per URL. A link
-- that ended up in a VOD has to be killable without taking the window beside
-- the hotbars down with it.
CREATE TABLE IF NOT EXISTS overlay_tokens (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  token TEXT NOT NULL UNIQUE,
  label TEXT,
  kind TEXT NOT NULL DEFAULT 'overlay',
  config_json TEXT NOT NULL DEFAULT '{}',
  created_ts INTEGER NOT NULL,
  revoked_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_overlay_tokens_user
  ON overlay_tokens(user_id, created_ts DESC);

-- v35: the two HAND MARKS — which AoEs you joust, and which belong on the mini
-- parse (`frontend/src/lib/marks.js`). One row per answer.
--
-- These lived in localStorage, and that was right for exactly as long as the
-- only other surface was a stream: a mark is a note about how somebody plays,
-- it is worth nothing to the server, and a settings table was a round trip in
-- front of a countdown. What broke it is the IN-GAME window (v34). EQ2's own
-- browser is a different browser too, so a raider who had spent a night marking
-- rows on the dashboard opened the window beside their hotbars and found none
-- of it there — jousting whatever their ACT list happened to list, and nothing
-- they had said by hand. The stream overlay had the same hole and nobody
-- noticed, because nobody reads their own stream.
--
-- THE ROW'S ABSENCE IS THE THIRD STATE, and that is why this is a row per
-- ability rather than a JSON blob of names. A mark is an ANSWER — yes, no, or
-- nothing said — and `nothing said` takes the default (whether ACT's spell
-- timer list knows the ability). A set of names could only ever say "these are
-- on", which makes a good default impossible to overrule downwards.
--
-- Keyed by ability NAME, never by source, fight or run: both marks are
-- properties of the ABILITY (joust Mayong's Soul Paralysis and you joust it on
-- every Mayong, in every zone, next week as well), so a mark outlives the pull
-- it was made on. Nothing here is derived and nothing rebuilds it, so it
-- survives a reparse without being carried.
CREATE TABLE IF NOT EXISTS user_marks (
  user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL,                     -- 'joust' | 'mini'
  ability TEXT NOT NULL,
  marked INTEGER NOT NULL,                -- 1 yes, 0 no; no row = nothing said
  updated_ts INTEGER NOT NULL,
  PRIMARY KEY (user_id, kind, ability)
);
-- v45: five named Planner equipment-set slots per account. The payload is the
-- reader's chosen loadout, not game reference data; keeping it as versioned
-- JSON lets the client evolve the working-set shape without a schema change.
-- Missing rows are the untouched defaults ("Set 1" through "Set 5").
CREATE TABLE IF NOT EXISTS planner_saved_sets (
  user_id INTEGER NOT NULL REFERENCES users(id),
  slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 5),
  name TEXT NOT NULL,
  payload_json TEXT,
  updated_ts INTEGER NOT NULL,
  PRIMARY KEY (user_id, slot)
);
-- v36: the public chat box became a RECORD (`pipeline/chatbus.py`). It used to
-- be a relay with a few hours of memory that a restart emptied; it is now the
-- site's archive of the three PUBLIC channels, kept for as long as there is
-- disk, because a window into the server's chat is only worth having if you can
-- look at last Tuesday through it.
--
-- WHAT DID NOT CHANGE: `redact.py` is still untouched. v36 originally treated
-- General (2), LFG (3), and Auction (10) as fixed ids, but EQ2 channel numbers
-- were later proven to be per-character slots. `chatbus` now defaults-deny by
-- the numbered channel SHAPE plus those three exact names. A tell, guild chat,
-- officer chat and /say still have no route here.
--
-- Nothing here JOINS. There is no user_id, no character_id, no session: a chat
-- line belongs to the server, not to whoever's plugin happened to relay it, and
-- the uploader is deliberately not recorded. `who` is the SPEAKER as the game
-- printed the name, which is a fact about the game world.
--
-- `text` is the message with EQ2's link markup INTACT — the markup is stripped
-- on the way out (`_parts`) rather than on the way in, so a later reader can
-- draw an item link the way the game draws it. UNIQUE is the dedupe: every
-- raider in the zone logs the same General line, and now the collapse survives
-- a restart instead of living in a bounded set in memory.
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY,                 -- also the stream cursor (`since`)
  ts INTEGER NOT NULL,                    -- the LOG clock, unix
  ch TEXT NOT NULL,                       -- general|lfg|auction
  who TEXT NOT NULL,
  text TEXT NOT NULL,
  UNIQUE(ts, ch, who, text)
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_ch_ts ON chat_messages(ch, ts);

-- v39: private Discord DMs for account-owned chat alert rules. The Discord
-- application is installed to a USER, never a guild; `dm_channel_id` is the
-- BOT_DM where `/link` was invoked and is the only destination the worker can
-- reach. A link carries no OAuth token and grants no access to a Discord
-- server. Removing it takes every pending delivery with it at the API layer.
CREATE TABLE IF NOT EXISTS discord_links (
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  discord_user_id TEXT NOT NULL UNIQUE,
  dm_channel_id TEXT NOT NULL,
  display_name TEXT,
  paused INTEGER NOT NULL DEFAULT 0,
  linked_ts INTEGER NOT NULL,
  last_error TEXT,
  last_error_ts INTEGER
);

-- Short-lived, single-use codes bridge an authenticated EQ2Advanced browser
-- to a signed Discord `/link` interaction. The raw code is returned once and
-- only its digest is kept. One active code per account means pressing Pair
-- twice invalidates the first rather than leaving several small credentials.
CREATE TABLE IF NOT EXISTS discord_pair_codes (
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  code_hash TEXT NOT NULL UNIQUE,
  created_ts INTEGER NOT NULL,
  expires_ts INTEGER NOT NULL
);

-- One rule is deliberately one phrase. Several alternatives are several rows,
-- which makes pausing, naming and cooldowns independently understandable.
-- Matching is case-insensitive over the rendered message text: linked item and
-- guild labels count, EQ2's hidden markup does not.
CREATE TABLE IF NOT EXISTS chat_alert_rules (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'any',    -- any|general|lfg|auction
  query TEXT NOT NULL,
  exclude_query TEXT,
  speaker TEXT,
  cooldown_s INTEGER NOT NULL DEFAULT 300,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_ts INTEGER NOT NULL,
  updated_ts INTEGER NOT NULL,
  last_sent_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chat_alert_rules_user
  ON chat_alert_rules(user_id, enabled);

-- The transactional outbox. A chat insert and its alert matches commit (or
-- roll back) together, then a worker sends the DM. One row per user/message
-- bundles overlapping rules into one notification; rule_ids_json records the
-- candidates and they are checked again at send time so deleting or pausing a
-- rule before delivery takes effect.
CREATE TABLE IF NOT EXISTS chat_alert_deliveries (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  message_id INTEGER NOT NULL REFERENCES chat_messages(id),
  rule_ids_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', -- pending|sent|suppressed|failed
  attempts INTEGER NOT NULL DEFAULT 0,
  available_ts INTEGER NOT NULL,
  created_ts INTEGER NOT NULL,
  sent_ts INTEGER,
  error TEXT,
  UNIQUE(user_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_chat_alert_delivery_queue
  ON chat_alert_deliveries(status, available_ts, id);

-- v37: how many people came to look (`visitors.py`). This exists for ONE
-- question — the /chat link was publicized, so how many strangers did it bring
-- — and the shape is cut down to answering exactly that.
--
-- A VISITOR IS A DAY, NOT A PERSON. The row's key is (day, visitor), where
-- `visitor` is sha256 over a salt that belongs to that day alone. The salt is
-- thrown away two days later (`visitors.sweep`), and after that the hash is
-- attached to nothing: it cannot be turned back into an address, and it cannot
-- be matched against another day's hash for the same person either. So this
-- table answers "how many distinct people that day" and CANNOT answer "did
-- this one come back on Thursday". That is the intended ceiling, not a gap to
-- close later — it is what makes counting readers compatible with a site that
-- refuses to keep a roster of them (`docs/sharing.md`).
--
-- NOTHING HERE JOINS, same as `chat_messages`. `signed_in` is a flag on the
-- day, never a user_id: the point of the count is the SIGNED-OUT stranger, and
-- an admin page that could turn a visit into an account would be building the
-- thing this design refuses to build.
CREATE TABLE IF NOT EXISTS visit_days (
  day TEXT NOT NULL,                    -- YYYY-MM-DD, the SERVER's day
  visitor TEXT NOT NULL,                -- sha256(day salt + address + agent)
  signed_in INTEGER NOT NULL DEFAULT 0, -- had a live session at some point today
  chat INTEGER NOT NULL DEFAULT 0,      -- landed on /chat at some point today
  hits INTEGER NOT NULL DEFAULT 0,      -- page loads, not requests
  first_ts INTEGER NOT NULL,
  last_ts INTEGER NOT NULL,
  PRIMARY KEY (day, visitor)
);

-- One row per day and deleted as soon as it is old. Keeping this table small
-- is the whole privacy property: the salt is the only thing that could link a
-- stored hash back to somebody, so it is not kept.
CREATE TABLE IF NOT EXISTS visit_salts (
  day TEXT PRIMARY KEY,
  salt TEXT NOT NULL
);

-- v40: the Planner's catalog (`backend/planner/`, docs/planner.md). Reference
-- data about the GAME, per expansion — no account, no parse, no session and no
-- visibility predicate reaches any of it, exactly as `items` puts it: one row
-- serves every reader forever.
--
-- THE ERA IS A COLUMN, NOT A BUILD-TIME CONSTANT. The page lets a reader pick
-- which expansions count — EoF, RoK, or both — so era is stored per row and
-- filtered at read time. Adding a third expansion is a re-sync, not a
-- migration.
--
-- Filled ONLY by `tools/sync_planner.py`, run by hand and never on a schedule
-- — the same rule the wiki ability ingest keeps, for the same reason: a crawl
-- that runs itself is a crawl nobody is watching.

-- The keys are the WIKI PAGE, not the Census id. Every row has a page (it is
-- where the row came from); `census_id` is absent on the handful of pages whose
-- `itemlink` is missing, and two versions of one item — `(Level 78)` and
-- `(Level 80)` — are two rows that a name alone could not tell apart.
CREATE TABLE IF NOT EXISTS plan_items (
  page_title TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  census_id INTEGER,                      -- the log's item id, unsigned; joins `items`
  era TEXT NOT NULL,                      -- rok|eof, taken from the SOURCE's `patch`
  slot TEXT,
  slot2 TEXT,                             -- a second slot the item also fills
  level INTEGER,
  tier TEXT,                              -- icat: FABLED|LEGENDARY|TREASURED|…
  dtype TEXT,                             -- armour type (Chain Armor, Cloth Armor)
  wtype TEXT,                             -- weapon type, when it is one
  classes TEXT,                           -- comma list of SUBCLASSES, era-filtered
  flags TEXT,                             -- lore, no-trade, heirloom…
  adorns_json TEXT,                       -- {"turquoise":1,"white":1} — capacity to HOST
  set_name TEXT,                          -- the adornment set its turquoise belongs to
  stats_json TEXT NOT NULL,               -- {stat key: value}, era-hidden stats dropped
  effects TEXT,                           -- the proc's NAME (layer 2 — nothing rules on it)
  effect_desc TEXT,
  icon INTEGER,
  fetched_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_items_era ON plan_items(era, slot, level);
CREATE INDEX IF NOT EXISTS idx_plan_items_set ON plan_items(set_name);

-- Source attribution is built by INVERTING mob and quest pages, because the
-- item's own `obtain` field is blank more often than not. `kind` is the
-- raid/group/solo split, which comes free from the monster's `diff` — and it
-- is the filter that separates a raider's question from a soloer's.
-- One item may have several sources; the pair is the key.
--
-- THE SOURCE CARRIES THE ERA, and the era filter reads it here rather than on
-- the item. An item introduced in EoF that also drops off a RoK named is RoK
-- content for somebody planning RoK, and filing it by where it FIRST appeared
-- would hide it from the only reader asking. `plan_items.era` stays as the
-- expansion it was introduced in, which is a different fact and is displayed
-- rather than filtered on.
CREATE TABLE IF NOT EXISTS plan_sources (
  page_title TEXT NOT NULL REFERENCES plan_items(page_title) ON DELETE CASCADE,
  source_page TEXT NOT NULL,              -- the mob or quest page it came from
  source TEXT NOT NULL,                   -- what to call it
  kind TEXT NOT NULL,                     -- raid|group|solo|quest|unknown
  era TEXT NOT NULL,                      -- rok|eof — the SOURCE's expansion
  zone TEXT,
  level INTEGER,
  detail TEXT,                            -- the wiki's own `diff` wording
  PRIMARY KEY (page_title, source_page)
);
CREATE INDEX IF NOT EXISTS idx_plan_sources_era ON plan_sources(era, kind);
CREATE INDEX IF NOT EXISTS idx_plan_sources_page ON plan_sources(page_title);

-- THE ITEM IS NOT THE UNIT OF VALUE. In EoF and RoK the set bonus is not on
-- the armour: it is on a turquoise adornment that ships inside the item and
-- can be pulled out and moved into anything of the same level or higher. So a
-- set is its own row and is shortlisted on its own terms — "this Fabled has
-- mediocre stats but carries the 6-piece turquoise" is a real answer, and for
-- somebody deciding what to bid on it is a different answer from "upgrade".
CREATE TABLE IF NOT EXISTS plan_sets (
  name TEXT PRIMARY KEY,
  page_title TEXT NOT NULL,
  era TEXT NOT NULL,
  level INTEGER,
  pieces_json TEXT NOT NULL,              -- ["Focused Mind Set: Chest", …]
  bonuses_json TEXT NOT NULL,             -- [{"pieces":3,"text":"Applies …"}]
  fetched_ts INTEGER NOT NULL
);

-- What the last crawl covered, per era, so the page can say how old its
-- catalog is and the ingest can report rather than guess. One row per era.
CREATE TABLE IF NOT EXISTS plan_syncs (
  era TEXT PRIMARY KEY,
  items INTEGER NOT NULL DEFAULT 0,
  sources INTEGER NOT NULL DEFAULT 0,
  sets INTEGER NOT NULL DEFAULT 0,
  quests INTEGER NOT NULL DEFAULT 0,
  edges INTEGER NOT NULL DEFAULT 0,
  pages INTEGER NOT NULL DEFAULT 0,       -- wiki pages fetched
  synced_ts INTEGER NOT NULL
);

-- v43: a character looked up BY NAME on /plan, with no account behind it.
--
-- The Planner needs no account and neither should trying your own gear on it —
-- the whole point of the loadout is to mess about with a toon before chasing
-- anything, and making that the one part that needs signing up is backwards.
--
-- **This is a CACHE of a public Census record, not a character.** No user_id,
-- no snapshots, no history, nothing that could become somebody's account
-- state: `characters` remains the only owned thing and this table can be
-- dropped without losing anything a person typed. It exists so the lookup
-- survives Census being down (which is normal and comes and goes by time of
-- day) and so a reader refreshing the page does not re-ask Census.
CREATE TABLE IF NOT EXISTS plan_characters (
  name_lower TEXT NOT NULL,
  world_id INTEGER NOT NULL,
  name TEXT NOT NULL,                     -- as Census spells it
  doc_json TEXT,                          -- NULL when Census has no such name
  fetched_ts INTEGER NOT NULL,
  PRIMARY KEY (name_lower, world_id)
);

-- v44: item records recovered from EQ2 Lexicon when Census supplied the
-- equipped ids but its item collection was unavailable.  Keep provenance
-- separate: Census always wins when it has a row, while this durable cache
-- prevents every character-page read from becoming a request to Lexicon.
-- `complete=0` is the name/icon-only character response; a later bounded
-- enrichment may replace it with `/api/item/<id>`'s full examine data.
CREATE TABLE IF NOT EXISTS lexicon_items (
  item_id INTEGER PRIMARY KEY,
  name TEXT,
  tier TEXT,
  json TEXT NOT NULL,
  complete INTEGER NOT NULL DEFAULT 0,
  fetched_ts INTEGER NOT NULL
);

-- v42: the Planner's OUTLINE (Phase 2, docs/planner.md). The crawl already
-- read every quest page in the era to find its rewards and threw the rest
-- away; these two tables are what it keeps. Same category of row as the
-- catalog above — reference data about the game, no account, no parse.
--
-- The quest is stored whether or not it rewards anything, because a quest with
-- no gear on it is still the step that unlocks the one that does.
CREATE TABLE IF NOT EXISTS plan_quests (
  page_title TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  era TEXT NOT NULL,                      -- rok|eof, the category it came from
  level INTEGER,                          -- NULL when the quest SCALES
  level_text TEXT,                        -- the journal value as written
  zone TEXT,                              -- szone: where you pick it up
  timeline TEXT,                          -- the wiki's own grouping
  jcat TEXT,                              -- the journal category
  diff TEXT,                              -- the wiki's `diff` wording
  kind TEXT NOT NULL,                     -- raid|group|solo|unknown
  fetched_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_quests_era ON plan_quests(era, level);

-- TWO QUESTS IN ONE CHAIN CANNOT BE WORKED AT THE SAME TIME, and this is the
-- only record of that. Both directions of the wiki's own fields land here as
-- one edge set: `prereq`/`prelist` on the later quest and `next`/`nextlist`
-- on the earlier one describe the same edge, and the wiki fills them
-- independently, so reading both closes gaps neither has alone.
--
-- `kind` is HARD or ENABLE and they are different claims: hard says you
-- cannot, enable says it gets much cheaper (travel, a language, a key item).
-- Only hard edges come off a template — an enablement edge is stated in prose
-- and is layer 2, so nothing writes one yet.
--
-- `or_group` is non-zero when the edges sharing it are ALTERNATIVES: any one
-- of them satisfies the requirement. Kunark's prerequisites really are
-- disjunctive and retrofitting OR-nodes after every consumer assumes a flat
-- list would touch everything.
CREATE TABLE IF NOT EXISTS plan_quest_edges (
  from_page TEXT NOT NULL,                -- what you do first
  to_page TEXT NOT NULL,                  -- what it opens
  era TEXT NOT NULL,                      -- the crawl that produced it
  kind TEXT NOT NULL DEFAULT 'hard',      -- hard|enable
  or_group INTEGER NOT NULL DEFAULT 0,    -- >0: alternatives for the same to_page
  PRIMARY KEY (from_page, to_page, kind)
);
CREATE INDEX IF NOT EXISTS idx_plan_quest_edges_to ON plan_quest_edges(to_page);
CREATE INDEX IF NOT EXISTS idx_plan_quest_edges_era ON plan_quest_edges(era);

-- v46: wikq2's structured reading of the 24 original class epic timelines.
-- This is one offline synchronization boundary: wikq2 decides which links are
-- requirements and which are the ordered heroic/raid chain; the Planner stores
-- that exact result instead of independently interpreting the same wiki prose.
CREATE TABLE IF NOT EXISTS plan_epic_timelines (
  title TEXT PRIMARY KEY,
  class_name TEXT NOT NULL UNIQUE,
  quests_json TEXT NOT NULL,
  requirements_json TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source_version INTEGER NOT NULL,
  fetched_ts INTEGER NOT NULL
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


def _rebuild_planner(conn) -> None:
    """v40: the Planner's tables are a CACHE of a wiki crawl — no account, no
    parse, nothing anybody typed — so a table of the wrong shape is DROPPED and
    refilled by re-running `tools/sync_planner.py`, rather than migrated. That
    is only ever reachable on a box where the dev reloader created the table
    mid-edit; a shipped database has never had these tables at all.

    Runs BEFORE `executescript(SCHEMA)`, because the failure it repairs is the
    CREATE INDEX in that script naming a column the stale table lacks."""
    for table, needed in (("plan_items", "era"), ("plan_sources", "era"),
                          ("plan_sets", "era"), ("plan_syncs", "pages"),
                          ("plan_quests", "kind"), ("plan_quest_edges", "or_group")):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if cols and needed not in cols:
            conn.execute(f"DROP TABLE {table}")


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
    PARSESHOTS_DIR.mkdir(parents=True, exist_ok=True)
    NOTESHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    _rebuild_users(conn)
    _rebuild_characters(conn)
    _rebuild_sessions(conn)
    _rebuild_planner(conn)
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
        if "redacted_lines" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN "
                         "redacted_lines INTEGER NOT NULL DEFAULT 0")
        enc_cols = {r[1] for r in conn.execute("PRAGMA table_info(encounters)")}
        if "zone_run_id" not in enc_cols:
            conn.execute("ALTER TABLE encounters ADD COLUMN zone_run_id INTEGER")
        if "dup_of" not in enc_cols:
            conn.execute("ALTER TABLE encounters ADD COLUMN dup_of INTEGER")
        if "deleted_ts" not in enc_cols:
            conn.execute("ALTER TABLE encounters ADD COLUMN deleted_ts INTEGER")
        if "hidden_ts" not in enc_cols:
            conn.execute("ALTER TABLE encounters ADD COLUMN hidden_ts INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_encounters_run ON encounters(zone_run_id)")
        # hidden_count starts at 0 on every existing row, which is the truth
        # (there are no `hide` edits yet); the startup relink recomputes it
        run_cols = {r[1] for r in conn.execute("PRAGMA table_info(zone_runs)")}
        if "hidden_count" not in run_cols:
            conn.execute("ALTER TABLE zone_runs ADD COLUMN "
                         "hidden_count INTEGER NOT NULL DEFAULT 0")
        if "is_raid" not in run_cols:
            # NULL preserves the old roster-size answer until the startup
            # relink has classified every existing run from zone/target facts.
            conn.execute("ALTER TABLE zone_runs ADD COLUMN is_raid INTEGER")
        # v27 shipped `imported_parses` before it kept the picture; the columns
        # are added by shape so a database created in between gets them too.
        # NULL means "no image", which is what those rows honestly are.
        shot_cols = {r[1] for r in conn.execute("PRAGMA table_info(imported_parses)")}
        for col, typ in (("image_name", "TEXT"), ("thumb_name", "TEXT"),
                         ("image_w", "INTEGER"), ("image_h", "INTEGER"),
                         ("image_bytes", "INTEGER")):
            if shot_cols and col not in shot_cols:
                conn.execute(f"ALTER TABLE imported_parses ADD COLUMN {col} {typ}")
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
        # v22: the verdict/candidate split. The old rows carried machine guesses
        # in `unit`/`proc`; `catalog.reset_verdicts` demotes them into the new
        # candidate columns at startup, so nothing that was learned is lost —
        # it just stops being a claim.
        if "pet_seen" not in cat_cols:
            conn.execute("ALTER TABLE ability_catalog ADD COLUMN "
                         "pet_seen INTEGER NOT NULL DEFAULT 0")
        if "proc_candidate" not in cat_cols:
            conn.execute("ALTER TABLE ability_catalog ADD COLUMN "
                         "proc_candidate INTEGER NOT NULL DEFAULT 0")
        if "proc_class" not in cat_cols:
            conn.execute("ALTER TABLE ability_catalog ADD COLUMN proc_class TEXT")
        # v23b: wiki_abilities was first keyed on `name` alone, which let the
        # deity sync overwrite 37 AAs of the same name (and hand the fury spell
        # `Tempest` to Karana). It is pure re-syncable cache, so the repair is
        # to drop it and let `tools/sync_wiki.py` refill — no data is lost that
        # a re-sync cannot rebuild. Detected by shape, like the rest.
        wiki_pk = [r[5] for r in conn.execute("PRAGMA table_info(wiki_abilities)")]
        if wiki_pk and sum(1 for p in wiki_pk if p) < 2:
            conn.execute("DROP TABLE wiki_abilities")
            conn.executescript(SCHEMA)
        # v10: intercepts + the press ("adjusted delay") columns. Added by
        # shape like the rest — the rows themselves are rebuilt by the
        # PARSE_VERSION sweep, so the defaults only live until the reparse.
        actor_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(encounter_actor_stats)")}
        # v47 rides along: how many of a row's deaths the log never announced
        # and the site recovered from the hole they left (pipeline/downs.py).
        for col in ("intercepts", "presses", "press_span_s", "deaths_inferred"):
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
        # v19: `roster_classes` — every raider's class from Census, not only the
        # ones with an account here. Created by the CREATE TABLE IF NOT EXISTS
        # above; it starts empty and fills in the background, and an empty
        # table just means inference answers the way it did before.
        # v20: a raid is tagged with the guild its roster mostly belongs to.
        # `guild_checked` is the tri-state that keeps the vote honest: 0 means
        # nobody ever asked (every pre-v20 row, and the backfill queue), 1 with
        # a NULL `guild_name` means Census answered and the character is in no
        # guild. Only a 1 gets a vote — treating "unknown" as "guildless" would
        # put somebody else's guild on the raid, or strip a real one.
        rc_cols = {r[1] for r in conn.execute("PRAGMA table_info(roster_classes)")}
        for col, typ in (("guild_name", "TEXT"), ("guild_id", "INTEGER"),
                         ("guild_checked", "INTEGER NOT NULL DEFAULT 0")):
            if col not in rc_cols:
                conn.execute(f"ALTER TABLE roster_classes ADD COLUMN {col} {typ}")
        # and the tag itself. NULL means "no majority" as well as "not computed
        # yet" — the two are the same to every reader, and retagging is pure SQL
        # over cached rows, so every pass just recomputes the lot.
        if "guild" not in run_cols:
            conn.execute("ALTER TABLE zone_runs ADD COLUMN guild TEXT")
        # v21: `guild_shares` — the second standing-share branch. A user connects
        # one of their own guild tags to a group and their uploads carry it, so
        # a new character needs no new rule. Created by the CREATE TABLE IF NOT
        # EXISTS above and nothing else: it is a new table, not a column, and an
        # empty one reads exactly like the pre-v21 behaviour. The match is on the
        # UPLOADER's character's Census guild — never the run's majority-vote
        # tag, which is a derived property of a raid and not a decision anybody
        # made about their own logs.
        # v25: `feedback` — bug reports and suggestions filed from the site,
        # triaged on the admin console. New table, same reasoning as v21: an
        # empty one reads exactly like the pre-v25 behaviour.
        # v27: `imported_parses` — a parse read back off an ACT screenshot.
        # New table again, and deliberately a table of its OWN: it holds
        # claims about somebody else's night, so nothing that aggregates real
        # sessions can reach it by accident.
        # v28: `raid_notes` + `raid_note_shots` — what the dashboard writes
        # down mid-raid, keyed by zone and named rather than by encounter.
        # Two more new tables: nothing existing reads them, so an old database
        # gets them empty and behaves exactly as it did.
        # v29: `overlay_tokens` — the stream overlay's URL credential. Same
        # reasoning again: new table, nothing else reads it.
        # v30: which plugin build each pairing is running, so the site can
        # offer an update to the people who need one and nobody else. NULL is
        # "never told us", which every pre-v30 row is until its next batch —
        # and NULL never produces a pill, because "unknown" is not "old".
        if "client_version" not in token_cols:
            conn.execute("ALTER TABLE device_tokens ADD COLUMN client_version TEXT")
        # v31: `run_dismissals` — a raid shared with you, off YOUR list. New
        # table again: an empty one reads exactly like the pre-v31 behaviour,
        # and it is read beside the visibility predicate rather than inside it
        # (groups.LISTED_RUN_IDS), so it can never widen what anybody can see.
        # v32 shape guard: `qty` landed after the table did, and on the dev box
        # the reloader had already run the CREATE. Guarded by SHAPE like every
        # other column here — a database that got the table before this column
        # existed is otherwise indistinguishable from an up-to-date one.
        loot_cols = {r[1] for r in conn.execute("PRAGMA table_info(loot_drops)")}
        if loot_cols and "qty" not in loot_cols:
            conn.execute("ALTER TABLE loot_drops ADD COLUMN qty INTEGER NOT NULL DEFAULT 1")
        if loot_cols and "rolls_json" not in loot_cols:
            conn.execute("ALTER TABLE loot_drops ADD COLUMN rolls_json TEXT")
        item_cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        for col in ("stats_json", "effects_json"):
            if item_cols and col not in item_cols:
                conn.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
        # v32: `loot_drops` + `items` — what the chests gave the raid, and the
        # display record for the items they gave. Two new tables and no column
        # anywhere else: nothing that existed reads them, an old database gets
        # them empty, and no stat, roster or segment changes shape. History is
        # filled by `tools/backfill_loot.py` re-reading stored raw, which is why
        # this needs no PARSE_VERSION bump — loot is written BESIDE the parse,
        # never inside its arithmetic.
        # v33: `aoe_cycles` — one watched enemy recast per row, with whether a
        # reuse debuff was on the mob when it started. New table, so an old
        # database gets it empty and every consumer falls back to exactly the
        # pre-v33 behaviour: no cycles is no learned timer, and no learned
        # timer is ACT's list, which is what the countdown used before.
        # It fills from the PARSE_VERSION sweep rather than a backfill script —
        # the cycles come out of the same segmentation the rollups do, so the
        # reparse that stamps the new version writes them on its way past.
        # `duration_s` on wiki_abilities lands with it, guarded by SHAPE: the
        # column carries how long an effect lasts, which is what makes a debuff
        # window out of the single damage line that is all a debuff ever prints.
        wiki_cols = {r[1] for r in conn.execute("PRAGMA table_info(wiki_abilities)")}
        if wiki_cols and "duration_s" not in wiki_cols:
            conn.execute("ALTER TABLE wiki_abilities ADD COLUMN duration_s REAL")
        # v34: `overlay_tokens.kind` — the same capability pointed at a second
        # screen (EQ2's own browser window). Guarded by SHAPE like every other
        # column here, and the DEFAULT is what makes it a no-op for anybody
        # already holding a link: every existing row is an `overlay`, which is
        # the only kind that existed, and the page it serves is unchanged.
        ovl_cols = {r[1] for r in conn.execute("PRAGMA table_info(overlay_tokens)")}
        if ovl_cols and "kind" not in ovl_cols:
            conn.execute("ALTER TABLE overlay_tokens ADD COLUMN kind TEXT "
                         "NOT NULL DEFAULT 'overlay'")
        # v35: `user_marks` — the joust and mini marks, on the account instead
        # of in one browser's localStorage. New table, so an old database gets
        # it empty and every reader falls back to exactly the pre-v35 answer:
        # no row is "nothing said", and nothing said is the ACT-list default.
        # It is not backfilled and cannot be — what is in a raider's
        # localStorage is only reachable from that browser — so the SPA adopts
        # what it finds there on the first signed-in read (`lib/marks.js:
        # syncMarks`), which is the only place those answers exist.
        # v36: `chat_messages` — /chat stopped being a relay with a few hours of
        # memory and became the record. New table, so an old database gets it
        # empty and the page simply starts its archive from the first line
        # relayed after the upgrade; there is nothing to backfill, because the
        # thing this replaces was never written down.
        # v37: `visit_days` + `visit_salts` — counting readers. New tables, and
        # nothing before the upgrade can be recovered: no log of who asked for a
        # page has ever been kept here, which is the same reason the chat
        # archive could not be backfilled. The count starts at the upgrade.
        # v38: operational admin state and curator timer rulings. The new
        # tables come from SCHEMA; feedback's optional workflow fields need a
        # shape migration for existing databases.
        fb_cols = {r[1] for r in conn.execute("PRAGMA table_info(feedback)")}
        if "assignee_user_id" not in fb_cols:
            conn.execute("ALTER TABLE feedback ADD COLUMN assignee_user_id INTEGER")
        if "admin_note" not in fb_cols:
            conn.execute("ALTER TABLE feedback ADD COLUMN admin_note TEXT")
        # v39: Discord links, alert rules and the delivery outbox are all new
        # tables. An old database receives them empty from SCHEMA, which means
        # exactly what it did before: no account is linked and no message can
        # match a rule. Nothing can or should be backfilled from chat history.
        # v40: the Planner's catalog — `plan_items`, `plan_sources`,
        # `plan_sets`, `plan_syncs`. Four new tables and no column anywhere
        # else, so an old database gets them empty and every existing page
        # behaves exactly as it did. There is nothing to backfill and nothing
        # that COULD be: the rows come from a wiki crawl, not from anybody's
        # log, so `tools/sync_planner.py` fills them and an empty catalog is
        # simply a Planner with no era synced yet.
        # v41: `zone_runs.is_raid` separates raid CONTENT from attendance.
        # Shape-guarded above; NULL falls back to the former seven-raider rule
        # only until the ordinary startup relink writes the reference answer.
        # v42: the Planner's outline — `plan_quests`, `plan_quest_edges`. Two
        # new tables, empty until the next hand-run `tools/sync_planner.py`;
        # until then the Outline tab shows the hand-curated prelude alone,
        # which is correct and useful on its own. `plan_syncs` gains two
        # counters, and that one is an ALTER rather than a rebuild: the row is
        # the record of WHEN an era was crawled and there is no reason to lose
        # it to a column that counts something new.
        sync_cols = {r[1] for r in conn.execute("PRAGMA table_info(plan_syncs)")}
        if sync_cols and "quests" not in sync_cols:
            conn.execute("ALTER TABLE plan_syncs ADD COLUMN quests "
                         "INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE plan_syncs ADD COLUMN edges "
                         "INTEGER NOT NULL DEFAULT 0")
        # v43: `plan_characters`, the by-name Census lookup behind /plan's
        # loadout. A new table and nothing else — `CREATE TABLE IF NOT EXISTS`
        # in SCHEMA is the whole migration, and an empty one is a Planner whose
        # loadout has simply not been asked about anybody yet.
        # v44: `lexicon_items` is a separate fallback cache for worn item and
        # adornment ids Census could not resolve. It is intentionally not a
        # column on `census_items`: source precedence must remain explicit and
        # a later Census answer must supersede the fallback without a rewrite.
        # v45: `planner_saved_sets` gives every account five renameable gear
        # builds. It is a new table, so an old database receives five implicit
        # empty defaults and no existing Planner state changes.
        # v46: `plan_epic_timelines` is wikq2's structured epic prerequisite
        # and quest-chain export. It is an offline cache and starts empty until
        # the next Planner sync, like the rest of the catalog.
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
