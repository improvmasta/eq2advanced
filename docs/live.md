# Live ingest, the raid dashboard, overlays and replay

Index: `ARCHITECTURE.md`.

## The ingest contract (frozen)

`GET /api/ingest/hello`, `POST /api/ingest/batch`, `POST /api/ingest/backfill/done`.
Auth is `Authorization: Bearer <device_token>` only. A batch is gzip (or plain)
JSON `{batch_id, mode: live|backfill, lines: [verbatim lines]}` →
`{accepted, duplicates, session_id}`. **That is the whole surface a device token
reaches** — it sends logs and does nothing else (`docs/sharing.md`).
`backend/tools/simulate_live.py` is the reference client;
`improvmasta/eq2advanced-act` implements it for real and the two must stay in step.

- **Batch idempotency** — the response is stored per `(token, batch_id)` in
  `ingest_batches`; a resend replays it. One batch in flight per token (429).
- **Line dedupe** — `ingest_lines` keys on sha256 of
  `(occurrence-ordinal-within-batch, line)`, so identical legit lines both count
  and a re-upload overlap drops cleanly. **Corollary: batches must be cut on
  log-second boundaries** so a second never splits across batches.
- **Incremental finalization is a view, not the record.** A per-session in-memory
  tail (`pipeline/live.py`) re-segments on each batch and flushes any encounter
  that can no longer change (a later segment exists, or
  `CLOSE_S = GAP_S + TRAIL_GRACE_S` = 17s of log time passed). At close (`done`, or
  30-minute staleness) the session is **rebuilt from raw through `parse_session`**,
  the exact bulk path, so a finished live session is provably identical to
  uploading the file (`test_golden_equivalence`). Encounter ids change at rebuild,
  which is why the Live page refetches on `status: ready`.
- **Staleness needs a reaper, not just a check** (`live.reap_idle_live_sessions`,
  from `main.lifespan` at startup and every 5 min). Evaluated only on the next
  batch, an abandoned session sits at `receiving` forever — the raid page keeps
  saying Live, and since closing is what rebuilds from raw it is also out of reach
  of the `PARSE_VERSION` sweep (which walks `ready`/`parsing` only). The startup
  pass runs BEFORE `_reparse_stale` so it is reparsed once.
- **Restart-safe by construction** — the in-memory tail is disposable; raw chunks
  and `ingest_lines` survive.
- **SSE** — `GET /api/sessions/{id}/stream` (cookie auth) pushes `encounter` cards,
  `status` and `partial` views of the open fight, closing at ready/error. It is
  WOKEN rather than polled, with `STREAM_POLL_S` (1.5s) as the fallback tick.
- GOTCHA `process_batch(token_row, char, …)`: `token_row` is an ACCOUNT token.

### Latency: the screen sees a hit in ~1s

Plugin cadence 0.5s + `SNAPSHOT_MIN_S` 0.25s + a pushed SSE (`livebus`) instead of
a 1.5s poll. **The remaining floor is the plugin holding the newest log second**,
which is architectural: a line cannot be sent until its second is complete, and
going below it means re-keying the dedupe to `(log second, ordinal)` so a second
may arrive in pieces — a change to the frozen contract.

`IdleFlushSeconds` (1.5s) governs the END of a pull: mid-fight the next second
releases the held one, but when the log goes quiet the last second sits in the
plugin — exactly when everybody is reading the meter. It stays under the 7s gap.

**Tuning fields are saved verbatim on the first batch**, so a new default reaches
nobody who already has the plugin. `Settings.TuningGeneration` lets an older
config take this build's tuning once, leaving hand-tuned values alone.

### Waking a stream instead of polling it (`pipeline/livebus.py`)

A per-session doorbell: `_publish_snapshot` rings it on the ingest thread and the
SSE loops park on it. **Subscribe around the whole read-and-yield body**, not
around the sleep, or a snapshot published mid-read is lost — the failure that only
appears under raid load. **The timeout stays** as the fallback that keeps
`mark_watched` alive and survives a lost publish. The overlay re-resolves its
session every pass so it subscribes per pass; `subscribe(None)` is a plain
sleeper. Process-local, rung with `call_soon_threadsafe` because ingest is a sync
handler on a worker thread.

### The ACT plugin

`backend/refdata/plugin/EQ2Advanced.dll` is committed (private source repo,
expiring Actions artifacts) and served by `routers/plugin_api.py` as a **ZIP** —
browsers block a bare `.dll`, and the install steps say Unblock BEFORE extracting,
because Explorer copies the mark-of-the-web onto what it unpacks. Refresh with
`bash scripts/update-plugin.sh`.

**An update pill appears only for an account whose OWN uploads say it is behind:**
`device_tokens.client_version` (v30) is written from the uploader's User-Agent
(`auth.client_version` is strict — a curl or a browser reads as "no idea"), and is
compared against `backend/refdata/plugin/VERSION`, which is committed as a fact
because a .NET assembly version cannot be read back without a PE parser. **Never
heard from is not behind**, versions compare as NUMBERS (`version_tuple`), and the
newest version any pairing reported wins, so a rarely-used second machine does not
nag forever.

## The raid dashboard (`/live`)

Second-monitor page: the night's fights in the rail left, the pull happening now in
the middle, notes and screenshots right. It picks the raid up on its own.

The meter is ACT-shaped — a class-coloured bar behind every row — over a scrolling
raid DPS/HPS chart with AoE countdowns above it. **The metric chips are SWITCHES,
not tabs**: Damage, Healing and Tank can all be on at once, each its own stack,
because a raid leader wants the tank's incoming NEXT TO the healers' output.
Screenshots PASTE, because mid-raid nobody is naming a file.

### The live meter is a VIEW and writes nothing

`_flush` already computes the open segment and drops it; the snapshot
(`pipeline/livemeter.py`) is built from those events, handed to SSE as a `partial`,
and stored nowhere — no DB writes, no entity resolution, no encounter row. That is
what keeps `test_golden_equivalence` true. Consequences are stated in the payload
rather than hidden: the fight's name is `provisional_*` until it commits, and
credit is by NAME because resolution is the expensive half of a flush.

**Its arithmetic deliberately matches `roll_encounter`** — same self-damage
exclusion, same elapsed denominator, same HP-deficit overheal — or the meter would
visibly disagree with itself thirty seconds later.

Three gates decide whether a snapshot is built, all about not showing a raid that
is not happening: **nobody watching, nothing built** (`mark_watched`);
**`mode=backfill` is history** by the plugin's own word; and **`LIVE_LAG_S`**
catches log time far behind the clock, which is a replay. The SSE generator reads
it as an RCU-style pointer swap — the producer builds a fresh dict under the
session lock and assigns; readers take the reference and treat it as frozen.

