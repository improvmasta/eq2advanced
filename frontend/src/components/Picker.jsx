import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

/* A dropdown that belongs to this page instead of to the operating system.

   A native `<select>` costs three things the Compare page cannot spend:

   - its popup is OS chrome. `color-scheme` gets the list dark and that is the
     end of what CSS may say about it — no parchment, no gold, no hairlines —
     so the one surface the reader opens most often is the one surface that
     looks like nothing else here.
   - an `<option>` is a string. A player is a name AND a class, and a class is
     a word next to a colored dot, which an option element cannot hold.
   - a closed select is as wide as its widest option. A fight label is a time,
     a mob name and sometimes "(wipe)", so the subject picker beside it got
     shoved to the far side of a column that is only 380px wide to begin with.

   So: a quiet button that opens a themed list over the thing it changes — the
   same bones as the table's Columns menu (`.colmenu`) and the source filter,
   because it is the same gesture. The BUTTON is sized by its row and truncates;
   the PANEL is sized by its content and may be wider. That split is the whole
   answer to a long option in a narrow column.

   `options` are `{ value, label, hint, icon, group, title, key, menuLabel }`:
   `group` starts a section (an optgroup), `hint` is the muted half of a row —
   a class, a pull count — `icon` is a node in front of it, and `menuLabel`
   names the row in the LIST where that differs from what the button should
   read. Past `filterFrom` rows the panel grows a filter box, because a 24-name
   roster is a list you search rather than one you scroll.

   Two rows may share a VALUE — a named fight is also in the every-fight
   section, and picking either means the same thing — so a row is identified by
   `key` where it has one. Highlighting the first match is right for that case:
   the sections read top-down, and the shortlist is where you were looking.

   THE OPEN PANEL IS RENDERED INTO `document.body`, positioned from the
   button's rect. Not a preference: every card on this site carries
   `backdrop-filter`, which makes that card a stacking context AND a containing
   block for `position: fixed`, so a menu written inside one is sealed into it.
   The search band is a card and the parses are cards after it in the DOM, so a
   facet menu dropped down over them was painted UNDERNEATH them however high
   its z-index went — the same trap that put the screenshot viewer under the
   next column (see ShotViewer.jsx). Leaving the card is the fix; a bigger
   z-index cannot be one. The cost is that the panel no longer moves with the
   page, so it re-measures on scroll and resize. */
