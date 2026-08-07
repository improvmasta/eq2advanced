import { useMemo } from 'react'
import { fmt } from '../lib/api.js'

/* The last seconds before a death, incoming and healing interleaved. "What
   killed me, and was anyone healing me" is the question after every wipe, and
   the events to answer it were always in the log.

   Two dresses. `inline` is the one the death list uses — it opens INSIDE the
   row you clicked, so the events sit under the name they belong to and the
   heading is that row. Standalone (a card with its own head and a ✕) is what
   anything opening a recap on its own still gets. */
export default function DeathRecap({ death, windowS, onClose, inline }) {
  const rows = useMemo(() => {
    if (!death) return []
    const inc = (death.incoming || []).map((e) => ({ ...e, dir: 'in' }))
    const heal = (death.healing || []).map((e) => ({ ...e, dir: 'heal' }))
    return [...inc, ...heal].sort((a, b) => a.t - b.t)
  }, [death])

  if (!death) return null

  /* One line of facts, not four stat tiles. The tiles were a quarter of the
     card's height saying four numbers, and this list now opens inside a table
     row in a column beside another one — height is the scarce thing. */
  const facts = (
    <div className="factline">
      <span title={`Everything that hit ${death.name} in the ${windowS}s before they died`}>
        Took <b>{fmt.num(death.incoming_total)}</b>
      </span>
      <span title={`Heals and wards that landed on ${death.name} in the same ${windowS}s`}>
        Healed <b>{fmt.num(death.healing_total)}</b>
      </span>
      <span>{(death.incoming || []).length} hits</span>
      <span>{(death.healing || []).length} heals</span>
    </div>
  )

  /* No bars. A per-row length is a chart of one column of numbers that are
     already right there, and in a narrow column it cost more width than the
     amounts it was illustrating. */
  const body = (
    <>
      {facts}
      {!rows.length && <p className="muted">No events in the window — the killing blow is all the log kept.</p>}
      {rows.length > 0 && (
        <div className="tablewrap">
          <table className="data recap">
            <thead>
              <tr>
                {/* The wall clock, like the tank column's two tables — whole
                    seconds, because an EQ2 log stamps whole epoch seconds and a
                    `.0` here was precision the data never had. */}
                <th className="l" title={`The ${windowS}s before the death; the last row is the second they died in`}>Time</th>
                <th className="l">Source</th><th className="l">Ability</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className={r.dir === 'in' ? 'inrow' : 'healrow'}>
                  <td className="l">{fmt.clockS(death.ts + Math.round(r.t))}</td>
                  <td className="l name">{r.source || 'unknown'}</td>
                  <td className="l">{r.ability || (r.dir === 'in' ? 'melee' : r.kind)}</td>
                  <td>{r.dir === 'in' ? '−' : '+'}{fmt.num(r.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(death.incoming_truncated || death.healing_truncated) && (
        <p className="note">Only the most recent events in the window are shown.</p>
      )}
    </>
  )

  if (inline) {
    return (
      <div className="recapin">
        <p className="note recapin-head">
          The {windowS}s before {death.name} died, incoming and healing together.
        </p>
        {body}
      </div>
    )
  }

  return (
    <div className="card">
      <div className="drillhead">
        <h2>Death recap — {death.name}</h2>
        <span className="muted">
          {death.encounter_name || 'trash'} · {fmt.timeS(death.ts)} · last {windowS}s
        </span>
        <button className="chip closex" style={{ marginLeft: 'auto' }} onClick={onClose} aria-label="Close recap">✕</button>
      </div>
      {body}
    </div>
  )
}
