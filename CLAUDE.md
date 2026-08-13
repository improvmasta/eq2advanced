# eq2advanced — Claude context

## Behavior

- Be concise, make focused changes, prefer updating existing files over adding
  abstractions, keep secrets out of the repo.
- Use the local helper scripts for restart and shipping.

## Read also

- `ARCHITECTURE.md` — the INDEX of the design reference in `docs/*.md`. Read only
  the topic file for the area you are changing, before touching it. New design
  decisions and their evidence go there, not here.
- `AGENTS.md` — agent instructions and provisioning notes. `codex.md` is a pointer
  to this file.

## App

- Public URL **https://eq2advanced.com** (`www` too — both DNS-only, own Cloudflare
  zone). `eq2advanced.jupiterns.org` is RETIRED.
- Local port 8450; dev binds `0.0.0.0:8450`.
- Image `ghcr.io/improvmasta/eq2advanced:main`, container on 10.1.1.5.
- **The public hostname points at the DEV box** (deliberate, since 2026-08-03).
  Back to the container:
  `/home/lindsay/scripts/provision-app.sh route eq2advanced 8450 --deploy-server media`

## Commands

```bash
bash restart.sh
.venv/bin/python -m pytest backend/tests/ -q   # golden fixture = /home/lindsay/bobby.txt
npm --prefix frontend run build                # SPA → frontend/dist
SHIP_TOOL=claude bash ship.sh "message"        # Ship log + commit; pushes on main
```

Host CLI tools, session helpers and provisioning commands live in
`/home/lindsay/CLAUDE.md` and `/home/lindsay/AGENTS.md` — do not duplicate them
here.

## Stack

FastAPI + SQLite (WAL) in `backend/`; Vite + React SPA in `frontend/`, built to
`dist/` and served by the API process. `DATA_DIR` (`./data`, `/data` in the
container) holds `eq2advanced.db`, `uploads/` (gzipped raw logs, content
addressed), `raw/` (live-ingest chunks), `parseshots/`, `noteshots/` and `icons/`.
Schema is at **v35**; migrations in `db.py` are guarded by table SHAPE, not
`user_version` (the dev reloader can stamp the version mid-edit).

## The rules — don't relitigate these

One line each; the evidence is in the named `docs/` file. Read it before arguing
with a rule or working near one.

**Deployment** (`docs/runtime.md`)

- **Never ship, never deploy.** The container on 10.1.1.5 is Lindsay's.
- **The Cloudflare proxy stays off** — its 100 MB body cap eats raid backfills and
  it breaks HTTP-01 renewal.
- **`siteconfig.py` owns the three request facts the proxies falsify** (client
  address, scheme, public base URL). Never `request.client.host`/`base_url`.

**Sharing and visibility** (`docs/sharing.md`)

- **Sharing is set on the site, never by the uploader.** A device token sends log
  lines and nothing else.
- **`groups.py` owns the one visibility predicate**, decided at READ time. A
  standing-share branch has FOUR query sites and all four are traps; every branch
  reaching a group must carry `LIVE_GROUP`.
