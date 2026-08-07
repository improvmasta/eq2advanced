import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../lib/api.js'

/* What to CALL an imported parse. ACT's title bar names the view, not the
   parse, so a whole-night screenshot comes back called `All` — which is a true
   answer to a question nobody asked and no answer at all to "which parse is
   this". The name people would write themselves is who, where and which fight:
   `Bobby — Halls of Fate — All`. Each part is dropped when the shot doesn't
   carry it, so an anonymous image is still named by whatever it does have and
   only a shot with nothing at all falls back to its id. */
export const shotTitle = (shot) => [shot?.character_name, shot?.zone, shot?.encounter]
  .filter(Boolean).join(' — ') || `Imported parse #${shot?.id ?? ''}`

/* The screenshot an imported parse was read from, over the page.

   IT IS RENDERED INTO `document.body`, not where it is written. Every card on
   this site carries `backdrop-filter`, and a filtered ancestor is a containing
   block for `position: fixed` AND a stacking context — so a viewer opened from
   inside a compare column was trapped in that column's box and painted under
   the column to its right. Nothing about the CSS said so; the fix is to leave
   the card rather than to fight it with a bigger z-index.

   It OPENS FIT TO THE SCREEN and zooms on request. Opening at the stored size
   was the first rule here, on the argument that the reason to open a shot is to
   read a number off it and a scaled-down screenshot of small antialiased digits
   is exactly what cannot be checked. That argument holds for the zoom and not
   for the opening: a 2200px capture dropped onto a laptop screen shows you one
   corner of a table and no way to tell which corner. So the first thing shown
   is the whole window, and `Full size` — in the head bar, which is the one
   place a click does not close the viewer — scrolls it at its stored pitch for
   the checking. The kept copy is still never shrunk on disk; this is only what
   the first paint scales it to.

   CLICKING ANYWHERE CLOSES IT — the backdrop, the picture, the caption — plus
   Escape and the Close chip. Only the head bar is exempt, so the title stays
   selectable and the chip is not a click that fires twice. A viewer opened to
   glance at something is closed by the first thing anyone tries. */
export default function ShotViewer({ shot, onClose }) {
  const [full, setFull] = useState(false)

  useEffect(() => {
    const onKey = (ev) => { if (ev.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return createPortal((
    <div className="shotmodal" role="dialog" aria-modal="true"
         aria-label={`Screenshot for ${shotTitle(shot)}`}
         onClick={onClose}>
      <div className="shotmodalbody">
        <div className="shotmodalhead" onClick={(ev) => ev.stopPropagation()}>
          <b>{shotTitle(shot)}</b>
          <span className="muted" style={{ marginLeft: 'auto' }}>
            {shot.image_w && shot.image_h ? `${shot.image_w}×${shot.image_h}` : ''}
          </span>
          <button className={`chip${full ? ' on' : ''}`}
                  title="Show it at the size it was stored, for reading numbers off"
                  onClick={() => setFull((v) => !v)}>
            {full ? 'Fit to screen' : 'Full size'}
          </button>
          <button className="chip" onClick={onClose}>Close</button>
        </div>
        <div className={`shotmodalscroll${full ? ' full' : ''}`}>
          <img src={api.parseshotImage(shot.id)}
               alt={`ACT screenshot for ${shotTitle(shot)}`} />
        </div>
        <p className="note">
          A re-encoded copy, not the file you dropped — kept so the numbers can
          be checked against the picture they came from.
        </p>
      </div>
    </div>
  ), document.body)
}

/* The same picture as a thumbnail: the one piece of evidence behind the
   columns arithmetic cannot check, so it travels with the parse rather than
   living on a detail page. Silent for a shot with no image kept. */
export function ShotThumb({ shot, onOpen, className = '' }) {
  if (!shot?.has_image) return null
  return (
    <button className={`shotthumb${className ? ` ${className}` : ''}`} onClick={onOpen}
            title="See the screenshot this was read from">
      <img src={api.parseshotImage(shot.id, true)} alt="" loading="lazy" />
    </button>
  )
}
