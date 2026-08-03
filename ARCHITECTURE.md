# eq2advanced — Architecture

Raid-coaching web app for EverQuest II TLE (Wuoshi, EoF era). Core idea:
*Census says what an ability should do at your stats; the parse says what it
actually did; coaching lives in the gap.*

## Runtime

1. Cloudflare DNS for `eq2advanced.jupiterns.org`
2. Zoraxy on `10.1.1.4:8000`
3. **Live** target: Docker container on `10.1.1.5:8450` (Lindsay deploys it —
   `ghcr.io/improvmasta/eq2advanced:main`, built on push to main)
4. **Dev** site: this box, `http://10.1.1.15:8450` via `restart.sh`

## Stack

- **Backend**: FastAPI + SQLite (WAL) in `backend/`; uvicorn on `${PORT:-8450}`,
  binds `${HOST:-0.0.0.0}`.
- **Frontend**: Vite + React SPA in `frontend/`, built to `frontend/dist`,
  served by the API process. UI dev: `npm --prefix frontend run dev` (:5173
  proxies `/api` to :8450). Theme: eq2lexicon-sibling dark + warm-parchment
  light, toggle in the top nav.
- **Data**: `DATA_DIR` (default `./data`; container mounts `/data`) —
  `eq2advanced.db`, `uploads/` (gzipped raw logs by sha256), `raw/` (reserved
  for live ingest batches).

## Backend layout

- `parser/` — pure streaming log parser, no DB. `prefix.py` (epoch prefix,
  CRLF, comma/`296.1K` amounts), `subjects.py` (subject model — see below),
  `classify.py` (ordered regex chain), `events.py` (dataclasses + flag bits),
  `flavor/` (prepare-line → ability resolution, see "Cast ground truth").
  `parse_lines(lines, logger)` is the single entry point for bulk files AND
  live batches; it also collapses the client's same-second duplicate prepare
  lines (234 of 918 in bobby.txt are exact dupes).
- `pipeline/` — `encounters.py` (gap segmentation + named labels),
  `statsroll.py` (per-encounter actor/ability rollups + HP-deficit healer
  estimates; `ACTOR_INSERT`/`actor_rows` is the one place the actor-stats
  column order lives), `ingest_writer.py` (the one write path: entity/ability
  resolution, events + rollups in one transaction, session status),
  `prune.py` (events retention, see "Pruning").
- `routers/` — `uploads_api` (multipart → sha256-deduped gzip → background
  parse thread), `sessions_api`, `encounters_api`, `auth_api` (open sign-up +
  cookie login; the FIRST registered account becomes admin), `characters_api`
  (CRUD; creating a name that exists unowned from phase-1 uploads CLAIMS it),
  `tokens_api` (per-character device tokens: mint shown-once, QR pair payload,
  revoke; the ACT plugin authenticates with these in phase 3).
- `auth.py` (PBKDF2 password + hashed session/device tokens) and `security.py`
  (`require_user`/`require_admin` deps + `owned_character`). Isolation rule:
  every session/encounter query is scoped through `characters.user_id`; admin
  sees everything; foreign ids 404 (not 403) so existence doesn't leak.
- `pipeline/live.py` + `routers/ingest_api.py` — live ingest (see below).
- `census/` + `routers/census_api.py` — Census sync (see below);
  `census/catalog.py` populates `ability_catalog` (see "Ability catalog").
- `coach/` + `routers/coach_api.py` — coach engine + raid report (see below).

## Live ingest (phase 3 — the frozen ACT-DLL contract)

