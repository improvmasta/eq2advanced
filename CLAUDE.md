# eq2advanced - Claude Context

## Behavior

- Be concise and make focused changes.
- Prefer updating existing files over adding new abstractions.
- Keep secrets out of the repository.
- Use the local helper scripts below for restart and shipping.

## Read also

- `ARCHITECTURE.md` — how it works and WHY. This file is the index and the
  rules; that one holds the reasoning, and it is where a design decision goes.
- `AGENTS.md` — agent instructions and provisioning notes.
- `codex.md` — the same context for Codex; keep the two in sync.

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
.venv/bin/python -m pytest backend/tests/ -q   # 449 tests; golden = /home/lindsay/bobby.txt
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
addressed) and `raw/` (live-ingest chunks). Schema is at **v26**; migrations in
`db.py` are guarded by table SHAPE, not `user_version` (the dev reloader can
stamp the version mid-edit).

## The rules — don't relitigate these

Every one has a section in `ARCHITECTURE.md` carrying the evidence.

- **Never ship, never deploy.** The container on 10.1.1.5 is Lindsay's.
- **Sharing is set on the site, never by the uploader.** A device token sends
  log lines and nothing else. v11 built the opposite (`session_shares`, a
  `can_share` token scope); v12 removed it. Don't rebuild it.
- **`groups.py` owns the one visibility predicate** — own / shared with a group
  you're in / either STANDING share (a character's auto-share, a user's guild
  tag) minus a per-run `hide` / published, decided at READ time. A standing
  branch has FOUR query sites and all four are traps: `PERSONAL_RUN_IDS`
  (missing it is a leak), `set_run_shares`'s `auto` set (it deletes explicit
  shares, so a branch it doesn't know about survives the untick and revokes
  nothing), `shared_via_for_runs`, and `shares_for_runs` — that last one looks
  cosmetic and isn't: ShareDialog saves back what it returns, so an unreported
  group gets a spurious `hide` and the raid is silently unshared. Every branch
  that reaches a group must also carry `LIVE_GROUP` (deleting a group is a soft
  delete — all its rows are still there).
- **A guild share is matched on the UPLOADER's character's Census guild**, never
  on the run's majority-vote `guild` tag, and it is a per-user rule rather than
  a group-manager power (`PUT /groups/{id}/guild-shares` is member-gated).
  `guild_checked = 1` only — 0 abstains, exactly like the raid-tag vote.
- **Seeing is never changing** (`owned_zone_run`), and authorization is per
  ENCOUNTER (`visible_encounters`), never per session — a shared raid must not
  expose the other fights in the same uploaded file.
- **Hiding is a SECOND predicate, beside the sharing one, never folded into
  it.** Sharing asks who the owner sent a raid to; hiding asks whether they
  meant anyone to read it at all, and the answer is the same for every viewer.
  `VISIBLE_UNHIDDEN_RUN_IDS` wraps `VISIBLE_RUN_IDS` rather than adding a
  branch to it — that predicate stays the one auditable statement of the
  sharing rule. Per fight, the choke point is `visible_encounters`: ids are
  sequential, so "absent from the payload we sent" is not an access rule.
  A hidden fight still SEGMENTS (dropping it would split a night at a gap that
  only exists because it was hidden) and never COUNTS — `encounter_count` is
  the visible count, `hidden_count` is the rest.
- **Admin is operational, not omniscient.** `role='admin'` is absent from every
  visibility decision; support is "ask them to share the raid".
