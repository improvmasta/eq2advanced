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
.venv/bin/python -m pytest backend/tests/ -q   # full suite; long phases print a heartbeat
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
Schema is at **v52**; migrations in `db.py` are guarded by table SHAPE, not
`user_version` (the dev reloader can stamp the version mid-edit).

## The rules — don't relitigate these

**One line each. The WHY and the evidence live in the named `docs/` file — read
it before arguing with a rule or working near one.** A rule that has grown a
paragraph here has outgrown this file: move the reasoning to `docs/`.

**Deployment** (`docs/runtime.md`)

- **Never ship, never deploy.** The container on 10.1.1.5 is Lindsay's.
- **The Cloudflare proxy stays off** — 100 MB body cap, breaks HTTP-01 renewal.
- **`siteconfig.py` owns the three request facts the proxies falsify** (client
  address, scheme, public base URL). Never `request.client.host`/`base_url`.

**Sharing and visibility** (`docs/sharing.md`)

- **Sharing is set on the site, never by the uploader.** A device token sends log
  lines and nothing else.
- **`groups.py` owns the one visibility predicate**, decided at READ time; every
  branch reaching a group must carry `LIVE_GROUP`.
- **A guild share matches the UPLOADER's character's Census guild**, per-user,
  member-gated, `guild_checked = 1` only — never the run's majority-vote tag.
- **Seeing is never changing** (`owned_zone_run`); authorization is per ENCOUNTER
  (`visible_encounters`), never per session.
- **Hiding is a SECOND predicate beside sharing, never folded in** —
  `VISIBLE_UNHIDDEN_RUN_IDS` wraps `VISIBLE_RUN_IDS`. A hidden fight still
  SEGMENTS and never COUNTS.
- **A reader dismissing a shared raid is a THIRD predicate** (`run_dismissals`,
  `LISTED_RUN_IDS`, raid list only) and is not a revocation.
- **Admin is operational, not omniscient** — `role='admin'` is absent from every
  visibility decision. **`role` is `user|curator|admin`**; curator opens the
  Abilities console and nothing else; none of the three reaches anybody's parse.
- **Private chat is stripped at INGEST, never at display** (`pipeline/redact.py`);
  the classifier is an ALLOWLIST that fails closed and imports its patterns from
  `classify`; the content address stays the sha256 of the ORIGINAL bytes.
- **`/chat` KEEPS the three public channels** (`chat_messages` v36,
  `pipeline/chatbus.py`) — no user/character/session in the table, uploader
  deliberately unrecorded. Log redaction is UNCHANGED and the inconsistency is the
  design: do not "fix" `redact.py` to agree with it.
- **The chat channel test is default-deny by shape AND name** — the exact
  `tells <Name> (<n>),` shape plus an allowlisted name in `CHANNELS`; the number
  is a per-character slot, never identity. Live batches only, outside the explicit
  `backend/tools/recover_chat.py` operator recovery.
- **Chat dedupe is a WINDOW, not a key** (`DEDUPE_WINDOW_S`, 20s); the UNIQUE
  constraint is only the exact-match backstop.
- **The chat archive cannot be backfilled from this server** — only a player's own
  untouched log can seed it, through dry-run-first `tools/recover_chat.py`.
- **A chat date is the BROWSER's day**; the server never guesses a reader's
  midnight. Still no uploader LIST.
- **The chat light is one bit in two places** — header plaque and the `Chat` title;
  green only when SSE is up AND `connected > 0`. Red is a normal 4am state.
- **The Stats panel has NO charts** (the request) and sits UNDER the window, so it
  wears the SITE's tokens — anything inside `.eq2win` wears EQ2's. It follows the
  box's window (pinned day / live = all time), the BROWSER bins `hours`, and the
  caveat stays: a chat stat is a SAMPLE of what somebody's plugin relayed.
- **A VISITOR IS A DAY, NOT A PERSON** (`visitors.py`, `visit_days` v37) — id is
  `sha256(day salt + address + agent)`, salt DELETED after `SALT_KEEP_DAYS`.
  "Unique visitors this month" is uncomputable and must not be faked (totals say
  **visitor-days**). No address, no user_id, no list.