`GET /api/ingest/hello`, `POST /api/ingest/batch`, `POST /api/ingest/backfill/done`;
auth is `Authorization: Bearer <device_token>` only. A batch is gzip (or plain)
JSON `{batch_id, mode: live|backfill, lines: [verbatim lines]}` → `{accepted,
duplicates, session_id}`. `backend/tools/simulate_live.py` is the reference
client the DLL mirrors (and feeds the equivalence test's batch cutter).

Design points, in the order they bit:

- **Batch idempotency**: response stored per `(token, batch_id)` in
  `ingest_batches`; a resend replays the stored answer. One batch in flight per
  token (429 + Retry-After).
- **Line dedupe**: `ingest_lines` key = sha256 of `(occurrence-ordinal-within-
  batch, line)`. Identical legit lines in one batch get distinct ordinals (two
  identical hits in the same second both count); a backfill/re-upload overlap
  carries the same ordinals and drops cleanly. Corollary: batches must be cut
  on log-second boundaries so a second never splits across batches — the
  simulator's `--window` does this and the DLL must too.
- **Incremental finalization is a view, not the record.** Per-session in-memory
  tail (`pipeline/live.py`); each batch re-segments the tail and flushes any
  encounter that can no longer change (a later segment exists, or GAP+grace =
  35s of log time passed) to the normal tables — that's what the Live page's
  SSE cards read. At close (`done`, or 30-min staleness) the whole session is
  **rebuilt from its raw chunks through `parse_session`** — the exact bulk
  path — so a finished live session is provably identical to uploading the
  file (guarded by `test_golden_equivalence`: encounters, actor stats, ability
  stats all byte-equal on bobby.txt; encounter ids change at rebuild, which is
  why the Live page refetches on `status: ready`).
- **Restart-safe by construction**: the in-memory tail is disposable; raw
  chunks + `ingest_lines` survive, and the close-time rebuild reparses raw.
- SSE: `GET /api/sessions/{id}/stream` (cookie auth) polls the DB ~1.5s and
  pushes `encounter` cards + `status` heartbeats (incl. uploader-online from
  `device_tokens.last_seen_ts`); closes at ready/error.

## Census sync (phase 4)

`census/client.py` (HTTP, retrying — Census reads regularly stall; service id
from `CENSUS_SERVICE_ID`, default `s:example`), `census/sync.py` (the sync +
display payloads), `census/effects.py` (effect_list grammar → structured
effects), `routers/census_api.py` (summary / refresh / snapshot history /
spell detail). Query shapes verified live 2026-08-02 — see client.py docstring.

- `sync_character()` is the ONE entry point (manual Refresh + hourly loop in
  `main.py`, which syncs owned characters >24h stale; gate with
  `CENSUS_AUTO_REFRESH=0` — tests set it in conftest.py). It fetches the
  character doc by `name.first_lower` + `locationdata.worldid=618`, updates
  `characters.class/level/census_character_id`, snapshots a TRIMMED doc
  (identity/stats/resists/gear/spell_list/AA points — not quests/collections,
  which are huge), and fills the `census_spells` / `census_items` caches for
  any scribed-spell / equipped-item ids not yet cached.
- **Snapshots only when Census's own `last_update` moved** — so history rows
  mean "the character actually changed" and the diff endpoint (stats, gear
  slot swaps, spell tier changes computed against the previous snapshot) stays
  meaningful. Manual refresh has a 60s cooldown.
- **Damage numbers exist only in effect_list text** ("Inflicts 33 - 45 disease
  damage on target instantly and every second."). `effects.py` parses that
  grammar (damage/heal/ward/power/stat/proc, ranges, %-of-health, tick
  period); anything unrecognized is kept verbatim as kind `other` so phase 5
  can only under-use a spell, never misread it. Typed spell fields
  (`cast_secs_hundredths`, `recast_secs`, `duration.*_sec_tenths`) are
  reliable — EXCEPT `recovery_secs_tenths`, which stores HUNDREDTHS despite
  the name (every spell carries 50 = the universal 0.5s recovery; dividing by
  10 gave 5s and clamped idle% to 0 — found on the real raid night, phase 6).
- The character doc's typed stats carry everything coaching needs (verified on
  Bobby: `combat.abilitymod` 1442, `basemodifier` 68.1, `critchance` 53.5,
  `ability.spelltimereusepct`/`spelltimecastpct`) — no text parsing there.
- Tests (`test_census.py`) run entirely from recorded fixtures in
  `tests/fixtures/census/` (trimmed real responses for Bobby) via a fake
  injected as `census.client._shared` — no live Census calls in CI.

### Bulk spell ingest (phase 7 groundwork)

Census spell records are BASE, pre-stat values per tier (App1–Celestial share a
`crc`) — no wiki scrape or manual entry needed; in-game tooltips are these
numbers with the player's stats applied, which is exactly `fit.py`'s damage
model. `tools/ingest_spells.py --all --max-level 70` proactively caches the
full class books via `sync.ingest_class_spells()` →
`client.spells_by_class()` (query `classes.<cls>.level=[<max>`, lowercase
class keys, `[` = Census's <= operator, `c:start` paging — verified live
2026-08-02, wizard <=70 = 1152 records; `sync.ALL_CLASSES` holds the 24
EoF-era names). Marks `spell_line:{crc}` so upgrade advice never refetches.
`census_spells` carries typed columns (`cast_s/recast_s/recovery_s/duration_s/
power_cost/dmg_min/dmg_max/dmg_dtype/dmg_period_s`, schema v4) populated on
insert by `sync.typed_fields()` — the ONE owner of the unit conversions
(including the recovery-hundredths gotcha; `fit.spellbook()` uses it too).
`dmg_*` is the primary damage effect = largest midpoint (DoT initial hit over
its tick). `sync.backfill_typed_columns()` repairs pre-migration rows;
`spell_overrides` remains the manual escape hatch where Census text is wrong.
GOTCHA: the default `s:example` service id gets burst-throttled hard on bulk
pulls ("Missing Service ID" error after ~10 full-record requests — LESS than
one class book; it also silently CLAMPS `c:limit` to 100, so paging advances
by `len(rows)` and stops only on an empty page — a short page proves nothing).
`ingest_class_spells` therefore persists every page as it lands and stores the
offset in settings (`ingest_progress:{cls}:{max_level}`): an interrupted class
RESUMES mid-book instead of restarting from zero (a from-zero fetch could
never outrun the burst budget). Only the empty end-page clears the offset and
writes the `spell_line:{crc}` completeness markers. The tool paces 30s/page on
`s:example`; a registered service id (census.daybreakgames.com signup) in
`.env` as `CENSUS_SERVICE_ID` removes the limit — start.sh and the ingest
tool both load `.env`.

## Coach engine (phase 5)

`coach/` — `descriptive.py` (session currencies: DPS/crit/autoattack share,
cast estimates, idle-GCD estimate, cure latency, rez responsiveness),
`fit.py` (observed vs Census), `replay.py` (what-if stat marginals + tier
upgrades), `advisor.py` (report assembly, persisted to `coach_reports`),
`raidreport.py`. API: `GET|POST /api/sessions/{id}/coach`,
`GET /api/sessions/{id}/raid-report`, `POST /api/sessions/{id}/calibration`.
Pages: Coach, RaidReport, Calibration.

- **Census is a prior, the parse is the evidence.** Damage model (EoF era):
  `expected = base_mid*(1+basemod/100) + min(abilitymod, base_mid/2)`; the fit
  reconciles observed non-crit means into a per-ability coefficient
  (observed/expected — ~2-4.6x on real TLE data, Census drifts a LOT) and all
  what-if math scales through it. Crit multiplier fitted per ability
  (crit mean / non-crit mean), default 1.3. Multi-effect spells: each hit is
  assigned to the nearest expected effect and the biggest cluster is fitted —
  we under-use a spell rather than misread it.
- **Stat marginals are differences of `predicted_damage`**, which is monotone
  in every stat by construction. Reuse only pays on cooldown-locked abilities
  (median inter-cast gap ≤ 1.25× effective recast); cast speed only converts
  to damage when the rotation isn't idle.
- **Tier upgrades cap at Master (tier 9)** — Ancient/Celestial don't exist on
  TLE and Grandmaster is a class choice, not a scroll. Spell lines are cached
  via `ensure_spell_lines`; Census's comma OR-list works for `id=` but
  silently returns NOTHING for `crc=` (verified live 2026-08-02), so
  `spells_by_crcs` is one request per crc, marker-gated in `settings`.
- **Spellbook join**: log base name → the character's highest scribed version
  at or below their level (Bobby has above-level RoK pre-scribes), overrides
  from `spell_overrides` applied on top.
- **Calibration**: a session flagged via `POST /calibration` (auto-pins) is
  per-ability ground truth — its coefficients override every later report for
  that character (confidence `calibrated`). Uses current snapshot stats, so
  recalibrate after big gear changes.
- Report degrades gracefully with no Census snapshot (currencies + findings,
  `no_census` finding); a Census outage only costs the tier-upgrade section.

### Coach correctness (phase 6, 2026-08-02) — what fixed the v1 debt

Lindsay's v1 verdict was "good base, far from correct"; phase 6 attacked that
list in dependency order. What changed:

1. **Cast ground truth** (`parser/flavor/`): `You prepare` lines resolve to
   ability names — generic article-strip ("the Bloodcloud" → Bloodcloud) +
   `to inflict X on <tgt>` + per-class prose maps (`necromancer.py`, verified
   on bobby.txt; every fixture flavor resolves). CRITICAL WRINKLE: only spells
   with a cast bar print prepare lines — instant casts (Lifetap, 344 hits,
   zero prepares) never do. Discriminator in `descriptive.py`: prepared →
   real count; in the spellbook but never prepared → real instant spell,
   initiation-estimated; in neither → buff/item proc, ZERO casts (Lich's
   Siphoning et al. stop polluting idle%). `currencies.cast_source` says
   which mode ran; `casts` also lands on the damage-kind rollup row.
2. **Two-point calibration** (`fit._solve_two_point`): dummy parses at two
   abmod values (≥100 apart; stats are CAPTURED per session at flag time in
   `sessions.calib_stats_json`) solve the TRUE base piecewise
   (uncapped/capped/mixed hypotheses, 10% tolerance). With a solve, the fit
   row swaps Census base for truth (`base_source: calibrated2`) — the abmod
   cap in every marginal becomes real. Until a second point exists the report
   carries a `calibration_second_point` finding.
3. **k-spread = debuff measurement** (`fit.apply_calibration`): the dummy fit
   NEVER overwrites a healthy raid fit — `k_dummy` rides alongside and
   `debuff_uplift = raid_k / dummy_k` per ability, medianed per damage school
   into `report.debuff_uplift`. Dummy k substitutes only when the session
   sample is thin (<5 non-crits).
4. **Ability catalog** (`census/catalog.py`): populated from every cached
   census spell (name + base_name, class, unit=player) and — via the
   effect-grammar `proc` kind ("may cast X on ...") — the proc'd names get
   `proc=1`. Curated rows (pet kits + observed buff/item procs from
   bobby.txt) always win over census rows. Consumers: `fit.spellbook` drops
   pet-kit names from the player join; a **k sanity gate** (0.2–12; Master's
   Strike misjoined at 54.6) marks the rest `suspect_join` → excluded from
   marginals/upgrades, surfaced as a finding.
5. **Healer/utility currencies**: HP-deficit reconstruction in `statsroll`
   (full HP assumed at pull; wards never touch HP) → `overheal_est` +
   `save_count` (heal landing at ≥60% of the target's worst deficit) +
   `ward_bleedthrough`, persisted per actor and rolled into raid report +
   coach findings. Logger-only debuff uptime (`descriptive._debuff_uptime`):
   real cast starts + Census durations vs burn windows (rolling 10s raid
   damage ≥1.5× encounter mean). All flagged as estimates in the UI.
6. **Engagement classifier v2** (`raidreport`): catalog-proc abilities never
   anchor; inside the opening 2s an ability that fires ≤1s after being hit is
   a reactive proc (skipped); the logger's own prepare line is an exact
   high-confidence anchor (`anchor: cast`); remainder keeps the low-confidence
   flag.

Still open (smaller, unchanged from v1 item 7): rez/time-dead next-action
proxies, DoT tier-upgrade tick tail, reuse marginal rotation displacement.
The abmod marginal is only as good as the calibration points backing it —
Lindsay still needs to RUN the two dummy parses.

## Raid Report (phase 5)

`coach/raidreport.py`, computed on demand from stored events (no schema
change). Per encounter + per night, all raiders in the log: damage/share/DPS,
deaths, time dead (death → next own action), **death DPS cost** (alive-DPS ×
time dead), cures delivered, rez delay, heals/wards/power.

**Engagement timing with the proc caveat** (verified on the Zylphax pull —
pre-pull wards/procs flood the log ~1s after the real opener): ward absorbs
and heals are never engagement anchors; anchors are damage/hostile-threat
lines; an *ability* first-action inside the opening 2s is flagged
low-confidence (possible buff proc), autoattack and pet swings are always
deliberate. Night rollup averages named-fight delays only.

## The subject model (the crux — verified against a real raid log)

- `YOU` / `YOUR <Ability>` = the logging player, always.
- **Bare logger-name = the logger's own pet** (pets can share the owner's exact
  name). `<logger>'s <Ability>` = pet ability.
- Other single-token capitalized names = players (their pet conflates into
  them — community parse convention; fixed properly when they upload their own
  logs).
- `<Owner>'s <lowercase pet>['s <Ability>]` = swarm-pet chain.
- Possessive hazards: `Aros' Soulrot` (ability) vs `Aros's blighted horde`
  (pet); never split inside a capitalized remainder (`Autumn's Kiss`,
  `Treyloth D'Kulvith's Bloodcoil`).
- `focus` dtype = self-inflicted (Vampiric Requiem) — excluded from DPS,
  rolled up as ability kind `self`.
- Logger deaths: `<Killer> has killed you.` + `You regain consciousness!`.

Parser truth source: `backend/tests/test_parser.py` (verbatim log lines) and
`backend/tests/test_golden.py` (full /home/lindsay/bobby.txt fixture: 275,822
lines, 10 segments, 0 unmatched damage lines). **Rerun the golden test after
any parser or segmentation change.**

## Encounter segmentation

EQ2 logs have no encounter markers. Segments = damage-gap ≥ 25s, hard-cut on
zone lines, labeled from `has killed <Named>` where the killer is a player
(mobs killing things = casualties, not wins). Chain-pulled group content can
merge into one segment — labels list every named ("A + B"). 15s was tried and
splits real fights (a 21s lull mid-Garanel in the fixture).

## Zone runs (the navigation model — 2026-08-03)

Files ("sessions") are the INGEST unit only; the UI navigates **zone runs** —
one contiguous visit to one zone by one character, derived entirely from
encounter rows by `pipeline/zoneruns.py`:

- **Dedupe**: overlapping uploads produce byte-identical encounters
  (segmentation is deterministic per parse_version). `rebuild_zone_runs`
  groups a character's encounters by `(started_ts, ended_ts, zone, name)`;
  the copy from the widest-coverage session is canonical, the rest get
  `encounters.dup_of` and never join a run. Marking, not deleting — every
  parse stays complete and the marks re-derive after any reparse. Sessions
  only dedupe against equal `parse_version` (mid-sweep safety).
- **Segmentation**: canonical encounters in time order split on a zone change
  or an idle gap > `ZONE_RUN_GAP_S` (3600s). NULL-zone encounters (log began
  mid-zone) form their own "Unknown zone" runs.
- **Id stability**: the upsert matches recomputed runs to existing rows by
  zone + overlapping time window, so `/zones/:id` URLs survive reparses and
  backfills; rollup columns recompute every rebuild.
- **Hooks**: end of `parse_session`, live `_flush` (when fights land), and a
  startup relink sweep in `main.py` before AND after `_reparse_stale` — the
  sweep is also the migration for pre-zone_runs databases (schema v6:
  `zone_runs` table + `encounters.zone_run_id/dup_of`).

API: `GET /api/zone-runs` (list, powers the date-grouped Home page),
`GET /api/zone-runs/{id}` (run + canonical encounters with logger headline),
`GET /api/zone-runs/{id}/report` (raid report scoped to the run —
`raidreport.build_for_encounters` handles cross-session sets, pulls pruned
encounters from frozen reports, flags `partial`). `GET /api/encounters/agg`
accepts cross-session id sets: every session is visibility-checked, actors
merge by `name|kind` (`key` + `entity_ids[]` in the payload), abilities by
source key, with `rollup_key` resolving pet credit (players self-credit —
their DB `rollup_to` is NULL).

Frontend: `/` = `Home.jsx` (runs grouped by local day), `/zones/:id` =
`ZoneRun.jsx` (fight rail + tabs Overview/Damage/Healing/Defense/Insights;
`?sel`/`?actor`/`?tab`/`?cmp` all URL state), right-hand `ActorPanel`
(per-actor drilldown) or `ComparePanel` (checkbox multi-select, per-metric
grouped bars from `lib/stats.js`). `/uploads` = file management;
`/sessions/:id` (Workspace) survives as the per-file debug view.

## ACT parity (diffed 2026-08-02 vs Lindsay's ACT screenshot, Zylphax the Shredder)

The Zylphax encounter now matches ACT **exactly — all 25 players to the point
of damage** (`test_act_parity_zylphax` guards it). Three bugs found and fixed:

1. **Logger's swarm pets didn't roll up** (Bobby −17.3%): in the possessive
   owner slot (`Bobby's blighted horde`) the logger's name means the PLAYER,
   but the bare-name-is-pet rule classified it as his pet → rollup dropped.
2. **`lastrowid` after `ON CONFLICT DO NOTHING` is garbage** (connection-wide
   last-insert id, any table). Harmless on a fresh DB; corrupts ability
   attribution on ANY second session or reparse. Now guarded by `rowcount`.
3. **Non-focus self-hits counted as damage** (Spades +746): ACT excludes all
   self-inflicted damage from Damage, we only excluded `focus` dtype. Self-hits
   now shelve under ability kind `self` like focus does.

Still open / by design (revisit when they matter):
- ~~Mobs absent from the actor table~~ FIXED phase 7b: mobs, mob-owned pets,
  and the pooled `Unknown` all get actor rows (with `damage_taken`).
- ~~Sourceless passive damage credited to no one~~ FIXED phase 7b: pooled
  under an `Unknown` entity, incl. the previously mis-parsed
  `X is/are hit by <Effect> for N` grammar (1,007 lines in bobby.txt).
- **Encounter cuts**: Zylphax's window matched ACT to the second, so the
  cutter isn't broken per se — but our ≥25s gap merges chain-pulled trash into
  one segment where ACT splits per pull ([16] encounters in ACT's zone list vs
  our ~10). Need a specific mis-cut fight from Lindsay before tuning.
- **Exact Unknown parity unverified**: our Zylphax Unknown pool is 451,633
  (all Stench of Death); ACT reportedly showed ~1.17M — recheck against ACT
  once the same fight is compared column-for-column.

## Phase 7b — attribution overhaul + stats engine v2 + workspace UX (2026-08-02)

**Pet knowledge base** (`parser/petnames.py`, global `pet_names` table): named
pets (`Ellea's Lunar Attendant`) are grammatically identical to abilities with
internal possessives (`Banjeaux's Daro's Dull Blade`), so ONLY names in the
knowledge base decompose as pets (`Subject.unit == "named_pet"`). Sources:
curated seed, plus **learning** — every parse prescans its raw lines for
`Alas, <Owner>'s <Capitalized> has died…` evidence (kill-victim guard rejects
mob adds like `Garanel's Shade`), unions it with the global table, and after
the parse persists new names + every ability actually cast by a pet entity
(`ability_catalog`, `source='observed'`; curated > observed > census).
Knowledge applies **backwards** two ways: the prescan covers the current file
from line 1, and `sessions.parse_version` + the startup reparse sweep
(`main._reparse_stale`; also `POST /api/sessions/{id}/reparse`) re-attribute
old sessions whenever `PARSE_VERSION` bumps. A session stuck at `parsing` on
startup is an orphan (dead worker) and is swept too. Conflated pets (another
player's pet under the owner's own name) can't be split — ability rows whose
name is a known pet ability get `via_pet` at API read time instead (damage
stays with the owner, matching ACT).

**Behavioral mob refinement** (`pipeline/refine.py`): single-token capitalized
names default to "player", but a kill-victim of a player-credited kill line, or
a name that trades damage with confirmed players (≥2 hit / ≥3 hitting) without
ever touching a heal, reclassifies to `mob` — so one-word bosses ("Venekor")
stop appearing as raiders and their kills label the encounter. Target-side
resolution now decomposes possessives exactly like source-side, so damage
taken by `Ellea's blighted horde` lands on the same entity row.

**Stats engine v2** (schema v5, `pipeline/statsroll.py`): per-ability avoid
breakdown (`misses/parries/ripostes/dodges/blocks/reflects/resists` — the
`parries`→"parrie" bug is dead), `zero_hits` (absorbed hits stay inside `hits`
for ACT parity; min/max/median are non-zero), `median`, `avg_delay_s`,
`dtypes` (JSON per-school split incl. dual-type components), autoattack split
into `(melee)/(multi attack)/(aoe attack)/(flurry)` (flags `F_AOE`/`F_FLURRY`),
crits on ward/power/threat, threat split into `threat`/`detaunt` kinds, casts
attached to the ability's busiest row (no phantom damage rows), actor
`damage_taken`/`power_drain`/`cure_count`, and actor rows for mobs + Unknown.
API derives `swings` and `to_hit_pct`. `GET /api/encounters/agg?ids=…` sums
any set of one session's encounters into the same payload shape (single-id
fast-path; medians recomputed from events, null when pruned) — it powers
every tree node below.

