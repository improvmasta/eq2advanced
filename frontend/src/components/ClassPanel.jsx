import { useMemo } from 'react'
import { ClassChip } from './Identity.jsx'
import { fmt } from '../lib/api.js'
import { CLASS_COLOR, ROLES, ROLE_LABEL, classLabel } from '../lib/classes.js'

/* The Class tab: the stats only one class can answer.

   Every other tab compares the whole raid down one column. This one cannot —
   "was Jester's Cap up all fight" is a question about a troubador and nobody
   else, and a column that is blank for twenty-five classes is not a column.
   So the tab is a rail of the classes actually in this raid, and each one owns
   its panel.

   The renderer is deliberately generic: the backend registry
   (`pipeline/classstats.py`) sends columns with a unit and rows keyed by
   column key, and everything here formats by unit. Adding a class stat is
   then a Python function — no component, no route, no release note. A class
   with nothing written yet still gets its section, because the honest state of
   this tab is "we know who was here, we have not written their stats". */

const cell = (value, unit) => {
  if (value == null || value === '') return '—'
  switch (unit) {
    case 'num': return fmt.num(value)
    case 'pct': return `${Math.round(value)}%`
    case 'secs': return fmt.dur(Math.round(value))
    case 'clock': {
      const s = Math.max(0, Math.round(value))
      return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
    }
    case 'rate': return Number(value).toFixed(1)
    default: return String(value)
  }
}

const NUMERIC = new Set(['num', 'pct', 'secs', 'clock', 'rate'])

function Metric({ metric }) {
  return (
    <div className="card classmetric">
      <h3>{metric.label}</h3>
      {/* One line, and it is the stat's LIMIT rather than a description of it —
          these numbers have edges and the edge belongs beside the number. Keep
          it short: this is a stats panel, not a manual. */}
      <p className="note">{metric.blurb}</p>
      {metric.status !== 'ok' ? (
        <p className={metric.status === 'error' ? 'err' : 'muted'}>{metric.note}</p>
      ) : !metric.rows.length ? (
        <p className="muted">Nothing in these fights.</p>
      ) : (
        <>
          <div className="tablewrap">
            <table className="data">
              <thead>
                <tr>
                  {metric.columns.map((c) => (
                    <th key={c.key} className={NUMERIC.has(c.unit) ? '' : 'l'}
                        title={c.title || undefined}>{c.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {metric.rows.map((row, i) => (
                  <tr key={i}>
                    {metric.columns.map((c) => (
                      <td key={c.key} className={NUMERIC.has(c.unit) ? '' : 'name l'}>
                        {cell(row[c.key], c.unit)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {metric.note && <p className="note">{metric.note}</p>}
        </>
      )}
    </div>
  )
}

export default function ClassPanel({ data, err, cls, onPick }) {
  const sections = data?.classes || []

  /* Default to the first class that actually has something to read, so the
     tab opens on content rather than on whichever class sorts first. */
  const active = useMemo(() => {
    const hit = sections.find((s) => s.class === cls)
    if (hit) return hit
    return sections.find((s) => s.metrics.length) || sections[0] || null
  }, [sections, cls])

  if (err) return <p className="err">{err}</p>
  if (!data) return <p className="muted">Loading…</p>
  if (data.pruned) {
    return <p className="muted">Events pruned — no class stats for this parse.</p>
  }
  if (!sections.length) {
    return (
      <p className="muted">
        No classes resolved{data.unclassified?.length ? ` (${data.unclassified.length} unmatched)` : ''}.
      </p>
    )
  }

  const byRole = ROLES.map((role) => ({
    role, classes: sections.filter((s) => s.archetype === role),
  })).filter((g) => g.classes.length)

  return (
    <div className="classtab">
      <div className="classrail">
        {byRole.map((g) => (
          <div className="classgroup" key={g.role}>
            <div className="classrole">{ROLE_LABEL[g.role]}</div>
            {g.classes.map((s) => (
              <button
                key={s.class}
                className={`chip classpick ${active?.class === s.class ? 'on' : ''}`}
                onClick={() => onPick(s.class)}
              >
                <i style={{ background: CLASS_COLOR[s.class] }} />
                <span className="n">{classLabel(s.class)}</span>
                <span className="ct">{s.actors.length}</span>
              </button>
            ))}
          </div>
        ))}
      </div>

      {active && (
        <div className="classbody">
          <div className="card classhead">
            <h2>{classLabel(active.class)}</h2>
            <div className="classroster">
              {active.actors.map((a) => (
                <span className="rosterchip" key={a.key || a.name}>
                  <span className="n">{a.name}</span>
                  <ClassChip actor={a} />
                </span>
              ))}
            </div>
          </div>

          {active.metrics.length
            ? active.metrics.map((m) => <Metric key={m.key} metric={m} />)
            : (
              <div className="card classempty">
                <p>Coming soon.</p>
              </div>
            )}
        </div>
      )}

      {!!data.unclassified?.length && (
        <p className="note classunmatched">
          Class unmatched: {data.unclassified.join(', ')}
        </p>
      )}
    </div>
  )
}