Cost is one pure-Python pass per batch (~65ms on the archive's biggest fight), with
`SNAPSHOT_MIN_S` so a fast client cannot spin on it.

### Who a name is, without resolving anything (`livemeter.Names`)

Grammar alone is not enough, and all four failures were fixed with knowledge the
app already had rather than with entity resolution:

- **A one-word boss reads as a raider**, so it never enters the pool the fight is
  named from and the title goes to the multi-word adds. `refine_known_mobs` is a
  pure function over parsed events, so the live path runs it too.
- **A possessive pet reads as a mob**, so damage into it named the fight after
  somebody's dumbfire. Targets decompose exactly as
  `EntityResolver.resolve_target` does, and a possessive pet stays its own
  combatant on the taken side (`statsroll.taken_key`).
- **A bare dumbfire reads as a raider** — EQ2 never prints an owner for it.
- **`YOU` is not a name**, so the logger was two rows and a raid-wide AoE missed
  the ≥5-raider anchor by exactly one.

The knowledge is `live.snapshot_context`, read once when `LiveState` is created
(four indexed reads): classes, mobs, players and bare-named pets, all settled
output of finished parses. That is what makes the FIRST second of a pull as
accurate as the fortieth.

Two orderings are load-bearing: **Census wins on class and an earlier parse fills
the gaps** (order of authority unchanged, `docs/parser.md`), and **a seeded mob
outranks the roster, with only Census vetoing it** — the roster is evidence about
what one segment can INFER, not about the seed. Without that, refine's kill-victim
rule reads the raiders a boss just killed as mobs and the meter empties out.

**A stranger's class is asked of Census DURING the pull** (`_queue_roster_lookup`).
`snapshot_context` holds only what this app has parsed, so beside another guild's
raid every bar was uncoloured until the close-time rebuild hours later — and on
this screen the class *is* the bars.

- **Ask about the built snapshot's unclassed player rows** (`_unclassed`), not the
  batch's names: the snapshot has already ruled out mobs and pets, resolved `YOU`,
  and cut to `MAX_ACTORS`. It rides the existing gate — no dashboard, no lookup.
- **Never on the ingest thread.** `process_batch` holds `state.lock` for a whole
  batch, so names go to a queue and one process-wide worker; the lock is taken only
  to write answers back, and `get_db()` is thread-local.
- **A name is asked about once a session**, marked when QUEUED, not when answered.
- **Merge the CACHE, not the call's report** — `roster.resolve` asks about stale
  names only, so an already-resolved name comes back `found: 0`. Reading the report
  is how a second meter sits uncoloured beside one that has the answer on disk.
- **A failure is not an answer** — retried behind `LIVE_ROSTER_RETRY_S`.
- **A found name is proof of PERSONHOOD**, so it joins `known_players`, or refine's
  kill-victim rule reads a wipe's dead raiders as mobs.

Nothing publishes from the worker; the class lands on the next snapshot.

### The fight rail

**The pull in progress is the rail's LAST ROW, never a button beside it**
(`EncounterTree`'s `live` prop). As a button it vanished the moment you clicked
back to an earlier fight, because a fight only becomes a row when the writer
commits it. It carries no checkbox — the numbers under it are a view rebuilt every
couple of seconds, and a combined-stats set holding one would change under the
reader.

`Live.jsx` HOLDS the just-ended pull on that row as `saving` until its `encounter`
arrives, cleared by the encounter count going up. The `HOLD_MS` cap is load-bearing:
**a segment the raid never ENGAGED never commits at all** (`_ENGAGE_KINDS`,
`docs/parser.md`), so without it the rail would say `saving` until the next pull.

**Between pulls that row is an ELLIPSIS** — no name, no clock, no dot. Its only
remaining job is to be where the next fight appears and the click back to live.

**Clearing the rail is about the SCREEN, never the raid** (`Live.jsx: cleared`,
`EncounterTree`'s `onClear`). One ACT process is a whole evening, so by raid time
the rail already holds the afternoon.

- A cleared fight is still parsed, still on `/zones/:id`, still shared.
- So rows get a single **✕ and no confirmation**, where the raid page offers Hide,
  Delete and a confirm. Hiding is a fact about the raid; this is about one screen.
- **Edit and Done are one button in one spot**, docked left of the fight count
  (`countAction`, `.railtail`) — the count is what the button is about. It wears
  `.ebtn`, not gold: gold means the raid changed.
- Kept **per SESSION in localStorage**, so it survives a reload and cannot leak
  onto tomorrow's.
- **Only the RAIL reads the filtered list.** `lastEnc` and the commit count stay on
  the full one, or the page would think a fight it saw is still `saving`. Clearing
  also drops the selection back to live.

### Clocks, motion and layout

**A figure steps, a length slides, a clock ticks** (`lib/smooth.js`), all off ONE
20Hz `requestAnimationFrame` loop so React batches each tick into one render pass,
with the moving part always a LEAF.

- **DIGITS STEP.** A rate counting up cannot be read while it does it, and a pull's
  opening seconds become a slot machine. **Tweened figures were built and REMOVED —
  do not re-add them.** The cure for stale-feeling numbers is a shorter
  `CadenceSeconds`, never an animation over the gap.
- **LENGTHS SLIDE** (`Smooth.jsx: Bar`), cutting past `SNAP_FRACTION` — a rank
  changing hands is not a drift.
- **THE AoE DRAIN BAR IS NOT ON THAT LOOP** and must not be put back on it (below).

Nothing here invents data: a slide only travels between two numbers the server
sent.

**The elapsed clock counts in the browser and its correction is asymmetric** — take
a payload ahead of us, hold against latency behind us, restart only past `SNAP_S`
(a different fight) — so it never repeats or skips a second. The AoE countdowns
take the same correction through `useLogClock`, since a flat re-anchor on every
payload is a sawtooth.

**A fight ENDS at `GAP_S` (7s, where ACT calls it) and only COMMITS at `CLOSE_S`
(17s)**, because a late kill or death line may still join the segment. The payload's
`ended` is the difference and it is what stops the clock, the pulse and the rail
row. It comes from the LOG clock (`now_ts`: the newest line sent, or the replay
cursor) and **never the wall clock**, which a backfilling log would make a liar.
**A clock stops when its fight does** — a frozen parse counting off seconds is the
one thing here that would be actively wrong. `/act end` is the exception: a segment
carrying `Segment.ended_by_cmd` commits at the next flush.

**Live `elapsed_s` is damage-to-damage, exactly like `Segment.end_ts`**, so the
meter and the card it becomes are the same length. Trailing heals are still
counted; they just do not extend the fight.

**The meter is exempt from `prefers-reduced-motion`, deliberately.** A bar's length
and a rate's digits are the READING, arriving in steps only because the uploader
batches; snapping them is a meter lying about when the fight changed. The pulse dot
is decoration and still goes.

**A row is a RATE, never a total**, and the tail folds at 12 rows (`SortableTable`'s
`fold`, which cuts AFTER the sort). The stream overlay keeps a hard `max_rows`
instead — nobody watching a stream can click. **Max hit is the one exception**
(`max_hit`/`max_heal`, by SOURCE): a nuke and a DoT with the same DPS are not the
same thing.

**The live meter sizes itself and the middle column keeps the slack.** The column is
sized for the PARSE a finished fight becomes; the pull in progress is a name, a bar
and a rate, and stretched across it read as text with an empty bar in between.
`--live-w` is the PANEL (`fit-content`, ending at the headline's `deaths` stat) and
`--live-tbl-w` (360px) is the BARS AND COUNTDOWNS — separators included, or a
`border-top` draws the panel wider than its contents. A countdown row sheds words by
**container query** (`@container aoepanel`), never a second `compact` flag, so
widening `--live-tbl-w` earns them back on its own.

**The chart's peak is a headline figure, not a caption on the chart** — the SMOOTHED
peak matching the drawn line, in the `.lhnums` row.

### A finished fight on the dashboard is THE PARSE

The moment a pull ends the middle column becomes `ParseView.jsx`, the identical
component `/zones/:id` renders, and it stays until the next pull opens. Between
pulls it shows the fight that just ended (`showLastRecorded`).

**The cut-down recap (`RecordedFight.jsx`, two rate tables) is DELETED — do not
rebuild a second shape for a finished fight.** What gets asked between pulls is
what crit for how much, who died to what, whether the AoE was covered and who was
late, all of which were a click away in a different table; and two shapes meant
every column added to the parse had to be argued about twice. The dashboard hands
it fights and hides the rail (`.workspace.norail`); the raid page hands it a run
and a rail.

Also deleted from that column: the raid-rate headline block, the AoE audit and the
death report. Three panels stacked under the parse pushed the parse up the screen.
Those live on `/zones/:id`, one click away. What stays beside the fight's name is
its LENGTH, because every figure under it is a rate.

**An open drilldown docks the raider list under the RAIL** (`ParseView`'s
`pickerSlot`, `.dashpicker`) and takes the middle column, one parse or several. The
rail belongs to the PAGE and the picker to a parse three levels down the middle
column, so the two cannot share a grid — the parse portals its column into a slot.
Docked, the left column stops being sticky and the rail caps at 44vh, and a
drilldown's table scrolls inside its card rather than setting the column width,
because the page cannot scroll with the notes standing to the right.

The parse needs the RAID REPORT where there is no run to ask about, hence
`GET /api/encounters/report?ids=` — the same `build_for_encounters`, memoized on
the id set. It is a PROP, not a fetch inside the parse.

**`stale` means the UPLOADER is quiet, never "between pulls".** It used to mean
both, which washed out the numbers everybody reads hardest in the half minute after
a kill. The mini rail is handed the last pull rather than null.

**The middle column can be switched OFF** (`Parse`, remembered): dimmed and PAUSED,
countdowns included, so the mini overlay is the only thing moving while you play. A
fight that ends still lands there as its record.

**The display switches live in the rail head, across from the character name**
(`headActions`): `Mini · Parse · In-game · Overlay` — where is the parse, nearest
window outward. **The dashboard bar carries no status**; the site header owns "ACT
connected" and idle/in-combat, and the bar does not render when nothing is
happening to the page.

### Following the log that is being PLAYED

Two EQ2 clients is two receiving sessions on one account. `liveliest()` picks on
`in_combat`, then `last_ingest_ts` (newest-created is not a signal):

- **Never mid-pull.** A session in combat keeps the page whatever the other does —
  which also stops two boxed clients bouncing the screen every poll.
- **Between pulls, the fight wins.**
- **A hand-picked character holds it** (`picked`) until its log goes quiet
  (`QUIET_S`, 2 min). Following the action is a default, not a cage.
- **The switch chips only list clients that are alive** — the server keeps a session
  `receiving` for `LIVE_IDLE_S` (30 min), which is right for the record and wrong
  for a row of buttons. Same `QUIET_S` cutoff, dot on whichever is fighting.
- **Re-read every `FOLLOW_POLL_MS` (6s) while more than one session is receiving;**
  with one session nothing polls.

**`in_combat` means an open segment whose last DAMAGE was inside `GAP_S`**
(`LiveState.open_end_ts`), not merely an open segment — the segment stays open for
`CLOSE_S`, and anything reading combat through the difference is talking about a
pull ACT has already ended.

**The nav owns the raid's state.** The **Live Parser** tab sits last, dressed as a
state rather than a place (Idle / In Combat), answered by `live.in_combat` off
`/api/sessions` — which costs nothing and never turns snapshot building on, and
goes dark when log time falls `LIVE_LAG_S` behind the clock. Green in three states
of one hue, because the parser working is the good case, and colour rather than
motion because it sits in the corner of the eye all night. The header pill now
covers only **Parsing**, the one state the tab does not carry.

## AoE detection and the Spell timers panel

Live detection **imports `pipeline/aoes.py`'s constants and clustering** rather than
restating them, so the live and recorded rules cannot drift. Two deliberate
differences: nothing filters on name grammar (live, a one-word boss looks like a
raider — touching five RAIDERS in one second is the real evidence, a claim only an
enemy ability can make), and a sourceless `X is hit by <Effect>` counts, pooled
under `Unknown`. Only casts inside the CURRENT fight feed an observed period.

**For an ability ACT's list KNOWS, reach stops deciding what a cast is**
(`aoes.anchors`): one target anchors, a pet anchors (`PET_KINDS` — evidence, never
coverage, so `hit`/`avoided`/`absorbed`/`blocked_pct` stay statements about
raiders), and one cast is a row where an unlisted ability still needs `MIN_CASTS`.
Reach-only anchoring left a boss ability that landed 11 times re-arming off 3
seconds hundreds of seconds apart, reading overdue all fight. Over 60 named fights
this added 13 rows and removed none.

**Don't bound how far a cluster runs from its start** (`aoes._cluster`). Some
abilities tick for longer than their own cycle, so any span short enough to split a
DoT tail from the next cast chops those into casts that never happened — which then
presents itself as a "your timer should be much shorter" suggestion. **Merging is
the failure to prefer**: a merged cast makes a gap LONGER, which `observed_period`
survives by design, while a split one makes a gap SHORTER and nothing downstream
can tell that from a real timer.

**An audit's threshold is not a panel's.** `MIN_TARGETS` (5, an EQ2 group) is right
for `aoes.detect`, whose job is to miss nothing in a tab you scroll afterwards, and
far too loose for a panel you glance at mid-pull — it drew ten rows for three real
abilities on one measured kill. The populations do not overlap (over eight fights
everything the raid calls out reached 72–100% of the raid or carried a reported
timer; every piece of clutter reached 15–43%), so **the panel additionally needs a
reported timer OR `RAID_FRACTION` (0.6) of the raid reached**. A reported timer is
not fraction-gated, because reach varies enormously between a long fight and a
short one. The denominator is `raid.raiders`, which self-scales where an absolute
count would not.

**A cast is a MOMENT; a damage shield is a CONDITION** (`aoes.SUSTAINED_RUN` = 6). A
shield reaches the raid exactly the way a cast does, so reach cannot separate them
and only DURATION can: 9–36 raid-wide seconds per burst against 1 for every real
AoE, with no overlap. It must be caught explicitly, because the clustering actively
hides it — six-second gaps assemble a plausible timer out of melee windows, and
`MIN_CASTS` only ever dropped the shields that never stop. A reported timer is
exempt. The row stays on the recorded tab marked `shield` with its `run_s`; what it
loses is a countdown it never had anything to count down to.

The panel is headed **Spell timers**, not "Raid-wide": it lists the shortlist worth
calling out. The recorded AoE tab still lists everything that touched five people.

### What a timer row carries

- **The countdown** — big tabular digits counting UP past due, because a small
  overdue reads as a stunned mob and a large one as a wrong timer, and "due" alone
  could not say which.
- **The LAST cast's outcome** — how many ate it, how many were covered (deduplicated
  the way `aoes.detect` does). "Is the raid handling this" is the question the
  countdown sets up.
- **What it lands AS**, one word, as a pill (`DtypePill`, drawn identically on the
  recorded tab). Not colour-coded — twelve schools is more hues than the page has.
- **A SUGGESTED timer, printed and never applied** (`aoes.suggest_period`): 3+
  agreeing CLEAN intervals, past 15% and 3s, never when `several_bodies` explains
  it. It is an ERRAND — edit your ACT config so the rest of the raid sees what you
  see — not the countdown, which uses the learned number. Not on the stream
  overlay, because nobody watching a stream can run an errand.

### The two hand marks (`lib/marks.js`, `joust.js`, `minipin.js`)

Two things a log cannot supply: **JOUST** (running out of an AoE and standing in it
look identical in a log) and **MINI** (which AoE is worth a slot beside the game).
Both are keyed by ability NAME, because both are properties of the ability and a
mark has to outlive the pull it was made on.

Stacked **PILLS** per row on the live panel and the recorded AoE tab, never
checkboxes: a tick says what it does only in its tooltip, and these rows are read
mid-pull when nobody hovers anything.

**A mark is an ANSWER — yes, no, or nothing said — and nothing said defaults to
whether ACT's list knows the ability** (`marks.actListed`, off `reported_s`). A set
of names could only say "these are on", and a good default must be overrulable
downwards. The list is the raid's own callout shortlist. Clicking stores the
opposite of what is SHOWING, so the first click on a defaulted-on row turns it off.

**A mark is on the ACCOUNT, and localStorage is now the CACHE in front of it**
(`user_marks` v35, `backend/marks.py`, `GET/PUT /api/marks`) — the same panels are
drawn in three browsers, and the in-game window is read by the person who did the
marking.

- Module state is seeded from localStorage at import, so the first paint awaits
  nothing and the account's answer corrects it. A click applies locally and pushes
  in the background, so signed out or offline still marks.
- **The two token screens are handed the marks with their config** — no cookie, so
  they cannot ask, and the poll they already run means a pill toggled on the
  dashboard reaches the game window on the next tick. What crosses is a set of
  ability names with no account, character or raid attached.
- **The first signed-in read MERGES per ability** (`syncMarks`): every pre-v35 mark
  lives in one machine's localStorage, so an ability the account has no answer for
  takes the browser's and one it does have keeps the account's.

**MINI decides eligibility, `MINI_TIMER_ROWS` still decides capacity.** Damage is a
guess at which AoEs matter; the one somebody needs on screen is the one they have to
MOVE for, which is not a quantity in the log. They stay separate because a fixed
scene is fixed however strongly somebody feels about a sixth countdown.

### Expiry, and the burn window

Snapshots are rebuilt from the fight's events rather than accumulated, so a dropped
row returns on its own. The browser re-applies both lines against its own clock
(`aoe_missed_s` / `aoe_drop_s` ride in the payload), since that clock runs ahead.

- **A row WITH a period is admitted MISSED at `MISSED_S` (15s).** Past that the mob
  was stunned, or everyone blocked the cast so nothing printed to detect on, or the
  timer is wrong — none is a countdown.
- **A row with NO period leaves at `OVERDUE_DROP_S` (60s) from its LAST CAST.** It
  has nothing to be late for, so nothing expired it and it held a slot all fight —
  which matters for the raid-wide abilities that do not repeat on a clock.

**15s and not 60 because of the BURN WINDOW.** The window belongs to the SOONEST
jousted cast, and a cast due thirty seconds AGO is soonest by a mile, so one skipped
cast held the window at a large overdue number through a stretch the raid could have
been burning in. `nextJoust` skips a cast past `missedS` outright.

**The burn window** is the last row and the only one that is not an ability. It reads
the same seconds the other way round — not "the AoE lands in 24s" but "you have 24
seconds in melee", which is the number a raid calls out. Its own colour (`--joust`,
teal: the drain is amber and overdue is red, and this row is neither a reading nor a
problem), and inside `JOUST_WARN_S` (5) it says **JOUST** in the clear, flashing six
times over three seconds and then holding — three seconds catches an eye that was on
the game; a light blinking for the rest of the window is one people stop seeing.
Under `prefers-reduced-motion` the blink is REPLACED by a solid danger block, not
removed: reduced motion is a request for less movement, not less warning.

### The compact panel is not the dashboard's (`AoeTimers: miniTimers`)

The dock and the stream overlay draw the METER UNDER the timers in a fixed scene, so
every permanent row is a raider off the bottom. So: rows with no period are dropped
**while the fight is running** (once it ends they all belong again), the rest is
capped at `MINI_TIMER_ROWS` (3) cut by DAMAGE and drawn in first-cast order, no
dtype pill, and no `measured`/`timer`/`seen` word — provenance is something you look
up once, so it stays on the title (`PERIOD_NOTE`) and on the full panel, and it costs
the name and the digits width.

### Nothing moves that does not have to

The panel is read while fighting, so the cost of motion is that **you lose your
place.**

- **Row order is fixed by FIRST CAST** (`livemeter._live_aoes`), not soonest-due,
  which reshuffles on every re-arm. A first cast cannot un-happen. The accepted cost
  is that the next cast due is no longer the top row: **read by POSITION, not by
  rank** — a position is learned once where a rank is re-read every glance.
- **The compact three are chosen by damage but drawn in first-cast order**, so a
  swap changes at most one row, in place.

**The one exception is the landing: the row flashes red and says `HIT!`** (`justHit`).
A reset countdown looks exactly like one that has been running, so the flash is the
only thing distinguishing "it fired" from "you looked away". Three details, each
against the obvious choice: **one pulse, not a strobe** (already a cross-fade, so
reduced motion keeps it — a panel that stopped reporting landings would be
withholding a reading); **NOT seeked**, unlike the drain, because the screen is ~1s
behind the log and seeking would show the decay rather than the flash; and **derived
from `last_cast_ts` per render, never remembered** (`printed()` includes the flash
window, or a landing stays announced until a digit changes).

### The drain bar belongs to the COMPOSITOR

Both halves of this were caught on somebody's stream, not on a dev box.

- **`log_ts` is the newest line SENT, so anchoring on it flat re-anchors backward
  every payload** and the bar jumped and re-drained. Same cure as the elapsed clock:
  `useLogClock` predicts forward and takes a payload only when it is AHEAD, or
  `SNAP_S` behind.
- **20Hz is not a frame rate.** An OBS source composites at 60fps, so a length
  rewritten every 50ms advances in 3- and 4-frame steps — judder from a perfectly
  correct value. The bar is a CSS animation (`@keyframes aoedrain`,
  `transform: scaleX()`) over one period, SEEKED with a negative `animation-delay`
  taken **once at mount** and keyed on `next_due_ts`, so a new cast remounts and
  re-seeks and nothing else touches it. Between casts JS does not touch the bar.

The digits stay on the ticker but re-render only when what they SAY changes. The
panel takes `running` off the same `ended`/`stale` pair the elapsed clock does: a bar
draining toward a cast the ended pull will never get is actively wrong. The rows stay
and say how many times each ability fired.

### The timer is LEARNED, and a reuse debuff moves it

Two changes that only work together: the site measures its own timers from every raid
on a mob, and it measures what a reuse debuff does to them.

**Tracking the debuff costs nothing** (`refdata/reuse_debuffs.json`). The registry
holds which player abilities open a window and how long it stays open, both off the
ability's wiki infobox (which is why `gamewiki` captures `duration` as well as
`recast`). A reuse debuff is a **damage line from a player onto a mob**, so both
paths already have it and **no cast line is involved** — which matters, because
third-person cast lines are exactly what this parser drops. Two things the log cannot
say, stated rather than papered over: nothing prints when a hostile debuff FADES
(hence the curated duration), and nothing says whether the mob resisted it while
eating the hit — so a window means "applied", never "working", which is what the
verdict measures.

**A swipe is matched on WHAT IT LANDED ON, never on the source.** A registry entry is
a player ability by definition, and another raider's ability line parses to
`Subject(name, 'unknown')` because a bare possessive name is what the parser cannot
classify without the roster. A first cut required `unit == "player"` there, which
matched the LOGGER and nobody else — usually the one person not pressing it — so the
live panel saw none of a whole fight's windows while the audit path (resolved
entities) was unaffected, and the two disagreed silently.

