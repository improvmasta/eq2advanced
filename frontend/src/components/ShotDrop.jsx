import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api.js'

/* Drop an ACT screenshot here and get a parse back.

   PASTE is first-class, not a nicety: the screenshot people want to compare
   against is in Discord, and the way it comes out of Discord is right-click →
   Copy image. Making them save a file first would be a step invented by this
   form. Drag-and-drop and a plain file picker are here for the same reason —
   whichever one somebody already reaches for should work.

   Reading a shot takes SECONDS (it is an OCR pass over a table, not an
   upload), so the box both SAYS how long — a spinner names no duration — and
   MOVES while it waits. A still box with a sentence on it is what a finished
   box also looks like, and several seconds of that reads as a drop that did
   not take; the ring is the only part of this that says the work is still
   running. One at a time: two concurrent reads on one box would race for the
   same "which one finished" answer, and nobody drops two screenshots meaning
   to compare them with each other. */
/* `upload` is where the image goes; the default reads it as an ACT parse. The
   raid dashboard hands it a different destination (a note's attachment) —
   getting an image out of somebody's clipboard is the same problem either way,
   and it is the part worth having in one place. `busyLabel` follows, since OCR
   takes seconds and a plain attachment does not. */
export default function ShotDrop({
  onImported, compact, className = '', label,
  upload = api.importParseshot, busyLabel, accept = 'image/*',
}) {
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const fileRef = useRef()
  const hostRef = useRef()

  async function send(file) {
    if (!file || busy) return
    if (!file.type?.startsWith('image/')) {
      setErr('That needs to be an image — a screenshot of your ACT window.')
      return
    }
    setErr('')
    setBusy(true)
    try {
      onImported(await upload(file))
    } catch (e) {
      setErr(e.message || 'Could not read that screenshot.')
    } finally {
      setBusy(false)
    }
  }

  /* Paste anywhere while this box is on screen. Scoped to a paste that
     actually carries an IMAGE, so copying text on the page is untouched. */
  useEffect(() => {
    const onPaste = (ev) => {
      const item = [...(ev.clipboardData?.items || [])]
        .find((i) => i.type?.startsWith('image/'))
      if (!item) return
      ev.preventDefault()
      send(item.getAsFile())
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  })

  return (
    <div
      ref={hostRef}
      className={`shotdrop${drag ? ' over' : ''}${busy ? ' busy' : ''}`
        + `${compact ? ' compact' : ''}${className ? ` ${className}` : ''}`}
      onDragOver={(ev) => { ev.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(ev) => {
        ev.preventDefault()
        setDrag(false)
        send(ev.dataTransfer?.files?.[0])
      }}
      onClick={() => !busy && fileRef.current?.click()}
      role="button"
      tabIndex={0}
      aria-label="Import a parse from an ACT screenshot"
      onKeyDown={(ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); fileRef.current?.click() }
      }}
    >
      <input
        ref={fileRef}
        type="file"
        accept={accept}
        hidden
        onChange={(ev) => { send(ev.target.files?.[0]); ev.target.value = '' }}
      />
      {busy ? (
        <>
          <span className="spinner" role="status" aria-label="Reading the screenshot" />
          <p className="muted">{busyLabel || 'Reading the screenshot… this takes a few seconds.'}</p>
        </>
      ) : (
        <>
          {/* The + is what makes a bordered rectangle read as somewhere you
              PUT something. Decorative — the box's own words say what goes
              in it, and the whole box is the button. */}
          <span className="plus" aria-hidden="true">+</span>
          {label || (
            <p className="muted">
              <b>Drop an ACT screenshot</b> — or paste one, or click to browse.
            </p>
          )}
        </>
      )}
      {err && <p className="err">{err}</p>}
    </div>
  )
}
