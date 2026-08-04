import { useEffect, useMemo, useState } from 'react'
import { api, fmt } from '../lib/api.js'

/* Two or more raids, actor for actor. The head-to-head card answers "which
   night was bigger"; this answers "who did what differently", which needs the
   real parse on both sides rather than the list row.

   Each raid picks its own fight, so the useful comparison — the same named
   boss on two different nights — is a dropdown on each column rather than a
   guess about which fights correspond. "Whole raid" aggregates every fight in
   the run, exactly as the run page's totals do (`/encounters/agg`).

   Rows are raiders, matched BY NAME across raids: entity ids are session
   scoped and two nights are always different sessions. Somebody who only shows
   up on one night gets a row with one number in it, which is the answer to
   "who was missing". */

const healedOf = (a) => (a.heals || 0) + (a.wards_absorbed || 0)

const METRICS = [
  { key: 'dps', label: 'DPS', get: (a, d) => (a.damage || 0) / d, fmt: fmt.num },
  { key: 'damage', label: 'Damage', get: (a) => a.damage || 0, fmt: fmt.num },
  { key: 'hps', label: 'HPS', get: (a, d) => healedOf(a) / d, fmt: fmt.num },
  { key: 'healed', label: 'Healed', get: (a) => healedOf(a), fmt: fmt.num },
  { key: 'cures', label: 'Cures', get: (a) => a.cure_count || 0, fmt: (v) => v },
  { key: 'taken', label: 'Dmg taken', get: (a) => a.damage_taken || 0, fmt: fmt.num, worse: true },
  { key: 'deaths', label: 'Deaths', get: (a) => a.deaths || 0, fmt: (v) => v, worse: true },
]

