import { fmt } from '../lib/api.js'

/* Where a parse actually comes from. One stacked bar, 2px surface gaps between
   segments, and a written legend under it — the answer to "is this player's
   number their rotation or their gear procs?" */
export default function CompositionBar({ parts, total, note }) {
  const shown = parts.filter((p) => p.value > 0)
  if (!total || !shown.length) return null
  return (
    <div className="compbar">
      <div className="track">
        {shown.map((p) => (
          <i key={p.key} style={{ width: `${(p.value / total) * 100}%`, background: p.color }}
             title={`${p.label} — ${fmt.num(p.value)} (${Math.round((p.value / total) * 100)}%)`} />
        ))}
      </div>
      <div className="complegend">
        {shown.map((p) => (
          <span key={p.key} className="lg">
            <i style={{ background: p.color }} />
            {p.label}
            <b>{Math.round((p.value / total) * 100)}%</b>
          </span>
        ))}
        {note && <span className="muted">{note}</span>}
      </div>
    </div>
  )
}