**A cycle belongs to the state at the cast that STARTED it** (`aoes.split_cycles`).
The obvious model — how much of the gap the debuff covered — does not separate the
populations: the covered fraction of the short cycles sat inside the range of the long
ones. Classified at the cast, the same fight splits cleanly, because the mob takes its
recast from what is on it when it casts and a debuff landing halfway through does not
retune one already running. The audit, the live panel and the rollup all call it.

**What it does is MEASURED, never assumed** (`pipeline/aoelearn.py`). The tooltip says
-50% reuse speed; it does not mean ×1.5 and it does not apply to everything. Affected
abilities land around ×1.28–1.32 and immune ones at ×1.00 with nothing in between, so
`AFFECTED_AT = 1.15` and `IMMUNE_UNDER = 1.10` sit in the empty middle and a row
between them stays **unknown** rather than being forced to a verdict. One boss has
three abilities under one debuff in one fight measuring ×1.47, ×1.35 and ×1.03 — a
global factor would have got the immune one wrong. Read against ACT's numbers the
swiped values look like config errors and are not; measuring both sides is what
separates them.

**A swiped bar is ONE SPAN with a tick at the normal timer** (`NormalMark`) — the
stretched number from the first second, never a length that changes mid-drain. A cast
landing ON the tick says the ability is immune; one landing at the END says the
stretch is real. The bar runs to `base × factor` when measured to stretch, to the base
when measured not to move, and to the stretched number using this ability's own ratio
or `typical_factor` when unknown — deliberately weaker evidence than the verdict asks
for, because **the verdict decides what we CLAIM and the bar only decides where the
drain ends**, with the tick putting the other number on screen anyway.