export default function RaidParseCompare({ runs, onClose }) {
  const [metric, setMetric] = useState('dps')
  const [encs, setEncs] = useState({})    // run id -> encounters
  const [pick, setPick] = useState({})    // run id -> 'all' | encounter id
  const [aggs, setAggs] = useState({})    // `${run id}:${pick}` -> agg payload
  const [error, setError] = useState(null)

  useEffect(() => {
    const esc = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])

  // the run's fight list, for the dropdown
  useEffect(() => {
    for (const r of runs) {
      if (encs[r.id]) continue
      api.zoneRun(r.id)
        .then((d) => setEncs((s) => ({ ...s, [r.id]: d.encounters })))
        .catch((e) => setError(e.message))
    }
  }, [runs, encs])

  // the parse itself, per (run, chosen fight) — cached by key, so flipping the
  // dropdown back to a fight you already looked at repaints without a request
  useEffect(() => {
    for (const r of runs) {
      const list = encs[r.id]
      if (!list?.length) continue
      const sel = pick[r.id] || 'all'
      const key = `${r.id}:${sel}`
      if (aggs[key]) continue
      const ids = sel === 'all' ? list.map((e) => e.id) : [Number(sel)]
      api.encountersAgg(ids)
        .then((d) => setAggs((s) => ({ ...s, [key]: d })))
        .catch((e) => setError(e.message))
    }
  }, [runs, encs, pick, aggs])

  const m = METRICS.find((x) => x.key === metric)

  const cols = useMemo(() => runs.map((r) => {
    const sel = pick[r.id] || 'all'
    return { run: r, sel, agg: aggs[`${r.id}:${sel}`], fights: encs[r.id] }
  }), [runs, pick, aggs, encs])

  const rows = useMemo(() => {
    const byName = new Map()
    cols.forEach((c, i) => {
      const dur = Math.max(c.agg?.encounter?.duration_s || 0, 1)
      for (const a of c.agg?.actors || []) {
        if (a.kind !== 'player') continue
        const row = byName.get(a.name) || { name: a.name, vals: cols.map(() => null) }
        row.vals[i] = m.get(a, dur)
        row.cls = row.cls || a.class
        byName.set(a.name, row)
      }
    })
    return [...byName.values()]
      .filter((r) => r.vals.some((v) => v))
      .sort((a, b) => Math.max(...b.vals.map((v) => v || 0))
        - Math.max(...a.vals.map((v) => v || 0)))
  }, [cols, m])

  /* The raid's own number: every raider's value added up. That is a sum for
     the totals AND for the rates — every raider's DPS is measured over the
     same fight clock, so they add to the raid's DPS. Averaging them would
     answer a different question. */
  const totals = cols.map((c) => {
    if (!c.agg) return null
    const dur = Math.max(c.agg.encounter?.duration_s || 0, 1)
    const players = c.agg.actors.filter((a) => a.kind === 'player')
    return players.length ? players.reduce((s, a) => s + m.get(a, dur), 0) : null
  })

  const best = (vals) => {
    const present = vals.filter((v) => v != null && v !== 0)
    if (present.length < 2) return -1
    const target = m.worse ? Math.min(...present) : Math.max(...present)
    if (present.every((v) => v === target)) return -1
    return vals.indexOf(target)
  }

  const cell = (v, i, vals) => {
    const b = best(vals)
    const lead = b >= 0 ? vals[b] : null
    const gap = v != null && lead && i !== b
      ? Math.round((100 * (v - lead)) / Math.abs(lead)) : null
    return (
      <td key={i} className={i === b ? 'best' : ''}>
        <span className="cmpval">
          <span className="v">{v == null ? '—' : m.fmt(v)}</span>
          {i === b ? <span className="d lead" title="Best here">▲</span>
            : gap ? <span className="d">{gap > 0 ? '+' : ''}{gap}%</span> : null}
        </span>
      </td>
    )
  }

  // success is 0/1/NULL — only a recorded 0 is a wipe; NULL is "never decided"
  const label = (e) =>
    `${fmt.time(e.started_ts)}  ${e.name || 'Unknown'}${e.success === 0 ? ' (wipe)' : ''}`

  return (
    <div className="modalwrap" onClick={(ev) => { if (ev.target === ev.currentTarget) onClose() }}>
      <div className="card modalcard pccard" role="dialog" aria-label="Compare parses"
           style={{ '--pccols': cols.length }}>
        <div className="selhead-top">
          <h2>Compare parses</h2>
          <div className="sa">
            <button className="chip" onClick={onClose}>Close</button>
          </div>
        </div>

        {error && <p className="err">{error}</p>}

        <div className="tabs">
          {METRICS.map((x) => (
            <button key={x.key} className={`tab ${metric === x.key ? 'on' : ''}`}
                    onClick={() => setMetric(x.key)}>
              {x.label}
            </button>
          ))}
        </div>

        <div className="tablewrap">
          <table className="data cmptable parsecmp">
            <colgroup>
              <col className="cmplabelcol" />
              {cols.map((c) => <col key={c.run.id} />)}
            </colgroup>
            <thead>
              <tr>
                <th className="l">Raider</th>
                {cols.map((c) => (
                  <th key={c.run.id}>
                    <div className="pchead">
                      <span className="z">{c.run.zone || 'Unknown zone'}</span>
                      <span className="muted">{fmt.date(c.run.started_ts)}</span>
                      <select
                        value={c.sel}
                        disabled={!c.fights}
                        onChange={(ev) => setPick((s) => ({ ...s, [c.run.id]: ev.target.value }))}
                      >
                        <option value="all">
                          Whole raid{c.fights ? ` — ${c.fights.length} fights` : ''}
                        </option>
                        {c.fights?.some((e) => e.is_named) && (
                          <optgroup label="Named">
                            {c.fights.filter((e) => e.is_named).map((e) => (
                              <option key={e.id} value={e.id}>{label(e)}</option>
                            ))}
                          </optgroup>
                        )}
                        <optgroup label="Every fight">
                          {c.fights?.map((e) => (
                            <option key={e.id} value={e.id}>{label(e)}</option>
                          ))}
                        </optgroup>
                      </select>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr className="parsetotal">
                <td className="l lbl">Raid</td>
                {totals.map((v, i) => cell(v, i, totals))}
              </tr>
              {rows.map((r) => (
                <tr key={r.name}>
                  <td className="l lbl">{r.name}</td>
                  {r.vals.map((v, i) => cell(v, i, r.vals))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {cols.some((c) => !c.agg) && <p className="muted">Loading parses…</p>}
        {cols.every((c) => c.agg) && rows.length === 0 && (
          <p className="muted">Nothing to compare for this metric.</p>
        )}
      </div>
    </div>
  )
}
