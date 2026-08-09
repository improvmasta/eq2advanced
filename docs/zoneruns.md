# eq2advanced — Zone runs, the raid page and its APIs

Part of the architecture reference. Index: `ARCHITECTURE.md`.

## Zone runs — the navigation model

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
- **Roster** (`raider_count`): see below — the count that decides what the
  Home page calls a raid.
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

Frontend: `/` = `Home.jsx` (one sortable table of raids), `/zones/:id` =
`ZoneRun.jsx` (fight rail + tabs Damage/Healing/Defense/AoEs/Timeline/Class,
Deaths beside them; `?sel`/`?actor`/`?tab`/`?cmp` all URL state).

**The parse is `ParseView.jsx` and the page is `ZoneRun.jsx`.** The tabs, the
tables, the filters and the drilldown column know nothing about runs: they take
a set of encounter ids, a raid report and a `rail` to put in the left column.
Everything that IS about the run — whose night it is, who it is shared with,
which fights count, the hand edits — stays on the page. That split is what lets
the raid dashboard show the SAME parse for a fight that has just ended
(`docs/live.md`), rather than a second cut-down shape of it. A railless parse
renders as one column (`.workspace.norail`).

**Insights is hidden for now** — one commented line in `TABS`
(`ParseView.jsx`); the panel and `coach_api` are untouched and putting the
entry back turns it on. It is the one tab the parse cannot render on its own
(the coach engine is per session), so the page passes it in as a function of
the parse's own actor rows. Right-hand
`ActorPanel`
(per-actor drilldown) or `ComparePanel` (checkbox multi-select, per-metric
grouped bars from `lib/stats.js`). `/import` = the import hub (plugin, API key,
uploader, imported logs), with `/uploads` redirecting to it; `/sessions/:id`
(Workspace) survives as the per-file debug view.

### The roster, and what counts as a raid

An encounter is a time slice, not a guest list: it holds every combat line the
log heard while you were fighting. `raider_count` used to be "distinct
`player` entities in the run's busiest fight", which counted all of them, and
the Home page's raids-only filter (>= 7 people, one more than a full group)
turned that into a wrong answer — a six-man Halls of Fate night read as a raid
off two strangers.

`_raider_count` in `pipeline/zoneruns.py` is now the run's ROSTER, three rules
deep, keyed by entity NAME (a run spans files, entities are session-scoped):

- **player** — mobs and the pooled `Unknown` source are not people. A
  single-token capitalized mob name is a player as far as `classify_entity_kind`
  can tell, and the ones `pipeline/refine.py` doesn't catch land in this table
  looking like raiders (`Ishi-Kurrat`, `Axxyk'Tuur`).
- **acted** — damage, heals, wards, cures, power, rezzes or swings. A row with
  nothing but `damage_taken` is a bystander who got clipped by an AE.
- **presence** — they turn up in >= `ROSTER_PRESENCE` (25%) of the run's
  fights, minimum 2; under `SHORT_RUN_FIGHTS` (4) there is no attendance to
  read and one is enough. This is the rule that does the work. Measured over
  Lindsay's 49 runs, a raid sits at 45-100% of its fights and passers-by at
  3-15%, with nothing in between: Castle Mistmoore is four regulars across
  12-18 of 23 fights plus another group that appears in exactly 2.

A cooperation graph (link players by support flowing between them or by damage
into a shared enemy, keep the logger's component) was built and **rejected**:
it changed 0 of 49 runs, because a passing group does hit the mobs you are
hitting. Don't rebuild it without a log where presence demonstrably fails.

Net effect on the real corpus: Emerald Halls 26 -> 24 (the raid was 24), Lord
Vyemm 32 -> 26, Halls of Fate 7 -> 6 (no longer a "raid"), Freethinker Hideout
25 -> 25 and Ascent of the Awakened 12 -> 12 (real raids, untouched).

### The fight rail

**The rail's head is the raid page's title block, and the only one.** The page
used to carry a `.pagehead` above the tables — zone name, date, time range,
character, sharing badges, the parse picker, Share and Compare — while the rail
printed the zone name again a few pixels to its left. Worse, that head was
hidden the moment a drilldown or a comparison opened (`!panelOpen`), so the
buttons on it came and went with the panel. The block that survives every view
is the one that gets to be the title: `EncounterTree` takes `sub` (date · time
range · character, one small line, ellipsised, with the long date on hover),
`actions` (badges, parse picker, Share, Compare) and `titled` (render the zone
name as the page's `h1` — `/sessions/:id` keeps its own head, so it passes
`titled` off and the page still has exactly one `h1`). `.wsmain > :first-child`
zeroes its top margin so the stat strip lines up with the rail beside it.

The selected FIGHT no longer gets a title of its own. The rail's active row
already says which one it is, and its footer says how many are counted.

Two things share that head and are not the same kind of thing. The guild rides
on the `sub` line, right of the character whose parse this is (`subTag`, which
keeps its width while the caption ellipsises) — it describes the night. The
`actions` row below carries the sharing badges and the parse picker, and ends
with Compare pushed to the far right (`.endact`), because it is the one control
there that DOES something. Compare also wears `.btn.solid` — a filled gold
button — everywhere it appears (rail, drilldown, the raid list's head-to-head
card): the default button is a gold outline with a gold wash, which is pixel for
pixel what every *on* toggle in the app wears (`.chip.on`, `.chip.toggle.big.on`,
`.listtools .chip.on`), so beside a row of badges it read as a switch somebody
had already flipped.

**The drilldown opens on the tab you were reading.** The page tabs and a parse's
kind tabs are the same question at two scales, so `PANEL_KIND` (`ZoneRun.jsx`)
translates Damage→Damage and Healing→Heals and hands it to `ActorPanel` /
`ComparePanel` as their starting tab; clicking through six raiders on Healing
used to cost a second click each. Only the page tabs with a per-ability view map
— from Defense, Deaths, AoEs, Timeline or Insights the panel keeps whatever it
is on — and the panel's own tabs still win until the page tab moves again.

`components/EncounterTree.jsx` is the zone page's navigation AND its scope
control, so the two gestures are kept distinct: **clicking** a fight (or the
root, a zone block, a `Trash ×N` group) makes it the only selection; **ticking**
its checkbox adds or removes it from the current one, which is how several pulls
merge into one set of combined stats. The boxes always show what is currently
counted, so the root row visibly means all sixty fights. Selection stays in
`?sel=` (an id list, or absent for all), so a merged set is a shareable URL;
selecting everything collapses back to `all` rather than a 60-id query string.

Rows are three fixed columns — checkbox, `9:35p` start, name — with the length
right-aligned as `m:ss` in tabular figures. `Trash ×N` groups keep their
twisty, and a group checkbox ticks the whole group (indeterminate when
partial).

**It is a tree with one root, not a list whose first item is special.** The root
is **Zonewide** on `/zones/:id` and `All fights` on `/sessions/:id` — a zone run
is one zone by construction, a session file can span several, and the label may
not claim more than it covers. Everything below hangs off it down an indented
spine, and the root is sticky so the total stays on screen to read every row
against. Two corollaries the layout depends on: the run's length is on the root
row, in the same column every fight's length is read down (the head carries the
count only), and the footer is always present once the rail is selectable —
appearing on the first tick shoved the list up under the cursor.

**One "on" for the whole panel.** Row checkboxes were the browser's default, the
`All` chip was rarity-blue and the wipes switch was gold — three ways to say
the same thing in a 40px strip. The checkbox is now drawn in the switch's own
track colors, and the strip separates its two KINDS of control: presets on the
left (what is selected), counting rules on the right of a hairline (what the
numbers mean). The `All` chip is gone; it duplicated the root row.

Repeated nameds carry an **attempt number** (`#3`) rendered outside the
ellipsis. Seven pulls at one boss otherwise render as seven rows of
`Vampire Lord Mayong Mist…` — the truncated part is the part that identifies
them, and the number is the only thing on the row that survives.

**Wipes are counted by default** — ACT counts them,
and a night with two Galiel wipes IS a night with two Galiel wipes. The rail's
switch (`?wipes=0`) takes them out of every total while leaving them listed and
dimmed, and the page head then says how many were left out. Selecting a wipe on
purpose always shows it — the filter can never empty the page.

**There is no Nameds switch** (removed 2026-08-06). The switches exist to take
things OUT of the count, and nobody reads a raid night with the bosses removed
— that one was there to be left on. `named` is still a KIND (`KIND_OF`, the row
colouring, the attempt numbers); it just has no switch, which leaves Trash and
Wipes, packed left rather than spread across the rail.