The first build planned NORMAL and grew past it toward the estimate: the bar resized
while being read, and a cast that was never late opened well past due. **Overdue counts
up from the number the bar was running to.** There is **no pill** — an earlier build
badged every debuffed row, and **a word on this panel has to change what somebody does
in the next few seconds**; that one restated the bar in text and spent a raider's row
doing it. It lives on the tick's `title`.

#### Timers are crowdsourced (`aoe_cycles`, schema v33)

Every watched recast is stored — one row per interval, tagged with the state at the
cast that started it — and the timer is derived from all of them, site-wide. **The ACT
list is where a timer STARTS, not where it ends.** Order of authority: `learned` >
`reported` > this pull (`aoelearn.timer_for`).

Adoption needs `MIN_AGREE` (6) agreeing CLEAN intervals across `MIN_FIGHTS` (2)
distinct fights, and never when `aoes.several_bodies` explains the number better.
**Observations are stored, the conclusion is derived**, so a threshold can change
without re-reading a year of logs, a re-parsed fight replaces its own rows instead of
double-counting, and a learned number can be taken apart into the cycles behind it.

**A FIGHT IS A PULL, NOT AN ENCOUNTER ROW** (`aoelearn.pull_keys`). Two raiders who
both upload one raid produce two encounters of every pull, which let ONE night satisfy
`MIN_FIGHTS` by itself — exactly the anecdote the gate refuses. `encounters.dup_of`
cannot answer this: it is one character's overlapping FILES, and merging two players'
parses would break what a zone run is. **Identity is OVERLAP, not start time** — same
mob name, windows overlapping by more than half the shorter fight. A start-time rule
gets ~92% at 15s, and the pairs it misses have 100% overlap: a raider who engaged late
has a shorter encounter sitting entirely inside somebody else's.

