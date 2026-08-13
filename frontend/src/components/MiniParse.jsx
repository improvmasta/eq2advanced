import { fmt } from '../lib/api.js'
import { barFill, classLabel, classShort } from '../lib/classes.js'
import { useSmoothSeconds } from '../lib/smooth.js'
import AoeTimers, { miniTimers } from './AoeTimers.jsx'
import { METRICS, meterRate, meterRows } from './LiveMeter.jsx'
import MiniAlerts from './MiniAlerts.jsx'
import { useMiniPins } from '../lib/minipin.js'

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
   spoken short form, the rate and the biggest hit; the deaths badge, the cures
   column, the AoE source and the hit/blocked split all go, and so does the
   fold — nobody clicks "12 more" mid-pull, and nobody watching a stream can
   click at all.

   THE RATE SITS AGAINST THE NAME and the max hit is the outer column, which is
   the opposite of the dashboard's meter and of what this drew first. On a
   full-width meter the eye has room to run along a row; at 244px it does not,
   and the ranking is read DOWN the rate — so the rate belongs next to the
   thing that says whose it is. The max hit is the number you go looking for
   ("what did that crit for"), not the one you scan, so it takes the far side.
   Each cell keeps its own weight through the swap: the rate is the bold
   figure, the max hit stays muted.

   It does NOT re-derive the ranking. `LiveMeter` exports `meterRows`/
   `meterRate` and this calls them, because two orderings of the same parse on
   one screen is the bug nobody would think to look for.

   The caller supplies the box. This renders a run of `.minipanel`s and nothing
   that positions them — the rail is fixed to a window edge, the overlay fills
   an OBS source, and neither wants the other's frame. */

/* The rail is a glance, and the fold is the wrong affordance here. */
export const MINI_ROWS = 10

