/* Shape of a night at a glance — one value per fight, no axes. Decorative
   context next to numbers that are already written out, so it carries no
   information on its own. */
export default function Sparkline({ values, width = 96, height = 20, title }) {
  if (!values || values.length < 2) return null
  const max = Math.max(...values, 1)
  const step = width / (values.length - 1)
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(1)},${(height - (v / max) * (height - 2) - 1).toFixed(1)}`).join('')
  return (
    <svg className="spark" width={width} height={height} aria-hidden="true" focusable="false">
      {title && <title>{title}</title>}
      <path d={d} fill="none" strokeWidth="1.5" />
    </svg>
  )
}
