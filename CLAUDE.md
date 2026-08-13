# eq2advanced - Claude Context

## Behavior

- Be concise and make focused changes.
- Prefer updating existing files over adding new abstractions.
- Keep secrets out of the repository.
- Use the local helper scripts below for restart and shipping.

## Read also

- `ARCHITECTURE.md` — the INDEX of the design reference in `docs/*.md`. Read
  the topic file for the area you're changing (only that one) before touching
  it; new design decisions and their evidence go there, not here.
- `AGENTS.md` — agent instructions and provisioning notes.
- `codex.md` — a pointer here; context lives in this file only.

## App

- Public URL: **https://eq2advanced.com** (`www` too — both DNS-only, in their
  own Cloudflare zone). `eq2advanced.jupiterns.org` is RETIRED.
- Local port 8450; dev binds `0.0.0.0:8450` → http://10.1.1.15:8450.
- Image `ghcr.io/improvmasta/eq2advanced:main`, container on **10.1.1.5**.
- **The public hostname currently points at the DEV box** (deliberate, since
  2026-08-03). Back to the container:
  `/home/lindsay/scripts/provision-app.sh route eq2advanced 8450 --deploy-server media`

## Commands

```bash
bash restart.sh
.venv/bin/python -m pytest backend/tests/ -q   # golden fixture = /home/lindsay/bobby.txt
npm --prefix frontend run build                # SPA → frontend/dist
SHIP_TOOL=claude bash ship.sh "message"        # Ship log + commit; pushes on main
```

`ship.sh` is the generic helper from `/home/lindsay/scripts`. Set
`SHIP_TOOL=claude` (or `codex`) for the matching co-author trailer; the Ship
log below updates on every ship and condenses itself.

## Host context

CLI tools (`gh`, `rg`, `jq`, `fd`), the `state`/`logs`/`restart`/`ship` session
helpers, provisioning commands, and deploy notes live in `/home/lindsay/CLAUDE.md`
and `/home/lindsay/AGENTS.md` — don't duplicate them here.

## Stack

FastAPI + SQLite (WAL) in `backend/`; Vite + React SPA in `frontend/`, built to
`dist/` and served by the API process. `DATA_DIR` (`./data`, `/data` in the
container) holds `eq2advanced.db`, `uploads/` (gzipped raw logs, content
addressed), `raw/` (live-ingest chunks), `parseshots/` and `noteshots/`
(re-encoded screenshots) and `icons/` (item icons cached from the wiki, keyed
by Census icon id — reference data about the game, not about a raid). Schema
is at **v35**; migrations in
`db.py` are guarded by table SHAPE, not `user_version` (the dev reloader can
stamp the version mid-edit).

## The rules — don't relitigate these

One line each; the evidence lives in the named `docs/` file. Read it before
arguing with a rule or working near one.

Deployment and runtime — `docs/runtime.md`:

- **Never ship, never deploy.** The container on 10.1.1.5 is Lindsay's.
- **The Cloudflare proxy stays off** — the edge's 100 MB body cap eats raid
  backfills and it breaks HTTP-01 renewal.
- **`siteconfig.py` owns the three request facts the proxies falsify** (client
  address, scheme, public base URL). Never `request.client.host`/`base_url`.

Sharing and visibility — `docs/sharing.md`:

- **Sharing is set on the site, never by the uploader.** A device token sends
  log lines and nothing else (v11 built the opposite; v12 removed it).
- **`groups.py` owns the one visibility predicate**, decided at READ time. A
  standing-share branch has FOUR query sites and all four are traps; every
  branch reaching a group must carry `LIVE_GROUP`.
