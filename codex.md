# eq2advanced Codex Notes

## Goal

New app served at https://eq2advanced.jupiterns.org.

See also: `AGENTS.md` (agent instructions), `ARCHITECTURE.md` (wiring + deploy),
`CLAUDE.md` (Claude context — keep in sync with this file).

## Current Stack

- Minimal static Python web server
- Docker image published to GHCR
- Zoraxy routes https://eq2advanced.jupiterns.org to 10.1.1.15:8450

## Key Files

- `AGENTS.md`: agent instructions and provisioning notes
- `ARCHITECTURE.md`: runtime wiring and deployment
- `public/index.html`: starter page
- `Dockerfile`: container image
- `docker-compose.yml`: local/runtime compose
- `restart.sh`: detached local server restart
- `ship.sh`: generic ship — updates Ship log in CLAUDE.md+codex.md, commits; pushes on main, else offers to merge the branch
  (`SHIP_TOOL=claude|codex` selects the co-author trailer; default `codex`)
- `.github/workflows/docker.yml`: GHCR build and public package visibility

## Host context

CLI tools, session helpers, provisioning commands, and deploy notes live in
`/home/lindsay/AGENTS.md` — don't duplicate them here.

## Migration

The app is FastAPI + SQLite (`backend/`) with a Vite+React SPA (`frontend/` -> `dist/`
served by the API). Dev binds 0.0.0.0:8450 -> http://10.1.1.15:8450; the LIVE site is a
Docker container on 10.1.1.5 (Zoraxy routes there) that Lindsay deploys himself — never
auto-ship, never deploy. Read `ARCHITECTURE.md` before touching parser/segmentation
(subject model: bare logger-name = their PET; possessive hazards are test-covered).
Tests: `.venv/bin/python -m pytest backend/tests/ -q` (70; golden fixture = /home/lindsay/bobby.txt).
Keep the public image name as `ghcr.io/improvmasta/eq2advanced:main` unless the repository is renamed.

Phase 2 (accounts) is BUILT: open sign-up (first account = admin), cookie sessions,
characters CRUD + claim-of-unowned, per-character device tokens (QR pairing),
per-user isolation everywhere. SIGN-UP MODEL NOT SETTLED — Lindsay wants to discuss
before it goes further; don't extend registration/login (see plan md → "OPEN: sign-up
model"). ACT parity: Zylphax fight matches ACT exactly
(25/25 players) after three attribution fixes 2026-08-02 — see `ARCHITECTURE.md`
→ "ACT parity" for the remaining opens (mob rows, Unknown damage, trash-merge cuts).

Phase 3 (live ingest + SSE) is BUILT: `/api/ingest/hello|batch|backfill/done` on device
tokens (the frozen ACT-DLL contract), incremental encounter finalization + close-time
rebuild from raw, SSE `/api/sessions/{id}/stream`, Live page, and
`backend/tools/simulate_live.py` (reference client). See `ARCHITECTURE.md` → "Live
ingest". `test_golden_equivalence` proves streamed == uploaded on bobby.txt.

Phase 4 (Census) is BUILT: `backend/census/` (retrying client, effect_list grammar
parser, sync + snapshot history + spell/item caches), `routers/census_api.py`
(summary/refresh/snapshots/diff/spell detail), hourly auto-refresh of >24h-stale owned
characters (`CENSUS_AUTO_REFRESH=0` disables; tests set it), and the Character page
(stats/gear/scribed tiers, Refresh, history diffs). See `ARCHITECTURE.md` → "Census
sync". CI uses recorded fixtures in `tests/fixtures/census/` — never live Census.

