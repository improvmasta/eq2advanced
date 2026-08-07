import { useEffect, useMemo, useRef, useState } from 'react'
import { fmt } from '../lib/api.js'

/* The pull's shape, drawn behind the headline numbers.

   This is not the Timeline tab. That one is a reading — axes, ticks, a series
   per raider, a tooltip. This is the wallpaper the raid DPS number sits on: no
   axes, no legend, one filled area, and it scrolls as the fight runs. It says
   "we are ramping / we just lost the burst / the adds landed" at a glance from
   across a desk, which is the only question a second monitor can answer.

   Per-second raid damage is spiky enough to read as noise, so the line is a
   short rolling mean — the shape is the point, and the exact height of one
   second is what the tables are for. */

const H = 92
const SMOOTH_S = 5

function useWidth(ref) {
  const [w, setW] = useState(760)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref])
  return w
}

function smooth(values, window) {
  if (values.length <= window) return values
  const out = new Array(values.length)
  let sum = 0
  for (let i = 0; i < values.length; i += 1) {
    sum += values[i]
    if (i >= window) sum -= values[i - window]
    out[i] = sum / Math.min(i + 1, window)
  }
  return out
}

export default function LiveTimeline({ dmg = [], heal = [], metric = 'damage' }) {
  const ref = useRef(null)
  const width = useWidth(ref)

  const { area, line, peak } = useMemo(() => {
    const raw = metric === 'heal' ? heal : dmg
    const vals = smooth(raw, SMOOTH_S)
    const n = vals.length
    const top = Math.max(1, ...vals)
    if (n < 2) return { area: null, line: null, peak: top }
    const x = (i) => (i / (n - 1)) * width
    const y = (v) => H - (v / top) * (H - 8) - 2
    const pts = vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('L')
    return {
      line: `M${pts}`,
      area: `M0,${H}L${pts}L${width.toFixed(1)},${H}Z`,
      peak: top,
    }
  }, [dmg, heal, metric, width])

  return (
    <div ref={ref} className={`livechart ${metric}`} aria-hidden="true">
      {area && (
        <svg width={width} height={H} preserveAspectRatio="none">
          <path d={area} className="livearea" />
          <path d={line} className="liveline" fill="none" />
        </svg>
      )}
      {area && <span className="peak">peak {fmt.num(peak)}/s</span>}
    </div>
  )
}
