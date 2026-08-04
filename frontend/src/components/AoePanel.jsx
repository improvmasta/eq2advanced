import { useState } from 'react'
import SortableTable from './SortableTable.jsx'
import { fmt } from '../lib/api.js'

/* Incoming raid AoEs: what the enemy pulled, how often it really landed, and
   how much of the raid was covered when it did.

   Two timers sit side by side and the point is the distance between them.
   "Reported" is ACT's spell-timer list — what the raid was told to expect.
   "Observed" is the shortest interval between two casts that repeats, taken
   from this log. They disagree for real reasons: a timer that was never right
   for this expansion, a mob that got stunned, or several trash mobs sharing
   one name.

   What is NOT claimed: that every cast was seen. An AoE that never reached
   five people leaves no trace wide enough to detect, so the cast count is a
   floor. A missed cast can only make a gap longer, never shorter, which is
   why the observed timer is the shortest repeating gap rather than the mean. */

const clock = (ts, base) => {
  const s = Math.max(0, ts - base)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function Casts({ row, base }) {
  return (
    <div className="tablewrap">
      <table className="data">
        <thead>
          <tr>
            <th className="l">Cast</th><th>Targets</th><th>Hit</th>
            <th>Avoided</th><th>Absorbed</th><th>Damage</th><th className="l">Covered</th>
          </tr>
        </thead>
        <tbody>
          {row.cast_list.map((c, i) => (
            <tr key={i}>
              <td className="name l">{clock(c.ts, base)}</td>
              <td>{c.targets}</td>
              <td>{c.hit}</td>
              <td>{c.avoided || ''}</td>
              <td>{c.absorbed || ''}</td>
              <td>{fmt.num(c.damage)}</td>
              <td className="l muted">
                {c.blocked_by.map((k) => k.split('|')[0]).join(', ')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function AoePanel({ data, err, base }) {
  const [open, setOpen] = useState(null)

  if (err) return <p className="err">{err}</p>
  if (!data) return <p className="muted">Loading…</p>
  if (data.pruned) {
    return (
      <p className="muted">
        No AoEs — this run&apos;s raw events were pruned. The tables read from
        frozen rollups and are unaffected.
      </p>
    )
  }
  if (!data.aoes?.length) {
    return (
      <p className="muted">
        Nothing in this selection hit {data.min_targets} raiders at once more
        than once.
      </p>
    )
  }

  const key = (r) => `${r.source}|${r.ability}`
  const delta = (r) => (r.reported_s && r.observed_s
    ? r.observed_s - r.reported_s : null)

  const columns = [
    {
      key: 'ability', label: 'AoE', align: 'l', fixed: true,
      render: (r) => (
        <span className="name">
          {r.ability}
          <button
            className="expandcol"
            onClick={(e) => { e.stopPropagation(); setOpen(open === key(r) ? null : key(r)) }}
            title={open === key(r) ? 'Hide casts' : 'Show every cast'}
          >{open === key(r) ? '▾' : '…'}</button>
        </span>
      ),
      sortValue: (r) => r.ability,
    },
    { key: 'source', label: 'From', align: 'l', render: (r) => r.source },
    { key: 'casts', label: 'Casts' },
    {
      key: 'reported_s', label: 'ACT timer',
      render: (r) => (r.reported_s != null
        ? `${r.reported_s}s`
        : <span className="muted" title="Not in the ACT spell-timer list">—</span>),
      sortValue: (r) => r.reported_s ?? null,
    },
    {
      key: 'observed_s', label: 'Observed',
      /* Confidence is the number of intervals that agreed, and it is printed
         rather than folded into a badge: two agreeing gaps is a guess, twenty
         is a measurement. */
      render: (r) => (r.observed_s != null
        ? <span title={`${r.observed_agree} intervals agreed`
            + (r.missed_hint ? ` · ${r.missed_hint} cast(s) look missed` : '')}>
            {r.observed_s}s
            {r.observed_agree < 3 && <span className="selfmark">?</span>}
          </span>
        : <span className="muted" title="No interval repeated — too few casts">—</span>),
      sortValue: (r) => r.observed_s ?? null,
    },
    {
      key: 'delta', label: 'Δ',
      render: (r) => {
        const d = delta(r)
        if (d == null) return ''
        const off = Math.abs(d) / r.reported_s
        return (
          <span
            style={off > 0.15 ? { color: 'var(--warning)' } : undefined}
            title={r.instances_hint
              ? `Or ${r.instances_hint} mobs of this name casting on their own timers`
              : undefined}
          >
            {d > 0 ? '+' : ''}{d.toFixed(1)}s
            {r.instances_hint ? <span className="selfmark">*</span> : null}
          </span>
        )
      },
      sortValue: (r) => { const d = delta(r); return d == null ? null : Math.abs(d) },
    },
    { key: 'median_targets', label: 'Targets', render: (r) => r.median_targets },
    {
      key: 'blocked_pct', label: 'Covered',
      render: (r) => (r.blocked
        ? <span title={`${r.blocked} of ${r.casts} casts' targets avoided or absorbed it`}>
            {r.blocked_pct}%
          </span>
        : ''),
      sortValue: (r) => r.blocked_pct,
    },
    { key: 'damage', label: 'Damage', render: (r) => fmt.num(r.damage) },
    {
      key: 'fights', label: 'Fights', render: (r) => (r.fights > 1 ? r.fights : ''),
      sortValue: (r) => r.fights,
    },
  ]

  return (
    <div className="card">
      <div className="drillhead">
        <h2>Incoming AoEs</h2>
        <span className="muted">
          hit {data.min_targets}+ raiders at once · ACT timer vs what the log shows
        </span>
      </div>
      <SortableTable
        columns={columns}
        rows={data.aoes}
        prefsKey="zonerun:aoes"
        defaultSort={{ key: 'damage', dir: 'desc' }}
        rowKey={key}
        selectedKey={open}
        onRowClick={(r) => setOpen(open === key(r) ? null : key(r))}
      />
      {open && data.aoes.some((r) => key(r) === open) && (
        <>
          <h3 style={{ marginTop: 12 }}>
            Every cast — {data.aoes.find((r) => key(r) === open).ability}
          </h3>
          <Casts row={data.aoes.find((r) => key(r) === open)} base={base} />
        </>
      )}
      {data.pruned_encounters > 0 && (
        <p className="note">
          {data.pruned_encounters} of the selected fights had their events pruned
          and contribute nothing here.
        </p>
      )}
      <p className="note">
        A cast that never reached {data.min_targets} raiders leaves no trace wide
        enough to see, so the cast count is a floor. Observed is the shortest
        interval that repeats — a cast we missed can only make a gap longer.
      </p>
    </div>
  )
}
