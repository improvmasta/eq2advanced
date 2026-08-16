import { useRef, useState } from 'react'
import { fmt } from '../lib/api.js'
import { useLogClock, useTicker } from '../lib/smooth.js'
import { isJousted, nextJoust, toggleJoust, useJoust } from '../lib/joust.js'
import { isMiniPinned, toggleMiniPin, useMiniPins } from '../lib/minipin.js'

/* When the next raid-wide hit is due, and what the last one did.

   The server detects casts (`pipeline/livemeter.py`) and says what the period
   is and where it came from: `reported` is ACT's spell-timer list — what the
   raid was TOLD to expect — and `observed` is the shortest gap that has
   repeated in this fight. The distinction stays on screen, because a countdown
   nobody can source is a countdown nobody should trust.

   Which abilities reach this panel at all is decided server-side and is not
   the recorded audit's list: see `livemeter.RAID_FRACTION`. The audit lists
   everything that touched five people; this is the shortlist you call out
   during a pull.

   Each row also carries the LAST cast's outcome — how many people ate it and
   how many were covered (avoided or absorbed it) — because "is the raid
   handling this AoE" is the question the countdown exists to set up. Overdue
   is a state, not a moment: the clock keeps counting UP past due, since "3s
   late" reads as stunned-or-dead mob and "40s late" reads as the timer being
   wrong.

   The bar drains locally between partials. The server ticks every couple of
   seconds; a countdown that only moved that often would read as broken, so the
   clock here runs ahead of the payload and the payload corrects it — through
   `useLogClock`, whose correction is asymmetric, because a flat re-anchor on
   every payload is a sawtooth (lib/smooth.js says why).

   TWO clocks draw it, and that split is the point:

   - The BAR is a CSS animation, seeked to where it should be and then left
     alone (`DrainBar`). It is the one thing here that moves continuously, and
     continuous motion driven from JS is only ever as smooth as the loop that
     drives it — sampled by an OBS browser source compositing at 60fps, a 20Hz
     drain judders visibly on somebody's stream. Handed to the compositor it is
     smooth by construction, at any frame rate, however busy the tab is.
   - The DIGITS step, on the shared ticker, and only re-render when what they
     SAY changes — once a second per row rather than twenty times a second for
     the list. `ceil` means "0:01" is up until the cast is actually due.

   `running` is the stale flag, the same one the elapsed clock takes: a fight
   that has ENDED has no next cast, so the countdown stops rather than draining
   on toward a cast that is never coming. The row stays — which AoEs went off
   and how many they hit is worth reading after the pull — but it says how many
   times it fired, not when the next one is.

   TWO HAND MARKS live on the full panel, as a stacked pair of pills per row
   (`MarkPills`), because they are the two things about an AoE that are not in
   any log. JOUST (`lib/joust.js`) says the raid leaves for this one; MINI
   (`lib/minipin.js`) says it belongs on the mini parse and the overlay.

   THE BURN WINDOW is the last row and the only one that is not an ability, and
   it is what the joust mark buys: the same countdown read the other way round
   — the same seconds, but as how long there is to stand in melee rather than
   as how long until something lands. It is the soonest jousted cast that owns
   it, it is coloured differently because it is an instruction rather than a
   reading, and inside JOUST_WARN_S it says so in the clear.

   A ROW MAY HAVE NO COUNTDOWN AND STILL BELONG, and one case is not the
   server's caution but the game's: a mob that SPLITS wears one name on several
   bodies, so its casts interleave and no period describes them
   (`aoes.several_bodies`). The payload sends no `period_s` for those, the row
   reports how many times the ability fired, and `BODIES_NOTE` explains it on
   the title.

   Two things are the server's and are only re-applied here. A row the payload
   has dropped for being `aoe_drop_s` past due (livemeter.OVERDUE_DROP_S) is
   dropped here too the moment this clock passes the same line, because this
   clock runs ahead of the payload by design and a row would otherwise sit
   there counting up for a poll. And a SUGGESTED timer — the period this log
   measured, when it disagrees with ACT's list by enough to be worth acting on
   — is printed rather than applied: the countdown stays on the number the raid
   configured, because a countdown that silently uses a different timer from
   everybody else's is worse than one that is wrong in the same way as theirs. */