- **A guild share matches the UPLOADER's character's Census guild** (never the
  run's majority-vote tag), per-user, member-gated, `guild_checked = 1` only.
- **Seeing is never changing** (`owned_zone_run`); authorization is per ENCOUNTER
  (`visible_encounters`), never per session.
- **Hiding is a SECOND predicate beside sharing, never folded in** —
  `VISIBLE_UNHIDDEN_RUN_IDS` wraps `VISIBLE_RUN_IDS`. A hidden fight still
  SEGMENTS and never COUNTS.
- **A reader dismissing a shared raid is a THIRD predicate** (`run_dismissals`,
  `LISTED_RUN_IDS`, read by the raid list alone) and is not a revocation.
- **Admin is operational, not omniscient** — `role='admin'` is absent from every
  visibility decision.
- **Private chat is stripped at INGEST, never at display** (`pipeline/redact.py`);
  the classifier is an ALLOWLIST that fails closed and imports its patterns from
  `classify`; the content address stays the sha256 of the ORIGINAL bytes.

**Ability knowledge, Census and the wiki** (`docs/census-abilities.md`)

- **A pet or proc label is a CLAIM, and only a human makes one** — the ladder is
  `ability_rulings` > curated seed > no label. The machine only nominates
  candidates for `/admin/abilities`.
- **`You prepare <X>` does NOT print for an AA activation** — `gamewiki.activated`
  settles pressed-vs-proc and is checked BEFORE the prepare-line test.
- **The wiki ingest is ERA-FILTERED (EoF) and must stay that way**; run
  `tools/sync_wiki.py` BY HAND, never on a schedule.
- **A NAME is not a key** — `wiki_abilities` is keyed (name, kind), same-name AAs
  MERGE tiers, and a scribed Census record beats the wiki.
- **Census answers "spell, AA, gear or deity" — read it, don't guess.** "No cached
  spell casts it" means GEAR, and gear is CLOSED AS WONTFIX (see Open).
- **A grant is to a TIER of EQ2's class tree, not a class** (`classtree.expand`);
  not the same thing as the ROLE map in `coach/descriptive.py`.
- **`role` is `user|curator|admin`**; curator opens the Abilities console and
  nothing else; none of the three reaches anybody's parse.
- **Census by NAME is ground truth for the whole raid** (`census/roster.py`); needs
  a real `CENSUS_SERVICE_ID`; backfill via `tools/sync_roster.py`.
- **A raid's guild is a MAJORITY VOTE of its roster**, abstaining readily; the tag
  is derived, so it is RECOMPUTED (`retag_runs`), never maintained.
- **Census `crc=` silently rejects comma OR-lists** — one request per crc. Tests
  never touch live Census (fixtures + `CENSUS_AUTO_REFRESH=0`).

**Parser and stats** (`docs/parser.md` — read it BEFORE touching the parser or
segmentation)

- **Bump `PARSE_VERSION`** (`pipeline/ingest_writer.py`) after ANY parser or rollup
  semantics change; the startup sweep reparses stale sessions.
- **A segment is only a FIGHT if the raid engaged it** (`_ENGAGE_KINDS`); an ally
  death makes a no-damage segment a WIPE.
- **`/act end` ends the fight, live and on rebuild** — it hard-cuts like a zone
  line, nothing trails into it, and `Segment.ended_by_cmd` commits it without
  waiting out `CLOSE_S`.
- **Do NOT re-add trailing-event trimming** — it regresses cures/EncHPS.
- **Everything that vetoes a mob reclassing claims the name is a PERSON**;
  `roster_prescan` is the authority, softer signals never veto alone.
- **A bare capitalized name is a raider, a boss OR a dumbfire** — only behavior
  tells them apart; nothing a mob or pet can also produce is proof.
- **Order of class authority per fight**: screen > era > Census > pooled vote.
  Census must never relabel a raid from before a betrayal.
- **A class change is a DATE, not a tie** — on a deadlocked vote, check whether the
  contenders' ability windows are disjoint.
- **Ground truth is an ACT XML export**, one per fight — not screenshots.
- GOTCHA `process_batch(token_row, char, …)`: `token_row` is an ACCOUNT token, not
  a character row.

**Live, notes and replay** (`docs/live.md`)

- **The live meter is a VIEW and writes nothing**; its arithmetic deliberately
  matches `roll_encounter`.
- **A finished fight on the dashboard is THE PARSE** — the same `ParseView.jsx`
  `/zones/:id` renders, not a summary of it. The cut-down recap is DELETED; don't
  rebuild a second shape for a finished fight.
- **The pull in progress is the rail's LAST ROW**, held as `saving` until its
  encounter commits (bounded by `HOLD_MS`), and an ELLIPSIS between pulls.
- **A fight ENDS at `GAP_S` and only COMMITS at `CLOSE_S`** — the payload's `ended`
  comes from the LOG clock, never the wall clock. Live `elapsed_s` is
  damage-to-damage like `Segment.end_ts`.
- **`stale` means the UPLOADER is quiet, never "between pulls".**
- **Two clients is two live sessions, and the dashboard follows the one being
  PLAYED** (`liveliest`) — `in_combat` first, then `last_ingest_ts`; never out of a
  pull in progress. `in_combat` means an open segment whose last DAMAGE was inside
  `GAP_S`.
- **Resolving nothing is not knowing nothing** — `livemeter.Names` decides who a
  name is from `live.snapshot_context` plus `refine_known_mobs`. A seeded mob
  outranks the roster and only CENSUS vetoes it.
- **A stranger's class is asked of Census DURING the pull**, off the ingest thread,
  once per name per session, with backoff on failure.
- **A meter row is a rate, never a total**, folded at 12 rows; the overlay keeps a
  hard `max_rows` instead. **Max hit is the one exception.**
- **The parse does not ANIMATE; only its clocks move** (`lib/smooth.js`). Tweened
  figures and sliding bars were built and REMOVED. **A clock stops when its fight
  does.**
- **The AoE drain bar is the COMPOSITOR's, never the ticker's** — a CSS animation
  seeked with a negative `animation-delay` and keyed on `next_due_ts`.
- **The mini parse and the stream overlay are ONE component** (`MiniParse.jsx`);
  `MiniRail` is only the dock. A change to one IS a change to the other.
- **The rail's switches are the RAIL's**; the meter's chips drive the middle column
  only. Every meter off is a setting, not an empty state.
- **A translucent fill is `rgba(var(--x-rgb), 0.NN)`, NEVER `color-mix()`** — the
  parse draws in four places and only the dashboard is a current browser.
  `stats.rankColor` is the one exception. Keep the `-rgb` pairs in `tokens.css` in
  step across both themes.
- **Smaller type needs MORE contrast, not the same contrast smaller**; sharpness is
  whole pixels and a hinted face, not weight. Watch for `rem` in compact rules.
- **In-game rows are a GRID with content-independent tracks, never a flex run**, and
  the in-game page grid packs to the top (`align-content: start`).
- **NOTHING ON THE AoE PANEL MOVES THAT DOES NOT HAVE TO** — rows are ordered by
  FIRST CAST and read by POSITION. The one exception is the landing flash.
- **Live AoE detection imports `aoes.py`'s constants and clustering.** For an
  ability ACT's list KNOWS, reach stops deciding what a cast is (`aoes.anchors`).
- **Don't bound how far a cluster runs from its start** (`aoes._cluster`) — merging
  is the failure to prefer.
- **The countdown's number is LEARNED, not ACT's** (`pipeline/aoelearn.py`,
  `aoe_cycles`): `learned` > `reported` > this pull, pooled SITE-WIDE, adopted only
  on agreeing CLEAN intervals across distinct PULLS (`aoelearn.pull_keys`). A
  suggested timer is OFFERED, never applied.
- **A reuse debuff moves some AoE timers and not others, and which is MEASURED**
  (`refdata/reuse_debuffs.json`); matched on WHAT IT LANDED ON, never the source.
  **A cycle belongs to the state at the cast that STARTED it.** A swiped bar is ONE
  SPAN with a tick at the normal timer.
- **A cast is a MOMENT; a damage shield is a CONDITION** (`aoes.SUSTAINED_RUN`) —
  only DURATION separates them.
- **A timer is per (MOB, ability).** A mob that SPLITS is a special case and is
  written down (`refdata/split_mobs.json`); **how many bodies a name has is game
  knowledge, never inferred from parse shape.**
- **An audit's threshold is not a panel's** — the Spell timers panel additionally
  needs a reported timer OR `RAID_FRACTION` of the raid reached.
- **A cast that named a second and did not happen is admitted MISSED at `MISSED_S`
  (15s)**, which also stops it anchoring the burn window; a row with no timer leaves
  at `OVERDUE_DROP_S` (60s) from its last cast.
- **The two hand marks are JOUST and MINI, keyed by ability NAME**, on the ACCOUNT
  (`user_marks` v35) with localStorage as the cache in front; nothing said defaults
  to whether ACT's list knows the ability. MINI decides eligibility,
  `MINI_TIMER_ROWS` decides capacity.
- **A note is keyed by (user, zone, named), NEVER by encounter**; the zone is the
  BASE name.
- **Which expansion a zone came from is REFERENCE DATA, never inferred**
  (`backend/zones.py` ← `refdata/zone_eras.json`), synced BY HAND.
- **The overlay token is a capability in a URL** — it reaches the live meter and
  nothing else; revoked and never-existed answer the same; `enabled:false` is not
  revoked. Two kinds (`overlay_tokens.kind` v34) because revoking is per URL.
- **A correct token is not a guess — `_resolve` looks it up BEFORE the rate
  limiter**, and a hit no longer `clear()`s the bucket.
- **A failed request is not a dead link** — only a 404 latches "no longer active"
  and stops the poll; everything else retries. Never let a hiccup become a permanent
  state on a page nobody can refresh.
- **A replay is the live meter fed from a file, and it is TWO gates**:
  `require_curator` gates the TOOL, `visible_encounters` gates the FIGHT. It writes
  NOTHING. It also feeds that account's overlay (`replaybus.py`); the `replay` block
  never crosses.

**Display and import** (`docs/zoneruns.md`, `docs/compare-import.md`)

- **A mob earns a Damage-tab row on what it TOOK, not what it dealt.** Raiders keep
  the old test.
- **Rank coloring is PLACEMENT within the row's role**, never distance from a
  median; no role or under `MIN_PEERS` = no color.
- **A parse table is FROZEN** (`SortableTable`'s `frozen`): the header row and the
  name column hold still and the pinned cells are OPAQUE.
- **A long ability name shortens only when the table cannot fit**; un-shortening is
  asymmetric on purpose.
- **Parses side by side scroll as ONE** (`syncScroll` groups).
- **An imported screenshot is a CLAIM, kept out of everything that aggregates** (no
  session/character/encounter/run; private, no group predicate). The row ladder is
  FITTED, the decimal mark is ARITHMETIC, the fight length comes from the TABLE; a
  cell that fails a check it was subject to is blanked, not published wrong.
- **`/characters` is off the nav and must not be linked** — an upload derives the
  character from the FILE NAME.
- **eq2lexicon CANNOT be framed** (`X-Frame-Options: DENY`); reverse-proxying it
  through this backend is REJECTED. wikQ2 frames, and its tab is HIDDEN rather than
  unmounted.
- **The roster cooperation graph was REJECTED** (moved 0 of 49 real runs); don't
  rebuild it without a log where the presence rule demonstrably fails.
- **Loot is CHEST loot; a corpse drop is not loot**, and loot is written BESIDE the
  parse, never into `events` (so it needs no `PARSE_VERSION` bump). A chest belongs
  to the fight its MOB names, not to the clock.
- **An item's rarity comes from Census, its picture from EQ2i.** The log's item id
  is the Census item id, so item lookup is exact and is not the closed gear-proc
  problem. The hover card is a REPLICA of EQ2i's item box, never a screenshot or its
  HTML, and it does NOT theme. `ERA_HIDDEN` drops stats TLE does not have yet, and
  Census's `all` is ABILITY MOD.
- **The lotto block is exact, `/random` dice are not** — never mix dice into a lotto
  block.
- **A doubled asterisk before a slash closes a JS block comment** — check
  `npm run build` output, not just its exit code.

## What the app is

Details per area live in the `docs/` file named beside it.

- **Ingest** (`docs/live.md`) — `/import` is the whole onboarding: plugin download,
  account API key, drag-drop uploader. Logs arrive as uploads or live batches
  (`/api/ingest/*`, Bearer device token — a frozen contract with
  `improvmasta/eq2advanced-act`). A live session is rebuilt from raw at close.
- **Navigation is zone runs, not files** (`docs/zoneruns.md`) — a run is one
  contiguous visit to one zone by one character. `/` is the raid list, `/zones/:id`
  the raid page, `/sessions/:id` the per-file debug view. Raids are EDITABLE
  (hide/delete/merge/split), keyed by encounter FINGERPRINT so edits survive
  reparses. `raidmatch.py` collapses the same night from several uploaders into one
  row with a Parse switch.
- **The raid page** (`docs/zoneruns.md`) — Damage/Healing/Defense/AoEs/Timeline/
  Class/Loot tabs (Insights hidden), fight rail left, drilldown right. Loot is the
  one tab that is not the parse. The tabs and tables ARE `ParseView.jsx`, which the
  dashboard renders too; `ZoneRun.jsx` is the page around it. Pets/NPCs switches;
  per-tab per-browser column memory; Deaths is two columns. The Class tab is the
  `pipeline/classstats.py` registry — one `@register` function per class, `blurb`
  required on every metric.
- **The raid dashboard** `/live` (`docs/live.md`) — fight rail, ACT-shaped live
  meter with AoE countdowns, ACT-style mini overlays docked to either window edge,
  notes + pasted screenshots by zone/named, an OBS stream overlay and an EQ2
  in-game browser window (each its own token URL — the same mini parse at three
  sizes), and curator replay of any visible fight, which the overlay can watch too.
- **Compare** `/compare` (`docs/compare-import.md`) — any parses side by side,
  signed-out too; the whole comparison lives in `?c=` so a link IS the comparison;
  the picker is a faceted band computed in the browser; the last column is the
  screenshot dropslot. Every dropdown is `Picker.jsx`, never `<select>`; open panels
  render into `document.body` (backdrop-filter stacking trap).
- **The sibling TLE sites** (`docs/zoneruns.md`) — the top bar carries plaques out
  to wikQ2 and eq2lexicon. wikQ2 opens as a tab inside the shell (`/wiki`, an iframe
  kept mounted); eq2lexicon opens in a new tab because it refuses to be framed.
- **Accounts and sharing** (`docs/sharing.md`) — username + password, no email;
  security-question recovery. Groups via invite/join code/link; auto-share by
  character or by Census guild tag.
- **The admin console** (`docs/sharing.md`) — five tabs; an alert is something
  BROKEN (`receiving` is healthy); the accounts table is searched/sorted/paged on
  the SERVER. Feedback is triaged open → planned → closed.
- **Coach and Census** (`docs/coach.md`, `docs/census-abilities.md`) — intact behind
  `coach_api` and the hidden Insights tab.

## The ACT plugin

`backend/refdata/plugin/EQ2Advanced.dll` is committed (source repo is private,
Actions artifacts expire) and served by `routers/plugin_api.py` as a ZIP — browsers
block a bare `.dll`, and the install steps say Unblock BEFORE extracting. Refresh
with `bash scripts/update-plugin.sh`. Source: `/home/lindsay/eq2advanced-act`,
builds here with `bash build.sh`.

## Open

- **Two dummy parses at different Ability Mod** — the abmod marginal is only real
  once Lindsay runs them and flags both (`POST /sessions/{id}/calibration`).
- **Ascent of the Awakened drilldown cross-check** — the 2026-08-02 ACT screenshots
  were never diffed column-for-column; that log isn't uploaded.
- **AA modeling** — a curated per-class `aa_effects` table, not a full tree ingest.
  Discuss with Lindsay before building it.
- **Gear proc coverage is CLOSED AS WONTFIX** (2026-08-05): ~212k items, no reverse
  index, 13/60 hit rate on a full-text trial. The remedy is a curator looking one up
  by hand. Reopen only if Fandom enables CirrusSearch (`insource:`).
- **Buff attribution** — another player's buff proccing on you parses as yours;
  sourceless `is hit by` pools under "Unknown". Real utility DPS needs buff uptime
  windows in the parser.
- **Third-person cast lines are dropped except the curated ones**
  (`parser/buffs.py`). A raid-wide cast timeline needs the generic form plus a
  flavor → ability-line map, and the map is the work.
- **Proc-derived buff coverage cannot be anything else** — where a buff prints no
  cast, landing or fade line, its proc is the only evidence, so every number is a
  floor; don't widen `JOIN_GAP_S` to "find" more coverage.
- ACT residuals, in size order: ~3s earlier open on a THREAT pull, boss's own Damage
  column ~10% light in `statsroll`, ACT counts deaths on mob rows.

## Ship log

- 2026-08-13 (claude): Docs pass: tighten CLAUDE/AGENTS/README and the docs/ reference, move the skillissue proposal into docs/
- 2026-08-13 (claude): Crowdsourced AoE timers, account-kept hand marks, and a reflect countdown for Treyloth
- 2026-08-10 (claude): Live meter: Census resolves strangers mid-pull, AoE rows with no timer expire (carries /act end, joust marks, overlay text scale)
- 2026-08-09 (claude): Plugin update copy ships with the build (refdata NOTES), not hardcoded in the page
- 2026-08-09 (claude): Publish ACT plugin 0.2.1 (never skips unsent log on a failed send)
- 2026-08-09 (claude): Loot tab: chest drops, EQ2i-style item cards and roll history (schema v32)
- 2026-08-08 (claude): Live dashboard build-out (mini parse/overlay dock, livebus SSE wakeups, smooth clocks, ParseView), zone eras as reference data, Features page, docs/ split out of ARCHITECTURE
- 2026-08-07 (claude): Replay a recorded fight through the live meter (curator/admin), no writes
- 2026-08-07 (claude): Raid dashboard: the fight in progress (livemeter partials), raid notes by zone/named (v28), stream overlay (v29)
- 2026-08-06 (claude): Docs and repo cleanup: rewrite README, drop shipped plan files, remove dead ShareBar component + CSS
- 2026-08-05 (claude): Pets and procs stop being inferred: ability_rulings + the Abilities console (curator role), EQ2 class tree, and the wiki as reference data (schema v23, PARSE_VERSION 20)
- 2026-08-05 (claude): Sharing page rebuild (Groups + Automatic sharing side by side, guild-tag auto-share UI, settings-list switches)
- 2026-08-04 (claude): Phase 24: one raid, several uploaders — raidmatch clustering (schema v18 roster_json), your parse first, a Parse switch on the list and the raid page
- 2026-08-04 (claude): Import page rebuild: account-scoped pairing (schema v13), drag-drop uploader, no character prompt
- 2026-08-04 (claude): Serve the ACT plugin from the site: download + install steps + auto-sharing on Import, header pill
- 2026-08-04 (claude): Revert phase 17: sharing belongs on the site, the ACT plugin only sends logs (schema v12 drops session_shares + can_share)
- 2026-08-03 (claude): Phase 9+10: editable raid list, import hub, fight rail rebuild, engagement v3, read caches
- 2026-08-03 (claude): Zone runs phase 6: encounter deep-links resolve to runs (via dup_of)
- 2026-08-03 (claude): Zone runs phase 5: checkbox multi-select + ComparePanel (per-metric grouped bars from agg + report data)