export function MiniSection({ fight, mkey, rows: cap = MINI_ROWS, dense = false }) {
  const m = METRICS[mkey]
  const rows = meterRows(fight, mkey).slice(0, cap)
  const max = rows.length ? rows[0][m.key] || 0 : 0
  if (!rows.length) return null
  return (
    <div className="minirows">
      {/* Column heads, which the dashboard's meter has had all along and this
          did not — it carried the metric's name only when TWO stacks were on
          screen and needed telling apart. That is the author's question, not
          the reader's: somebody who arrives at a stream mid-pull has no way to
          know whether the big number is DPS or HPS, and no way to ask. The
          rank needs no head (a column of 1..10 is self-evident) and neither
          does the class, which reads as the caption on the name it sits
          against. The numeric heads are what earn their line. */}
      {/* `dense` drops the max hit column with its head. It is the number you
          go LOOKING for — "what did that crit for" — which is a question you
          ask after the pull, and it was costing about a fifth of a window whose
          whole problem is width. Every parse page still has it, and so does the
          stream overlay, where there is room. */}
      <div className="minirow minihdr">
        <span className="rank" />
        <span className="who">Player</span>
        <span className="val">{m.rateLabel}</span>
        {m.best && !dense && <span className="best">{m.best.label}</span>}
      </div>
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
                recognizable, a clipped class is not.

                `dense` DROPS IT, and that is the in-game window paying for the
                name. At ~180px the class was not a caption on the name, it was
                the reason there was no name: rows read `S… Swash`, which
                identifies nobody. A four-character class beside a truncated
                first initial is the wrong half of the pair to keep — the bar's
                hue still carries the archetype, and the person reading this one
                is in the raid and knows who is in it. */}
            <span className="who">
              <b title={row.name}>{row.name}</b>
              {!dense && row.class && (
                <em className="cls" title={classLabel(row.class)}>{classShort(row.class)}</em>
              )}
            </span>
            {/* ROUNDED, unlike the dashboard meter and the parse tables, which
                keep ACT's two decimals because they are read side by side with
                ACT. Nobody reads `.20` off a strip beside the game, and nobody
                on a stream can read it at all — those three characters are
                width, and width here is type size. */}
            <span className="val">
              {fmt.num(meterRate(row, m, fight.elapsed_s))}
            </span>
            {/* the one number the rate cannot say — see METRICS.best. It used
                to caption itself (`12.4k max`), because an unlabelled second
                figure beside a rate is just two numbers and people read the
                wrong one. The column head says it now, once, for ten rows. */}
            {!dense && !!best && (
              <span className="best" title={`${m.best.label}: ${best.toLocaleString()}`}>
                {fmt.num(best)}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

/* `dense` is the THIRD size this component is drawn at, and the tightest.
   The dock is 244px of a monitor and the OBS scene is as wide as somebody
   chooses; the in-game window is neither — it is a rectangle inside the game's
   own UI, perhaps 180px, over the top of a hotbar, read at 1:1 while playing.
   Everything it drops is dropped because the thing beside it was worth more at
   that width: the class (so the NAME fits), and HPS and deaths off the headline
   (so the head is one thin line instead of four columns, one of which was
   already clipping). Deaths are not lost — the notification block under the
   bars is the in-game window's own, and a death is what it is for. */
export default function MiniParse({
  fight, metrics = ['damage'], rows = MINI_ROWS, layout = 'vertical',
  showAoes = true, showBurn = true, showNums = true, showHead = true,
  showSuggest = true, notify = null, panel = null, dense = false,
  stale, title, actions,
}) {
  const active = metrics.filter((k) => METRICS[k])
  const aoes = fight?.aoes || []
  /* Read HERE as well as inside `AoeTimers`, because the frame around the
     panel is this component's and the rows inside it are not: the strip is
     bordered whether or not anything is in it, so the question "will there be
     a row" has to be asked with the same pins the answer will be computed
     with. */
  const pins = useMiniPins()
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

      {/* Whatever the DOCK wants directly under its own head — today that is
          the settings panel the ⚙ opens (`MiniRail`). It goes through here
          rather than being rendered beside this component because the rail is
          a run of panels in one flex column and a panel written outside the
          run would sit outside the order. */}
      {panel}

      {/* Raid DPS and the clock are the two figures that are not on any row
          below, which is what earns them a headline. HPS is a whole STACK away
          (turn healing on and it is twenty rows, not one number), and deaths
          are what the notification block exists to shout about — so `dense`
          keeps the pair and drops the pair, and the head becomes one line.

          AND IT REVERSES THEM, so raid DPS lands on the right — directly over
          the column of per-raider rates under it, because a total that does not
          line up with the numbers it totals is a number you have to go looking
          for. The clock takes the left, where a row's rank would be. */}
      {showNums && fight && (
        <div className="minipanel mininums">
          {dense && <span><b>{fmt.clock(elapsed)}</b><i>time</i></span>}
          <span><b>{fmt.num(fight.raid.dps)}</b><i>dps</i></span>
          {!dense && <span><b>{fmt.num(fight.raid.hps)}</b><i>hps</i></span>}
          {!dense && <span><b>{fmt.clock(elapsed)}</b><i>time</i></span>}
          {!dense && (
            <span className={fight.raid.deaths ? 'bad' : ''}>
              <b>{fight.raid.deaths}</b><i>dead</i>
            </span>
          )}
        </div>
      )}

      {/* Asked of `AoeTimers`' own rule rather than of `aoes.length`: this
          panel is a bordered strip whether or not there is a row inside it,
          and the compact list is not the payload's list — an ability off the
          MINI mark never gets here, and a countdown-less row is a raider off
          the bottom of the scene (see `miniTimers`). */}
      {showAoes && !!miniTimers(aoes, !frozen, pins).length && (
        <div className="minipanel minitimers">
          {/* The countdowns freeze for exactly the reasons the clock above
              does. A pull that has ENDED has no next cast, so a bar still
              draining toward one — on the rail, or over somebody's stream — is
              counting down to something that will never happen. */}
          <AoeTimers aoes={aoes} logTs={fight?.log_ts ?? fight?.last_ts}
                     running={!frozen} dropS={fight?.aoe_drop_s} missedS={fight?.aoe_missed_s}
                     showSuggest={showSuggest} showBurn={showBurn} compact />
        </div>
      )}

      {/* EVERY meter switched off is a legitimate setting, not an empty state:
          somebody who wants the countdowns and the notifications beside the
          game and nothing else has asked for exactly this, and a bordered strip
          with nothing in it would be the only thing on screen saying otherwise.
          The idle panel is for having no FIGHT, which is a different thing. */}
      {!fight ? (
        <div className="minipanel minidle">Waiting for the first pull.</div>
      ) : !!active.length && (
        /* `horizontal` puts the second stack BESIDE the first instead of under
           it — the same parse in a wide scene rather than a tall one. */
        /* Dimmed only when the UPLOADER is quiet — a fight that has merely
           ended is the thing you are reading right now, and washing it out the
           second the boss dies is exactly backwards. */
        <div className={`minipanel miniparse ${layout}${stale ? ' stale' : ''}`}>
          {active.map((k) => (
            <MiniSection key={k} fight={fight} mkey={k} rows={rows} dense={dense} />
          ))}
        </div>
      )}

      {/* LAST, because it is the one panel that appears and disappears — under
          the parse, where it can push nothing around, and mounted even when it
          has nothing to draw so that it keeps counting deaths between cards. */}
      {notify && (
        <MiniAlerts fight={fight} running={!frozen} notify={notify} />
      )}
    </>
  )
}
