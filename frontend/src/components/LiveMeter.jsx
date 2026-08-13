import { useMemo, useState } from 'react'
import { fmt } from '../lib/api.js'
import { barFill, classLabel } from '../lib/classes.js'
import { useSmoothSeconds } from '../lib/smooth.js'
import AoeTimers from './AoeTimers.jsx'
import LiveTimeline, { peakRate } from './LiveTimeline.jsx'

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
   game (lib/classes.js).

   The metric chips are SWITCHES, not tabs: damage, healing and incoming can
   all be on at once, each as its own stack of bars. A raid leader watching a
   pull wants the tank's incoming NEXT TO the healers' output, not behind it.

   A row shows a RATE and no total. Mid-fight the total is the less useful of
   the two — it only ever goes up, so it says who has been fighting longest —
   and printing both put two numbers of different magnitudes side by side in
   every row, which is what a meter you read at a glance cannot afford. The
   healer stack earns the one extra column there is any call for: cures.

   NOTHING IN THE PARSE IS ANIMATED. Rows and bars change when the payload
   changes them, full stop. Tweened figures and sliding bars were both built
   and both removed: a rate counting up to its new value cannot be read while
   it does it, and the opening seconds of a pull — where every payload is an
   enormous relative change — turned into a slot machine. The answer to
   "the numbers feel stale" is a shorter ingest cadence, not an animation
   painted over the gap (docs/live.md). The two things that DO move are clocks,
   and they move because they are counting, not because they are decorated: the
   AoE bars drain against their own countdown, and the elapsed clock ticks once
   a second in the browser rather than restating log time in whatever steps the
   uploader's batches happened to be.

   MAX HIT is the exception to that rule and earns its place beside the rate:
   it is the one thing a rate cannot say. A 3M nuke and 3M of DoT ticks are the
   same DPS, and "what did that crit for" is a question the raid asks out loud
   every pull. It is the biggest single line of the fight so far, so it only
   ever goes up — which is fine for a number nobody averages. */

/* `short` is what the SWITCHES say, here and in the mini rail's config, and it
   is deliberately the word a raid says out loud rather than the metric's name:
   nobody asks who is top of "Healing". `label` stays the prose form for
   sentences and column captions, and `rateLabel` stays the head over the
   NUMBER — which is why tank keeps `Inc/s` there and is `TANK` on its switch:
   the switch names the stack, the head names the figure under it. */
export const METRICS = {
  damage: { key: 'damage', rate: 'dps', label: 'Damage', short: 'DPS', rateLabel: 'DPS',
            best: { key: 'max_hit', label: 'max hit' } },
  heal: { key: 'heals', rate: 'hps', label: 'Healing', short: 'HPS', rateLabel: 'HPS',
          extra: { key: 'cures', label: 'cures' },
          best: { key: 'max_heal', label: 'max heal' } },
  tank: { key: 'damage_taken', rate: 'dtps', label: 'Tank', short: 'TANK',
          rateLabel: 'Inc/s' },
}

/* Past this many bars a meter stops being readable at a glance and the raid is
   scrolling, so the tail folds away behind one line you can click. Twenty-four
   raiders is three screens of bars on a second monitor. */
const DEFAULT_ROWS = 12

/* The bars for one metric, ranked. Exported because `MiniRail` draws the same
   ranking at a fifth of the width, and two orderings of the same parse on one
   screen is the bug nobody would think to look for. */
export function meterRows(fight, mkey) {
  const m = METRICS[mkey]
  if (!m || !fight) return []
  return (fight.actors || [])
    .filter((a) => a.kind === 'player' && (a[m.key] || 0) > 0)
    .sort((a, b) => (b[m.key] || 0) - (a[m.key] || 0))
}

/* A recorded row carries no dtps; the elapsed clock recovers the same rate. */
export function meterRate(row, m, elapsed) {
  return row[m.rate] ?? (elapsed ? (row[m.key] || 0) / elapsed : 0)
}

