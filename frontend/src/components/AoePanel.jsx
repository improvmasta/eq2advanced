import { useState } from 'react'
import SortableTable from './SortableTable.jsx'
import { DtypePill } from './AoeTimers.jsx'
import { fmt } from '../lib/api.js'
import { toggleJoust, useJoust } from '../lib/joust.js'

/* Incoming raid AoEs: what the enemy pulled, how often it really landed, and
   how much of the raid was covered when it did.

   Two timers sit side by side and the point is the distance between them.
   "Reported" is ACT's spell-timer list — what the raid was told to expect.
   "Observed" is the shortest interval between two casts that repeats, taken
   from this log. They disagree for real reasons: a timer that was never right
   for this expansion, a mob that got stunned, or several trash mobs sharing
   one name.

   What is NOT claimed: that every cast was seen. An AoE the timer list has
   never heard of has to reach five people to leave a trace wide enough to
   detect, so its cast count is a floor. A missed cast can only make a gap
   longer, never shorter, which is why the observed timer is the shortest
   repeating gap rather than the mean.

   Two things here are not readings. The JOUST tick is the one fact a log
   cannot supply — whether the raid leaves for this one — and it is marked
   here because this is the tab you have open when you are working out what
   went wrong; the dashboard turns it into a burn-window countdown
   (`lib/joust.js`). And a SUGGESTED timer is what to go and type into ACT: it
   appears only when this log measured a period that disagrees with the
   configured one by more than the noise, over enough agreeing intervals to
   mean it (`aoes.suggest_period`). */

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
  const jousted = useJoust()

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
        Nothing in this selection reached {data.min_targets} raiders at once
        more than once, and nothing in it is on the ACT spell-timer list.
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
          {/* The tick, ahead of the name: it is an input, and an input that
              sits after the thing it acts on reads as a result of it. */}
          <label className="joustbox"
                 title={jousted.has(r.ability)
                   ? `${r.ability} is jousted — it drives the burn window on the`
                     + ' raid dashboard'
                   : `Mark ${r.ability} as one you joust`}
                 onClick={(e) => e.stopPropagation()}>
            <input type="checkbox" checked={jousted.has(r.ability)}
                   onChange={() => toggleJoust(r.ability)} />
          </label>
          {r.ability}
          <DtypePill row={r} />
          {/* A CONDITION, not a cast: it kept meeting the raid-wide anchor
              second after second, which is a damage shield or an aura rather
              than something the mob cast at the raid (aoes.SUSTAINED_RUN). It
              stays listed — it did reach the raid and that is what this tab
              records — but it is marked, and it never gets a countdown. */}
          {r.sustained && (
            <span className="selfmark"
                  title={`Sustained: ${r.run_s}s of raid-wide hits in a row per burst.`
                    + ' A damage shield or aura, not a timed cast — no countdown.'}>
              shield
            </span>
          )}
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
        ? (
          <span>
            {r.reported_s}s
            {/* What to type into ACT instead. Only ever shown next to the
                number it disagrees with, because that is the edit. */}
            {r.suggested_s && (
              <span className="suggest"
                    title={`${r.observed_agree} intervals in this log agree on`
                      + ` ${r.suggested_s}s, not ${r.reported_s}s`}>
                ⇢{r.suggested_s}s
              </span>
            )}
          </span>
        )
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
          on the ACT timer list, or {data.min_targets}+ raiders at once ·
          ACT timer vs what the log shows
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
        An ability ACT&apos;s list knows counts every landing, however few it
        found. One it does not has to reach {data.min_targets} raiders to leave
        a trace wide enough to see, so its cast count is a floor. Observed is
        the shortest interval that repeats — a cast we missed can only make a
        gap longer.
      </p>
    </div>
  )
}
