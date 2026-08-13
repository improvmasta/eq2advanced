# eq2advanced — Live ingest, the raid dashboard and replay

Part of the architecture reference. Index: `ARCHITECTURE.md`.

## Live ingest — the frozen ACT-DLL contract

`GET /api/ingest/hello`, `POST /api/ingest/batch`, `POST /api/ingest/backfill/done`;
auth is `Authorization: Bearer <device_token>` only. A batch is gzip (or plain)
JSON `{batch_id, mode: live|backfill, lines: [verbatim lines]}` → `{accepted,
duplicates, session_id}`. That is the whole surface a device token reaches — it
sends logs and does nothing else (see "Sharing is a decision for the account" in `docs/sharing.md`).
`backend/tools/simulate_live.py` is the reference client (and feeds the
equivalence test's batch cutter); `improvmasta/eq2advanced-act` is the ACT plugin
that implements it for real, and the two must stay in step.

Design points, in the order they bit:

- **Batch idempotency**: response stored per `(token, batch_id)` in
  `ingest_batches`; a resend replays the stored answer. One batch in flight per
  token (429 + Retry-After).
- **Line dedupe**: `ingest_lines` key = sha256 of `(occurrence-ordinal-within-
  batch, line)`. Identical legit lines in one batch get distinct ordinals (two
  identical hits in the same second both count); a backfill/re-upload overlap
  carries the same ordinals and drops cleanly. Corollary: batches must be cut
  on log-second boundaries so a second never splits across batches — the
  simulator's `--window` does this and the DLL must too.
- **Incremental finalization is a view, not the record.** Per-session in-memory
  tail (`pipeline/live.py`); each batch re-segments the tail and flushes any
  encounter that can no longer change (a later segment exists, or
  `CLOSE_S = GAP_S + TRAIL_GRACE_S` = 17s of log time passed) to the normal
  tables — that's what the dashboard's SSE cards read. At close (`done`, or 30-min staleness) the whole session is
  **rebuilt from its raw chunks through `parse_session`** — the exact bulk
  path — so a finished live session is provably identical to uploading the
  file (guarded by `test_golden_equivalence`: encounters, actor stats, ability
  stats all byte-equal on bobby.txt; encounter ids change at rebuild, which is
  why the Live page refetches on `status: ready`).
- **Staleness needs a reaper, not just a check** (`live.reap_idle_live_sessions`,
  driven from `main.lifespan` at startup and every 5 minutes). The 30-minute
  rule used to be evaluated only inside `open_live_session` — i.e. when the
  SAME character's NEXT batch arrived. Quit EQ2 and ACT and that never happens:
  the session sits at `receiving` forever, the raid page keeps saying **Live**,
  and because closing is what rebuilds it from raw, it is also out of reach of
  the `PARSE_VERSION` sweep (which only looks at `ready`/`parsing`), so no
  parser improvement can ever land on the night you just raided. The startup
  pass runs BEFORE `_reparse_stale` so an abandoned session is closed and
  reparsed once, at the current version, instead of twice.
- **Restart-safe by construction**: the in-memory tail is disposable; raw
  chunks + `ingest_lines` survive, and the close-time rebuild reparses raw.
- SSE: `GET /api/sessions/{id}/stream` (cookie auth) pushes `encounter` cards +
  `status` (incl. uploader-online from `device_tokens.last_seen_ts`) +
  `partial` views of the open fight (below); closes at ready/error. It is WOKEN
  rather than polled — see "How fast the screen sees a hit" — with
  `STREAM_POLL_S` (1.5s) left as the fallback tick. `status` goes out when it
  CHANGES plus a slow heartbeat, because a woken loop runs several times a
  second and the same six fields every time is noise.
- GOTCHA `process_batch(token_row, char, …)`: `token_row` is an ACCOUNT token,
  not a character row — it used to be one.

### How fast the screen sees a hit

The complaint this answers is "the live meter feels slower than ACT's own", and
it is a real measurement, not a rendering problem — which is why the fix is
here and not in the browser (the meter deliberately does not animate; see
below). One line of log has to cross four waits:

| Stage | Was | Is | Why it costs what it does |
|---|---|---|---|
| The held second, in the plugin | 0–1s | 0–1s | A second is never split across batches (the dedupe ordinal), so the newest one waits for a line from the next |
| `Settings.CadenceSeconds` | 0–2s | 0–0.5s | The uploader's own loop; empty reads send nothing |
| Parse + snapshot (`SNAPSHOT_MIN_S`) | ~0.1s | ~0.1s | 65ms on the biggest fight ever measured; the floor moved 1.0 → 0.25 so it stops discarding batches |
| SSE (`STREAM_POLL_S`) | 0–1.5s | ~0 | Pushed now (`pipeline/livebus.py`); the timer is only a fallback |
| **Typical** | **~2.4s** | **~1.0s** | ACT's own display refreshes once a second |

**The held second is the floor, and it is architectural.** A line cannot be
sent until the second it belongs to is complete, which is known only when the
next second starts. Going below that means changing the dedupe key to
`(log second, ordinal within that second)` so a second may arrive in pieces —
a change to the frozen contract, and not worth it to shave the last ~0.5s.

**`IdleFlushSeconds` (3.0 → 1.5) is the END of a pull.** Mid-fight the next
second always arrives and releases the held one immediately; when the log goes
quiet, the last second of the fight sits in the plugin — which is exactly when
everybody is reading the meter. It stays well under the 7s encounter gap, so
holding a second back still never delays a fight card.

**Everyone's config already had the old numbers in it.** The tuning fields are
saved verbatim on the first batch, so a new DEFAULT reaches nobody who already
has the plugin — precisely the people whose meter feels slow. Hence
`Settings.TuningGeneration`: a config written by an older build takes this
build's tuning once and is then left alone, so a hand-tuned value stays
hand-tuned.

### Waking a stream instead of asking it (`pipeline/livebus.py`)

A per-session doorbell. `_publish_snapshot` rings it in the ingest thread; the
SSE loops park on it instead of sleeping. Two rules, both about not missing an
edge:

- **Subscribe, then read.** The subscription wraps the whole read-and-yield
  body, so a snapshot published while the loop is reading leaves the bell rung
  and the next wait returns at once. Subscribing around the sleep alone would
  drop exactly the update that arrived under load — the failure that only
  appears in a raid.
- **The timeout stays.** A wake-up is never the contract: the loops still come
  round on their own to refresh `mark_watched`, to notice a fight card or a
  finalized session, and to survive a lost publish. The overlay re-resolves
  which session it is watching every pass, so it subscribes per pass, and
  `subscribe(None)` (nothing streaming, or a replay, which has no bell) is a
  plain sleeper rather than a fork in the loop.

Process-local, like `live.py`'s own state and `replaybus.py` — one uvicorn
process serves this app, and waiters are rung with `call_soon_threadsafe`
because ingest is a sync handler on a worker thread.

### The ACT plugin itself

`backend/refdata/plugin/EQ2Advanced.dll` is committed and served by
`routers/plugin_api.py` (`GET /api/plugin`, `/api/plugin/download`, both
unauthenticated). The download is a **ZIP** — Chrome and Edge block a bare
`.dll` — and the install steps say to Unblock it BEFORE extracting, because
Explorer copies the mark-of-the-web onto what it unpacks and ACT won't load a
marked plugin. It ships committed rather than linked because the source repo
is private and Actions artifacts expire. Refresh with
`bash scripts/update-plugin.sh`. Source: `/home/lindsay/eq2advanced-act`
(`improvmasta/eq2advanced-act`), which builds on this host with `bash build.sh`.

**Telling people a new build exists, without telling everybody.** The plugin
has no updater, and nobody re-reads an install page they finished with months
ago — so a release needs a way to reach the people running the old one. It is
one pill in the header, and it appears ONLY for an account whose own uploads
say it is behind:

- `device_tokens.client_version` (v30) is written from the uploader's
  User-Agent (`eq2advanced-act/0.2.0` — the plugin always sent it, nothing read
  it). `auth.client_version` is strict: a curl, a browser or an invented header
  reads as "no idea", never as a version.
- The version being SERVED is `backend/refdata/plugin/VERSION`, written beside
  the DLL by `update-plugin.sh`. A .NET assembly version cannot be read back
  without a PE parser, and this number decides who is nagged, so it is
  committed as a fact rather than guessed from the file.
- `GET /api/plugin` answers `update_available` only when signed in AND a
  pairing has reported AND that version is behind. **Never heard from is not
  behind** — a NULL, or anything that fails to parse, stays quiet, because the
  cost of a false pill is somebody reinstalling a plugin that was current.
- Versions compare as NUMBERS (`plugin_api.version_tuple`): `"0.10.0" <
  "0.9.0"` is true of text and false of software.
- Two machines and one updated counts as updated (the NEWEST version any
  pairing reported). A pill that keeps nagging about the laptop somebody raids
  from twice a year is a pill people learn to ignore.

## The raid dashboard (`/live`) and the fight in progress

Everything above reports fights that are OVER. The dashboard is the second
monitor during a raid, so it needed the other thing — the pull that is
happening — and that is `pipeline/livemeter.py`.

**The page**: the night's fights in the rail on the left, the pull happening
right now in the middle, notes and screenshots on the right. The meter is
ACT-shaped — a class-coloured bar behind every row, because a number you have
to compare against twenty-three others is a table and a bar you can read from
three feet away is a meter — over a scrolling raid DPS/HPS chart, with AoE
countdowns above it (ACT's reported timer where it knows one, the shortest gap
that repeated this fight where it does not, and it says which). The metric
chips are SWITCHES, not tabs: Damage, Healing and Tank (incoming) can all be
on at once, each its own stack of bars — a raid leader wants the tank's
incoming NEXT TO the healers' output, not behind it. It picks the raid up on
its own, so it can be left open, and it says so when the night finalizes.
Screenshots PASTE, because mid-raid nobody is naming a file. Replay runs from
the dashboard's own bar — the way this page gets worked on out of raid hours.

**Clearing the rail is about the SCREEN, never about the raid** (`Live.jsx`:
`cleared`, `EncounterTree`'s `onClear`). One ACT process is one session and a
session is a whole evening, so by the time the raid pulls, the rail already
holds the afternoon's dungeon, the writs, and every trash fight between them.
What is wanted there is a clean rail for tonight — which is not an edit to the
raid, and the difference is the whole design:

- A cleared fight is **still parsed, still on `/zones/:id`, still visible to
  everyone it was shared with**. Nothing is destroyed and nothing is hidden;
  it is simply not drawn here.
- So the rows get a single **✕ and no confirmation**, where the raid page's
  edit mode offers Hide, Delete and a second click to confirm. Hiding is a fact
  about the raid and it reaches everybody (`docs/sharing.md`); this is a fact
  about one screen. Same edit mode, same `railedit` slot, one honest verb.
- **Edit and Done are one button in one spot**, docked to the LEFT OF THE FIGHT
  COUNT on the who line (`EncounterTree`'s `countAction`, `.railtail`). Two
  earlier placements were wrong for the same reason. In the head switches
  (Mini / Parse / Overlay) it was the one control there that is not about which
  parse shows where, and `Done` then appeared a section lower — leaving edit
  mode meant hunting for the button that got you in. Given the raid page's own
  actions ROW it stopped moving, but a whole row of a 300px rail for one button
  is rail the fights could have had. The count line already exists, and the
  count is the thing the button is about. It wears `.ebtn`, not gold: gold in
  this app means the raid changed, and clearing rows off a screen does not.
  `✕ Clear all` still unfolds into `.railsec.acts`, which therefore exists only
  while edit mode is on.
- It is kept **per SESSION in localStorage**, so it survives a reload of the
  page you leave open all night and cannot leak onto tomorrow's. Ids are the
  key, and they change when a live session is finalized and rebuilt from raw —
  a cleared id that no longer matches anything is an entry nobody reads.
- Only the RAIL reads the filtered list. `lastEnc` and the commit count stay on
  the full one, or clearing a row off a screen would make the page think a
  fight it already saw is still `saving`. Clearing also drops the SELECTION
  back to live, since the middle column would otherwise go on rendering a parse
  whose row is gone.

**The pull in progress is a ROW IN THE RAIL, and it is never missing.** It was
a button above the list, on the reasoning that it is not one of the night's
fights — and that is exactly how it got lost: click back to an earlier fight
mid-pull and the fight you were IN had no row to return to, because a fight
only becomes a row when the writer commits it, a second or two after it ends.
It is the newest fight, so it is the last row of the list (`EncounterTree`'s
`live` prop). It carries no checkbox — the numbers under it are a view rebuilt
every couple of seconds and stored nowhere, and a combined-stats set holding
one would change under the reader.

The gap it closes is real, so it is closed at both ends: `Live.jsx` HOLDS the
last live fight on that row, marked `saving`, until its `encounter` record
actually arrives, so the list never has a moment with nothing in it. What
clears the hold is the encounter count going up — the commit is the only thing
that makes the held fight redundant. The timeout beside it is not
belt-and-braces: a segment the raid never ENGAGED is not a fight and never
becomes a row at all, so without a cap, walking away from something that did
not fight back would leave the rail saying `saving` until the next pull.

**The bar is faint, and it is one value** (`classes.js: barFill`, the archetype
hue at 24%). What you read off a row is its LENGTH; the hue only says which
archetype got that far, and at a fuller mix the fill was competing with the
name and the rate written on top of it — which is the text the row exists for.
The meter and the mini parse call the same helper, because two weights of the
same bar on one screen reads as two different meters.

**The chart's peak is a headline figure, not a caption on the chart.** It used
to sit in the chart's bottom-right corner, which is also where `.lhnums` ends
once the window is narrow enough to wrap it. It is now a stat in that row
(`peak DPS` / `peak HPS`, from `LiveTimeline.peakRate`), so the flexbox gives it
a place. It is the SMOOTHED peak, matching the drawn line — a label reading
higher than anything on the chart under it is a label nobody trusts twice — and
it only appears where the chart does, since it describes it.

**THE LIVE METER SIZES ITSELF; THE COLUMN KEEPS THE SLACK** (`base.css`:
`--live-w` / `--live-tbl-w` on `.dashmain .livemeter`). The middle column is
sized for the PARSE — a fight that has ended becomes `ParseView` there, twenty
columns of ability breakdown that genuinely want every pixel of a second
monitor. The pull in progress is the opposite kind of object: a name, a bar and
a rate. Stretched across a thousand pixels it read as a row of text with a
quarter-mile of empty bar between the name and the number, and the one thing
the panel exists to say — who is on top, by how much — was the hardest thing on
it to see.

So two widths, because the panel is two things stacked:

- `--live-w` is the PANEL, and it is `fit-content`: it ends where the headline
  does, at the `deaths` stat, the last figure in `.lhnums`. Deliberately not a
  number — the headline is the widest thing in there and is allowed to say how
  wide "there" is.
- `--live-tbl-w` (360px) is the BARS AND THE COUNTDOWNS, narrower still —
  about where `raid HPS` ends. Everything below the headline takes it,
  separators included: a `border-top` running on past the last bar draws the
  panel wider than its contents are, which is the same complaint in one line.

A countdown row carries more than a meter row does, so what it can show is a
question about the room it was GIVEN, not about which panel it is: the shedding
is a **container query** (`@container aoepanel`), not a second `compact` flag.
It drops what `compact` drops and in the same order — the dtype pill, the
source, the last cast's split and the provenance word go; the ability, the
clock and `HIT!` stay, and the ability ellipsises. Widen `--live-tbl-w` and the
row earns its words back on its own.

Only the LIVE meter is held in. A finished fight is the parse, and a parse
being read against another parse extends as far as it likes — which is what the
width was there for in the first place.

**A figure steps, a length slides, a clock ticks** (`lib/smooth.js`). The
picture arrives in batches — the plugin sends every `CadenceSeconds` (2s), the
stream polls the database every `STREAM_POLL_S` (1.5s) — and the three things
on a row want different treatment of that.

DIGITS STEP. Counting a rate up to its new value makes it unreadable while it
does so, and since a fresh number only lands every couple of seconds it would
be doing that most of the time; the first seconds of a pull are worse still,
because every payload is an enormous relative change (a rate over three seconds
of log halves when the fourth is quiet) and six digits rolling through each one
is a slot machine, exactly when people are looking hardest. **Tweened figures
were built and REMOVED** — don't re-add them. The fix for numbers that feel
stale is a shorter `CadenceSeconds`, not an animation over the gap.

LENGTHS SLIDE. A bar is not a value you read off, it is a shape you catch out
of the corner of an eye, so the fill travels (`Smooth.jsx: Bar`), and a move
past `SNAP_FRACTION` cuts instead — a rank changing hands is not a drift.

THE CLOCK TICKS, in the browser, once a second (below). All three run off ONE
`requestAnimationFrame` loop at 20Hz, so React batches every tick into a single
render pass, and the moving part is always a LEAF so it never re-renders the
row around it. Nothing here invents data: a slide only ever travels between two
numbers the server sent.

That loop drives DIGITS, and 20Hz is chosen for digits that change once a
second. The one thing here that moves CONTINUOUSLY — the AoE drain bar — is not
on it and must not be put back on it: a length rewritten from JS is only as
smooth as the loop rewriting it, and 20Hz sampled by an OBS source compositing
at 60fps judders on the stream. That one is handed to the compositor (below).

**The elapsed clock counts in the browser, and its correction is asymmetric.**
`elapsed_s` is log time arriving in whatever steps the uploader's batches
happen to be, so printed directly it stutters — "0:04, 0:04, 0:07" — and a
clock that stutters reads as broken even when the parse behind it is right. A
payload AHEAD of our count is taken as is (a batch we had not seen, or a replay
running faster than real time). A payload a fraction BEHIND it is batch
latency, and following that jitter down is exactly how a clock prints the same
second twice or drops one, so our count stands. Only a payload well behind
(`SNAP_S`) is a different fight, and that one starts the clock again. It stops
when the picture does — a frozen fight whose timer keeps climbing is the one
thing here that would be actively wrong — and it is printed as a clock
(`fmt.clock`: `2:07`, tabular figures) rather than as `2m 7s`.

**A fight ENDS before it commits, and the payload says which** (`ended`,
`build_snapshot`). These are two different moments and the screen used to know
only the second one. Combat stops at `GAP_S` (7s of no damage — where ACT calls
it, within a second); the writer cannot CLOSE the segment for `CLOSE_S` (17s),
because a late kill or death line may still join it. In between, the payload
still carried a fight, so the browser clock — which re-anchors only when
`elapsed_s` CHANGES, and a finished fight's does not — kept counting: ACT had
called the pull and this page was still ticking off seconds. So the server
answers it, from the LOG clock (`now_ts`: the newest line the plugin has sent,
or the replay's cursor — never the wall clock, which a backfilling log would
make a liar). `ended` stops the clock, kills the pulse dot, and moves the
rail's row to `saving`. The fight stays on screen with its numbers until its
record lands, which is the point: those are the seconds everyone is reading.

The exception is `/act end` (`docs/parser.md`), where the raid says the pull is
over: `CLOSE_S` exists because a late kill line can still arrive for a fight
that merely went quiet, and that line settles it. A segment carrying
`Segment.ended_by_cmd` is committed by the very next flush rather than waiting
the 17s out, so the card lands while the meter is still being read.

**The fight's length is damage to damage, live and recorded alike.**
`elapsed_s` was the last EVENT in the open segment, which counted the trailing
heals and cures that the idle window keeps — so the meter read a few seconds
longer than the card it turned into, and its rates read low against their own
parse. It ends at the last damage/avoid line now, which is exactly what
`encounters.Segment.end_ts` does. The trailing events are still COUNTED (see
the `finalize` note in `pipeline/encounters.py`); they just do not extend the
fight. `last_ts` still rides in the payload for the timeline, and `log_ts` is
the log clock the AoE countdowns are read against.

**The meter is exempt from `prefers-reduced-motion`, deliberately.** A bar's
length and a rate's digits are the READING, arriving in steps only because the
uploader batches; snapping them is not a calmer meter, it is a meter lying
about when the fight changed. The reduced-motion rules that used to kill those
transitions were what made the bars jerk. The live pulse dot is decoration and
still goes.

**A row is a RATE, and twelve rows is the meter.** Mid-fight a total only ever
goes up, so it says who has been fighting longest rather than who is doing the
most, and printing both put two numbers of different magnitudes in every row —
which is what a screen meant to be read from three feet away cannot afford.
The healing stack carries the one number no rate can say: cures. Past twelve
bars a meter stops being glanceable and the raid is scrolling, so the tail
folds behind one clickable line — on the meter and on a finished fight's
tables alike (`SortableTable`'s `fold`, which cuts AFTER the sort, because a
table folded by its caller would keep showing the top twelve of the DEFAULT
column no matter what you then sorted by). The stream overlay is the exception
and keeps its hard `max_rows` cap: it has to fit a scene, and nobody watching
a stream can click.

**Only the pull in progress wears the meter, and the moment it ends the middle
column becomes THE PARSE** — `ParseView.jsx`, the identical component
`/zones/:id` is made of, tabs and columns and drilldowns and all. It stays
until the next pull opens. Between pulls the fight shown is the one that just
ended (`showLastRecorded`), with a line above it naming the pull.

It was a cut-down "recap" of its own until 2026-08-07: `RecordedFight.jsx`, a
headline and two rate tables, on the reasoning that between pulls you only
want a glance. You do not. What gets asked in that minute is what crit for
how much, who died and to what, whether the AoE was covered, and who was late
— every one of which was a click away on the other page, in a different table
with different columns. Two shapes for the same fight also meant every column
added to the parse had to be argued about twice. One component, both places:
the dashboard hands it fights and hides the rail (`.workspace.norail` — the
dashboard has its own), the raid page hands it a run and a rail.

**An open drilldown docks the raider list under the dashboard's rail**
(`ParseView`'s `pickerSlot`, `Live.jsx`'s `.dashpicker`). Checking a player is
a request to read their parse, and stacked in one column that parse got a third
of the width it needs, under the list it was picked from and often below the
fold; two of them side by side were worse. The raid page already solved this —
panel open, the fight rail and the condensed picker share the left column and
the parses take the rest — but here the rail belongs to the PAGE and the picker
to a parse three levels down the middle column, so the two cannot be put in one
grid. The parse portals its column into a slot the dashboard renders under its
rail; the middle is left to whatever was opened. One drilldown or a comparison,
no difference — the same rule `.workspace.withpanel` follows.

Two consequences, both of them the price of the dashboard's third column.
Docked, the left column stops being sticky and the rail caps at 44vh: a sticky
column taller than the window pins its top and hides its bottom. And a
drilldown's ability table scrolls inside its card here rather than setting the
width of the column it is in — on the raid page that column is `auto` and the
PAGE scrolls, which it cannot do with the notes standing to the right.

Which leaves the parse needing the RAID REPORT (overheal, time dead, damage
lost dead, engage — four columns the aggregate does not carry) somewhere with
no run to ask about: hence `GET /api/encounters/report?ids=`, the same
`build_for_encounters` the run report is, scoped to the fights in hand and
memoized on the id set. It is a PROP, not a fetch inside the parse, because
its two callers ask different questions — the raid page reads the night's
report once, the dashboard reads the pull that just ended.

**Between pulls nothing goes blank and nothing dims.** The dashboard used to
answer the gap between fights three times, in three shapes: a rail row reading
"Between pulls" in grey with a dead clock beside it, an empty mini panel, and a
`stale` class washing the whole meter out at 72% — that last one because
`stale` meant "no fight in progress" as well as "the uploader has gone quiet".
The result was that the numbers everybody reads hardest, in the half minute
after a kill, were the faintest thing on the screen. Now: `stale` means ONLY
that the uploader has gone quiet; the mini rail is handed the last pull
(`lastFight`, kept for as long as the page is about this night) rather than
null, so it keeps the parse up with its dot dark; and the rail's live row
between pulls is an ELLIPSIS — no name, no clock, no dot — because it is a gap
in the list and the only thing it still has to be is the row the next fight
appears in and the click that gets you back to live.

**The middle column can be switched OFF** (`Parse`, remembered). Dimmed hard
and PAUSED — the payload as it stood when the switch was thrown, countdowns
included — so the mini overlay is the only thing moving on the monitor while
you play. It is a switch about the METER, not about the page: a fight that ends
still lands in the middle as its record, which is what between-pulls looks like
either way.

**The display switches live in the rail head, across from the character name**
(`EncounterTree`'s `headActions`): `Mini`, `Parse`, `Overlay`. They are one
question — which parse is showing where — and the rail head is the only part of
this page that is about the night rather than about one fight. What used to sit
in the bar over the middle column with them has GONE: an "uploader online" pill
that says what the header's ACT light already says in a different shape, and a
`N fights · N lines` readout nobody acts on. The bar is now only what is
happening to the page — a replay running, another character to switch to, the
way out to the full parse — and it does not render at all when there is none.

**It is the PARSE, not a recap.** It carried a headline block of raid rates,
the AoE audit and the death report as well, on the reasoning that "what
happened to that AoE" and "why did the tank die" are the between-pulls
questions. They are not — not on this page. What the dashboard is watched for
is the parse, and three panels stacked under it pushed the parse up the screen
and made getting back to it a scroll. The audit and the death report are what
`/zones/:id` is, one click away on the bar's "Open the full parse", which is
where a question you sit down to ask belongs. What stays beside the fight's
name is its LENGTH, because every figure under it is a rate and a rate whose
denominator is off screen is a number you have to take on trust.

**The dashboard follows the log that is being PLAYED, not the newest one.**
Two EQ2 clients logging at once is two receiving sessions on one account —
`process_batch` files each batch under the character the plugin read off the
file name, which is right — and the page used to take `live[0]`, the session
created last. Open a second client and the dashboard left a raid in progress
for an alt parked in town with ninety lines of chat in it, and then STAYED
there: the only rule for keeping a session was that it still existed.

`liveliest()` picks instead, and `in_combat` is what it picks on, since only
one character can be fighting at a time. Ties and idle rooms fall back to
`last_ingest_ts` (now on `/api/sessions` for exactly this). The following
rules, in order:

- **Never mid-pull.** A session in combat keeps the page, whatever the other
  one is doing. Jumping out of the fight you are in is worse than showing the
  wrong character between them, and it also stops two boxed clients bouncing
  the screen back and forth every poll.
- **Between pulls, the fight wins.** Not in combat here, in combat there — go.
- **A hand-picked character holds it** (`picked`) while its log is still
  moving, and lets go after `QUIET_S` (2 min). Following the action is a
  default, not a cage.
- **The switch chips only list clients that are alive.** The server keeps a
  session `receiving` for `LIVE_IDLE_S` (30 min), which is right for the
  record and wrong for a row of buttons — an evening of alts logging in and
  out left five chips up, four pointing at nothing. Same `QUIET_S` cutoff, and
  a dot on whichever one is fighting.
- **The list is re-read every `FOLLOW_POLL_MS` (6s) while more than one
  session is receiving.** It is the only way to see that the fighting moved to
  the other client; with one session (the normal case) nothing polls at all.

`in_combat` therefore has to mean *being fought right now*: it is no longer
"there is an open segment" but "there is an open segment whose last damage was
inside `GAP_S`" (`LiveState.open_end_ts`, straight off `Segment.end_ts`). The
segment stays open for `CLOSE_S` after that, and a light — or a session
picker — reading combat through the difference is talking about a pull ACT has
already ended.

**The nav owns the raid's state.** The **Live Parser** tab sits LAST in the
header nav, dressed as a state rather than a place: it says "Idle" or "In
Combat" before anyone clicks. The light is answered by `live.in_combat` — a
read of the in-memory tail's open segment, surfaced on `/api/sessions` — which
costs nothing, never turns snapshot building on, and goes dark when log time
falls `LIVE_LAG_S` behind the clock (a plugin that dies mid-fight leaves a
segment only a later batch could close, and a light that stays on forever is a
broken light).

It is GREEN, in three states of one hue: **Off** is the plain tab dress (there
is no parser running to shout about), **Idle** outlines in green with a dim
dot, **In Combat** fills the tab in and lights the dot. Green because the
parser working is the good case — the state that used to go red was the raid
doing exactly what it is supposed to — and colour rather than motion, because
this sits in the corner of the eye for a whole raid night. The tab is also the
site's loudest object while a raid uploads, which is the correct weight: on a
raid night the parser IS what the site is doing.

That left the "Connected to ACT" pill saying the same thing three inches to the
right, so it now appears for ONE state the tab does not carry — **Parsing**, a
log still being chewed — and points at Import, where that log is listed.

**It is a view, and being a view is the whole design.** `_flush` already
computes the open segment and drops it; the dashboard's snapshot is built from
exactly those events, handed to the SSE stream as a `partial`, and stored
nowhere. No DB writes, no entity resolution, no encounter row. That is what
keeps `test_golden_equivalence` true: the record is still the record, and this
is a photograph of it mid-fight. The consequences are stated in the payload
rather than hidden — the fight's name is `provisional_*` until it closes and
arrives again as an `encounter` card, and credit is by NAME (a pet credits its
owner through the subject it already carries) because resolution is the
expensive half of a flush.

What it measures is deliberately the same statistic the recorded page reports:
self-inflicted damage is excluded exactly as `roll_encounter` excludes it, DPS
divides by the fight's elapsed clock, and overheal is the same HP-deficit
reconstruction. A live meter that used different arithmetic would visibly
disagree with itself thirty seconds later.

### Who a name is, without resolving anything (`livemeter.Names`)

Being a view means there is no `EntityResolver`, and for a while that was read
as "grammar is all we get". It is not enough, and one replayed Wuoshi pull
showed every way it fails at once: the pull was titled `Tragedy's unswerving
hammer`, then `Ancient Grovebeast`, and never `Wuoshi`; the raider count read
26 for a raid of 24; and three raiders had no class.

Four separate mistakes, and none of them needed entity resolution to fix — only
knowledge the app already had:

- **A one-word boss reads as a raider.** `Wuoshi` is a single capitalized
  token, so it sat in the bar list and in the raider count, and — being a
  "player" — never entered the pool the fight is named from, which is why the
  title went to the multi-word adds. `refine_known_mobs` is the recorded path's
  answer to exactly this and is a pure function over parsed events, so the live
  path runs it too.
- **A pet reads as a mob.** `Tragedy's unswerving hammer` is multi-word, so
  damage into it counted as damage into an enemy — enough, in the first seconds
  of a pull, to name the fight after somebody's dumbfire. Targets now decompose
  exactly as `EntityResolver.resolve_target` decomposes them, and a possessive
  pet stays its own combatant on the taken side the way `statsroll.taken_key`
  keeps it.
- **A bare dumbfire reads as a raider.** EQ2 writes `Knyi` exactly like a
  person and never prints an owner for it anywhere in the file.
- **`YOU` is not a name.** The logger's own lines say YOU/YOURSELF, so they
  were two rows, and a raid-wide AoE that reached them missed the ≥5-raiders
  anchor by exactly one — on the one log that can see the cast at all.

The knowledge itself is `live.snapshot_context`, read once when the session's
`LiveState` is created (four indexed reads, ~5ms): classes, mobs, players and
bare-named pets, all of it the settled output of parses that already finished.
That is what makes the FIRST second of a pull as accurate as the fortieth —
`refine_known_mobs` over 200 events of an opening wipe knows nothing, and the
raid is looking at the screen right then.

Two orderings in there are load-bearing:

- **Census wins on class, an earlier parse fills the gaps.** Three raiders on
  that pull came back `found=0` from Census and showed no class at all, while
  their own parses had said conjuror, illusionist and guardian for weeks. The
  standing order of authority (`docs/parser.md`) is unchanged — Census still
  wins where it answers.
- **A seeded mob outranks the roster, and only Census vetoes it.** `Dixie` and
  `Mixie` are Emerald Halls nameds that an early parse filed as raiders before
  the behavioural pass caught them, and they still carry that stale player row;
  a roster veto would hand the label back to the adds. The roster is evidence
  about what ONE segment can INFER, not about the seed — without it, refine's
  kill-victim rule reads the eight raiders a boss just killed as eight mobs,
  and the meter empties out at the moment the raid wants to look at it.

#### The stranger problem, and asking Census mid-pull

Everything above is knowledge the app already had, which is exactly its limit.
`snapshot_context` is read ONCE, when the session's `LiveState` is created, and
every row in it is the settled output of a parse that already finished. For the
people you raid with every week that is the right trade and costs four indexed
reads. For anybody else it is empty: stand next to another guild's avatar pulls
to gather data and every name in the meter is one this app has never parsed, so
the whole raid sits there with no class — and on this screen the class is not a
label, it is the bars (`classes.js: barFill` colours damage and healing by
archetype). The answer did arrive, at session CLOSE, when the rebuild ran
`ingest_writer._sync_roster_classes` — which is hours after the pull nobody
could read.

Census does not need a spellbook to answer that. It needs the NAME
(`census/roster.py`), which the log has printed the moment somebody swings. So
the live path asks, and the same cache the recorded path fills answers for both:

- **What is asked about is the built snapshot's unclassed player rows**
  (`_unclassed`), not the batch's names. The snapshot is where every one of
  those words has already been decided — `Names` has ruled the mobs and the
  pets out, `YOU` has become the logger, the list is already cut to
  `MAX_ACTORS` — so nothing is spent on the zone's trash and nothing is asked
  about that the meter would not colour. It rides the existing gate too: no
  dashboard open, no snapshot, no lookup.
- **Never on the ingest thread.** `process_batch` holds `state.lock` for the
  whole of a batch, and `CensusClient` retries a stalled read three times, so
  an HTTP call there stalls the meter with it. The names go to a queue and one
  process-wide worker; `state.lock` is taken only to write the answers back,
  and `get_db()` is thread-local so the worker has its own connection.
- **A name is asked about once a session**, marked when it is QUEUED rather
  than when it is answered — the plugin sends twice a second and the same
  unclassed row is in every payload, so marking on the answer would queue a
  slow lookup twenty more times while it ran.
- **What gets merged is the CACHE, not the call's report.** `roster.resolve`
  asks about the stale names only, so a name the parse path or another
  uploader's session already resolved comes back `found: 0` — it was never
  asked about. Reading the report instead of the table is how a second meter
  sits uncoloured all night beside a first one that has the answer on disk.
- **A failure is not an answer.** `roster.resolve` already declines to cache a
  network error, so the names go back on the table behind a cooldown
  (`LIVE_ROSTER_RETRY_S`) — an outage is retried at that rate rather than at
  the plugin's.
- **A found name is also proof of PERSONHOOD**, so it joins `known_players`.
  That is the second thing the live path was missing about a stranger: one
  segment of a wipe is 200 events in which the boss kills eight people, and
  `refine_known_mobs`'s kill-victim rule reads those eight as mobs.

Nothing publishes from the worker. The class lands on the bars when the next
snapshot is built, which is the next half second — a payload republished with
the same numbers would be a repaint the stream cannot use.

Checked against the back catalogue: over two full raid nights the provisional
label now matches the recorded one on 614 of 640 fights, and every disagreement
left is a 3-to-400-event trash stub where two adds trade the lead or the
recorded path calls it `trash`. Cost is one more pass over the open segment —
the biggest fight in the archive (44k events) builds in 113ms.

Three gates decide whether a snapshot is built at all, and all three are about
not showing a raid that is not happening:

- **Nobody watching, nothing built.** `mark_watched(session_id)` is called by
  each stream poll; without a dashboard open the raid pays nothing.
- **`mode=backfill` is history**, by the plugin's own word for it — an old log
  being caught up must not flash on screen as a pull in progress.
- **`LIVE_LAG_S`** catches the same thing from the other side: log time far
  behind the clock is a replay, which is what `simulate_live.py` does *without*
  `--restamp` (that flag exists so an old log can be replayed as a raid
  happening now, and it is how this is tested by hand).

Reading it from the SSE generator is an **RCU-style pointer swap**: the
producer (a worker thread, under the per-session lock) builds a fresh dict and
assigns the attribute; readers on the event loop take the reference and treat
it as frozen. One atomic store, no reader lock, no copies.

**Live AoE timers** reuse `pipeline/aoes.py`'s definition — a second in which
one enemy ability touched `MIN_TARGETS` players is a cast, or touched ANYONE
at all if the reported-timer list knows the ability (`aoes.anchors`, below) —
importing its constants and its clustering rather than restating them, so the
live rule and the recorded rule cannot drift apart. Two differences, both
deliberate. Nothing filters on name
grammar, because that would drop exactly the bosses worth a countdown (live,
`Venekor` is indistinguishable from a raider by name; the anchor rule is the
real evidence — a raider's green AE hits mobs, so touching five RAIDERS in one
second is a claim only an enemy ability can make). And a sourceless
`X is hit by <Effect>` counts, pooled under `Unknown` the way the recorded tab
pools it: bobby.txt's `Stench of Death` reaches 17 people on a 30s reported
timer, and dropping it would hide the biggest thing on the screen. Only casts
inside the CURRENT fight feed an observed period — the wait between two pulls
is a raid taking a break — so a boss's first cast counts down only when ACT's
reported-timer list knows the ability, and a single cast with no timer at all
is not shown, because a row that can only say "that happened" is noise.

### The timer list decides what a CAST is, not just what earns a row

`RAID_FRACTION` below decides which abilities reach the panel. This decides
what counts as a cast once one has, and the two were entangled: an ability
earned its row by being on ACT's list, and then re-armed its countdown only on
the seconds that reached five raiders. The countdown was therefore anchored to
a second that had nothing to do with when the boss last cast it.

Measured on the 2026-08-09 Vampire Lord Mayong Mistmoore kill in Throne of New
Tunaria, 15m52s, reported timer 37s. `Soul Paralysis` **landed 11 times**; it
reached five or more raiders **three** times. The parse itself was never in
doubt — all 47 log lines parsed and attributed — but the panel saw three casts
598 and 313 seconds apart, and read overdue for most of the fight.

So for an ability the list knows by name, reach stops being evidence:

- **One target is a cast**, and so is a cast that found nothing but a pet
  (`aoes.PET_KINDS`). A pet ANCHORS and is never counted as coverage — `hit`,
  `avoided`, `absorbed` and `blocked_pct` stay statements about raiders.
- **One cast is a row**, where an unlisted ability still needs `MIN_CASTS`. A
  boss's first cast of the night is exactly when a countdown is worth the most,
  and the reported timer is the only one available on it.

Over 60 recent named fights this ADDED 13 rows and removed none: `Mayong's
Touch` (102 casts, 37s reported, 38.8s measured), `Prone to Corruption`,
`Touch of Darkness`, `Reaching Rot` — abilities on the raid's own callout list
that had never once appeared, because they do not reach five people.

**Do not bound how far a cluster may run from its start** (`aoes._cluster`).
The one flaw in hop-by-hop clustering is real — `Stench of Death` ticks every
3s for ~15s on a 23s cycle, and its tail walks into the next cast and merges
them, costing about a third of its casts — and a span bound trades it for a
worse one. `Blanket of Eternal Night` ticks every 6s for **76 seconds**, longer
than its own ~60s cycle, so any span short enough to split Stench chops
Blanket's tail into casts that never happened: 65 casts became 72 and the
measured period fell from 59.8s to 40.3s, which then presented itself as a
"your 60s timer should be 40s" suggestion. Merging is the failure to prefer,
because a merged cast makes a gap LONGER and `observed_period` is built to
survive exactly that, while a split one makes a gap SHORTER and nothing
downstream can tell that from a real timer.

### An audit's threshold is not a panel's (`RAID_FRACTION`)

`MIN_TARGETS` is five people in one second, which is an EQ2 GROUP. That is the
right anchor for `aoes.detect`, whose job is to miss nothing in a tab you
scroll afterwards. It is much too loose for a countdown panel you glance at
mid-pull, and the measurement says so: on the recorded Mayong Mistmoore kill it
drew **ten rows for the three abilities the raid actually calls out** — the
seven extra were add cleaves (`Madness of the Void` off a Libant infiltrator)
and one-off boss spells (`Chaos Anthem`, `Tap Essence`, `Wail of the Banshee`)
that clipped one group and had nothing to count down to.

The two populations do not overlap, which is what makes a threshold safe. Over
eight real raid fights every ability the raid calls out reached **72–100%** of
the raid or carried a reported timer; every piece of clutter reached
**15–43%**. So a row earns its place two ways, and `RAID_FRACTION = 0.6` sits
in the empty middle:

- **ACT's spell-timer list knows the ability** — the raid was TOLD to expect
  it, and tonight's reach is not the evidence. `Soul Paralysis` lands on one
  group in a 21-minute fight and on 17 people in a 3-minute one; a fraction
  gate applied to reported timers would show it in one pull and hide it in the
  next, which is worse than showing it always.
- **or the widest cast reached `RAID_FRACTION` of the raid** — which is what
  keeps a timerless `Overnuke` that hit 24 of 25 on Malkonis D'Morte, the same
  argument as `Stench of Death` above. A fraction rather than a ban on
  timerless rows, for exactly that case.

Denominator is `raid.raiders`, i.e. every name the fight has seen act like a
player, so it runs a little high (32 on a 24-raider Mayong pull, where the real
AoEs still measured 75%). It self-scales, which an absolute count would not: a
six-person group run has no raid-wide AoE by any absolute rule, and by this one
its group hit is 100%.

Casualty worth naming: `Fiery End` off a mutagenic disgorgant (43% reach, 7.5s
*observed* period) no longer shows. Its period is the trash-instances artifact
`aoes.py` documents — several mobs sharing a name read as one casting far too
often — so it is a countdown that was already lying.

### A cast is a moment; a damage shield is a condition (`SUSTAINED_RUN`)

`Caress Feedback` was drawing a countdown on Mayong. It is his damage shield —
it fires when somebody swings at him — so it reaches the raid exactly the way a
cast does, and neither the five-in-a-second anchor nor `RAID_FRACTION` can tell
the two apart. Reach is the wrong question. **How long it goes on** is the
right one: an ability that keeps meeting the raid-wide anchor second after
second is not being cast at the raid, it is a state the raid is standing in.

Median raid-wide seconds per burst, over 60 named fights (288 minutes):

| | median | longest burst |
|---|---|---|
| `Caress Feedback` (D'Lizta) | **36** | 149s |
| `Royal Decree` (Lenya Thex) | **33–38** | 232s |
| `Caress Feedback` (Mayong) | **9** | 29s |
| `Stench of Death` — the widest-reaching real AoE | 3 | 8s |
| `Vortex of Darkness`, `Rumbling of Earth` | 2 | |
| `Blanket of Eternal Night`, `Soul Paralysis`, `Dark Visage`, `Ydalian Bolt`, `Regal Backlash`, `Enthralling Flames` | **1** | 1–3s |

No overlap and no borderline case, so `SUSTAINED_RUN = 6` sits in the empty
middle. A REPORTED timer is exempt, the same way it outranks an observed period
everywhere else — the raid's own list beats any shape argument.

It has to be caught explicitly, because **the clustering actively hides it**.
Six-second gaps turn a shield that never stops into tidy "casts" 19 seconds
apart, and one that pauses while the mob is untargetable into a plausible
55-second timer — a countdown assembled out of somebody's melee windows. The
one shield the old code did drop was an ACCIDENT: 149 unbroken seconds became a
single cluster and fell under `MIN_CASTS`. So the defence was dropping the
shields that never stop and admitting the ones that pause, which is backwards.

The row stays on the recorded AoE tab, marked `shield` and carrying its
`run_s`. It reached the raid and that is what the tab records; what it loses is
the countdown, which it never had anything to count down to.

The recorded AoE tab is otherwise untouched by any of this. It still lists
everything that touched five people, because that is a different question asked
at a different time.

The panel is headed **Spell timers**, not "Raid-wide": what it lists is the
shortlist worth calling out, and "raid-wide" described the anchor rule rather
than the contents.

Each timer row also carries the LAST cast's outcome — how many players ate it
and how many were covered (avoided or absorbed, the same three outcomes
`aoes.detect` reports in the audit afterwards, deduplicated the same way: a
player who ate the cast is not also a player who dodged it). "Is the raid
handling this AoE" is the question the countdown exists to set up, and the
countdown itself is big tabular digits that keep counting UP past due —
"+0:03" reads as a stunned mob, "+0:40" reads as the timer being wrong, and
"due" alone could not say which.

Three more things a row carries, and one that it loses.

- **What it lands AS**, as a pill beside the name (`DtypePill`, exported from
  `AoeTimers.jsx` and drawn identically on the recorded tab). One word, because
  it answers a one-word question: whether the raid can be asked to cover this
  one, and by whom. The pill is the biggest school; a dual-type hit keeps the
  rest on the title. Not colour-coded — twelve schools is more hues than the
  page has to spare.
- **A SUGGESTED timer**, printed and never applied. `aoes.suggest_period`
  offers the measured period when it clears both the 15% the Δ column already
  highlights and three seconds flat, over `SUGGEST_MIN_AGREE = 3` agreeing
  intervals, and never when `several_bodies` explains the disagreement as
  several mobs sharing a name. The countdown itself stays on the configured
  number: a countdown that silently uses a different timer from everybody
  else's is worse than one that is wrong the same way as theirs. On 60 recent
  named fights it fires 9 times — `Soul Paralysis` 37s→43.6s over 42 agreeing
  intervals, `Stench of Death` 30s→23s, `Dark Visage` 28s→44.3s. It is shown on
  the live parse and the mini rail and NOT on the stream overlay, because it is
  an errand (go and edit an ACT config) and nobody watching a stream can run
  it.
- **Two HAND MARKS**, as a stacked pair of pills per row (`AoeTimers.
  MarkPills`), on the full-width live panel and the recorded AoE tab — the two
  surfaces anybody can click. **JOUST** (`lib/joust.js`) is the thing on this
  panel a log cannot supply: running out of an AoE and standing in it look
  identical in a log. **MINI** (`lib/minipin.js`) says this ability belongs on
  the mini parse and the stream overlay, overriding the damage cut those panels
  guess with. Both are keyed by ability NAME, not by source or fight, because
  both are properties of the ability and a mark has to outlive the pull it was
  made on; both run through one implementation (`lib/marks.js`).

  **They are on the ACCOUNT** (`user_marks`, schema v35, `backend/marks.py`),
  and they were not — localStorage was the original call and the argument for
  it was real: a mark is a note about how somebody plays, it is worth nothing to
  the server, and the alternative is a settings table and a round trip in front
  of a countdown. What broke it is that the same panels are now drawn in THREE
  browsers. An OBS source inheriting nothing was written off, because the
  ACT-list defaults are a defensible floor for a stream and nobody reads their
  own stream. EQ2's in-game window (`/ingame/<token>`, v34) is a different
  browser too, and that one is read by the person who did the marking, mid-pull,
  beside their own hotbars — so it opened jousting whatever their ACT list
  happened to list and nothing they had said by hand. An account is the only
  thing the three screens share.

  **The round trip is still not in front of the countdown**, which is the part
  worth keeping from the old design. localStorage is now a CACHE: module state
  is seeded from it synchronously at import, so the first paint has last
  night's marks with nothing awaited, and the account's answer arrives
  afterwards and corrects it. A clicked pill is applied locally and pushed in
  the background — a failed push costs the server's copy of one mark, never the
  pill, so signed out, offline and a server down mid-raid all still mark.

  **The two token screens are handed the marks with their config**
  (`GET /api/overlay/<token>`), on the poll they already run to pick up a
  setting changed mid-raid. Neither holds a cookie, so neither could ask
  `/api/marks` for itself; riding along means a pill toggled on the dashboard
  reaches the game window on the next tick, and it costs one query on a request
  that is already resolving the token. What crosses is a set of ability names —
  the same kind of game fact as the countdowns already on screen — with no
  account, character or raid attached.

  **The adoption gets one chance and is per ability.** Every mark made before
  v35 is in one machine's localStorage, which is the only place it exists and
  somewhere the server can never reach, so the first signed-in read merges
  rather than replaces (`syncMarks`): an ability the account has no answer for
  takes this browser's, one it does have keeps the account's. Wholesale either
  way is wrong — the server's would have silently binned a night's marking on
  deploy day, the browser's would resurrect a mark deliberately turned off on
  another machine.

  They are PILLS and not checkboxes, and the reason generalises: a tick is a
  control that says what it does only in its tooltip, and these rows are read
  while somebody is fighting, when nobody hovers anything. A word says what it
  is when it is off and says it is on by being lit — one thing to learn instead
  of two. They stack because the cost on this panel is horizontal (the dock and
  the overlay are as narrow as the game leaves them and the ability name is
  what has to survive), and two pills at that size fit inside the row height one
  line of countdown digits already asks for.

  **Both default ON for an ability ACT's list knows** (`marks.actListed`, off
  `reported_s`), which is why a mark is an ANSWER per ability and not a set of
  names: a set can only say "these are on", and a good default needs to be
  overrulable downwards. Three states — yes, no, nothing said — and nothing
  said takes the default. The list is the raid's own shortlist: somebody typed
  those entries in because the raid calls those abilities out, you joust the
  things you were told to expect, and the things you were told to expect are
  the ones worth a slot beside the game. It is what an account that has never
  marked anything gets, and what the two token screens got for ALL of anything
  before v35 — a different browser inherited no marks, so the stream drew the
  automatic damage cut and no burn window at all. Clicking a pill
  stores the opposite of what it is SHOWING, so the first click on a
  defaulted-on row turns it off, exactly as it looks like it should.
- **And it LEAVES when it has been overdue too long.** Past due is information,
  right up until it stops telling anybody when anything is due; the panel is a
  shortlist. Nothing needs un-dropping, because every snapshot is rebuilt from
  the fight's events rather than accumulated — the row returns on its own the
  moment the ability lands again. The browser re-applies the same lines against
  its own clock (`aoe_drop_s` and `aoe_missed_s` in the payload), since that
  clock runs ahead of the payload by design and the row would otherwise sit
  there counting up for a poll.

  **How long depends on whether it named a second**, and the two are different
  questions. A row WITH a period claimed the next few seconds, so once it is
  `MISSED_S` = **15s** past due that claim is simply wrong — the mob was
  stunned, or the cast landed and every single person blocked or absorbed it so
  nothing printed to detect on, or the timer is off. A row with NO period has
  nothing to be late for and only stops being recent, which takes
  `OVERDUE_DROP_S` = 60s from its last cast.

  **15s and not 60 because of what the long fuse did to the BURN WINDOW.** The
  window belongs to the SOONEST jousted cast — and a cast due thirty seconds
  AGO is soonest by a mile, so it won that comparison against every real cast
  behind it. Vampire Lord Mayong Mistmoore's `Soul Paralysis` gets skipped a
  minute or two into the fight, and the one number a raid acts on then read
  `+0:47`, counting up, through a stretch they could have been burning in.
  `nextJoust` now skips a cast past `missedS` outright: the window moves to the
  next jousted ability, or there is none, which is also an answer.

**A row with no timer leaves on the same line, measured from its LAST CAST**,
and that was the half of the rule that was missing. A row earns its place with
two casts even when nothing repeats (`observed_period` needs two agreeing gaps,
so three casts), and a row with no period has nothing to be late for — so
nothing expired it and it held its slot for the rest of the pull. An avatar is
where that stops being theoretical: several of its raid-wide abilities do not
repeat on a clock at all (`Stealth Assault`, `Mischievous Bombardment` — two
casts, no agreeing gap, no entry in ACT's list), so a five-row panel was two
rows of countdown and three rows saying `2×` forever. What a row with no timer
has to say is that this just happened, so it says it for as long as that is
true.

That mattered where it costs the most. The dashboard's panel is a page you can
scroll; the dock and the stream overlay draw the METER UNDERNEATH the timers in
a scene of fixed height, so every permanent row there is a raider pushed off the
bottom — people simply vanished from the parse mid-pull. The expiry is the fix
for why those rows existed; the compact panel takes the belt as well
(`AoeTimers: miniTimers`), because a fixed scene cannot afford to find out it
was wrong: rows with no period are dropped while the fight is RUNNING (once it
ends every row loses its countdown and they all belong again), and what is left
is capped at `MINI_TIMER_ROWS` = 3, cut by DAMAGE and drawn in the panel's own
first-cast order ("Nothing moves that does not have to", below — an earlier cut
took the four soonest-due, which re-sorted the dock on every re-arm). It is the
same trade as the meter's
own `max_rows` on the overlay against the dashboard's fold — nobody watching a
stream can click, and nobody mid-pull can scroll a dock.

A compact ROW is cut the same way, and by the same test: does this change what
somebody does in the next few seconds? The source, the hit/blocked split, the
dtype pill and the `measured`/`timer`/`seen` word all fail it and all go. The
last one is worth naming because it is otherwise the obvious thing to keep — it
says where the countdown's number came from, which is provenance rather than
news, so it is something you look up once. It stays on the cell's title
(`PERIOD_NOTE`, with the period itself) and on the full panel, which is where a
timer is actually worked out; on a 244px strip with the game behind it, those
eight characters are width both the ability's name and the digits want.

**The MINI mark decides eligibility; the cap still decides capacity.** Damage
is a decent guess at which three AoEs matter and only ever a guess — the one
somebody needs on screen is the one they have to MOVE for, which is not a
quantity in the log. So a third cut goes in FRONT of the other two
(`lib/minipin.js`): an ability is eligible for these panels if ACT's list knows
it, unless somebody has said otherwise. Everything else here got in because the
site DETECTED it reaching the raid, which is a fine reason to record it on the
audit tab and a poor reason to spend a slot beside the game on it.

Eligibility and capacity stay separate on purpose. A mark says what MATTERS;
it does not get to say how many rows fit, because the scene is fixed however
strongly somebody feels about a sixth countdown — the raider pushed off the
bottom is the cost either way. So the mark's real work is the two exceptions:
the listed ability that clutters the strip, and the unlisted one — a new mob,
an overnuke nobody has an entry for — that has to be on it.

**The burn window** is the last row and the only one that is not an ability.
Once anything counts as jousted, the soonest such cast owns a row that reads
the same seconds the other way round: not "the AoE lands in 24s" but "you have
24 seconds in melee", which is the number a raid actually calls out. It takes
its own colour (`--joust`, teal — the drain is already amber and overdue is
already red, and this row is neither a reading nor a problem), and inside
`JOUST_WARN_S` = 5 it says **JOUST** in the clear, flashing six times over
three seconds and then holding. A few times, not forever: three seconds catches
an eye that was on the game, and a light blinking for the rest of the window is
something people learn to stop seeing. Under `prefers-reduced-motion` the blink
is REPLACED rather than removed — the word takes a solid danger-coloured block
— because reduced motion is a request for less movement, not for less warning.

**The drain bar belongs to the COMPOSITOR, and the countdown stops when the
fight does.** Both halves of this were caught on somebody's Twitch stream
rather than on a dev box, which is the environment that makes them visible.

The bar and the digits used to run the same way: the shared 20Hz ticker set
state, React re-rendered the list, and the fill's `width` was rewritten twenty
times a second. Two separate things jerked.

- **The payload re-anchored the clock flat.** The countdowns are in log time and
  the panel anchored on `log_ts` every time a payload arrived. But `log_ts` is
  the newest line the plugin has SENT, so it trails the log clock by however
  long that batch took to reach the browser, and that varies — so every couple
  of seconds the countdown jumped BACKWARD by a fraction of a second and drained
  forward again. Same disease the elapsed clock was already inoculated against,
  same cure: `useLogClock` (`lib/smooth.js`) predicts forward and takes a
  payload only when it is AHEAD, or `SNAP_S` behind (a different fight). It is
  also why the digits sometimes printed a second twice.
- **20Hz is not a frame rate.** An OBS browser source composites at the scene's
  rate, typically 60fps, so a length rewritten every 50ms advances in 3-frame,
  4-frame, 3-frame steps — judder, from a value that is perfectly correct. So
  the bar is now a CSS animation (`@keyframes aoedrain`, `transform: scaleX()`)
  running over one period and SEEKED to where the fight is with a negative
  `animation-delay`. The seek is taken once, at mount, and the element is keyed
  on `next_due_ts`: a genuinely new cast remounts and re-seeks it, and nothing
  else touches it, because rewriting the delay on a running animation re-seeks
  it — a jump per payload, the artifact this is fixing. Between casts JS does
  not touch the bar at all, and it cannot judder however busy the tab is.

The digits stay on the ticker — they change once a second, so a tick only has
to be fine enough to catch the crossing — but they now re-render only when what
they SAY changes, rather than twenty times a second for the whole list.

And the panel takes `running`, the flag the elapsed clock takes, off the same
`ended`/`stale` pair: a pull that ACT has called has no next cast, so a bar
draining toward one is counting down to something that will never happen. The
rows stay, because which AoEs went off and how many they hit is worth reading
after the pull — they say how many times it fired instead of when the next one
is due. Nothing about this is overlay-specific: the mini parse and the stream
overlay are one component, so the dock beside the game gets the same fix.

Cost: one pure-Python pass over the open fight per batch. Measured against the
biggest fight in bobby.txt — 46,521 events over 408 seconds — that is 65ms,
with a `SNAPSHOT_MIN_S` floor so a client sending faster than the stream polls
cannot spin on it.

**Max hit is the one total a rate cannot stand in for.** Everything else on a
meter row is a rate on purpose (above), and that rule has exactly one
exception: 3M in a single nuke and 3M of DoT ticks are the same DPS, and only
one of them is what the raid is asking about. So `livemeter` carries a
`max_hit` and a `max_heal` per actor — the biggest single line of the fight so
far, credited to the SOURCE, so a pet's crit lands on its owner's row the way
its damage does and a self-hit is not a hit. It rides beside the rate on the
meter, in front of it on the mini parse, and it is a sortable column on the
Damage and Healing tabs (`stats.js`: the rollers store a max per ABILITY, so an
actor's is the max over theirs) and on Compare — where an imported ACT
screenshot already carried one, so the two are computed alike.

### The timer is LEARNED, and a reuse debuff moves it

Two changes to where a countdown's number comes from, and they only work
together: the site measures its own timers from every raid on a mob, and it
measures what `Traumatic Swipe` does to them. Either one alone is wrong.

#### Tracking the debuff costs nothing (`refdata/reuse_debuffs.json`)

`Traumatic Swipe` is a Rogue Strength-line AA: *decreases Ability Reuse Speed
of target by 50%*, **recast 30s, duration 30s**. Duration equal to recast means
one rogue holds it at 100% uptime and two make it permanent — which is why a
fight anybody is pressing it in usually has no clean cycle left in it at all.

It is visible for free, and this is the part that makes the whole feature
possible:

```
Klebb's Traumatic Swipe hits Mayong Mistmoore for 3,431 disease damage.
```

A damage line from a player onto a mob. So the audit and the live path both
already have it, from whoever cast it — no cast line is involved, which matters
because a third-person cast line is exactly what this parser drops (Open:
"Third-person cast lines are dropped except the curated ones"). The registry
holds only which abilities open a window and how long it stays open; both
numbers come off the ability's wiki infobox, which is why `gamewiki` now
captures `duration` as well as `recast`.

Two things the log cannot say, stated rather than papered over: it prints
nothing when a hostile debuff FADES (hence the duration), and it says nothing
about a mob resisting the debuff while eating the hit. So a window means "it was
applied", never "it was working" — and the difference is precisely what the
verdict below is measuring.

**What it landed ON is the whole test, and the SOURCE side must not be part of
it.** A registry entry is a player ability by definition — no mob casts
`Traumatic Swipe` — and the source cannot carry a test anyway, because another
raider's ability line parses to `Subject('Tezen', 'unknown')`: a bare possessive
name is exactly what the parser cannot classify without the roster. A first cut
of the live path required `unit == "player"` there, which matched the LOGGER and
nobody else — the one person in the raid who usually is not the rogue pressing
it. Today's avatar was swiped 35 times and the panel saw none of them. The audit
path was unaffected (it reads resolved entities, where `player` really means
player), so the two paths disagreed silently; a replay of the fight is what
turned it up.

#### A cycle belongs to the state at the cast that STARTED it

The obvious model — how much of the gap the debuff covered — was built first and
does not separate the populations. On a 20-minute Mayong kill with the debuff up
about three quarters of the time, `Blanket of Eternal Night` ran 60s and 77s
cycles side by side, and the covered fraction of the 60s ones (0.62, 0.80) sat
*inside* the range of the 77s ones (0.60–0.97). No threshold on coverage can cut
that.

Classified at the cast, the same fight gives **57 / 60 / 60 / 58 against ~77** —
the split the eye already sees in the gaps. The mob takes its recast from what
is on it when it casts; a debuff landing halfway through does not retune one
already running. `aoes.split_cycles` owns that rule and the audit, the live
panel and the rollup all call it.

#### What it actually does, measured (`pipeline/aoelearn.py`)

The tooltip says -50% reuse speed. It does not mean ×1.5, and it does not apply
to everything. Measured against clean cycles of the SAME ability:

| | clean | swiped | |
|---|---|---|---|
| `Soul Paralysis` (Mayong) | 43.6s | 57.5s | **×1.32** |
| `Mayong's Touch` | 38.8s | 51.3s | **×1.32** |
| `Blanket of Eternal Night` | 59.8s | 77.2s | **×1.29** |
| `Enthralling Flames` (Enynti) | 29.3s | 37.5s | **×1.28** |
| `Cloud of Torpor` (Chel'Drak) | 50.5s | 64.9s | **×1.29** |
| `Titanic Stomp` (Chel'Drak) | 29.2s | 29.2s | ×1.00 |
| `Searing Rot` (Treyloth) | 20.1s | 20.0s | ×1.00 |

Two clumps with nothing between them, so `AFFECTED_AT = 1.15` and
`IMMUNE_UNDER = 1.10` sit in the empty middle and a row between them stays
**unknown** rather than being forced to a verdict.

The magnitude is therefore LEARNED per (mob, ability) and never assumed. The
Avatar of Mischief is the case that proves it, and it took the clean cycles from
three attempts to settle:

| | clean | swiped | |
|---|---|---|---|
| `Wave of Sophistry` | 48.5s | 71.2s | ×1.47 |
| `Whimsical Oscillation` | 58.5s | 79.0s | ×1.35 |
| `Whirling Bladestorm` | 53.8s | 55.6s | **×1.03 — immune** |

One debuff, one fight, three abilities, and one of them does not move. Nothing
derived from the debuff alone could have produced that, and nothing that
adjusted every row by a global factor would have got `Whirling Bladestorm`
right. Note also what the clean column says about the other two: read against
ACT's 45 and 50 the swiped numbers look like 1.6× config errors, and they are
not — the mob's own timers are 48.5s and 58.5s and the rest was the debuff.
Guessing either way would have been wrong; measuring both sides was not.

#### The bar draws the difference (`AoeTimers.jsx: NormalMark`)

**One span, decided before the countdown starts, and it never changes length.**
A swiped row counts the stretched number from its first second and puts a
**tick where the un-slowed timer would have fired**. A cast landing ON the tick
says this ability is immune; one landing at the END says the stretch is real.
Both readings are available at a glance, all fight, without the bar moving.

Which number the bar runs to, and which evidence each decision is allowed:

- **Measured to stretch** — `base × factor`, the ability's own measured ratio.
- **Measured not to move** — the base timer, no mark. Nothing is added.
- **Not known either way** — still the stretched number, using this ability's
  own ratio when it has one and the median of the confirmed rows
  (`typical_factor`, ×1.29 today) when it does not. Weaker evidence than the
  verdict asks for, deliberately: **the verdict decides what we CLAIM, the bar
  only decides where the drain ends**, and the tick puts the other number on
  screen anyway.

The first build did this differently and it was wrong in a way worth recording,
because the mistake is not specific to AoE timers. An unconfirmed row planned
the NORMAL timer and then grew past it toward the estimate — "never assume a
stretch nobody measured", which reads well and fails in use. Two things broke:
the bar resized mid-drain, so a length meant one thing at 0:30 and another at
0:10; and the digits went overdue at the normal mark, so a cast that was never
late opened at **"+0:24"**. The correct reading was available the whole time and
the panel was choosing not to show it, in order to be conservative about a claim
nobody had asked it to make. The tick carries everything the growth was trying
to say, and it holds still.

There is no pill. An earlier build badged every debuffed row (`swiped ×1.31`,
`swiped?`, a dim `swiped` for immune) and it went, for a reason that applies to
the next thing that wants a place here: **a word on this panel has to change
what somebody does in the next few seconds.** That one restated the bar in text
— the countdown was already the adjusted number, the tick already the
un-adjusted one — and spent a raider's row doing it. It lives on the tick's
`title` now, which is where something you might want to look up belongs.

#### Nothing moves that does not have to

The panel is read by somebody who is fighting at the same time, so the cost of
motion is not aesthetic: **you lose your place.** Three rules fall out, and they
outrank being clever about what is most urgent.

- **Row order is fixed by FIRST CAST** (`livemeter._live_aoes`), not by
  soonest-due. Soonest-due is the obvious order and it reshuffles the list on
  every re-arm — the thing you are tracking is somewhere else each time you look
  back. A first cast cannot un-happen, so a row appears at the bottom when its
  ability first goes off and holds that slot until it expires. The accepted cost
  is that the next cast due is no longer the top row: the panel is read by
  POSITION rather than by rank, and a position can be learned once where a rank
  has to be re-read every glance.
- **The compact panel keeps three** (`MINI_TIMER_ROWS`), down from four, and
  picks them **by damage** — a fixed scene with room for three has to spend them
  on what the raid is actually fighting. Cumulative damage only grows, so the
  choice settles inside the first minute. *Which* three is by damage; *where*
  they sit is still first-cast order, so a swap changes at most one row, in
  place, and the two that stayed do not move.
- **No damage-type pill in the compact panel.** What an AoE lands AS is a
  question you ask after the pull, working out who can be asked to cover it. The
  full panel and the recorded tab keep it.

**And the one exception, which proves the rule: the row flashes red and says
`HIT!` when the cast lands** (`justHit`, `HIT_FLASH_S`). Everything above holds
still because movement with no news costs you your place; this moves because it
IS the news. A cast has landed and the countdown it just reset is about to look
identical to one that has been running a while — on a bar that is nearly full
either way, "it fired" and "you looked away for twenty seconds" are the same
picture. The flash is the difference.

Three details, each of which was a choice against the obvious one:

- **One pulse, not a strobe** — up fast, then decay to nothing. That shape is
  already a cross-fade, so it degrades correctly under reduced motion rather
  than needing to be switched off. A countdown panel that stopped reporting
  landings under reduced motion would be withholding a reading, not sparing
  anybody an effect.
- **NOT seeked, unlike the drain.** The screen sees a hit about a second after
  the log records it, so seeking the animation to the log stamp would drop it
  into the middle of its own decay and hand everybody a dim smear. A landing
  announced a beat late is worth more than one announced faintly on time.
- **Derived from `last_cast_ts` on every render, never remembered.** No per-row
  refs, nothing to reset between pulls, and the earliest anybody can know a cast
  happened is the payload carrying it — so it fires on that render and no later.
  `printed()` includes the flash window, or a landing announced for a second
  stays announced until a digit happens to change.

#### Timers are crowdsourced, and that is only safe now (`aoe_cycles`, v33)

Every watched recast is stored — one row per interval, tagged with the state at
the cast that started it — and the timer is derived from all of them, site-wide.
The uploaded ACT list is where a timer STARTS, not where it ends: `Soul
Paralysis` measures 43.6s over 42 clean intervals across 6 fights against the
list's 37, and a countdown that keeps insisting on 37 is not being cautious.
Order of authority is `learned` > `reported` > this pull (`aoelearn.timer_for`).

Adoption needs `MIN_AGREE = 6` agreeing CLEAN intervals across `MIN_FIGHTS = 2`
distinct fights, and never when several mobs sharing one name explains the
number better (`aoes.several_bodies`, next section). Observations are stored
rather than the conclusion, so a threshold can change without re-reading a year
of logs, a re-parsed fight replaces its own rows instead of double-counting, and
a learned number that looks wrong can be taken apart into the cycles behind it.

**A FIGHT IS A PULL, NOT AN ENCOUNTER ROW** (`aoelearn.pull_keys`). Two raiders
who both upload the same raid produce two encounters of every pull in it, and
counting those as two fights let ONE night satisfy `MIN_FIGHTS` by itself —
exactly the anecdote the gate exists to refuse. `encounters.dup_of` cannot
answer this and should not try: it is one character's overlapping FILES, and
merging two players' parses would break the one thing a zone run is
(`docs/zoneruns.md`). So the notion is derived here, where it is needed.

**Identity is OVERLAP, not start time**: same mob name, and time windows
overlapping by more than half the shorter fight. Measured over the corpus, 247
pairs of same-named encounters from different characters overlap by more than
half and 1,424 overlap by less — the same pull and adjacent pulls of the same
trash, with nothing in between. A start-time rule was tried first and gets 92%
of them at 15s, but the 19 pairs it misses have **100%** overlap: a raider who
engaged late has a shorter encounter sitting entirely inside somebody else's,
which is the one case a start delta is blind to. Site-wide this collapses 5,034
named encounters to 4,773 real pulls.

Worth recording that fixing it changed **no adopted timer today** — all 21 have
two genuinely distinct pulls behind them. It is a gate that had stopped being
able to fail, not a number that was wrong.

**Pooled site-wide**, and that is a reading of the sharing rules rather than an
exception to them: a mob's recast is a fact about the GAME, the same kind of
thing as `zone_eras.json` or an item's stats. The rows carry no raider, no
roster, no parse and no run — there is nothing in them to gate.

Why it is safe to ADOPT a measured number now, when the same measurement could
previously only be OFFERED: before the debuff was accounted for, "observed
disagrees with reported" had two explanations and no way to choose. A clean
cycle has no such ambiguity left. `suggest_period` still exists and is still
only ever printed — it is a different errand, "go and edit your ACT config so
the rest of the raid sees what you see" — and it now takes CLEAN cycles only.
That is a bug fix: today's avatar had six agreeing gaps at 72.3s against ACT's
45 and would have proposed the edit, off a fight somebody else's brigand
debuffed end to end.

#### A mob that splits is a SPECIAL CASE, and is written down (`split_mobs.json`)

**A timer is per (MOB, ability), everywhere it exists.** The cycle rows, the
derived answer and `timer_for` are all keyed that way, so two mobs with
DIFFERENT names casting one AoE learn one timer each and get one countdown row
each — seven mobs cast `Faith Strike` in this corpus and there are seven
separate measurements, from 9.5s to 15s, none of them pooled. Only ACT's list is
keyed by ability alone, because that is ACT's file format; a shared entry is
where both timers START, not where either ends.

What breaks that is one name on SEVERAL BODIES, and the case is **The Emerald
Halls rumbler**: The Segmented Rumbler splits into two Bisected on death and
each of those into three Trisected, so up to six bodies wear one name, each on
its own 50s recast. The gaps between their casts measure the superposition, not
a timer. Two halves alternating read as one mob on 28.7s — with 21 agreeing
CLEAN intervals across 4 uploads behind it, past every gate — and this site had
ADOPTED that number and was offering it as a config edit. More evidence makes
this failure worse rather than better, because every fight counts the same two
halves.

So it is named in `refdata/split_mobs.json`, reference data about the game
beside `zone_eras.json` and `reuse_debuffs.json`. A name in there is never
learned from, never suggested from, and gets **no live countdown at all** —
there is no number that would say when the next cast lands, and a bar that is
wrong on nearly every cast while looking exactly like the bars either side of it
that are right is worse than no bar. The row stays, says how many times the
ability fired and still flashes on the landing, which is how a damage shield is
treated and for the same reason. It is also the only reason that works for an
ability no timer list has heard of, which is most of the point: `Engulfing Maw`
is on nobody's list.

**HOW MANY BODIES A NAME HAS IS GAME KNOWLEDGE, AND IS NOT INFERRED FROM THE
SHAPE OF A PARSE.** That was tried, in one commit, and it is worth writing down
because it looks so reasonable: "a measurement well under the ACT timer, from a
source that is not the fight's named, might be two mobs". It takes `Ancient
Grovebeast`'s `Tremerous Stomp` (33.6s against 40) with it, and only one
grovebeast is ever up. `is_named` is set from the ENCOUNTER's headline name, so
every add and every second boss in a room fails it however singular it is — and
a mis-typed ACT entry reads exactly like two mobs anyway. Most mobs sharing a
name never overlap their AoEs; the splitter is the exception, and an exception
belongs in a file.

The one inference kept is the pre-existing `aoes._instances_hint`, and it
survives because it is a SIGNATURE rather than a direction: the measurement is a
clean whole fraction of the ACT timer, to inside 20%, which is what N mobs on
one timer look like and is not what a wrong config entry looks like. It says
which N, it withholds the learned timer and the suggestion, and it does NOT take
a countdown away — it is computed off this pull's own moving number, so a row
gated on it would gain and lose a countdown mid-fight. Six pairs in the corpus
sit there, all of them adds that really do come in packs (`a maven of wisdom` at
9.5s against ACT's 20).

A NAMED is exempt from `instances` (one boss is one body, which is why
`Chel'Drak`'s `Titanic Stomp` at 29.2s against ACT's 35 is adopted) and is NOT
exempt from the file — a splitter is exactly the mob a fight gets named after.

The verdict is read at DERIVE time and never stored on a cycle row — the whole
point of `aoe_cycles` keeping observations instead of conclusions. Naming a new
splitter re-decides every fight the site already holds, with no reparse and no
`PARSE_VERSION` bump.

#### The reflect window — a duration, not a period (`livemeter._live_reflect`)

Every row above is anchored on a cast and counts toward the next one. This one
is anchored on a mob entering a STATE and counts toward it leaving. It shares
the drain bar and nothing else: no ability, no reach, no `RAID_FRACTION`, no
period to measure, and — this is the part that shapes everything — no cast line.

**The mechanic announces itself nowhere.** Checked against every non-damage line
at all three window starts on six Treyloth kills: no emote, no buff, no
`X begins to cast`. The only evidence a window has opened is a raider being
denied — `<caster> tries to <verb> <mob> with <spell>, but <mob> reflects` — so
the row cannot exist until somebody has already paid for it. That cost is
bounded and small, and measuring it is what justified the feature: of the 1,073
casts eaten across those 18 windows, **55 (5%) landed in the trigger second
itself**. The row is for the other 95%. Nothing predicts the next window and the
row does not pretend to; what it says is how long the CURRENT one has left.

**The duration is the clustering rule** (`aoes.reflect_bursts`), which is what
makes this simpler than `_cluster` and not merely different. Everywhere else the
gap between casts is the unknown being measured, so ticks are merged by a
threshold that is a guess. Here the duration is the curated fact and the gap
between windows is the accident, so a stamp belongs to the open window if it
falls within `window_s` of that window's START. A window can therefore never be
reported longer than the mechanic is. Tuning a merge gap instead failed both
ways in testing: 8s split real windows into spurious one-second fragments, 12s
risked welding two together on a mob that reflects more often.

`REFLECT_EDGE_S` (2s) is slack on **membership only, never on the bar**. A log
stamps whole seconds, so a 30s state entered at t=0.4 prints its last deny at
+31 having lasted 30.5 — the same quantization `SUGGEST_MIN_DELTA_S` exists for.
Across 18 measured windows the last deny lands at +28 to +31, and taking the
duration literally split one of the 18. The drain still runs to the documented
number, because that is what the raid is being told. The tally uses the burst's
boundary and not the bar's: two rules for one boundary is how a count and a
countdown come to disagree.

**WHICH MOBS GET A ROW IS A HUMAN'S CALL** (`refdata/reflect_windows.json`), on
the ladder `docs/census-abilities.md` sets for a pet or proc label — measurement
nominates, a person rules, an unlisted mob gets nothing rather than a guess. The
corpus has **nine** mobs that reflect something and the severity spans two
orders of importance. Normalized against the health pools in
`census_char_snapshots`, reflected damage taken in one window:

| | Treyloth D'Kulvith | Vampire Lord Mayong Mistmoore |
|---|---|---|
| median | 16.2% of the caster's health | 9.7% |
| p90 | **78.6%** | 22.7% |
| worst | 96.8% | 26.1% |
| windows costing >50% of a health bar | 3 of 16 | **0 of 37** |

Treyloth earns a row; Mayong does not, and the raid's own behaviour agrees —
they call one and ignore the other. **This distinction is invisible in absolute
damage**, which is why the allowlist is not a detector and why a severity
threshold was not attempted instead. Median reflected hit 650 vs 560, max 11,557
vs 12,516, return ratio 26.3% vs 21.0%: on every raw measure the two mobs look
alike. Two measures actively point the wrong way — raid-wide incoming damage
during Treyloth's windows is 0.91x the rest of the fight (his windows are calmer
than average) against Mayong's 1.38x, and heavy casters take 31% of their deaths
in Treyloth's windows versus 39% for light casters, while on Mayong it is 42%
against 20%. The denominator was the whole thing: 6,237 damage is unremarkable
until you know the median health pool in that raid is 10,922.

Two further findings the row is built on. The reflect returns the spell to **its
caster** — 113 of 115 pairings — which is why `damage` is paired on (ability,
caster) rather than summed from the mob's output during the window; the boss is
doing plenty else in those 30 seconds and summing it reads far scarier than the
truth. And the reflect is a **chance, roughly 50%**, not a wall: 62 reflected
against 55 landed on single-hit nukes. So the row says *reflect*, never *your
spells will be reflected*, and the window's end is not observable from the log
— casts keep landing throughout — which is exactly why the duration has to be
curated rather than watched for.

**It leads the panel and is exempt from `MAX_AOES`**, the one place a row jumps
the queue. The "rows do not move" rule protects rows somebody learns by position
over a whole fight; this one lives 30 seconds and then is gone, so there is no
position to learn, and while it is up it is the only row on the panel that is
about right now. It holds its slot for `REFLECT_CLEAR_S` after ending and says
CLEAR rather than vanishing at 0:00 — every other countdown here counts toward
something happening, this one counts toward something STOPPING, and that moment
is the only thing anybody is waiting for. Red while it runs and green when it
clears, which is the opposite polarity to `.aoerow.due` immediately above it in
the stylesheet: there red means a cast is LATE and something is wrong, here it
means the mechanic is working exactly as intended and you should stop casting.
Same colour, opposite cause, which is why the row spends its width on words.

**Only the window that has already STARTED is reported.** Live ingest cannot
produce anything else — events arrive up to now and no further — but replay can
(`replaybus`, `simulate_live.py` without `--restamp`), where the whole fight's
events exist and a cursor moves through them. Taking the last burst outright put
the third window's countdown on screen from the pull timer, draining five
minutes toward a mechanic that had not fired.

### The mini parse, and the two places it is drawn (`MiniParse.jsx`)

The dashboard is a second-monitor page and assumes it owns that monitor. Often
it does not — the game is there, and what is wanted is ACT's mini overlays: a
strip saying what is due and who is on top, with the rest of the panel left to
EverQuest. So the **Mini** switch in the dashboard bar docks a condensed copy
of the timers and the parse flush against one edge of the window.

Condensed **horizontally**, which is the whole constraint. Vertical space on a
1440p panel is free and every pixel of width is taken from the game, so the
rail is 244px and what survives a row is the rank, the name, the class, the
rate and the max hit; the deaths badge, the cures column, the AoE source and the
hit/blocked split all go, and the fold goes with them — nobody clicks "12 more"
mid-pull, so it is a hard ten rows.

The class survives as the **short form the raid says out loud** (`classShort`:
`SK`, `Zerker`, `Illy`), because the bar's hue is the ARCHETYPE and four hues
cannot separate six fighters — "which tank is that" needs the word. Full class
names do not fit at 244px: `Shadowknight` is wider than the name it captions.
The name ellipsizes before the class does — a clipped name is still the row you
were looking for, a clipped class is not — and both numeric columns are named
by a head over the stack, once for ten rows, since an unlabelled second figure
beside a rate is two numbers and people read the wrong one.

**The stream overlay draws the same component.** A strip beside the game and a
strip over the game on somebody's stream are the same object under the same
constraint — narrow, glanceable, read by someone who cannot click it — so
`MiniParse.jsx` is the parse and `MiniRail.jsx` is only the DOCK (which edge,
and the two buttons). The overlay used to render the full dashboard meter,
which was a page scaled down rather than a thing designed for the space.
`MiniParse` renders a run of `.minipanel`s and nothing that positions them:
the rail is fixed to a window edge, the overlay fills an OBS source, and
neither wants the other's frame.

**Which edge is a setting**, remembered per browser alongside the switch
itself, because it is a fact about a desk rather than about tonight: a rail on
the left is in the way of somebody whose game sits to the left of the browser.
The button on the rail's head moves it and squares off the border against
whichever edge it lands on.

Two things it deliberately does NOT do:

- It does not follow the main column. Click back through the rail to an
  earlier fight and the middle of the page becomes that record, while the mini
  stays on the pull in progress — it is what you read while looking at the
  game, and a parse of a fight from twenty minutes ago is not that.
- It does not re-derive the ranking. `LiveMeter` exports `meterRows`/
  `meterRate` and the rail calls them, because two orderings of the same parse
  on one screen is the bug nobody would think to look for.

It renders into `document.body` for the reason `RaidNotes` and `Picker` do:
every `.card` here carries `backdrop-filter`, which is a containing block for
`position: fixed` as well as a stacking context, so a fixed rail written inside
one is sealed into it.

#### The rate sits against the name

A mini row is the rank, the name and class, the RATE, then the max hit — the
opposite order from the dashboard's meter, and the opposite of what this drew
first. A full-width meter leaves the eye room to run along a row; 244px does
not, and what is read on the rail is the RANKING, straight down the rate
column. So the rate goes next to the thing that says whose it is, and the max
hit — the number you go looking for once a pull ("what did that crit for"),
never the one you scan — takes the outer column. Each cell keeps its own weight
through the swap: the rate is the bold figure, the max hit stays muted, and the
column heads swap with them.

#### The rail has its own switches (`MiniRail`'s ⚙, `eq2a.mini.cfg`)

The Damage/Healing/Tank chips under the dashboard's meter used to drive the
rail as well. They no longer do, and the reason is that they were making a
decision for the wrong surface: the middle column is a page read BETWEEN pulls
and can spare three stacks of bars, while the rail is 244px read DURING one
with the game behind it — where the third stack is what pushes the countdowns
off the bottom of the screen. So the meter's chips switch the meter, the ⚙
beside the rail's edge button switches the rail, and neither is a global
setting about "the parse". Both label their switches `DPS`/`HPS`/`TANK` now
(`METRICS.short`) — the word a raid says out loud; `label` stays the prose form
for sentences and `rateLabel` stays the head over the figure, which is why tank
is `TANK` on a switch and `Inc/s` over its column.

The panel carries five switches for what is DRAWN — the three meters, the AoE
countdown panel, the burn window row — and then, separately, what is allowed to
INTERRUPT. The AoE and burn switches appear in both groups on purpose: wanting
the countdowns on the strip without a card in your face for each one is a
normal answer, and so is the reverse. It is remembered per browser, alongside
which edge the rail is docked to, for the same reason — it is a fact about a
desk. **Every meter switched off is a legitimate setting**, not an empty state:
countdowns and notifications with nothing under them is a rail somebody asked
for, so the bars' panel is absent rather than drawn empty.

The panel docks UNDER the head rather than floating over the parse: the parse
is the one panel that gives up height, so a settings list opened mid-pull
shortens the bars instead of covering the countdowns it is there to configure.

**The middle column got the AoE switch too** (`LiveMeter`'s chip row,
`eq2a.mainaoes`). The countdown panel was the only thing on the dashboard meter
with no way to turn it off, which made it the one part of that column you had
to accept whole — and between pulls, when the meter has become the parse, the
strip of countdowns above it is often the last thing anybody is reading. It is
a chip in the meter's OWN row rather than a switch beside Mini/Parse/Overlay in
the rail head: the head says which SURFACE is on, that row says what is in the
middle column, which is exactly what the metric chips say. Ruled off from them
with a hairline (`.tabsep`), because a chip left of the rule adds a stack of
BARS and this one adds a panel. It is remembered per browser, like `Mini` and
`Parse`, and it is on by default — an audit you have to go and find is one
nobody finds. It does not reach the rail: the rail's countdowns are the rail's
switch, per the split above.

#### One settings row, two panels (`Settings.jsx`)

The ⚙ and the stream overlay's options ask the same question — what is on
screen while the raid runs — and used to answer it in two idioms: a grid of lit
PILLS on the rail, named rows with a switch and a line of explanation in the
overlay panel. The pills were argued for on SIZE, and that is the wrong axis.
What a 244px strip cannot afford is a control you have to press to find out what
it does: `Burn window` lit gold says nothing about what a burn window is, and
the sentence that would have said it was in a tooltip nobody hovers mid-pull.

So `SettingRow` and `Switch` are one pair of components used by both, and the ⚙
is now the same list one size down — the same `pophead`, the same name / hint /
control row, tightened in CSS (`.miniconf .settingrow`) and never rebuilt. Two
details the shared pair settles rather than each caller:

- **A row whose control is a set of CHIPS is not a `<label>`** (`as="div"`). A
  label wrapping buttons adopts the first one as its control, so clicking the
  word "Theme" pressed "Transparent". A row whose control is a switch stays a
  label — at a switch's size, the name and the hint are most of the hit area.
- **A switch held off by a master switch is DISABLED, never hidden.** A row
  that vanishes takes its setting with it, and a panel that has forgotten what
  you told it is worse than a greyed row.

The one row on the ⚙ that is not a switch is `Bars`, because it is the one
setting that is not a yes/no — any subset of the three stacks, as chips, the
way the overlay's Layout and Theme rows are chips. It stacks (`.stack`) because
three chips, a name and a line of hint do not share a line at 244px.

#### The notification block (`MiniAlerts.jsx`)

**Directly under the last stack of bars**, which is why `.miniparse` shrinks but
does not GROW (`flex: 0 1 auto`). It is still the one panel that gives up height
on a short window — a countdown squeezed off the bottom is the row that had to
be visible — but when it stretched to fill a tall one, everything below it was
pushed to the bottom edge of the screen and the block ended up a column of empty
space away from the parse it belongs to.

It holds two kinds of content that behave differently on purpose:

- **The countdowns, one size up.** The AoE rows and the burn window, drawn
  BIGGER — and drawn by `AoeTimers` itself, not by a copy of it: same rows
  (`miniTimers` picks them), same drain on the compositor, same JOUST flash,
  same landing flash, scaled in CSS alone. A second implementation of a
  countdown is a second set of numbers to keep in step, which is the same
  reasoning that has the mini parse calling `LiveMeter`'s `meterRows`. They are
  PERSISTENT: a countdown that only appeared when it was nearly due is one
  nobody can plan around. `showRows` and `showBurn` are independent because the
  block is sometimes the burn window ALONE.
- **The death cards**, which pop in above the countdowns, hold for seven
  seconds and go — the only thing on the rail that appears and disappears,
  because it is the only thing here that is an EVENT rather than a clock.
  **MAIN/OFF TANK DOWN**, and **N DOWN** for more than five raiders inside 8s;
  a wipe card supersedes the tank card rather than stacking on it, since when
  six people are on the floor, which of them was the main tank is not what the
  next two seconds are for. Two cards at once, maximum. These run on the WALL
  clock — a card that shows for seven seconds is seven seconds of somebody's
  attention, not of log time.

The block is absent entirely when it has nothing to hold, rather than sitting
there as an empty frame.

**Which deaths are worth a card** is the design of the death half. A single dps
dying is a fact for the parse, not an interruption, and the deaths figure in
the numbers strip already carries it; a TANK dying changes the shape of the
fight. Who that is comes off the parse rather than off a setting: **the fighter
who has taken the most damage this fight is the main tank and the second is the
off tank** (`tankOrder`, over `CLASS_ROLE` — all six fighter classes, brawlers
included). It is the answer the raid would give out loud, it needs nothing
configured, and being noisy in the first seconds of a pull costs nothing
because nobody is dead in the first seconds of a pull. A raider with no class
resolved yet is nobody's tank — guessing would name the wrong person at the
worst possible moment.

**Deaths are counted by DIFFERENCE**, because the payload carries a running
total per actor and no death events. The first payload of a pull is a BASELINE
and announces nothing — otherwise every fight would open by announcing the
deaths of the fight before it — and the baseline is retaken whenever
`started_ts` changes.

The cards do not blink. A death card is a SOLID danger-coloured block, which is
the loudest thing available that does not move, and the one animation anywhere
here is a 180ms cross-fade on arrival — so there is nothing for reduced motion
to take away (the same reasoning as the AoE landing flash).

Two switches short of what could be here, deliberately: the death cards ride
the master Notifications switch and have none of their own. A countdown you
have tuned out is a preference; a tank on the floor is not.

### Notes are keyed by zone and named, never by encounter (schema v28)

The dashboard's right column files what you write mid-raid. On trash it belongs
to the ZONE (`mob_name IS NULL`), on a named it belongs to that boss, and the
client decides which — it is the thing that knows what is on screen, and a
server reading it back off encounter rows would be reconstructing an answer it
was already told.

The key is `(user_id, zone, mob_name)` because **encounter ids do not survive**:
a live session is rebuilt from raw when it closes and every id in it changes, so
a note that identified itself by one would lose its subject overnight.
`encounter_id`/`zone_run_id` are stored as provenance and are never joined for
identity. Keying on the boss instead means tonight's note lands beside every
other attempt on that boss, which is the pile this exists to grow into — an
outline of the zone, written one pull at a time (`GET /api/notes/outline`).

Notes are private to whoever wrote them, with no group predicate, exactly as
imported parses are: `groups.py` owns the one visibility rule and does not get a
weaker sibling. Screenshots are stored the way parse shots are — re-encoded to
WebP under `NOTESHOTS_DIR`, never the uploaded bytes, served by an owner-checked
endpoint rather than a static mount.

**The instance number is about the night, not the place.** The game logs a
repeat lockout as `Castle Mistmoore 2`, so a note filed under the zone as the
log spells it would start a second pile every time the raid re-entered. Notes
are FILED under the base name (`zones.base_name`, in `notes_api._zone`), and a
read matches on it too (`_variants`) so rows written before that was true fold
into the pile they always belonged to. Nothing migrates: the old spellings stay
on the rows, they just stop being separate subjects.

**The column shows the ZONE, the composer writes to the SUBJECT.** These are
two different questions and it used to answer only the second — engaging a
named hid the zone's own notes, which is the moment you most want them.
`GET /api/notes?zone=X&scope=zone` is the wide read; the default stays narrow
because it is what the composer asks, and `mob_name IS NULL` is a real filter,
not a missing one. The subject being written to sorts first and says so.

**The column COLLAPSES SIDEWAYS, and the composer is a text box with its button
under it.** Three things about writing a note mid-raid, all of them about the
hand that is not on the keyboard. The card collapses with the site's own switch
(remembered, `eq2a.notes.open`) — on a pull you want the width for the parse —
and collapsing gives the WIDTH back, not just the height: closed, the column is
a tab down the edge of the page (`.notestab`) and the grid track shrinks to it
(`.dashgrid:has(.notestab)`). It used to keep its whole 340px around one header
row, which is a collapse in name only and is exactly the width two raiders
opened side by side in the middle column were short of. The tab is the whole
control, because a switch that can only be turned on is not a switch. ENTER
files the note; Shift+Enter is the newline, and Ctrl+Enter
still works because it was the only way for a year. The `File under X` button
sits directly under the textarea rather than below the screenshot box, which
had put the thing you press furthest from the thing you type. And the drop box
is a strip: pasting needs no target (it is a window listener), so the box only
has to say so and be droppable — full size it was a third of the column.

### The notes outline is grouped by expansion, and the era comes off the wiki

The raid list's right column (`NotesOutline.jsx`, `GET /api/notes/outline`) is
the whole pile as a table of contents: every zone, every named inside it, and
the notes open where they sit. The dashboard can only ever show the zone you
are standing in — this is the view that shows what a year of pulls added up to.

**It opens from a button, and is closed by default** (`Notes`, the right end of
the raid list's tools line; remembered in `localStorage`). Standing open it
took 300px from a table that then had to scroll sideways to fit its columns,
and every raid row was read past a second panel that had nothing to do with it.
The notes are a second thing to read, not a margin on the first one.

It groups by **expansion**, because that is the order a TLE server unlocks
content in and therefore the order the zones already sit in in a raider's head.
Nothing in a log says which expansion a zone came from, so that fact is
REFERENCE DATA (`backend/zones.py`, from `refdata/zone_eras.json`) and never
inferred: the wiki's `IZoneInformation` box carries `introduced`, `instance`
(Raid/Group/Public) and `zdiff` (x2/x4), pulled by hand with
`tools/sync_zone_eras.py` and committed. The app reads the file at import and
never touches the network, so a wiki outage is not an outage here.

Two details the sync earns its keep on. A zone the wiki files under a live
update (`introduced = LU22`) is resolved to whichever expansion was live on that
update's date — LU22's own patch notes say "a dangerous new raid zone in
Kingdom of Sky", and the date says the same thing without anybody hand-filing
it. And the dates themselves are typed four different ways across six years of
patch notes (`April 13, 2006`, `December 20th 2006`, `April 17,2011`,
`2/28/2007`); a parser that knew one form left whole expansions unplaced.

A zone with no entry still appears, under **Other**, at the bottom. An outline
that silently dropped notes because a zone was not on the wiki would be worse
than one with a ragged last section.

**Every named links out to eq2lexicon** (`lib/raids.js: lexiconRaid`) —
`/raids/<zone>/<named>` is its strategy page. That is the deliberate division:
the lexicon says what the encounter does, the note says what happened to US on
that pull, and this site restates none of the former. The links open in a new
tab because eq2lexicon refuses to be framed (see `docs/zoneruns.md`). Only
nameds get one — a link to a zone's trash would be a promise about a page with
nothing on it.

### The stream overlay is a capability in a URL (schema v29)

`/overlay/<token>` is a page for an OBS browser source, and that decides the
design: a browser source carries no cookies and `EventSource` cannot set a
header, so the token has to ride in the path. Taking that seriously means the
token is deliberately narrow — it reaches the in-flight meter for whichever of
that account's characters is streaming right now, and nothing else. No session
ids, no fight cards, no history, no account name; a URL that ends up in a VOD
must not be a way into anybody's parses (`test_overlay_api.py` asserts the
absence of each field).

Two doors, one read surface: the overlay stream and the session stream both
read `live.live_snapshot`, rather than one endpoint branching on how the caller
authenticated — a generator that decides authorization halfway through is one
nobody can audit. The session it points at is re-resolved every tick, so
switching to an alt mid-stream follows on its own, and the stream stays OPEN
with `{"live": false}` when nothing is running: an OBS source is opened once
and left for hours, so a stream that ended between raids would be a scene that
goes blank for good. Revoking is a row update because a URL already out in the
world has to be killable without changing anything else — and a revoked token
answers exactly like one that never existed, so the URL leaks nothing about
whether it ever worked. `/account` mints the URL (theme, which parses, how
many rows).

The page renders BEFORE the app shell (`App.jsx` branches on the path): nav,
theme toggle and account icon are furniture on somebody's stream. `transparent`
is the default theme and means the document paints nothing at all — html and
body included — because OBS composites the page over the game, and a background
there is not a style choice, it is a rectangle over the raid.

The overlay never goes BLANK out of combat, only quiet. A meter that exists
solely mid-fight cannot be positioned in OBS, and a stream between pulls
showed a hole where the parse was — so between pulls it keeps the last fight
on screen, dimmed with a "between pulls" tag, and before any fight has
happened it draws a placeholder parse of invented raiders, marked "sample
parse — waiting for combat" so nobody quotes it.

**The settings open where they are used** (`OverlayPanel.jsx`), from the
dashboard bar beside the Mini switch, as a small window rather than an
expanding band — a settings list that pushes the meter down every time it is
opened is one nobody opens during a fight. It sits beside Mini because it is
the same question: what is on screen while the raid runs. The first open MINTS
the link if the account has none, so Copy link always has something to copy;
the old flow was "go to /account, press Create, come back", which is the only
reason anybody went there. `/account` keeps the same panel (one component,
`OverlayOptions.jsx` — two settings lists for one feature is how they end up
disagreeing) and is still where extra links are minted.

What it offers is what a stream scene actually asks: **on/off**, the link,
healing on/off, AoE timers on/off, **where healing goes** (under the damage
bars or beside them), how many combatants, how wide, and the theme. Three of
those are new ideas rather than moved ones:

- **OFF is not REVOKED.** Off blanks the page while the browser source stays
  connected and positioned; revoke kills the URL for good. Taking the parse off
  a scene for one pull must not cost a trip into OBS afterwards.
- **The page re-reads its config on a timer.** The thing being configured is in
  OBS, not in the tab holding the settings, and nobody can press refresh on a
  browser source.
- **A CORRECT TOKEN IS NOT A GUESS, so it is resolved BEFORE the rate limiter
  is consulted** (`_resolve`). The other order is what a limiter normally wants
  and it was wrong here for a reason specific to this endpoint: the bucket is
  keyed by client ADDRESS, and every screen in this feature re-asks the same
  question every five seconds forever. One revoked link left open in a browser
  somebody forgot about is twelve `fail()`s a minute, and five of those took
  the same machine's VALID overlay down with it — the request never reached the
  lookup, so the success that would have cleared the bucket could not happen.
  An IP-wide, self-inflicted, self-sustaining lockout on the whole feature,
  which is exactly how it presented: a black in-game window and a 429 on a
  token that was fine. Enumeration is made of MISSES, and a miss still costs a
  failure and is still refused once the bucket is full, so nothing about the
  brake weakens. **A hit no longer CLEARS the bucket either** — `clear()` on
  success belongs to the login route, where the person failing and the person
  succeeding are the same person; it does not survive being moved ahead of the
  limiter, because a working overlay polling every five seconds would wipe the
  counter often enough that no guesser could ever be stopped. The holder does
  not need it any more. `test_a_stale_link_cannot_lock_out_a_good_one` holds
  both halves.
- **A 404 STOPS THE POLL.** The client half of the same bug: nothing on the
  other end can un-revoke a token, so a page that goes on asking every five
  seconds has nothing left to learn and is only manufacturing failed
  credential attempts. Failures also back the interval off toward a minute — a
  fixed fast poll against something that is failing is how a page turns an
  outage into its own.
- **A FAILED READ IS NOT A DEAD LINK, and 404 is the only definitive answer.**
  The first build rejected anything that was not a 2xx and latched `gone`
  forever, which turned every hiccup — a 429, a 502, the backend restarting,
  the game client dropping a request while it zones — into a permanent "this
  link is no longer active" on the one kind of page that cannot be reloaded.
  `_resolve` returns 404 for revoked and never-existed alike, so nothing else
  the server can say means the token is gone; everything else keeps what is on
  screen and retries on the next tick, and a read that succeeds CLEARS the
  state so a window that gave up while the server was down comes back on its
  own. The card it does show now names the cause and the fix, because it can
  only mean one thing.
- **A dropped connection is EventSource's problem; a refused one is ours.** It
  reconnects a stream the server merely closed, which is why the error handler
  was empty — but it will not retry a non-2xx, and after one it goes to CLOSED
  and stays there with the last frame on screen forever. On a source nobody can
  refresh, silent-and-still is indistinguishable from a quiet raid. So CLOSED
  (and only CLOSED — CONNECTING means it is already handling it) schedules a
  reopen five seconds out.
- **"Not yet" is not "nothing".** Before the first config landed, the page drew
  an empty div — and an empty div on a document painted black is a black
  window, which is how the rate-limited read above presented itself: no card,
  no error, no clue, just a rectangle. It says `connecting…` now. `enabled:
  false` keeps the blank to itself, which is the one state that genuinely means
  paint nothing.
- **Width is TYPED, in pixels, and blank means "fill the source"** (`width_px`,
  clamped 160–1920 rather than rejected — it is a number typed into a box on a
  dashboard, and a 422 mid-raid is worse than a sane one). A scene is built
  once and lived with, so a parse that reflows every time the source is nudged
  is one nobody can line anything else up against; the sheet still caps it at
  `max-width: 100%`, because a number wider than the source has to clip rather
  than put a scrollbar across somebody's stream.

### The same link, pointed at the game client (`/ingame/<token>`, schema v34)

EQ2 has its own browser window, and the parse belongs in it: the player who has
to move out of an AoE is sitting at the game, not at the dashboard on the other
monitor and not watching the stream. Pointing that window at the overlay URL
does not work — **the overlay is sized to be read after a downscale and an
encode, and this one is read at 1:1 on the same monitor as the game**, in a
window that is covering somebody's UI. `text_scale` defaults to 1.25 there and
0.9 here, which is the whole disagreement in one number.

**Two kinds of token, one capability** (`overlay_tokens.kind`). The public half
does not branch on kind at all: a token is a token, it resolves to the account
that minted it and reaches the fight in progress. Only the config defaults and
their clamps differ (`CONFIGS`, `INGAME_MIN_SCALE`/`INGAME_MAX_SCALE`). They are
separate ROWS rather than one row carrying two config blocks for one reason
that decides it: **revoking is per URL.** A link that ended up in a VOD has to
be killable without taking the window beside somebody's hotbars down with it.
`kind` is fixed at creation and never patched — a link that changed what it
draws under a running OBS source is a different feature, not a setting.

**One page, two paths** (`pages/Overlay.jsx`, `kind`). Everything that is hard
about this screen is identical for both: a config re-read on a timer because
nobody can press refresh on it, an `EventSource` that has to survive a restart,
and a between-pulls state that must not go blank. Two copies of that is two
copies to keep in step. What actually differs is three lines:

- **The notification block is ON in-game and absent on the stream**
  (`MiniAlerts` via `MiniParse`'s `notify`). Every card on it is an instruction
  — tank down, a wipe, a marked AoE about to land — and a stream audience is
  the one audience that cannot follow one. It is mounted whether or not a pull
  is running, never gated on `inCombat`: deaths are counted by DIFFERENCE
  against a baseline the block keeps, so one that unmounted between pulls would
  open every fight announcing the last one's dead.
- **The document is PAINTED, not transparent.** `transparent` is what an OBS
  source needs and the exact opposite of what this one does — EQ2 puts the page
  in a window and composites nothing behind it, so a transparent html/body is
  the browser's own default showing through as a white margin around a dark
  panel. `data-ingame` overrides `data-overlay` on the document for that, and
  `transparent` is not offered as a theme here at all.
- **The window is the width, and it SCROLLS.** No `width_px` and no
  `layout`: geometry is for a scene somebody composes, and this one is resized
  by dragging its edge. Overflow scrolls, which the overlay must never do (a
  scrollbar on a stream is a grey bar over the game) and which is right here —
  the alternative is a countdown pushed out of a window nobody can make taller
  without covering more of the fight.

**This window is what put the hand marks on the account** (v35, above). Which
AoEs get a countdown here and which cast owns the burn window are the two
things on it that no log supplies, they are marked by hand on the dashboard,
and this is a DIFFERENT BROWSER — so it opened with none of them, jousting
whatever the raider's ACT list happened to list. That was a defensible floor on
a stream, where nobody reads their own overlay, and obviously wrong three
inches from the hotbars of the person who did the marking. The marks now ride
in with the config on the poll this page already runs, so a pill toggled
mid-raid arrives on the next tick.

#### Four rendering targets, and only one of them is a current browser

The dashboard is read in whatever the raider uses. The parse is ALSO drawn
inside an OBS browser source and inside EQ2's own in-game browser, and both of
those are embedded CEF builds years behind — OBS ships a CEF several Chrome
majors old, and EQ2's is older than that. Nothing tells you when a feature is
missing there: an engine that cannot parse a property VALUE throws the whole
declaration away and paints the element without it.

That is not a hypothetical. **`color-mix()` needs Chrome 111 (2023)**, and the
whole site was using it for every translucent fill — the class-coloured bar
behind a meter row (`classes.barFill`), the AoE drain, the `due` and `swiped`
bars, the landing flash, the normal-timer mark, the burn window, the alert
block. In the in-game window every one of them rendered as **nothing**: the
elements were there, correctly sized and positioned, with no background. What
still worked was everything drawn in a plain colour — the live dot, the `HIT!`
word, the countdown digits — which is exactly the shape of the bug report ("the
green dot and the AoE hit flash work, the bars counting down just don't show":
the flash he could see was the word, not the red wash behind it).

So: **a translucent fill is `rgba(var(--x-rgb), 0.NN)`, never `color-mix`.**
Every colour that needs one carries an `-rgb` triplet beside it in
`tokens.css`, in both themes, and they have to be kept in step. `rgba()` has
worked since IE9 and says the same thing.

The one survivor is `stats.rankColor`, which mixes toward `--text` rather than
toward transparent — there is no `rgba()` for blending two colours, and it is
deliberately left: it is only on `/zones/:id` and `/compare`, neither of which
is ever a token URL, and its failure mode is a cell with no rank tint rather
than a cell with nothing in it.

The general rule this is an instance of: **on the two surfaces nobody can open
devtools on, prefer the older construct.** A silent visual failure on a browser
source in the middle of a raid costs more than the nicer syntax is worth.

#### Small is the easy half; readable at small is the point

**Sharpness is not weight, and it is not size either — it is whole pixels and a
face drawn for them.** ACT's own window is Courier at about 9px and it is
perfectly crisp, which is the tell: what reads at this size is not a bigger or
bolder glyph, it is one whose stems land ON pixels. Three things, in order of
how much they matter:

1. **Whole pixels.** Everything derived from `calc(15px * --ovl)`, and 0.7 of
   15 is 10.5 — with every `em` size under it landing on another fraction. A
   stem on a half pixel is rasterised across two of them at half strength each,
   which is most of what "small but mushy" was. `--ovl-px` is rounded in
   `Overlay.jsx` and every size in the scope is a whole-pixel `calc` off it.
   The in-game Text size chips are therefore labelled in **px, not percent** —
   the number means something now, and it is comparable to the ACT window next
   to it.
2. **A face with hints.** Tahoma and Verdana were drawn for small sizes on
   screens and are hinted down to ~8px; `system-ui`/`Segoe UI` is a display
   face that goes soft there. This scope overrides `--font-data`.
3. **Not bold.** Weight was the first lever tried and it was the wrong one: at
   9px a 700 stem is not thicker, it is smeared — there is no room for the
   extra weight to go, so it fills the counters. Contrast carries the hierarchy
   (white against `#b9c0d6`), which is how ACT does it. Every added 700 in this
   scope came back out.

**A GRID, NOT A FLEX RUN, and it answers four complaints at once.** Every row
was a flex line sized by its own contents — rank, a name taking the slack,
numbers pushed right with `margin-left: auto`. That has two failure modes and
the in-game window is the only surface narrow enough to hit both.

*It moves.* A rate going from 9,999 to 10,000 is one more glyph, so the column
widens and the name narrows — every row, mid-fight, while somebody is reading
down it. Nothing in a parse should change position because a number changed
value.

*It escapes.* When the contents stop fitting, flex has nowhere to put the
overflow but past the right edge, where the window clips it: `137,412 DP`, and
`JOUST` not there at all. Squeezing the window made it worse, which is the
opposite of what a window meant to be squeezed should do.

Tracks that do not consult their contents fix both. The rank and the figure are
fixed in `ch` — the width of a digit, which with tabular figures is exactly what
a number column wants and scales with the type size on its own. The NAME is the
only elastic track (`minmax(0, 1fr)`), so it is the only thing that ever gives,
and it gives by ellipsising, which a name survives. The right edge cannot move
and nothing can be pushed past it. The AoE rows take the same treatment (name
elastic, countdown fixed), and the BURN row's second track is `auto` because it
carries a word as well as a clock: `JOUST` is the one thing on that panel that
is an instruction rather than a reading, so the label beside it — which never
changes and is already said by the row's colour — is what yields.

The other shift was **an inner scrollbar**. `.miniparse` is `overflow: auto`
everywhere else, which is right for a dock that gives up height, but a scrollbar
appearing as the row count changes is a few pixels of width appearing, and every
number in the panel moves when it does. In-game the WINDOW scrolls and the panel
does not.

#### The page grid packs to the TOP (`align-content: start`)

The same grid/flex difference one level up, and it produced a symptom that
pointed at the wrong feature. `.overlaypage` is a grid; the in-game scope gives
it `min-height: 100vh`, because the window is the viewport. A grid's default
`align-content` is `normal`, which behaves as **`stretch`**, and stretch grows
every auto-sized ROW equally to fill the container — where a flex child stays
its content size unless it asks to grow. So a short pull in a tall window did
not leave the slack at the bottom; it divided it evenly between `.minihead`,
`.mininums` and `.miniparse`, and each panel padded itself out with its share.
`.minihead` is `align-items: center`, so the mob name floated in the middle of
its own band; the other two put their content at the top and took the space
underneath.

**It presented as "the in-game window looks stupid when there are no AoEs",**
and that reading is worth keeping because the real cause is the opposite of
what it suggests: nothing about the countdowns is special. A fourth panel is
one more track dividing a SMALLER remainder, so a pull with countdowns looked
nearly right and a pull without them gave a third of the window away to empty
space. Chasing the AoE panel would have found nothing wrong with it.

`align-content: start` on the in-game scope. The window is a rectangle the
player has already decided to give up: the block sits at the top of it and the
rest stays black, which is what ACT's own mini parse does. `min-height: 100vh`
stays — the page div being the full height is what makes the dark surface the
page's rather than the document's. The OBS overlay is untouched: it has no
height constraint, so its tracks were always content-sized, and the 6px gaps
between its visible cards are deliberate.

One more empty frame went with it. `MiniParse` decides whether to draw the
`.minitimers` panel from the RAW payload, while `AoeTimers` first runs it
through `live()` and returns nothing if the drop line took the last row — and
this browser's clock runs ahead of the payload by design, so for a poll the
frame can be committed to and the rows gone. `.minipanel.minitimers:empty`
collapses it, the same guard `.minialerts` already carried for the same reason.
Under the stretch above, that empty strip was also claiming a full share of the
window's slack.

Three more things `dense` does for width, since the window is meant to be
draggable down to almost nothing:

- **Max hit goes.** It is the number you go LOOKING for after a pull, not one
  you scan, and it was costing about a fifth of the window. Every parse page
  keeps it, and so does the stream overlay.
- **Raid DPS moves to the RIGHT of the headline, over the column it totals**,
  with the clock taking the left where a rank would be. It renders key-first
  (`row-reverse`) so it is the FIGURE that meets the column edge, not the word.
- **Ability names drop to the label size and nothing leaves a row.** `JOUST`
  was escaping the right edge, and the mechanism is worth writing down: `.cd`
  holds a min-width digit cell and a word, neither of which can shrink below
  its own text, so once the name has given up all its width the overflow has
  nowhere to go but out. The name ellipsises, the clock never shrinks, and the
  row clips the remainder rather than letting it into the game. The compact
  digits were also sized in `rem`, which ignores the scale entirely — so at 60%
  they stayed full size while the row shrank around them.

The thing this is competing with is ACT's own mini parse: white and green on
flat black at about 9px, and perfectly legible at arm's length while fighting.
The first build here was the dock's palette scaled down, and **scaling down is
exactly what a palette cannot survive.** `--text-muted` is `#8b90ab` over a
translucent `#252840` — about 3.5:1, which is fine at 12.5px on a monitor you
are looking straight at and is a grey smudge at 8px in the corner of an eye.
Turning the type size down made it *less* readable, which is the whole finding:
**smaller type needs MORE contrast, not the same contrast smaller.**

So `.overlaypage.ingame.theme-dark` re-declares the TOKENS rather than
re-lighting elements one at a time the way the OBS scope does — an element list
misses every panel added after it was written. Flat and opaque (translucency
over a moving game is contrast you do not control), `#ffffff` text, a muted at
`#b9c0d6` (~10:1 rather than ~3.5:1), and hairlines bright enough to survive
being one physical pixel. Light theme is left alone: dark ink on parchment is
already the contrast this is buying.

Three more things follow from the same measurement:

- **Weight is free width.** Stems are what the eye loses first at 8px, and a
  heavier stem costs nothing horizontally the way a bigger glyph does. The name,
  the rate and the clock go up a weight; the labels stay.
- **One block, not a stack of cards.** No gaps, no radii, no shadow and no
  outer frame — the WINDOW is the frame, and a border drawn just inside another
  border is a pixel of raid spent saying nothing. Panels are separated by one
  hairline, which is what ACT's is.
- **Between pulls it dims to 0.88, not 0.72.** The dock can afford to wash the
  last pull out because the dashboard is right there; at 8px that difference is
  quiet versus gone.

#### `dense`: the third size the mini parse is drawn at (`MiniParse`)

The dock is 244px of a monitor; the OBS scene is as wide as somebody chooses;
the in-game window is neither — a rectangle inside the game's own UI, maybe
180px, sitting over a hotbar. At that width the first build was clipping the
right-hand column and rendering rows as `S… Swash`, and both are the same bug:
things that earn their width at 244px do not earn it at 180.

- **The class goes, so the NAME fits.** A four-character class beside a
  truncated first initial identifies nobody — it was not a caption on the name,
  it was the reason there was no name. The bar's hue still carries the
  archetype, and the person reading this screen is in the raid.
- **HPS and deaths come off the headline**, leaving raid DPS and the clock —
  the two figures that appear nowhere else on the panel. HPS is a whole STACK
  away (turn healing on and it is twenty rows, not one number), and a death is
  what the notification block exists to shout about. Two figures fit on one
  line laid out sideways, so the head stops being a four-column block and
  becomes a thin strip.
- **Nothing scrolls sideways** (`overflow-x: hidden` on the document and the
  parse). A row that runs off the right edge takes the number with it, and the
  number is the row. The name gives way instead; it already ellipsises.

**The button sits between Parse and Overlay** in the rail head, and that row now
reads top-to-bottom as "where is the parse": `Mini` is this window's edge,
`In-game` is the game client's own window, `Overlay` is OBS. In-game comes
first of the two links because it is the one reached DURING a raid; the OBS
link is set up once and left. Both are `OverlayPanel` with a `kind`, and
`useOverlays` filters by it — a panel reaching for `overlays[0]` opens the
wrong settings the moment both links exist. `/account` lists the stream
overlays only: an in-game link is pasted into EQ2 while sitting in the game, so
it is minted and revoked from the dashboard and the account page just says so.

**A replay feeds the overlay** (`pipeline/replaybus.py`). The overlay reads the
LIVE snapshot, which made it the one surface that could only be worked on
during a raid — the scene has to be positioned and the options judged against a
real pull, and both were a Tuesday-night-only job. So a replay also drops its
latest frame in a per-account slot and the overlay stream picks it up, ahead of
that account's live session, the way the dashboard makes the two exclusive.
Three properties are what make that safe: it is keyed by USER and an overlay
only ever reads its own owner's key; the frame EXPIRES (`MAX_AGE_S`), so a
replay that ended or a tab that was closed releases the screen with no stop
message to lose; and what is published is the LIVE payload — the `replay` block
names the fight and the night it came from, and this token is not allowed to
hold either.

### Replaying a recorded fight (`routers/replay_api.py`)

The meter is the one surface here that only exists while a raid is happening,
which made it the one surface that could not be worked on: a change to a bar, a
countdown or an empty state had to wait for the next raid night and then got one
pass at it. A replay reads a recorded fight's RAW LINES back off disk, parses
them with the same `parse_lines` the live path calls, and walks a cursor through
the result in wall-clock time. The page cannot tell the difference, which is the
whole point — the component under test does not know which socket it is reading.

It is a third reader of `livemeter`, so it inherits that module's promise: it
**writes nothing**. No session, no encounter, no rows, no `LiveState`. That is
what makes it safe to point at any fight in the back catalogue, and it is why
`test_golden_equivalence` never enters the picture.

`backend/tools/simulate_live.py` is deliberately NOT this. It pushes a log
through the real ingest endpoint, which parses, dedupes, writes chunks and
creates a session — the right tool for testing INGEST, the wrong one for
testing the SCREEN, because tuning a dashboard should not leave a night of
junk raids behind it.

**Two gates, kept apart.** `require_curator` (admin implies curator) gates the
TOOL — a developer control does not belong in a reader's dashboard.
`visible_encounters` gates the FIGHT, exactly as every other read does. Widening
the second along with the first is the obvious mistake and would make replay a
door into everybody's raids; "admin is operational, not omniscient" has to hold
here too, and `test_replay_api.py` pins it with an admin being refused a
stranger's fight.

**Reading only the raw the fight can be in.** A live session is stored as a
chunk per ingest batch, each carrying its own time bounds, so the window query
skips the hours in front of the pull: on a real 340-second raid fight that was
5.9s of decompression before the first frame, and is now 0.03s. An upload is one
file and has no such shortcut. Past the fight, reading stops after
`TAIL_GRACE_S` rather than at the first later timestamp — an EQ2 log is written
in order, but the failure mode of being wrong about that is a silently short
replay, so the margin is generous.

Cadence is `TICK_S = 2.0`, the plugin's own send cadence, because a replay that
refreshed faster than a raid can would be a smoother meter than any raid
produces and would get tuned against a fiction. Speed multiplies log time, not
the tick, and is clamped: at a high enough speed the fight arrives in one frame,
which is a parse, not a replay.

Verified against a real 21-raider pull (Mistress of the Veil, 19,761 events):
the meter ramps the way a live one does, three AoEs count down off ACT's
reported timers, and seven of the top eight damage rows match the recorded parse
to the number. The eighth differs by 0.3% — the documented consequence of a view
that credits by NAME instead of resolving entities.