export default function Picker({
  value, onChange, options, label, placeholder = 'Choose…', disabled = false,
  className = '', filterFrom = 10, filterHint = 'Filter…',
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const [at, setAt] = useState(null)
  const box = useRef(null)
  const menu = useRef(null)

  const current = options.find((o) => o.value === value) || null
  const ql = q.trim().toLowerCase()
  const shown = useMemo(() => (ql
    ? options.filter((o) => `${o.label} ${o.hint || ''}`.toLowerCase().includes(ql))
    : options), [options, ql])

  /* Where the panel goes, in viewport coordinates. It drops DOWN unless the
     button is low enough that the list would be cut off, and it never runs off
     the right edge — a facet at the end of the search band is a real case. */
  useLayoutEffect(() => {
    if (!open) { setAt(null); return undefined }
    const place = () => {
      const r = box.current?.getBoundingClientRect()
      if (!r) return
      const below = window.innerHeight - r.bottom
      setAt({
        left: r.left,
        width: r.width,
        room: Math.max(window.innerWidth - r.left - 12, 200),
        ...(below < 240 && r.top > below
          ? { bottom: window.innerHeight - r.top + 4, cap: r.top - 16 }
          : { top: r.bottom + 4, cap: below - 16 }),
      })
    }
    place()
    window.addEventListener('resize', place)
    // capture: the page's own scroll AND any scrolling box the picker sits in
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open])

  // outside click and Escape close it — the two ways anybody abandons a menu.
  // The panel is not inside `box` any more, so both count as inside.
  useEffect(() => {
    if (!open) return undefined
    const away = (ev) => {
      if (box.current?.contains(ev.target) || menu.current?.contains(ev.target)) return
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

  // opening starts on what is already chosen, not at the top: the list is
  // usually opened to move ONE step from where you are
  useEffect(() => {
    if (!open) { setQ(''); return }
    setActive(Math.max(0, options.findIndex((o) => o.value === value)))
  }, [open])

  const pick = (v) => { onChange(v); setOpen(false) }
  const move = (d) => setActive((i) => {
    if (!shown.length) return 0
    return (i + d + shown.length) % shown.length
  })
  const onKey = (ev) => {
    // Home/End belong to the CARET while the filter box has focus — a menu
    // does not get to take a text field's own keys away from it
    const typing = ev.target.tagName === 'INPUT'
    if (ev.key === 'ArrowDown') { ev.preventDefault(); open ? move(1) : setOpen(true) }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); open ? move(-1) : setOpen(true) }
    else if (ev.key === 'Home' && !typing) { ev.preventDefault(); setActive(0) }
    else if (ev.key === 'End' && !typing) { ev.preventDefault(); setActive(shown.length - 1) }
    else if (ev.key === 'Enter' && open) {
      ev.preventDefault()
      if (shown[active]) pick(shown[active].value)
    } else if (ev.key === 'Tab') setOpen(false)
  }

  let group = null
  return (
    <span className={`picker${open ? ' open' : ''}${className ? ` ${className}` : ''}`}
          ref={box}>
      <button
        type="button"
        className="pickerbtn"
        disabled={disabled}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={current ? `${current.label}${current.hint ? ` — ${current.hint}` : ''}` : placeholder}
        onClick={() => !disabled && setOpen((v) => !v)}
        onKeyDown={onKey}
      >
        {current?.icon}
        <span className="pv">{current ? current.label : placeholder}</span>
        {current?.hint && <span className="ph">{current.hint}</span>}
        <span className="caret" aria-hidden="true">▾</span>
      </button>
      {open && at && createPortal((
        <div
          className="pickermenu"
          role="listbox"
          aria-label={label}
          ref={menu}
          style={{
            left: at.left,
            top: at.top,
            bottom: at.bottom,
            minWidth: at.width,
            maxWidth: Math.min(340, at.room),
            '--picker-cap': `${Math.max(140, at.cap)}px`,
          }}
        >
          {options.length > filterFrom && (
            <input
              className="pickerfilter"
              type="search"
              autoFocus
              value={q}
              placeholder={filterHint}
              aria-label={`${label} — filter`}
              onChange={(ev) => { setQ(ev.target.value); setActive(0) }}
              onKeyDown={onKey}
            />
          )}
          <div className="pickerlist">
            {!shown.length && <p className="muted pickerempty">Nothing matches.</p>}
            {shown.map((o, i) => {
              const head = o.group && o.group !== group ? o.group : null
              group = o.group || null
              return (
                <div key={o.key ?? o.value} className="pickerslot" role="presentation">
                  {head && <div className="pickergroup">{head}</div>}
                  <div
                    role="option"
                    aria-selected={o.value === value}
                    className={`pickeropt${o.value === value ? ' on' : ''}`
                      + `${i === active ? ' active' : ''}`}
                    title={o.title || undefined}
                    ref={i === active ? (el) => el?.scrollIntoView({ block: 'nearest' }) : undefined}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => pick(o.value)}
                  >
                    {o.icon}
                    {/* A facet's off-row is named for the facet on the BUTTON
                        ("Zone", so a control reads its own name when it is
                        doing nothing) and named for what it does in the LIST
                        ("Any zone", which is what you are choosing). */}
                    <span className="pv">{o.menuLabel ?? o.label}</span>
                    {o.hint && <span className="ph">{o.hint}</span>}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ), document.body)}
    </span>
  )
}