Phase 5 (Coach v1 + Raid Report) is BUILT: `backend/coach/` (descriptive currencies,
Census-as-prior fit with per-ability coefficients, monotone stat replay, advisor
persisted to `coach_reports`, raid report with engagement timing + death cost),
`routers/coach_api.py`, calibration sessions (auto-pin, override the fit), and the
Coach / RaidReport / Calibration pages. Tier upgrades cap at Master (no
Ancient/Celestial on TLE). GOTCHA: Census `crc=` queries do NOT accept comma OR-lists
(id= does) — `spells_by_crcs` is one request per crc. See `ARCHITECTURE.md` → "Coach
engine" / "Raid Report". 79 tests.
Phase 6 (coach correctness + hardening) is BUILT 2026-08-02: `parser/flavor/`
(prepare-line → ability, real cast counts fix idle%; instants never print prepare —
spellbook membership discriminates procs), `census/catalog.py` (ability_catalog from
census + curated pet kits; pet-name join gate + k sanity gate 0.2–12), two-point
calibration (per-session stat capture, true base/abmod-cap solve), k-spread kept as
`debuff_uplift` (dummy never overwrites a healthy raid fit), healer/utility currencies
(HP-deficit overheal/saves/bleedthrough + debuff uptime vs burn windows), engagement
classifier v2, events pruning (`PRUNE_DAYS`, frozen `raid_reports`), multi-file
backfill upload, live fight-card hints. See `ARCHITECTURE.md` → "Coach correctness
(phase 6)" / "Hardening". Abmod marginal becomes real once TWO dummy parses at
different Ability Mod are flagged.

Phase 7b (UX overhaul + parsing correctness) is BUILT 2026-08-02: one
ACT-style Workspace page at `/sessions/:id` (encounter tree with zone blocks +
collapsed `Trash ×N` nodes, sortable combatant table, per-actor drilldown with
Swings/ToHit/Median/AvgDelay/damage-types, DPS bar strip; selection in `?sel=`
URL params; SessionDetail/Encounter/RaidReport/Coach PAGES deleted — the coach
ENGINE + calibration + APIs stay, no UI surface). Backend: named-pet knowledge
base (`parser/petnames.py`), behavioral mob refinement (`pipeline/refine.py`),
stats engine v2 (schema v5), the `is/are hit by` grammar, `GET
/api/encounters/agg`, and `sessions.parse_version` + startup reparse sweep
(bump `PARSE_VERSION` in `pipeline/ingest_writer.py` after ANY parser/rollup
semantics change). See `ARCHITECTURE.md` -> "Phase 7b".

Phase 7 groundwork (base spell data) is BUILT 2026-08-02: Census spell records ARE
the base pre-stat values per tier (no wiki/manual entry) — tooltips = base + stats,
which is fit.py's damage model. `tools/ingest_spells.py --all --max-level 70`
bulk-caches full class books (`sync.ingest_class_spells` / `client.spells_by_class`,
`classes.<cls>.level=[<max>` + `c:start` paging); `census_spells` gained typed columns
(cast/recast/recovery/duration/power/dmg_*, schema v4) via `sync.typed_fields()` — the
one owner of unit conversions, shared with `fit.spellbook()`. See `ARCHITECTURE.md`
→ "Bulk spell ingest" (incl. the s:example burst-throttle gotcha — bulk pulls need
a registered `CENSUS_SERVICE_ID` in `.env`, or a ~1-class-per-8-min drip).
NEXT (discuss with Lindsay first): AA modeling — curated per-class `aa_effects`
table for throughput AAs, not full-tree ingest.

## Ship log

- 2026-08-03 (claude): Zone runs phase 5: checkbox multi-select + ComparePanel (per-metric grouped bars from agg + report data)
- 2026-08-03 (claude): Zone runs phase 4: zone-page tabs (Overview/Damage/Healing/Defense/Insights), right-side ActorPanel, shared stats.js, coach resurfaced
- 2026-08-03 (claude): Zone runs phase 3: Raids home (date-grouped runs), /zones/:id page v1, Uploads management page, shared UploadDrop
- 2026-08-03 (claude): Zone runs phase 2: zone-runs API, cross-session encounters/agg (name-keyed merge), run-scoped raid report
- 2026-08-03 (claude): Zone runs phase 1: zone_runs table (schema v6), content dedupe + segmentation linker, parse/live/startup hooks
- 2026-08-03 (claude): Phase 7b: Workspace UX (ACT-style tree + drilldown), stats v2 surfacing, pet knowledge refine pass
- 2026-08-02 (claude): Phase 6: coach correctness (flavor cast ground truth, two-point calibration, debuff uplift, ability catalog + join gates, healer/utility estimates, engagement v2) + hardening (events pruning, frozen raid reports, multi-file backfill, live hints)
