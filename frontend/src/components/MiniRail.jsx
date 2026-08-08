import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import MiniParse from './MiniParse.jsx'

/* ACT's mini overlays: the spell timers and the parse, docked to the edge of
   the window and narrow enough to live beside the game.

   The dashboard is a second-monitor page and assumes it owns that monitor. A
   lot of the time it does not — the game is there, and what is wanted is a
   strip of it: what is due, who is on top, nothing else. `MiniParse` is that
   strip and draws the whole of it; this file is the DOCK — which edge it is
   pinned to, and the buttons that move and close it.

   Which side is a setting, because the answer depends on where the second
   monitor is — a rail on the left is useless to somebody whose game sits to
   the left of the browser. It is remembered, since it is a fact about a desk
   and not about a pull.

   Rendered into `document.body`. Every `.card` on this page carries
   backdrop-filter, which is a containing block for position:fixed as well as a
   stacking context, so a fixed rail written inside one is sealed into it —
   the same trap `RaidNotes` and `Picker` document. */

const SIDE_KEY = 'eq2a.mini.side'

export default function MiniRail({ fight, metrics = ['damage'], stale, onClose }) {
  const [side, setSide] = useState(
    () => (localStorage.getItem(SIDE_KEY) === 'right' ? 'right' : 'left'))
  useEffect(() => { localStorage.setItem(SIDE_KEY, side) }, [side])

  return createPortal((
    <aside className={`minirail ${side}`} aria-label="Mini overlays">
      <MiniParse
        fight={fight}
        metrics={metrics}
        stale={stale}
        actions={(
          <>
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
