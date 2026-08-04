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
   (rank coloring lives there).
   wrapClass 'sticky' pins the header for long raid tables.
   groupBy: {key, of(row), label(row, rows)} draws a heading row whenever the
   group changes — only while the table is sorted by `key`, because a heading
   that repeats every third row is not a grouping. Pass an ARRAY of those to
   group differently per sort column (the raid list: nights under a date sort,
   zones under a zone sort); whichever def matches the active sort is the one
   that draws.
   prefsKey turns on per-user column layout: drag a header to reorder, hide
   columns from the Columns menu, both remembered in localStorage under that
   key. Which columns a table SHOULD offer is still the caller's decision;
   this only remembers what you did with them. */

const PREFS_PREFIX = 'eq2adv:cols:'

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
    if (!prefs.order?.length && !prefs.hidden?.length) {
      localStorage.removeItem(PREFS_PREFIX + key)
    } else {
      localStorage.setItem(PREFS_PREFIX + key, JSON.stringify(prefs))
    }
  } catch { /* private mode / full quota — the layout just doesn't persist */ }
}

export default function SortableTable({
  columns, rows, defaultSort, rowKey, onRowClick, selectedKey, className = '',
  checkable, checkedKeys, onCheck, childrenOf, rowClass, wrapClass = '', groupBy,
  prefsKey,
}) {
  const [sort, setSort] = useState(defaultSort || null) // {key, dir: 'asc'|'desc'}
  const [prefs, setPrefs] = useState(() => loadPrefs(prefsKey))
  const [menuOpen, setMenuOpen] = useState(false)
  const [drag, setDrag] = useState(null)          // {key, over}
  const toolsRef = useRef(null)

  // each tab keeps its own layout, so switching tabs loads that tab's
  useEffect(() => { setPrefs(loadPrefs(prefsKey)); setMenuOpen(false) }, [prefsKey])

  useEffect(() => {
    if (!menuOpen) return
    const away = (e) => { if (!toolsRef.current?.contains(e.target)) setMenuOpen(false) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [menuOpen])

  const write = (next) => { setPrefs(next); savePrefs(prefsKey, next) }

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

  const hidden = useMemo(() => new Set(prefs.hidden || []), [prefs.hidden])
  const shown = useMemo(
    () => [...columns.filter((c) => c.fixed), ...movable.filter((c) => !hidden.has(c.key))],
    [columns, movable, hidden])
  const cols = prefsKey ? shown : columns

  const toggleHidden = (key) => {
    const next = new Set(hidden)
    if (next.has(key)) next.delete(key); else next.add(key)
    write({ ...prefs, hidden: [...next] })
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

  const expanded = useMemo(
    () => sorted.flatMap((r) => [r, ...(childrenOf?.(r) || [])]), [sorted, childrenOf])

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
      {prefsKey && (
        <div className="tabletools" ref={toolsRef}>
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
                  disabled={!prefs.order?.length && !prefs.hidden?.length}
                >Reset</button>
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
        </div>
      )}
      <div className={`tablewrap ${wrapClass}`}>
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
                      className={`${c.align === 'l' ? 'l ' : c.align === 'c' ? 'c ' : ''}${(!r.__sub && c.cellClass?.(r)) || ''}`}
                      style={(!r.__sub && c.cellStyle?.(r)) || undefined}
                    >
                      {c.render ? c.render(r) : c.format ? c.format(r[c.key]) : r[c.key]}
                    </td>
                  ))}
                </tr>
              </Fragment>
            )
          })}
        </tbody>
      </table>
      </div>
    </>
  )
}
