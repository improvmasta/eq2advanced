import { useEffect, useRef, useState } from 'react'

/* When the next raid-wide hit is due.

   The server detects casts (`pipeline/livemeter.py`) and says what the period
   is and where it came from: `reported` is ACT's spell-timer list — what the
   raid was TOLD to expect — and `observed` is the shortest gap that has
   repeated in this fight. The distinction stays on screen, because a countdown
   nobody can source is a countdown nobody should trust.

   The bar drains locally between partials. The server ticks every couple of
   seconds; a countdown that only moved that often would read as broken, so the
   clock here runs on the browser's own frame and the payload corrects it. */

const tick = () => Math.floor(Date.now() / 1000)

export default function AoeTimers({ aoes = [], logTs }) {
  const [now, setNow] = useState(tick)
  /* Countdowns are in LOG time; the browser only has its own. Anchoring on the
     moment each payload arrived keeps the two in step without trusting the
     browser's clock to agree with the server's. */
  const anchor = useRef({ log: logTs, wall: tick() })
  if (logTs && logTs !== anchor.current.log) anchor.current = { log: logTs, wall: tick() }

  useEffect(() => {
    const id = setInterval(() => setNow(tick()), 500)
    return () => clearInterval(id)
  }, [])

  if (!aoes.length) return null

  const at = anchor.current.log + (now - anchor.current.wall)

  return (
    <div className="aoetimers">
      <div className="aoehead">Raid-wide</div>
      {aoes.map((r) => {
        const due = r.next_due_ts
        const left = due == null ? null : due - at
        const pct = due == null || !r.period_s ? 0
          : Math.max(0, Math.min(100, 100 * (1 - left / r.period_s)))
        const overdue = left != null && left <= 0
        return (
          <div key={`${r.source}|${r.ability}`}
               className={`aoerow ${overdue ? 'due' : ''}`}>
            <i className="fill" style={{ width: `${pct}%` }} />
            <span className="ab">{r.ability}</span>
            <span className="src">{r.source}</span>
            {r.period_s ? (
              <span className="cd" title={r.period_src === 'reported'
                ? `${r.period_s}s — ACT's spell-timer list`
                : `${r.period_s}s — the shortest gap that repeated this fight`}>
                {overdue ? 'due' : `${Math.ceil(left)}s`}
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