**The head is three blocks, and each answers one question.** It carried four
kinds of thing in one undifferentiated stack until 2026-08-06, and the failure
was legible:
`Skill Issue` (the guild Census voted the roster into) sat one line above
`Skill Issue (Temp)` (a sharing group) as two pills of nearly the same shape,
`＋ Share` was a verb in a row of nouns, and `Done`/`Compare` right-aligned at
the bottom read as a page footer — so the `THIS RAID …` bar that Edit opened
*underneath* them read as a new section rather than as the button's own effect.
You had to already know where to look.

| block | question | contents |
|-------|----------|----------|
| `.railhead` | what was this night? | zone (h1), date · time range, then one line of character + guild + Live + `shared` + parse picker with `N fights · N hidden` right-aligned on it |
| `.railsec.seen` | who can see it? | `SHARING` + filled `.sharepill`s (Public in blue, group names in gold) + a square gold `＋` |
| `.railsec.acts` | what can I do to it? | Compare (left), Edit (right) — no label: a row of buttons says what it is, where pills alone do not |

Two rules come out of that split. **A pill's fill says what kind of claim it
is**: outlined is a fact somebody else established (the guild tag, Live,
`shared`), filled gold is a decision the owner made (a share). That was already
the app's rule everywhere else — `.badge.guild` is deliberately quiet *because*
gold means a sharing group — and the head is where it was being broken. Inside
that row `Public` is filled BLUE rather than shouted in caps: it is a different
kind of reach (people you never named), and colour says so without making one
pill louder than the rest. And
**the count moved off the title's baseline**, where a long zone name wrapped to
two lines and then got crowded by it.

`Click to focus · tick to combine` is **gone**. It taught the two gestures once
and then sat there forever; the space is worth more as the raid's own controls.

**The share controls open where you clicked them.** `＋` is a square gold
button at the end of the pills — square is what makes it read as a control
rather than a pill that lost its label, so it is sized with `aspect-ratio` and
centred with flex rather than by two hand-picked pixel values (they were 26 ×
24, which is exactly the bug you cannot name but can see). It is a toggle — the
same button closes what it opened — and `ShareDialog` renders inside the
`SHARING` section, under the pills it edits, not as a card at the top of
`.wsmain` (a
different part of the screen from the one you were looking at, and often not on
it at all). It is capped at `42vh` with its own scroll, because the rail is a
fixed-height column and somebody in ten groups must not push the fight list off
the bottom of it.

**Edit mode is a state of the rail, not a screen you go to.** `✎ Edit` sits at
the RIGHT end of the `RAID` row with Compare holding the left, owner only.
Pressing it replaces the row with one right-packed cluster (`.editopts`, a
max-width reveal that degrades to a cross-fade) — `⊘ Hide`, `🗑 Delete`,
`✓ Done` — with Done in the spot Edit was, so the button that opened the
options is the button that closes them. The options grow leftward into the gap
because there is nothing to the right of Done in a 300px rail.

**Compare is not in the edit row, and `.editopts` is `nowrap`.** Both are the
same lesson. Four labelled buttons do not fit across this rail; the first
attempt kept Compare and let the row wrap, and what fell off the end was Done,
onto a second line at the far left — which is exactly the button-hunting this
layout exists to stop. So the cluster carries only what editing needs, the
labels lose the redundant "raid" (the section is called `RAID`), and if it ever
has to break it breaks as a UNIT with Done still attached. Compare comes back
with Done.

Edit also tints the whole column (`.rail.editing`, plus an amber rule drawn
*over* the children in a `::before` — the tint is deliberately faint, so an
inset shadow would sit under every panel background) and puts the same two
verbs on every row below. Nothing else moves: the fights, their checkboxes and
the drilldown all keep working, because deciding a pull does not belong is
something you do WHILE reading the parse that told you so.

**The row buttons are drawn at full strength**, not as ghosts that appear on
hover. Edit mode is a state you deliberately entered, and its whole point is
the controls it added; a mode that can delete a night must not make you hunt
for them. (The raid LIST is the other way round — see below — because there
the pencil is on sixty rows of a page people mostly read.)

**Delete confirms in place.** Clicking `🗑` turns that row's controls into
`Yes` / `✕`, and only that row's — one `confirm` key in `EncounterTree`, one
`rowConfirm` id on the raid list, so a second row can never be armed at the
same time. A dialog would have covered the fight it was asking about; the
second click keeps it under the cursor. Hiding needs no confirmation because
hiding is the undo.

**A hidden fight stays in the rail, struck through, with the switch that puts
it back** — this is the only place its owner can reach it, since it is out of
every payload anyone else gets. It is out of everything the rail COUNTS,
though: the head's fight count (which says `+N hidden` beside it), the kind
switches, the group checkboxes, the footer's total, and the selection ZoneRun
hands to `/encounters/agg`. `ZoneRun` keeps the raw payload in `allEncounters`
and derives `encounters` (the parse) from it; the rail is the one component
that sees both.

**Pets and NPCs are two switches in the filter bar**, immediately right of the
role chips, on every parse tab (Damage / Healing / Defense / Deaths) and off by
default. Who may have a row and what a row must CARRY to earn one are separate
questions: `kindAllowed` answers the first, the per-tab predicate the second
(`ZoneRun.jsx`, `rowsFor`). Both off, every tab is the raid.

They lived on Defense alone until 2026-08-06, on the reasoning that a
non-raider row carries nothing but DmgTaken. That is true of an OWNED pet and
false of a mob. A pet's damage is credited to its owner (`statsroll.actor_key`,
ACT does the same), so `Tragedy's unswerving hammer` really is a paladin hammer
pet with a DmgTaken figure and nothing else — but a mob keeps its own credit
(`_OWN_ROW_KINDS`), so the boss row has real damage, real DPS, its self-heals
and everything the raid put into it. Hiding it from the Damage tab hid a parse
people ask for, and clicking the row opens that parse in the panel like any
raider's (mob rows have no checkbox, so it is a plain drilldown — see the
compare-checkbox section).

**And a mob earns its Damage-tab row on what it TOOK, not on what it dealt.**
The predicate was `damage > 0`, which is the right test for a raider and the
wrong one for a mob: how much the raid put INTO a mob is the reading its row
exists for, and plenty of mobs deal nothing at all — anything that dies before
it swings, anything killed by somebody far enough above it that it never
lands. That is also the easiest case to test the switch on, which is how it
came to look broken: a guard in Freeport, 0 dealt and 27k taken, ticked NPCs
and got no row. Raiders keep the old test, because a raider with no damage on
the damage tab is noise.

