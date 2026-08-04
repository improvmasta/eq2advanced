import { fmt } from '../lib/api.js'

/* Head-to-head for checked raids — the same table language as ComparePanel
   (metrics as rows, one column per raid, leader marked, everyone else carrying
   their gap), built entirely from the already-loaded list rows. Checking two
   nights of the same zone answers "was tonight better" without opening either.

   Only the DPS rows have a winner: more fights or more raiders is a different
   night, not a better one, so those rows are context and stay unmarked. */

const peak = (r) => (r.spark?.length ? Math.max(...r.spark) : null)

const METRICS = [
  { label: 'Raid DPS', get: (r) => r.raid_dps || null, fmt: fmt.num },
  { label: 'Peak fight DPS', get: peak, fmt: fmt.num },
  { label: 'Fights', get: (r) => r.encounter_count, fmt: (v) => v, neutral: true },
  { label: 'Raiders', get: (r) => r.raider_count || null, fmt: (v) => v, neutral: true },
  { label: 'Combat', get: (r) => r.combat_s, fmt: fmt.dur, neutral: true },
  {
    label: 'Duration', neutral: true, fmt: fmt.durH,
    get: (r) => (r.ended_ts && r.started_ts ? r.ended_ts - r.started_ts : null),
  },
]

export default function RaidCompare({ runs, onRemove, onCompareParses }) {
  if (runs.length < 2) return null
  const metrics = METRICS
    .map((m) => ({ ...m, vals: runs.map((r) => m.get(r)) }))
    .filter((m) => m.vals.some((v) => v != null && v !== 0))

  /* Past three raids the columns are narrower than the zone names, so the
     table keeps a floor width and the card scrolls sideways inside itself —
     the alternative is either unreadable columns or a page that scrolls. */
  const minWidth = 92 + runs.length * 124

  const bestIdx = (m) => {
    const present = m.vals.filter((v) => v != null)
    if (m.neutral || present.length < 2) return -1
    const target = Math.max(...present)
    if (present.every((v) => v === target)) return -1
    return m.vals.indexOf(target)
  }

  return (
    <div className="card raidcmp">
      {/* The parse comparison is the good half of this feature and it was
          hidden in a row of chips, so it leads — the summary below is what you
          read on the way to deciding you want the real numbers. */}
      <div className="cmpbar">
        <button className="cmpparse" onClick={onCompareParses}
                title="Damage, healing and deaths raider by raider, fight by fight">
          Compare parses
        </button>
        <span className="muted">{runs.length} raids</span>
      </div>
      <div className="tablewrap">
        <table className="data cmptable" style={{ minWidth }}>
          <colgroup>
            <col className="cmplabelcol" />
            {runs.map((r) => <col key={r.id} />)}
          </colgroup>
          <thead>
            <tr>
              <th className="l" />
              {runs.map((r) => (
                <th key={r.id}>
                  <button className="cmphead" onClick={() => onRemove(r.id)}
                          title={`${r.zone || 'Unknown zone'} — remove from comparison`}>
                    {r.zone || 'Unknown zone'}
                  </button>
                  <div className="cls muted">{fmt.date(r.started_ts)}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => {
              const best = bestIdx(m)
              const lead = best >= 0 ? m.vals[best] : null
              return (
                <tr key={m.label}>
                  <td className="l lbl">{m.label}</td>
                  {m.vals.map((v, i) => {
                    const has = v != null
                    const gap = has && lead && i !== best
                      ? Math.round((100 * (v - lead)) / Math.abs(lead)) : null
                    return (
                      <td key={runs[i].id} className={i === best ? 'best' : ''}>
                        <span className="cmpval">
                          <span className="v">{has ? m.fmt(v) : '—'}</span>
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
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