- **Private chat is stripped at INGEST, never at display** (`pipeline/redact.py`).
  An EQ2 log is the whole client log; the server stores the fight plus the group
  and raid talk around it, and the unredacted file never lands on disk at all —
  the upload path filters the byte stream as it arrives and the live path filters
  before writing each chunk. Dropped: tells, guild, officer, every named channel
  (LFG/General/Auction/…) and local `/say`. The classifier is an ALLOWLIST, so an
  unrecognised channel is dropped rather than kept — a chat type nobody thought of
  has to fail closed. It governs exactly the set `classify_body` already returns
  None for, and it imports `CHAT_PREFIXES`/`CHAT_RE` from `classify` rather than
  restating them, because a drifting copy is how redaction would start eating real
  events; that is also why redaction can never change a number. `trim_to_fights`
  is the second pass — retained group/raid chat outside any encounter window
  (±`FIGHT_MARGIN_S`) goes once the parse knows where the fights are, and it takes
  the UNION of windows across every session sharing those bytes so trimming for
  one uploader cannot cut chat out from under another. The content address stays
  the sha256 of the ORIGINAL bytes, or two raiders' copies of one night stop
  deduping. Logs stored before this existed are cleaned by
  `backend/tools/redact_existing.py` (one-time, `--dry-run` first).
- **A pet or proc label is a CLAIM, and only a human makes one.** The ladder is
  `ability_rulings` > the curated seed > no label (`census/catalog.py`, the two
  `*_ability_names` readers are the only doors). Inferring them cost 228
  pet-flagged names, 108 of which Census knows as scribed player spells (`Ice
  Comet`, `Harm Touch`, `Raging Blow`) — `observe_pet_abilities` took a label
  from one sighting, and `refine_bare_pets` read that table back to decide what
  a pet was, so the error fed itself. Necromancer looked right only because the
  curated seed covered it. Everything the machine notices is a CANDIDATE
  (`pet_seen`, `proc_candidate`), reviewed at `/admin/abilities`. `pet_definite`
  (a possessive with a lowercase remainder) is grammar; `pet_guess` (a bare
  capitalized name) is where every bad label came from — never treat them alike.
- **`You prepare <X>` does NOT print for an AA activation**, so the log's proof
  of a press is missing exactly where AAs live and a pressed AA is
  indistinguishable from a gear proc. `Lifeburn` (a 5-minute recast) read as
  gear, with 45 rows on the same silence. `gamewiki.activated` (a recast timer)
  is the only evidence that settles it, which is why `suggest()` checks it
  BEFORE the prepare-line test — the ordering is the fix. The wiki is also a
  proc SOURCE (`Avast Ye` -> `Pirate Stab`), using the same grammar as Census.
- **The wiki ingest is ERA-FILTERED and must stay that way** (`gamewiki.AA_TREES`,
  `DEFAULT_ERAS = ("eof",)`). This is a level-70 TLE server; the expansion trees
  don't overlap at all (1215 EoF pages vs 407 later, zero shared), and pulling
  Heroic/Shadows/Dragon would label raids with content that does not exist here.
  Adding RoK is one entry plus a re-sync. Run `tools/sync_wiki.py` BY HAND —
  never on a schedule. Census stays authoritative for spells; the wiki covers
  what Census was never asked for. Deities (`--what deity`, 139 blessings and
  miracles) are always EoF — they arrived with it.
- **A NAME is not a key, and a wiki match is the weakest join there is.**
  `wiki_abilities` is keyed on (name, KIND) because one name really is two
  abilities: the fury spell `Tempest` and Karana's miracle both print the same,
  and 37 AA names collide with a blessing. A single-column key let the deity
  sync silently overwrite them. The same AA on several classes (`Enhance: Cure`
  x3) MERGES its tiers rather than overwriting — 66 pages were collapsing to
  29 names. And a wiki row only speaks when Census does not contradict it: a
  scribed spell record is the game naming the class, so `scribed_by` wins and
  the wiki stays visible as evidence (`gamewiki.by_name` marks `ambiguous`,
  `suggest` refuses to be confident about it). Disambiguation pages are skipped
  outright — they are pointers, not abilities.