**Workspace UX**: `/sessions/:id` is now one ACT-style page
(`frontend/src/pages/Workspace.jsx` + the first shared components:
`SortableTable`, `EncounterTree`, `ShareBar`, `useQueryState`): left tree
(session **All** root → zone blocks → fights; consecutive trash collapses to
`Trash ×N`), right pane = sortable combatant table → per-actor ability
drilldown (ACT columns: Swings, ToHit, Median, AvgDelay, damage types, kind
filter chips) + a DPS bar strip. Selection lives in the URL (`?sel=` id-list
or `all`, `&actor=`). SessionDetail/Encounter/RaidReport/Coach **pages** are
deleted (`/encounters/:id` redirects); the raid-report API remains and its
engagement/death-cost/overheal numbers merge into the All node as columns.
The coach engine, calibration, and `coach_api` are intact — no UI surface.
Chain-pull labels cap at 4 nameds (`A + B + C +N more`).

## Hardening (phase 6)

- **Pruning** (`pipeline/prune.py`, loop in `main.py` every 6h, `PRUNE_DAYS`
  env, default 180, 0 disables): ready+unpinned sessions older than the
  cutoff get their raid report FROZEN into `raid_reports`, then their
  `events` rows deleted and `sessions.pruned=1`. Rollups/entities/encounters
  stay (Encounter + SessionDetail pages are rollup-backed and unaffected);
  raw gzips remain the reprocessing safety net. A pruned session serves the
  frozen raid report (`"frozen": true`), refuses coach regeneration (409),
  and GET /coach still returns the last persisted report. Calibration
  auto-pins, so ground truth is never pruned.
- **Backfill UX**: the Sessions dropzone takes multiple files (sequential
  queue, per-file errors); server-side sha256 + line dedupe make overlap
  harmless.
- **Live coach hints**: each SSE fight card carries `hints` (died N×, mostly
  overheal, wards punched through) computed from the logger's rollup row in
  `sessions_api._card_hints` — cheap flags, not the full engine.

## Verification

```bash
.venv/bin/python -m pytest backend/tests/ -q     # 85 tests incl. golden fixture
bash restart.sh && curl -s localhost:8450/api/sessions
curl -F "file=@/home/lindsay/bobby.txt" -F "character_name=Bobby" localhost:8450/api/uploads
```
