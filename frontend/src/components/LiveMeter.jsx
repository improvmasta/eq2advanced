import { useMemo } from 'react'
import { fmt } from '../lib/api.js'
import { classLabel, familyColor } from '../lib/classes.js'
import AoeTimers from './AoeTimers.jsx'
import LiveTimeline from './LiveTimeline.jsx'

/* The fight in progress: ACT's meter, sized for a second monitor.

   Everything here comes from one `partial` payload
   (backend/pipeline/livemeter.py) and none of it is the record — the same
   fight arrives again as an `encounter` card once it closes, and THAT is what
   the parse pages report. The numbers move while the pull runs, which is the
   point and also the caveat: nothing on this screen is a final DPS.

   The bar behind each row is the reading. A number you have to compare against
   twenty-three other numbers is a table; a bar you can see from three feet
   away is a meter, and this is meant to be read while you are playing. The
   colour is the raider's EQ2 archetype, the one they already know from the
   game (lib/classes.js). */

const METRICS = {
  damage: { key: 'damage', rate: 'dps', label: 'Damage', rateLabel: 'DPS' },
  heal: { key: 'heals', rate: 'hps', label: 'Healing', rateLabel: 'HPS' },
}

function MeterRow({ row, metric, max, rank }) {
  const m = METRICS[metric]
  const value = row[m.key] || 0
  const width = max > 0 ? Math.max(1.5, (value / max) * 100) : 0
  const tint = familyColor(row.class)
  return (
    <div className={`meterrow ${row.kind === 'mob' ? 'mob' : ''}`}>
      <i
        className="fill"
        style={{
          width: `${width}%`,
          background: tint
            ? `color-mix(in oklab, ${tint} 42%, transparent)`
            : 'var(--bar-track)',
        }}
      />
      <span className="rank">{rank}</span>
      <span className="who">
        <b>{row.name}</b>
        {row.class && <em title={classLabel(row.class)}>{classLabel(row.class)}</em>}
        {row.deaths > 0 && (
          <span className="badge conf-low" title="Deaths this fight">
            {row.deaths}×
          </span>
        )}
      </span>
      <span className="val">
        <b>{fmt.num(value)}</b>
        <em>{fmt.num2(row[m.rate] || 0)}</em>
      </span>
    </div>
  )
}

export default function LiveMeter({
  fight, metric, onMetric, stale, maxRows, showTimers = true, showChart = true,
}) {
  const m = METRICS[metric] || METRICS.damage

  const rows = useMemo(() => {
    const all = (fight?.actors || []).filter((a) => a.kind === 'player')
    const sorted = [...all].sort((a, b) => (b[m.key] || 0) - (a[m.key] || 0))
    const live = sorted.filter((a) => (a[m.key] || 0) > 0)
    return maxRows ? live.slice(0, maxRows) : live
  }, [fight, m.key, maxRows])

  const max = rows.length ? rows[0][m.key] || 0 : 0

  if (!fight) {
    return (
      <div className="livemeter idle">
        <p className="muted">Between pulls — waiting for the next one.</p>
      </div>
    )
  }

  return (
    <div className={`livemeter ${stale ? 'stale' : ''}`}>
      <div className="liveheadline">
        {showChart && (
          <LiveTimeline dmg={fight.timeline?.dmg} heal={fight.timeline?.heal}
                        metric={metric} />
        )}
        <div className="lhtext">
          <div className="lhname">
            <span className={`dot ${stale ? '' : 'on'}`} />
            <h2>{fight.provisional_name || 'Pull in progress'}</h2>
            {fight.provisional_is_named && <span className="badge named">named</span>}
            <span className="zone">{fight.zone || 'Unknown zone'}</span>
          </div>
          <div className="lhnums">
            <span className="big">
              <b>{fmt.num2(fight.raid.dps)}</b><i>raid DPS</i>
            </span>
            <span className="big">
              <b>{fmt.num2(fight.raid.hps)}</b><i>raid HPS</i>
            </span>
            <span>
              <b>{fmt.dur(fight.elapsed_s)}</b><i>elapsed</i>
            </span>
            <span>
              <b>{fight.raid.raiders}</b><i>raiders</i>
            </span>
            <span className={fight.raid.deaths ? 'bad' : ''}>
              <b>{fight.raid.deaths}</b><i>deaths</i>
            </span>
          </div>
        </div>
      </div>

      {showTimers && <AoeTimers aoes={fight.aoes} logTs={fight.last_ts} />}

      <div className="metertabs">
        {Object.entries(METRICS).map(([k, v]) => (
          <button key={k} className={`chip ${metric === k ? 'on' : ''}`}
                  onClick={() => onMetric?.(k)}>
            {v.label}
          </button>
        ))}
        <span className="collabel">{m.rateLabel}</span>
      </div>

      <div className="meterrows">
        {rows.map((row, i) => (
          <MeterRow key={row.name} row={row} metric={metric} max={max} rank={i + 1} />
        ))}
        {!rows.length && (
          <p className="muted">No {m.label.toLowerCase()} in this pull yet.</p>
        )}
      </div>
    </div>
  )
}