- **Census answers "spell, AA, gear or deity" — read it, don't guess.** Spell
  records carry `given_by`, `type`, `alternate_advancement` and `deity`, and a
  proc's source spell is findable by its effect text: `Fae Fires` is `Fae Fire`,
  a level 35 FURY spell, not "a gear proc". The gap is real and narrow —
  `census_items` has 143 rows and 2 spells carry the deity flag. AAs and
  deities are now covered by `gamewiki.py`, so "no cached spell casts it" means
  GEAR — and gear is a closed question, not a pending pull (see Open). Self vs
  granted is a per-ROW question against `grant_class`, never a property of the
  ability.
- **A grant is to a TIER of EQ2's class tree, not a class** (`classtree.py`).
  AAs are handed out at every level — Predator is rangers AND assassins, a
  Scout AA is all seven — so `expand()` is the one translation and a ruling
  against `predator` groups under both without being written twice. Census
  never needs it (it writes only subclass names, pre-expanded); this is for
  what a PERSON types, which is why an unrecognized target is rejected rather
  than dropped. Not the same thing as the ROLE map in `coach/descriptive.py` —
  don't merge them.
- **`role` is operational and now has three values** (`user|curator|admin`).
  `curator` opens the Abilities console and nothing else; none of the three
  reaches anybody's parse (`security.py`). A curator gets a ⚙ on every parsed
  ability row into `/admin/abilities?q=<name>` — the wrong label gets noticed
  on a raid page, not in the queue.
- **Bump `PARSE_VERSION` (`pipeline/ingest_writer.py`) after ANY parser or
  rollup semantics change** — the startup sweep reparses stale sessions.
  Zone-run dedupe only matches equal `parse_version`, so duplicate marking
  converges after that sweep, not during it.
- **A segment is only a FIGHT if the raid engaged it** (`_ENGAGE_KINDS`: a
  swarm pet is a proc, not a decision). Non-fights keep their name but are
  `is_named` 0 / `success` NULL; the exception is an ally death, which makes a
  no-damage segment a WIPE — `success` NULL renders exactly like a kill.
- **Do NOT re-add trailing-event trimming.** It regresses cures/EncHPS; ACT
  keeps idle-window heals and power inside the encounter.
- **Everything that vetoes a mob reclassing is a claim that the name is a
  PERSON** (`pipeline/refine.py`), so each one is a hole a mob can crawl
  through. Owning a swarm pet is NOT proof — an encounter holding the raid's
  dumbfires prints `Enynti's protoflame` for the boss, which put a mob in the
  MMIS raider table with 872k damage. `roster_prescan` is the authority; the
  softer signals no longer veto on their own.
- **Census by NAME is ground truth for the whole raid**, not just people with
  an account (`census/roster.py`, `roster_classes`). A raid page should not
  show a "?" — 94% of raiders resolve, and a name Census has never heard of is
  a pet or a mob, not an unknown player. Needs a real `CENSUS_SERVICE_ID`
  (`s:example` throttles after ~6 requests); backfill with
  `backend/tools/sync_roster.py --all`, guilds with `--guilds`.
- **A raid's guild is a MAJORITY VOTE of its roster, and it abstains twice as
  readily as it commits** (`census/guilds.py`, schema v20). No guild unless half
  the roster resolved AND a strict majority of the resolved share one, with the
  known-guildless counting against. `roster_classes.guild_checked` is why that
  works: 1 + a name is a guild, 1 + NULL is known guildless (it votes), 0 is
  never asked (it abstains) — collapse the last two and a backfill in progress
  strips real tags. The tag is derived, so it is RECOMPUTED, never maintained:
  `retag_runs` is pure SQL and every path that rewrites a roster calls it.
- **Order of class authority per fight** (`classguess.resolve_class`): what the
  fights on screen prove > the era the fight falls in > Census > the pooled
  vote. Census says what someone is NOW; only the log is dated, so Census must
  never relabel a raid from before a betrayal.
- **A bare capitalized name is a raider, a boss OR a dumbfire** and only
  behavior tells them apart (`pipeline/refine.py`). Nothing that a mob or a pet
  can also produce is proof of personhood — not a self-heal, not owning a swarm
  pet, and not `<Name> receives ...` (that one is a debuff as often as loot).
