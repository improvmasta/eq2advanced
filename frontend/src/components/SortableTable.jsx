import { Fragment, useEffect, useMemo, useRef, useState } from 'react'

/* Generic click-to-sort data table.
   columns: [{ key, label, align ('l' left, 'c' centred), headAlign (the header
              alone, when a wide column's left-hugged label reads as belonging
              to the column before it), render(row), format(value),
              sortValue(row), sortable (default true), fixed (never hidden or
              dragged — the name column) }]
   Numeric-first: default direction on a fresh column is descending (big
   numbers on top, the ACT way); clicking the active column flips it.
   checkable(row) adds a leading checkbox column (controlled via checkedKeys, a
   Set of row keys, + onCheck(key)); rows where checkable returns false get an
   empty cell.
   childrenOf(row) may return sub-rows rendered right under their parent
   (sorting only orders the top level); rowClass(row) adds a per-row class, and
   a column's cellClass(row) / cellStyle(row) add a per-cell class or style
   (rank coloring lives there), and cellTitle(row) is its tooltip — a tinted
   cell has to be able to answer "says who?", so the two travel together.
   wrapClass 'sticky' pins the header for long raid tables; `frozen` pins the
   header AND the first column, and syncScroll ties this table's sideways
   scroll to every other table sharing the same group name.
   groupBy: {key, of(row), label(row, rows)} draws a heading row whenever the
   group changes — only while the table is sorted by `key`, because a heading
   that repeats every third row is not a grouping. Pass an ARRAY of those to
   group differently per sort column (the raid list: nights under a date sort,
   zones under a zone sort); whichever def matches the active sort is the one
   that draws. While a group IS drawing, the column it groups on has its value
   in the heading over every row it owns, so a column's groupedRender(row) is
   what that column shows INSTEAD for those rows — whatever is left once the
   heading has said the shared part. Without one the column renders normally
   (a heading that only sometimes draws can't silently blank a cell).
   prefsKey turns on per-user column layout: drag a header to reorder, hide
   columns from the Columns menu, both remembered in localStorage under that
   key. Which columns a table SHOULD offer is still the caller's decision;
   this only remembers what you did with them. defaultHidden is the caller's
   starting layout and it is a BASELINE, not a first guess — see below. */

const PREFS_PREFIX = 'eq2adv:cols:'

/* Comparison surfaces render several tables under ONE prefsKey — the whole
   point is that the columns line up — so a layout change in any of them has
   to reach the others in the same frame, not on the next mount. localStorage
   only notifies other tabs; this covers this one. */
const prefsListeners = new Map()   // prefsKey -> Set<callback>

function loadPrefs(key) {
  if (!key || typeof localStorage === 'undefined') return {}
  try {
    return JSON.parse(localStorage.getItem(PREFS_PREFIX + key)) || {}
  } catch {
    return {}
  }
}

function savePrefs(key, prefs) {
  if (!key || typeof localStorage === 'undefined') return
  try {
    /* Only Reset (write({})) clears the entry — an empty `hidden` beside a
       non-empty `shown` is a real layout, not an untouched one. */
    if (!Object.keys(prefs).length) {
      localStorage.removeItem(PREFS_PREFIX + key)
    } else {
      localStorage.setItem(PREFS_PREFIX + key, JSON.stringify(prefs))
    }
  } catch { /* private mode / full quota — the layout just doesn't persist */ }
}

/* Keep a scroll box's bottom edge on screen: its height is whatever is left
   between where the box starts and the foot of the window. That is the only
   way the horizontal scrollbar stays reachable — a fixed `100vh - Npx` cap
   cannot know how much page sits above the box, and a 60-ability necromancer
   parse then runs its bar clean off the bottom of the monitor.

   Measured rather than sticky-positioned: `position: sticky` is clipped here
   by an ancestor, which is why the floating bar only ever appeared at the
   very bottom of the page. Clamping the top at 0 keeps the box from growing
   as you scroll past it, which would extend the page under its own feet. */
function useFitViewport(enabled, ref) {
  useEffect(() => {
    const el = ref.current
    if (!enabled || !el) return
    let raf = 0
    const apply = () => {
      raf = 0
      const top = el.getBoundingClientRect().top
      const h = Math.max(220, window.innerHeight - Math.max(top, 0) - 24)
      const next = `${Math.round(h)}px`
      if (el.style.maxHeight !== next) el.style.maxHeight = next
    }
    const schedule = () => { if (!raf) raf = requestAnimationFrame(apply) }
    apply()
    window.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    // the head wrapping to two lines moves the box down; so does a column
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(schedule) : null
    if (ro && el.parentElement) ro.observe(el.parentElement)
    return () => {
      window.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
      ro?.disconnect()
      if (raf) cancelAnimationFrame(raf)
    }
  }, [enabled])
}

