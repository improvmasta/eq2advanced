export default function ShareBar({ value, max, kind = 'dps' }) {
  const w = max > 0 ? Math.max((value / max) * 100, 0.5) : 0
  return (
    <div className={`sharebar ${kind}`} role="img" aria-label={`${Math.round((value / (max || 1)) * 100)}% of top`}>
      <i style={{ width: `${w}%` }} />
    </div>
  )
}