**Pooled SITE-WIDE**, which is a reading of the sharing rules rather than an exception:
a mob's recast is a fact about the GAME, like `zone_eras.json` or an item's stats, and
the rows carry no raider, roster, parse or run.

Adopting a measured number is only safe now the debuff is accounted for — before,
"observed disagrees with reported" had two explanations and no way to choose.
`suggest_period` takes CLEAN cycles only for the same reason.

#### A mob that SPLITS is a special case, and is written down (`split_mobs.json`)

**A timer is per (MOB, ability)** — cycle rows, `learn`, `timer_for` and the live rows
all key that way, so two mobs with different names casting one AoE keep two timers and
two countdowns. Only ACT's list is per ability, because that is ACT's format.

What breaks that is one name on SEVERAL BODIES — a mob that splits, each body on its
own recast, so the gaps measure a superposition. Two halves alternating read as one mob
on half the real number, with enough agreeing clean intervals across enough uploads to
pass every gate, and **more evidence makes it worse rather than better**.

So it is named in `refdata/split_mobs.json`, reference data beside `zone_eras.json` and
`reuse_debuffs.json`. A name there is never learned from, never suggested from, and
gets **no live countdown at all** — a bar that is wrong on nearly every cast while
looking exactly like the correct bars beside it is worse than no bar. The row stays,
counts its casts and still flashes on the landing, as a damage shield does. Read at
DERIVE time and never stored on a cycle row, so naming a new splitter re-decides every
fight the site holds — no reparse, no `PARSE_VERSION` bump.

**HOW MANY BODIES A NAME HAS IS GAME KNOWLEDGE, NEVER INFERRED FROM PARSE SHAPE.** "A
measurement well under the ACT timer, from a source that is not the fight's named,
might be two mobs" was built and REVERTED: it killed real single-mob timers, `is_named`
is the ENCOUNTER's headline so every add fails it, and a mis-typed ACT entry looks
identical anyway. Most mobs sharing a name never overlap their AoEs.

The one inference kept is `aoes._instances_hint`, because it is a SIGNATURE rather than
a direction: a clean whole fraction of the ACT timer to inside 20% is what N mobs on
one timer look like and is not what a wrong config entry looks like. It says which N,
withholds the learned timer and the suggestion, and **never takes a countdown away** —
it is computed off this pull's own moving number, so a row gated on it would gain and
lose a countdown mid-fight. A NAMED is exempt from `instances`, and NOT from the file.

### The reflect window — a duration, not a period (`livemeter._live_reflect`)

Anchored on a mob entering a STATE and counting toward it leaving. It shares the drain
bar and nothing else: no ability, no reach, no `RAID_FRACTION`, no period, no cast line.

**The mechanic announces itself nowhere** — no emote, no buff, no cast line at any
measured window start. The only evidence is a raider being denied, so the row cannot
exist until somebody has paid for it. That cost is bounded and is what justified the
feature: ~5% of the casts eaten across the measured windows landed in the trigger
second itself; the row is for the other 95%. It says how long the CURRENT window has
left and does not pretend to predict the next.

