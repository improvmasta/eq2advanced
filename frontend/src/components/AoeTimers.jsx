import { useRef, useState } from 'react'
import { useTicker } from '../lib/smooth.js'

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
   clock here runs on the browser's own frame and the payload corrects it.

   It runs in FRACTIONS of a second on the shared ticker (lib/smooth.js), which
   is what the bar wants — 20 samples a second is a drain you can watch rather
   than a bar stepping once a second. The DIGITS still change on the second,
   because they are a countdown: `ceil` means "0:01" is up on screen until the
   cast is actually due. */

const wall = () => Date.now() / 1000

const mmss = (s) => {
  const n = Math.max(0, Math.ceil(s))
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`
}

export default function AoeTimers({ aoes = [], logTs, compact = false }) {
  const [now, setNow] = useState(wall)
  /* Countdowns are in LOG time; the browser only has its own. Anchoring on the
     moment each payload arrived keeps the two in step without trusting the
     browser's clock to agree with the server's. */
  const anchor = useRef({ log: logTs, wall: wall() })
  if (logTs && logTs !== anchor.current.log) anchor.current = { log: logTs, wall: wall() }

  useTicker(() => setNow(wall()), aoes.length > 0)

  if (!aoes.length) return null

  const at = anchor.current.log + (now - anchor.current.wall)

  return (
    <div className={`aoetimers ${compact ? 'mini' : ''}`}>
      {!compact && <div className="aoehead">Spell timers</div>}
      {aoes.map((r) => {
        const due = r.next_due_ts
        const left = due == null ? null : due - at
        const pct = due == null || !r.period_s ? 0
          : Math.max(0, Math.min(100, 100 * (1 - left / r.period_s)))
        const overdue = left != null && left <= 0
        const blocked = r.last_blocked || 0
        return (
          <div key={`${r.source}|${r.ability}`}
               className={`aoerow ${overdue ? 'due' : ''}`}>
            <i className="fill" style={{ width: `${pct}%` }} />
            <span className="ab">{r.ability}</span>
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
            {r.period_s ? (
              <span className="cd" title={r.period_src === 'reported'
                ? `${r.period_s}s — ACT's spell-timer list`
                : `${r.period_s}s — the shortest gap that repeated this fight`}>
                {overdue
                  ? <b className="digits over">+{mmss(-left)}</b>
                  : <b className="digits">{mmss(left)}</b>}
                {overdue && <em className="overlabel">overdue</em>}
                <em className={r.period_src}>{r.period_src === 'reported' ? 'timer' : 'seen'}</em>
              </span>
            ) : (
              <span className="cd muted">{r.casts}×</span>
            )}
          </div>
        )
      })}
    </div>
  )
}
