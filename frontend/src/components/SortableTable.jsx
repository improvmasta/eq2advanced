import { useMemo, useState } from 'react'

/* Generic click-to-sort data table.
   columns: [{ key, label, align ('l' for left), render(row), format(value),
              sortValue(row), sortable (default true) }]
   Numeric-first: default direction on a fresh column is descending (big
   numbers on top, the ACT way); clicking the active column flips it.
   checkable(row) adds a leading checkbox column (controlled via checkedKeys, a
   Set of row keys, + onCheck(key)); rows where checkable returns false get an
   empty cell.
   childrenOf(row) may return sub-rows rendered right under their parent
   (sorting only orders the top level); rowClass(row) adds a per-row class, and
   a column's cellClass(row) adds a per-cell one (rank coloring lives there).
   wrapClass 'sticky' pins the header for long raid tables. */
export default function SortableTable({
  columns, rows, defaultSort, rowKey, onRowClick, selectedKey, className = '',
  checkable, checkedKeys, onCheck, childrenOf, rowClass, wrapClass = '',
}) {
  const [sort, setSort] = useState(defaultSort || null) // {key, dir: 'asc'|'desc'}

  /* One table instance serves several tabs, and columns come and go (the
     Healed breakdown). A sort on a column that is no longer rendered would
     leave the table silently unsorted, so fall back to this column set's own
     default instead of to raw API order. */
  const has = (key) => columns.some((c) => c.key === key)
  const active = sort && has(sort.key) ? sort
    : (defaultSort && has(defaultSort.key) ? defaultSort : null)

  const sorted = useMemo(() => {
    if (!active) return rows
    const col = columns.find((c) => c.key === active.key)
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
  }, [rows, active, columns])

  const click = (col) => {
    if (col.sortable === false) return
    setSort({
      key: col.key,
      dir: active?.key === col.key ? (active.dir === 'desc' ? 'asc' : 'desc') : 'desc',
    })
  }

  return (
    <div className={`tablewrap ${wrapClass}`}>
      <table className={`data ${className}`}>
        <thead>
          <tr>
            {checkable && <th className="checkcol" aria-label="Select" />}
            {columns.map((c) => (
              <th
                key={c.key}
                className={`${c.align === 'l' ? 'l ' : ''}${c.sortable === false ? '' : 'sortable'}`}
                onClick={() => click(c)}
                aria-sort={active?.key === c.key ? (active.dir === 'asc' ? 'ascending' : 'descending') : undefined}
              >
                {c.label}
                {active?.key === c.key && <span className="sortmark">{active.dir === 'asc' ? '▲' : '▼'}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.flatMap((r) => [r, ...(childrenOf?.(r) || [])]).map((r) => {
            const k = rowKey(r)
            return (
              <tr
                key={k}
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
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={`${c.align === 'l' ? 'l ' : ''}${(!r.__sub && c.cellClass?.(r)) || ''}`}
                  >
                    {c.render ? c.render(r) : c.format ? c.format(r[c.key]) : r[c.key]}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