- **A guild share matches the UPLOADER's character's Census guild** (never the
  run's majority-vote tag), per-user, member-gated; `guild_checked = 1` only.
- **Seeing is never changing** (`owned_zone_run`); authorization is per
  ENCOUNTER (`visible_encounters`), never per session.
- **Hiding is a SECOND predicate beside sharing, never folded in** —
  `VISIBLE_UNHIDDEN_RUN_IDS` wraps `VISIBLE_RUN_IDS`. A hidden fight still
  SEGMENTS and never COUNTS.
- **A reader takes a shared raid off their OWN list, and that is not a
  revocation** — `run_dismissals` (v31) is a THIRD predicate, `LISTED_RUN_IDS`,
  read by the raid list alone: the link still opens, the owner's audience is
  untouched, and a dismissal follows the run through a rebuild
  (`carry_shares`). Your own raid is refused — that one is `hide`.
- **Admin is operational, not omniscient** — `role='admin'` is absent from
  every visibility decision.
- **Private chat is stripped at INGEST, never at display** (`pipeline/redact.py`);
  the classifier is an ALLOWLIST that fails closed and imports its patterns
  from `classify`; the content address stays the sha256 of the ORIGINAL bytes.

Ability knowledge, Census and the wiki — `docs/census-abilities.md`:

- **A pet or proc label is a CLAIM, and only a human makes one** — the ladder
  is `ability_rulings` > curated seed > no label; the machine only nominates
  candidates for `/admin/abilities`.
- **`You prepare <X>` does NOT print for an AA activation** — `gamewiki.activated`
  settles pressed-vs-proc and is checked BEFORE the prepare-line test.
- **The wiki ingest is ERA-FILTERED (EoF) and must stay that way**; run
  `tools/sync_wiki.py` BY HAND, never on a schedule.
- **A NAME is not a key** — `wiki_abilities` is keyed (name, KIND), same-name
  AAs MERGE tiers, and a scribed Census record beats the wiki (`scribed_by` wins).
- **Census answers "spell, AA, gear or deity" — read it, don't guess.** "No
  cached spell casts it" means GEAR, and gear is CLOSED AS WONTFIX (see Open).
- **A grant is to a TIER of EQ2's class tree, not a class** (`classtree.expand`);
  not the same thing as the ROLE map in `coach/descriptive.py`.
- **`role` is `user|curator|admin`**; curator opens the Abilities console and
  nothing else; none of the three reaches anybody's parse.
- **Census by NAME is ground truth for the whole raid** (`census/roster.py`);
  needs a real `CENSUS_SERVICE_ID`; backfill via `tools/sync_roster.py`.
- **A raid's guild is a MAJORITY VOTE of its roster** and abstains twice as
  readily as it commits; the tag is derived, so it is RECOMPUTED (`retag_runs`),
  never maintained.
- **Census `crc=` silently rejects comma OR-lists** — one request per crc.
  Tests never touch live Census (fixtures + `CENSUS_AUTO_REFRESH=0`).

Parser and stats — `docs/parser.md` (read it BEFORE touching the parser or
segmentation; the subject model is verified against a real raid log):

- **Bump `PARSE_VERSION`** (`pipeline/ingest_writer.py`) after ANY parser or
  rollup semantics change; the startup sweep reparses stale sessions.
- **A segment is only a FIGHT if the raid engaged it** (`_ENGAGE_KINDS`); an
  ally death makes a no-damage segment a WIPE.
- **`/act end` ends the fight, live and on rebuild** — EQ2 rejects the command
  into the log (`Unknown command: 'act end'`), which is the same channel ACT
  hears it on; it hard-cuts like a zone line, nothing trails into it, and
  `Segment.ended_by_cmd` commits it without waiting out `CLOSE_S`.
- **Do NOT re-add trailing-event trimming** — it regresses cures/EncHPS.
- **Everything that vetoes a mob reclassing claims the name is a PERSON**;
  `roster_prescan` is the authority, softer signals never veto alone.
- **A bare capitalized name is a raider, a boss OR a dumbfire** — only
  behavior tells them apart; nothing a mob or pet can also produce is proof.
- **Order of class authority per fight**: screen > era > Census > pooled vote;
  Census must never relabel a raid from before a betrayal.
- **A class change is a DATE, not a tie** — on a deadlocked vote, check
  whether the contenders' ability windows are disjoint.
- **Ground truth is an ACT XML export**, one per fight — not screenshots.
- GOTCHA `process_batch(token_row, char, …)`: `token_row` is an ACCOUNT token,
  not a character row.

Live, notes and replay — `docs/live.md`:

- **The live meter is a VIEW, and writes nothing**; its arithmetic
  deliberately matches `roll_encounter`.
- **A finished fight on the dashboard is THE PARSE — the same component
  `/zones/:id` renders** (`ParseView.jsx`), not a summary of it. The live
  meter turns into it the moment the pull ends and it stays until the next one
  opens. The cut-down "recap" (`RecordedFight.jsx`, two rate tables) is DELETED
  — don't rebuild a second shape for a finished fight. The parse takes its
  fights and its raid report as props; the dashboard's report comes from
  `/api/encounters/report?ids=` because mid-night there is no run to ask for.
- **The pull in progress is the rail's LAST ROW, never a button beside it**
  (`EncounterTree`'s `live` prop). A fight only becomes a real row when the
  writer commits it, so `Live.jsx` holds the just-ended pull on that row
  (`saving`) until its `encounter` arrives — bounded by `HOLD_MS`, because a
  segment the raid never engaged never commits at all. Between pulls that row
  is an ELLIPSIS: no name, no clock, no dot.
- **A fight ENDS at `GAP_S`, and only COMMITS at `CLOSE_S`** — the payload's
  `ended` (from `now_ts`, the LOG clock, never the wall clock) is the
  difference, and it is what stops the clock, the pulse and the rail row. Live
  `elapsed_s` is damage-to-damage like `Segment.end_ts`, so the meter and the
  card it becomes are the same length; trailing heals are still counted.
- **`stale` means the UPLOADER is quiet, never "between pulls"** — a fight that
  merely ended is what everyone is reading, so nothing dims. The mini rail is
  handed the last pull rather than null, and the middle column can be switched
  off (`Parse`: dimmed and paused) without switching off the page.
- **The display switches live in the rail head, across from the character
  name** (`headActions`: Mini, Parse, Overlay). The dashboard bar carries no
  status — the site header owns "ACT connected" and idle/in-combat.
- **Two clients is two live sessions, and the dashboard follows the one being
  PLAYED** (`liveliest`) — `in_combat` first, then `last_ingest_ts`; never out
  of a pull in progress; a hand-picked character holds it until its log goes
  quiet (`QUIET_S`). Newest-created is not a signal. `in_combat` means an open
  segment whose last DAMAGE was inside `GAP_S`, not merely an open segment.
- **Resolving nothing is not the same as knowing nothing** — `livemeter.Names`
  decides who a name is from `live.snapshot_context` (classes, mobs, players,
  bare pets, read once per session) plus `refine_known_mobs` over the open
  segment. A seeded mob outranks the roster and only CENSUS vetoes it; the
  roster vetoes only what one segment INFERS.
- **A stranger's class is asked of Census DURING the pull** (`live.
  _queue_roster_lookup`) — `snapshot_context` is read once at session start, so
  next to another guild's raid every bar was uncoloured until the close-time
  rebuild hours later. The unclassed player rows of the built snapshot go to a
  background worker; the HTTP never touches the ingest thread, a name is asked
  about once a session, and a failure backs off (`LIVE_ROSTER_RETRY_S`).
- **A meter row is a rate, never a total**, and the tail folds at 12 rows
  (`SortableTable`'s `fold` cuts after the sort). The stream overlay keeps its
  hard `max_rows` cap instead — nobody can click a stream. **Max hit is the
  one exception** (`max_hit`/`max_heal`, by SOURCE): a nuke and a DoT with the
  same DPS are not the same thing.
- **The live meter sizes itself and the middle column keeps the slack** —
  `--live-w` (`fit-content`, so the panel ends at the headline's `deaths`
  stat) and `--live-tbl-w` (360px, about where `raid HPS` ends) on
  `.dashmain .livemeter`. A pull in progress is a name, a bar and a rate; the
  column's width is for the PARSE a finished fight turns into, which still
  extends. The countdown rows shed words by CONTAINER QUERY (`@container
  aoepanel`), never a second `compact` flag.
- **The mini parse and the stream overlay are ONE component** (`MiniParse.jsx`);
  `MiniRail` is only the dock. Don't give the overlay a meter of its own — a
  change to the mini parse IS a change to the overlay.
- **The rail's switches are the RAIL's** (`MiniRail`'s ⚙, `eq2a.mini.cfg`) — the
  meter's chips drive the middle column only. A page read between pulls can
  spare three stacks of bars; 244px beside the game cannot, and the third stack
  is what pushes the countdowns off. Switches say `DPS`/`HPS`/`TANK` on both
  (`METRICS.short`); `rateLabel` still heads the FIGURE, so tank is `Inc/s`
  there. Every meter off is a setting, not an empty state. **The AoE panel is
  switchable on BOTH** — the rail's is in its ⚙, the middle column's is a chip
  in the meter's own row (`eq2a.mainaoes`, ruled off from the metric chips with
  `.tabsep`), and neither reaches the other. Not in the rail head: that says
  which SURFACE is on, the chip row says what is in the column.
- **One settings row, two panels** (`Settings.jsx`) — the mini rail's ⚙ and the
  stream overlay's options are the same list at two sizes; the rail's lit-pill
  grid is GONE (a pill you must press to learn what it does is what a 244px
  strip really cannot afford). A chips row is `as="div"`, never a `<label>` (a
  label adopts the first button, so clicking "Theme" pressed "Transparent"); a
  switch held off by a master switch is `disabled`, never hidden.
- **On a mini row the RATE sits against the name and max hit is outboard** —
  the reverse of the dashboard meter. At 244px the ranking is read straight
  down the rate; max hit is looked up once a pull, never scanned. Each cell
  keeps its own weight through the swap.
- **The notification block sits under the last stack of bars** (`MiniAlerts.
  jsx`), which is why `.miniparse` shrinks but never GROWS. Its countdowns are
  `AoeTimers` scaled in CSS — never a second countdown — and they are
  PERSISTENT; only the death cards pop in and expire (WALL clock, ≤2 at once, a
  wipe card supersedes a tank card). Nothing blinks: a solid danger block plus a
  180ms cross-fade, so reduced motion has nothing to take away.
- **The main tank is the FIGHTER who has taken the most damage this fight**,
  the off tank the second (`MiniAlerts.tankOrder`, over `CLASS_ROLE` — all six
  fighters, brawlers included). Nothing is configured and nothing is guessed:
  an unclassed raider is nobody's tank. **A single dps dying earns no card** —
  the deaths figure carries it; a tank dying, or more than five at once, is the
  fight changing shape. Death cards ride the master Notifications switch and
  have no switch of their own.
- **Deaths are counted by DIFFERENCE and the first payload of a pull is a
  BASELINE** — the payload carries a running total per actor and no death
  events, so without the baseline (retaken on `started_ts`) every pull would
  open by announcing the last one's dead.
- **The parse does not ANIMATE; only its clocks move** (`lib/smooth.js`).
  Figures and bars change when the payload changes them. Tweened numbers and
  sliding bars were both built and both REMOVED — a rate counting up cannot be
  read while it does it, and a pull's opening seconds became a slot machine.
  The cure for numbers that feel stale is a shorter ingest cadence, never an
  animation over the gap. What still moves is the two things that are functions
  of TIME: the AoE countdowns, and the elapsed clock counting in the browser
  (its correction is asymmetric — take a payload ahead of us, hold against
  latency behind us — so it never repeats or skips a second; the countdowns
  take the same correction through `useLogClock`, since a flat re-anchor on
  every payload is a sawtooth). **A clock stops when its fight does** — the
  elapsed clock and the countdowns alike: a frozen parse counting off seconds,
  or a bar draining toward a cast the ended pull will never get, is the one
  thing here that is actively wrong.
- **The AoE drain bar is the COMPOSITOR's, never the ticker's** — a CSS
  animation over one period, seeked with a negative `animation-delay` at mount
  and keyed on `next_due_ts`. The 20Hz ticker exists for digits; a length
  rewritten by JS at 20Hz judders in an OBS source compositing at 60fps.
- **The screen sees a hit in ~1s, and every term of that is written down**
  (`docs/live.md` → "How fast the screen sees a hit"). Plugin cadence 0.5s,
  `SNAPSHOT_MIN_S` 0.25s, and the SSE streams are WOKEN by `pipeline/livebus.py`
  rather than polled. Subscribe around the whole read, not around the sleep, or
  a push loses the update that lands mid-read; the timeout stays as the
  fallback that keeps `mark_watched` alive. The remaining floor is the plugin
  holding the newest log second, which is the dedupe contract, not a bug.
- **An update pill only ever appears for somebody whose OWN uploads say they
  are behind** (`device_tokens.client_version` v30, off the uploader's
  User-Agent, vs `refdata/plugin/VERSION`). Never heard from is not behind, and
  versions compare as numbers.
- **Live AoE detection imports `aoes.py`'s constants and its clustering**; the
  ≥5-raiders-in-a-second anchor is the evidence that a cast HAPPENED — for an
  ability the ACT timer list has never heard of.
- **For an ability that list KNOWS, reach stops deciding what a cast is**
  (`aoes.anchors`): one target anchors, a pet anchors (`PET_KINDS` — evidence,
  never coverage), one cast is a row. Mayong's `Soul Paralysis` landed 11 times
  on the TNT kill and reached 5 raiders 3 times, so the countdown re-armed from
  the wrong second and read overdue all fight. Added 13 rows over 60 named
  fights, removed none.
- **Don't bound how far a cluster runs from its start** (`aoes._cluster`) — it
  splits `Blanket of Eternal Night`'s 76s tail into casts that never happened
  (65→72, measured period 59.8s→40.3s). Merging is the failure to prefer: a
  merged cast makes a gap LONGER, which `observed_period` survives by design.
- **A suggested timer is OFFERED, never applied** (`suggest_period`): 3+
  agreeing intervals, past 15% and 3s, never when `instances_hint` explains it,
  and off CLEAN cycles only. It is an errand (edit your ACT config), not the
  countdown — which now uses the learned number instead, see below.
- **The countdown's number is LEARNED, not ACT's** (`pipeline/aoelearn.py`,
  `aoe_cycles` v33): `learned` > `reported` > this pull. Adoption needs 6
  agreeing CLEAN intervals across 2 fights, never when `instances_hint` explains
  it. Pooled SITE-WIDE — a mob's recast is a fact about the game, like
  `zone_eras.json`, and the rows carry no raider, parse or run. Observations are
  stored, the conclusion is derived.
- **A reuse debuff moves some AoE timers and not others, and which is MEASURED**
  (`refdata/reuse_debuffs.json`): `Traumatic Swipe` is a damage line from a
  player onto a mob, so it needs no cast line; recast 30s and duration 30s, so
  one rogue is 100% uptime. **A swipe is matched on WHAT IT LANDED ON, never on
  the source** — live, another raider's ability line is `Subject(name,
  'unknown')`, so a `unit == "player"` test matches the logger and nobody else. The tooltip says -50% reuse speed and it measures
  ~×1.3 on the abilities that respond, ×1.03 on the avatar's `Whirling
  Bladestorm` in the same fight as ×1.47 and ×1.35. Never assume the magnitude;
  never adjust an unmeasured ability.
- **A cycle belongs to the state at the cast that STARTED it**
  (`aoes.split_cycles`) — not to how much of the gap the debuff covered, which
  does not separate the populations (60s and 77s `Blanket` cycles had
  overlapping coverage fractions in one fight; at the cast they split cleanly).
- **A swiped bar is ONE SPAN with a tick at the normal timer** (`NormalMark`) —
  the stretched number from the first second, never a length that changes
  mid-drain. The first build planned normal and grew past it: the bar resized
  while being read and a cast that was never late opened at "+0:24". Overdue
  counts up from the number the bar was running to. No pill — a word here has to
  change what somebody does in the next few seconds, and that one restated the
  bar in text.
- **NOTHING ON THE AoE PANEL MOVES THAT DOES NOT HAVE TO** — it is read while
  fighting, and motion costs you your place. Rows are ordered by FIRST CAST, not
  soonest-due (a re-arm must never reshuffle the list); the compact panel keeps
  3, chosen by damage but drawn in first-cast order; no dtype pill in compact.
  Read by POSITION, not by rank.
- **The exception is the landing: the row flashes red and says `HIT!`**
  (`justHit`) — a reset countdown looks exactly like one that has been running,
  so the flash is the only thing distinguishing "it fired" from "you looked
  away". One pulse (a cross-fade, so reduced motion keeps it), NOT seeked (the
  screen is ~1s behind the log; seeking would show the decay, not the flash),
  and derived per render from `last_cast_ts` rather than remembered.
- **The two hand marks are JOUST and MINI, keyed by ability NAME**
  (`lib/marks.js` + `joust.js`/`minipin.js`) — a log cannot tell running out
  from standing in, nor which AoE is worth a slot beside the game.
  Stacked PILLS per row on the live panel and the AoE tab, never checkboxes (a
  tick says what it does only in its tooltip, and these rows are read mid-pull).
  A mark is an ANSWER — yes/no/nothing said — and **nothing said defaults to
  whether ACT's list knows the ability** (`actListed`, off `reported_s`): the
  raid's own callout list.
- **A mark is on the ACCOUNT, and localStorage is now the CACHE in front of it**
  (`user_marks` v35, `backend/marks.py`, `GET/PUT /api/marks`) — the same panels
  are drawn in three browsers, and the in-game window is a different browser
  read by the person who did the marking. Module state is seeded from
  localStorage at import so the first paint awaits nothing, and the account's
  answer corrects it; a click applies locally and pushes in the background, so
  signed out or offline still marks. **The two token screens are handed the
  marks with their config** — no cookie, so they cannot ask, and the poll they
  already run means a pill toggled on the dashboard reaches the game window on
  the next tick. **The first signed-in read MERGES per ability** (`syncMarks`):
  every pre-v35 mark lives in one machine's localStorage and nowhere the server
  can reach, so an ability the account has no answer for takes the browser's
  and one it has an answer for keeps the account's.
- **MINI decides eligibility, `MINI_TIMER_ROWS` still decides capacity** — a
  mark says what matters, and a fixed scene is fixed however strongly somebody
  feels about a sixth countdown.
- **A timer is per (MOB, ability)** — cycle rows, `learn`, `timer_for` and the
  live rows all key that way, so two mobs with different names casting one AoE
  keep two timers and two countdowns. Only ACT's list is per ability, because
  that is ACT's format.
- **A mob that SPLITS is a SPECIAL CASE and is written down**
  (`refdata/split_mobs.json`, `aoes.several_bodies`): one name on several
  bodies, so its gaps are a superposition and not a recast. Never learned,
  never suggested, and no live countdown at all. The Emerald Halls rumbler had
  21 agreeing intervals across 4 uploads saying 28.7s against ACT's 50 and the
  site had adopted it. Read at derive time — no reparse, no `PARSE_VERSION`.
- **HOW MANY BODIES A NAME HAS IS GAME KNOWLEDGE, never inferred from parse
  shape.** "Measures well under the ACT timer and isn't the fight's named" was
  built and REVERTED — it killed `Ancient Grovebeast`'s `Tremerous Stomp` and
  only one grovebeast is ever up; `is_named` is the ENCOUNTER's headline, so
  every add fails it, and a wrong ACT entry looks identical. Most mobs sharing a
  name never overlap their AoEs. Only `_instances_hint` survives, because a
  clean whole fraction of the timer is a SIGNATURE, not a direction — and it
  never takes a countdown away.
- **A cast that NAMED A SECOND and did not happen is admitted MISSED at
  `MISSED_S` (15s)** — a much shorter fuse than `OVERDUE_DROP_S`, and a
  different question. Past it the mob was stunned, or every single person
  blocked it so nothing printed to detect on, or the timer is wrong; none is a
  countdown. It also stops anchoring the BURN WINDOW (`nextJoust` skips it):
  the window belongs to the SOONEST jousted cast and a cast in the past is
  soonest by a mile, so Mayong's skipped `Soul Paralysis` held the window at
  `+0:47` through a stretch the raid could have burned in. Both numbers ride
  the payload (`aoe_missed_s`/`aoe_drop_s`) because the browser clock runs
  ahead of it.
- **A countdown overdue by `OVERDUE_DROP_S` (60s) leaves the panel** — that
  line is now for a row with NOTHING to be late for (no period) — and comes
  back on its own — snapshots are rebuilt, never accumulated. **A row with no
  timer leaves on the same line, measured from its last cast**: an avatar's
  irregular raid-wide abilities (`Stealth Assault`) have nothing to be late for,
  so nothing expired them and they held a slot all fight.
- **The compact panel is not the dashboard's** (`AoeTimers: miniTimers`) — the
  dock and the stream overlay draw the meter UNDER the timers in a fixed scene,
  so a countdown-less row is a raider off the bottom: capped at
  `MINI_TIMER_ROWS` = 3 (cut by DAMAGE, drawn in first-cast order), rows with no
  period dropped while the fight runs, no dtype pill, and no `measured`/`timer`/
  `seen` word — provenance is a thing you look up once (it stays on the title),
  never the thing you read mid-pull, and it costs the name and the digits width.
- **An audit's threshold is not a panel's** — five targets is a GROUP, and it
  drew 10 rows for 3 real abilities on a Mayong kill. The Spell timers panel
  additionally needs a reported timer OR `RAID_FRACTION` of the raid reached;
  the recorded AoE tab still lists everything.
- **A cast is a MOMENT; a damage shield is a CONDITION** (`aoes.SUSTAINED_RUN`)
  — a shield reaches the raid exactly the way a cast does, so reach cannot
  separate them and only DURATION can: 9–36 raid-wide seconds per burst against
  1 for every real AoE measured. Catch it explicitly; the clustering hides it
  by assembling a plausible timer out of melee windows, and `MIN_CASTS` only
  ever dropped the shields that never stop. A reported timer is exempt.
- **The notes column collapses SIDEWAYS, and Enter files a note** (Shift+Enter
  is the newline). Closed it is a tab down the page's edge and its grid track
  shrinks to it — a collapse that keeps 340px is no collapse. The `File under X`
  button sits under the textarea, and the screenshot drop is a strip — a paste
  needs no target.
- **An open drilldown on the dashboard docks the raider list under the RAIL**
  (`ParseView`'s `pickerSlot`) and takes the middle column, one parse or
  several — the raid page's `.workspace.withpanel` layout, portalled because
  the rail belongs to the page and the list to the parse.
- **A note is keyed by (user, zone, named), NEVER by encounter** — encounter
  ids all change on rebuild; `encounter_id` is provenance, not identity. The
  zone is the BASE name (`zones.base_name`): "Castle Mistmoore 2" is a second
  lockout, not a second castle. The dashboard column shows the whole zone
  (`scope=zone`); the composer still writes to one subject.
- **Which expansion a zone came from is REFERENCE DATA, never inferred**
  (`backend/zones.py` ← `refdata/zone_eras.json` ← the wiki's zone infobox,
  synced BY HAND with `tools/sync_zone_eras.py`). It is read at import and
  never fetched at runtime; a zone with no entry groups under "Other" rather
  than being dropped. An `introduced = LU22` zone resolves by that update's
  DATE to the expansion that was live.
- **WHICH INFOBOX a zone page wears is itself the fact** — `IZoneInformation`
  is an instance (its `instance` field says Raid/Group/Solo), `ZoneBox` is an
  outdoor zone. The FIELD is unmaintained (Antonica fills it in, six others
  leave it blank); the template is not. A blank `introduced` on a `ZoneBox` is
  an ANSWER — the template's own comment says "leave blank if this is an
  original EQ2 zone" — and reading it as a gap is why every original overland
  zone was missing from the file.
- **A run in a PUBLIC zone wears its named mob** (`_headline_named` +
  `lib/raids.js: runLabel`): in an instance the zone IS the event, in a public
  zone it is only where somebody stood. Needs `zones.is_public` (reference
  data, so an unknown zone is left alone) and exactly ONE distinct named.
- **Observed is CONTRIBUTION, not presence** (`_observed_runs`) — no damage,
  heals, wards OR cures from the logger anywhere in the run; all four, or the
  word is wrong about a healer, a defiler or a cure bot. Damage TAKEN is
  deliberately out: an AoE reaches whoever is standing there. Derived at read
  time, never stored, like the guild tag.
- **The Live pill needs the zone LINE, not just the newest encounter**
  (`_live_runs`) — an encounter is the last thing that FOUGHT, so a raid that
  ended and went to sell lit its last pull for as long as ACT stayed open.
  Leave the zone and the pill drops; the next fight lights the new run.
- **The notes outline links every named out to eq2lexicon**
  (`lib/raids.js: lexiconRaid` → `/raids/<zone>/<named>`, new tab). The
  lexicon holds the strategy, the note holds what happened to us — don't
  restate one on the other.
- **The overlay token is a capability in a URL** — it reaches the live meter
  and nothing else; revoked and never-existed answer the same. Its options live
  on the DASHBOARD (beside Mini) and `enabled:false` is not revoked.
- **A translucent fill is `rgba(var(--x-rgb), 0.NN)`, NEVER `color-mix()`** —
  the parse draws in four places and only the dashboard is a current browser;
  an OBS browser source and EQ2's in-game browser are embedded CEF builds years
  behind, `color-mix()` needs Chrome 111, and an unparseable VALUE takes its
  whole declaration with it. Every bar on both was invisible while the plain-
  coloured dot, `HIT!` and digits worked. `-rgb` triplets live beside their
  colours in `tokens.css` in BOTH themes — keep the pairs in step.
  `stats.rankColor` is the one exception (mixes toward `--text`, not
  transparent; dashboard-only; degrades to no tint). On the surfaces nobody can
  open devtools on, prefer the older construct.
- **A correct token is not a guess — `_resolve` looks it up BEFORE the rate
  limiter** and a hit no longer `clear()`s the bucket. The bucket is per
  ADDRESS and these pages re-ask every 5s forever, so one forgotten revoked
  link was 12 failures a minute and locked the same machine's VALID overlay out
  of the whole feature (429 on a good token → black window). Misses still cost
  and still get refused; clear-on-success belongs to the login route, not here.
  Guarded by `test_a_stale_link_cannot_lock_out_a_good_one`.
- **A failed request is not a dead link** (`pages/Overlay.jsx`) — only a 404
  latches "no longer active" (`_resolve` gives it for revoked and never-existed
  alike) and it STOPS the poll (nothing can un-revoke a token); a
  429/502/restart/dropped request keeps the screen and retries with backoff,
  and a good read clears it. Same rule for the stream: EventSource reconnects a
  dropped connection itself but gives up permanently on a non-2xx, so
  `readyState === CLOSED` schedules a reopen. And "not yet" is not "nothing" —
  no config yet says `connecting…`, because a blank rectangle on a black
  document is how a 429 spent an evening looking like a broken page. These
  pages cannot be refreshed — never let a hiccup become a permanent state on
  one.
- **Two kinds of that token, one page** (`overlay_tokens.kind` v34;
  `/overlay/<t>` for OBS, `/ingame/<t>` for EQ2's own browser window). Separate
  ROWS because revoking is per URL — a link in a VOD must be killable without
  taking the window beside somebody's hotbars with it — and `kind` is fixed at
  creation. The public half never branches on kind; only the config defaults
  and clamps do, and they point OPPOSITE ways: `text_scale` 1.25 on a stream
  (read after a downscale and an encode), 0.9 in-game (read at 1:1, where every
  pixel is a pixel of raid). In-game gets the NOTIFICATION block and the stream
  never does — a card is an instruction, and a stream audience can't follow
  one — plus a painted document (EQ2 composites nothing, so transparent is a
  white margin), no `width_px`/`layout`, and overflow that scrolls.
  Button order in the rail head is `Mini · Parse · In-game · Overlay`: where is
  the parse, nearest window outward.
- **Smaller type needs MORE contrast, not the same contrast smaller** — the
  in-game window is competing with ACT's mini parse (white on flat black, ~9px,
  legible while fighting), and the dock's palette scaled down is a grey smudge:
  `--text-muted` is ~3.5:1, fine at 12.5px and gone at 8px. So
  `.overlaypage.ingame.theme-dark` re-declares the TOKENS (flat opaque black,
  `#fff` text, ~10:1 muted, bright hairlines) rather than re-lighting elements
  one at a time like the OBS scope — an element list misses every panel added
  after it. Weight is free width; radii, gaps, shadows and the outer frame are
  not (the window is the frame).
- **`dense` is the mini parse's THIRD size** (`MiniParse`) — ~180px inside the
  game's UI. The class goes so the NAME fits (`S… Swash` identifies nobody, and
  the bar's hue still carries the archetype); MAX HIT goes (looked for after a
  pull, not scanned); HPS and deaths come off the headline, leaving the clock
  LEFT and raid DPS RIGHT over the column it totals (key-first, so the figure
  meets the column edge). Nothing scrolls sideways and nothing leaves a row —
  the name ellipsises, the clock never shrinks, the row clips the rest.
- **In-game rows are a GRID with content-independent tracks, never a flex run**
  — rank and figure fixed in `ch`, the NAME the only elastic track. A flex row
  sized by its contents both MOVES (a rate gaining a digit re-widths every row)
  and ESCAPES (overflow goes off the right edge and the window clips it:
  `137,412 DP`, no `JOUST`). The burn row's second track is `auto` because it
  carries a word as well as a clock. `.miniparse` is `overflow: visible` here
  too — an inner scrollbar appearing is a horizontal shift of every number.
- **The in-game PAGE grid packs to the top** (`align-content: start`) — the
  page is a grid, `min-height: 100vh` gives it a height the content does not
  fill, and a grid's default `align-content` is STRETCH, which grows every auto
  row equally (flex children don't). Each panel padded itself out with a third
  of the leftover window: the mob name floating in its band, a void over the
  column head. It presented as "looks stupid when there are no AoEs" and had
  nothing to do with them — FEWER panels is a BIGGER share each. An empty
  `.minitimers` frame collapses too (`:empty`, like `.minialerts`): `MiniParse`
  frames off the raw payload, `AoeTimers` drops rows off a clock that runs
  ahead of it.
- **Sharpness is whole pixels and a hinted face, NOT weight** — `calc(15px *
  0.7)` is 10.5px and every `em` under it lands on another fraction, which is
  what "small but mushy" was; `--ovl-px` is rounded in `Overlay.jsx` and the
  scope calcs off it in whole px (so the in-game size chips read in **px**, not
  percent). Tahoma/Verdana are hinted to ~8px where `system-ui` is not. At 9px
  a 700 stem smears rather than thickens — contrast carries the hierarchy, the
  way ACT does it. Don't re-add bold here. Watch for `rem` in the compact
  rules: it ignores the scale entirely.
- **Clearing the dashboard rail is about the SCREEN, never the raid**
  (`Live.jsx: cleared`, `EncounterTree`'s `onClear`) — one ACT process is a
  whole evening, so by raid time the rail already holds the afternoon. A
  cleared fight is still parsed, still on `/zones/:id`, still shared; it is
  per-SESSION in localStorage. That is why the rows get ✕ and no confirmation
  rather than the raid page's Hide and Delete.
- **A replay is the live meter fed from a file, and it is TWO gates**:
  `require_curator` gates the TOOL, `visible_encounters` gates the FIGHT. It
  writes NOTHING.
- **A replay also feeds that account's stream overlay** (`replaybus.py`,
  per-user slot, expires) — how the overlay gets worked on outside raid hours.
  It publishes the LIVE payload; the `replay` block never crosses.

Display and import — `docs/zoneruns.md`, `docs/compare-import.md`:

- **A mob earns a Damage-tab row on what it TOOK, not what it dealt** — plenty
  deal nothing (anything that dies before it swings), and requiring damage
  dealt made the NPCs switch look broken exactly where it is easiest to test.
  Raiders keep the old test: a raider with no damage there is noise.
- **Rank coloring is PLACEMENT within the row's role**, never distance from a
  median; no role or under `MIN_PEERS` = no color.
- **A parse table is FROZEN** (`SortableTable`'s `frozen`): the header row and
  the name column hold still, the pinned cells are OPAQUE (`--frozen-bg`), and
  a checkable table pins the checkbox with the name at a measured `--fzleft`.
- **A long ability name shortens only when the table cannot fit** — never
  because a column is narrow, never a badge or a control, full name on `title`;
  un-shortening is asymmetric on purpose (it oscillates otherwise).
- **Parses side by side scroll as ONE** (`syncScroll` groups) — a comparison is
  read across, so the same column stays under the same column.
- **An imported screenshot is a CLAIM, kept out of everything that
  aggregates** (no session/character/encounter/run; private, no group
  predicate). The row ladder is FITTED, the decimal mark is ARITHMETIC, the
  fight length comes from the TABLE, never the title bar; a cell that fails a
  check it was subject to is blanked, not published wrong.
- **`/characters` is off the nav and must not be linked** — an upload derives
  the character from the FILE NAME.
- **eq2lexicon CANNOT be framed** (`X-Frame-Options: DENY`, browser-enforced,
  every origin) — its top-bar link opens away, and reverse-proxying it through
  this backend to strip the header is REJECTED. wikQ2 frames, and its tab is
  HIDDEN rather than unmounted — unmounting or moving the iframe reloads it and
  loses their place.
- **The roster cooperation graph was REJECTED** (moved 0 of 49 real runs);
  don't rebuild it without a log where the presence rule demonstrably fails.
- **Loot is CHEST loot; a corpse drop is not loot** — same verbs, and only the
  `from the <X> Chest of <Mob>` clause tells them apart. A win with no source
  clause is not evidence of a chest either.
- **Loot is written BESIDE the parse, never into `events`** — resolving a
  looter's name would put somebody who walked past the chest into the fight's
  roster. That is also why it needs no `PARSE_VERSION` bump.
- **A chest belongs to the fight its MOB names, not to the clock** — three
  rungs (encounter name → mob in the fight → nearest prior), and rung 3 says
  `approx` rather than claiming it.
- **An item's rarity comes from Census, its picture from EQ2i** — the log's
  `looted the Fabled …` line only prints for people near you (15 of 43 in the
  fixture), so it proves who TOOK it and nothing else. The log's item id is
  the Census item id; item lookup is exact and is not the closed gear-proc
  problem.
- **The hover card is a REPLICA of EQ2i's item box, never a screenshot of it
  and never its HTML** — EQ2i builds that box from the same Census record we
  hold, so the card is our data in the wiki's clothes (`.ew-*`/`.xqc-*` colours
  copied from `MediaWiki:ExamineWindow.css`). It does NOT theme: an examine
  window is black in a light client too. Built at resolve time into
  `items.stats_json`, so widening it means `backfill_loot.py --refresh-census`
  (and `--refresh-wiki` for the proc, which only the wiki has).
- **Census's `all` is ABILITY MOD, not "all stats"** — the wiki's `abmod`
  proves it. And **`ERA_HIDDEN` drops stats TLE does not have yet** (Crit
  Bonus today, Fervor when one appears): Census describes the LIVE item.
- **The lotto block is exact, `/random` dice are not** — dice say nothing
  about which item they are for, so they attach by announcement or by
  proximity and the card says which. Never mix dice into a lotto block.
- **A doubled asterisk before a slash closes a JS block comment** — `**/random**`
  in a comment silently failed the SPA build. Check `npm run build` output,
  not just its exit path.

## What the app is

Details per area live in the `docs/` file named beside it.

- **Ingest** (`docs/live.md`): `/import` is the whole onboarding — plugin
  download, account API key, drag-drop uploader. Logs arrive as uploads or
  live batches (`/api/ingest/*`, Bearer device token — a frozen contract with
  `improvmasta/eq2advanced-act`). A live session is rebuilt from raw at close.
- **Navigation is zone runs, not files** (`docs/zoneruns.md`): a run is one
  contiguous visit to one zone by one character. `/` is the raid list,
  `/zones/:id` the raid page, `/sessions/:id` the per-file debug view. Raids
  are EDITABLE (hide/delete/merge/split), keyed by encounter FINGERPRINT so
  edits survive reparses. Hiding is not deleting (v26). `raidmatch.py`
  collapses the same night from several uploaders into one row with a Parse
  switch.
- **The raid page** (`docs/zoneruns.md`): Damage/Healing/Defense/AoEs/
  Timeline/Class/Loot tabs (Insights hidden — one commented line in
  ParseView.jsx's `TABS`), fight rail left, drilldown right. Loot is last and
  is the one tab that is not the parse: what the chests gave, who took it,
  with the item's icon and its EQ2i page (`backend/items.py`). The tabs and tables ARE
  `ParseView.jsx`, which the raid dashboard renders too; `ZoneRun.jsx` is the
  page around it (title, rail, sharing, edit mode). Pets/NPCs switches; per-TAB
  per-browser column memory; Deaths is two columns (tank report + every
  death). The Class tab is the `pipeline/classstats.py` registry — one
  `@register` function per class, `blurb` required on every metric.
- **The raid dashboard** `/live` (`docs/live.md`): fight rail, ACT-shaped
  live meter with AoE countdowns, ACT-style **mini overlays** docked to either
  window edge (`MiniRail.jsx`), notes + pasted screenshots by zone/named,
  OBS stream overlay and an EQ2 in-game browser window, each its own token URL
  — the same mini parse at three sizes, configured from the rail head beside
  Mini — and curator replay of any visible fight, which the overlay can watch
  too.
- **Compare** `/compare` (`docs/compare-import.md`): any parses side by side,
  signed-out too; the whole comparison lives in `?c=` so a link IS the
  comparison; the picker is a faceted band computed in the browser; the last
  column is the screenshot dropslot. Every dropdown is `Picker.jsx`, never
  `<select>`; open panels render into `document.body` (backdrop-filter
  stacking trap). Don't rebuild the old modal or page-wide kind tabs.
- **The sibling TLE sites** (`docs/zoneruns.md`): the top bar carries plaques
  out to wikQ2 and eq2lexicon, so this is the one door to the rest. wikQ2 opens
  as a tab inside the shell (`/wiki`, an iframe kept mounted, so it keeps its
  place) and follows this site's light/dark; eq2lexicon opens in a new tab
  because it refuses to be framed.
- **Accounts and sharing** (`docs/sharing.md`): username + password, no
  email; security-question recovery. Groups via invite/join code/link;
  auto-share by character or by Census guild tag. The Sharing page is two
  cards side by side; manage pages share the pagehead → cards → `.formcol`
  pattern inside the `.manage` type scope — retune in `base.css`, not per
  component.
- **The admin console** (`docs/zoneruns.md`): five tabs; an alert is
  something BROKEN (`receiving` is healthy); the accounts table is
  searched/sorted/paged on the SERVER, deliberately not `SortableTable`.
  Feedback (v25) is triaged open → planned → closed.
- **Coach and Census** (`docs/coach.md`, `docs/census-abilities.md`): intact
  behind `coach_api` and the hidden Insights tab.

## The ACT plugin

`backend/refdata/plugin/EQ2Advanced.dll` is committed (source repo is private,
Actions artifacts expire) and served by `routers/plugin_api.py` as a ZIP —
browsers block bare `.dll`, and the install steps say Unblock BEFORE
extracting. Refresh with `bash scripts/update-plugin.sh`. Source:
`/home/lindsay/eq2advanced-act` (`improvmasta/eq2advanced-act`), builds here
with `bash build.sh`.

## Open

- **Two dummy parses at different Ability Mod** — the abmod marginal is only
  real once Lindsay runs them and flags both (`POST /sessions/{id}/calibration`).
- **Ascent of the Awakened drilldown cross-check** — the 2026-08-02 ACT
  screenshots were never diffed column-for-column; that log isn't uploaded.
- **AA modeling** — a curated per-class `aa_effects` table, not a full tree
  ingest. Discuss with Lindsay before building it.
- **Gear proc coverage is CLOSED AS WONTFIX** (2026-08-05): ~212k items, no
  reverse index (`EquipmentEffect` is a template parameter), 13/60 hit rate on
  a full-text trial. The remedy is the curator looking one up by hand. Reopen
  only if Fandom enables CirrusSearch (`insource:`).
- **Buff attribution** — another player's buff proccing on you parses as
  yours; sourceless `is hit by` pools under "Unknown". Real utility DPS needs
  buff uptime windows in the parser.
- **Third-person cast lines are dropped except the curated ones**
  (`parser/buffs.py`). A raid-wide cast timeline needs the generic form plus
  a flavor → ability-line map, and the map is the work.
- **PotM coverage is proc-derived and cannot be anything else** — every
  number is a floor; don't widen `JOIN_GAP_S` to "find" more coverage.
- ACT residuals, in size order: ~3s earlier open on a THREAT pull, boss's own
  Damage column ~10% light in `statsroll`, ACT counts deaths on mob rows.

## Ship log

- 2026-08-13 (claude): Crowdsourced AoE timers, account-kept hand marks, and a reflect countdown for Treyloth
- 2026-08-10 (claude): Live meter: Census resolves strangers mid-pull, AoE rows with no timer expire (carries /act end, joust marks, overlay text scale)
- 2026-08-09 (claude): Plugin update copy ships with the build (refdata NOTES), not hardcoded in the page
- 2026-08-09 (claude): Publish ACT plugin 0.2.1 (never skips unsent log on a failed send)
- 2026-08-09 (claude): Loot tab: chest drops, EQ2i-style item cards and roll history (schema v32)
- 2026-08-08 (claude): Live dashboard build-out (mini parse/overlay dock, livebus SSE wakeups, smooth clocks, ParseView), zone eras as reference data, Features page, docs/ split out of ARCHITECTURE; fix pages shrink-wrapping instead of filling the shell
- 2026-08-07 (claude): Replay a recorded fight through the live meter (curator/admin), no writes
- 2026-08-07 (claude): Raid dashboard: the fight in progress (livemeter partials), raid notes by zone/named (v28), stream overlay (v29)
- 2026-08-06 (claude): Docs and repo cleanup: rewrite README, drop shipped plan files, remove dead ShareBar component + CSS, fix stale test count
- 2026-08-05 (claude): Pets and procs stop being inferred: ability_rulings + the Abilities console (curator role), EQ2 class tree, and the wiki as reference data (schema v23, PARSE_VERSION 20)
- 2026-08-05 (claude): Sharing page rebuild (Groups + Automatic sharing side by side, guild-tag auto-share UI, settings-list switches); restore base.css styles lost to a git checkout
- 2026-08-04 (claude): Phase 24: one raid, several uploaders — raidmatch clustering (schema v18 roster_json), your parse first, a Parse switch on the list and the raid page
- 2026-08-04 (claude): Import page rebuild: account-scoped pairing (schema v13), drag-drop uploader, no character prompt, no ACT export box
- 2026-08-04 (claude): Serve the ACT plugin from the site: download + install steps + auto-sharing on Import, header pill
- 2026-08-04 (claude): Revert phase 17: sharing belongs on the site, the ACT plugin only sends logs (schema v12 drops session_shares + can_share)
- 2026-08-04 (claude): Phase 17: sharing from the ACT plugin (schema v11) — session_shares, token can_share scope, share_groups on ingest batches; also carries phases 11-16, which were still uncommitted
- 2026-08-03 (claude): Phase 9+10: editable raid list, import hub, fight rail rebuild, engagement v3, read caches
- 2026-08-03 (claude): Fix Insights crash (coach.character is an object, render its name) + error boundaries at route and panel level
- 2026-08-03 (claude): Zone runs phase 6: encounter deep-links resolve to runs (via dup_of), docs (ARCHITECTURE/CLAUDE/codex zone-runs sections)
- 2026-08-03 (claude): Zone runs phase 5: checkbox multi-select + ComparePanel (per-metric grouped bars from agg + report data)
