import { useEffect, useMemo, useRef, useState } from 'react'
import { fmt } from '../lib/api.js'
import { CHART_COLORS, CHART_DASH } from '../lib/classes.js'
import { ClassChip } from './Identity.jsx'

/* Damage/healing over the fight, one line per checked raider.

   The x axis is the CONCATENATED combat clock the rest of the page uses (the
   backend removes the gaps between fights), so a multi-fight selection reads
   continuously and the totals still agree with the tables. Values are plotted
   as a rate — bucket amount / bucket seconds — so changing the bucket size
   rescales the noise, not the height of the line. */

const PAD = { t: 12, r: 14, b: 22, l: 52 }
const H = 250

const clock = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`

function useWidth(ref) {
  const [w, setW] = useState(760)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref])
  return w
}

export default function TimelineChart({ data, keys, actorsByKey, metric, onMetric }) {
  const wrapRef = useRef(null)
  const width = useWidth(wrapRef)
  const [hover, setHover] = useState(null)   // bucket index
  const [showTable, setShowTable] = useState(false)
  /* Healing on its own is a shape with no cause. The raid's incoming damage
     behind it is the cause: a spike on the tank and the heals that answered it
     (or didn't) line up in the same second, on their own scale so a 400k hit
     doesn't flatten the healers into the axis. */
  const [overlay, setOverlay] = useState(false)

  const series = useMemo(() => {
    const byKey = new Map((data?.series || []).map((s) => [s.key, s]))
    return keys.map((k, i) => {
      const s = byKey.get(k)
      if (!s) return null
      return { ...s, color: CHART_COLORS[i % CHART_COLORS.length], dash: CHART_DASH[i % CHART_DASH.length] }
    }).filter(Boolean)
  }, [data, keys])

  const bucketS = data?.bucket_s || 1
  const n = data?.bucket_count || 0
  const vals = (s) => s[metric] || []
  const peak = useMemo(() => Math.max(1, ...series.flatMap((s) => vals(s))) / bucketS, [series, metric, bucketS])

  // raid-wide incoming damage per bucket, for the overlay
  const taken = useMemo(() => {
    if (!data?.bucket_count) return null
    const out = new Array(data.bucket_count).fill(0)
    for (const s of data.series || []) {
      const t = s.taken || []
      for (let i = 0; i < out.length; i += 1) out[i] += t[i] || 0
    }
    return out
  }, [data])
  const takenPeak = useMemo(
    () => Math.max(1, ...(taken || [0])) / bucketS, [taken, bucketS])
  const showOverlay = overlay && metric !== 'taken' && !!taken?.some((v) => v > 0)

  if (!data || !n) return <p className="muted">No timeline for this selection.</p>
  if (!series.length) {
    return <p className="muted">Check raiders in the table to plot them here (up to {CHART_COLORS.length}).</p>
  }

  const iw = Math.max(80, width - PAD.l - PAD.r)
  const ih = H - PAD.t - PAD.b
  const x = (i) => PAD.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw)
  const y = (v) => PAD.t + ih - (v / peak) * ih
  const path = (s) => {
    const pts = vals(s)
    // a one-bucket selection has no segment to draw — give it a short stub so
    // the series is visible instead of an invisible lone moveto
    if (pts.length === 1) {
      const py = y(pts[0] / bucketS)
      return `M${(x(0) - 8).toFixed(1)},${py.toFixed(1)}L${(x(0) + 8).toFixed(1)},${py.toFixed(1)}`
    }
    return pts.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v / bucketS).toFixed(1)}`).join('')
  }

  const yTaken = (v) => PAD.t + ih - (v / takenPeak) * ih
  const takenArea = () => {
    const pts = taken.map((v, i) => `${x(i).toFixed(1)},${yTaken(v / bucketS).toFixed(1)}`)
    return `M${x(0).toFixed(1)},${(PAD.t + ih).toFixed(1)}L${pts.join('L')}`
      + `L${x(n - 1).toFixed(1)},${(PAD.t + ih).toFixed(1)}Z`
  }

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => peak * f)
  const bucketAt = (px) => {
    const i = Math.round(((px - PAD.l) / iw) * (n - 1))
    return Math.max(0, Math.min(n - 1, i))
  }
  const move = (e) => {
    const r = e.currentTarget.getBoundingClientRect()
    setHover(bucketAt(e.clientX - r.left))
  }

  const hoverRows = hover == null ? [] : series
    .map((s) => ({ s, v: (vals(s)[hover] || 0) / bucketS }))
    .sort((a, b) => b.v - a.v)
  const metricLabel = { damage: 'DPS', heals: 'HPS', taken: 'Damage taken/s' }[metric]

  return (
    <div ref={wrapRef} className="chartwrap">
      <div className="chartbar">
        <div className="chips">
          {[['damage', 'Damage'], ['heals', 'Healing'], ['taken', 'Damage taken']].map(([k, l]) => (
            <button key={k} className={`chip ${metric === k ? 'on' : ''}`} onClick={() => onMetric(k)}>{l}</button>
          ))}
        </div>
        {metric !== 'taken' && (
          <label className="chip toggle" title="Raid-wide incoming damage behind the lines, on its own scale">
            <input type="checkbox" checked={overlay} onChange={(e) => setOverlay(e.target.checked)} />
            {' '}+ damage taken
          </label>
        )}
        <span className="muted">{bucketS}s buckets</span>
        <button className="chip" style={{ marginLeft: 'auto' }} onClick={() => setShowTable((v) => !v)}>
          {showTable ? 'Hide table' : 'Table view'}
        </button>
      </div>

      <div className="chartlegend">
        {showOverlay && (
          <span className="lg">
            <svg width="18" height="8" aria-hidden="true">
              <rect x="0" y="1" width="18" height="6" className="takenswatch" />
            </svg>
            Raid damage taken
            <span className="muted"> peak {fmt.num(takenPeak)}/s</span>
          </span>
        )}
        {series.map((s) => (
          <span key={s.key} className="lg">
            <svg width="18" height="8" aria-hidden="true">
              <line x1="0" y1="4" x2="18" y2="4" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash || undefined} />
            </svg>
            {s.name}
            <ClassChip actor={actorsByKey?.[s.key]} />
          </span>
        ))}
      </div>

      <svg
        width={width} height={H} role="img"
        aria-label={`${metricLabel} over time for ${series.map((s) => s.name).join(', ')}`}
        onMouseMove={move} onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={width - PAD.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={PAD.l - 6} y={y(t) + 3} className="axis" textAnchor="end">{fmt.num(t)}</text>
          </g>
        ))}

        {/* behind the grid lines and every series: context, not a reading */}
        {showOverlay && n > 1 && <path d={takenArea()} className="takenarea" />}

        {(data.segments || []).slice(1).map((seg) => (
          <line key={seg.encounter_id} className="segline"
                x1={x(seg.start_bucket)} x2={x(seg.start_bucket)} y1={PAD.t} y2={PAD.t + ih} />
        ))}
        {(data.segments || []).filter((s) => s.is_named).map((seg) => (
          <text key={`l${seg.encounter_id}`} className="seglabel" x={x(seg.start_bucket) + 3} y={PAD.t + 8}>
            {seg.name}
          </text>
        ))}

        {series.map((s) => (
          <path key={s.key} d={path(s)} fill="none" stroke={s.color} strokeWidth="2"
                strokeDasharray={s.dash || undefined} strokeLinejoin="round" />
        ))}

        {(data.markers || []).filter((m) => series.some((s) => s.key === m.key)).map((m, i) => {
          const s = series.find((x2) => x2.key === m.key)
          const cy = y((vals(s)[m.bucket] || 0) / bucketS)
          return (
            <g key={i}>
              <circle cx={x(m.bucket)} cy={cy} r="5" className="markerring" />
              <circle cx={x(m.bucket)} cy={cy} r="3.5" fill={s.color} />
              <title>{m.name} died at {clock(m.bucket * bucketS)}</title>
            </g>
          )
        })}

        {hover != null && (
          <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={PAD.t + ih} />
        )}

        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const i = Math.round(f * (n - 1))
          return (
            <text key={f} className="axis" x={x(i)} y={H - 6}
                  textAnchor={f === 0 ? 'start' : f === 1 ? 'end' : 'middle'}>
              {clock(i * bucketS)}
            </text>
          )
        })}
      </svg>

      {hover != null && (hoverRows.some((r) => r.v > 0)
        || (showOverlay && taken[hover] > 0)) && (
        <div className="charttip" style={{ left: Math.min(Math.max(x(hover), 60), width - 150) }}>
          <div className="tt">{clock(hover * bucketS)}</div>
          {hoverRows.filter((r) => r.v > 0).map(({ s, v }) => (
            <div key={s.key} className="tr">
              <i style={{ background: s.color }} />{s.name}<b>{fmt.num(v)}</b>
            </div>
          ))}
          {showOverlay && (
            <div className="tr">
              <i className="takenswatch" />Raid taken
              <b>{fmt.num((taken[hover] || 0) / bucketS)}</b>
            </div>
          )}
        </div>
      )}

      {showTable && (
        <div className="tablewrap">
          <table className="data">
            <thead>
              <tr><th className="l">Raider</th><th>Total</th><th>Peak {metricLabel}</th><th>Average</th></tr>
            </thead>
            <tbody>
              {series.map((s) => {
                const v = vals(s)
                const total = v.reduce((a, b) => a + b, 0)
                return (
                  <tr key={s.key}>
                    <td className="name l">{s.name}</td>
                    <td>{fmt.num(total)}</td>
                    <td>{fmt.num(Math.max(0, ...v) / bucketS)}</td>
                    <td>{fmt.num(total / (n * bucketS))}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