What a mob does NOT get is a share of a raid denominator (`Dmg %` is a share of
RAID damage) or a rank color (`rankPool` returns null for anything that is not
a player, so a boss can never sit in a tank's peer group). Those cells stay
blank on its row rather than answering a question nobody asked. Deaths are the
one tab where the NPC switch adds nothing and that is honest, not a bug: a
death is credited to a player or their pet only (`rollup` is NULL for a mob),
which is the ACT residual already recorded at the end of CLAUDE.md.

### The Deaths tab is two columns (`TankDeaths.jsx` + `DeathList.jsx`)

Two different questions were sharing one full-width table. *How did the tank
die* is answered by ONE death in detail; *who died tonight* is answered by all
of them in a list. The list was eating the page's whole width to answer the
second question badly and the first one not at all, so it now sits on the
right of a grid with a tank report on its left (`.deathcols.two`, one column
under 1150px or with a drilldown open).

**The page decides the layout, not the grid.** `.two` is added only when
`hasTankDeath` says there is a tank death to put in the left column — a first
grid child that renders `null` would drop the death list into the narrow
column and leave the wide one empty, so the question has to be answered before
the grid is drawn, by the same predicate the component itself filters on.

**The tank report is the two curves, at three resolutions.** A tank's death is
a failure of what was coming in against what was going out to meet it, and the
thing every raid argues about afterwards is whether the heals were there. So
each tank death gets the same question answered three ways: a **fact line**
(took, healed), a **ledger** of one row per second, and a **log** of every
event. Tanks are `roleOf(actor) === 'tank'`, which is all six FIGHTERS
including brawlers.

Both tables lead with the WALL CLOCK (`fmt.clockS`, the same readout without
the AM/PM the card already established), not with a `−5s` countdown: the rows
are consecutive seconds of a real night and the log everyone cross-checks
against is stamped the same way.

**`Net` is the ledger's whole point** — healed minus took, per second, red when
they lost ground. Took and Healed side by side are two columns the reader has
to subtract in their head every row; Net is the answer to the only question
being asked. Every second of the window renders whether or not anything landed
in it, because a ledger that skipped its empty seconds read as a list of events
rather than a countdown, and a two-second hole where nobody healed him — the
thing that killed him — rendered as no row at all.

**FIVE seconds for the tank, THREE for the raid list, ONE request.** A tank
dies to a spike and the spike is over in two or three seconds; twelve seconds
of context buried the moment under the rest of the pull. The fetch asks for the
wider of the two (`TANK_WINDOW_S`) and `DeathList.clip` narrows to
`RAID_WINDOW_S` in the browser, which is EXACT rather than approximate:
`_window_slice` caps each list at `DEATH_MAX_ENTRIES` and keeps the TAIL, so
the last 3s of a 5s window is complete even when the 5s list was truncated —
which is why `clip` only carries the truncation flag over when nothing was
actually cut. Two requests would have fetched the same events twice.

**There are no tenths of a second in an EQ2 log.** Every line is stamped
`(1785630623)[Sat Aug  1 20:30:23 2026]` — whole epoch seconds — so `events.ts`
is an INTEGER and the per-second buckets are the log's own resolution rather
than a resampling of something finer. Within one second, line order survives as
`events.seq`, which is why each `/deaths` list is ordered `(ts, seq)` and the
blow by blow can only claim ordering within a SIDE: the payload carries no
`seq`, so a hit and a heal in the same second cannot be interleaved against
each other. Nothing on this tab may print a tenth it cannot measure — the
recap's old `0.0s` column was formatting precision it never had.

**A class chip may abbreviate where the width is not there.** `classShort`
(`lib/classes.js`) is the name a raider says out loud — SK, Necro, Wiz, Troub,
Illy, Conj, Brig, Swash — and `ActorName`/`ClassChip` take a `short` prop that
the tank picker and the death list pass. `Shadowknight` alone is wider than the
name it captions in a 380px column. Only classes with real in-game shorthand
are in the map; anything else keeps its full name rather than being truncated
into something nobody says, and the chip's tooltip (`classTitle`) always spells
it out in full either way.

**No charts anywhere in this tab.** The recap's per-row bars are gone — they
were a chart of a column of numbers printed directly beside them, and in a
column that now shares the page they cost more width than the amounts they
illustrated. Direction survives as the sign and the row color, which is all
the bar encoded. The recap's four stat tiles became one `.factline` for the
same reason: same numbers, a quarter of the height.

### Every death, by fight and by MOMENT (`components/DeathList.jsx`)

The Deaths tab's lower half was a flat table of every death in the selection,
and it was wrong in three ways that all have the same cause — it rendered the
API's list rather than the thing the list describes.

**Fights are separated.** A night's deaths run together otherwise, and a raid
that wiped twice on one named and lost a healer on trash reads as one
undifferentiated column of names. Each fight gets a `grouphead` band carrying
its name, when its first death landed and how many it had.

**The clock runs to the second** (`fmt.timeS`). An EQ2 log stamps to the
second and a wipe happens well inside one minute, so `9:41p` is exactly the
resolution at which four deaths to one AoE stop looking like four unrelated
events.

**Deaths within `CLUSTER_S` (5s) of each other are one MOMENT.** A wipe used to
spend twenty-four rows saying one thing. The moment row says `6 players`, opens
on a twisty into the individual deaths, and is captioned with what killed them
— `commonBlow` reads the LAST entry of each `incoming` list (ordered by
`(ts, seq)`, and a truncated list keeps its tail, so the killing blow always
survives the cap) and only speaks when the log agrees: one source and one
ability gives `Cataclysmic Slam — Overking Ohrmzz`, one source and several
abilities gives the mob and a count, and neither gives `N sources`. A moment
where some deaths carry no incoming events at all is marked `*`, because the
caption is then a claim about the others. Five seconds is half a cast bar: two
deaths that far apart are one AoE landing, ten seconds apart are two separate
problems and folding those together would hide the second.

**`Took` and `Healed` name their window in the column head** (`.colsub`, "last
12s"). They were `Damage taken` / `Healing` — true numbers over an interval the
header never mentioned, which is not a number anyone can read.

**The recap opens inside the row it belongs to.** It used to render as its own
card at the foot of the page, which on a bad night put it under a thousand
lines of list; `DeathRecap` grew an `inline` dress (no card, no ✕, a gold spine
down the left) that the expanded row hosts in a full-width cell. The standalone
card is unchanged for anything that opens a recap on its own.

What is expanded is indexed into the CURRENT list of deaths, so `DeathList`
carries `key={deaths:${sel}}` — a new fight selection starts it closed instead
of leaving a recap open on whatever death has since moved into that index.

### Read caches

Clicking a zone re-earned the same expensive answer every time: the run report
replays every stored event (~1.5s on the 60-fight Emerald Halls night) and
`/encounters/agg` recomputes medians from events.

- **Server** — `backend/memo.py`, a 12-entry in-process map keyed by
  `(epoch, key)` and used by `/zone-runs/{id}/report` and `/encounters/agg`.
  Authorization happens BEFORE the memo on every request; only the computed
  payload is shared, and callers copy it before adding fields. Every write
  bumps the epoch and clears the map: `rebuild_zone_runs` is the funnel
  (uploads, live closes, reparses, deletes, hand edits) plus `prune_once`,
  which deletes events without touching run membership. A build that races a
  write is discarded instead of stored. `test_memo.py` pins the invalidation.
- **Client** — `lib/api.js` keeps a read-through map of GET payloads for the
  session and every mutation clears it (`clearCache`). `peek(url)` returns a
  cached payload synchronously, which is what lets `ZoneRun` repaint on a
  click instead of blanking; an uncached selection keeps the previous numbers
  on screen dimmed (`.wsmain.stale`) rather than replacing the page with
  "Loading…".

### Hand edits to the raid list (schema v8, hiding v26)

Segmentation is a guess, so the list is editable: delete a raid or a fight,
merge runs the game logged as two visits, unmerge them again. The hard part is
that a reparse DROPS AND RECREATES every encounter row — an edit keyed by
encounter id would silently evaporate on the next backfill. So `run_edits`
keys by **fingerprint** — `<started_ts>|<zone>|<name>`, the dedupe key minus
`ended_ts`, which every duplicate copy of a fight shares — with four kinds:

| kind | meaning | written by |
|------|---------|------------|
| `delete` | this fight is gone, for its owner too | `POST /api/encounters/delete`, `DELETE /api/zone-runs/{id}` |
| `hide` | the owner's alone: nobody else's payload, nobody's totals | `POST /api/encounters/hide`, `POST /api/zone-runs/{id}/hide` |
| `join` | never start a run here (merge) | `POST /api/zone-runs/merge` |
| `break` | always start a run here (unmerge/split) | `POST /api/zone-runs/{id}/split` |

A reader sweeping a shared raid off their list is NOT one of these: `run_edits`
is keyed by the owner's character, and rebuilding somebody else's runs from a
reader's decision is exactly the copy of the visibility rule this file spends
its length avoiding. It is a row keyed by run id in `run_dismissals`, carried
across rebuilds by `groups.carry_shares` — see `docs/sharing.md`.

`rebuild_zone_runs` is the only writer of run membership, so every edit is
applied by re-running it: deletes re-stamp `encounters.deleted_ts` (a derived
mark — `run_edits` is the truth) and drop out before dedupe, and `_segment`
consults breaks/joins at each boundary. `POST /api/encounters/restore` removes
delete rows (the Undo on Home), `POST /api/zone-runs/{id}/unmerge` removes the
joins inside one run, and the run list carries `merged` so the UI only offers
Unmerge where there is something to undo.

**Hide is not a soft delete.** Delete says the pull never happened; hide says it
is not the raid's business — a wipe on the way out, a guild-bank pull, the hour
after the raid broke up. Sharing asks who the owner sent a raid to; hiding asks
whether they meant anyone to read it at all, and the answer is the same for
every viewer. Three consequences, and each one is a place the code
has to say it:

- **It still segments.** A hidden fight stays in the encounter stream
  `_segment` reads. Dropping it would split a night in two at a forty-minute
  gap that exists only because somebody hid the pull spanning it.
- **It stops counting**, for the owner as much as for anyone else.
  `encounter_count` is the VISIBLE fight count and `hidden_count` carries the
  rest; `named_count`, `success_count` and `combat_s` are taken over the shown
  fights, as are the roster and therefore the majority-vote guild tag. The run's
  WINDOW too — a night with its last two pulls hidden must not still claim to
  have run until midnight. `_spark` filters `hidden_ts` for the same reason its
  denominator did, and `/zone-runs/{id}/report` excludes them outright.
  **The exception is a run with nothing shown at all**, which keeps the whole
  night's window and roster (`described = counted or members`). Those two fields
  say what KIND of night it was, and `raider_count` is what partitions Raids
  from Solo/Group in the list (`lib/raids.js`) — blanking it moved a hidden
  24-man raid across a filter that is on by default, so the raid vanished off
  its OWNER's list and the switch that un-hides it became unreachable. Hiding a
  raid must never make it hard to un-hide.
- **It is a visibility rule, and it lives beside the sharing one rather than
  inside it.** `groups.VISIBLE_UNHIDDEN_RUN_IDS` wraps `VISIBLE_RUN_IDS`;
  keeping the two separate is what leaves that predicate the single auditable
  statement of the sharing rule (see its four traps in `docs/sharing.md`). A run whose
  `encounter_count` is 0 with `hidden_count > 0` is a raid hidden whole, and it
  leaves every list, detail and report a non-owner can reach. Per FIGHT, the
  choke point is `security.visible_encounters`: a hidden encounter is refused
  to everyone but the owner, because "not in the payload we sent" is not an
  access rule — the ids are sequential and a viewer can guess a neighbour's.

Hiding is reversible from the same control that set it (`{"hidden": false}`),
which is the whole reason it exists as a separate kind: un-hiding must never be
able to resurrect something deleted.

`DELETE /api/sessions/{id}` is the only thing that destroys data: derived rows,
ingest bookkeeping, frozen reports, and the raw bytes (content-addressed, so
the file goes only with the last session pointing at it). It also drops the
`run_edits` whose fingerprints no longer match any surviving encounter —
otherwise re-uploading the same log would come back with every deleted fight
still hidden and nothing on screen to explain why. Home surfaces this as: all
fights deleted -> "this log has nothing left in it, delete it too?"

## The raid list as a list

- **`raid_dps`**: player damage over the run's `combat_s`, from the same
  grouped query that builds the sparkline (`_spark` returns both). It replaced
  "Peak DPS", which ranked nights by their single best pull. **Named is gone
  from the UI** — `named_count` is still written, but is not trusted enough to
  print.
- **`shared_via`** (`groups.shared_via_for_runs`): the VIEWER's mirror of
  `shares_for_runs` — which of *your* groups reach somebody else's raid, by the
  same three-way rule. Both carry `group_id`, because the list filters by group
  and a name is not a handle. A viewer still learns nothing about who ELSE can
  see a raid: `shared_with` stays owner-only.
- **Grouping follows the sort**: `SortableTable groupBy` takes an ARRAY of defs
  and draws whichever matches the active sort column — nights under a date
  sort, zones under a zone sort, nothing otherwise.
- **Selection lives on the sticky toolbar line** (`.listtools`), not in a card
  above the table and not in a bar pinned to the bottom. A selection with
  nothing you own says "shared with you — read only" rather than showing an
  empty row of buttons.
- **Somebody else's raid opens the same pencil onto ONE button**: off my list,
  or back onto it (`api.dismissZoneRun`, `docs/sharing.md` → `run_dismissals`).
  Same column, same gesture, and the wording is "off your list", never
  "hidden" — hiding is the owner's and it reaches everybody. The sweep is the
  only narrowing on this page that OUTLIVES the page, so it is the only one
  that has to announce itself: an `N off your list` chip beside the source
  filter lists them again (a refetch, `?dismissed=1`, because the filter is the
  server's), each wearing an `off your list` badge. A raid that just stopped
  appearing, with nothing on screen about it, is indistinguishable from a share
  that was revoked.
- **One raid is edited from its own row**, without checking anything first: a
  pencil in the last column, on your own rows opening Hide and Delete
  SIDEWAYS beside it (`.rowedits`, a max-width reveal that degrades to a
  cross-fade under `prefers-reduced-motion`). Sideways because a menu dropping
  out of a table cell covers the raids underneath, and the row you are editing
  is the line you are pointing at. Delete arms in place, exactly as in the
  rail. The bulk versions on `.listtools` are unchanged — this is the same two
  verbs at the scale people actually use them. A raid hidden whole wears a
  `hidden` badge; nobody else's list can carry one, because for everybody else
  the row is not there. The PENCIL is quiet until its row is hovered and what
  it opens is not (`.rowedit > .ebtn` vs `.rowedits .ebtn` — a child selector,
  deliberately): sixty lit pencils would be noise, but the controls of a row
  already in edit mode are the point.
- **Two comparisons**: `RaidCompare` (list-row numbers, opens beside the table
  in `.raidcmp`; the list drops Timeline/Combat/Raiders to make room) answers
  "which night was bigger" from what the list already knows; its "Compare
  parses" button hands the checked raids to `/compare` (`docs/compare-import.md`), which is the
  deep answer. A `RaidParseCompare` MODAL used to be that answer — raid columns
  only, unshareable — and was folded into the page; don't rebuild it.
- **The filters are two questions.** SIZE — `Raids` and `Solo/Group` as
  independent toggles that PARTITION the list, so a third "All" button was a
  synonym for both on. SOURCE — `components/SourceFilter.jsx`, one menu of
  ticks in three sections (your characters, groups, published), OR'd, empty
  meaning everything. Keys are `char:<id>` / `group:<id>` / `public`; the page
  always fetches `scope=all` and narrows in the browser, so flipping a tick
  never refetches.

  Three earlier attempts at SOURCE were worse and are worth not repeating.
  (1) All / Mine / Shared-with-me chips — Mine was a filter that never
  filtered, since your own raids are always listed. (2) A Shared-with-me switch
  beside a group filter: two controls on ONE axis, and the switch silently
  changed what a group pill MEANT, because a group says "I sent it here" on
  your own raid and "it reached me through here" on somebody else's. (3) The
  same sources as always-visible pills — honest, but a toolbar of proper nouns
  competing with the mode chips beside them.


## The same raid, uploaded by several people (schema v18)

Everyone in a raid runs their own ACT. Share a night with a guild group and it
arrives four times over, and the list called that four raids. Within ONE
character's uploads the copies already collapse by content (`_dedupe`, above),
but two people's logs are not the same bytes — different subjects, different
vantage points, different fights heard — so both parses are real and neither is
a copy. Nothing here merges or deletes anything. It answers two questions:
which rows are the same night, and which one to open first.

`backend/raidmatch.py` decides the first, at READ time over the runs a viewer
can already see. Materialising it would be a fact about somebody else's
account, and it would go stale the moment a share was revoked.

- **zone** — equal, NULL-safe. An "Unknown zone" run (the log began mid-zone)
  can still match, but only on the roster; the place is what it cannot state.
- **time** — the windows overlap, ± `CLOCK_SKEW_S` (120s). The epoch prefix is
  authoritative and comes off the raider's own machine, so two clocks in one
  raid agree to within seconds; the slack is not a fuzzy-match knob.
- **roster** — enough of the same people. This is the rule that says NO: two
  guilds in the same instance zone at the same hour pass the first two and
  share nobody. `ROSTER_AGREEMENT` (0.34 of the smaller roster, minimum 2
  names) sits well under a real pair (they run ~1.0) and well over the overlap
  a passing group leaves behind.

That needed the roster itself, not its size, so **schema v18 adds
`zone_runs.roster_json`** — `_raider_count` became `_roster` and
`raider_count` is now its length. Existing rows stay NULL until the startup
relink sweep rewrites them, which it does on every boot; a missing roster costs
the match its cross-check, never a wrong merge (a named zone plus an
overlapping window still stands, an unknown zone does not).

**Precedence is two decisions, in two places, on purpose.**

- `GET /api/zone-runs` stamps `raid_key` (the cluster's lowest run id — a
  handle, meaningless outside the payload), `parses`, and `primary`: the site's
  pick, viewer-independent, so two people discussing a raid are reading the
  same numbers. `_score` is coverage — fights, then combat time, then roster
  size, tie-broken toward the first upload. Someone who zoned in for the last
  two pulls has a real parse of two pulls, and a stranger should not land on it.
- **Your own parse wins**, and that is the browser's to apply (`Home.jsx`
  `chooseParse`), because it depends on who is looking and the payload is
  shared. Your numbers are the ones you can check against what you remember,
  and yours is the one that survives the sharer leaving the group.

The list draws one row per raid with a `Parse` select naming the uploaders
(`character_name`, fight count, "(yours)"); the raid page carries the same
control in its head and switching NAVIGATES, because the fights and the vantage
point are all theirs. `GET /api/zone-runs/{id}` serves `alternates` for it,
filtered through `VISIBLE_RUN_IDS` — the switch re-sorts raids the viewer was
always allowed to open, it is not a directory of who else parsed the night.

Clustering happens AFTER the source/size filters, so narrowing to one group
narrows the menu with it rather than offering parses that are no longer listed.


### `GET /api/encounters/timeline?ids=…&bucket=auto`

Per-actor damage / heals / damage-taken bucketed over time. The clock is the
**concatenated** combat clock — the between-fight gaps are removed — so a
multi-fight selection reads continuously and `duration_s` still equals the
summed `duration_s` the tables divide by. `segments[]` carries the fight
boundaries, `markers[]` the deaths. `auto` picks the finest bucket from
`[1,2,5,10,15,30,60]` that stays under 240 columns. Credit follows the same
pet rollup as `statsroll`, and series key by `name|kind` to match `/agg`.
Pruned sessions contribute nothing and are counted in `pruned_encounters`
(`pruned: true` when all of them are) — the tables still work there, the plot
does not.

### `GET /api/encounters/deaths?ids=…&window=12`

One entry per player death with the incoming hits and the healing received in
the `window` seconds before it (`t` relative and negative, far edge
inclusive, clamped 3–60s, capped at 40 entries per list with a `_truncated`
flag). Deaths use the same death/kill rules as `statsroll`, including the
logger's bare-name pet.

### `GET /api/encounters/aoes?ids=…`

Incoming raid AoEs for the selection (`pipeline/aoes.py`), one row per
(enemy source, ability) with every detected cast attached.

The log never says "this was an AoE", so the definition is behavioural: a
second in which ONE enemy ability touched at least `MIN_TARGETS` (5) players
is a **cast**. Everything that ability does for the next few seconds — DoT
ticks, a second wave on a second group — belongs to that same cast; the merge
threshold is `max(6s, 0.4 x the reported timer)`, because a 60s AoE does not
land twice in 24 seconds. Both damage and *ability-named* avoids count: "The
Corsolander tries to crush Brandomar with War Stomp, but Brandomar resists" is
the AoE, while a bare "tries to crush X, but X parries" is a melee swing and
carries no ability, so it never enters.

Two timers sit side by side and the gap between them is the point:

- **reported** — ACT's spell-timer list, shipped as `backend/refdata/
  act_spell_timers.json` (446 entries extracted from Lindsay's ACT config;
  only `<SpellTimers>` name/duration/category, not the chat triggers). Joined
  by ability NAME, which works because ACT keys off the same log string.
- **observed** — the shortest interval between two casts that REPEATS, within
  one fight (the wait between two pulls is a raid taking a break, not a
  cooldown).

Shortest-repeating rather than mean or median because of how the measurement
fails: an AoE that never reached five people is a cast we cannot see, and a
missed cast makes one gap look like two — it can only ever make a gap LONGER.
So the smallest gap that happens more than once is the closest thing to the
real timer, `observed_agree` says how many intervals agreed (two is a guess,
twenty is a measurement), and `missed_hint` counts the gaps that look like
multiples. On the real DB: Blanket of Eternal Night 60.2s observed vs 60
reported (22 agreeing), Ydalian Bolt 47.7 vs 49, War Stomp 47.4 vs 45 —
and Furious Storm reads 52 against a reported 45 from three different
casters, which is the reported timer being wrong for this expansion.

**Coverage** is who was not hit: `avoided` (Bladedance and friends, an avoid
event) plus `absorbed` (Tortoise Shell and friends, a zero-damage hit with
`F_ZERO`), minus anyone the same cast also hit — a second wave landing on
someone who parried the first is a hit, not a block.

GOTCHA: entities are keyed by NAME, so six "a maven of wisdom" pulling the
same AoE read as one mob casting it six times as often. `instances_hint`
flags the giveaway — an observed timer that is a clean fraction of the
reported one, from a source that is not a named — instead of reporting the
ACT list as wrong. Named bosses, the case that matters, are unique.

### `GET /api/encounters/class-stats?ids=…` — the Class tab

The stats only one class can answer. "Was Jester's Cap up on the assassin all
fight" is a troubador question and nobody else's; as a column in the combatant
table it would be blank for twenty-five classes out of twenty-six. So the
selection is split by class instead, and each class owns its panel.

`pipeline/classstats.py` is a **registry**, and that is the whole design: a
metric is one `@register(...)`-decorated function declaring its columns and
returning rows. The endpoint enumerates the registry, the frontend
(`ClassPanel.jsx`) formats by column UNIT (`text|num|pct|secs|clock|rate`), and
nothing else has to change. Adding "Perfection of the Maestro uptime" is a
Python function, not a migration plus an API change plus a component.

Rules the shape enforces:

- **`blurb` is required**, and it carries the LIMIT, not the pitch. These stats
  live at the edge of what a log can prove — a buff with no fade line, a proc
  with no logged source — and the caveat belongs beside the number, in the
  panel, rather than in a doc nobody opens.
- **A class with no metrics is still a section.** The honest state of this tab
  is "we know who was here, we have not written their stats yet", and the
  frontend says exactly that (`Coming soon.`). It is not an error state.
- **Class resolution is not redone here.** The actor list comes from the
  memoized `/agg` payload the Damage tab already fetched, so "what class is
  this raider" has one answer per page — the one `classguess.resolve_class`
  gave, dated to the fight.
- **`class_source == 'unidentified'` is not a class we failed to guess.** It is
  the refine pass saying nothing in the log proved a person was behind the
  name, which usually means a summoned pet. Those names appear in neither the
  class sections nor the unmatched list; filing them under "class unknown"
  would be a claim about a pet.
- **A metric that raises is isolated** (`status: "error"`) and the rest of the
  tab renders. One bad regex must not take the Class tab out for a whole raid.
- **`needs_events=True` on a pruned selection reports that** rather than
  returning zeroes: pruning keeps the rollups and drops the events, and "no
  uptime" and "no events" are different answers.

`Ctx.events(types)` is the one expensive door — stored events for the live
encounters with entity ids resolved to names, cached per type-set for the
request, so two metrics wanting casts pay for one read.

`test_classstats.py` covers the pipe with stub metrics (it empties the registry
for every test, so the shape stays pinned whatever real metrics exist);
`test_classmetrics.py` covers the real ones.

### Curated buff lines (`parser/buffs.py`) — and Jester's Cap uptime

A beneficial buff is nearly invisible in an EQ2 log: no damage, no heal, no
name, no fade line. A handful of abilities do print flavor text, twice, and
**both lines are written for everyone in chat range**:

```
Vestigial begins to play the song of the Jester.     <- the cast, caster named
The Jester inspires Rorschach.                       <- the landing, target named
```

That is the only place in the parser where ANOTHER player's cast is visible at
all (`You prepare …` is the logger's own and nothing else), which is what makes
buff uptime computable from any raider's upload rather than only the buffer's.

**Curated, not generic.** The third-person grammar exists — `<Name> begins
<flavor>.`, 822 `Tasrin begins a phantasmal enchantment.` lines in one raid —
but the flavor names an ability LINE, not an ability ("an augmentation song" is
every troubador group buff), and the first-person form is not even always a
spell: `You begin to breathe normally.`, `You begin to move faster!`, `You
begin to choke!`. A line earns an entry when its flavor identifies ONE ability
and its landing names the target. Each entry carries a `token` substring so the
regexes never run on the lines that cannot match.

Two event types come out: `buff_cast` (src = caster) and `buff` (tgt = who got
it). `_pair_buffs` gives a landing the caster that produced it — the two lines
are written independently, so the only link is time, and Census puts the cast
at 1s. Across a three-troubador log that left **590 of 596 landings with
exactly one candidate caster and none ambiguous**; when two casters do fall in
one window the landing keeps NO source, because picking one would invent
attribution that reads as measured.

**Blast radius is deliberately nil.** `statsroll` ignores both types (the
if/elif chain has no branch for them), and `segment_events` only ever opens or
extends a segment on `damage`/`avoid` — so a troubador chain-casting between
pulls cannot merge two fights, no ability row grows, no class vote changes, and
ACT parity is untouched. The events are read by the Class tab alone.
`PARSE_VERSION` 19 is the bump that backfills them into stored sessions.

**Jester's Cap uptime** (`pipeline/classmetrics/troubador.py`) is the first
metric. Census: troubador 65, single target, +22.5–42% Reuse Speed by tier,
**30s duration on a 30s recast** (25s with the Enhance AA). Duration equals
recast, so ~100% on one target is the ceiling and the number measures chain
discipline rather than timing. Coverage is the UNION of the applications'
windows, not their sum — an early refresh extends the buff, it does not add a
second one — clipped to each fight and cut short by the target's death (a buff
does not survive it and a rez does not bring it back). Applications are read
with a one-duration lookback (`Ctx.events_around`), because a cap landed during
the pull covers the opening of the fight and belongs to no encounter;
the window is bounded to each selected fight's own run-up rather than opened to
the session, since authorization here is per encounter.

On the real 2026-08-03 raid the parser finds 829 casts (Vestigial 781,
Piedpipper 48) and 820 landings across 12 targets, 816 of them credited.

Still open, and stated in the metric's `blurb` rather than in a doc: a cast
out of chat range is not logged at all, so every count is a floor.

### Perfection of the Maestro — a metric with no line at all

PotM prints nothing: no cast line, no landing line, no fade. All three were
looked for. The only `augmentation song` casts in a raid night are the
concentration buffs (77 across a five-week log, **none** within 35s of a PotM
window), and the `An augmentation song affects X` landings name mobs buffing
themselves. What gives it away is its PROC — Census says PotM casts *Precise
Note* on a hostile spell cast, and exactly one spell in the game casts Precise
Note. **A Precise Note is proof its caster had PotM that second**, and it is
the only proof there is. 35,452 of them were already in the events table, so
this metric needed no parser change.

Everything it reports is therefore a floor, and the two constants are measured
rather than assumed:

- **`WINDOW_S` = 30.** Census says 20s, Enhance: PotM adds 10, and the longest
  proven run across the reference raids is 31s — consistent, and it rules out
  reading Census's base row as the answer.
- **`JOIN_GAP_S` = 3.** How far apart two procs can be and still count as one
  covered stretch. Across 35,339 stored procs, 95% of consecutive gaps inside a
  window are ≤3s, and 3s is the largest join that does not start inventing
  coverage: runs longer than the buff can possibly last are 0.5% of runs at a
  3s join and **6.8% at 8s**. A generous tolerance does not find more coverage
  — it manufactures the overlap below, which is how the first cut of this
  metric reported 182 wasted seconds that were not there.

`Windows` counts CASTS, not stretches — a proc more than one duration after the
window opened cannot belong to it, while counting stretches would count how
choppy someone's casting was (a caster who pauses twice inside one window has
three stretches and one buff).

**Double-covered** is the RoK question asked early. One troubador cannot chain
PotM (90s recast, 30s buff), so a covered stretch longer than one window took
more than one cast, and the excess is buff paid for twice. Group-scoped in EoF
that is a rarity — 26s in a ten-fight Vyemm run, nothing at all in most — but
when the buff goes raid-wide next expansion it becomes the direct measure of a
second troubador's cast landing on someone already buffed. The arithmetic is
the same in both eras, so there is no era switch: the column is quiet today
because the waste is not happening yet.

### `GET /api/encounters/loot?ids=…` — the Loot tab

What the chests in the selection gave, and who ended up with it. One row per
item off one chest: the fight it belonged to, the mob whose chest it was, the
raider who won or took it, and the item's own card (icon, rarity, wiki page).
The tab is LAST, after Class Report — everything before it is the parse.

**Chest loot only, and that is the feature.** The log writes chest drops and
corpse drops with the same verbs, and only the source clause tells them apart:

```
Buls wins the lotto for <ITEM> from the Exquisite Chest of Zylphax the Shredder.
Buls wins the lotto for <ITEM> from the corpse of a doomed visitant.
```

A corpse gives shards, body parts and vendor coin — a couple of hundred lines a
night that bury the eight items a raid remembers. So the source clause is
REQUIRED and must name one of the four chests (`Exquisite`, `Ornate`,
`Treasure`, `Small`); a line with no source at all — `wins the lotto for a
<ITEM>.` — is dropped too, because "probably a chest" is not evidence.

**Loot is not an event, and it must never become one.** It is written beside
the parse into `loot_drops` and never into `events`. A looter is a bare NAME on
a line, and pushing it through `EntityResolver` would mint an entity — putting
somebody who only walked past the chest into the fight's roster, its class vote
and its ACT parity. `pipeline/loot.py` resolves nothing, rolls up nothing and
segments nothing; `test_loot.py` pins that `Buls`, who only ever appears on a
loot line, never reaches `/agg`.

**Two lines say what happened and only one knows where it came from.** The
lotto/loot line names the chest and the mob; a second line confirms the winner
actually took it — `Buls looted the Fabled <ITEM>.` — and it names no chest, so
it can never CREATE a drop. It is matched back by (item, looter) and enriches
one. A win nobody confirmed is kept and flagged: declining an item you rolled
for happens, and the raid remembers the roll either way.

**The rarity is Census's, not the log's.** The `looted the Fabled …` line only
prints for people standing near you — 15 of the 43 in the golden fixture — so
reading rarity off it would leave two thirds of a night blank. Census has the
tier for every id.

**The fight is found by the mob's name, not by the clock** — a ladder, most
exact first, because a chest is opened after the pull and sometimes after the
next one has started (median 26s in the archive, tail past twenty minutes):

1. the fight was NAMED for that mob (`encounters.name`) — 69% of the archive;
2. that mob was IN the fight, from the events — 29%, and the case a chain pull
   needs: the pull is labelled for one mob and the chest belongs to another;
3. the last fight before it, within `NEAREST_S` (900s) — 2%, and marked
   `attribution: 'nearest'` so the table can say `approx` rather than claim it.

Rung 2 reads `events`, which pruning eventually removes, so an old session
falls to rung 3 or to nothing rather than to a WRONG fight. A drop with no
fight keeps `encounter_id` NULL and is returned when it falls inside the
selection's own span — bounded by fights the caller was already authorized to
see, never by a whole session.

**Who else wanted it** — hovering the looter shows the contest, and the card
says which of two very different records it is looking at:

- **The lotto** (`source: 'lotto'`) is the game's own, and it prints the whole
  thing against the item BY NAME, so it cannot be wrong. `Now rolling on
  <ITEM>...` opens a block, `- Khael chooses GREED and rolls 43.` fills it, the
  `wins the lotto` line closes it. Blocks INTERLEAVE — several chests roll at
  once — so they are keyed by item, never by "the last block we saw".
  Resolution order is NEED before GREED and highest first inside each, because
  a NEED of 12 beats a GREED of 98; checked against **752 real blocks and the
  winner is the top line in every one**.
  `- Beaux chose GREED.` with no number is 3,919 lines in the archive and is
  KEPT as a roll with no value: they wanted it, and that is most of what a
  loot list is for.
- **`/random` dice** are a raid running loot by hand — announce the item,
  everyone rolls, loot it to the winner. `Random: Reyfiler rolls from 1 to 100
  on the magic dice...and scores a 2!`. Nothing in that line says WHICH item,
  so attribution is a ladder: an announcement that linked that exact item
  (`announced`), else the nearest burst (`nearby`, a proximity claim the panel
  and a dotted underline both admit to). The window is TWO-SIDED — on the raid
  this was built against, 22 of 39 drops had their burst before the loot line
  and 12 after — and dice are NEVER mixed in beside a lotto block.
- GOTCHA: the dice line's `Random: ` prefix is the channel tag, not the
  roller. A survey that normalised the first token read it as the name and the
  pattern matched nothing on real data.
- Two logs of the same night can disagree completely about this. Vestigial's
  MMIS raid used the dice and has no lotto block anywhere in the zone; Bobby's
  raid the same evening used the lotto for all 51 items. Neither log is wrong
  — the roll list is a property of how that raid ran loot, and of whose client
  was listening.

**History came from the archive, not from a re-upload** (`tools/
backfill_loot.py`). Every session's raw is still on disk, so one pass over the
same bytes filled 1,809 drops across 23 nights. Deliberately **not** a
`PARSE_VERSION` bump: loot changes no stat, no segment, no roster and no
rollup, so making the startup sweep re-derive every session to pick up a table
nothing else reads would be work for nothing. `clear_derived` still drops loot
with the encounters it points at and the parse writes it back, so a reparse
neither loses it nor leaves it pointing at ids that no longer exist.

### Items as reference data (`backend/items.py`, schema v32)

The display record for an item a log named. Census answers what it IS, the wiki
answers what it LOOKS like — one row and one file serve every account forever,
because this is a fact about the game and not about anybody's raid.

**The log's item id IS the Census item id**, written signed. `\aITEM
-1813422462 -590025310:Hoop of War\/a` off the raid log is Census item
2481544834 — verified against Census's own `gamelink`, which it returns in the
log's notation. So this is an exact lookup and **none of the reasons gear procs
are closed as wontfix apply** (`docs/census-abilities.md`: 212k items, no
reverse index, 13/60 on a full-text trial). There is nothing to search for.

**EQ2i hosts the game's icons as `File:Item <iconid>.png`.** Census hands out an
`iconid` and no image; the wiki has the picture. One file per ICON, not per
item — 952 items in the archive resolve to 489 pictures, 2.2 MB, served from
`/api/items/icon/<iconid>.png` with no visibility check because there is no
raid behind it.

- GOTCHA: **`format=original` is not optional.** The wikia CDN re-encodes to
  WebP on the way out — a URL ending `.png` answers with RIFF/WebP whatever
  `Accept` asks for. The parameter turns the optimiser off, and the bytes are
  then verified by magic number rather than trusted.
- GOTCHA: an item page is often a **disambiguation** (`Hoop of War` is two
  lines pointing at `(Version 1)` and `(Version 2)`), so it resolves to the
  version the wiki lists first. `gamewiki.fetch_wikitext` cannot be reused for
  this: it asks for `redirects=1` and then discards the mapping, and here the
  mapping IS the answer.
- Coverage on the real archive: 949/952 rarities, 948/952 icons, 752/952 wiki
  pages. The misses are spell scrolls (`Tyrant's Pact V (Adept)`), which EQ2i
  has no per-tier page for; they still show a name, an icon and a rarity.

**The examine window is a REPLICA of EQ2i's item box, not a screenshot of it**
(`stat_block()`, hovering an item name in the Loot tab). EQ2i's box is itself a
replica of the in-game examine window — black, Times, a glowing rarity word,
yellow uppercase flags, a green block of flat stats and a light-blue one of
property modifiers — and it is built out of **the same Census record we already
hold**. So the card is our data in the wiki's clothes: the `.ew-*` / `.xqc-*`
class names and every colour in `base.css` are copied from
`MediaWiki:ExamineWindow.css`, and the content comes from `items.stats_json`.

That beats both alternatives outright. Against screenshotting: crisp at any
zoom, selectable, one cached row instead of a headless browser. Against
embedding EQ2i's rendered HTML: no third-party markup in the page, no
sanitiser to get wrong, and it still works for the ~200 items whose wiki page
does not exist.

- **Green is everything flat, blue is everything that modifies a property**,
  which is the wiki's own split (`.ew-stats` / `.ew-effectlist`). The green
  block reads attributes → resistances → skills, EQ2i's order and NOT by size:
  sorting it as one list put a big `All` above the primary attributes.
  Percentages are the blue block only, less DPS and Haste.
- Census's `ac` entries are per resist school; matching values read as one
  `Resistances` line the way the game shows them, and disagreeing ones are
  listed rather than summed into a wrong number. A modifier type the card has
  no place for is DROPPED, never guessed at.
- **`all` / "All" IS Ability Modifier**, and this one is a correction rather
  than a rename. Census's display name reads as "+68 to all something" and is
  nothing of the kind; the wiki settles it — Bee Sting's `EquipInformation`
  carries `abmod = +62` and Census's record for the same item carries
  `all: 62`, beside its own separate `strength` and `stamina`. Ability Mod is
  one of the two stats that matter on this server, and "All" hid it in plain
  sight on 200 items.
- **A stat this server does not have yet is not shown** (`ERA_HIDDEN`).
  Census describes the item as it stands on LIVE, so a TLE raider was being
  shown a **Crit Bonus** their character cannot use — worse than showing
  nothing, because it invites comparing two items on a stat neither one
  grants. Crit Bonus is the whole list today; Fervor is the other of its kind
  and belongs there the moment an item turns up carrying it. Delete an entry
  when the server gets the stat and re-resolve; nothing else changes.
- **A weapon leads with Damage and Delay**, using the BASE range and the
  rating (`72 - 216  One-Handed Piercing` over `4.0 seconds  (72.15 Rating)`)
  — EQ2i's own choice. Census carries the mastery range too; that is what the
  weapon does with the skill capped, a different claim from the one the item
  box makes. All 98 weapons in the archive resolve.
- **The item's own proc comes from the WIKI, not Census** — `effectlist=
  {{EquipmentEffect|Mind Shatter|VII}}` and the asterisk-indented `effectdesc`
  bullets, whose DEPTH is kept because `*When Equipped:` is the condition and
  `**Increases mental damage…` is what it then does. This is the FORWARD
  direction and it costs nothing: the page is already in hand for the wiki
  link. It is why the gear-proc wontfix does not apply — that one is *ability
  name → which item casts it*, a reverse lookup with no index. A disambig
  resolves to a version page, and the effect lives on the version page, so
  those are fetched in a second batched pass rather than left without one.
- The ten **adornment-slot gems** are cached from the wiki like the icons — a
  fixed set, the same picture on every item, served from
  `/api/items/adorn/<colour>.png`. Some are uploaded `.png` and some `.jpg`,
  so the format is decided by MAGIC NUMBER and the file is named to match.
- One line is ours and not EQ2i's: **Dropped by**, the mob whose chest it was.
  The wiki cannot know that; the raid log does.
- The block is built at RESOLVE time and stored, so the hover card is a read.
  Widening the card therefore means re-resolving:
  `backfill_loot.py --refresh-census`.
- 605 of the archive's 952 items have one. The rest are spell scrolls,
  patterns and harvestables, which genuinely have no equipment stats — those
  get no card rather than an empty one pretending to be an examine window.
- **It does not theme.** An examine window is black in a light client too;
  recolouring it is the one change that would stop it looking like the thing
  it is quoting.
- The card is `position: fixed` in `document.body`, placed from the name's
  rect, for the same reason `.pickermenu` is: the table scrolls sideways
  inside `.tablewrap` and a card parented to a cell is clipped by it. It is
  `pointer-events: none`, so it can never sit between the cursor and the link
  it describes.
- Its height is **MEASURED, not guessed**. These cards are not one size — a
  weapon with a proc and a four-line description is more than twice a
  pattern's — so a fixed "does it fit below" threshold cut the tall ones off
  at the bottom of the window. Below if it fits, above if it fits there, else
  pinned to the top edge with a cap that makes it scrollable (and only THAT
  card takes the pointer back, because one nobody can reach cannot be
  scrolled). It renders hidden for the frame it is measured in, so a tall
  card never flashes at the wrong spot.
- CAVEAT worth knowing: Census returns the item as it stands on LIVE, and the
  wiki's `EquipInformation` template holds much the same numbers (checked
  against `Hoop of War (Version 1)`), so there is no era-correct source that
  differs. What TLE actually grants may not match, and no source here can
  tell us.

**Nothing else is fetched on a page load.** Resolution runs after a parse (outside
the write transaction, like the roster sync — failing costs a raid its pictures,
never its parse) and in `backfill_loot.py --resolve`. The endpoint serves what
is already known, and an unresolved item renders as the name the log wrote with
`unresolved` counting how many. CI never reaches either source:
`items.network_allowed()` reads the same `CENSUS_AUTO_REFRESH` switch conftest
turns off.

### Frontend

- `lib/classes.js` owns identity. **Color is assigned by EQ2 archetype
  (fighter/priest/mage/scout), not by class** — the palette validator says
  four hues separate cleanly and twenty-six cannot, so the family color
  carries identity in stripes and legends while the per-class tint is
  decoration on a chip that always spells the class out. Role
  (tank/healer/dps/utility, mirroring `coach.descriptive.ARCHETYPES`) drives
  the filter chips instead. Chart series use their own 8-color validated
  palette in fixed selection order, since two raiders of one class would
  otherwise draw the same line.
- **Selection sums** (`SelectionBar`): checking rows adds them up in a sticky
  footer instead of immediately hijacking the panel — comparing is a deliberate
  second click. The same bar serves the ability breakdown, so "what fraction of
  my parse is my priority spells" is a few checkboxes.
- **Click reads, tick compares** (`ZoneRun.focusActor` vs `toggleCmp`). A click
  on a raider's row REPLACES what the drilldown column is showing; only the
  checkbox adds a second parse beside the first. They were the same gesture for
  a while — a click ticked the box — and reading down the table three names deep
  left three quarter-width parses side by side when what was wanted was the
  third one. Clicking the raider already open closes the panel, so a click still
  undoes itself, and mob/pet rows (which have no checkbox) stay plain
  drilldowns on `?actor`.
- **A comparison is a ROW, and it scrolls sideways** (`.cmpraiders`, base.css).
  The raider boxes never wrap: past two or three they run off the right-hand
  edge and take a horizontal scrollbar with them. Wrapping the third box under
  the first read as a grid rather than as parses lined up, and it doubled the
  panel column's height — which, with the rail and the raid table stacked in
  the other column of the same grid, silently pushed the raid table an inch
  down the page. `.workspace.withpanel` also pins that to `grid-template-rows:
  auto 1fr` so a tall panel grows the table's row, never the rail's.
- **Rank coloring** (`stats.rankScale`/`rankColor`/`rankTitle`, applied through
  `SortableTable`'s `cellStyle` / `cellTitle` hooks): a row's PLACE among the
  same-role raiders on screen, mixed into the text with `color-mix`, with the
  standing spelled out in the cell's tooltip ("3rd of 7 healers").

  It has been wrong twice. Hard terciles called the bottom third of the raid red
  even when the whole field was within a point of each other — exactly what crit
  becomes in later expansions. Distance-from-median fixed that and introduced a
  worse problem: the size of the gap and the size of the group both moved the
  color, and each row was measured against ITS OWN role's median, so one column
  carried up to four yardsticks at once. On the Minion of Evil fight that put a
  red 9,662 DPS (necromancer, against a 10,630 DPS-peer median) two rows above a
  green 1,868 (defiler, against a 981 healer median), with nothing on screen
  saying why. Position is the one thing a reader can verify against the column
  they are already looking at.

  **A row with no role gets no color**, and neither does a group under
  `MIN_PEERS`. The old fallback — borrow the whole raid's median — is what made
  the mixing invisible: a third of the roster has no class (Census covers about
  half the ability names) and tanks are usually fewer than four, so most of the
  uncomparable rows were quietly on the raid-wide scale beside role-scoped
  neighbours. Better to claim nothing.
- **Decomposition** (`stats.decompose`): DPS split into activity × hit size ×
  crit × alive%, each against the best peer, naming the biggest gap — the
  difference between "you're 20% behind" and "you cast 30% less".
- **A parse table is FROZEN: row one and column one hold still**
  (`SortableTable`'s `frozen` prop + `useFrozen`, `.tablewrap.frozen` in
  base.css). Reading Crit % off row nineteen means carrying a name across ten
  columns and a header down nineteen rows, and a table that scrolls both away
  is read from memory. Every parse surface is frozen: the raid table, the
  drilldown, both comparison surfaces (`BreakdownTable` turns it on for
  itself — a parse looks the same wherever it is rendered). A checkable table
  pins the checkbox WITH the name, at a measured offset (`--fzleft`), because
  the box is part of the name column's job rather than a column of its own.

  Two things the stylesheet cannot decide, so JS measures them. The divider
  down the frozen column only draws once the table is actually scrolled
  sideways (`.xscrolled`) — on a table that fits it is a line for no reason.
  And the pinned cells are **opaque** (`--frozen-bg` / `--frozen-head`, the
  translucent surfaces already composited over the page): a name column at 82%
  with figures sliding under it is worse than no freeze at all. Row tints
  (hover, selected, the All line, a hover-linked row) go back on top as a
  gradient LAYER, since `background` alone can only be tinted or solid.
- **A long ability name is shortened only when the table cannot fit**
  (`.overflowing` + `.abname`). Not when there is room, not when the name is
  short, and never the badges/⚙/expander beside it — those are controls. The
  full name is on the cell's `title`, so an ellipsis is never the end of it.
  Un-shortening is asymmetric ON PURPOSE: asking "does it fit?" of a table
  that fits BECAUSE it is clamped oscillates, so the width the table wanted
  before the clamp is remembered and the names only come back when there is
  room for that.
- **A default-hidden column is a BASELINE, not a first guess**
  (`SortableTable`, `localStorage` under `eq2adv:cols:<prefsKey>`). Stored
  prefs are TWO lists — `hidden` (what the reader turned off) and `shown` (what
  they turned back on) — and `defaultHidden` sits underneath both: hidden if
  the caller hides it by default or the reader hid it, unless the reader asked
  for it by name. One list could not express that. It REPLACED `defaultHidden`
  wholesale, so the first time anyone touched the Columns menu on a comparison
  table its whole starting layout evaporated, and any default-hidden column
  added later turned itself on for everyone who had ever dragged a header —
  the exact opposite of default-hidden. The menu's reset says **Reset to
  defaults**, because that is what it does: order and visibility both.
- **Each parse tab offers the OTHER tab's rate, folded away.** `HPS` is a
  default-hidden column on Damage and `DPS` is one on Healing (`tabHidden` in
  `ZoneRun.jsx`), each sitting immediately beside the rate it pairs with. A
  shadowknight who healed 400k while topping the parse is a fact about the
  damage tab, and reading it meant switching tabs and finding the row again.
  The layout is per tab and per browser, keyed `zonerun:<tab>` — not per run —
  so ticking it once is ticking it for every raid you open afterwards.


## The sibling TLE sites in the top bar (`App.jsx`, `SITES`)

The bar carries two links out to the other EQ2 TLE sites — wikQ2 and eq2lexicon
— in the slack between the nav and the tools. The intent is one door: a raider
opens eq2advanced and reaches the rest without hunting bookmarks. It is the same
gesture eq2lexicon already makes with its own row of plaques, and the reason the
tokens file calls this site a "visual sibling of eq2lexicon.com".

**They are drawn as plaques, not tabs.** Everything in `nav` is somewhere in
THIS app; these are somewhere else, and the shape has to say so before anyone
reads the words. Gold, bordered, filled, heading face — the dress code of the
row of sites they belong to.

**wikQ2 opens INSIDE the shell; eq2lexicon cannot, and no code here can change
that.** `wuoshi.eq2lexicon.com` answers every request with `X-Frame-Options:
DENY`, which the browser enforces against every origin — there is no markup,
sandbox attribute or client-side trick that defeats it, and the only two ways
around it are their cooperation (a `frame-ancestors https://eq2advanced.com`
header on their side) or reverse-proxying their site through this backend. The
proxy is rejected: it would rehost somebody else's app under our domain without
their permission and carry our users' logins to THEIR site through our server.
So that link opens away and wears the arrow that admits it. If they ever add the
header, deleting `away: true` from its `SITES` entry is the whole change.

**The framed tab is hidden, never unmounted** (`.siteframe.away` is
`display:none`). This is the whole reason it behaves like a browser tab: a
hidden iframe keeps its document alive, so the search box, the results and the
scroll position are all still there when you come back. Unmounting it — or
merely letting React MOVE it in the tree — reloads it and loses their place,
which is why the frame is rendered as a sibling of `<main>` and NOT as the
`/wiki` route's element. The route exists (so the URL and the back button work)
and renders `null`. `wikiOpened` latches on first visit and never clears, so a
raider who never presses wikQ2 never loads it.

**Across a hard reload there is nothing to restore, and that is wikQ2's to fix,
not ours.** A cross-origin frame will not tell the parent what page it is on, so
a saved place would have to come from wikQ2 itself. wikQ2 today keeps its entire
state in React and never writes the query to its URL — reloading it in a plain
browser tab loses your place too, so the frame is not worse than the real thing.
If wikQ2 ever `replaceState`s its query into the `?q=` deep link it already
accepts, the parent can persist that and restore it on load.

**`--topnav-h` is measured, not a constant** (`ResizeObserver` on the header).
The frame is pinned under the bar rather than laid out after it, because inside
normal flow a percentage height has nothing to resolve against and an iframe
collapses to nothing. The bar wraps under 900px, so its height is a live value.

**The framed tab follows this site's theme, and the theme travels TWICE.** The
initial value rides in on the frame's URL (`?theme=`), because wikQ2's pre-paint
script reads it before it draws anything — a frame that opens white inside a
dark shell and corrects itself a moment later is worse than not syncing at all.
That src is then FROZEN (`setWikiSrc((s) => s ?? …)`): `src` is a prop, so
re-rendering a changed one reloads the frame and throws away the place this tab
exists to keep. Every later toggle is a `postMessage` instead, aimed at
`WIKI.origin` and never `'*'` — a wildcard target hands the theme, and the fact
that this frame exists, to whatever window happened to load there. wikQ2 checks
the sender against its own `lib/frame-parents.ts` allowlist and does NOT persist
what we send, so somebody visiting wikQ2 directly still gets their own choice.