**The duration is the clustering rule** (`aoes.reflect_bursts`). Everywhere else the
gap between casts is the unknown, so ticks merge on a threshold that is a guess; here
the duration is the curated fact and the gap between windows is the accident, so a
stamp joins the open window if it falls within `window_s` of that window's START — and
a window can never be reported longer than the mechanic is. `REFLECT_EDGE_S` (2s) is
slack on **membership only, never on the bar**: a log stamps whole seconds, so a state
entered mid-second prints its last deny a second late. The drain runs to the documented
number, and the tally uses the burst's boundary rather than the bar's.

**WHICH MOBS GET A ROW IS A HUMAN'S CALL** (`refdata/reflect_windows.json`), on the
ladder `docs/census-abilities.md` sets for a pet or proc label. Several mobs reflect
something and the severity spans two orders of importance — normalized against health
pools, one boss's windows cost a median 16% and a p90 of 79% of the caster's health
while another's cost 10% and 23%, and only the first ever costs more than half a bar.
**This distinction is invisible in absolute damage**, which is why the allowlist is not
a detector and why a severity threshold was not attempted: median hit, max hit and
return ratio all look alike, and two measures point the wrong way. The denominator was
the whole thing.

Two findings the row is built on: the reflect returns the spell to **its caster**, so
`damage` is paired on (ability, caster) rather than summed from the mob's output (the
boss is doing plenty else and summing it reads far scarier than the truth); and the
reflect is a **chance, not a wall**, so the row says *reflect*, never *your spells will
be reflected*. The window's end is not observable from the log, which is exactly why the
duration must be curated.

**It leads the panel and is exempt from `MAX_AOES`** — the one row that jumps the queue.
"Rows do not move" protects rows learned by position over a whole fight; this one lives
30 seconds, so there is no position to learn. It holds its slot for `REFLECT_CLEAR_S`
and says CLEAR rather than vanishing at 0:00 — every other countdown counts toward
something happening, this one toward something STOPPING, and that moment is what
everybody is waiting for. Red while running and green when clear, the opposite polarity
to `.aoerow.due` immediately above it, which is why the row spends width on words.

**Only the window that has already STARTED is reported.** Live ingest cannot produce
anything else, but replay can — taking the last burst outright put a later window's
countdown on screen from the pull timer.

## The mini parse (`MiniParse.jsx`)

**The mini parse and the stream overlay are ONE component**; `MiniRail.jsx` is only the
DOCK (which edge, and the two buttons). **Don't give the overlay a meter of its own — a
change to the mini parse IS a change to the overlay.** `MiniParse` renders a run of
`.minipanel`s and nothing that positions them.

Condensed **horizontally**, which is the whole constraint: vertical space is free and
every pixel of width is taken from the game. At 244px a row keeps the rank, name, class,
rate and max hit; the deaths badge, cures column, AoE source and hit/blocked split go,
and the fold goes with them (nobody clicks "12 more" mid-pull, so it is a hard ten rows).
The class survives as the **short form the raid says out loud** (`classShort`), because
the bar's hue is the ARCHETYPE and four hues cannot separate six fighters. The name
ellipsizes before the class does.

**On a mini row the RATE sits against the name and max hit is outboard** — the reverse
of the dashboard meter. At 244px the ranking is read straight down the rate column; max
hit is looked up once a pull, never scanned. Each cell keeps its own weight through the
swap.

**Which edge is a setting**, remembered per browser: it is a fact about a desk.

Two things it deliberately does NOT do: follow the main column (click back to an earlier
fight and the middle becomes that record while the mini stays on the pull in progress),
and re-derive the ranking (`LiveMeter` exports `meterRows`/`meterRate` and the rail calls
them — two orderings of one parse on one screen is the bug nobody would look for).

It renders into `document.body` for the reason `RaidNotes` and `Picker` do: every `.card`
here carries `backdrop-filter`, which is a containing block for `position: fixed` as well
as a stacking context.

### The rail has its own switches (`MiniRail`'s ⚙, `eq2a.mini.cfg`)

**The rail's switches are the RAIL's; the meter's chips drive the middle column only.** A
page read between pulls can spare three stacks of bars; 244px beside the game cannot, and
the third stack is what pushes the countdowns off. Both say `DPS`/`HPS`/`TANK`
(`METRICS.short`); `rateLabel` still heads the FIGURE, so tank is `Inc/s` there.

The panel carries five switches for what is DRAWN (three meters, the AoE panel, the burn
row) and then, separately, what may INTERRUPT — the AoE and burn switches appear in both
groups on purpose. **Every meter off is a setting, not an empty state**, so the bars'
panel is absent rather than drawn empty. The panel docks UNDER the head, so a settings
list opened mid-pull shortens the bars instead of covering the countdowns it configures.

**The AoE panel is switchable on BOTH** — the rail's in its ⚙, the middle column's as a
chip in the meter's own row (`eq2a.mainaoes`, ruled off from the metric chips with
`.tabsep` because a chip left of the rule adds a stack of BARS and this one adds a
panel), and neither reaches the other. Not in the rail head: that says which SURFACE is
on, the chip row says what is in the column. On by default — an audit you have to go and
find is one nobody finds.

### One settings row, two panels (`Settings.jsx`)

The ⚙ and the stream overlay's options are the same list at two sizes. The rail's lit-pill
grid is GONE: **a pill you must press to learn what it does is what a 244px strip really
cannot afford**, and the sentence that would have explained it was in a tooltip nobody
hovers mid-pull. `SettingRow` and `Switch` are one pair used by both, tightened in CSS
(`.miniconf .settingrow`) and never rebuilt.

- **A chips row is `as="div"`, never a `<label>`** — a label adopts the first button, so
  clicking "Theme" pressed "Transparent".
- **A switch held off by a master switch is `disabled`, never hidden** — a row that
  vanishes takes its setting with it.
- `Bars` is the one non-switch row (any subset of three stacks, as chips) and it stacks
  (`.stack`), because three chips plus a name and a hint do not share a line at 244px.

### The notification block (`MiniAlerts.jsx`)

**Directly under the last stack of bars**, which is why `.miniparse` shrinks but never
GROWS (`flex: 0 1 auto`): stretched to fill a tall window, the block ended up a column of
empty space away from the parse it belongs to.

- **The countdowns, one size up** — `AoeTimers` itself, not a copy: same rows
  (`miniTimers`), same compositor drain, same flashes, scaled in CSS alone. They are
  **PERSISTENT**; a countdown that only appears when nearly due is one nobody can plan
  around. `showRows` and `showBurn` are independent, because the block is sometimes the
  burn window alone.
- **The death cards** pop in above them, hold seven seconds and go — the only thing here
  that is an EVENT rather than a clock. **MAIN/OFF TANK DOWN**, and **N DOWN** for more
  than five raiders inside 8s; a wipe card supersedes the tank card rather than stacking.
  Two at once, maximum, on the WALL clock.

The block is absent entirely when it has nothing to hold.

**The main tank is the FIGHTER who has taken the most damage this fight, the off tank the
second** (`tankOrder`, over `CLASS_ROLE` — all six fighters, brawlers included). Nothing
is configured and nothing is guessed: **an unclassed raider is nobody's tank**. **A single
dps dying earns no card** — the deaths figure carries it; a tank dying, or more than five
at once, is the fight changing shape.

