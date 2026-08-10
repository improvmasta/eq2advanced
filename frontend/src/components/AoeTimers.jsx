import { useRef, useState } from 'react'
import { fmt } from '../lib/api.js'
import { useLogClock, useTicker } from '../lib/smooth.js'
import { nextJoust, toggleJoust, useJoust } from '../lib/joust.js'

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

   THE BURN WINDOW is the last row and the only one that is not an ability.
   Tick an AoE as one the raid jousts (`lib/joust.js`) and its countdown gets
   read the other way round: the same seconds, but as how long there is to
   stand in melee rather than as how long until something lands. It is the
   soonest jousted cast that owns it, it is coloured differently because it is
   an instruction rather than a reading, and inside JOUST_WARN_S it says so in
   the clear.

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

/* How many rows the COMPACT panel may draw. The dashboard's panel is a page
   you can scroll and read; the dock and the stream overlay are a fixed scene
   with the meter drawn UNDER this, so every row here is a raider off the
   bottom — the same reason the overlay keeps a hard `max_rows` on the meter
   instead of the dashboard's fold. Rows arrive soonest-due first, so a cap
   keeps exactly the ones a raid calls out. */
const MINI_TIMER_ROWS = 4

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
const live = (aoes, at, running, dropS) => (
  !running || !dropS ? aoes
    : aoes.filter((r) => at - (r.next_due_ts ?? r.last_cast_ts) <= dropS)
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
   - Whatever is left is capped. Sorted soonest-due first, so the cut falls on
     the ones furthest from mattering.

   The dashboard keeps the whole list: it has the room, and the AoE nobody has
   a timer for yet is exactly the one somebody is trying to work out.

   Exported because the dock and the overlay draw this panel inside a bordered
   `.minipanel`, which is a visible strip whether or not anything is in it —
   `MiniParse` has to ask whether there will be a row BEFORE it draws the frame
   around one, and asking is the only way that test and this one stay the same
   test. */
export const miniTimers = (rows, running) => (
  (running ? rows.filter((r) => r.period_s) : rows).slice(0, MINI_TIMER_ROWS)
)

/* What the panel's digits would print right now — every countdown cell plus
   the burn window's. The ticker compares this against what is on screen and
   asks for a render only when it moves. */
const printed = (aoes, at, burn) => aoes.map((r) => {
  const left = secsLeft(r, at)
  return left == null ? `${r.casts}` : (left <= 0 ? `+${mmss(-left)}` : mmss(left))
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
  editable = false, showSuggest = false, dropS = 0,
}) {
  const logNow = useLogClock(logTs)
  const onScreen = useRef('')
  const [, bump] = useState(0)
  const jousted = useJoust()

  /* One reading of the clock for the whole render — the drain's seek, the
     digits and the drop line all have to be talking about the same instant. */
  const at = logNow()
  const dropped = live(aoes, at, running, dropS)
  const rows = compact ? miniTimers(dropped, running) : dropped
  const burn = running ? nextJoust(rows, jousted) : null

  useTicker(() => {
    if (printed(rows, logNow(), burn) !== onScreen.current) bump((n) => n + 1)
  }, running && aoes.length > 0)

  onScreen.current = printed(rows, at, burn)

  if (!rows.length) return null

  return (
    <div className={`aoetimers ${compact ? 'mini' : ''}`}>
      {!compact && <div className="aoehead">Spell timers</div>}
      {rows.map((r) => {
        const left = running ? secsLeft(r, at) : null
        const overdue = left != null && left <= 0
        const blocked = r.last_blocked || 0
        return (
          <div key={`${r.source}|${r.ability}`}
               className={`aoerow ${overdue ? 'due' : ''}${jousted.has(r.ability) ? ' jousted' : ''}`}>
            {left != null && (
              <DrainBar key={r.next_due_ts} dueTs={r.next_due_ts}
                        period={r.period_s} at={at} />
            )}
            {/* The one fact on this panel a log cannot supply: whether the raid
                LEAVES for this one. Ticking it turns the row's countdown into
                the burn window at the bottom. */}
            {editable && (
              <label className="joustbox"
                     title={jousted.has(r.ability)
                       ? `${r.ability} is jousted — it drives the burn window`
                       : `Mark ${r.ability} as one you joust`}>
                <input type="checkbox" checked={jousted.has(r.ability)}
                       onChange={() => toggleJoust(r.ability)} />
              </label>
            )}
            <span className="ab">{r.ability}</span>
            <DtypePill row={r} />
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
              <span className="cd" title={r.period_src === 'reported'
                ? `${r.period_s}s — ACT's spell-timer list`
                : `${r.period_s}s — the shortest gap that repeated this fight`}>
                {overdue
                  ? <b className="digits over">+{mmss(-left)}</b>
                  : <b className="digits">{mmss(left)}</b>}
                {overdue && <em className="overlabel">overdue</em>}
                <em className={r.period_src}>{r.period_src === 'reported' ? 'timer' : 'seen'}</em>
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