- **A visit is counted where index.html goes out** (`spa.py`) — somebody
  ARRIVING, never an API call, asset or in-SPA tab change. Bots dropped on
  user-agent; counting NEVER raises.
- **WHERE and WHEN come from the CLIENT** (`visit_paths` v51, `POST /api/visit`,
  `App.jsx` beacon) — the server sees an arrival and never a destination, because
  the SPA routes itself. The route is stored as a PATTERN (`/zones/:id`), unknown
  paths collapse to `(other)`, and the table has **no visitor column at all**, so
  a page count can never be crossed with a visitor row. The beacon must never
  touch `hits`; a hit is still an arrival.
- **`app` is the honest people number, `visitors` is not** — a user-agent is a
  string anything can set and most of this site's counted traffic was crawlers.
  Running the beacon proves a browser rendered the page. Days before v51 read 0
  and mean "never asked", not "not a browser".
- **`/chat` needs NO account to read.** An account is what lets you FILL it.
- **Discord chat alerts are USER-INSTALLED private DMs** (`discord_alerts.py`,
  v39) — site login + one-time `/link` code, never Discord login or OAuth.
  Transactional outbox beside the public chat insert; no credential, worker off.
- **A chat line is split on the SERVER** (`_parts`) **and drawn on the client.**
  An item link keeps its Census id and opens the SAME examine card a chest drop
  does. A typed URL is shown as typed, `noopener noreferrer nofollow`.
- **A recruiting guild name is recognized, never guessed** — decorated names still
  need recruiting language; bare names stop at case-insensitive `is/are`.
- **Resolving a linked item is the worker's job; the card endpoint is a READ** —
  it answers `null` for an unresolved id and never calls `items.ensure`.

**Ability knowledge, Census and the wiki** (`docs/census-abilities.md`)

- **A pet or proc label is a CLAIM, and only a human makes one** — `ability_rulings`
  > curated seed > no label. The machine only nominates candidates.
- **`You prepare <X>` does NOT print for an AA activation** — `gamewiki.activated`
  settles pressed-vs-proc, checked BEFORE the prepare-line test.
- **The wiki ingest is ERA-FILTERED (EoF) and must stay that way**; run
  `tools/sync_wiki.py` BY HAND, never on a schedule.
- **A NAME is not a key** — `wiki_abilities` is keyed (name, kind), same-name AAs
  MERGE tiers, and a scribed Census record beats the wiki.
- **Census answers "spell, AA, gear or deity" — read it, don't guess.** "No cached
  spell casts it" means GEAR, and gear is CLOSED AS WONTFIX (see Open).
- **A grant is to a TIER of EQ2's class tree, not a class** (`classtree.expand`);
  not the ROLE map in `coach/descriptive.py`.
- **Census by NAME is ground truth for the whole raid** (`census/roster.py`); needs
  a real `CENSUS_SERVICE_ID`; backfill via `tools/sync_roster.py`.
- **A raid's guild is a MAJORITY VOTE of its roster**, abstaining readily, and is
  RECOMPUTED (`retag_runs`), never maintained.
- **Census `crc=` silently rejects comma OR-lists** — one request per crc. Tests
  never touch live Census (fixtures + `CENSUS_AUTO_REFRESH=0`).

**Parser and stats** (`docs/parser.md` — read it BEFORE touching the parser or
segmentation)

- **Bump `PARSE_VERSION`** (`pipeline/ingest_writer.py`) after ANY parser or rollup
  semantics change; the startup sweep reparses stale sessions.
- **A segment is only a FIGHT if the raid engaged it** (`_ENGAGE_KINDS`); an ally
  death makes a no-damage segment a WIPE.
- **`You lose consciousness!` is INCAPACITATED, never dead** (type `ko`).
- **Only YOUR OWN action ends your dead clock**, never a pet's; engagement timing
  still counts pet swings.
- **The logger's killer-less death prints NOTHING** — `pipeline/downs.py` recovers
  it from an unpaired `You regain consciousness!`, flagged `F_INFERRED`, idempotent.
- **`/act end` ends the fight, live and on rebuild** — hard cut, nothing trails in,
  committed without waiting out `CLOSE_S`.
