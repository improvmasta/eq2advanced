# Zone runs, the raid page and its APIs

Index: `ARCHITECTURE.md`.

## Zone runs — the navigation model

Files ("sessions") are the INGEST unit only; the UI navigates **zone runs** — one
contiguous visit to one zone by one character, derived from encounter rows by
`pipeline/zoneruns.py`.

- **Dedupe.** Overlapping uploads produce byte-identical encounters (segmentation is
  deterministic per parse_version). `rebuild_zone_runs` groups a character's
  encounters by `(started_ts, ended_ts, zone, name)`; newest parse wins, then widest
  coverage, then lowest session id. The rest get `encounters.dup_of` and never join a
  run. Marking, not deleting, so every parse stays complete and the marks re-derive.
  - **Scope is ONE CHARACTER's own files.** Two raiders who logged the same pull are
    two observations, not a duplicate. What needs "one real pull" across characters
    is timer learning, which derives its own (`aoelearn.pull_keys`).
  - **`parse_version` is not a partition**, and used to be. The group key *is* the
    segmentation result, so members have already agreed on it; a fight two versions
    really did segment differently lands in two groups and is untouched. The
    partition just left permanent duplicates behind every session that stopped being
    sweepable — `_reparse_stale` walks `ready`/`parsing` only, so sessions at `error`
    held an old version forever and `dup_of` was set on zero rows database-wide.
- **Segmentation.** Canonical encounters in time order split on a zone change or an
  idle gap > `ZONE_RUN_GAP_S` (3600s). NULL-zone encounters form "Unknown zone" runs.
- **Id stability.** The upsert matches recomputed runs to existing rows by zone +
  overlapping window, so `/zones/:id` URLs survive reparses and backfills.
- **Hooks.** End of `parse_session`, live `_flush`, and a startup relink sweep in
  `main.py` before AND after `_reparse_stale` — that sweep is also the migration for
  pre-`zone_runs` databases (schema v6).

API: `GET /api/zone-runs` (the date-grouped raid list), `/api/zone-runs/{id}`,
`/api/zone-runs/{id}/report` (`raidreport.build_for_encounters` — cross-session sets,
pruned encounters from frozen reports, `partial` flag). `GET /api/encounters/agg`
accepts cross-session id sets: every session is visibility-checked, actors merge by
`name|kind`, abilities by source key, `rollup_key` resolving pet credit.

Frontend: `/` = `Home.jsx`, `/zones/:id` = `ZoneRun.jsx`, `/import` = the import hub
(`/uploads` redirects), `/sessions/:id` = the per-file debug view. `?sel`/`?actor`/
`?tab`/`?cmp` are URL state.

**The parse is `ParseView.jsx` and the page is `ZoneRun.jsx`.** The tabs, tables,
filters and drilldown know nothing about runs: they take encounter ids, a raid report
and a `rail`. Everything that IS about the run — whose night it is, who it is shared
with, which fights count, the hand edits — stays on the page. That split is what lets
the dashboard show the SAME parse for a fight that just ended (`docs/live.md`). A
railless parse renders as one column (`.workspace.norail`).

**Insights is hidden** — one commented line in `ParseView.jsx`'s `TABS`. It is the one
tab the parse cannot render on its own (the coach engine is per session), so the page
passes it in as a function of the parse's own actor rows.

### The roster, and what counts as a raid

An encounter is a time slice, not a guest list. `_raider_count` is the run's ROSTER,
three rules deep, keyed by entity NAME (a run spans files, entities are
session-scoped):

- **player** — mobs and the pooled `Unknown` source are not people.
- **acted** — damage, heals, wards, cures, power, rezzes or swings. A row with only
  `damage_taken` is a bystander clipped by an AE.
- **presence** — in ≥ `ROSTER_PRESENCE` (25%) of the run's fights, minimum 2; under
  `SHORT_RUN_FIGHTS` (4) one is enough. This is the rule that does the work: across
  the corpus a raid sits at 45–100% of its fights and passers-by at 3–15%, nothing in
  between.

**A cooperation graph was built and REJECTED** — it moved 0 of 49 runs, because a
passing group does hit the mobs you are hitting. Don't rebuild it without a log where
presence demonstrably fails.

**Attendance is not content identity (schema v41).** `zone_runs.is_raid` is derived from
the committed zone reference: a raid instance is raid content; a mixed public/contested
zone is raid content only when its explicit raid target appears. Thus Castle Mistmoore's
heroic names and ordinary Loping Plains combat stay Solo/Group even when two nearby groups
push the roster over seven; Mayong Mistmoore and the Pumpkin Headed Horseman promote their
own runs. The roster threshold is only the migration fallback while a startup relink fills
the new field.

An uncatalogued target can be promoted conservatively when **three** signals agree: named,
at least seven contributors, and observed target HP of at least **10x the median successful
named heroic in its expansion**. The baseline is learned from Group, Solo-Group, Heroic and
Public parses; known raid zones and explicit mixed-zone raid targets stay out. At least 20
heroic observations are required, so a sparse era makes no claim. Median is intentional:
heroics are plentiful, and a few malformed raid-zone lines cannot move it like a maximum or
mean. In the current EoF corpus the median is about 330K and the largest observed heroic is
about 2.5M, putting the dynamic raid threshold near 3.3M without encoding today's level cap.

If the logged zone has no usable expansion (the malformed Trial of Leadership parse claimed
Qeynos), at least two named mobs previously seen in one expansion can supply the content era
for the HP comparison. The stored zone remains untouched. The HP disparity corroborates a
raid; neither a crowd nor one large heroic can claim it alone.

**A public zone can contain consecutive guilds' pulls without a zone line between them.**
For explicit contested raid targets, `_segment` starts a new run when consecutive pulls
share under half the smaller contributing roster. Trash between pulls does not hide the
boundary. This is why each Avatar pull can carry the guild that actually fought it and the
observer fact for the logger who only watched it.

**A missing zone line is recoverable, but only from consensus.** A raw Unknown-zone visit
whose named mobs have already appeared in correctly zoned logs adopts the one canonical zone
when at least two distinct names agree and no candidate ties. That recovers a Freethinker
Hideout attach/error from Zylphax + Othysis (and the rest), while one ambiguous named remains
Unknown.

## The fight rail (`components/EncounterTree.jsx`)