function MeterRow({ row, m, max, rank, elapsed }) {
  const value = row[m.key] || 0
  const rate = meterRate(row, m, elapsed)
  const width = max > 0 ? Math.max(1.5, (value / max) * 100) : 0
  const extra = m.extra && (row[m.extra.key] || 0)
  const best = m.best && (row[m.best.key] || 0)
  return (
    <div className={`meterrow ${row.kind === 'mob' ? 'mob' : ''}`}>
      <i className="fill" style={{ width: `${width}%`, background: barFill(row.class) }} />
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
        {!!best && (
          <span className="side" title={`Biggest single ${m.best.label === 'max hit' ? 'hit' : 'heal'} so far: ${best.toLocaleString()}`}>
            {fmt.num(best)} {m.best.label}
          </span>
        )}
        {!!extra && (
          <span className="side" title={`${fmt.num(extra)} ${m.extra.label}`}>
            {fmt.num(extra)} {m.extra.label}
          </span>
        )}
        <em>{fmt.num2(rate)}</em>
      </span>
    </div>
  )
}

function MeterSection({ fight, mkey, maxRows, labelled }) {
  const m = METRICS[mkey]
  const [open, setOpen] = useState(false)
  const rows = useMemo(() => meterRows(fight, mkey), [fight, mkey])

  // `maxRows` is a HARD cap and belongs to the stream overlay, where the meter
  // has to fit a scene and nobody can click anything. Everywhere else the tail
  // is folded, not dropped.
  const cap = maxRows || DEFAULT_ROWS
  const shown = open && !maxRows ? rows : rows.slice(0, cap)
  const hidden = maxRows ? 0 : rows.length - shown.length
  const max = rows.length ? rows[0][m.key] || 0 : 0

  return (
    <div className="meterrows">
      {labelled && (
        <div className="meterlabel">
          <span>{m.label}</span>
          <em>{m.rateLabel}</em>
        </div>
      )}
      {shown.map((row, i) => (
        <MeterRow key={row.name} row={row} m={m} max={max} rank={i + 1}
                  elapsed={fight.elapsed_s} />
      ))}
      {!maxRows && (hidden > 0 || open) && (
        <button className="metermore" onClick={() => setOpen(!open)}>
          {open ? `⋯ show the top ${cap}` : `⋯ ${hidden} more`}
        </button>
      )}
      {!rows.length && (
        <p className="muted">No {m.label.toLowerCase()} in this pull yet.</p>
      )}
    </div>
  )
}

