import { useMemo } from 'react'
import { fmt } from '../lib/api.js'
import { autoPct, castsPerMin, critPct, procPct } from '../lib/stats.js'
import { ClassChip } from './Identity.jsx'

/* Side-by-side comparison of checked combatants: metrics as rows, one column
   per raider, best value per row highlighted. Numbers only — no bars: each cell
   prints the value and, off the leader, the gap in percent, so "who is ahead"
   and "by how much" both read straight off the table. Computed from the
   already-loaded agg + report data, no extra requests. */

const healedOf = (a) => (a.heals || 0) + (a.wards_absorbed || 0)

export default function ComparePanel({ actors, keys, derived, repRows, duration, onRemove }) {
  const picked = useMemo(
    () => keys.map((k) => actors.find((a) => a.key === k)).filter(Boolean),
    [actors, keys])

  const sections = useMemo(() => {
    const rep = (a) => repRows?.[a.name]
    const defs = [
      ['Damage', [
        // rates only — the totals are the same ranking with more digits
        { label: 'DPS', get: (a) => (a.damage || 0) / duration, fmt: fmt.num2 },
        { label: 'Crit %', get: (a) => critPct(derived[a.key]), fmt: (v) => `${Math.round(v)}%` },
        { label: 'Autoattack %', get: (a) => autoPct(derived[a.key]), fmt: (v) => `${Math.round(v)}%`, neutral: true },
        { label: 'Proc %', get: (a) => procPct(derived[a.key]), fmt: (v) => `${Math.round(v)}%`, neutral: true },
        { label: 'Casts/min', get: (a) => castsPerMin(derived[a.key], duration), fmt: (v) => v.toFixed(1) },
        { label: 'Avg delay', get: (a) => a.avg_delay_s, fmt: (v) => `${v.toFixed(2)}s`, worse: true },
        { label: 'Engage delay', get: (a) => rep(a)?.avg_engage_delay_s, fmt: (v) => `${v}s`, worse: true },
      ]],
      ['Healing', [
        // wards are healing here — one HPS line, not heals-vs-wards bookkeeping
        { label: 'HPS', get: (a) => healedOf(a) / duration, fmt: fmt.num2 },
        { label: 'Overheal', get: (a) => rep(a)?.overheal_est, fmt: fmt.num, worse: true },
        { label: 'Cures', get: (a) => a.cure_count, fmt: (v) => v },
        { label: 'Power repl', get: (a) => a.power_fed, fmt: fmt.num },
      ]],
      ['Survival', [
        { label: 'Damage taken', get: (a) => a.damage_taken, fmt: fmt.num, worse: true },
        { label: 'Deaths', get: (a) => a.deaths, fmt: (v) => v, worse: true },
        { label: 'Time dead', get: (a) => rep(a)?.time_dead_s, fmt: fmt.dur, worse: true },
        { label: 'Dmg lost dead', get: (a) => rep(a)?.death_dps_lost, fmt: fmt.num, worse: true },
      ]],
    ]
    return defs
      .map(([title, metrics]) => [title, metrics
        .map((d) => ({ ...d, vals: picked.map((a) => d.get(a)) }))
        .filter((d) => d.vals.some((v) => v != null && v !== 0))])
      .filter(([, metrics]) => metrics.length)
  }, [picked, derived, repRows, duration])

  if (picked.length < 2) return null

  const bestIdx = (m) => {
    const nums = m.vals.map((v) => (v == null ? null : v))
    const present = nums.filter((v) => v != null)
    if (m.neutral || present.length < 2) return -1
    const target = m.worse ? Math.min(...present) : Math.max(...present)
    // no winner when everyone ties
    if (present.every((v) => v === target)) return -1
    return nums.indexOf(target)
  }

  return (
    <aside className="actorpanel card comparepanel">
      <div className="tablewrap">
        <table className="data cmptable">
          <colgroup>
            <col className="cmplabelcol" />
            {picked.map((a) => <col key={a.key} />)}
          </colgroup>
          <thead>
            <tr>
              <th className="l">Head to head</th>
              {picked.map((a) => (
                <th key={a.key}>
                  <button className="cmphead" title="Remove from comparison" onClick={() => onRemove(a.key)}>
                    {a.name}
                  </button>
                  <div className="cls"><ClassChip actor={a} /></div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sections.map(([title, metrics]) => (
              [
                <tr key={title} className="cmpsection">
                  <td className="l" colSpan={picked.length + 1}>{title}</td>
                </tr>,
                ...metrics.map((m) => {
                  const best = bestIdx(m)
                  const lead = best >= 0 ? m.vals[best] : null
                  return (
                    <tr key={`${title}:${m.label}`}>
                      <td className="l lbl">{m.label}</td>
                      {m.vals.map((v, i) => {
                        // a real zero is an answer — "0 deaths" is the whole
                        // point of the row, and printing "—" there made the
                        // winning column look like missing data
                        const has = v != null
                        // gap to the row's leader, signed the way the metric
                        // reads: for "lower is better" rows, over the leader
                        // is the bad direction. No percentage off a zero base.
                        const gap = has && lead && i !== best
                          ? Math.round((100 * (v - lead)) / Math.abs(lead)) : null
                        return (
                          <td key={picked[i].key} className={i === best ? 'best' : ''}>
                            <span className="cmpval">
                              <span className="v">{has ? m.fmt(v) : '—'}</span>
                              {/* the gutter says one thing per cell: the leader
                                  is marked, everyone else carries their gap */}
                              {i === best ? (
                                <span className="d lead" title="Best in this row">▲</span>
                              ) : gap != null && gap !== 0 ? (
                                <span className="d">{gap > 0 ? '+' : ''}{gap}%</span>
                              ) : null}
                            </span>
                          </td>
                        )
                      })}
                    </tr>
                  )
                }),
              ]
            ))}
          </tbody>
        </table>
      </div>
    </aside>
  )
}