/* Get out now. Long enough to cross a room in EQ2, short enough that it is
   not shouting through half the burn window. */
const JOUST_WARN_S = 5

/* How long a finished reflect window stays up saying so — `livemeter.
   REFLECT_CLEAR_S`, mirrored here because this clock runs AHEAD of the payload
   by design and would otherwise keep drawing a row the server has let go.

   The all-clear is the whole point of the row. Every other countdown here
   counts toward something happening; this one counts toward something
   STOPPING, and the moment it stops is the only thing anybody is waiting for,
   so vanishing silently at 0:00 throws away the announcement. */
const REFLECT_CLEAR_S = 5

/* Is this reflect window over — i.e. is it safe to cast again. Separate from
   `overdue`, which it would otherwise look like: an AoE row past due means a
   cast is LATE and something is wrong, and this means the mechanic ended on
   time and the raid can go back to work. Opposite readings, opposite colours,
   and one of them is good news. */
const reflectDone = (r, at) => r.kind === 'reflect' && at >= r.ends_ts

/* How many rows the COMPACT panel may draw. The dashboard's panel is a page
   you can scroll and read; the dock and the stream overlay are a fixed scene
   with the meter drawn UNDER this, so every row here is a raider off the
   bottom — the same reason the overlay keeps a hard `max_rows` on the meter
   instead of the dashboard's fold. Rows arrive soonest-due first, so a cap
   keeps exactly the ones a raid calls out. */
const MINI_TIMER_ROWS = 3

/* What the AoE lands AS, beside its name.

   One word, because it answers a question with one word: whether the raid can
   be asked to cover this one, and by whom. The head of the breakdown is the
   pill and the rest is on the title — a dual-type hit ("7,896 crushing and 556
   disease") is one ability with two schools and the small one is a footnote,
   not a second label competing with the first for the same three characters.

   Exported because the recorded AoE tab prints the same pill and this is the
   only definition of it — the same reason `MiniParse` calls `LiveMeter`'s
   `meterRows` instead of ranking the parse a second way. */
export function DtypePill({ row }) {
  if (!row.dtype) return null
  const all = Object.entries(row.dtypes || {})
  return (
    <span className="dpill" title={all.length > 1
      ? `${all.map(([t, n]) => `${t} ${fmt.num(n)}`).join(' · ')}`
      : `${row.dtype} damage`}>{row.dtype}</span>
  )
}

/* THE TWO HAND MARKS, stacked — drawn on the full-width live panel and on the
   recorded AoE tab, which are the two surfaces anybody can click.

   Both are facts no log holds — whether the raid LEAVES for this one
   (`lib/joust.js`) and whether it is worth a slot on the mini parse
   (`lib/minipin.js`) — and both are properties of the ABILITY, so they are
   made here and they outlive the pull.

   WORDS RATHER THAN TICKS, and that is the whole change from what was here
   before. A checkbox is a control that says what it does only in its tooltip;
   these two rows are read at a glance while somebody is fighting, and mid-pull
   nobody hovers anything. A pill says what it is when it is off and says it is
   on by being lit, which is one thing to learn instead of two.

   STACKED, because the cost on this panel is HORIZONTAL. The dock and the
   overlay are as narrow as the game leaves them and the ability name is what
   has to survive; vertical space is free. Two small pills stack inside the row
   height a single line of digits already asks for.

   Both default ON for an ability ACT's list knows (`lib/marks.js: actListed`),
   so a lit pill is usually a default rather than a decision and the click that
   matters is the one that turns it OFF. That is why each pill is handed the
   state it is showing: what gets stored is the answer opposite the one on
   screen, whether that answer was somebody's or the list's.

   `stopPropagation` because the AoE tab's rows are clickable — marking an
   ability is not asking to open its cast list. */
