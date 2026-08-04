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

- Public URL: https://eq2advanced.com
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
The Docker container **on 10.1.1.5** is Lindsay's to deploy — never auto-ship,
never deploy.

**As of 2026-08-03 the public hostname points at the DEV box**:

```bash
/home/lindsay/scripts/provision-app.sh route eq2advanced 8450 --deploy-server media   # back to the container
```

The Cloudflare record was proxied for part of 2026-08-03 and Lindsay turned the
proxy **off** the same day, because the edge caps a request body at **100 MB**
and a raid backfill is bigger than that — the 413 comes from Cloudflare, the
app never hears about the upload. `siteconfig.edge_max_bytes()` reports that
ceiling per request (`CF-Ray` is the evidence) so the dropzone can say it
before someone spends ten minutes uploading; with the proxy off it reports 0
and nothing is claimed. Every request still arrives from Zoraxy —
`backend/siteconfig.py` is the one place that recovers the real visitor
address, the browser's scheme, and the public URL. Never go back to
`request.client.host` / `request.base_url`; see `ARCHITECTURE.md` → "The app is
behind two proxies".

Read `ARCHITECTURE.md` before touching the parser or segmentation — the
subject model (bare logger-name = their PET) and the possessive rules are
verified against a real raid log and covered by tests.

```bash
.venv/bin/python -m pytest backend/tests/ -q   # 198 tests; golden = bobby.txt
```

Phase 2 (accounts) is SUPERSEDED by phase 12 below — the sign-up model is now
settled. Device tokens (QR pairing) and the cookie session are unchanged.
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

Phase 11 (raids-only filter + the roster) is BUILT 2026-08-03: the Home page
defaults to **raids only** (a `.switch` in the pagehead, remembered in
`localStorage`), a raid being a run with >= 7 raiders — one more than a full
group. That made `raider_count` load-bearing, and it was wrong: it counted
distinct `player` entities in the run's busiest fight, and an encounter is a
TIME SLICE holding everyone the log overheard. `_raider_count`
(`pipeline/zoneruns.py`) is now a ROSTER, keyed by NAME (runs span files):
player (not mobs, not the pooled `Unknown`), acted (not `damage_taken`-only),
and present in >= 25% of the run's fights (min 2; under 4 fights anyone
counts). Emerald Halls 26 -> 24, Lord Vyemm 32 -> 26, Halls of Fate 7 -> 6
(correctly no longer a raid); real raids unchanged (FTH 25, Ascent 12). A
cooperation graph (support edges + shared-enemy edges, keep the logger's
component) was built and **REJECTED** — it moved 0 of 49 real runs, because a
passing group hits the same mobs you do; don't rebuild it without a log where
presence demonstrably fails. See `ARCHITECTURE.md` -> "The roster, and what
counts as a raid".

Phase 12 (accounts, groups, sharing) is BUILT 2026-08-03 — this replaces the
phase-2 placeholder and settles the sign-up model. **Login is username +
password; there is NO email anywhere.** The only self-service recovery is a
security question picked at sign-up (six of them, answers case- and
whitespace-insensitive; a reset kills every live cookie). Failure counting on
login/reset/join-code lives in `backend/ratelimit.py`.
**Admin is operational, not omniscient**: `role='admin'` grants the admin
console (users, storage, quotas, job health, audit log) and is absent from
every visibility decision in `security.py` — an admin 404s on a stranger's run,
agg, timeline, deaths, coach and Census. Support = ask them to share the raid.
**Character claims are not exclusive** (`UNIQUE(user_id, name, world_id)`): two
people can each claim Bobby, and `sessions.upload_sha256` lost its global UNIQUE
so both can upload the same night (one shared content-addressed gzip).
Invite links: `/join/<code>` carries the same 6-digit code (one credential to
rotate), works signed out via the thin unauthenticated
`GET /api/groups/preview/{code}`, and joins as soon as the visitor finishes
sign-up. The create form shows the code and link as you type the name
(`GET /groups/new-code`, claimed by `POST /groups`). Rotating the code keeps
every current member and kills the old code plus its links; removing a member
revokes their access on the next request. **Sharing is per zone run, via groups** — `groups.py` owns the one visibility
predicate (own / shared with a group you're in / character auto-share minus a
per-run `hide` / published), evaluated at READ time so it survives every
`rebuild_zone_runs` and revokes the moment you leave a group. Seeing never
implies changing (`owned_zone_run`), and authorization moved from session to
ENCOUNTER (`visible_encounters`) so a shared raid can't expose the other fights
in the same uploaded file. **Published raids** (admin-only, own raids only) are
readable with NO ACCOUNT — read routes take `optional_user` and the SPA renders
signed out. **Upload cap + parse-only** (`upload_max_bytes`/`storage_max_bytes`,
0 = unlimited and shipped that way): the cap is counted as the file streams and
the 413 offers `retain_raw=0`, which parses the log and drops the bytes — those
sessions can never be reparsed, and both reparse paths enforce it.
Schema v9 rebuilds `users`, `characters` and `sessions` (guarded by table SHAPE,
not user_version); verified on a copy of the real 344 MB DB. See
`ARCHITECTURE.md` -> "Accounts, groups and sharing".