- **Do NOT re-add trailing-event trimming** — it regresses cures/EncHPS.
- **Everything that vetoes a mob reclassing claims the name is a PERSON**;
  `roster_prescan` is the authority, softer signals never veto alone.
- **A bare capitalized name is a raider, a boss OR a dumbfire** — only behavior
  tells them apart.
- **Order of class authority per fight**: screen > era > Census > pooled vote.
  Census must never relabel a raid from before a betrayal.
- **A class change is a DATE, not a tie** — on a deadlock, check whether the
  contenders' ability windows are disjoint.
- **Ground truth is an ACT XML export**, one per fight — not screenshots.
- GOTCHA `process_batch(token_row, char, …)`: `token_row` is an ACCOUNT token.

**Live, notes and replay** (`docs/live.md`)

- **The live meter is a VIEW and writes nothing**; its arithmetic deliberately
  matches `roll_encounter`.
- **A finished fight on the dashboard is THE PARSE** — the same `ParseView.jsx`
  `/zones/:id` renders. The cut-down recap is DELETED; don't rebuild it.
- **The pull in progress is the rail's LAST ROW**, held `saving` until its
  encounter commits (bounded by `HOLD_MS`); an ELLIPSIS between pulls.
- **A fight ENDS at `GAP_S` and only COMMITS at `CLOSE_S`** — `ended` comes from
  the LOG clock, never the wall clock. Live `elapsed_s` is damage-to-damage.
- **`stale` means the UPLOADER is quiet, never "between pulls".**
- **Two clients is two live sessions, and the dashboard follows the one being
  PLAYED** (`liveliest`) — `in_combat`, then `last_ingest_ts`; never out of a pull
  in progress.
- **Resolving nothing is not knowing nothing** — `livemeter.Names` decides from
  `live.snapshot_context` plus `refine_known_mobs`; a seeded mob outranks the
  roster and only CENSUS vetoes it.
- **A stranger's class is asked of Census DURING the pull**, off the ingest thread,
  once per name per session, with backoff.
- **A meter row is a rate, never a total**, folded at 12 rows. **Max hit is the
  one exception.**
- **The parse does not ANIMATE; only its clocks move** (`lib/smooth.js`). Tweened
  figures and sliding bars were built and REMOVED. **A clock stops when its fight
  does.**
- **The AoE drain bar is the COMPOSITOR's, never the ticker's** — a CSS animation
  seeked with a negative `animation-delay`, keyed on `next_due_ts`.
- **The mini parse and the stream overlay are ONE component** (`MiniParse.jsx`);
  `MiniRail` is only the dock. A change to one IS a change to the other.
- **The rail's switches are the RAIL's**; the meter's chips drive the middle
  column only. Every meter off is a setting, not an empty state.
- **A translucent fill is `rgba(var(--x-rgb), 0.NN)`, NEVER `color-mix()`**
  (`stats.rankColor` excepted); keep the `-rgb` pairs in `tokens.css` in step
  across both themes. **Smaller type needs MORE contrast**, and in-game rows are a
  GRID with content-independent tracks, never a flex run.
- **NOTHING ON THE AoE PANEL MOVES THAT DOES NOT HAVE TO** — ordered by FIRST
  CAST, read by POSITION. The landing flash is the one exception.
- **Live AoE detection imports `aoes.py`'s constants and clustering**; for an
  ability ACT's list KNOWS, reach stops deciding what a cast is (`aoes.anchors`).
- **Don't bound how far a cluster runs from its start** (`aoes._cluster`).
- **The countdown's number is LEARNED, not ACT's** (`pipeline/aoelearn.py`):
  `learned` > `reported` > this pull, pooled SITE-WIDE, adopted only on agreeing
  CLEAN intervals across distinct PULLS, and OFFERED rather than applied.
- **A reuse debuff moves some AoE timers and not others, and which is MEASURED**
  (`refdata/reuse_debuffs.json`), matched on WHAT IT LANDED ON, never the source.
  **A cycle belongs to the state at the cast that STARTED it.**
- **A cast is a MOMENT; a damage shield is a CONDITION** (`aoes.SUSTAINED_RUN`) —
  only DURATION separates them.
- **A timer is per (MOB, ability)**; a mob that SPLITS is written down
  (`refdata/split_mobs.json`). **How many bodies a name has is game knowledge,
  never inferred from parse shape.**