export function MarkPills({ row, jousted, pinned }) {
  const ability = row.ability
  const pill = (kind, on, label, title, onClick) => (
    <button type="button" className={`aoepill ${kind} ${on ? 'on' : ''}`}
            title={title} aria-pressed={on}
            onClick={(e) => { e.stopPropagation(); onClick() }}>
      {label}
    </button>
  )
  return (
    <span className="aoemarks" onClick={(e) => e.stopPropagation()}>
      {pill('joust', jousted, 'Joust',
        jousted
          ? `${ability} is jousted — it drives the burn window on the dashboard,`
            + ' the in-game window and the stream overlay. Click if the raid'
            + ' stands in this one.'
          : `Mark ${ability} as one you joust`,
        () => toggleJoust(ability, jousted))}
      {pill('mini', pinned, 'Mini',
        pinned
          ? `${ability} may be drawn on the mini parse, the in-game window and`
            + ' the stream overlay (the biggest few fit). Click to keep it off'
            + ' them.'
          : `Let ${ability} onto the mini parse, the in-game window and the`
            + ' stream overlay',
        () => toggleMiniPin(ability, pinned))}
    </span>
  )
}

/* Where the number being counted came from. Three sources now, in the order
   they outrank each other (`pipeline/aoelearn.timer_for`): what this site has
   MEASURED across several clean fights, then the ACT list the raid uploaded,
   then this one pull. `learned` beating `reported` is the crowdsourced half of
   this panel — the uploaded list is where a timer starts, not where it ends. */
const PERIOD_WORD = { reported: 'timer', learned: 'measured', observed: 'seen' }

const PERIOD_NOTE = (r) => {
  const base = r.normal_period_s || r.period_s
  const src = r.period_src === 'learned'
    ? `${base}s — measured across every raid on this mob, not ACT's list`
    : r.period_src === 'reported'
      ? `${base}s — ACT's spell-timer list`
      : `${base}s — the shortest gap that repeated this fight`
  return r.normal_period_s
    ? `${src}. Counting ${r.period_s}s: a reuse debuff is on ${r.source} and`
      + ` this ability measures x${r.swipe_factor} under it.`
    : src
}

/* WHY THIS ROW HAS NO COUNTDOWN, when that is why (`aoes.several_bodies`).

   Everything here keys an enemy by NAME, which is right for a boss and wrong
   for a mob that splits: The Emerald Halls rumbler becomes two halves and then
   six thirds, all wearing one name, each on its own recast. What the gaps
   between their casts measure is the superposition, not a timer, and no number
   the panel could draw would say when the next one lands. So it draws none,
   and the reason lives on the ability's title — where a thing you might want
   to look up belongs. */
const BODIES_NOTE = (r) => ({
  splits: `Several ${r.source} are up at once — this one splits. Their casts`
    + ' interleave, so no countdown here would be right; the row still says how'
    + ' many times it has gone off.',
  instances: `This log measures ${r.source}'s casts at a clean fraction of`
    + " ACT's timer, which is what several mobs of one name look like — they"
    + " cast on their own timers. The countdown stays on ACT's number.",
}[r.several_bodies])

/* NO PILL. An earlier build put a `swiped` badge on every debuffed row, in
   three states. It went, and the reasoning is worth keeping because it applies
   to the next thing that wants a place here: a word on this panel has to change
   what somebody DOES in the next few seconds, and that one never did. The
   countdown is already the adjusted number and the tick is already the
   un-adjusted one, so the badge restated the bar in text and took the space a
   raider's name could have had. Everything it said now lives on the tick's
   title, which is where a thing you might want to look up belongs. */

