import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import MiniParse from './MiniParse.jsx'
import { METRICS } from './LiveMeter.jsx'
import { SettingRow, Switch } from './Settings.jsx'

/* ACT's mini overlays: the spell timers and the parse, docked to the edge of
   the window and narrow enough to live beside the game.

   The dashboard is a second-monitor page and assumes it owns that monitor. A
   lot of the time it does not — the game is there, and what is wanted is a
   strip of it: what is due, who is on top, nothing else. `MiniParse` is that
   strip and draws the whole of it; this file is the DOCK — which edge it is
   pinned to, WHAT IS ON IT, and the buttons that move and close it.

   Which side is a setting, because the answer depends on where the second
   monitor is — a rail on the left is useless to somebody whose game sits to
   the left of the browser. It is remembered, since it is a fact about a desk
   and not about a pull.

   THE RAIL HAS ITS OWN SWITCHES, and that is the point of the ⚙ beside them.
   It used to take the dashboard meter's — one set of chips drove both — and
   that made a decision for the wrong surface: the middle column is a page you
   read between pulls and can spare three stacks of bars, while this is a 244px
   strip read mid-fight with the game behind it, where the third stack is the
   one that pushes the countdowns off the screen. So the meter's chips switch
   the meter, these switch the rail, and neither is a global setting about "the
   parse". They are remembered for the same reason the side is: what is on the
   strip beside the game is a fact about a desk.

   Rendered into `document.body`. Every `.card` on this page carries
   backdrop-filter, which is a containing block for position:fixed as well as a
   stacking context, so a fixed rail written inside one is sealed into it —
   the same trap `RaidNotes` and `Picker` document. */

const SIDE_KEY = 'eq2a.mini.side'
const CFG_KEY = 'eq2a.mini.cfg'

/* NOTIFICATIONS DEFAULT ON, with the two countdown kinds on under them. The
   panel is empty until something is actually happening, so the cost of the
   default being wrong is nothing, and the cost of it being OFF is that the one
   feature you cannot discover by looking at the rail stays undiscovered. */
const DEFAULTS = {
  metrics: ['damage'],
  timers: true,
  burn: true,
  notify: true,
  notifyAoes: true,
  notifyBurn: true,
}

const readCfg = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(CFG_KEY) || '{}')
    const cfg = { ...DEFAULTS, ...(raw && typeof raw === 'object' ? raw : {}) }
    // a metric key that no longer exists is a setting from an older build, not
    // a stack to draw
    cfg.metrics = (Array.isArray(cfg.metrics) ? cfg.metrics : [])
      .filter((k) => METRICS[k])
    return cfg
  } catch {
    return { ...DEFAULTS }   // private mode, or somebody's hand-edited value
  }
}

/* WHAT IS ON THE RAIL — the ⚙ panel, docked directly under the head.

   THE SAME LIST THE STREAM OVERLAY HAS (`Settings.jsx`, `OverlayOptions.jsx`).
   These two panels answer one question — what is on screen while the raid runs
   — and they used to answer it in two idioms: a grid of lit PILLS here, named
   rows with a switch and a line of explanation there. The pills were argued for
   on size (a 244px strip cannot afford prose), but the thing a strip cannot
   afford is a control you have to press to find out what it does: `Burn window`
   lit gold says nothing about what a burn window is, and the sentence that
   would have said it was in a tooltip nobody hovers mid-pull. So it is rows
   here too, tightened in CSS (`.miniconf .settingrow`) rather than rebuilt.

   Two groups, and the split is the one that matters: the first says what is
   DRAWN on the strip, the second says what INTERRUPTS. They are different
   questions about the same abilities — plenty of raids want the countdowns on
   the rail without a card in their face for each one, and somebody playing a
   tank wants the cards with no parse at all — so the AoE and burn switches
   appear in both groups rather than one pair being made to mean both.

   Under the head rather than over the parse, because the parse is the panel
   that gives up height (base.css) and a settings list that shoves the
   countdowns off the bottom is one nobody opens during a fight. */