- **A class change is a DATE, not a tie** (`pipeline/classguess.py`). Betrayal
  deadlocks the pooled vote between two full spellbooks and blanks the class in
  every raid the name appears in. When the vote deadlocks, check whether the
  contenders' ability windows are disjoint before giving up.
- **Rank coloring is PLACEMENT within the row's role**, never distance from a
  median, and a row with no role (or a group under `MIN_PEERS`) gets no color.
  Falling back to the whole raid put four yardsticks in one column.
- **Ground truth is an ACT XML export** (`Import/Export` → XML), one per fight
  — not screenshots.
- **Census**: `crc=` silently returns nothing for comma OR-lists (`id=` accepts
  them), so `spells_by_crcs` is one request per crc. Tests never touch live
  Census — recorded fixtures in `tests/fixtures/census/`, and conftest sets
  `CENSUS_AUTO_REFRESH=0`.
- **`siteconfig.py` owns the three request facts the proxies falsify** (real
  client address, scheme, public base URL). Never go back to
  `request.client.host` / `request.base_url`.
- **The Cloudflare proxy stays off**: the edge caps a request body at 100 MB
  (a raid backfill is bigger, and that 413 never reaches the app) and it breaks
  HTTP-01 renewal.
- **`/characters` is off the nav and must not be linked** — an upload derives
  the character from the FILE NAME.
- **The roster cooperation graph was REJECTED** (moved 0 of 49 real runs; a
  passing group hits the same mobs you do). Don't rebuild it without a log
  where the presence rule demonstrably fails.
- GOTCHA `process_batch(token_row, char, …)`: `token_row` is an ACCOUNT token,
  not a character row — it used to be one.
- Read `ARCHITECTURE.md` before touching the parser or segmentation. The
  subject model (bare logger-name = their PET) and the possessive rules are
  verified against a real raid log and covered by tests.

## What the app is

**Ingest.** `/import` is the whole onboarding: the ACT plugin download, the
account API key, a drag-drop uploader, and the imported-log table. Logs arrive
as uploads or as live batches from the plugin (`/api/ingest/hello|batch|
backfill/done`, Bearer device token — a frozen contract shared with
`improvmasta/eq2advanced-act`). A live session is rebuilt from raw at close, so
it is provably identical to uploading the same file.

**Navigation is zone runs, not files.** A run is one contiguous visit to one
zone by one character, derived from encounter rows by `pipeline/zoneruns.py`
(content dedupe → segmentation → id-preserving upsert). `/` is the raid list,
`/zones/:id` the raid page, `/sessions/:id` survives as the per-file debug
view. Raids are EDITABLE — hide, delete, merge, split — and every edit is keyed
by encounter FINGERPRINT so it survives the reparse a backfill triggers.

**Hiding is not deleting** (schema v26). Delete says the pull never happened;
hide says it is not the raid's business. A hidden fight is still its OWNER'S —
listed in the rail, struck through, with the switch that puts it back — and is
absent from every payload anybody else gets, out of `encounter_count`, the
roster, `combat_s`, the sparkline and the raid report. Hide a whole raid and it
leaves everyone else's list entirely. It rides the same `run_edits` mechanism
(`kind='hide'`), so it survives a reparse; the visibility half is
`groups.VISIBLE_UNHIDDEN_RUN_IDS` for a whole run and
`security.visible_encounters` for one fight. Edit mode is `✎ Edit`, left of
Compare on the raid page, plus a pencil on your own rows in the list. Delete
confirms in place — click, then click Yes — never in a dialog.

**One raid can arrive from several people.** `raidmatch.py` says which runs are
the same night (zone + overlapping windows + shared roster) and the list draws
one row with a `Parse` switch. Yours wins; otherwise the site picks the parse
with the widest coverage, the same one for everybody.