**Deaths are counted by DIFFERENCE and the first payload of a pull is a BASELINE** — the
payload carries a running total per actor and no death events, so without the baseline
(retaken on `started_ts`) every pull would open by announcing the last one's dead.

**Nothing blinks**: a solid danger block plus a 180ms cross-fade, so reduced motion has
nothing to take away. Death cards ride the master Notifications switch and have no switch
of their own — a countdown you have tuned out is a preference; a tank on the floor is not.

## Notes (schema v28)

The dashboard's right column files what you write mid-raid. On trash it belongs to the
ZONE (`mob_name IS NULL`), on a named to that boss, and **the client decides which** — it
is the thing that knows what is on screen.

**A note is keyed by `(user_id, zone, mob_name)`, NEVER by encounter**: a live session is
rebuilt from raw at close and every id changes, so a note identified by one would lose its
subject overnight. `encounter_id`/`zone_run_id` are provenance, never identity. Keying on
the boss means tonight's note lands beside every other attempt on it, which is the pile
this grows into (`GET /api/notes/outline`).

Notes are private to whoever wrote them, with no group predicate, exactly as imported
parses are. Screenshots are re-encoded to WebP under `NOTESHOTS_DIR`, never the uploaded
bytes, served by an owner-checked endpoint.

**The zone is the BASE name** (`zones.base_name`): a repeat lockout is a second lockout,
not a second zone, so filing under the log's spelling would start a new pile each entry. A
read matches on it too (`_variants`), so older rows fold in. Nothing migrates.

**The column shows the ZONE, the composer writes to the SUBJECT** — engaging a named used
to hide the zone's own notes, which is the moment you most want them.
`?scope=zone` is the wide read; the default stays narrow because it is what the composer
asks.

**The column collapses SIDEWAYS, and Enter files a note** (Shift+Enter is the newline;
Ctrl+Enter still works). Closed it is a tab down the page's edge and its grid track shrinks
to it (`.dashgrid:has(.notestab)`) — a collapse that keeps 340px is no collapse. The tab is
the whole control, because a switch that can only be turned on is not a switch. The
`File under X` button sits under the textarea, and the screenshot drop is a strip: a paste
needs no target.

### The notes outline, grouped by expansion

The raid list's right column (`NotesOutline.jsx`) is the whole pile as a table of contents.
**It opens from a button and is closed by default** — standing open it took 300px from a
table that then scrolled sideways.

It groups by **expansion**, the order a TLE server unlocks content in. **Which expansion a
zone came from is REFERENCE DATA, never inferred** (`backend/zones.py` ←
`refdata/zone_eras.json` ← the wiki's zone infobox, synced by hand with
`tools/sync_zone_eras.py`), read at import and never fetched at runtime. A zone the wiki
files under a live update resolves by that update's DATE to the expansion that was live;
patch-note dates are typed four different ways, and a parser that knew one form left whole
expansions unplaced. **A zone with no entry groups under "Other" rather than being
dropped.**

**Every named links out to eq2lexicon** (`lib/raids.js: lexiconRaid`, new tab because it
refuses to be framed). **The lexicon holds the strategy, the note holds what happened to
us** — don't restate one on the other. Only nameds get a link.

## The stream overlay (`/overlay/<token>`, schema v29)

A page for an OBS browser source, which decides the design: a browser source carries no
cookies and `EventSource` cannot set a header, so **the token rides in the path**. It is
therefore deliberately narrow — **it reaches the in-flight meter and nothing else**: no
session ids, no fight cards, no history, no account name, because a URL that ends up in a
VOD must not be a way into anybody's parses (`test_overlay_api.py` asserts each absence).

Two doors, one read surface: the overlay stream and the session stream both read
`live.live_snapshot` rather than one endpoint branching on how the caller authenticated —
a generator that decides authorization halfway through is one nobody can audit. The session
is re-resolved every tick, so switching to an alt follows on its own, and the stream stays
OPEN with `{"live": false}` when nothing is running, because an OBS source is opened once
and left for hours. **Revoking is a row update, and a revoked token answers exactly like
one that never existed.**

The page renders BEFORE the app shell (`App.jsx` branches on the path): nav, theme toggle
and account icon are furniture on somebody's stream. `transparent` is the default theme and
paints nothing at all, html and body included.

**It never goes BLANK out of combat, only quiet.** A meter that exists solely mid-fight
cannot be positioned in OBS: between pulls it keeps the last fight dimmed with a "between
pulls" tag, and before any fight it draws a placeholder parse marked "sample parse —
waiting for combat" so nobody quotes it.

**The settings open where they are used** (`OverlayPanel.jsx`), from the dashboard bar
beside Mini. The first open MINTS the link if the account has none, so Copy link always has
something to copy. `/account` keeps the same panel (`OverlayOptions.jsx` — two settings
lists for one feature is how they end up disagreeing).

- **OFF is not REVOKED.** Off blanks the page while the source stays connected and
  positioned; revoke kills the URL for good.
- **The page re-reads its config on a timer**, because nobody can press refresh on a
  browser source.
- **A correct token is not a guess — `_resolve` looks it up BEFORE the rate limiter, and a
  hit no longer `clear()`s the bucket.** The bucket is per ADDRESS and these pages re-ask
  every 5s forever, so one forgotten revoked link was a dozen failures a minute and locked
  the same machine's VALID overlay out of the whole feature (429 on a good token → black
  window). Misses still cost and are still refused. Clear-on-success belongs to the login
  route, where the person failing and the person succeeding are the same; here a working
  overlay would wipe the counter often enough that no guesser could be stopped. Guarded by
  `test_a_stale_link_cannot_lock_out_a_good_one`.
- **A failed request is not a dead link.** Only a 404 latches "no longer active"
  (`_resolve` gives it for revoked and never-existed alike) and it STOPS the poll, since
  nothing can un-revoke a token; a 429/502/restart/dropped request keeps the screen and
  retries with backoff, and a good read clears it. Failures back the interval off toward a
  minute. **Never let a hiccup become a permanent state on a page nobody can refresh.**
- **A dropped connection is EventSource's problem; a refused one is ours.** It reconnects a
  stream the server merely closed but gives up permanently on a non-2xx, so
  `readyState === CLOSED` (and only CLOSED) schedules a reopen.
- **"Not yet" is not "nothing"** — no config yet says `connecting…`, because a blank
  rectangle on a black document is how a 429 spent an evening looking like a broken page.
  `enabled: false` is the one state that genuinely means paint nothing.
- **Width is TYPED in pixels and blank means "fill the source"** (`width_px`, clamped
  160–1920 rather than rejected — a 422 mid-raid is worse than a sane value). A scene is
  built once and lived with. The sheet caps it at `max-width: 100%`, because a number wider
  than the source has to clip rather than put a scrollbar across somebody's stream.

## The same link, pointed at the game client (`/ingame/<token>`, schema v34)

EQ2 has its own browser window, and the parse belongs in it: the player who has to move out
of an AoE is at the game. **The overlay is sized to be read after a downscale and an encode;
this one is read at 1:1 on the same monitor as the game**, covering somebody's UI —
`text_scale` defaults to 1.25 there and 0.9 here, which is the whole disagreement in one
number.