/* HOW LONG THE LANDING IS MARKED.

   The one piece of motion on this panel that is not a clock, and the exception
   that proves the rule the rest of it follows: everything else holds still
   because movement with no news in it costs you your place, and this moves
   because it IS the news. A cast just landed and the countdown it resets is
   about to look exactly like a countdown that has been running for a while.
   Without a mark, the difference between "it fired" and "you looked away for
   twenty seconds" is invisible on a bar that is nearly full either way.

   The animation runs 1.4s from MOUNT and is deliberately not seeked, which is
   the opposite of what the drain does — and the difference is latency. The
   screen sees a hit about a second after the log records it ("How fast the
   screen sees a hit"), so seeking to the log stamp would drop the flash into
   the middle of its own decay and hand everybody a dim smear instead of a
   flash. A landing announced a beat late is worth far more than one announced
   faintly on time.

   This window is therefore only how long the element may stay MOUNTED: wide
   enough that the animation always finishes (1.4s of it, plus the second or so
   it took to get here), narrow enough that a stale row cannot announce a
   landing nobody is waiting for. */
const HIT_FLASH_S = 3

/* Whether this row's newest cast is still inside that window.

   Derived from `last_cast_ts` on every render rather than remembered, which is
   what keeps it stateless: no per-row refs, nothing to reset between pulls, and
   a row that mounts mid-flash (a page opened, a dock switched on) picks up the
   remainder instead of restarting or missing it. The earliest anybody can know
   a cast happened is the payload that carries it, so this fires on that render
   and no later. */
const justHit = (r, at, running) => (
  running && r.last_cast_ts != null
  && at >= r.last_cast_ts && at - r.last_cast_ts < HIT_FLASH_S
)