- **An audit's threshold is not a panel's** — Spell timers additionally needs a
  reported timer OR `RAID_FRACTION` reached. A cast that named a second and did
  not happen is MISSED at `MISSED_S` (15s); a row with no timer leaves at
  `OVERDUE_DROP_S` (60s).
- **The two hand marks are JOUST and MINI, keyed by ability NAME**, on the ACCOUNT
  (`user_marks` v35) with localStorage in front. MINI decides eligibility,
  `MINI_TIMER_ROWS` decides capacity.
- **A note is keyed by (user, zone, named), NEVER by encounter**; zone is the BASE
  name.
- **Which expansion a zone came from is REFERENCE DATA, never inferred**
  (`backend/zones.py` ← `refdata/zone_eras.json`), synced BY HAND.
- **The overlay token is a capability in a URL** — it reaches the live meter and
  nothing else; revoked and never-existed answer the same; `enabled:false` is not
  revoked. Two kinds (`overlay_tokens.kind` v34) because revoking is per URL.
- **A correct token is not a guess — `_resolve` looks it up BEFORE the rate
  limiter**, and a hit no longer `clear()`s the bucket.
- **A failed request is not a dead link** — only a 404 latches "no longer active";
  everything else retries.
- **A replay is the live meter fed from a file, and it is TWO gates**:
  `require_curator` gates the TOOL, `visible_encounters` gates the FIGHT. It writes
  NOTHING. It feeds that account's overlay (`replaybus.py`); `replay` never crosses.

**The Gear Planner** (`docs/planner.md` — Phases 0-2 complete; 3-4 design)

*The catalog and its era*

- **WHICH EXPANSIONS COUNT IS THE READER'S** — EoF and/or RoK, chosen on the page.
  Era is a COLUMN, never a build-time constant; a third expansion is a re-sync.
- **The era filter reads the SOURCE, not the item.** `plan_items.era` is where it
  was INTRODUCED — displayed, never filtered on.
- **The catalog is built by INVERTING mobs and quests** (`planner/ingest.py`): the
  monster and the quest carry `patch`, `zone` and the raid/group/solo split.
- **The expansion category is not the expansion — for MOBS or QUESTS.** Mobs are
  asked for by ZONE as well (`zones.in_era`), quests by zone AND by TIER.
- **A ZONE SWEEP CANNOT SEE NEW CONTENT IN AN OLD ZONE** — the Artisan Epic runs
  out of Rivervale (Shattered Lands) and rewards level-80 gear. The TIER index
  names it, filtered on the page itself: later expansions refused by patch
  (`wiki.declared_after`), earlier carried forward by level (`wiki.era_at_least`).
- **A TRASH DROP IS REACHABLE ONLY FROM `Category:<zone> Dropped Items`** — kind
  `zone` ("World drop"), only for items no named or quest already claims.
- **CRAFTED GEAR IS INDEXED FROM NOWHERE THE INVERSIONS LOOK** — the crafted
  categories cut to the era's tier band reach it, on listings alone, and the
  RECIPE level decides the era, not the item's own.
- **A reward is the first thing a list item names, whatever names it** —
  `{{Equip}}`, `{{Item}}` or a plain link, `*` and `#` alike.
- **`obtain` is structured and IS read** (`wiki.parse_obtain`) — blank on over half
  of item pages, so never the spine, but exact where present.
- **A SET PIECE IS BEHIND A CRATE AND THE CRATE IS WHAT DROPS** — the crawl follows
  `contains` like a disambiguation and the armour inherits the source.
- **A SET TIER IS A BLOCK, NOT A LINE** (`wiki._BONUS_TIER`) — the flat stats are
  BARE lines under the proc's sub-bullets.
- **An ITEM above the era's level cap is DROPPED** (`wiki.ERA_CAP`, EoF 70 / RoK
  80). **Not the same rule as a QUEST level above the cap**, which is normal.
- **A crawl cannot report its own completeness** — `tools/planner_coverage.py`
  takes the denominator from indexes the crawl does not use, on listings only.
