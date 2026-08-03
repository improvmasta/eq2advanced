import { useMemo } from 'react'
import { fmt } from '../lib/api.js'

/* The last seconds before a death, incoming and healing interleaved. "What
   killed me, and was anyone healing me" is the question after every wipe, and
   the events to answer it were always in the log. */
export default function DeathRecap({ death, windowS, onClose }) {
  const rows = useMemo(() => {
    if (!death) return []
    const inc = (death.incoming || []).map((e) => ({ ...e, dir: 'in' }))
    const heal = (death.healing || []).map((e) => ({ ...e, dir: 'heal' }))
    return [...inc, ...heal].sort((a, b) => a.t - b.t)
  }, [death])

  if (!death) return null
  const worst = Math.max(1, ...rows.map((r) => r.amount || 0))

  return (
    <div className="card">
      <div className="drillhead">
        <h2>Death recap — {death.name}</h2>
        <span className="muted">
          {death.encounter_name || 'trash'} · {fmt.time(death.ts)} · last {windowS}s
        </span>
        <button className="chip closex" style={{ marginLeft: 'auto' }} onClick={onClose} aria-label="Close recap">✕</button>
      </div>
      <div className="metrics">
        <div className="metric"><div className="v">{fmt.num(death.incoming_total)}</div><div className="k">Damage taken</div></div>
        <div className="metric"><div className="v">{fmt.num(death.healing_total)}</div><div className="k">Healing received</div></div>
        <div className="metric"><div className="v">{(death.incoming || []).length}</div><div className="k">Hits</div></div>
        <div className="metric"><div className="v">{(death.healing || []).length}</div><div className="k">Heals</div></div>
      </div>
      {!rows.length && <p className="muted">No events in the window — the killing blow is all the log kept.</p>}
      {rows.length > 0 && (
        <div className="tablewrap">
          <table className="data recap">
            <thead>
              <tr><th>T−</th><th className="l">Source</th><th className="l">Ability</th><th>Amount</th><th className="l" /></tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className={r.dir === 'in' ? 'inrow' : 'healrow'}>
                  <td>{Math.abs(r.t).toFixed(1)}s</td>
                  <td className="l name">{r.source || 'unknown'}</td>
                  <td className="l">{r.ability || (r.dir === 'in' ? 'melee' : r.kind)}</td>
                  <td>{r.dir === 'in' ? '−' : '+'}{fmt.num(r.amount)}</td>
                  <td className="l">
                    <span className="recapbar">
                      <i style={{ width: `${(r.amount / worst) * 100}%` }} />
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(death.incoming_truncated || death.healing_truncated) && (
        <p className="note">Only the most recent events in the window are shown.</p>
      )}
    </div>
  )
}