const mmss = (s) => {
  const n = Math.max(0, Math.ceil(s))
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`
}

/* Seconds until the next cast, or null for a row with no period to count. */
const secsLeft = (r, at) => (
  r.next_due_ts == null || !r.period_s ? null : r.next_due_ts - at
)

/* The rows still worth a countdown, which is the payload's list minus the ones
   this clock has run past the server's drop line since it was sent.

   A row with no period is measured from its LAST CAST rather than from a due
   time it does not have — the same two-sided rule `livemeter` drops on, applied
   here for the same reason the timed side is: this clock runs ahead of the
   payload by design, and a row would otherwise hold its slot for a poll after
   the server had already let it go. */
const live = (aoes, at, running, dropS, missedS) => (
  !running || !dropS ? aoes
    : aoes.filter((r) => (r.kind === 'reflect'
      /* Its own line, and a much shorter one. A reflect row is not late when
         it passes its end — it has FINISHED, which is the good outcome — so
         the AoE side's `missedS` fuse would leave an all-clear on screen for
         fifteen seconds after it stopped being news. */
      ? at - r.ends_ts <= REFLECT_CLEAR_S
      : r.next_due_ts != null
      /* a cast that had a time and did not happen: `livemeter.MISSED_S`.
         `||`, NOT `??` — the prop defaults to 0 and `0 ?? dropS` is 0, which
         would drop every row the instant it went one second past due. A
         payload from before `aoe_missed_s` existed falls back to the long
         line, which is exactly the behaviour it was built with. */
      ? at - r.next_due_ts <= (missedS || dropS)
      // nothing to be late for, so only "recently": `livemeter.OVERDUE_DROP_S`
      : at - r.last_cast_ts <= dropS))
)

/* What the compact panel is allowed to show, which is not what the dashboard's
   is. Two cuts, both about the same thing — this panel is drawn ABOVE the
   meter in a scene of fixed height, so it spends its space on rows that are
   still telling somebody when to move:

   - A row with NO countdown is dropped while the fight is RUNNING. It can only
     say "that happened twice", which is worth reading on the dashboard and is
     worth a raider's slot nowhere. It comes back the moment it has a period —
     three casts is all `observed_period` needs — and once the pull is over
     every row loses its countdown and they all belong again, which is why this
     is gated on `running` and not on the row alone.
   - Whatever is left is capped, by DAMAGE rather than by what is due next
     (see below).

   The dashboard keeps the whole list: it has the room, and the AoE nobody has
   a timer for yet is exactly the one somebody is trying to work out.

   Three cuts now, and the first one is the only one anybody controls. The MINI
   mark (`lib/minipin.js`) decides which abilities are ELIGIBLE — ACT's list by
   default, since that is what the raid decided to watch for — and the two cuts
   below decide how many of the eligible ones fit. Eligibility and capacity are
   deliberately separate: a mark says what matters, and a fixed scene is fixed
   however strongly somebody feels about a sixth countdown.

   Exported because the dock and the overlay draw this panel inside a bordered
   `.minipanel`, which is a visible strip whether or not anything is in it —
   `MiniParse` has to ask whether there will be a row BEFORE it draws the frame
   around one, and asking is the only way that test and this one stay the same
   test. */
export const miniTimers = (rows, running, pins) => {
  const eligible = rows.filter((r) => isMiniPinned(pins, r))
  const live = running ? eligible.filter((r) => r.period_s) : eligible
  if (live.length <= MINI_TIMER_ROWS) return live
  /* THE THREE BIGGEST, KEPT WHERE THEY WERE. Two separate decisions and the
     order of them is the point:

     - WHICH three is by damage, because a fixed scene with room for three has
       to spend them on the three the raid is actually fighting, and total
       damage is what says which those are. Cumulative damage only ever grows,
       so this settles within the first minute and then holds.
     - WHERE they sit is the order the payload already put them in — first cast
       first (`livemeter._live_aoes`) — NOT their damage rank. Ranking would put
       the list back in motion every time one ability overtook another, which is
       the whole thing this panel is trying to stop.

     So a swap changes at most one row, in place, and the two that stayed do not
     move. */
  const keep = new Set([...live]
    .sort((a, b) => (b.damage || 0) - (a.damage || 0))
    .slice(0, MINI_TIMER_ROWS)
    .map((r) => `${r.source}|${r.ability}`))
  return live.filter((r) => keep.has(`${r.source}|${r.ability}`))
}

/* What the panel's digits would print right now — every countdown cell plus
   the burn window's. The ticker compares this against what is on screen and
   asks for a render only when it moves. */
const printed = (aoes, at, burn, running) => aoes.map((r) => {
  const left = secsLeft(r, at)
  /* A reflect row prints two moving things, not one: the countdown, and how
     many casts the window has eaten so far — which climbs while the digits
     tick and is the row's only evidence anybody is still casting into it. Both
     go in, or the tally freezes between whole seconds. */
  if (r.kind === 'reflect') {
    return `refl:${reflectDone(r, at) ? 'clear' : mmss(left)}:${r.casts}`
  }
  const digits = left == null
    ? `${r.casts}` : (left <= 0 ? `+${mmss(-left)}` : mmss(left))
  /* The flash's window is part of what is on screen, so the ticker has to
     see it end. Without this the row keeps whatever it last rendered until a
     digit happens to change, and a landing announced for a second and a half
     stays announced for as long as the countdown reads the same. */
  return justHit(r, at, running) ? `${digits}!` : digits
}).concat(burn ? [`burn:${mmss(secsLeft(burn, at))}`] : []).join('|')

/* The drain, on the compositor.

   `aoedrain` runs from empty to full over one period; a NEGATIVE delay starts
   it partway through, which is how the bar picks up where the fight actually
   is instead of restarting whenever this is drawn. The seek is taken ONCE, at
   mount, deliberately: the delay is measured from when the animation started,
   so rewriting it on a later render would re-seek a running animation — a jump
   per payload, which is the artifact this whole file exists to avoid. The
   parent keys this on `next_due_ts`, so a genuinely new cast remounts it and
   re-seeks, and nothing else does. */
function DrainBar({ dueTs, period, at }) {
  const seek = useRef(null)
  if (seek.current === null) seek.current = Math.max(0, period - (dueTs - at))
  return (
    <i className="fill"
       style={{ animationDuration: `${period}s`, animationDelay: `-${seek.current}s` }} />
  )
}

/* WHERE THE UN-SLOWED TIMER WOULD HAVE FIRED, on a bar that is already the
   slowed one. A reuse debuff (`refdata/reuse_debuffs.json`) stretches some of a
   mob's abilities and not others, so a swiped row counts the stretched number
   from its first second and puts a tick at the normal one.

   One span, decided before the countdown starts, and it never changes length.
   The first build made the unconfirmed case plan the normal timer and then GROW
   past it, which was wrong twice over: the bar resized mid-drain, and the
   digits went overdue at the normal mark so a cast that was never late read as
   "+0:24". A bar that means one thing at 0:30 and another at 0:10 costs the
   person reading it their place, and they are reading it while fighting.

   The tick keeps everything the growth was trying to say. A cast landing ON it
   says this ability is immune; one landing at the END says the stretch is real.
   Both readings are available at a glance, all fight, without the bar moving. */
function NormalMark({ row }) {
  if (!row.normal_period_s || !row.period_s) return null
  const frac = row.normal_period_s / row.period_s
  if (!(frac > 0) || frac >= 1) return null
  return (
    <i className="mark" style={{ left: `${frac * 100}%` }}
       title={`Un-slowed this is ${row.normal_period_s}s. A reuse debuff is on`
         + ` ${row.source}, so the bar runs to ${row.period_s}s.`} />
  )
}

/* STOP CASTING. The other row on this panel that is an instruction rather than
   a reading, and the only one whose countdown ends in good news.

   It is a DURATION, not a period. Every other row here is anchored on a cast
   and counts toward the next one; this is anchored on the mob entering a state
   and counts toward it leaving. The drain bar is reused unchanged — the server
   sends the end as `next_due_ts` precisely so it can be (`livemeter.
   _live_reflect`) — but nothing else about the row is the same shape:

   - There is no "next one". The mechanic is health-triggered, and nothing here
     predicts it. What the row says is how long the CURRENT window has left.
   - It cannot appear until somebody has already been reflected, because the
     game announces the window nowhere. Measured, that costs the raid about 5%
     of the casts a window eats; the row is for the other 95%.
   - The tally is who is still paying. It counts casts EATEN, live, which is
     the one number that says whether the call is being heard.

   Ending is the announcement, so the row holds its slot for `REFLECT_CLEAR_S`
   and says CLEAR rather than vanishing at 0:00 — the moment it stops is the
   moment everybody is waiting for. */
function ReflectRow({ row, at, running }) {
  const left = secsLeft(row, at)
  const done = !running || reflectDone(row, at)
  return (
    <div className={`aoerow reflect ${done ? 'clear' : ''}`}>
      {running && !done && (
        <DrainBar key={row.started_ts} dueTs={row.next_due_ts}
                  period={row.period_s} at={at} />
      )}
      <span className="ab">{done ? 'Reflect over' : 'REFLECT — stop casting'}</span>
      <span className="src"
            title={`${row.source} is reflecting spells back at whoever casts`
              + ` them, for ${row.window_s}s. Nothing in the log announces this`
              + ` window, so it is measured from the first reflected cast —`
              + ` which means it may already have been up for a moment.`}>
        {row.source}
      </span>
      {/* What it has cost so far: casts refused, and the damage those casts
          put back into the people who threw them. Both are the whole argument
          for the row, so both are on it rather than on a title. */}
      <span className="split"
            title={`${row.casts} cast${row.casts === 1 ? '' : 's'} reflected`
              + ` from ${row.casters} raider${row.casters === 1 ? '' : 's'}`
              + `${row.damage ? `, for ${fmt.num(row.damage)} back at them` : ''}`}>
        <b className="hitn">{row.casts}</b> eaten
        {row.damage > 0 && <>{' · '}<b className="blkn">{fmt.num(row.damage)}</b></>}
      </span>
      <span className="cd">
        {done
          ? <em className="clearnow" key={row.ends_ts}>CLEAR</em>
          : <b className="digits">{mmss(left)}</b>}
      </span>
    </div>
  )
}

/* The instruction row. Same clock, same drain, read the other way round: this
   is time IN melee, and it belongs to whichever jousted cast lands first. */
function BurnRow({ row, at }) {
  const left = secsLeft(row, at)
  if (left == null) return null
  const warn = left <= JOUST_WARN_S
  return (
    <div className={`aoerow burn ${warn ? 'out' : ''}`}>
      <DrainBar key={row.next_due_ts} dueTs={row.next_due_ts}
                period={row.period_s} at={at} />
      <span className="ab">Burn window</span>
      <span className="src" title={`until ${row.source}'s ${row.ability}`}>
        to {row.ability}
      </span>
      <span className="cd">
        {left > 0
          ? <b className="digits">{mmss(left)}</b>
          : <b className="digits over">+{mmss(-left)}</b>}
        {/* keyed on the cast, so it mounts — and so flashes — once per window
            rather than restarting on every render inside the warning */}
        {warn && <em key={row.next_due_ts} className="joustnow">JOUST</em>}
      </span>
    </div>
  )
}