**The raid page** opens on Damage, with Healing / Defense / AoEs / Timeline /
Class beside it, a fight rail on the left and a drilldown panel on the right.
**Insights is hidden for now** — one commented line in `TABS` (ZoneRun.jsx);
the panel and `coach_api` are untouched and putting the entry back turns it on. **Pets** and **NPCs** are two switches beside the role chips, on every parse
tab and off by default: a mob keeps its own credit, so the boss row is a real
parse (damage, DPS, self-heals) and clicking it opens that parse in the panel.
Columns are the reader's (drag to reorder, hide from the Columns menu,
**Reset to defaults** to undo it all) and are remembered per TAB, per browser —
not per run, so a layout set once holds on every raid you open next. Each parse
tab also offers the other one's rate folded away: HPS is default-hidden on
Damage and DPS on Healing. `defaultHidden` is a BASELINE the reader's own
choices sit on top of, never a first guess a single menu click wipes out.
Rank coloring is continuous distance from the peer median (`stats.js
rankScale`/`rankColor`) and says nothing under four peers. **Deaths** is two
columns: a **Tank deaths** report on the left (one tank death in detail — the
killing blow, took/healed, a row per SECOND of damage taken beside healing
received with NET as the verdict, then the raw log) and **Every death** on the
right (`DeathList.jsx`) — fights separated, the clock to the second, deaths
within 5s folded into one expandable moment captioned with what killed them,
and the recap opening inside its own row. Windows are **5s for the tank, 3s for
the raid list, one request** (a spike is over in two seconds); an EQ2 log stamps
WHOLE seconds, so nothing here prints a tenth. Class chips abbreviate in the
tight columns (`classShort`: SK, Necro, Troub, Illy…). No charts on the tab:
the recap's per-row bars are gone and its stat tiles are one fact line. Opening a raider
carries the page's tab into their parse (Damage → Damage, Healing → Heals) and
heads it with who they are — class, plus the level and guild Census already
cached for the class lookup, which are undated and so caption the name
rather than feeding any number. The rail's head puts the raid's guild pill
right of the character whose parse it is and ends its action row with Compare.

**The Class tab** holds the stats only one class can answer — a troubador's
buff uptime is not a column the other twenty-five can share. A rail of the
classes actually in the raid, a panel each, fed by the `pipeline/classstats.py`
registry: adding a class stat is one `@register`-decorated function declaring
its columns (metrics live in `pipeline/classmetrics/<class>.py`), and a class
with none written yet says "Coming soon" rather than being hidden. `blurb` is
required on every metric and carries the stat's LIMIT, because these live at
the edge of what a log proves. Troubador so far: **Jester's Cap uptime** and
casts (off the curated buff lines in `parser/buffs.py`) and **Perfection of
the Maestro** coverage (off its Precise Note proc — PotM logs nothing else),
which carries the raid-wide "double-covered" column for RoK. See
ARCHITECTURE.md → the Class tab.

**Compare** (`/compare`, in the nav, signed-out too) puts any parses side by
side — whole raids or single players from different nights, matched by name.
A column is the ACTUAL parse, like two ACT windows lined up: a player column
is their ability breakdown, a raid column is the zone page's parse list, and
the table is the shared `BreakdownTable.jsx` (drilldown, raid-page compare
panel and this page all render it — comparing looks the same everywhere).
Share/ToHit are hidden by default; the Columns menu brings them back. The
whole comparison lives in `?c=<runId>:<sel>:<subject>,...` (kind tab in `?k`)
so a link IS the comparison. Compare chips on the raid page and the player
drilldown seed the first
column; the add card is one faceted live search — a box over
Zone/Date/Guild/Player dropdowns, computed IN THE BROWSER from the visible list
it already fetched (`?roster=1`), so each dropdown only offers values that leave
results and no combination strands you on an empty list. The card renders before
the columns, so it holds the left edge as parses stack up beside it. Typing
`freeth` finds Freethinker Hideout nights and Freethinkers-guild nights alike.
A raid click adds; a player click selects the night and fills the dropdowns,
then a confirm strip picks who. `GET /api/players` stays but the picker no
longer calls it. It absorbed the old `RaidParseCompare` modal — don't rebuild
it. See ARCHITECTURE.md → The Compare page.

