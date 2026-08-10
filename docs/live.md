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

**The nav owns the raid's state.** The Live tab sits LAST in the header nav,
dressed as a state rather than a place: it says "Idle" or "In Combat" before
anyone clicks. The light is answered by `live.in_combat` — a read of the
in-memory tail's open segment, surfaced on `/api/sessions` — which costs
nothing, never turns snapshot building on, and goes dark when log time falls
`LIVE_LAG_S` behind the clock (a plugin that dies mid-fight leaves a segment
only a later batch could close, and a light that stays on forever is a broken
light). The old "Connected to ACT" pill shrank to a signal icon + "ACT" +
green dot: it only says the plugin's line is up.

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
  intervals, and never when `instances_hint` explains the disagreement as
  several mobs sharing a name. The countdown itself stays on the configured
  number: a countdown that silently uses a different timer from everybody
  else's is worse than one that is wrong the same way as theirs. On 60 recent
  named fights it fires 9 times — `Soul Paralysis` 37s→43.6s over 42 agreeing
  intervals, `Stench of Death` 30s→23s, `Dark Visage` 28s→44.3s. It is shown on
  the live parse and the mini rail and NOT on the stream overlay, because it is
  an errand (go and edit an ACT config) and nobody watching a stream can run
  it.
- **A JOUST tick** (`lib/joust.js`), which is the only thing on this panel a
  log cannot supply — running out of an AoE and standing in it look identical
  in a log. It is keyed by ability NAME, not by source or fight, because
  jousting is a property of the ability and a mark has to outlive the pull it
  was made on. It lives in localStorage: per browser, deliberately not per
  account, since it is a note about how you play and the alternative is a
  settings table and a round trip before a countdown can draw. The consequence
  is that an OBS browser source is a different browser and inherits nothing.
  Ticks are drawn on the full-width live panel and the recorded AoE tab — the
  two surfaces anybody can click.
- **And it LEAVES when it has been overdue for `OVERDUE_DROP_S` = 60s.** Past
  due is information, right up until it stops telling anybody when anything is
  due; the panel is a shortlist. Nothing needs un-dropping, because every
  snapshot is rebuilt from the fight's events rather than accumulated — the row
  returns on its own the moment the ability lands again. The browser re-applies
  the same line against its own clock (`aoe_drop_s` in the payload), since that
  clock runs ahead of the payload by design and the row would otherwise sit
  there counting up for a poll.

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
is capped at `MINI_TIMER_ROWS` = 4. Rows arrive soonest-due first, so the cut
falls on the ones furthest from mattering. It is the same trade as the meter's
own `max_rows` on the overlay against the dashboard's fold — nobody watching a
stream can click, and nobody mid-pull can scroll a dock.

**The burn window** is the last row and the only one that is not an ability.
Once anything is ticked as jousted, the soonest such cast owns a row that reads
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

### The mini parse, and the two places it is drawn (`MiniParse.jsx`)

The dashboard is a second-monitor page and assumes it owns that monitor. Often
it does not — the game is there, and what is wanted is ACT's mini overlays: a
strip saying what is due and who is on top, with the rest of the panel left to
EverQuest. So the **Mini** switch in the dashboard bar docks a condensed copy
of the timers and the parse flush against one edge of the window.

Condensed **horizontally**, which is the whole constraint. Vertical space on a
1440p panel is free and every pixel of width is taken from the game, so the
rail is 244px and what survives a row is the rank, the name, the class, the max
hit and the rate; the deaths badge, the cures column, the AoE source and the
hit/blocked split all go, and the fold goes with them — nobody clicks "12 more"
mid-pull, so it is a hard ten rows.

The class survives as the **short form the raid says out loud** (`classShort`:
`SK`, `Zerker`, `Illy`), because the bar's hue is the ARCHETYPE and four hues
cannot separate six fighters — "which tank is that" needs the word. Full class
names do not fit at 244px: `Shadowknight` is wider than the name it captions.
The name ellipsizes before the class does — a clipped name is still the row you
were looking for, a clipped class is not — and the max hit is captioned `MAX`,
since an unlabelled second figure beside a rate is two numbers and people read
the wrong one.

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

**The column COLLAPSES, and the composer is a text box with its button under
it.** Three things about writing a note mid-raid, all of them about the hand
that is not on the keyboard. The card collapses to its heading with the site's
own switch (remembered, `eq2a.notes.open`) — on a pull you want the width for
the parse. ENTER files the note; Shift+Enter is the newline, and Ctrl+Enter
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
- **Width is TYPED, in pixels, and blank means "fill the source"** (`width_px`,
  clamped 160–1920 rather than rejected — it is a number typed into a box on a
  dashboard, and a 422 mid-raid is worse than a sane one). A scene is built
  once and lived with, so a parse that reflows every time the source is nudged
  is one nobody can line anything else up against; the sheet still caps it at
  `max-width: 100%`, because a number wider than the source has to clip rather
  than put a scrollbar across somebody's stream.

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