Phase 13 (the public front door) is BUILT 2026-08-03, no schema change:
`backend/siteconfig.py` owns the three request facts a reverse proxy falsifies.
**The brute-force counter was the urgent one** — behind Zoraxy every request
carried the same client address, so `ratelimit`'s per-address bucket was one
counter for the entire internet and five wrong passwords by anyone locked
*everybody* out of login for fifteen minutes. `client_ip` reads
`CF-Connecting-IP` / `X-Forwarded-For`, but ONLY from a trusted peer
(`TRUSTED_PROXIES`, default `10.1.1.4` + loopback), so a direct LAN client
can't invent an address; a forged header buys a fresh address bucket and
nothing more, because the per-username bucket is what actually guards a
password. `/auth/password` and `/auth/security-question` are now counted too
(both are password oracles). `is_secure` restores the `Secure` flag on the
session cookie, which edge TLS had been silently dropping. `public_base_url()`
(env `PUBLIC_BASE_URL`, default the live hostname) is what group **invite
links** (`GET /api/groups` → `invite_base`) and the device **pair payload**
are built from, instead of the browser's origin and `request.base_url` — both
of those handed out `10.1.1.15:8450` links that mean nothing to the recipient.

Phase 14 (rezzes, revives, intercepts, adjusted delay) is BUILT 2026-08-03,
schema v10 + PARSE_VERSION 13 (the startup sweep reparses everything):
**every rez family counts** — `rez` matched only the cleric line ("petitions
the divinities of resurrection"), so the 73 druid/shaman casts ("calls forth
primeval/primal forces") of 142 were invisible and Ramms/Squigs showed zero
rezzes on a night they cast 41. **Revives are parsed for everyone** ("X is
revived!" / "is resurrected!"), which fills `time_dead_s` — a column that
existed and was never written, so the aggregate reported a confident 0; the
roller and the raid report now share one death->{revive|acted|fight end} rule
and a test pins them equal. **Intercepts** are a new event + actor column: the
log carries NO amount and names the victim only from the logger's seat, and
the "for you"/"for your target" pair is one event printed twice (1270 of 1442
seconds carry both), so `_dedupe_repeats` keys on (type, who, second).
**"AvgDelay adj"** is ACT's Avg Delay over ACTIVATIONS instead of landings:
same-second hits collapse (AoE), a hit within a tick period of the previous
hit on the same target continues a chain (DoT), autoattack and catalog procs
are not presses. Tick period = Census `dmg_period_s` where known (~60 base
names), else inferred by MODAL DOMINANCE — real logs settle it (Bloodcoil 3s
75%, Grave Decay 1s 86%, vs Lifetap's nuke gaps spread 8-14s with none over
15%). On Zylphax, ACT's delay reads 0.14-0.39s for the top parsers; adjusted
reads 1.2-1.65s and separates them. Class inference was rebuilt in the same
pass: evidence pools across sessions keyed by NAME (and is written back to
every session's rows), shared spells vote in fractions, and a margin rule sits
beside the share rule — 131 -> 147 names resolved on the real DB, nothing
changed or lost, and the 19 players who had two different classes in one list
now have one. The remaining gap is DATA, not voting: 433 of 919 log ability
names have no Census row (AAs, gear procs, item effects), and 38 of the
unresolved "players" are named pets. See `ARCHITECTURE.md` -> "Rezzes,
revives, intercepts and the adjusted delay" / "Class inference".
The Raids home groups its rows by night (`SortableTable groupBy`, headings
only while sorted by night); Defense carries Intercepts, Damage carries
AvgDelay adj, and the drilldown carries it per ability.

Phase 15 (raid page, reader's layout) is BUILT 2026-08-04, frontend only:
**Overview is gone** — the run page opens on Damage (`?tab=overview` still
resolves there), and the metric block above the table stayed but is now retuned
per tab (Damage: raid damage/DPS/raiders; Healing: healed/HPS/overheal/cures;
Defense: taken/deaths/time dead/dmg lost/self-inflicted). The Pets and NPCs
switches moved to Defense with the rows they reveal — a pet row carries nothing
but DamageTaken. **Columns are the reader's**: drag a header to reorder, hide
any of them from the Columns menu, remembered per tab in localStorage
(`SortableTable prefsKey`, `eq2adv:cols:<key>`; a `fixed` column — Name — never
moves or hides, and stored order is by KEY so a column added later keeps its
natural place). Class and Casts/min columns are gone (ActorName already carries
the class chip); Dmg lost dead now follows Deaths and Time dead, the order the
cost actually reads in.
**Rank coloring is continuous** (`stats.js rankScale`/`rankColor`, applied via
the new `cellStyle` hook): distance from the peer MEDIAN as a fraction of it,
mixed into the text with `color-mix`, instead of hard red/green terciles. A
tercile called the bottom third of the raid red even when the whole field was
within a point of each other — which is exactly what crit becomes in later
expansions. Under four peers, or inside the noise floor, nothing is colored.
**Self-inflicted damage is marked, not hidden**: the Bloodthirsty Choker's
Vampiric Requiem was already excluded from Damage and DamageTaken (ACT parity,
`statsroll` self_hit — verified on the real DB: Sorzi 1,010,709 total incoming,
1,007,048 of it choker, 3,661 stored), so DmgTaken now prints a `*` with the
excluded amount on hover and the Defense block carries the raid total.
**Timeline follows its metric**: with nothing checked it plots the top five for
the metric you are on (healing shows healers, not the top five parsers who heal
nothing), and a `+ damage taken` overlay draws raid-wide incoming damage as a
filled backdrop on its own scale, so a spike on the tank and the heals that
answered it line up in the same second.

Phase 16 (the AoE tab) is BUILT 2026-08-04, no schema change: a fifth tab on
the run page answering "what hit the raid, how often did it REALLY land, and
who was covered". `pipeline/aoes.py` + `GET /api/encounters/aoes?ids=` +
`components/AoePanel.jsx`, 17 tests in `test_aoes.py`.
A **cast** is a second in which one enemy ability touched >= 5 players; ticks
and second waves inside `max(6s, 0.4 x reported)` ride along with it.
**Reported** is ACT's spell-timer list, extracted to
`backend/refdata/act_spell_timers.json` (446 entries, `<SpellTimers>` only — not
the chat triggers) and joined by ability NAME. **Observed** is the shortest
interval between two casts that repeats, measured inside one fight: an AoE
that misses a group is a cast we never see, and a missed cast can only make a
gap LONGER, so the mean and the median both drift up while the shortest
repeating gap does not. `observed_agree` prints the confidence.
Verified against the real DB: Blanket of Eternal Night 60.2 vs 60 reported,
Ydalian Bolt 47.7 vs 49, War Stomp 47.4 vs 45 with 48.8% of its targets
covered; Furious Storm reads 52 vs a reported 45 from three separate casters
— the reported timer is wrong for this expansion, which is the whole point of
printing both. Coverage = avoids (Bladedance) + zero-damage hits (Tortoise
Shell) minus anyone the same cast also hit. GOTCHA: entities are keyed by
NAME, so several trash mobs sharing one look like a single fast caster —
`instances_hint` says so rather than calling the ACT list wrong. See
`ARCHITECTURE.md` -> "GET /api/encounters/aoes".

Phase 17 (sharing from the ACT plugin) was BUILT and then REVERTED on
2026-08-04. **Sharing is set on the site, never by the uploader.** The ACT
plugin sends log lines and nothing else; a device token cannot read a parse back
and cannot change who sees one. The two site controls already cover it: the
character's standing auto-share (Characters page) and a raid's own Share control.
Schema v11 added `session_shares` + `device_tokens.can_share`; **v12 drops both**
(shape-guarded, verified against the real 431 MB DB with its device tokens
intact). Don't rebuild it — the reasoning is in `ARCHITECTURE.md` -> "Sharing is
a decision for the account, not the uploader".
GOTCHA worth keeping from that round: `set_run_shares` writes a `hide` for groups
reaching a run through a STANDING decision, and it only counted `character_shares`
— any future read-time share branch must be added there too, or unticking the box
looks like it works and revokes nothing.
The plugin is `/home/lindsay/eq2advanced-act` (improvmasta/eq2advanced-act), which
builds on the Linux dev host via `bash build.sh`.

## The domain (2026-08-04)

The site is **https://eq2advanced.com**, with `www.eq2advanced.com` routed to
the same app. Both are CNAMEs to `jupiterns.org` (flattened at the apex to
216.193.154.21) in their OWN Cloudflare zone, **DNS-only** — the proxy still
caps a request body at 100 MB and still breaks HTTP-01 renewal, so it stays
grey-clouded for the same reasons it did on the old hostname.

`eq2advanced.jupiterns.org` is **retired** — DNS record and Zoraxy route both
deleted 2026-08-04. Anything that carried the old base URL stopped working
that day: outstanding group invite links, and any device paired with the ACT
DLL (the pair payload embeds `public_base_url()`). Those devices need
re-pairing against the new domain.

`siteconfig.DEFAULT_PUBLIC_BASE_URL` is the code default and is now the new
domain; `PUBLIC_BASE_URL` in the environment still overrides it, and the
container should set it explicitly rather than lean on the default.

Both hostnames were created with `provision-app.sh route`, which grew two
things to make this possible — the Cloudflare zone is now resolved FROM THE
HOSTNAME (longest matching zone on the account, falling back to
`CLOUDFLARE_ZONE_ID`), and a new `unroute` action deletes a hostname's DNS
record and Zoraxy route while leaving the app, repo and containers alone.
`disable` stops the app and keeps the name; `delete` takes the repo with it;
neither is what retiring an address means.

## The ACT plugin download

The site SERVES the plugin DLL: `backend/refdata/plugin/EQ2Advanced.dll`,
committed here, exposed by `routers/plugin_api.py` as `GET /api/plugin`
(metadata) and `GET /api/plugin/download`, both unauthenticated. It is built by
CI in `improvmasta/eq2advanced-act`; refresh it with
`bash scripts/update-plugin.sh` and ship this repo.

**The download is a ZIP** (`EQ2Advanced.zip`, built in memory and cached, the
DLL unchanged inside): Chrome and Edge block a bare `.dll`. The install steps
say to Unblock the zip BEFORE extracting — Explorer copies the mark-of-the-web
onto what it unpacks and ACT won't load a marked plugin.

Why committed rather than linked: that repo is PRIVATE and a GitHub Actions
artifact needs an authenticated session and expires after 90 days, so a link to
one works for nobody and eventually not even for Lindsay. 35 KB buys a download
link that works for a stranger and ships inside the container.

The Import page's first panel is the plugin: download, install steps, and the
account API key (see phase 20 — sharing is the site's job, never the plugin's,
and its controls live on the Sharing page). A header pill links to it.

Phase 18 (2026-08-04, **schema v13**): **device tokens belong to an ACCOUNT,
not a character.** Pairing used to ask "which character?" — the one question
nobody can answer at pairing time, and it made every alt need its own token.
Now `POST /api/tokens` mints for the user, and each ingest batch carries
`character` (the plugin reads it off `eq2log_<Name>.txt`); an unseen name is
created on the spot, exactly like an upload does. `hello` answers for the
account and lists what is receiving. A batch with no character and no legacy
binding is 422 — the parser cannot resolve subjects without knowing whose log
it is. Migration `_rebuild_device_tokens` backfills `user_id` through the old
character and KEEPS `character_id` as the fallback for pre-v13 plugins; verified
on the real DB, 3 tokens intact.
GOTCHA: `process_batch(token_row, char, ...)` — `token_row` used to BE the
character row, so anything reading `token_row["id"]` for a character id is a
bug (`rebuild_zone_runs` was one).
The Import page is now the whole onboarding: plugin download, pairing, drag-drop
uploader, imported-log table. `/characters` is off the nav and must not be linked
— upload derives the character from the FILE NAME (`components/UploadDrop.jsx`),
so nobody is asked to pick one.

Phase 19 (2026-08-04, **schema v14**): **manage-page UI unification + the back
catalogue becomes a choice.** The non-raid pages (Import / Sharing / Account /
Admin) share one pattern: pagehead → cards with small-caps h2 + one line of
`.note` → lists as `table.data` or ruled rows → forms as `.formcol`. A group is
never a pill — it's a `.togglerow` (switch + name), used by AutoShare and
ShareDialog alike. The Sharing page is auto-share first (one `.autochar` row
per character), then Groups as a ruled master–detail (`.mdgrid`): list left,
open group right with Members table, then a one-line Invite section (code +
Copy invite link + quiet `new code · turn off` text links). The plugin box on
Import is state-aware: install steps until paired, a status line + collapsible
steps after; its auto-share block is gone.
**`character_shares.since_ts`** (NULL = back catalogue included, else only runs
with `started_ts >= since_ts`): a NEW auto-share defaults to new-raids-only and
an "include past raids" checkbox on the enabled row flips it; since_ts pins to
the FIRST enable and survives later saves. The guard lives in all three query
sites — `PERSONAL_RUN_IDS`, `shares_for_runs`, and `set_run_shares`' `auto` set
(where a share that doesn't reach the run must NOT leave a `hide` behind).
`PUT /characters/{id}/shares` takes `shares` `[{group_id, history}]`; bare
`group_ids` still means history included. Pinned by
`test_auto_share_new_raids_only_excludes_the_back_catalogue`; migration is a
shape-guarded ADD COLUMN, existing rows stay NULL (nothing already visible
disappears).

Phase 20 (2026-08-04, **schema v15**): **Sonarr-style API key + the manage
readability scope.** The QR/shown-once pairing is gone: the account has ONE
API key, persistent and readable on Import behind Show/Copy/Refresh — exactly
the control everyone running Sonarr/Radarr already knows. `device_tokens`
gained `token_plain` (v15); ingest lookups still go through the hash, and the
ingest-only scope is what makes storing the plaintext acceptable.
`GET /api/tokens` serves the plaintext for LIVE keys only (owner-only route);
`POST /api/tokens/refresh` revokes every live key and mints the one
replacement (`test_api_key_is_readable_and_refresh_replaces_it`). Plain
`POST /api/tokens` keeps its old semantics for tests/scripts. Keys minted
before v15 have no plaintext — the UI says so and offers Refresh. The plugin
side needs nothing: `ApplyPairPayload` accepts a bare pasted token and its
Host already defaults to `https://eq2advanced.com`. The `qrcode` npm dep is
removed.
**Readability**: raid pages keep the tight data scale; the manage pages
(Import/Sharing/Account/Admin/Characters/Login) sit in a `.manage` scope that
overrides the type tokens one step up (base 14px), brightens `--text-muted`
per theme, and adds hierarchy — the subject of a row (group name, join code,
API key, character) reads larger than its metadata. Retune in the `.manage`
block in `base.css`, not per component.

## The fight clock (2026-08-04, PARSE_VERSION 15)

The encounter's clock runs to the GROUP's last action, not to the last log
line in it. `pipeline/encounters.split_trailing_corpse` stops it there and
hands a dead mob's trailing tick its own encounter.

Verified against ACT exports (`/home/lindsay/tmp/act-export*.xml`), which are
the ground truth to use — get one per fight rather than tuning off screenshots:
the Freeport pull (28s + a stub, every number exact), the `a knotted guardian`
wipe (40s exact, 24/26 damage exact), and the Malkonis D'Morte kill (27/30
exact, cures exact). Mob damage never extends the clock even while the mobs are
alive and hitting bodies; heals after the kill never did either.

Membership and clock are DIFFERENT questions — the tail stays in the encounter
(zone totals unchanged), only the duration stops. ACT's own source
(`ACT_English_Parser.cs`) has its kill-based `EndCombat` commented out and no
combat-state handling at all, so our rule is a behavioural match, not ACT's
mechanism. Before re-deriving it, read `ARCHITECTURE.md` -> "The corpse tail"
for the table of rejected rules and the three open gaps (ACT opens an encounter
on THREAT ~3s earlier; the boss's Damage column is ~10% light in statsroll;
deaths on mob rows and other players' pets).

Phase 21 (2026-08-04, the raid list as a list) is BUILT, frontend + two list
fields, no schema change. `GET /api/zone-runs` gained **`raid_dps`** (player
damage over `combat_s`, from the same query as the sparkline) which replaces
Peak DPS, and **`shared_via`** (`groups.shared_via_for_runs`) — the viewer's
mirror of `shares_for_runs`, so a raid shared WITH you finally names the group
that brought it. Both carry `group_id`; the toolbar's ONE
source filter (`Mine` + a pill per group, OR'd, empty = everything) replaced
two controls that sat on the same axis, and a gear beside it deep-links
`/groups?g=<id>`. **Named is gone from the
UI** (still written, not trusted enough to print). `SortableTable groupBy`
takes an ARRAY now and draws whichever def matches the active sort — nights
under a date sort, zones under a zone sort. Selection moved onto the sticky
toolbar line (no card, no bottom bar) and says "shared with you — read only"
instead of showing nothing. Two comparisons: `RaidCompare` opens beside the
table (the list drops Timeline/Combat/Raiders to make room) and
`RaidParseCompare` is a modal over `/encounters/agg` with a per-column fight
picker, matching raiders BY NAME across raids. The topnav **Live pill** is now
the parse-state light (streaming / parsing / idle); Home no longer prints
"Parsing…". See `ARCHITECTURE.md` -> "The raid list as a list".

Phase 22 (2026-08-04, **schema v16**): **auto-share carries raids only.**
`character_shares.raids_only` (new shares 1, existing rows migrated to 0 so a
migration never revokes access somebody already has); the Sharing page's
enabled row gained an "include group content" tick beside "include past raids",
both starting at the narrow reading. The read-time guard is ONE definition —
`groups.AUTO_SHARE_REACHES` (since_ts window + size + no `hide`) — used by all
four query sites: `PERSONAL_RUN_IDS`, `shares_for_runs`, `shared_via_for_runs`
and `set_run_shares`' auto set. That last one is the trap the phase-17 note
warned about: a share that does NOT reach a run must be unticked with a plain
delete, never a `hide`, or the row lingers and blocks a later opt-in. Pinned by
`test_auto_share_carries_raids_only_by_default` (a real eight-man roster and a
solo zone in one log). `PUT /characters/{id}/shares` takes `group_content` in
the `shares` form; the bare `group_ids` form keeps its pre-v16 meaning
(everything, past included).

Phase 23 (2026-08-04, **schema v17**): **deleting a group is reversible, and it
costs you typing the name.** `groups.deleted_ts` — a delete erases nothing, so
members, invites, join code and every share stay put and an admin restores the
group with one row update (`GET /api/admin/groups` +
`POST /api/admin/groups/{id}/restore`, a "Deleted groups" card on the Admin
page). That makes "deleted" a READ-TIME condition like the rest of `groups.py`:
the guard is written once as `groups.LIVE_GROUP(col)` and spliced into
`AUTO_SHARE_REACHES` (covering all four auto-share sites), the `run_shares`
branch of `PERSONAL_RUN_IDS`, `shares_for_runs`, `shared_via_for_runs`, and —
because membership rows survive — `is_member` / `member_role` / `my_groups` /
`group_by_code`. GOTCHA: miss one of those and a deleted group goes on sharing
raids; the deleted row KEEPS its join code (unjoinable, still reserved, so a
restore is exact). `DELETE /api/groups/{id}` now requires `?confirm=<name>`
matching byte for byte, case included, enforced server-side. Pinned by
`test_deleting_a_group_needs_its_name_and_can_be_restored`.
Also `POST /api/admin/users/{id}/username` (Rename on the Accounts table):
nothing but `users` stores a username — everything else points at the user id —
so it is a relabel and the account stays signed in.
Also frontend: the Compare-parses modal sizes itself to its columns
(`.modalcard.pccard`, `--pccols`) instead of stretching two raids across 1120px.

Phase 24 (2026-08-04, **schema v18**): **one raid, several uploaders.**
Everyone in a raid runs their own ACT, so a night shared with a guild group
arrived four times and the list called it four raids. `backend/raidmatch.py`
says which runs are the same night, at READ time over what the viewer can see:
same zone (NULL-safe), windows overlapping ± `CLOCK_SKEW_S` (120s — the epoch
prefix comes off the raider's own machine), and enough of the same people.
The roster is the rule that says NO (two guilds in one instance zone at one
hour pass the first two and share nobody), which is why **`zone_runs.roster_json`
is new** — `_raider_count` became `_roster` and `raider_count` is its length.
Old rows are NULL until the startup relink rewrites them; a missing roster
costs the match its cross-check, never a wrong merge.
**Precedence is deliberately split.** `GET /api/zone-runs` stamps `raid_key`
(the cluster's lowest run id), `parses` and `primary` — the site's pick, the
same for everyone, scored by COVERAGE (fights, combat time, roster, ties to the
first upload) so a stranger doesn't land on someone's two-pull parse. **Your own
parse wins**, and that is the browser's call (`Home.jsx chooseParse`), because
it depends on who is looking and the payload is shared. The list draws one row
per raid with a `Parse` select; the raid page has the same control in its head
and switching NAVIGATES (`GET /api/zone-runs/{id}` → `alternates`, filtered
through `VISIBLE_RUN_IDS` — it re-sorts raids you could already open, it is not
a directory of who parsed the night). Clustering runs AFTER the source/size
filters. Pinned by `backend/tests/test_raidmatch.py`. See `ARCHITECTURE.md` ->
"The same raid, uploaded by several people".

## Ship log

- 2026-08-04 (claude): Phase 24: one raid, several uploaders — raidmatch clustering (schema v18 roster_json), your parse first, a Parse switch on the list and the raid page
- 2026-08-04 (claude): Import page rebuild: account-scoped pairing (schema v13), drag-drop uploader, no character prompt, no ACT export box
- 2026-08-04 (claude): Serve the ACT plugin from the site: download + install steps + auto-sharing on Import, header pill
- 2026-08-04 (claude): Revert phase 17: sharing belongs on the site, the ACT plugin only sends logs (schema v12 drops session_shares + can_share)
- 2026-08-04 (claude): Phase 17: sharing from the ACT plugin (schema v11) — session_shares, token can_share scope, share_groups on ingest batches; also carries phases 11-16, which were still uncommitted
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