function MiniConfig({ cfg, onSet, onMetric, onClose }) {
  return (
    <div className="minipanel miniconf" role="dialog" aria-label="Mini parse settings">
      <div className="pophead">
        <b>What is on the rail</b>
        <button className="minibtn" onClick={onClose}
                title="Close" aria-label="Close settings">×</button>
      </div>

      {/* The one row that is not a switch, for the one setting that is not a
          yes/no: any subset of the three stacks, chips like the overlay's own
          layout and theme rows. The hint says what is on rather than repeating
          the chips — at this width the chips ARE the reading. */}
      <SettingRow as="div" label="Bars" className="stack"
                  on={cfg.metrics.length > 0}
                  hint={cfg.metrics.length
                    ? cfg.metrics.map((k) => METRICS[k].label).join(' · ')
                    : 'None — countdowns and notifications only'}>
        <span className="chips">
          {Object.entries(METRICS).map(([k, m]) => (
            <button key={k} type="button" aria-pressed={cfg.metrics.includes(k)}
                    className={`chip ${cfg.metrics.includes(k) ? 'on' : ''}`}
                    title={cfg.metrics.includes(k)
                      ? `Take the ${m.label.toLowerCase()} bars off the rail`
                      : `Put the ${m.label.toLowerCase()} bars on the rail`}
                    onClick={() => onMetric(k)}>
              {m.short}
            </button>
          ))}
        </span>
      </SettingRow>

      <SettingRow label="AoE timers" on={cfg.timers}
                  hint="The countdown strip above the bars">
        <Switch on={cfg.timers} onChange={(v) => onSet({ timers: v })} />
      </SettingRow>

      <SettingRow label="Burn window" on={cfg.burn}
                  hint="A jousted cast read the other way round: time left in melee">
        <Switch on={cfg.burn} onChange={(v) => onSet({ burn: v })} />
      </SettingRow>

      <div className="confsec">Notifications</div>

      <SettingRow label="Notifications" on={cfg.notify}
                  hint={cfg.notify
                    ? 'On — cards pop up under the parse'
                    : 'Off — nothing interrupts the rail'}>
        <Switch on={cfg.notify} onChange={(v) => onSet({ notify: v })} />
      </SettingRow>
      {/* Off with the master switch rather than hidden by it: a row that
          vanishes takes its setting with it, and coming back to a panel that
          has forgotten what you told it is worse than a greyed row. */}
      <div className="confopts">
        <SettingRow label="AoEs" on={cfg.notify && cfg.notifyAoes}
                    hint="A marked AoE, a few seconds before it lands">
          <Switch on={cfg.notify && cfg.notifyAoes} disabled={!cfg.notify}
                  onChange={(v) => onSet({ notifyAoes: v })} />
        </SettingRow>
        <SettingRow label="Burn window" on={cfg.notify && cfg.notifyBurn}
                    hint="JOUST, as the window closes">
          <Switch on={cfg.notify && cfg.notifyBurn} disabled={!cfg.notify}
                  onChange={(v) => onSet({ notifyBurn: v })} />
        </SettingRow>
      </div>
      {/* The two that are not switchable, said once here rather than left to be
          discovered the night they fire. A tank on the floor is not a countdown
          somebody tuned out — it is the fight changing shape. */}
      <p className="confnote">
        A tank dying, or the raid going down together, always pops up while
        notifications are on.
      </p>
    </div>
  )
}

export default function MiniRail({ fight, stale, onClose }) {
  const [side, setSide] = useState(
    () => (localStorage.getItem(SIDE_KEY) === 'right' ? 'right' : 'left'))
  useEffect(() => { localStorage.setItem(SIDE_KEY, side) }, [side])

  const [cfg, setCfg] = useState(readCfg)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    try { localStorage.setItem(CFG_KEY, JSON.stringify(cfg)) } catch { /* private mode */ }
  }, [cfg])

  const set = (patch) => setCfg((c) => ({ ...c, ...patch }))
  /* Any subset, including NONE — see MiniParse: countdowns and notifications
     with no bars under them is a rail somebody has deliberately asked for. The
     order is METRICS' own, so switching one back on puts it where it was. */
  const toggleMetric = (k) => setCfg((c) => {
    const next = c.metrics.includes(k)
      ? c.metrics.filter((x) => x !== k) : [...c.metrics, k]
    return { ...c, metrics: Object.keys(METRICS).filter((x) => next.includes(x)) }
  })

  return createPortal((
    <aside className={`minirail ${side}`} aria-label="Mini overlays">
      <MiniParse
        fight={fight}
        metrics={cfg.metrics}
        stale={stale}
        showAoes={cfg.timers}
        showBurn={cfg.burn}
        notify={cfg.notify ? { aoes: cfg.notifyAoes, burn: cfg.notifyBurn } : null}
        panel={open && (
          <MiniConfig cfg={cfg} onSet={set} onMetric={toggleMetric}
                      onClose={() => setOpen(false)} />
        )}
        actions={(
          <>
            <button className={`minibtn ${open ? 'on' : ''}`}
                    onClick={() => setOpen(!open)} aria-expanded={open}
                    title="What is on the rail" aria-label="Mini parse settings">
              ⚙
            </button>
            <button className="minibtn" onClick={() => setSide(side === 'left' ? 'right' : 'left')}
                    title={`Move to the ${side === 'left' ? 'right' : 'left'} edge`}
                    aria-label={`Move to the ${side === 'left' ? 'right' : 'left'} edge`}>
              {side === 'left' ? '»' : '«'}
            </button>
            <button className="minibtn" onClick={onClose}
                    title="Close the mini overlays" aria-label="Close the mini overlays">
              ×
            </button>
          </>
        )}
      />
    </aside>
  ), document.body)
}