**Accounts** are username + password, no email anywhere; the only self-service
recovery is a security question. Groups carry sharing: an invite by username, a
6-digit join code, or a `/join/<code>` link — one credential to rotate. A
character's auto-share carries raids only unless told otherwise, and can
include or exclude the back catalogue (`since_ts`); connecting a guild TAG to a
group is the same rule keyed on the guild Census says the uploading character
wears, so a new alt is covered without a new switch.

**Coach and Census** are intact behind `coach_api` (and the Insights tab, while
it is hidden) —
descriptive currencies, a Census-as-prior fit with per-ability coefficients,
stat-marginal replay, calibration sessions, and the raid report (engagement
timing, death cost, overheal/save estimates).

**Manage pages** (Import / Sharing / Account / Admin) share one pattern:
pagehead → cards with a small-caps h2 and one line of `.note` → `table.data` or
ruled rows → `.formcol` forms, all inside the `.manage` type scope. A group is
never a pill there — it is a `.settingrow`. Retune in the `.manage` block in
`base.css`, not per component, and keep the type ladder intact: h1 > card h2 >
card h3 > the subject of a row > the column labels over it. Headings own the
heading font; a row's subject does not (Cinzel names set larger than the head
above them turned the page into a stack of headlines).

**The admin console** is five tabs (`?tab=`): Overview, Accounts, Content,
Feedback, Audit — each fetching its own data. Two rules it now keeps. *An
alert is something broken*: `receiving` is a plugin streaming RIGHT NOW, the
healthiest state a session has, so Overview lists only errored sessions and
parses stuck past `STUCK_PARSE_S`, each with the owner and an age, and counts
the live ones separately. The old "jobs needing attention" listed every
non-final session, which made a 24-raider night read as 24 failures. *The
accounts table is searched, sorted and paged on the SERVER* (`q/sort/dir/
limit/offset`, whitelisted sort columns, grouped joins rather than five
correlated subqueries per row) — so it deliberately does NOT use
`SortableTable`, which sorts in the browser and would sort one page while
claiming to sort the set. Row actions live in a panel you open by clicking the
row, not as four controls on every row. **Feedback** (schema v25) is a bug or
suggestion filed from the topnav button on any page, carrying the path the
reporter was on; admins triage it open → planned → closed.

**Sharing** is two cards SIDE BY SIDE (`.sharegrid`, stacked under 1180px):
*Groups* on the left — the create/join bar plus the master–detail (list,
members, invite, leave/delete under a rule), with the join code in a
field-shaped box rather than big gold type — and *Automatic sharing* on the
right, holding the two standing rules as one ruled table each: by character,
and by the guild tag Census says that character wears (`GET /api/guild-shares`,
`PUT /groups/{id}/guild-shares`, both member-gated). Both tables draw
`ShareRows` from `AutoShare.jsx`: a phone settings list, name left and switch
right, with the share's two choices as indented rows of the same shape.
Switches throughout — every row asks "is this on", which is not a checkbox's
question. Rule weight carries the structure: heavy under a section head, full
between subject blocks (six alts must read as six blocks), hairlines within
one, and a vertical rule down the subject column.

## The ACT plugin

`backend/refdata/plugin/EQ2Advanced.dll` is committed and served by
`routers/plugin_api.py` (`GET /api/plugin`, `/api/plugin/download`, both
unauthenticated). The download is a **ZIP** — Chrome and Edge block a bare
`.dll` — and the install steps say to Unblock it BEFORE extracting, because
Explorer copies the mark-of-the-web onto what it unpacks and ACT won't load a
marked plugin. It ships committed rather than linked because the source repo is
private and Actions artifacts expire. Refresh with `bash scripts/update-plugin.sh`.
Source: `/home/lindsay/eq2advanced-act` (`improvmasta/eq2advanced-act`), which
builds on this host with `bash build.sh`.