- **`tools/sync_planner.py` runs MONTHLY on cron** (`sync_wiki.py` unchanged), safe
  unattended because `ingest.CrawlCollapsed` REFUSES a crawl under
  `COLLAPSE_RATIO` of the last. It must NOT set `CENSUS_AUTO_REFRESH=0` (that
  switch also gates icon downloads). A Census outage is picked up by the 30-min
  probe, which finishes the item and roster backfills; a down probe is a no-op.
- **Multi-era sync resolves the graph again after all eras are stored**; dangling
  titles are counted and omitted, never invented as quests.
- **A comma in `prereq`/`next` is part of the title, never a separator** — only
  linked `prelist`/`nextlist` are multi-valued, and alternatives are OR-groups.

*Ranking*

- **The Equipment priority list is an ORDER, not numbers, and no weight is ever
  shown** — three dropdowns numbered 1-3, defaulting to Any. No sliders or cap
  math; its `required` parameter has no control. **Quick Equip is separate**:
  lexicographic whole-loadout base-stat targets, up to five priorities with
  coarse rounded sliders bracketing feasible filtered min/max totals and inline
  per-item Required checks, and no
  proc/adornment/set-bonus valuation. A target is reader intent, not a game cap;
  surplus above it stops outranking the next priority. `Require Crit Chance` is
  also a separate, default-off rule in the left Stat priorities gutter and
  survives removing Crit from the priority order. Maximum Gear Level descends
  from the live catalog cap and has an adjacent `Max Lvl` reset action.
- **The rows carrying ALL your stats lead the table**, then partial ones in score
  order — a TIER, not a filter, sorted on the SERVER.
- **EQ2 GEAR IS FOUR-STAT, so ranking N stats shows items with min(2, N) of them**
  (`FOUR_STAT_FLOOR`), counted over the stats that RANK and answered back so the
  page says "2 of 3". **Not the same control as `required`**; both apply.
- **POTENCY AND CRIT ARE NOT EQUIPMENT-SEARCH PRIORITY OPTIONS** — 80% and 72%
  of the catalog, so ordering individual rows by them orders by nothing; they
  stay on the card and as columns. Quick Equip may total/require both across a
  complete loadout.
  **Crit Bonus: TLE does not have it** (`ERA_HIDDEN_FIELDS`) — never on a card.
- **The fourteen that can be ranked, in `wiki.STAT_GROUPS` order** (game knowledge,
  from Lindsay): Abilities — abmod, casting speed, reuse speed, ability
  doublecast; Melee — haste, dps,
  multi attack, flurry, AE autoattack; Tanking — block, hate gain, mitigation,
  strikethrough; then max health alone.
- **Scores normalise against the whole selected-era catalog, not the filtered
  view** — pressing a filter must not rescore every row.
- **A rarity is asked for by the word a PLAYER uses** (`wiki.TIER_BUCKETS`): five
  buckets, not the wiki's eleven `icat` spellings. How a piece was MADE is not a
  rarity; an unrecognized value stays bucketless and `plan_items.tier` is untouched.
- **Armour weight is derived from `dtype`, never stored twice** (`wiki.armor_of`);
  **a two-hander's slot reads `Primary/2H`** (`wiki.slot_label`) and still shows
  under a Primary filter. "Main Hand" vs "One-Handed" is deliberately NOT claimed.
- **PLANNER DECIMALS ARE SOURCE PRECISION** — the two decimals the Census bridge
  preserves; never through integer `fmt.num`. **`.plantable` headers carry more
  contrast than `table.data`'s** — the parse views were not changed underneath.

*The page*

- **`/plan` HAS NO PERMANENT RAIL** — expansion toggles live in the item-search
  header, class is a FACET, and the right Outline is contextual: closed for an
  empty plan, opens on a pick, collapsible.
- **Equipment / Set Adorns / Quick Equip are compact PRIMARY TOP TABS** on the
  search block, content-width rather than stretched across it; the active tab
  is raised and gold. Quick Equip inherits editable character class/level, filters allowed
  sources and wearable armor weights, lists maximum gear level downward from
  the selected catalog cap, compares 2H against Primary+Secondary,
  scales stat targets to achievable filtered full-loadout minima/maxima, offers
  three duplicate-free choices per slot, and copies gear-only results to a new
  or explicitly overwritten character-scoped Gear Set without replacing unsaved
  working gear.