export default function AoeTimers({
  aoes = [], logTs, compact = false, running = true,
  editable = false, showSuggest = false, showBurn = true, showRows = true,
  dropS = 0, missedS = 0,
}) {
  const logNow = useLogClock(logTs)
  const onScreen = useRef('')
  const [, bump] = useState(0)
  const jousted = useJoust()
  const pinned = useMiniPins()

  /* One reading of the clock for the whole render — the drain's seek, the
     digits and the drop line all have to be talking about the same instant. */
  const at = logNow()
  const dropped = live(aoes, at, running, dropS, missedS)
  const rows = compact ? miniTimers(dropped, running, pinned) : dropped
  /* TWO SWITCHES, and they are the dock's alone (`MiniRail`'s config). The
     burn window is the one row here that is an instruction rather than a
     reading, so a raid that does not joust this fight wants its slot back; and
     the notification block draws this same panel a size up, sometimes for the
     burn window ALONE. Neither switch touches which cast OWNS the window —
     that is still the JOUST mark — only whether the row is drawn.

     `burn` is computed from the whole row set even when the rows are hidden:
     the window belongs to the soonest jousted cast whether or not its
     countdown is on screen. */
  const burn = running && showBurn ? nextJoust(rows, jousted, at, missedS) : null
  const shown = showRows ? rows : []

  useTicker(() => {
    if (printed(shown, logNow(), burn, running) !== onScreen.current) bump((n) => n + 1)
  }, running && aoes.length > 0)

  onScreen.current = printed(shown, at, burn, running)

  if (!shown.length && !burn) return null

  return (
    <div className={`aoetimers ${compact ? 'mini' : ''}`}>
      {!compact && <div className="aoehead">Spell timers</div>}
      {shown.map((r) => {
        /* Its own row, because it is its own kind of countdown — a state
           ending, not a cast arriving. Everything below assumes the latter. */
        if (r.kind === 'reflect') {
          return <ReflectRow key={`${r.source}|${r.ability}`} row={r}
                             at={at} running={running} />
        }
        const left = running ? secsLeft(r, at) : null
        const overdue = left != null && left <= 0
        const hit = justHit(r, at, running)
        const blocked = r.last_blocked || 0
        return (
          <div key={`${r.source}|${r.ability}`}
               className={`aoerow ${overdue ? 'due' : ''}`
                 + `${r.swiped ? ' swiped' : ''}`
                 + `${isJousted(jousted, r) ? ' jousted' : ''}`}>
            {left != null && (
              <>
                <DrainBar key={r.next_due_ts} dueTs={r.next_due_ts}
                          period={r.period_s} at={at} />
                <NormalMark row={r} />
              </>
            )}
            {/* It just landed. Keyed on the cast so it mounts once per landing
                and plays once, rather than restarting on every render inside
                the window. */}
            {hit && <i className="hitflash" key={r.last_cast_ts} />}
            {/* The two facts on this panel a log cannot supply: whether the
                raid LEAVES for this one, and whether it is worth a slot on the
                mini parse. Jousting turns this row's countdown into the burn
                window at the bottom. */}
            {editable && (
              <MarkPills row={r} jousted={isJousted(jousted, r)}
                         pinned={isMiniPinned(pinned, r)} />
            )}
            {/* NOT A WORD BESIDE THE NAME, on purpose. That this name is worn
                by several bodies explains a countdown that is MISSING, and a
                missing countdown is already visible; a pill saying so would be
                a word that never changes what anybody does in the next few
                seconds, which is the same test the `swiped` badge failed. */}
            <span className="ab" title={BODIES_NOTE(r)}>{r.ability}</span>
            {/* What it lands AS is a question you ask AFTER the pull, working
                out who can be asked to cover it. The compact panel is read
                DURING one, in a fixed scene the meter is drawn under, and a
                word that never changes the next thing you do is space a raider
                could have had. Full panel and recorded tab keep it. */}
            {!compact && <DtypePill row={r} />}
            <span className="src">{r.source}</span>
            {r.last_targets > 0 && (
              <span className="split"
                    title={`Last cast: ${r.last_hit} hit`
                      + (blocked ? `, ${blocked} avoided or absorbed it` : '')
                      + ` (${r.casts} cast${r.casts === 1 ? '' : 's'} this fight)`}>
                <b className="hitn">{r.last_hit}</b> hit
                {blocked > 0 && <>
                  {' · '}
                  <b className="blkn">{blocked}</b> blocked
                </>}
              </span>
            )}
            {left != null ? (
              <span className="cd" title={PERIOD_NOTE(r)}>
                {/* Overdue counts up from the number the bar was actually
                    running to — the swiped one on a swiped row. Counting from
                    the normal mark instead made a cast that was never late
                    open at "+0:24". */}
                {/* HIT! takes the word slot rather than the number's: the
                    countdown has just been reset by the very cast being
                    announced, and it is the reading somebody wants back the
                    moment the flash clears. */}
                {overdue
                  ? <b className="digits over">+{mmss(-left)}</b>
                  : <b className="digits">{mmss(left)}</b>}
                {hit
                  ? <em className="hitnow" key={r.last_cast_ts}>HIT!</em>
                  : overdue && <em className="overlabel">overdue</em>}
                {/* WHERE THE NUMBER CAME FROM — `measured`/`timer`/`seen` —
                    and not on the compact panel. It is a word about the
                    countdown's provenance rather than about the fight, which
                    makes it a thing you look up once (it is still on the
                    cell's title, with the period, via `PERIOD_NOTE`) and never
                    the thing you read mid-pull. At 244px, with the game behind
                    it, those eight characters are width the ability's name and
                    the digits both want. The full panel keeps it: that is
                    where the timer is worked out. */}
                {!compact && (
                  <em className={r.period_src}>{PERIOD_WORD[r.period_src] || 'seen'}</em>
                )}
                {/* Not applied, only offered — see the header note. */}
                {showSuggest && r.suggested_s && (
                  <em className="suggest"
                      title={`This log measures ${r.suggested_s}s between casts, not`
                        + ` ${r.period_s}s. The countdown still uses ${r.period_s}s.`}>
                    ⇢{r.suggested_s}s
                  </em>
                )}
              </span>
            ) : (
              /* No period to count, or nothing left to count toward: how many
                 times it fired, which is the honest reading either way. */
              <span className="cd muted">{r.casts}×</span>
            )}
          </div>
        )
      })}
      {burn && <BurnRow row={burn} at={at} />}
    </div>
  )
}
