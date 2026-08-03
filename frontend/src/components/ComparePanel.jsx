import { useMemo } from 'react'
import { fmt } from '../lib/api.js'
import { autoPct, castsPerMin, critPct } from '../lib/stats.js'

/* Side-by-side comparison of checked combatants: one section per metric,
   grouped horizontal bars normalized to the best value in the set. Computed
   from the already-loaded agg + report data — no extra requests. */
export default function ComparePanel({ actors, keys, derived, repRows, duration, onRemove, onClear }) {
  const picked = useMemo(
    () => keys.map((k) => actors.find((a) => a.key === k)).filter(Boolean),
    [actors, keys])

  const metrics = useMemo(() => {
    const rep = (a) => repRows?.[a.name]
    const defs = [
      { label: 'DPS', get: (a) => a.dps, fmt: fmt.num },
      { label: 'Damage', get: (a) => a.damage, fmt: fmt.num },
      { label: 'Crit %', get: (a) => critPct(derived[a.key]), fmt: (v) => `${Math.round(v)}%` },
      { label: 'Autoattack %', get: (a) => autoPct(derived[a.key]), fmt: (v) => `${Math.round(v)}%` },
      { label: 'Casts/min', get: (a) => castsPerMin(derived[a.key], duration), fmt: (v) => v.toFixed(1) },
      { label: 'HPS', get: (a) => (a.heals || 0) / duration, fmt: fmt.num },
      { label: 'Heals', get: (a) => a.heals, fmt: fmt.num },
      { label: 'Overheal', get: (a) => rep(a)?.overheal_est, fmt: fmt.num },
      { label: 'Saves', get: (a) => rep(a)?.saves, fmt: (v) => v },
      { label: 'Wards', get: (a) => a.wards_absorbed, fmt: fmt.num },
      { label: 'Cures', get: (a) => a.cure_count, fmt: (v) => v },
      { label: 'Damage taken', get: (a) => a.damage_taken, fmt: fmt.num, worse: true },
      { label: 'Deaths', get: (a) => a.deaths, fmt: (v) => v, worse: true },
      { label: 'Dmg lost dead', get: (a) => rep(a)?.death_dps_lost, fmt: fmt.num, worse: true },
      { label: 'Engage delay', get: (a) => rep(a)?.avg_engage_delay_s, fmt: (v) => `${v}s`, worse: true },
    ]
    return defs
      .map((d) => ({ ...d, vals: picked.map((a) => ({ a, v: d.get(a) })) }))
      .filter((d) => d.vals.some(({ v }) => v != null && v !== 0))
  }, [picked, derived, repRows, duration])

  if (picked.length < 2) return null

  return (
    <aside className="actorpanel card comparepanel">
      <div className="drillhead">
        <h2>Compare ({picked.length})</h2>
        <button className="chip closex" onClick={onClear} aria-label="Clear comparison">✕</button>
      </div>
      <div className="chips" style={{ marginBottom: 10 }}>
        {picked.map((a) => (
          <button key={a.key} className="chip on" title="Remove from comparison"
                  onClick={() => onRemove(a.key)}>
            {a.name} ✕
          </button>
        ))}
      </div>
      {metrics.map((m) => {
        const max = Math.max(...m.vals.map(({ v }) => v || 0), 1e-9)
        return (
          <div className="cmpmetric" key={m.label}>
            <div className="cmplabel">{m.label}</div>
            {m.vals.map(({ a, v }) => (
              <div className="cmprow" key={a.key}>
                <span className="cmpname">{a.name}</span>
                <span className={`cmpbar ${m.worse ? 'worse' : ''}`}>
                  <i style={{ width: `${Math.max(((v || 0) / max) * 100, v ? 2 : 0)}%` }} />
                </span>
                <span className="cmpval">{v != null ? m.fmt(v) : '—'}</span>
              </div>
            ))}
          </div>
        )
      })}
    </aside>
  )
}