**The rail's head is the raid page's title block, and the only one.** A separate
`.pagehead` repeated the zone name and was hidden whenever a drilldown opened, taking
its buttons with it. `EncounterTree` takes `sub` (date · time range · character),
`actions` (badges, parse picker, Share, Compare) and `titled` (render the zone as the
page's `h1`; `/sessions/:id` passes it off so there is exactly one `h1`). The selected
FIGHT gets no title of its own — the active row says which one it is.

**Navigation and scope are two distinct gestures.** Clicking a fight (or the root, a
zone block, a `Trash ×N` group) makes it the only selection; ticking its checkbox adds
or removes it. Selection lives in `?sel=` (an id list, or absent for all), so a merged
set is a shareable URL; selecting everything collapses back to `all`.

**It is a tree with one root, not a list whose first item is special.** The root is
**Zonewide** on `/zones/:id` and **All fights** on `/sessions/:id` — a zone run is one
zone by construction, a session file can span several, and the label may not claim
more than it covers. The root is sticky, the run's length sits on it in the same
column every fight's length is read down, and the footer is always present once the
rail is selectable (appearing on the first tick shoved the list under the cursor).

Rows are three fixed columns — checkbox, start time, name — with the length
right-aligned in tabular figures. Repeated nameds carry an **attempt number** rendered
outside the ellipsis, because the truncated part of a long boss name is the part that
identifies the pull.

**One "on" for the whole panel.** The row checkbox is drawn in the switch's own track
colors, and the strip separates its two KINDS of control: presets left (what is
selected), counting rules right of a hairline (what the numbers mean).

**Wipes are counted by default** — ACT counts them, and a night with two wipes is a
night with two wipes. `?wipes=0` takes them out of every total while leaving them
listed and dimmed, and the head says how many were left out. Selecting a wipe on
purpose always shows it, so the filter can never empty the page. **There is no Nameds
switch**: the switches exist to take things OUT, and nobody reads a raid night with
the bosses removed.

**The head is three blocks, each answering one question:**

| block | question | contents |
|---|---|---|
| `.railhead` | what was this night? | zone (h1), date · time range, then character + guild + Live + `shared` + parse picker, `N fights · N hidden` right-aligned |
| `.railsec.seen` | who can see it? | `SHARING` + filled `.sharepill`s + a square gold `＋` |
| `.railsec.acts` | what can I do to it? | Compare (left), Edit (right) |

**A pill's fill says what kind of claim it is**: outlined is a fact somebody else
established (guild tag, Live, `shared`), filled gold is a decision the owner made (a
share), and `Public` is filled BLUE because it is a different kind of reach. Compare
wears `.btn.solid` everywhere, because the default gold-outline button is pixel for
pixel what every *on* toggle wears and beside a row of badges it read as a switch
already flipped.

**The share controls open where you clicked them.** `＋` is a square gold button at the
end of the pills (sized with `aspect-ratio`), it is a toggle, and `ShareDialog` renders
inside the `SHARING` section under the pills it edits — not as a card at the top of
`.wsmain`, a different part of the screen. Capped at `42vh` with its own scroll, so
somebody in ten groups cannot push the fight list off the bottom.

**Edit mode is a state of the rail, not a screen you go to.** `✎ Edit` sits at the right
end of the RAID row, owner only, and replaces it with one right-packed cluster
(`.editopts`, a max-width reveal degrading to a cross-fade) — Hide, Delete, Done — with
Done in the spot Edit was. **Compare is not in the edit row and `.editopts` is
`nowrap`**: four labelled buttons do not fit across this rail, and letting the row wrap
put Done on a second line at the far left. If it ever has to break it breaks as a UNIT
with Done attached.

Edit tints the column (`.rail.editing`, plus an amber rule drawn *over* the children in
a `::before`) and puts the same two verbs on every row. Nothing else moves — deciding a
pull does not belong is something you do WHILE reading the parse that told you so.
**The row buttons are drawn at full strength**, not hover-ghosts: a mode that can delete
a night must not make you hunt for its controls. (The raid LIST is the other way round,
because there the pencil sits on sixty rows of a page people mostly read.)

**Delete confirms in place.** `🗑` turns that row's controls into `Yes` / `✕`, and only
that row's — one `confirm` key in `EncounterTree`, one `rowConfirm` id on the raid list,
so two rows can never be armed at once. A dialog would have covered the fight it was
asking about. Hiding needs no confirmation because hiding is the undo.

**A hidden fight stays in the rail, struck through, with the switch that puts it back** —
the only place its owner can reach it. It is out of everything the rail COUNTS: the fight
count (`+N hidden` beside it), the kind switches, the group checkboxes, the footer, and
the selection handed to `/encounters/agg`. `ZoneRun` keeps the raw payload in
`allEncounters` and derives `encounters` from it; the rail is the one component that sees
both.

### Pets and NPCs

Two switches in the filter bar, right of the role chips, on every parse tab, off by
default. Who may have a row and what a row must CARRY to earn one are separate questions:
`kindAllowed` answers the first, the per-tab predicate the second (`rowsFor`).

They lived on Defense alone, on the reasoning that a non-raider row carries nothing but
DmgTaken. That is true of an owned pet (its damage is credited to its owner,
`statsroll.actor_key`, as ACT does) and false of a mob, which keeps its own credit
(`_OWN_ROW_KINDS`) — so a boss row has real damage, real DPS, its self-heals and
everything the raid put into it.

**A mob earns a Damage-tab row on what it TOOK, not what it dealt.** `damage > 0` is
right for a raider and wrong for a mob: plenty deal nothing at all, which is also the
easiest case to test the switch on, which is how it came to look broken. Raiders keep the
old test. A mob gets no share of a raid denominator and no rank color (`rankPool` returns
null for anything that is not a player). Deaths is the one tab the NPC switch adds nothing
to, which is honest — a death is credited to a player or their pet only.

### An auto-attack row is named by what it LANDED as

`piercing (melee)`, not `(melee)`. On TLE the damage type does real work: **infusions move
a player's melee onto another school, and a pet cannot be infused** — so the type stops
tracking the weapon for the player and keeps tracking the pet.

That is also the one case a CONFLATED pet can be picked apart: a remote summoner's pet
shares their name, so its swings sit inside their melee row but not their weapon. Where
one type in the row is the swing type of the archetype the parse shows out
(`PET_MELEE_DTYPE`), that sub-row is badged `pet swing`. It fires when it can.

**The split is a DISPLAY of the row, never a second row.** The rollup already stores
per-type totals (`dtypes`), so no `PARSE_VERSION` bump. Counts stay on the parent: the log
gives a per-type total and nothing else, so hits, crit rate and AvgDelay belong to the
swing chain as a whole — splitting them would halve a dual-wielder's swing count and
double the AvgDelay on each fragment.

### The Deaths tab is two columns (`TankDeaths.jsx` + `DeathList.jsx`)

*How did the tank die* is answered by ONE death in detail; *who died tonight* by all of
them in a list. Sharing one full-width table, the list answered the second badly and the
first not at all. Now a grid (`.deathcols.two`, one column under 1150px or with a
drilldown open).

**The page decides the layout, not the grid.** `.two` is added only when `hasTankDeath`
says there is something for the left column — a first grid child rendering `null` would
drop the death list into the narrow column and leave the wide one empty.

**The tank report is the two curves at three resolutions**: a **fact line** (took,
healed), a **ledger** of one row per second, and a **log** of every event. Tanks are
`roleOf(actor) === 'tank'`, all six FIGHTERS including brawlers.

Both tables lead with the WALL CLOCK (`fmt.clockS`), not a countdown: the rows are
consecutive seconds of a real night and the log everyone cross-checks against is stamped
the same way.

**`Net` is the ledger's whole point** — healed minus took, per second, red when they lost
ground. Every second of the window renders whether or not anything landed in it, because a
ledger that skipped its empty seconds read as a list of events and the two-second hole
where nobody healed — the thing that killed him — rendered as no row at all.

**FIVE seconds for the tank, THREE for the raid list, ONE request.** The fetch asks for
the wider (`TANK_WINDOW_S`) and `DeathList.clip` narrows to `RAID_WINDOW_S` in the browser,
which is EXACT: `_window_slice` caps each list at `DEATH_MAX_ENTRIES` and keeps the TAIL,
so the last 3s of a 5s window is complete even when the 5s list was truncated — which is
why `clip` only carries the truncation flag over when something was actually cut.

**There are no tenths of a second in an EQ2 log.** Lines are stamped in whole epoch
seconds, so `events.ts` is an INTEGER and per-second buckets are the log's own resolution.
Within one second, line order survives as `events.seq`, so each `/deaths` list is ordered
`(ts, seq)` — and the blow by blow can only claim ordering within a SIDE, since the payload
carries no `seq`. **Nothing on this tab may print a tenth it cannot measure.**

**A class chip may abbreviate where the width is not there.** `classShort`
(`lib/classes.js`) is the name a raider says out loud; only classes with real in-game
shorthand are in the map, and anything else keeps its full name rather than being truncated
into something nobody says. The tooltip always spells it out.

**No charts anywhere in this tab.** The per-row bars were a chart of a column of numbers
printed beside them; direction survives as the sign and the row color.

### Every death, by fight and by MOMENT (`DeathList.jsx`)

It used to render the API's list rather than the thing the list describes.

- **Fights are separated** — each gets a `grouphead` band with its name, when its first
  death landed and how many it had.
- **The clock runs to the second** (`fmt.timeS`), the resolution at which four deaths to
  one AoE stop looking like four unrelated events.
- **Deaths within `CLUSTER_S` (5s) are one MOMENT.** A wipe used to spend twenty-four rows
  saying one thing. The row says `6 players`, opens on a twisty, and is captioned with what
  killed them — `commonBlow` reads the LAST entry of each `incoming` list (a truncated list
  keeps its tail, so the killing blow survives the cap) and only speaks when the log agrees:
  one source and one ability gives both, one source and several gives the mob and a count,
  neither gives `N sources`. A moment where some deaths carry no incoming events is marked
  `*`. Five seconds is half a cast bar; ten seconds apart are two separate problems.
- **`Took` and `Healed` name their window in the column head** (`.colsub`).
- **The recap opens inside the row it belongs to** — `DeathRecap` has an `inline` dress
  (no card, no ✕, a gold spine) hosted in a full-width cell. What is expanded is indexed
  into the CURRENT list, so `DeathList` carries `key={deaths:${sel}}`.

### Read caches

- **Server** — `backend/memo.py`, a 12-entry in-process map keyed by `(epoch, key)`, used by
  `/zone-runs/{id}/report` and `/encounters/agg`. **Authorization happens BEFORE the memo on
  every request**; only the computed payload is shared, and callers copy it before adding
  fields. Every write bumps the epoch and clears the map — `rebuild_zone_runs` is the funnel,
  plus `prune_once`, which deletes events without touching run membership. A build that races
  a write is discarded. `test_memo.py` pins the invalidation.
- **Client** — `lib/api.js` keeps a read-through map of GET payloads for the session and every
  mutation clears it (`clearCache`). `peek(url)` returns a cached payload synchronously, which
  lets `ZoneRun` repaint on a click instead of blanking; an uncached selection keeps the
  previous numbers dimmed (`.wsmain.stale`) rather than showing "Loading…".

## Hand edits (schema v8, hiding v26)

Segmentation is a guess, so the list is editable — but a reparse DROPS AND RECREATES every
encounter row, so an edit keyed by encounter id would evaporate on the next backfill.
`run_edits` keys by **fingerprint** — `<started_ts>|<zone>|<name>`, the dedupe key minus
`ended_ts` — with four kinds:

| kind | meaning | written by |
|---|---|---|
| `delete` | this fight is gone, for its owner too | `POST /api/encounters/delete`, `DELETE /api/zone-runs/{id}` |
| `hide` | the owner's alone: nobody else's payload, nobody's totals | `POST /api/encounters/hide`, `POST /api/zone-runs/{id}/hide` |
| `join` | never start a run here (merge) | `POST /api/zone-runs/merge` |
| `break` | always start a run here (unmerge/split) | `POST /api/zone-runs/{id}/split` |

A reader sweeping a shared raid off their list is NOT one of these — `run_edits` is keyed by
the owner's character, and rebuilding somebody else's runs from a reader's decision is exactly
the copy of the visibility rule this avoids. It is `run_dismissals` (`docs/sharing.md`).

`rebuild_zone_runs` is the only writer of run membership, so every edit is applied by
re-running it: deletes re-stamp `encounters.deleted_ts` (a derived mark — `run_edits` is the
truth) and drop out before dedupe, and `_segment` consults breaks/joins at each boundary.

**Hide is not a soft delete.** Delete says the pull never happened; hide says it is not the
raid's business. Sharing asks who the owner sent a raid to; hiding asks whether they meant
anyone to read it, and the answer is the same for every viewer. Three consequences:

- **It still SEGMENTS.** A hidden fight stays in the stream `_segment` reads, or a night
  would split in two at a gap that exists only because somebody hid the pull spanning it.
- **It stops COUNTING**, for the owner as much as anyone. `encounter_count` is the VISIBLE
  count and `hidden_count` carries the rest; `named_count`, `success_count`, `combat_s`, the
  roster, the guild tag and the run's WINDOW are all taken over the shown fights. **The
  exception is a run with nothing shown at all**, which keeps the whole night's window and
  roster (`described = counted or members`): the fallback classifier needs `raider_count`
  until a startup relink fills `is_raid`, so blanking it could move a hidden raid across the
  default filter during an upgrade — the raid vanished off its OWNER's list and the switch
  that un-hides it became unreachable.
  **Hiding a raid must never make it hard to un-hide.**
- **It is a visibility rule beside the sharing one, never folded in.**
  `groups.VISIBLE_UNHIDDEN_RUN_IDS` wraps `VISIBLE_RUN_IDS`, which is what leaves that
  predicate the single auditable statement of the sharing rule. A run at `encounter_count` 0
  with `hidden_count > 0` leaves every list, detail and report a non-owner can reach. Per
  FIGHT the choke point is `security.visible_encounters` — "not in the payload we sent" is
  not an access rule, since ids are sequential.

Hiding is reversible from the same control (`{"hidden": false}`), which is the whole reason
it is a separate kind: un-hiding must never resurrect something deleted.

`DELETE /api/sessions/{id}` is the only thing that destroys data: derived rows, ingest
bookkeeping, frozen reports, and the raw bytes (content-addressed, so the file goes with the
last session pointing at it). It also drops the `run_edits` whose fingerprints no longer match
a surviving encounter, or re-uploading the same log would come back with every deleted fight
still hidden and nothing on screen to explain why.

## The raid list

### Three things a run row says that it does not store

All derived at READ time, for the reason the guild tag is recomputed: they are facts about
the parse, so a reparse must not leave a stale one behind.

**`live` needs the zone LINE, not just the newest encounter** (`_live_runs`). A plugin left
running all evening puts several zones in one session. The newest ENCOUNTER is the last thing
that FOUGHT, which is not where the character is — a raid that killed its boss and went to
sell leaves the raid zone lit, because standing in a city produces no encounter to move the
mark. The lines are arriving from the city, so that is where the session is. Leave the zone
and the pill drops. A session with no zone line at all keeps the encounter's answer, and zones
are compared RAW rather than by `zones.base_name` — a second lockout is a new run.

**`observed` is CONTRIBUTION, not presence** (`_observed_runs`). Standing near somebody else's
pull to gather data is a real way to use this, and the parse says nothing about the person who
took it. The test is that the logger's entity has **no damage, no heals, no wards and no
cures** anywhere in the run — all four, or the word is wrong about a healer, a defiler or a
cure bot. Damage TAKEN is deliberately out: an AoE reaches whoever is standing there.

It is drawn BESIDE the guild tag and never folded into it — the guild is a majority vote over
the ROSTER and this is a fact about the one person who logged it. On the LIST it is an eye
glyph (`IconEye`) with the sentence on its title, a footnote on one row of forty; the raid
PAGE writes it out, where it stops the whole page reading as if the logger had fought.

**`headline_named` is what a run is CALLED when its zone is not the event**
(`_headline_named`, formatted by `lib/raids.js: runLabel`). In an instance the zone IS the
event; a public zone is a place several guilds pass through. Two conditions: `zones.is_public`
is reference data, so an unknown zone is left alone, and there must be exactly ONE distinct
named (two makes it a tour of the zone). A hidden named does not count — hiding says a fight
does not count, and the run's own name is the loudest place that could contradict it.

**WHICH INFOBOX a zone page wears is itself the fact.** `refdata/zone_eras.json` had no
outdoor zones at all, and `gamewiki.parse_zone` was why, in two ways that only bite them: an
instance is `IZoneInformation` and an outdoor zone is `ZoneBox`, and the `instance` FIELD is
unmaintained (some pages fill it in, most leave it blank) while the template is consistent —
so the template answers "is this an instance" and the field only says what KIND. And **a blank
`introduced` is an ANSWER**, per the template's own comment (leave blank for an original EQ2
zone); requiring it dropped every original zone. After the re-sync public zones nearly doubled
and the raid-zone count was unchanged, which is the number that matters.