- **An empty item table says WHICH CONTROL emptied it** (`before_priorities`) and
  that the catalog is a crawl. Loading a character must NOT set the class filter.
- **Clicking an item's ROW puts it in the window**; the name cell stops the click
  and still opens the wiki.
- **THE ITEM IS NOT THE UNIT OF VALUE** — the set bonus is on a turquoise that
  detaches, so a set is its own row, shortlisting from the set view adds the
  ADORNMENT, and **a set is ONE LINE opened for the work**. The grant is typed
  per-tier stats summed up the ladder — **prose is never turned into a number**.
- **A TURQUOISE'S SLOT SUFFIX IS NOT ITS SET IDENTITY** — group on canonical
  `set_name`. **Worn set bonuses are counted off the WINDOW** and **plans are
  character-keyed.**
- **GEAR SETS ARE A STABLE CHARACTER-SCOPED CONTROL, NOT TABS** — one chooser, `+`
  saves, Rename/Delete always visible, five slots per public character
  (`planner_saved_sets`, v49), local for everyone and on the account when signed
  in. A character key is a private filing folder, never ownership. Loading or
  resetting never discards unsaved work without Save/Discard/Cancel.
- **A PLAN FLOATS OVER EQUIPPED GEAR; IT IS NOT A SNAPSHOT** — only explicit
  gear/adornment targets are saved. Exact equipped Census ids feed the private
  additive obtained ledger (`planner_obtained_items`, v52); completed targets
  leave projection/Outline work without mutating the saved plan or dirty state.
- **PLANNER CHARACTER IDENTITY IS `world:lookup_name`, FROM THE BACKEND** —
  `planner_key` files work, `lookup_name` round-trips public search, and
  `display_name` only renders. Census ids and labels never become folder keys.
- **PLANNED GEAR MUST NEVER GET TRAPPED** — one left-edge clicker when a slot has
  alternatives; never spend item-name width on `1/2` plus prev/next. Every
  non-equipped item has a direct `×`. Search-name hover compares Candidate,
  Equipped and a different active Planned item side by side for the focused slot.
- **The Outline is only the selected gear's route list** — zones containing mobs
  and reward quests, plus those quests' hard prerequisites. No prelude, no manually
  kept targets. Quest checks are browser-local; quest hover exposes `wikq2` first.
- **RoK class epics are catalog items, not prose** — a focused Primary offers the
  class Fabled first, the Mythical once the Fabled is equipped — and **wikq2 owns
  class-epic timeline structure** (`plan_epic_timelines`, v46), exported offline
  for all 24 original classes before the monthly sync writes it here.
- **`GET /api/plan/character?name=` is the ONE `/plan` route that may reach the
  network** — cache-first, stale-on-failure, no account; `plan_characters` (v43)
  caches a PUBLIC record, never account state. **A signed-in reader's OWN
  characters come from `census_snapshots`**, so they survive an outage. The cache
  refresher STOPS when a row's stamp does not move.
- **LEXICON IS THE LAST CHARACTER AND WORN-ITEM FALLBACK** (`census/lexicon.py`,
  v44) — Census and local snapshots win; labeled `source: lexicon`, replaced by the
  next refresh, enrichment bounded to the allow-list, and its failure never fails
  the character page.
- **`catalog.card` builds `items.display`'s shape** so `ItemCard.jsx` is reused —
  three ways to meet an item, one examine window.
- **Planner examine text is parsed, not printed as wiki markup** — `desc` sits
  under the item name, `EquipmentEffect` becomes the centered proc name,
  asterisk depth becomes effect bullets, and artisan restrictions stay
  separate from adventure-class filtering. Do not add `Requires Expansion`.
- **Set armor shows compact `Set [worn/total]`; the turquoise adornment owns the
  ladder.** Preserve Census threshold stats, and keep Crit Chance directly under
  Potency in the blue block. Equipped Attuneable/Heirloom renders as
  Attuned/No-Trade. Search names are bold and tiers uppercase.
- **`/plan` is IN the nav as `Gear Planner`**, useful signed out; only the five
  saved-set routes need an account. **PLAYER-NAME LINKS GO TO
  `/plan?character=<name>`**, never the external Lexicon profile.
