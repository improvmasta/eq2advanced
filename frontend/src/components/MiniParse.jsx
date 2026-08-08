import { fmt } from '../lib/api.js'
import { barFill, classLabel, classShort } from '../lib/classes.js'
import { useSmoothSeconds } from '../lib/smooth.js'
import AoeTimers from './AoeTimers.jsx'
import { METRICS, meterRate, meterRows } from './LiveMeter.jsx'

/* The parse at a fifth of the width: the dashboard's meter with everything a
   glance does not use taken out.

   TWO surfaces render this, and that is the point. The mini overlays docked to
   the window edge (`MiniRail.jsx`) and the stream overlay in somebody's OBS
   scene (`pages/Overlay.jsx`) are the same object under different constraints
   — a strip beside the game, and a strip over the game on a stream — so they
   are one component rather than two that drift. The overlay used to draw the
   full `LiveMeter` and looked like a page someone had shrunk.

   The condensing is HORIZONTAL, because that is what is scarce in both cases:
   vertical space on a 1440p panel is free and every pixel of width is taken
   from the game or the scene. So a row is the rank, the name, the class in its
   spoken short form, the biggest hit and the rate; the deaths badge, the cures
   column, the AoE source and the hit/blocked split all go, and so does the
   fold — nobody clicks "12 more" mid-pull, and nobody watching a stream can
   click at all.

   It does NOT re-derive the ranking. `LiveMeter` exports `meterRows`/
   `meterRate` and this calls them, because two orderings of the same parse on
   one screen is the bug nobody would think to look for.

   The caller supplies the box. This renders a run of `.minipanel`s and nothing
   that positions them — the rail is fixed to a window edge, the overlay fills
   an OBS source, and neither wants the other's frame. */

/* The rail is a glance, and the fold is the wrong affordance here. */
export const MINI_ROWS = 10

export function MiniSection({ fight, mkey, rows: cap = MINI_ROWS, labelled }) {
  const m = METRICS[mkey]
  const rows = meterRows(fight, mkey).slice(0, cap)
  const max = rows.length ? rows[0][m.key] || 0 : 0
  if (!rows.length) return null
  return (
    <div className="minirows">
      {labelled && <div className="minilabel">{m.rateLabel}</div>}
      {rows.map((row, i) => {
        const best = m.best && (row[m.best.key] || 0)
        return (
          <div key={row.name} className="minirow">
            <i className="fill"
               style={{
                 width: `${max > 0 ? Math.max(1.5, ((row[m.key] || 0) / max) * 100) : 0}%`,
                 background: barFill(row.class),
               }} />
            <span className="rank">{i + 1}</span>
            {/* Name and class are ONE cell, not two things the row spaces out:
                the class is a caption on the name and reads as one when it sits
                against it — pushed to the far side of the gap it reads as its
                own column and you have to track back across the row to see
                whose it is.

                SHORT form (`SK`, not `Shadowknight`), because the full word is
                wider than the name it is captioning at 244px. The bar's hue is
                the ARCHETYPE and four hues cannot separate six fighters, so
                "which tank is that" needs the word, not the color. The name
                ellipsizes and the class does not: a clipped name is still
                recognizable, a clipped class is not. */}
            <span className="who">
              <b title={row.name}>{row.name}</b>
              {row.class && (
                <em className="cls" title={classLabel(row.class)}>{classShort(row.class)}</em>
              )}
            </span>
            {/* the one number the rate cannot say — see METRICS.best. It is
                LABELLED, because an unlabelled second figure beside a rate is
                just two numbers and people read the wrong one. */}
            {!!best && (
              <span className="best" title={`${m.best.label}: ${best.toLocaleString()}`}>
                {fmt.num(best)}<i>max</i>
              </span>
            )}
            <span className="val">
              {fmt.num2(meterRate(row, m, fight.elapsed_s))}
            </span>
          </div>
        )
      })}
    </div>
  )
}

export default function MiniParse({
  fight, metrics = ['damage'], rows = MINI_ROWS, layout = 'vertical',
  showAoes = true, showNums = true, showHead = true, stale, title, actions,
}) {
  const active = metrics.filter((k) => METRICS[k])
  const aoes = fight?.aoes || []
  /* Same browser-side clock as the dashboard's meter, and it stops for the
     same two reasons: the fight is over (`ended` — combat quiet for `GAP_S`,
     which is where ACT calls it), or the uploader has gone quiet. The rail is
     read out of the corner of an eye, and a clock still counting on a pull
     that finished is what an eye catches. */
  const frozen = !!stale || !!fight?.ended
  const elapsed = useSmoothSeconds(fight?.elapsed_s, !frozen)

  return (
    <>
      {showHead && (
        <div className="minipanel minihead">
          <span className={`dot ${fight && !frozen ? 'on' : ''}`} />
          <b title={fight?.provisional_name || ''}>
            {title || fight?.provisional_name || 'Waiting for the first pull'}
          </b>
          {actions}
        </div>
      )}

      {showNums && fight && (
        <div className="minipanel mininums">
          <span><b>{fmt.num(fight.raid.dps)}</b><i>dps</i></span>
          <span><b>{fmt.num(fight.raid.hps)}</b><i>hps</i></span>
          <span><b>{fmt.clock(elapsed)}</b><i>time</i></span>
          <span className={fight.raid.deaths ? 'bad' : ''}>
            <b>{fight.raid.deaths}</b><i>dead</i>
          </span>
        </div>
      )}

      {showAoes && !!aoes.length && (
        <div className="minipanel minitimers">
          <AoeTimers aoes={aoes} logTs={fight?.log_ts ?? fight?.last_ts} compact />
        </div>
      )}

      {fight ? (
        /* `horizontal` puts the second stack BESIDE the first instead of under
           it — the same parse in a wide scene rather than a tall one. */
        /* Dimmed only when the UPLOADER is quiet — a fight that has merely
           ended is the thing you are reading right now, and washing it out the
           second the boss dies is exactly backwards. */
        <div className={`minipanel miniparse ${layout}${stale ? ' stale' : ''}`}>
          {active.map((k) => (
            <MiniSection key={k} fight={fight} mkey={k} rows={rows}
                         labelled={active.length > 1} />
          ))}
        </div>
      ) : (
        <div className="minipanel minidle">Waiting for the first pull.</div>
      )}
    </>
  )
}