## Open

- **Two dummy parses at different Ability Mod.** The abmod marginal is only
  real once Lindsay runs them and flags both (`POST /sessions/{id}/calibration`).
- **Ascent of the Awakened drilldown cross-check** — the 2026-08-02 ACT
  screenshots were never diffed column-for-column; that log isn't uploaded.
- **AA modeling** — a curated per-class `aa_effects` table, not a full tree
  ingest. Discuss with Lindsay before building it.
- **Ability coverage: AAs and deities are done, GEAR IS CLOSED AS WONTFIX.**
  `gamewiki.py` holds 1215 EoF-era AAs and 139 blessings/miracles. Gear was
  investigated and **deliberately dropped** (2026-08-05) — do not reopen it
  without new information. There are ~212,000 items, so a crawl is out;
  `{{EquipmentEffect|<Ability>|}}` is a template PARAMETER rather than a link,
  so backlinks give no reverse index; and full-text search plus verification
  measured **13 of 60** on the largest unexplained abilities, two of those out
  of era (a Level 90 crate item), leaving ~15% once item `level` is filtered.
  ~1500 requests to answer maybe 55 of 381 is a far worse trade than the AA
  pull, which was one clean crawl and corrected 11 wrong verdicts. The remedy
  is the curator: an unresolved gear proc gets looked up by hand.
  Reopen only if Fandom enables CirrusSearch — `insource:` would turn that
  structured `effectlist` into a precise one-call reverse index.
- **Buff attribution** — damage from another player's buff proccing on you is
  entirely yours, and sourceless `is hit by <Effect>` lines pool under
  "Unknown". Real utility DPS needs buff uptime windows in the parser.
- **Third-person cast lines are still dropped, except the curated ones.**
  `parser/buffs.py` takes the handful whose flavor names ONE ability (Jester's
  Cap so far); `classify.py` otherwise handles only the logger's `You prepare
  <flavor>`, so 822 `Tasrin begins a phantasmal enchantment.` lines a raid go
  unread. A raid-wide cast timeline needs the generic form plus a
  flavor -> ability-line map, and the map is the work: the flavor identifies a
  whole spell line, and `You begin to breathe normally.` is not a cast at all.
- **PotM coverage is proc-derived and cannot be anything else.** Nothing in
  the log marks the cast, the landing or the fade, so `potm_coverage` reads
  Precise Note (its proc, and Census knows no other source). Every number is a
  floor — melee raiders with the buff up never proc — and the two constants
  are calibrated against the stored procs, not guessed. Don't widen
  `JOIN_GAP_S` to "find" more coverage; it manufactures double-cover instead.
- ACT residuals, in size order: ACT opens an encounter ~3s earlier on a THREAT
  pull, the boss's own Damage column reads ~10% light in `statsroll`, and ACT
  counts deaths on mob rows.

## Ship log

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
- 2026-08-03 (claude): Zone runs phase 4: zone-page tabs (Overview/Damage/Healing/Defense/Insights), right-side ActorPanel, shared stats.js, coach resurfaced
- 2026-08-03 (claude): Zone runs phase 3: Raids home (date-grouped runs), /zones/:id page v1, Uploads management page, shared UploadDrop
- 2026-08-03 (claude): Zone runs phase 2: zone-runs API, cross-session encounters/agg (name-keyed merge), run-scoped raid report
- 2026-08-03 (claude): Zone runs phase 1: zone_runs table (schema v6), content dedupe + segmentation linker, parse/live/startup hooks
- 2026-08-03 (claude): Phase 7b: Workspace UX (ACT-style tree + drilldown), stats v2 surfacing, pet knowledge refine pass
- 2026-08-02 (claude): Phase 6: coach correctness (flavor cast ground truth, two-point calibration, debuff uplift, ability catalog + join gates, healer/utility estimates, engagement v2) + hardening (events pruning, frozen raid reports, multi-file backfill, live hints)