- **Phase 0 is measured, not guessed** — 2,584 POI matches (74.86% overall, 83.46%
  of zone-labeled), median confidence 0.98. Phase 3 may use MATCHED rows; the 356
  unresolved cross-zone coordinates are not claims.

**Display and import** (`docs/zoneruns.md`, `docs/compare-import.md`)

- **A mob earns a Damage-tab row on what it TOOK, not what it dealt.**
- **Rank coloring is PLACEMENT within the row's role**, never distance from a
  median; no role or under `MIN_PEERS` = no color.
- **A parse table is FROZEN** (`SortableTable`'s `frozen`) — header row and name
  column hold still, pinned cells are OPAQUE.
- **A long ability name shortens only when the table cannot fit**; un-shortening is
  asymmetric on purpose.
- **Parses side by side scroll as ONE** (`syncScroll` groups).
- **An imported screenshot is a CLAIM, kept out of everything that aggregates.**
  Row ladder FITTED, decimal mark ARITHMETIC, fight length from the TABLE; a failed
  cell is blanked, not published wrong.
- **`/characters` is off the nav and must not be linked** — an upload derives the
  character from the FILE NAME.
- **eq2lexicon CANNOT be framed** (`X-Frame-Options: DENY`); reverse-proxying it
  here is REJECTED. wikQ2 frames, and its tab is HIDDEN rather than unmounted.
- **The roster cooperation graph was REJECTED** (moved 0 of 49 real runs).
- **Loot is CHEST loot; a corpse drop is not loot**, written BESIDE the parse and
  never into `events` (so no `PARSE_VERSION` bump). A chest belongs to the fight
  its MOB names, not to the clock.
- **An item's rarity comes from Census, its picture from EQ2i.** The hover card is
  a REPLICA of EQ2i's item box, never a screenshot or its HTML, and does NOT theme.
  `ERA_HIDDEN` drops stats TLE lacks; Census's `all` is ABILITY MOD.
- **The lotto block is exact, `/random` dice are not** — never mix them.
- **A doubled asterisk before a slash closes a JS block comment** — check
  `npm run build` output, not just its exit code.

## What the app is

One line per area; the detail is in the `docs/` file named beside it.

- **Ingest** (`docs/live.md`) — `/import` is the whole onboarding. Logs arrive as
  uploads or live batches (`/api/ingest/*`, Bearer device token — a frozen contract
  with `improvmasta/eq2advanced-act`); a live session is rebuilt from raw at close.
- **Navigation is zone runs, not files** (`docs/zoneruns.md`) — a run is one
  contiguous visit to one zone by one character. `/` raid list, `/zones/:id` raid
  page, `/sessions/:id` per-file debug. Raids are EDITABLE and keyed by encounter
  FINGERPRINT so edits survive reparses; `raidmatch.py` collapses one night from
  several uploaders into one row.
- **The raid page** — Damage/Healing/Defense/AoEs/Timeline/Class/Loot tabs
  (Insights hidden), fight rail left, drilldown right. The tabs and tables ARE
  `ParseView.jsx`, which the dashboard renders too. Class tab is the
  `pipeline/classstats.py` registry — `blurb` required on every metric.
- **The raid dashboard** `/live` — fight rail, ACT-shaped live meter with AoE
  countdowns, mini overlays docked to either edge, notes + pasted screenshots by
  zone/named, an OBS stream overlay, an EQ2 in-game browser window, and curator
  replay of any visible fight.
- **Compare** `/compare` — any parses side by side, signed out too; the comparison
  lives in `?c=` so a link IS the comparison. Every dropdown is `Picker.jsx`, never
  `<select>`; open panels render into `document.body`.
- **The sibling TLE sites** — top-bar plaques to wikQ2 (framed at `/wiki`, kept
  mounted) and eq2lexicon (new tab; refuses framing).
- **The chat box** `/chat` — General/LFG/Auction in three EQ2-styled blocks with a
  per-box filter, date and Stats panel, relayed live and KEPT as the site's record.
  Reached by the **In-game chat** plaque, never as a nav tab.
- **Accounts and sharing** — username + password, no email; security-question
  recovery. Groups via invite/join code/link; auto-share by character or guild tag.
- **The admin console** — six tabs; an alert is something BROKEN (`receiving` is
  healthy); accounts are searched/sorted/paged on the SERVER. **Visitors** is the
  day-by-day count.
- **The Gear Planner** `/plan` — what to chase in an expansion: pick the expansions,
  declare the stats you are pushing as an ORDER, read a ranked era-filtered catalog
  of every drop, quest reward and crafted piece with where it comes from, plus set
  adornments on their own axis, or generate a filtered whole-loadout Quick Equip
  draft. Load a Census character and the window projects a swap, keeps five named
  builds, and leads Primary through the class epic.
- **Coach and Census** (`docs/coach.md`) — intact behind `coach_api` and the hidden
  Insights tab.

## The ACT plugin

`backend/refdata/plugin/EQ2Advanced.dll` is committed (source repo private,
Actions artifacts expire) and served by `routers/plugin_api.py` as a ZIP — browsers
block a bare `.dll`, and the install steps say Unblock BEFORE extracting. Refresh
with `bash scripts/update-plugin.sh`. Source: `/home/lindsay/eq2advanced-act`.

## Open

- **Two dummy parses at different Ability Mod** — the abmod marginal is only real
  once Lindsay runs them and flags both (`POST /sessions/{id}/calibration`).
- **Ascent of the Awakened drilldown cross-check** — the 2026-08-02 ACT screenshots
  were never diffed column-for-column; that log isn't uploaded.
- **AA modeling** — a curated per-class `aa_effects` table, not a full tree ingest.
  Discuss with Lindsay first.
- **Gear proc coverage is CLOSED AS WONTFIX** (2026-08-05): ~212k items, no reverse
  index, 13/60 on a full-text trial. Reopen only if Fandom enables CirrusSearch.
- **Buff attribution** — another player's buff proccing on you parses as yours;
  sourceless `is hit by` pools under "Unknown". Needs buff uptime windows.
- **Third-person cast lines are dropped except the curated ones**
  (`parser/buffs.py`); a raid-wide cast timeline needs a flavor → ability-line map.
- **Proc-derived buff coverage cannot be anything else** — every number is a floor;
  don't widen `JOIN_GAP_S` to "find" more.
- **Separating RoK from TSO crafted gear** — TSO kept the level-80 cap, so
  `era_of_level` cannot split them. `obtain` gives the recipe BOOK title and that
  page carries its own patch; measure before filing on it.
- ACT residuals, in size order: ~3s earlier open on a THREAT pull, boss's own Damage
  column ~10% light in `statsroll`, ACT counts deaths on mob rows.

## Ship log

- 2026-08-22 (codex): Polish Quick Equip and feedback controls
- 2026-08-22 (codex): Reconcile Gear Planner set lifecycle
- 2026-08-21 (codex): Add targeted Quick Equip loadout builder
- 2026-08-21 (codex): Show Ability Doublecast in Gear Planner
- 2026-08-20 (codex): Fix Planner set bonus and comparison hovers
- 2026-08-20 (codex): Polish Gear Planner loadout and catalog UI
- 2026-08-20 (claude): Count where visitors go and whether they are real (v51)
- 2026-08-19 (codex): Polish Gear Planner catalog search
- 2026-08-19 (codex): Match Planner item cards to in-game examines
- 2026-08-19 (claude): Planner: find the gear three indexes and a crafted sweep were missing
- 2026-08-19 (codex): Redesign character-scoped Planner gear sets
- 2026-08-19 (codex): Add private Skill Issue loot portal
- 2026-08-19 (codex): Refine Gear Planner search and scalable outline
- 2026-08-17 (claude): Track the logger's unannounced deaths; pets no longer end the dead clock
- 2026-08-17 (claude): Gear Planner: one-line set adornment search
- 2026-08-16 (codex): Overhaul Gear Planner adornments and set pieces
- 2026-08-16 (codex): Refine character-bound planner outlines
- 2026-08-16 (codex): Document authoritative epic step coverage
- 2026-08-16 (codex): Refine Planner workspace and recommendations rail
- 2026-08-16 (codex): Polish saved gear sets and planned adornment deltas
