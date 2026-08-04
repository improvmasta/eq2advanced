import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

/* WHERE a raid came from, as one menu. Sections, because the three kinds of
   source are not interchangeable: characters are you, groups are other people,
   published is everyone. Ticks are OR'd and nothing ticked means everything
   you can see — one meaning per row, so no row can quietly change what
   another one does.

   Same bones as the table's Columns menu (`.colmenu`), because it is the same
   gesture: a quiet button that opens a list of ticks over the thing it filters.

   `sources` is a Set of keys — `char:<id>`, `group:<id>`, `public`. Home owns
   it and does the filtering; this only decides what you can tick. */
export default function SourceFilter({ chars, groups, hasPublic, sources, onToggle, onClear }) {
  const [open, setOpen] = useState(false)
  const box = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const away = (e) => { if (!box.current?.contains(e.target)) setOpen(false) }
    const esc = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  /* The button says what you'd otherwise have to open the menu to find out.
     Past two names it counts instead — "Bobby, Vestigial +2" is a label; the
     full list is a paragraph. */
  const names = [
    ...chars.filter((c) => sources.has(`char:${c.id}`)).map((c) => c.name),
    ...groups.filter((g) => sources.has(`group:${g.id}`)).map((g) => g.name),
    ...(sources.has('public') ? ['Public'] : []),
  ]
  const label = names.length === 0 ? 'Everything'
    : names.length <= 2 ? names.join(', ')
      : `${names.slice(0, 2).join(', ')} +${names.length - 2}`

  const row = (key, text, extra) => (
    <label key={key} className="colmenu-row">
      <input type="checkbox" checked={sources.has(key)} onChange={() => onToggle(key)} />
      <span>{text}</span>
      {extra}
    </label>
  )

  return (
    <span className="srcfilter" ref={box}>
      <button className={`chip ${sources.size ? 'on' : ''}`}
              aria-expanded={open}
              title="Whose raids to list"
              onClick={() => setOpen((v) => !v)}>
        Show: {label} <span className="caret">▾</span>
      </button>
      {open && (
        <div className="colmenu srcmenu" role="menu">
          <div className="colmenu-head">
            <span>Show raids from</span>
            <button className="chip" disabled={!sources.size} onClick={onClear}>
              Everything
            </button>
          </div>

          {chars.length > 0 && (
            <>
              <div className="srcsection">Your characters</div>
              {chars.map((c) => row(`char:${c.id}`, c.name))}
            </>
          )}

          {groups.length > 0 && (
            <>
              <div className="srcsection">
                Groups
                {/* the way to the Sharing page, where groups are actually run —
                    beside the header it belongs to rather than in the toolbar */}
                <Link className="gearlink" to="/groups" title="Manage groups and sharing"
                      aria-label="Manage groups">⚙</Link>
              </div>
              {groups.map((g) => row(`group:${g.id}`, g.name))}
            </>
          )}

          {hasPublic && (
            <>
              <div className="srcsection">Public</div>
              {row('public', 'Published raids')}
            </>
          )}
        </div>
      )}
    </span>
  )
}
