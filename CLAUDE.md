# eq2advanced - Claude Context

## Behavior

- Be concise and make focused changes.
- Prefer updating existing files over adding new abstractions.
- Keep secrets out of the repository.
- Use the local helper scripts below for restart and shipping.

## Read also

- `AGENTS.md` — agent instructions and provisioning notes.
- `ARCHITECTURE.md` — how the app is wired and how it deploys.
- `codex.md` — Codex working notes (keep in sync with this file).

## App

- Public URL: https://eq2advanced.jupiterns.org
- Local port: 8450
- Docker image: ghcr.io/improvmasta/eq2advanced:main

## Commands

```bash
bash restart.sh
SHIP_TOOL=claude bash ship.sh "message"   # updates Ship log in CLAUDE.md+codex.md, commits; pushes on main, else offers to merge the branch
docker compose up -d --build
```

`ship.sh` is the generic helper from `/home/lindsay/scripts`. Set `SHIP_TOOL=claude`
(or `codex`) for the matching co-author trailer; the Ship log below is updated
automatically on every ship and condenses itself.

## Host context

CLI tools (`gh`, `rg`, `jq`, `fd`), the `state`/`logs`/`restart`/`ship` session
helpers, provisioning commands, and deploy notes live in `/home/lindsay/CLAUDE.md`
and `/home/lindsay/AGENTS.md` — don't duplicate them here.

## Stack (real app — placeholder retired)

FastAPI + SQLite backend (`backend/`), Vite+React SPA (`frontend/` → `dist/`
served by the API). Dev server binds 0.0.0.0:8450 → http://10.1.1.15:8450.
Live site is a Docker container **on 10.1.1.5** (Zoraxy routes the public
hostname there) — Lindsay deploys it himself; never auto-ship, never deploy.

Read `ARCHITECTURE.md` before touching the parser or segmentation — the
subject model (bare logger-name = their PET) and the possessive rules are
verified against a real raid log and covered by tests.

```bash
.venv/bin/python -m pytest backend/tests/ -q   # 79 tests; golden = bobby.txt
```

Phase 2 (accounts) is BUILT: open sign-up (first account = admin), cookie
sessions, characters CRUD with claim-of-unowned, per-character device tokens
(QR pairing), and per-user isolation on every session/encounter/upload route.
**The sign-up model is NOT settled — Lindsay wants to discuss it before it goes
further.** Don't extend registration/login until that discussion happens (open
questions listed in the plan md → "OPEN: sign-up model").
The approved build plan (phases 5-6: coach engine, hardening)
lives at `~/.claude/plans/swirling-discovering-pixel.md`.

Phase 3 (live ingest + SSE) is BUILT: `/api/ingest/hello|batch|backfill/done`
on device tokens (the frozen ACT-DLL contract), incremental encounter
finalization with a close-time rebuild from raw chunks, SSE
`/api/sessions/{id}/stream`, the Live page, and the reference client
`backend/tools/simulate_live.py`. See `ARCHITECTURE.md` → "Live ingest";
`test_golden_equivalence` proves streamed batches == whole-file upload on
bobby.txt.

Phase 4 (Census) is BUILT: `backend/census/` (retrying client, effect_list
grammar parser, character sync + snapshot history + spell/item caches),
`routers/census_api.py`, an hourly auto-refresh of >24h-stale owned characters
(`CENSUS_AUTO_REFRESH=0` disables it; tests set that in conftest.py), and the
Character page (stats/gear/scribed tiers, Refresh with 60s cooldown, snapshot
history diffs). See `ARCHITECTURE.md` → "Census sync". CI runs entirely on
recorded fixtures in `tests/fixtures/census/` — never live Census.

Phase 5 (Coach v1 + Raid Report) is BUILT: `backend/coach/` (descriptive
currencies, Census-as-prior fit with per-ability coefficients, monotone stat
replay, advisor persisted to `coach_reports`, raid report with engagement
timing + death cost), `routers/coach_api.py`, calibration sessions
(`POST /api/sessions/{id}/calibration`, auto-pins, overrides the fit), and the
Coach / RaidReport / Calibration pages. Tier upgrades cap at Master (no
Ancient/Celestial on TLE). GOTCHA: Census `crc=` queries do NOT accept comma
OR-lists (id= does) — `spells_by_crcs` is one request per crc. See
`ARCHITECTURE.md` → "Coach engine" / "Raid Report".
Phase 6 (coach correctness + hardening) is BUILT 2026-08-02: `parser/flavor/`
(prepare-line → ability; real cast counts fix idle% — instants never print
prepare lines, spellbook membership is the proc discriminator),
`census/catalog.py` (ability_catalog from census + curated pet kits, proc
flag from the "may cast" grammar; pet-name join gate + k sanity gate 0.2–12
kill Master's-Strike misjoins), two-point calibration (per-session stat
capture in `sessions.calib_stats_json`, `_solve_two_point` fits the TRUE
base/abmod cap), k-spread kept as `debuff_uplift` (dummy never overwrites a
healthy raid fit), healer/utility currencies (HP-deficit overheal/saves/
bleedthrough estimates + debuff uptime vs burn windows), engagement
classifier v2 (proc + reactive-swing correlation + prepare ground truth),
events pruning (`PRUNE_DAYS`, frozen `raid_reports`, calibration pinned),
multi-file backfill upload, live fight-card hints. See `ARCHITECTURE.md` →
"Coach correctness (phase 6)" / "Hardening". The abmod marginal is only real
once Lindsay runs TWO dummy parses at different Ability Mod and flags them.