export default function LiveMeter({
  fight, metrics = ['damage'], onToggle, onToggleAoes, stale, maxRows, paused,
  showTimers = true, showChart = true, idleNote,
}) {
  const active = metrics.filter((k) => METRICS[k])
  const chartMetric = active.includes('heal') && !active.includes('damage')
    ? 'heal' : 'damage'
  /* The chart's high-water mark, printed as one of the headline figures rather
     than floated in the chart's corner — the corner is also where the numbers
     row ends on a narrow window, and two things wanting the same 90px is how
     you get a caption sitting on a stat. It only reads with a chart drawn. */
  const peak = showChart
    ? peakRate(chartMetric === 'heal' ? fight?.timeline?.heal : fight?.timeline?.dmg)
    : 0
  /* Log time restated as a clock the browser keeps, so it ticks once a second
     rather than in whatever steps the uploader's batches arrive in.

     It stops when the fight does, and `fight.ended` is the server saying so —
     combat has been quiet for `GAP_S`. The writer still cannot COMMIT the
     fight for another ten seconds (a late kill line may join it), and a clock
     that counted through that gap was the one thing on this screen that was
     actively wrong: ACT had called the pull and we were still counting.
     `stale` is the other reason to stop — the uploader has gone quiet, so the
     picture is not moving either. */
  const frozen = !!stale || !!paused || !!fight?.ended
  const elapsed = useSmoothSeconds(fight?.elapsed_s, !frozen)

  if (!fight) {
    return (
      <div className="livemeter idle">
        <p className="muted">{idleNote || 'Waiting for the first pull.'}</p>
      </div>
    )
  }

  return (
    <div className={`livemeter ${stale ? 'stale' : ''} ${paused ? 'off' : ''}`}>
      <div className="liveheadline">
        {showChart && (
          <LiveTimeline dmg={fight.timeline?.dmg} heal={fight.timeline?.heal}
                        metric={chartMetric} />
        )}
        <div className="lhtext">
          <div className="lhname">
            <span className={`dot ${frozen ? '' : 'on'}`} />
            <h2>{fight.provisional_name || 'Pull in progress'}</h2>
            {fight.provisional_is_named && <span className="badge named">named</span>}
            {/* the pull is over and its record is a few seconds behind it —
                say so, rather than leaving a still parse that reads as live */}
            {fight.ended && !paused && <span className="badge">ended</span>}
            <span className="zone">{fight.zone || 'Unknown zone'}</span>
          </div>
          <div className="lhnums">
            {/* the raid rate is read from across the room, and two decimals on
                a six-figure number are two characters nobody reads */}
            <span className="big">
              <b>{fmt.num(fight.raid.dps)}</b><i>raid DPS</i>
            </span>
            <span className="big">
              <b>{fmt.num(fight.raid.hps)}</b><i>raid HPS</i>
            </span>
            {!!peak && (
              <span title={`Best smoothed second of the pull: ${Math.round(peak).toLocaleString()}/s`}>
                <b>{fmt.num(peak)}</b><i>peak {chartMetric === 'heal' ? 'HPS' : 'DPS'}</i>
              </span>
            )}
            <span>
              <b>{fmt.clock(elapsed)}</b><i>elapsed</i>
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

      {/* Paused means nothing on this panel moves, countdowns included. They
          are the only other thing here that is a function of time.

          This is the full-width panel and the only one anybody can click, so
          the joust ticks and the suggested timers live here rather than on the
          rail or over somebody's stream. */}
      {showTimers && !paused && (
        <AoeTimers aoes={fight.aoes} logTs={fight.log_ts ?? fight.last_ts}
                   running={!frozen} dropS={fight.aoe_drop_s} missedS={fight.aoe_missed_s}
                   editable showSuggest />
      )}

      {/* THESE SWITCH THIS METER AND NOTHING ELSE. They used to drive the mini
          rail as well, on the reasoning that a raid wants one answer to "which
          meters am I watching" — which is exactly wrong: the middle column is
          read between pulls and the strip beside the game is read during one,
          so the middle can afford three stacks and the strip cannot. The rail
          keeps its own switches, in its own settings panel (`MiniRail`). */}
      {(onToggle || onToggleAoes) && (
        <div className="metertabs">
          {onToggle && Object.entries(METRICS).map(([k, v]) => (
            <button key={k} className={`chip ${active.includes(k) ? 'on' : ''}`}
                    aria-pressed={active.includes(k)}
                    title={active.includes(k)
                      ? `Hide ${v.label} on this meter`
                      : `Show ${v.label} on this meter`}
                    onClick={() => onToggle(k)}>
              {v.short}
            </button>
          ))}
          {/* THE COUNTDOWNS ARE A SWITCH HERE TOO, and it belongs in this row
              rather than beside Mini and Parse in the rail head: those say
              which SURFACE is on, this says what is in the middle column —
              exactly what the metric chips say. It is the rail's AoE switch
              (`MiniRail`'s ⚙) one panel over, and for the same reason the
              metric chips split: the strip beside the game and the page read
              between pulls do not want the same things on them.

              Ruled off from the metric chips because it is not one of them: a
              chip to the left of the rule adds a stack of BARS, this one adds
              a panel. */}
          {onToggleAoes && (
            <>
              {onToggle && <i className="tabsep" aria-hidden="true" />}
              <button className={`chip ${showTimers ? 'on' : ''}`}
                      aria-pressed={showTimers}
                      title={showTimers
                        ? 'Hide the AoE countdowns on this meter — the mini rail keeps its own'
                        : 'Show the AoE countdowns above the bars'}
                      onClick={onToggleAoes}>
                AoEs
              </button>
            </>
          )}
          {onToggle && active.length === 1 && (
            <span className="collabel">{METRICS[active[0]].rateLabel}</span>
          )}
        </div>
      )}

      {active.map((k) => (
        <MeterSection key={k} fight={fight} mkey={k} maxRows={maxRows}
                      labelled={active.length > 1 || !onToggle} />
      ))}
    </div>
  )
}