**Two kinds of token, one page** (`overlay_tokens.kind`). The public half never branches on
kind; only the config defaults and clamps do (`CONFIGS`, `INGAME_MIN_SCALE`/`MAX_SCALE`).
Separate ROWS because **revoking is per URL** — a link in a VOD must be killable without
taking the window beside somebody's hotbars with it. `kind` is fixed at creation.

Three things differ in `pages/Overlay.jsx`:

- **The notification block is ON in-game and absent on the stream** — a card is an
  instruction and a stream audience cannot follow one. It is mounted whether or not a pull
  is running, never gated on `inCombat`, because deaths are counted by DIFFERENCE against a
  baseline the block keeps.
- **The document is PAINTED, not transparent** — EQ2 composites nothing, so a transparent
  html/body shows through as a white margin. `data-ingame` overrides `data-overlay`, and
  `transparent` is not offered as a theme here.
- **The window is the width, and overflow SCROLLS.** No `width_px`, no `layout` — geometry
  is for a scene somebody composes, and this one is resized by dragging its edge. A
  scrollbar on a stream is a grey bar over the game; here it beats a countdown pushed out of
  a window nobody can make taller.

Button order in the rail head is `Mini · Parse · In-game · Overlay`. Both links are
`OverlayPanel` with a `kind` and `useOverlays` filters by it — a panel reaching for
`overlays[0]` opens the wrong settings once both exist. `/account` lists stream overlays
only.

### Four rendering targets, and only one is a current browser

The parse is drawn in the raider's browser, in an OBS source, and in EQ2's in-game browser.
The last two are embedded CEF builds years behind, and **an engine that cannot parse a
property VALUE throws the whole declaration away and paints the element without it.**

`color-mix()` needs Chrome 111, and it was used for every translucent fill — meter bars,
the AoE drain, `due` and swiped bars, the landing flash, the normal-timer mark, the burn
window, the alert block. In the in-game window all of them rendered as **nothing**: elements
present, correctly sized, no background, while everything drawn in a plain colour worked —
which is exactly the shape the bug report took.

**So a translucent fill is `rgba(var(--x-rgb), 0.NN)`, NEVER `color-mix()`.** Every colour
that needs one carries an `-rgb` triplet beside it in `tokens.css` in BOTH themes, and the
pairs must be kept in step. The one exception is `stats.rankColor`, which mixes toward
`--text` rather than transparent, is dashboard-only, and degrades to no tint.

**General rule: on the surfaces nobody can open devtools on, prefer the older construct.**

### Readable at small (`dense`, the mini parse's third size)

The dock is 244px, the OBS scene is whatever somebody chooses, and the in-game window is
neither — maybe 180px, inside the game's own UI.

- **The class goes so the NAME fits** — a truncated initial beside a four-character class
  identifies nobody, and the bar's hue still carries the archetype.
- **Max hit goes** — looked for after a pull, not scanned, and it cost a fifth of the
  window. Every parse page and the stream overlay keep it.
- **HPS and deaths come off the headline**, leaving the clock LEFT and raid DPS RIGHT over
  the column it totals, rendered key-first so the FIGURE meets the column edge.
- **Nothing scrolls sideways and nothing leaves a row** — the name ellipsises, the clock
  never shrinks, the row clips the rest.

**Sharpness is whole pixels and a hinted face, NOT weight.** `calc(15px * 0.7)` is 10.5px
and every `em` under it lands on another fraction, which is what "small but mushy" was:
`--ovl-px` is rounded in `Overlay.jsx` and the scope calcs off it in whole px, which is why
the in-game size chips read in **px, not percent**. Tahoma/Verdana are hinted to ~8px where
`system-ui` is not. At 9px a 700 stem smears rather than thickens — contrast carries the
hierarchy, the way ACT does it. **Don't re-add bold here**, and watch for `rem` in the
compact rules, which ignores the scale entirely.

**Smaller type needs MORE contrast, not the same contrast smaller.** `--text-muted` is
~3.5:1 — fine at 12.5px, gone at 8px — so `.overlaypage.ingame.theme-dark` **re-declares the
TOKENS** (flat opaque black, `#fff` text, ~10:1 muted, bright hairlines) rather than
re-lighting elements one at a time like the OBS scope, because an element list misses every
panel added after it. Weight is free width; radii, gaps, shadows and the outer frame are not
(the window is the frame). Between pulls it dims to 0.88, not 0.72.

**In-game rows are a GRID with content-independent tracks, never a flex run.** A flex row
sized by its contents both MOVES (a rate gaining a digit re-widths every row) and ESCAPES
(overflow goes off the right edge and the window clips it). Rank and figure are fixed in
`ch`, the NAME is the only elastic track, and it gives by ellipsising. The burn row's second
track is `auto` because it carries a word as well as a clock. `.miniparse` is
`overflow: visible` here too — an inner scrollbar appearing shifts every number.

**The in-game PAGE grid packs to the top** (`align-content: start`). The page is a grid,
`min-height: 100vh` gives it a height the content does not fill, and a grid's default
`align-content` is STRETCH, which grows every auto row equally where flex children do not —
so each panel padded itself out with a share of the leftover window. **It presented as
"looks stupid when there are no AoEs" and had nothing to do with them**: FEWER panels is a
BIGGER share each. An empty `.minitimers` frame collapses too (`:empty`, like `.minialerts`),
because `MiniParse` frames off the raw payload while `AoeTimers` drops rows off a clock that
runs ahead of it. The OBS overlay is untouched.

**A replay feeds the overlay** (`pipeline/replaybus.py`) — how the overlay gets worked on
outside raid hours. A replay drops its latest frame in a per-USER slot and the overlay stream
picks it up ahead of that account's live session. Three properties make it safe: keyed by
user, and an overlay only reads its owner's key; the frame EXPIRES (`MAX_AGE_S`), so a
replay that ended releases the screen with no stop message to lose; and what is published is
the LIVE payload — **the `replay` block never crosses**, since it names the fight and the
night and this token may hold neither.

## Replay (`routers/replay_api.py`)

Reads a recorded fight's RAW LINES off disk, parses them with the same `parse_lines` the live
path calls, and walks a cursor through the result in wall-clock time. The page cannot tell
the difference, which is the point — the meter is otherwise the one surface that can only be
worked on during a raid.

It is a third reader of `livemeter`, so **it writes NOTHING**: no session, no encounter, no
rows, no `LiveState`. That is what makes it safe to point at any fight in the back catalogue.
`simulate_live.py` is deliberately NOT this — it pushes a log through the real ingest
endpoint, which is right for testing INGEST and wrong for testing the SCREEN.

**Two gates, kept apart.** `require_curator` (admin implies curator) gates the TOOL;
`visible_encounters` gates the FIGHT, as every other read does. Widening the second along with
the first would make replay a door into everybody's raids — "admin is operational, not
omniscient" holds here too, and `test_replay_api.py` pins it.

**Read only the raw the fight can be in.** A live session is a chunk per ingest batch with its
own time bounds, so the window query skips the hours in front of the pull (seconds of
decompression become milliseconds); an upload is one file and has no shortcut. Past the fight,
reading stops after `TAIL_GRACE_S` rather than at the first later timestamp — an EQ2 log is
written in order, but being wrong about that fails as a silently short replay.

Cadence is `TICK_S = 2.0`, the plugin's own send cadence: a replay refreshing faster than a
raid can would be a smoother meter than any raid produces and would get tuned against a
fiction. Speed multiplies log time, not the tick, and is clamped.