/* A frozen table: the header row and the first column hold still while the
   rest scrolls under them. The pinning itself is CSS (`.tablewrap.frozen`);
   what JavaScript has to supply is the three things a stylesheet cannot
   measure.

   `--fzleft` — where the second frozen cell starts. A checkable table pins
   the checkbox column AND the name beside it, and the checkbox column's real
   width is whatever the browser gave it, not what the rule asked for.

   `xscrolled` — whether the table is actually scrolled sideways. The divider
   down the frozen column only means something once something has moved
   underneath it; on a table that fits, it is a line drawn for no reason.

   `overflowing` — whether the table fits its box at all, which is what gates
   shortening long ability names (see `.abname`). Names are shortened ONLY
   when the table cannot fit, and only the ones long enough to need it; the
   rest are untouched, however narrow the column gets.

   Un-shortening is deliberately asymmetric. Measuring an already-clamped
   table would ask "does it fit?" of a table that fits BECAUSE it is clamped,
   and the answer starts an oscillation — clamp, fits, unclamp, doesn't fit,
   clamp. So the width it wanted before the clamp is remembered, and the names
   only come back when there is room for that.

   Both flags are STATE and go through the className, not classList: React
   rewrites that attribute whole whenever the caller's wrapClass changes (the
   raid table gains `sticky` at fifteen rows), and a class this hook had poked
   into the DOM would go with it. */
function useFrozen(enabled, ref) {
  const [state, setState] = useState({ over: false, xscrolled: false })
  /* Read by the measurement, which is not a render: a state updater has to be
     pure, and deciding the clamp inside one would re-decide it every time
     React replayed the update. `natural` is what the table measured before it
     was clamped — see the oscillation above. */
  const now = useRef(state)
  now.current = state
  useEffect(() => {
    const el = ref.current
    if (!enabled || !el) return
    let raf = 0
    let natural = 0
    const measure = () => {
      raf = 0
      const head = el.querySelector('thead th')
      if (head) el.style.setProperty('--fzleft', `${head.offsetWidth}px`)
      let over = now.current.over
      if (!over && el.scrollWidth > el.clientWidth + 1) {
        natural = el.scrollWidth
        over = true
      } else if (over && natural && el.clientWidth >= natural) {
        natural = 0
        over = false
      }
      const xscrolled = el.scrollLeft > 0
      setState((s) => (over === s.over && xscrolled === s.xscrolled
        ? s : { over, xscrolled }))
    }
    const schedule = () => { if (!raf) raf = requestAnimationFrame(measure) }
    const onScroll = () => setState((s) => (
      (el.scrollLeft > 0) === s.xscrolled ? s : { ...s, xscrolled: el.scrollLeft > 0 }))
    measure()
    el.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', schedule)
    /* The table resizes whenever its rows, its columns or its clamp change,
       which is every reason this needs measuring again — so watch the element
       rather than re-running the effect on each render. */
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(schedule) : null
    if (ro) {
      ro.observe(el)
      const table = el.querySelector('table')
      if (table) ro.observe(table)
    }
    return () => {
      el.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', schedule)
      ro?.disconnect()
      if (raf) cancelAnimationFrame(raf)
    }
  }, [enabled])
  if (!enabled) return ''
  return ` frozen${state.over ? ' overflowing' : ''}${state.xscrolled ? ' xscrolled' : ''}`
}

/* Two parses side by side are read ACROSS, so moving one table's scrollbar
   moves the other's: the same columns stay under each other and the pair can
   be compared a stat at a time instead of a table at a time. Module state,
   like the hover channel in BreakdownTable — the tables are siblings in a
   comparison, not children of one component. */
const syncGroups = new Map()   // group name -> Set<scroll element>
let syncing = false

