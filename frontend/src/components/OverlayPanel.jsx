import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import OverlayOptions, { useOverlays } from './OverlayOptions.jsx'

/* A parse LINK's controls, on the desk they are used from.

   Beside the Mini switch, because they are the same gesture: both say what is
   on screen while the raid runs, and both are reached mid-pull by somebody who
   is not going to open another tab. The account page keeps the same panel for
   anyone who goes looking there — `OverlayOptions` is one component, used
   twice.

   TWO BUTTONS, ONE COMPONENT (`kind`). `Overlay` is the OBS browser source;
   `In-game` is EQ2's own browser window, which is the same live meter read at
   a completely different size and with the notifications turned back on
   (schema v34, `OverlayOptions`). They are separate LINKS rather than one link
   with two sizes, so revoking the one that ended up in a VOD does not take the
   window beside somebody's hotbars down with it — which is also why this hook
   filters by kind. A panel reaching for `overlays[0]` would open the wrong
   settings the moment both links exist.

   It opens as a small window over the dashboard rather than expanding the bar:
   a settings list that pushes the meter down every time it is opened is a
   settings list nobody opens during a fight.

   The FIRST open mints the link if the account has none, so "copy link" always
   has something to copy — the old flow made you go to /account, press Create,
   come back, and it is the only reason anyone would.

   Rendered into `document.body`, positioned off the button's rect: every card
   on this page carries `backdrop-filter`, which is a containing block for
   `position: fixed` as well as a stacking context, so a panel written inside
   one is sealed into it (the trap `Picker` and `RaidNotes` document). */

export default function OverlayPanel({ kind = 'overlay' }) {
  const ingame = kind === 'ingame'
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState(null)
  const { overlays, err, create, change, revoke } = useOverlays(kind)
  const box = useRef(null)
  const panel = useRef(null)
  const minted = useRef(false)

  const overlay = overlays?.[0] || null
  const live = overlay ? overlay.config?.enabled !== false : false

  // one link, minted the first time the panel is opened and never again — a
  // failed mint must not turn every open into another attempt
  useEffect(() => {
    if (!open || overlays === null || overlays.length || minted.current) return
    minted.current = true
    create()
  }, [open, overlays, create])

  useLayoutEffect(() => {
    if (!open) { setAt(null); return undefined }
    const place = () => {
      const r = box.current?.getBoundingClientRect()
      if (!r) return
      setAt({
        top: r.bottom + 6,
        // never off the right edge: this button sits well into the bar
        left: Math.max(8, Math.min(r.left, window.innerWidth - 372)),
        cap: window.innerHeight - r.bottom - 24,
      })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return undefined
    const away = (ev) => {
      if (box.current?.contains(ev.target) || panel.current?.contains(ev.target)) return
      setOpen(false)
    }
    const esc = (ev) => { if (ev.key === 'Escape') { ev.stopPropagation(); setOpen(false) } }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  const title = ingame ? 'In-game window' : 'Stream overlay'
  return (
    <span className="overlaybtn" ref={box}>
      <button className={`chip ${open ? 'on' : ''}`} aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              title={ingame
                ? "The link, and what it shows, for EQ2's own browser window"
                : 'The link, and what it shows, for an OBS browser source'}>
        {ingame ? 'In-game' : 'Overlay'}
        {overlay && <i className={`dot ${live ? 'on' : ''}`} aria-hidden="true" />}
      </button>
      {open && at && createPortal((
        <div className="overlaypop" ref={panel} role="dialog"
             aria-label={title}
             style={{ top: at.top, left: at.left,
                      '--pop-cap': `${Math.max(220, at.cap)}px` }}>
          <div className="pophead">
            <b>{title}</b>
            <button className="minibtn" onClick={() => setOpen(false)}
                    aria-label="Close">×</button>
          </div>
          {err && <p className="err">{err}</p>}
          {overlays === null && <p className="muted">Loading…</p>}
          {/* After a revoke there is deliberately no link and no new one: a URL
              somebody just killed must not come straight back. */}
          {overlays !== null && !overlay && (minted.current ? (
            <div className="formcol" style={{ marginTop: 8 }}>
              <button onClick={() => create()}>
                {ingame ? 'Create an in-game link' : 'Create an overlay link'}
              </button>
            </div>
          ) : <p className="muted">Making a link…</p>)}
          {overlay && (
            <>
              <p className="note">
                {ingame
                  ? 'Paste this into EQ2’s browser window (/browser, or the'
                    + ' EQ2 Maps button). It shows the fight you are in and'
                    + ' nothing else — and it is sized for a corner of your UI.'
                  : 'Paste this into an OBS browser source. Anyone holding it'
                    + ' sees the fight you are in — and nothing else, ever.'}
              </p>
              <OverlayOptions overlay={overlay}
                              onChange={(cfg) => change(overlay, cfg)}
                              onRevoke={() => { revoke(overlay); minted.current = true }} />
            </>
          )}
        </div>
      ), document.body)}
    </span>
  )
}