### The list itself

- **`raid_dps`** — player damage over the run's `combat_s`, from the same grouped query that
  builds the sparkline (`_spark` returns both). It replaced "Peak DPS", which ranked nights by
  their single best pull. `named_count` is still written but not trusted enough to print.
- **`shared_via`** (`groups.shared_via_for_runs`) — the VIEWER's mirror of `shares_for_runs`.
  Both carry `group_id`, because the list filters by group and a name is not a handle. A viewer
  learns nothing about who else can see a raid; `shared_with` stays owner-only.
- **Grouping follows the sort** — `SortableTable groupBy` takes an ARRAY of defs and draws
  whichever matches the active sort column.
- **Selection lives on the sticky toolbar line** (`.listtools`). A selection with nothing you
  own says "shared with you — read only" rather than an empty row of buttons.
- **Somebody else's raid opens the same pencil onto ONE button**: dismiss, or take it back.
  The wording is "dismiss", never "hide" — hiding is the owner's and reaches everybody. A
  dismissal is the only narrowing here that OUTLIVES the page, so it announces itself: an
  `N dismissed` chip lists them again (a refetch, `?dismissed=1`, because the filter is the
  server's), each wearing a badge. A raid that just stopped appearing is indistinguishable
  from a revoked share.
- **The page is titled by what is ON it** (`listTitle`) — the size toggles PARTITION the list,
  so the heading follows them.
- **One raid is edited from its own row**: a pencil in the last column opening Hide and Delete
  SIDEWAYS (`.rowedits`, a max-width reveal degrading to a cross-fade under
  `prefers-reduced-motion`), because a menu dropping out of a cell covers the raids underneath.
  Delete arms in place. A raid hidden whole wears a `hidden` badge; nobody else's list can
  carry one. The PENCIL is quiet until its row is hovered and what it opens is not
  (`.rowedit > .ebtn` vs `.rowedits .ebtn`, a child selector, deliberately).
- **Two comparisons.** `RaidCompare` (list-row numbers, opens beside the table in `.raidcmp`)
  answers "which night was bigger" from what the list already knows; its "Compare parses"
  button hands the checked raids to `/compare`, the deep answer. The old `RaidParseCompare`
  modal was folded into the page — don't rebuild it.
- **The filters are two questions.** SIZE — `Raids` and `Solo/Group` as independent toggles
  that PARTITION the list, so a third "All" button is a synonym for both on. SOURCE —
  `SourceFilter.jsx`, one menu of ticks in three sections (your characters, groups, published),
  OR'd, empty meaning everything. The page always fetches `scope=all` and narrows in the
  browser, so flipping a tick never refetches.

  Three earlier SOURCE attempts, worth not repeating: (1) All / Mine / Shared-with-me chips —
  Mine never filtered, since your own raids are always listed. (2) A Shared-with-me switch
  beside a group filter — two controls on ONE axis, and the switch silently changed what a
  group pill MEANT, because a group says "I sent it here" on your raid and "it reached me
  through here" on somebody else's. (3) The same sources as always-visible pills — honest, but
  a toolbar of proper nouns competing with the mode chips.

## The same raid, uploaded by several people (schema v18)

Everyone runs their own ACT, so a shared night arrives several times over. Within ONE
character's uploads the copies collapse by content, but two people's logs are not the same
bytes — different subjects, different vantage points, different fights heard — so both parses
are real and neither is a copy. **Nothing here merges or deletes anything.** It answers two
questions: which rows are the same night, and which to open first.

`backend/raidmatch.py` decides the first, at READ time over the runs a viewer can already see.
Materialising it would be a fact about somebody else's account and would go stale the moment a
share was revoked.

- **zone** — equal, NULL-safe. An "Unknown zone" run can still match, but only on the roster.
- **time** — windows overlap, ± `CLOCK_SKEW_S` (120s). The epoch prefix comes off the raider's
  own machine, so two clocks agree to within seconds; the slack is not a fuzzy-match knob.
- **roster** — enough of the same people. This is the rule that says NO: two guilds in the same
  instance at the same hour pass the first two and share nobody. `ROSTER_AGREEMENT` (0.34 of
  the smaller roster, minimum 2) sits well under a real pair (~1.0) and well over the overlap a
  passing group leaves.

That needed the roster itself, so **v18 adds `zone_runs.roster_json`** (`_raider_count` became
`_roster`; `raider_count` is its length). Existing rows stay NULL until the startup relink
sweep rewrites them; a missing roster costs the match its cross-check, never a wrong merge.

**Precedence is two decisions, in two places, on purpose.** `GET /api/zone-runs` stamps
`raid_key` (the cluster's lowest run id, a handle meaningless outside the payload), `parses`
and `primary` — the site's pick, viewer-independent, so two people discussing a raid read the
same numbers (`_score` is coverage: fights, then combat time, then roster size, tie-broken to
the first upload). **Your own parse wins**, and that is the browser's to apply (`Home.jsx`
`chooseParse`), because it depends on who is looking and the payload is shared.

The list draws one row per raid with a **`Character`** select naming the uploaders; the raid
page carries the same control and switching NAVIGATES, because the fights and the vantage point
are all theirs. `alternates` is filtered through `VISIBLE_RUN_IDS` — the switch re-sorts raids
the viewer was always allowed to open, it is not a directory. Clustering happens AFTER the
source/size filters.

**One `Character` column, not two.** "Which of my characters logged this" and "whose upload am
I reading" are the same question with the same answer. It is the normal text colour on the
list (gold read as a link out in a column of names) and keeps its border and arrow to stay a
control; the raid page's copy is gold, because there it sits in a head.

## The encounter APIs

### `GET /api/encounters/timeline?ids=…&bucket=auto`

Per-actor damage / heals / damage-taken bucketed over time. The clock is the **concatenated**
combat clock — between-fight gaps removed — so a multi-fight selection reads continuously and
`duration_s` still equals the summed `duration_s` the tables divide by. `segments[]` carries
fight boundaries, `markers[]` the deaths. `auto` picks the finest bucket from
`[1,2,5,10,15,30,60]` staying under 240 columns. Credit follows `statsroll`'s pet rollup, and
series key by `name|kind` to match `/agg`. Pruned sessions contribute nothing.

### `GET /api/encounters/deaths?ids=…&window=12`

One entry per player death with the incoming hits and healing received in the `window` seconds
before it (`t` relative and negative, far edge inclusive, clamped 3–60s, capped at 40 entries
per list with `_truncated`). Same death/kill rules as `statsroll`, including the logger's
bare-name pet.

### `GET /api/encounters/aoes?ids=…` — the AoE tab

One row per (enemy source, ability) with every detected cast attached
(`pipeline/aoes.py`). The log never says "this was an AoE", so the definition is behavioural:
**a second in which ONE enemy ability touched at least `MIN_TARGETS` (5) players is a cast.**
Everything that ability does for the next few seconds belongs to the same cast; the merge
threshold is `max(6s, 0.4 × the reported timer)`. Both damage and *ability-named* avoids count;
a bare avoid with no ability is a melee swing and never enters.

Three timers sit side by side and the gaps between them are the point:

- **measured** — what every raid on this site has seen this (mob, ability) do, from clean
  cycles only (`pipeline/aoelearn.py`, `aoe_cycles`). It is what the countdown counts with.
  Absent until 6 agreeing intervals across 2 fights; parenthesised while short of that.
- **reported** — ACT's spell-timer list, shipped as `refdata/act_spell_timers.json`
  (name/duration/category only). Joined by ability NAME, which works because ACT keys off the
  same log string.
- **observed** — the shortest interval between two casts that REPEATS, within one fight (the
  wait between pulls is a raid taking a break). Clean cycles when there are any, otherwise the
  swiped ones, flagged `observed_swiped`.

A fourth column, **Swiped**, is what makes the other three trustworthy: whether a reuse debuff
moves this ability, measured per (mob, ability) rather than taken from the tooltip
(`docs/live.md`).

**Shortest-repeating rather than mean or median, because of how the measurement fails**: an AoE
that never reached five people is a cast we cannot see, and a missed cast makes one gap look
like two — it can only ever make a gap LONGER. `observed_agree` says how many intervals agreed;
`missed_hint` counts the gaps that look like multiples.

**Coverage** is who was not hit: `avoided` plus `absorbed` (a zero-damage hit with `F_ZERO`),
minus anyone the same cast also hit.

GOTCHA: entities are keyed by NAME, so several trash mobs sharing a name read as one mob casting
far too often. `instances_hint` flags the giveaway — an observed timer that is a clean fraction
of the reported one, from a source that is not a named — rather than reporting ACT as wrong.

### `GET /api/encounters/class-stats?ids=…` — the Class tab

The stats only one class can answer, which as table columns would be blank for twenty-five
classes out of twenty-six. The selection is split by class instead and each class owns a panel.

`pipeline/classstats.py` is a **registry**: a metric is one `@register(...)`-decorated function
declaring its columns and returning rows. The endpoint enumerates the registry, `ClassPanel.jsx`
formats by column UNIT (`text|num|pct|secs|clock|rate`), and nothing else changes.

- **`blurb` is required**, and it carries the LIMIT, not the pitch. These stats live at the edge
  of what a log can prove, and the caveat belongs beside the number.
- **A class with no metrics is still a section** (`Coming soon.`) — not an error state.
- **Class resolution is not redone here** — the actor list comes from the memoized `/agg`
  payload the Damage tab already fetched, so there is one answer per page.
- **`class_source == 'unidentified'` is not a class we failed to guess** — it is the refine pass
  saying nothing proved a person was behind the name, which usually means a summoned pet. Those
  names appear in neither the class sections nor the unmatched list.
- **A metric that raises is isolated** (`status: "error"`) and the rest of the tab renders.
- **`needs_events=True` on a pruned selection reports that** rather than returning zeroes.

`Ctx.events(types)` is the one expensive door, cached per type-set for the request.
`test_classstats.py` covers the pipe with stub metrics; `test_classmetrics.py` the real ones.

### Curated buff lines (`parser/buffs.py`)

A beneficial buff is nearly invisible in an EQ2 log: no damage, no heal, no name, no fade line.
A handful of abilities print flavor twice — a cast line naming the caster and a landing line
naming the target — and **both are written for everyone in chat range**. That is the only place
in the parser where ANOTHER player's cast is visible, which makes buff uptime computable from
any raider's upload rather than only the buffer's.

**Curated, not generic.** The third-person grammar exists, but the flavor names an ability LINE
rather than an ability, and the first-person form is not always a spell. A line earns an entry
when its flavor identifies ONE ability and its landing names the target; each entry carries a
`token` substring so the regexes never run on lines that cannot match.

Two event types: `buff_cast` (src = caster) and `buff` (tgt = recipient). `_pair_buffs` gives a
landing its caster — the two lines are written independently, so the only link is time. **When
two casters fall in one window the landing keeps NO source**, because picking one would invent
attribution that reads as measured.

**Blast radius is deliberately nil.** `statsroll` ignores both types and `segment_events` only
opens or extends a segment on `damage`/`avoid`, so chain-casting between pulls cannot merge two
fights, no ability row grows, no class vote changes, and ACT parity is untouched. The Class tab
alone reads them.

**Uptime metrics** (`pipeline/classmetrics/`) compute coverage as the UNION of the applications'
windows, not their sum — an early refresh extends a buff, it does not add a second — clipped to
each fight and cut short by the target's death. Applications are read with a one-duration
lookback (`Ctx.events_around`), bounded to each selected fight's own run-up rather than opened
to the session, since authorization is per encounter. **Every count is a FLOOR** — a cast out of
chat range is not logged at all — and the `blurb` says so.

**A metric with no line at all is possible via its PROC.** Where a buff prints nothing, the only
evidence is an ability only that buff can cast, which proves its caster had the buff that second.
Both constants are measured rather than assumed: the window length from the longest proven run
across the reference raids (which rules out reading Census's base row as the answer), and the
join gap as the largest value that does not start inventing coverage — **a generous tolerance
does not find more coverage, it manufactures overlap.** `Windows` counts CASTS, not stretches,
because a caster who pauses twice inside one window has three stretches and one buff.
**Double-covered** measures buff paid for twice: a covered stretch longer than one window took
more than one cast. It is quiet in the current era and becomes the direct measure of a second
buffer's waste when the buff goes raid-wide, so there is no era switch.

### `GET /api/encounters/loot?ids=…` — the Loot tab

One row per item off one chest: the fight it belonged to, the mob whose chest it was, the raider
who won or took it, and the item's card. The tab is LAST — everything before it is the parse.

**Chest loot only, and that is the feature.** The log writes chest drops and corpse drops with
the same verbs, and only the source clause tells them apart. A corpse gives shards, body parts
and vendor coin — hundreds of lines a night that bury the eight items a raid remembers. So the
source clause is REQUIRED and must name one of the four chest tiers; a line with no source at
all is dropped, because "probably a chest" is not evidence.

**Loot is not an event, and must never become one.** It is written beside the parse into
`loot_drops`, never into `events`. A looter is a bare NAME on a line, and pushing it through
`EntityResolver` would mint an entity — putting somebody who walked past the chest into the
fight's roster, its class vote and its ACT parity. `pipeline/loot.py` resolves nothing, rolls up
nothing and segments nothing; `test_loot.py` pins that a loot-only name never reaches `/agg`.

**Two lines say what happened and only one knows where it came from.** The lotto/loot line names
the chest and the mob; the confirmation line names no chest, so it can never CREATE a drop — it
is matched back by (item, looter) and enriches one. A win nobody confirmed is kept and flagged.

**The rarity is Census's, not the log's** — the confirmation line only prints for people standing
near you, so reading rarity off it would leave most of a night blank.

**A chest belongs to the fight its MOB names, not to the clock** — a ladder, most exact first,
because a chest is opened after the pull and sometimes after the next one has started:

1. the fight was NAMED for that mob (`encounters.name`);
2. that mob was IN the fight, from the events — the case a chain pull needs;
3. the last fight before it within `NEAREST_S` (900s), marked `attribution: 'nearest'` so the
   table says `approx` rather than claiming it.

Rung 2 reads `events`, which pruning removes, so an old session falls to rung 3 or to nothing
rather than to a WRONG fight. A drop with no fight keeps `encounter_id` NULL and is returned when
it falls inside the selection's own span.

**Who else wanted it** — hovering the looter shows the contest, and the card says which of two
very different records it is:

- **The lotto** is the game's own and prints the whole thing against the item BY NAME, so it
  cannot be wrong. **Blocks INTERLEAVE**, so they are keyed by item, never by "the last block we
  saw". Resolution order is NEED before GREED and highest first inside each. A choice with no
  number is kept as a roll with no value: they wanted it, which is most of what a loot list is
  for.
- **`/random` dice** are a raid running loot by hand. Nothing in a dice line says WHICH item, so
  attribution is a ladder: an announcement that linked that exact item (`announced`), else the
  nearest burst (`nearby`, a proximity claim the panel and a dotted underline both admit to). The
  window is TWO-SIDED, and **dice are never mixed in beside a lotto block**.
- GOTCHA: the dice line's `Random: ` prefix is the channel tag, not the roller.
- Two logs of one night can disagree completely — the roll list is a property of how that raid ran
  loot and of whose client was listening.

**History came from the archive, not a re-upload** (`tools/backfill_loot.py`). Deliberately **not**
a `PARSE_VERSION` bump: loot changes no stat, segment, roster or rollup. `clear_derived` still
drops loot with the encounters it points at and the parse writes it back.

### Items as reference data (`backend/items.py`, schema v32)

The display record for an item a log named. Census answers what it IS, the wiki what it LOOKS
like — one row and one file serve every account forever, because this is a fact about the game.

**The log's item id IS the Census item id**, written signed — verified against Census's own
`gamelink`. So this is an exact lookup and **none of the reasons gear procs are closed as wontfix
apply** (`docs/census-abilities.md`). There is nothing to search for.

**EQ2i hosts the game's icons as `File:Item <iconid>.png`** — Census hands out an `iconid` and no
image. One file per ICON, not per item, served from `/api/items/icon/<iconid>.png` with no
visibility check, because there is no raid behind it.

- GOTCHA: **`format=original` is not optional** — the wikia CDN re-encodes to WebP on the way out,
  so a URL ending `.png` answers with WebP whatever `Accept` asks. Bytes are then verified by
  magic number rather than trusted.
- GOTCHA: an item page is often a **disambiguation**, so it resolves to the version the wiki lists
  first. `gamewiki.fetch_wikitext` cannot be reused: it asks for `redirects=1` and discards the
  mapping, and here the mapping IS the answer.

**The hover card is a REPLICA of EQ2i's item box, never a screenshot of it and never its HTML**
(`stat_block()`). EQ2i builds that box from the same Census record we already hold, so the card is
our data in the wiki's clothes: the `.ew-*`/`.xqc-*` class names and colours in `base.css` are
copied from `MediaWiki:ExamineWindow.css`, and the content comes from `items.stats_json`. That
beats screenshotting (crisp at any zoom, selectable, one cached row) and embedding rendered HTML
(no third-party markup, no sanitiser, and it works for items with no wiki page).

- **Green is everything flat, blue is everything that modifies a property**, the wiki's own split.
  Green reads attributes → resistances → skills, EQ2i's order and NOT by size. Percentages are the
  blue block only, less DPS and Haste.
- Census's `ac` entries are per resist school; matching values read as one `Resistances` line and
  disagreeing ones are listed rather than summed into a wrong number. **A modifier type the card
  has no place for is DROPPED, never guessed at.**
- **Census's `all` IS Ability Modifier**, not "all stats" — the wiki settles it. Ability Mod is one
  of the two stats that matter on this server, and "All" hid it in plain sight.
- **A stat this server does not have yet is not shown** (`ERA_HIDDEN`). Census describes the LIVE
  item, so a TLE raider was shown a stat their character cannot use — worse than nothing, because
  it invites comparing two items on a stat neither grants.
- **A weapon leads with Damage and Delay**, using the BASE range and the rating (EQ2i's choice).
  Census carries the mastery range too, which is a different claim.
- **The item's own proc comes from the WIKI, not Census** — `effectlist` plus the
  asterisk-indented `effectdesc` bullets, whose DEPTH is kept because the first level is the
  condition and the second is what it does. This is the FORWARD direction and costs nothing, since
  the page is already in hand for the wiki link. Disambigs resolve to a version page and the effect
  lives there, so those are fetched in a second batched pass.
- The adornment-slot gems are cached like the icons — a fixed set from
  `/api/items/adorn/<colour>.png`, format decided by MAGIC NUMBER because some are `.png` and some
  `.jpg`.
- Adornment items use their own examine shape: Census supplies colour, legal equipment slots and
  complete set bonuses; the wiki supplies the set name. Turquoise prints the RoK-or-earlier
  predicate that belongs to that slot colour even though Census leaves `placementflag_list` empty.
- Legacy set armour with a turquoise slot resolves its same-slot `<set>: <slot>` companion from
  Census at refresh time and stores that adornment inside the armour's card. The popup remains
  self-contained: ordinary armour stats first, then the included adornment and all of its bonuses.
- One line is ours and not EQ2i's: **Dropped by**, the mob whose chest it was.
- Built at RESOLVE time and stored, so the card is a read. Widening it means
  `backfill_loot.py --refresh-census` (and `--refresh-wiki` for the proc).
- Items with genuinely no equipment stats get no card rather than an empty one.
- **It does not theme** — an examine window is black in a light client too.
- `position: fixed` in `document.body`, placed from the name's rect, for the reason `.pickermenu`
  is: the table scrolls sideways inside `.tablewrap` and a card parented to a cell is clipped.
  `pointer-events: none`.
- Its height is **MEASURED, not guessed** — these cards are not one size, so a fixed threshold cut
  the tall ones off. Below if it fits, above if it fits there, else pinned to the top edge with a
  scrollable cap (and only THAT card takes the pointer back). It renders hidden for the frame it
  is measured in.
- CAVEAT: Census returns the item as it stands on LIVE and the wiki holds much the same numbers,
  so there is no era-correct source that differs.

**Nothing else is fetched on a page load.** Resolution runs after a parse (outside the write
transaction, like the roster sync — failing costs a raid its pictures, never its parse) and in
`backfill_loot.py --resolve`. `items.network_allowed()` reads the same `CENSUS_AUTO_REFRESH`
switch conftest turns off.

## Frontend conventions

- **Light mode is a neutral application UI, not parchment** (`styles/tokens.css`). Its canvas and
  structural surfaces stay white/cool-grey; interaction uses the steel-blue `--gold-*` ramp and
  only the wordmark keeps literal gold through `--brand`. Light-mode body copy uses the system
  sans face so dense lists read as software rather than a document. Dark mode keeps the original
  gold/Cinzel/Spectral identity. The historical token names remain the stable component API.
- `lib/classes.js` owns identity. **Color is assigned by EQ2 archetype
  (fighter/priest/mage/scout), not by class** — the palette validator says four hues separate
  cleanly and twenty-six cannot, so the family color carries identity in stripes and legends while
  the per-class tint is decoration on a chip that always spells the class out. Role
  (tank/healer/dps/utility, mirroring `coach.descriptive.ARCHETYPES`) drives the filter chips.
  Chart series use their own 8-color validated palette in fixed order, since two raiders of one
  class would otherwise draw the same line.
- **Selection sums** (`SelectionBar`) — checking rows adds them up in a sticky footer instead of
  hijacking the panel; comparing is a deliberate second click.
- **Click reads, tick compares** (`ZoneRun.focusActor` vs `toggleCmp`). A click REPLACES what the
  drilldown shows; only the checkbox adds a second parse. They were one gesture for a while, and
  reading three names deep left three quarter-width parses side by side when what was wanted was
  the third. Clicking the raider already open closes the panel; mob/pet rows have no checkbox.
- **The drilldown opens on the tab you were reading** — `PANEL_KIND` translates the page tab and
  hands it to `ActorPanel`/`ComparePanel` as their starting tab. Only page tabs with a per-ability
  view map; the panel's own tabs win until the page tab moves again.
- **A comparison is a ROW, and it scrolls sideways** (`.cmpraiders`). The raider boxes never wrap.
  Wrapping read as a grid rather than parses lined up, and it doubled the panel column's height —
  which, with the rail and the raid table stacked in the other column, pushed the raid table down
  the page. `.workspace.withpanel` pins `grid-template-rows: auto 1fr` so a tall panel grows the
  table's row, never the rail's.
- **Rank coloring is PLACEMENT within the row's role** (`stats.rankScale`/`rankColor`/`rankTitle`
  through `SortableTable`'s `cellStyle`/`cellTitle`), with the standing spelled out in the tooltip.

  It has been wrong twice. Hard terciles called the bottom third red even when the whole field was
  within a point of each other. Distance-from-median fixed that and was worse: the size of the gap
  and the size of the group both moved the color, and each row was measured against ITS OWN role's
  median, so one column carried up to four yardsticks at once — a red number could sit two rows
  above a green one five times smaller with nothing on screen saying why. Position is the one thing
  a reader can verify against the column they are already looking at.

  **A row with no role gets no color**, and neither does a group under `MIN_PEERS`. The old
  fallback — borrow the whole raid's median — is what made the mixing invisible.
- **Decomposition** (`stats.decompose`) — DPS split into activity × hit size × crit × alive%, each
  against the best peer, naming the biggest gap: the difference between "you're 20% behind" and
  "you cast 30% less".
- **A parse table is FROZEN: row one and column one hold still** (`SortableTable`'s `frozen` +
  `useFrozen`, `.tablewrap.frozen`). Every parse surface is frozen. A checkable table pins the
  checkbox WITH the name at a measured offset (`--fzleft`), because the box is part of the name
  column's job. Two things JS measures: the divider draws only once the table is actually scrolled
  sideways (`.xscrolled`), and the pinned cells are **opaque** (`--frozen-bg`/`--frozen-head`),
  because a translucent name column with figures sliding under it is worse than no freeze. Row
  tints go back on top as a gradient LAYER, since `background` can only be tinted or solid.
- **A long ability name shortens only when the table cannot fit** (`.overflowing` + `.abname`) —
  never because a column is narrow, never the badges/⚙/expander beside it, full name on `title`.
  **Un-shortening is asymmetric on purpose**: asking "does it fit?" of a table that fits BECAUSE it
  is clamped oscillates, so the width it wanted before the clamp is remembered.
- **A default-hidden column is a BASELINE, not a first guess** (`SortableTable`, `localStorage`
  under `eq2adv:cols:<prefsKey>`). Stored prefs are TWO lists — `hidden` (what the reader turned
  off) and `shown` (what they turned back on) — with `defaultHidden` underneath both. One list
  could not express that: it REPLACED `defaultHidden` wholesale, so the first touch of the Columns
  menu evaporated a comparison table's starting layout, and any default-hidden column added later
  turned itself on for everyone who had ever dragged a header. The reset says **Reset to
  defaults** — order and visibility both.
- **Each parse tab offers the OTHER tab's rate, folded away** — `HPS` default-hidden on Damage,
  `DPS` on Healing (`tabHidden`), each beside the rate it pairs with. Keyed `zonerun:<tab>`, per
  browser and not per run.

## The sibling TLE sites (`App.jsx`, `SITES`)

Two links out to wikQ2 and eq2lexicon in the slack between the nav and the tools. The intent is
one door. **They are drawn as plaques, not tabs** — everything in `nav` is somewhere in THIS app.

**wikQ2 opens INSIDE the shell; eq2lexicon cannot, and no code here can change that.** It answers
every request with `X-Frame-Options: DENY`, browser-enforced against every origin. The only ways
around it are their cooperation (a `frame-ancestors` header) or reverse-proxying their site through
this backend — **the proxy is REJECTED**: it would rehost somebody else's app under our domain
without permission and carry our users' logins to their site through our server. So that link opens
away and wears the arrow that admits it. If they add the header, deleting `away: true` from its
`SITES` entry is the whole change.

**The framed tab is HIDDEN, never unmounted** (`.siteframe.away` is `display:none`). A hidden
iframe keeps its document alive, so the search box, results and scroll position survive. Unmounting
it — or merely letting React MOVE it in the tree — reloads it and loses their place, which is why
the frame is a sibling of `<main>` and NOT the `/wiki` route's element. The route exists (so the
URL and back button work) and renders `null`. `wikiOpened` latches on first visit, so a raider who
never presses wikQ2 never loads it.

**Across a hard reload there is nothing to restore, and that is wikQ2's to fix.** A cross-origin
frame will not tell the parent what page it is on, and wikQ2 keeps its state in React without
writing the query to its URL — so reloading it in a plain tab loses your place too.

**`--topnav-h` is measured, not a constant** (`ResizeObserver` on the header). The frame is pinned
under the bar rather than laid out after it, because inside normal flow a percentage height has
nothing to resolve against and an iframe collapses to nothing. The bar wraps under 900px.

**The framed tab follows this site's theme, and the theme travels TWICE.** The initial value rides
in on the frame's URL (`?theme=`), because wikQ2's pre-paint script reads it before drawing. That
src is then FROZEN (`setWikiSrc((s) => s ?? …)`): `src` is a prop, so re-rendering a changed one
reloads the frame and throws away the place this tab exists to keep. Every later toggle is a
`postMessage` aimed at `WIKI.origin` and **never `'*'`** — a wildcard hands the theme, and the fact
that this frame exists, to whatever window happened to load there.