function useScrollSync(group, ref) {
  useEffect(() => {
    const el = ref.current
    if (!group || !el) return
    let set = syncGroups.get(group)
    if (!set) syncGroups.set(group, set = new Set())
    // a parse added to a comparison lands where the others already are
    for (const peer of set) { el.scrollLeft = peer.scrollLeft; break }
    set.add(el)
    const onScroll = () => {
      // the scrolls we are about to cause must not scroll us back
      if (syncing) return
      syncing = true
      for (const peer of set) {
        if (peer !== el && peer.scrollLeft !== el.scrollLeft) peer.scrollLeft = el.scrollLeft
      }
      requestAnimationFrame(() => { syncing = false })
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      el.removeEventListener('scroll', onScroll)
      set.delete(el)
      if (!set.size) syncGroups.delete(group)
    }
  }, [group])
}

export default function SortableTable({
  columns, rows, defaultSort, rowKey, onRowClick, selectedKey, className = '',
  checkable, checkedKeys, onCheck, childrenOf, rowClass, wrapClass = '', groupBy,
  prefsKey, topRows, defaultHidden, onRowHover, fitViewport, tools, fold,
  frozen, syncScroll,
}) {
  const wrapRef = useRef(null)
  useFitViewport(fitViewport, wrapRef)
  const frozenClass = useFrozen(frozen, wrapRef)
  useScrollSync(syncScroll, wrapRef)
  const [sort, setSort] = useState(defaultSort || null) // {key, dir: 'asc'|'desc'}
  const [unfolded, setUnfolded] = useState(false)
  const [prefs, setPrefs] = useState(() => loadPrefs(prefsKey))
  const [menuOpen, setMenuOpen] = useState(false)
  const [drag, setDrag] = useState(null)          // {key, over}
  const toolsRef = useRef(null)

  // each tab keeps its own layout, so switching tabs loads that tab's
  useEffect(() => { setPrefs(loadPrefs(prefsKey)); setMenuOpen(false) }, [prefsKey])

  // sibling tables on the same prefsKey follow this one's layout changes
  useEffect(() => {
    if (!prefsKey) return
    const onChange = () => setPrefs(loadPrefs(prefsKey))
    let set = prefsListeners.get(prefsKey)
    if (!set) prefsListeners.set(prefsKey, set = new Set())
    set.add(onChange)
    return () => { set.delete(onChange); if (!set.size) prefsListeners.delete(prefsKey) }
  }, [prefsKey])

  useEffect(() => {
    if (!menuOpen) return
    const away = (e) => { if (!toolsRef.current?.contains(e.target)) setMenuOpen(false) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [menuOpen])

  const write = (next) => {
    setPrefs(next)
    savePrefs(prefsKey, next)
    for (const fn of prefsListeners.get(prefsKey) || []) fn()
  }

  /* Stored order is a list of keys, not indexes: a column the caller adds
     later (or one that only exists on some tabs) has no entry and keeps its
     natural place rather than jumping to the front. */
  const movable = useMemo(() => {
    const rest = columns.filter((c) => !c.fixed)
    const at = new Map((prefs.order || []).map((k, i) => [k, i]))
    return rest
      .map((c, i) => ({ c, rank: at.has(c.key) ? at.get(c.key) : 1000 + i }))
      .sort((a, b) => a.rank - b.rank)
      .map((x) => x.c)
  }, [columns, prefs.order])

  /* Two lists, not one. `hidden` is what the reader turned OFF and `shown` is
     what they turned back ON, and defaultHidden sits underneath both: a column
     is hidden if the caller hides it by default or the reader hid it, unless
     the reader explicitly asked for it.

     A single stored list can't do that. It used to REPLACE defaultHidden
     wholesale, so the first time anyone touched the Columns menu the table's
     whole starting layout evaporated — and a default-hidden column added
     later (DPS on Healing) turned itself on for everybody who had ever moved
     a column, which is the opposite of default-hidden. */
  const hidden = useMemo(() => {
    const out = new Set(defaultHidden || [])
    for (const k of prefs.hidden || []) out.add(k)
    for (const k of prefs.shown || []) out.delete(k)
    return out
  }, [prefs.hidden, prefs.shown, defaultHidden])
  /* `visible`, not `shown` — `prefs.shown` right above is the stored list of
     keys the reader turned back on, and one name for both would read as the
     same thing twice. */
  const visible = useMemo(
    () => [...columns.filter((c) => c.fixed), ...movable.filter((c) => !hidden.has(c.key))],
    [columns, movable, hidden])
  const cols = prefsKey ? visible : columns

  const toggleHidden = (key) => {
    const off = new Set(prefs.hidden || [])
    const on = new Set(prefs.shown || [])
    if (hidden.has(key)) { off.delete(key); on.add(key) } else { on.delete(key); off.add(key) }
    write({ ...prefs, hidden: [...off], shown: [...on] })
  }
  const drop = (targetKey) => {
    setDrag(null)
    if (!drag || drag.key === targetKey) return
    const keys = movable.map((c) => c.key).filter((k) => k !== drag.key)
    const at = keys.indexOf(targetKey)
    keys.splice(at < 0 ? keys.length : at, 0, drag.key)
    write({ ...prefs, order: keys })
  }

  /* One table instance serves several tabs, and columns come and go (the
     Healed breakdown). A sort on a column that is no longer rendered would
     leave the table silently unsorted, so fall back to this column set's own
     default instead of to raw API order. */
  const has = (key) => cols.some((c) => c.key === key)
  const active = sort && has(sort.key) ? sort
    : (defaultSort && has(defaultSort.key) ? defaultSort : null)

  const sorted = useMemo(() => {
    if (!active) return rows
    const col = cols.find((c) => c.key === active.key)
    if (!col) return rows
    const val = col.sortValue || ((r) => r[col.key])
    const mul = active.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const va = val(a), vb = val(b)
      if (va == null && vb == null) return 0
      if (va == null) return 1               // nulls sink regardless of direction
      if (vb == null) return -1
      if (typeof va === 'string') return mul * va.localeCompare(vb)
      return mul * (va - vb)
    })
  }, [rows, active, cols])

  /* `fold` folds the tail away behind one clickable line. It cuts AFTER the
     sort, never before: a table folded on the caller's side would keep showing
     the top twelve by the DEFAULT column no matter what you then sorted by,
     which is a table quietly lying about what it holds. */
  const tailHidden = fold && !unfolded ? Math.max(0, sorted.length - fold) : 0
  const body = tailHidden ? sorted.slice(0, fold) : sorted

  /* topRows are pinned above the sorted body (the ACT "All" line): sorting
     never moves them and grouping never claims them. */
  const expanded = useMemo(
    () => [...(topRows || []), ...body.flatMap((r) => [r, ...(childrenOf?.(r) || [])])],
    [body, childrenOf, topRows])

  /* A heading only means something while the table is ordered by the thing it
     groups on; sorted by DPS, the same date would head half the rows. */
  const groupDef = (Array.isArray(groupBy) ? groupBy : groupBy ? [groupBy] : [])
    .find((g) => g.key === active?.key)
  const grouped = !!groupDef
  const groupHead = (r, prev) => {
    if (r.__sub) return null
    const g = groupDef.of(r)
    if (prev != null && !prev.__sub && groupDef.of(prev) === g) return null
    return groupDef.label(r)
  }

  /* The grouped column is the one column in the row whose value is already
     printed above it, so it is the one column that must not print it again.
     It keeps its header — that header is how you re-sort the table — and the
     cells under it fall back to whatever the caller says is left over.

     Only for rows a heading actually speaks for, which is why `pinned` is here:
     topRows sit ABOVE the first heading and grouping never claims them, so
     folding one would take a value away and put nothing over it. */
  const folded = (c) => grouped && groupDef.key === c.key && !!c.groupedRender
  const topCount = topRows?.length || 0
  const cellOf = (c, r, pinned) => (
    folded(c) && !pinned && !r.__sub ? c.groupedRender(r)
      : c.render ? c.render(r) : c.format ? c.format(r[c.key]) : r[c.key])

  const click = (col) => {
    if (col.sortable === false) return
    setSort({
      key: col.key,
      dir: active?.key === col.key ? (active.dir === 'desc' ? 'asc' : 'desc') : 'desc',
    })
  }

  const hiddenCount = movable.length - (cols.length - columns.filter((c) => c.fixed).length)

  return (
    <>
      {(prefsKey || tools) && (
        /* The table's own line: whatever the caller puts in front of the table
           (filters, switches) shares it with Columns rather than spending a
           row of its own above it. */
        <div className="tabletools" ref={toolsRef}>
          {tools}
          {prefsKey && (
          <>
          <button
            className={`chip ${menuOpen ? 'on' : ''}`}
            onClick={() => setMenuOpen((v) => !v)}
            title="Show, hide and reorder columns — drag a header to move it"
            aria-expanded={menuOpen}
          >
            Columns{hiddenCount > 0 ? ` (${hiddenCount} hidden)` : ''}
          </button>
          {menuOpen && (
            <div className="colmenu" role="menu">
              <div className="colmenu-head">
                <span>Drag a header to reorder</span>
                <button
                  className="chip"
                  onClick={() => write({})}
                  disabled={!Object.keys(prefs).length}
                  title="Put this tab's columns back the way they ship — order and visibility"
                >Reset to defaults</button>
              </div>
              {movable.map((c) => (
                <label key={c.key} className="colmenu-row">
                  <input
                    type="checkbox"
                    checked={!hidden.has(c.key)}
                    onChange={() => toggleHidden(c.key)}
                  />
                  <span>{c.menuLabel || (typeof c.label === 'string' ? c.label : c.key)}</span>
                </label>
              ))}
            </div>
          )}
          </>
          )}
        </div>
      )}
      <div className={`tablewrap ${wrapClass}${frozenClass}`} ref={wrapRef}>
      <table className={`data ${className}`}>
        <thead>
          <tr>
            {checkable && <th className="checkcol" aria-label="Select" />}
            {cols.map((c) => (
              <th
                key={c.key}
                className={[
                  (c.headAlign || c.align) === 'l' ? 'l'
                    : (c.headAlign || c.align) === 'c' ? 'c' : '',
                  c.sortable === false ? '' : 'sortable',
                  folded(c) ? 'folded' : '',
                  prefsKey && !c.fixed ? 'draggable' : '',
                  drag?.over === c.key && drag.key !== c.key ? 'dragover' : '',
                  drag?.key === c.key ? 'dragging' : '',
                ].filter(Boolean).join(' ')}
                onClick={() => click(c)}
                aria-sort={active?.key === c.key ? (active.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                draggable={!!prefsKey && !c.fixed}
                onDragStart={prefsKey && !c.fixed
                  ? (e) => { e.dataTransfer.effectAllowed = 'move'; setDrag({ key: c.key }) } : undefined}
                onDragOver={prefsKey && !c.fixed && drag
                  ? (e) => { e.preventDefault(); if (drag.over !== c.key) setDrag({ ...drag, over: c.key }) }
                  : undefined}
                onDrop={prefsKey && !c.fixed ? (e) => { e.preventDefault(); drop(c.key) } : undefined}
                onDragEnd={() => setDrag(null)}
              >
                {c.label}
                {active?.key === c.key && <span className="sortmark">{active.dir === 'asc' ? '▲' : '▼'}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {expanded.map((r, i) => {
            const k = rowKey(r)
            const head = grouped ? groupHead(r, expanded[i - 1]) : null
            return (
              <Fragment key={k}>
                {head !== null && (
                  <tr className="grouphead">
                    <th colSpan={cols.length + (checkable ? 1 : 0)} scope="colgroup">
                      {head}
                    </th>
                  </tr>
                )}
                <tr
                  className={`${onRowClick ? 'clickable' : ''} ${k === selectedKey ? 'selected' : ''} ${rowClass?.(r) || ''}`}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                  onMouseEnter={onRowHover ? () => onRowHover(r) : undefined}
                  onMouseLeave={onRowHover ? () => onRowHover(null) : undefined}
                >
                  {checkable && (
                    <td className="checkcol" onClick={(e) => e.stopPropagation()}>
                      {checkable(r) && (
                        <input
                          type="checkbox"
                          checked={checkedKeys?.has(k) || false}
                          onChange={() => onCheck?.(k)}
                          aria-label={`Select ${k}`}
                        />
                      )}
                    </td>
                  )}
                  {cols.map((c) => (
                    <td
                      key={c.key}
                      className={`${c.align === 'l' ? 'l ' : c.align === 'c' ? 'c ' : ''}${folded(c) ? 'folded ' : ''}${(!r.__sub && c.cellClass?.(r)) || ''}`}
                      style={(!r.__sub && c.cellStyle?.(r)) || undefined}
                      title={(!r.__sub && c.cellTitle?.(r)) || undefined}
                    >
                      {cellOf(c, r, i < topCount)}
                    </td>
                  ))}
                </tr>
              </Fragment>
            )
          })}
        </tbody>
      </table>
      {!!fold && (tailHidden > 0 || unfolded) && (
        <button className="metermore" onClick={() => setUnfolded(!unfolded)}>
          {unfolded ? `⋯ show the top ${fold}` : `⋯ ${tailHidden} more`}
        </button>
      )}
      </div>
    </>
  )
}