ACT parity: Zylphax matches exactly (guarded by `test_act_parity_zylphax`);
round 2 (2026-08-03, Emerald Halls zone view) rebuilt the model — ward-absorb
pairing (absorb line folds into the next hit on that target; "YOU" vs
"YOURSELF" gotcha), self-damage excluded from DamageTaken, `relieves` cure
grammar, drain verb family, self power gains, bare-name pet deaths merge,
possessive pets keep their own damage-taken rows, encounter cut = 7s combat
silence (ACT's idle timeout; damage+avoids hold fights open). Emerald Halls:
cures/deaths 25/25 exact, damage 21/25 exact, EncDPS within 0.7%. See
`ARCHITECTURE.md` → "ACT parity round 2" for the residuals — and DO NOT
re-add trailing-event trimming; it regresses cures/EncHPS.

Phase 7b (UX overhaul + parsing correctness) is BUILT 2026-08-02: one
ACT-style Workspace page at `/sessions/:id` (encounter tree with zone blocks +
collapsed `Trash ×N` nodes, sortable combatant table, per-actor drilldown with
Swings/ToHit/Median/AvgDelay/damage-types, DPS bar strip; selection in `?sel=`
URL params; SessionDetail/Encounter/RaidReport/Coach PAGES deleted — the coach
ENGINE + calibration + APIs stay, no UI surface). Backend: named-pet knowledge
base (`parser/petnames.py`, learns from Alas-death evidence + curated seed,
persists globally, applies backwards via reparse), behavioral mob refinement
(`pipeline/refine.py`), stats engine v2 (schema v5: avoid breakdown, zero_hits,
median, avg_delay_s, dtypes JSON, melee split, damage_taken, mob + Unknown
actor rows), the `is/are hit by <Effect>` grammar, `GET /api/encounters/agg`,
and `sessions.parse_version` + startup reparse sweep (bump `PARSE_VERSION` in
`pipeline/ingest_writer.py` after ANY parser/rollup semantics change — stale
and orphaned-parsing sessions reparse automatically at startup). See
`ARCHITECTURE.md` → "Phase 7b". Exact ACT drilldown numbers from Lindsay's
2026-08-02 daytime screenshots (Ascent of the Awakened) are NOT yet
cross-checked — that log wasn't uploaded; compare column-for-column when it is.

Phase 7 groundwork (base spell data) is BUILT 2026-08-02: Census spell records
ARE the base pre-stat values per tier (no wiki/manual entry) — tooltips = base
+ stats, which is fit.py's damage model. `tools/ingest_spells.py --all
--max-level 70` bulk-caches full class books (`sync.ingest_class_spells` /
`client.spells_by_class`, `classes.<cls>.level=[<max>` + `c:start` paging);
`census_spells` gained typed columns (cast/recast/recovery/duration/power/
dmg_*, schema v4) via `sync.typed_fields()` — the one owner of unit
conversions, shared with `fit.spellbook()`. See `ARCHITECTURE.md` → "Bulk
spell ingest" (incl. the s:example burst-throttle gotcha — bulk pulls need a
registered `CENSUS_SERVICE_ID` in `.env`, or a ~1-class-per-8-min drip).
NEXT (discuss with Lindsay first): AA modeling — curated per-class
`aa_effects` table for throughput AAs, not full-tree ingest.

Phase 8 (zone runs — the navigation model) is BUILT 2026-08-03: files are
ingest-only; the UI navigates **zone runs** (contiguous zone visits derived
from encounters by `pipeline/zoneruns.py` — content dedupe via
`encounters.dup_of`, gap/zone-change segmentation, id-preserving upsert,
schema v6, startup relink = the migration). `/` = date-grouped Raids home,
`/zones/:id` = run page (fight rail, tabs Overview/Damage/Healing/Defense/
Insights, right-hand ActorPanel drilldown, checkbox multi-select →
ComparePanel), `/uploads` = file management, `/sessions/:id` = per-file debug
view. `/api/encounters/agg` is cross-session (actors keyed `name|kind`);
the run raid report handles pruned sessions via frozen reports. See
`ARCHITECTURE.md` → "Zone runs". GOTCHA: dedupe requires equal
`parse_version` — after bumping PARSE_VERSION the startup sweep must finish
before duplicate marking converges.

Phase 9 (editable raid list + UI pass) is BUILT 2026-08-03: the raid list is a
sortable table you can EDIT — `run_edits` (schema v8) stores delete/join/break
by encounter FINGERPRINT (`started_ts|zone|name`), not id, so an edit survives
the reparse a backfill triggers; `rebuild_zone_runs` applies all three, and
`DELETE /api/sessions/{id}` is the only destructive path (it also forgets that
log's edits, or a re-upload comes back invisibly deleted). New: `/import` hub
(live link / log files / ACT export — the XML export is the target and is NOT
built; Lindsay will send one), EQ2 archetype colors as theme-aware `--fam-*`
tokens (fighter blue, priest green, mage red, scout yellow), rate-first columns
(EncDPS/EncHPS leftmost), Time dead + Rezzes on Overview, wipes as red fight
names with a dot instead of a badge, and the drilldown panel widened (the raid
table condenses to name + the sorted stat while it is open) with real tabs, a
Combine-pets checkbox defaulting OFF, and no coaching prose. Proc flags are now
per-row evidence, not a global name list — see `ARCHITECTURE.md` -> "Proc
exposure" (needs `ability_catalog.scribed`; `backfill_scribed` repairs old DBs
at startup).

Phase 10 (raid page pass) is BUILT 2026-08-03: **engagement v3** — engage is
the gap between the pull and a raider's first ACTION, and heals, cures, rezzes
and *attempted* swings now anchor it alongside damage/threat (`engage_anchor`
says which; ward absorbs and catalog procs still never anchor, anything inside
the opening 2s is still low-confidence). Scoring only hostile actions had
Tragedy engaging Sawtooth at 13s when the log shows a heal at 2s;
`test_engagement.py` pins the kinds. **Fight rail** rebuilt
(`EncounterTree.jsx`): click = focus one fight, checkbox = add it to the
combined stats, three-column rows (time / name / m:ss), and **wipes now count
by default** (ACT parity) behind a real switch (`?wipes=0`). Pet rows are off
by default on Overview — a pet's damage is already credited to its owner, so
its row can only carry DmgTaken and reads as a parse fragment. **Read caches**:
`backend/memo.py` (12 entries, epoch bumped by `rebuild_zone_runs` +
`prune_once`, pinned by `test_memo.py`) and a client-side GET cache with
`peek()` so a fight you have opened repaints without a "Loading…" flash. See
`ARCHITECTURE.md` -> "The fight rail" / "Read caches" / "Raid Report".

## Ship log

- 2026-08-03 (claude): Phase 9+10: editable raid list, import hub, fight rail rebuild, engagement v3, read caches
- 2026-08-03 (claude): Fix Insights crash (coach.character is an object, render its name) + error boundaries at route and panel level
- 2026-08-03 (claude): Zone runs phase 6: encounter deep-links resolve to runs (via dup_of), docs (ARCHITECTURE/CLAUDE/codex zone-runs sections)
- 2026-08-03 (claude): Zone runs phase 5: checkbox multi-select + ComparePanel (per-metric grouped bars from agg + report data)
- 2026-08-03 (claude): Zone runs phase 4: zone-page tabs (Overview/Damage/Healing/Defense/Insights), right-side ActorPanel, shared stats.js, coach resurfaced
- 2026-08-03 (claude): Zone runs phase 3: Raids home (date-grouped runs), /zones/:id page v1, Uploads management page, shared UploadDrop
- 2026-08-03 (claude): Zone runs phase 2: zone-runs API, cross-session encounters/agg (name-keyed merge), run-scoped raid report
- 2026-08-03 (claude): Zone runs phase 1: zone_runs table (schema v6), content dedupe + segmentation linker, parse/live/startup hooks
- 2026-08-03 (claude): Phase 7b: Workspace UX (ACT-style tree + drilldown), stats v2 surfacing, pet knowledge refine pass
- 2026-08-02 (claude): Phase 6: coach correctness (flavor cast ground truth, two-point calibration, debuff uplift, ability catalog + join gates, healer/utility estimates, engagement v2) + hardening (events pruning, frozen raid reports, multi-file backfill, live hints)
