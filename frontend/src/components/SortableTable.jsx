import { useMemo, useState } from 'react'

/* Generic click-to-sort data table.
   columns: [{ key, label, align ('l' for left), render(row), format(value),
              sortValue(row), sortable (default true) }]
   Numeric-first: default direction on a fresh column is descending (big
   numbers on top, the ACT way); clicking the active column flips it.
   checkable(row) adds a leading checkbox column (controlled via checkedKeys, a
   Set of row keys, + onCheck(key)); rows where checkable returns false get an
   empty cell. */
export default function SortableTable({
  columns, rows, defaultSort, rowKey, onRowClick, selectedKey, className = '',
  checkable, checkedKeys, onCheck,
}) {
  const [sort, setSort] = useState(defaultSort || null) // {key, dir: 'asc'|'desc'}

  const sorted = useMemo(() => {
    if (!sort) return rows
    const col = columns.find((c) => c.key === sort.key)
    if (!col) return rows
    const val = col.sortValue || ((r) => r[col.key])
    const mul = sort.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const va = val(a), vb = val(b)
      if (va == null && vb == null) return 0
      if (va == null) return 1               // nulls sink regardless of direction
      if (vb == null) return -1
      if (typeof va === 'string') return mul * va.localeCompare(vb)
      return mul * (va - vb)
    })
  }, [rows, sort, columns])

  const click = (col) => {
    if (col.sortable === false) return
    setSort((s) => ({
      key: col.key,
      dir: s?.key === col.key ? (s.dir === 'desc' ? 'asc' : 'desc') : 'desc',
    }))
  }

  return (
    <div className="tablewrap">
      <table className={`data ${className}`}>
        <thead>
          <tr>
            {checkable && <th className="checkcol" aria-label="Compare" />}
            {columns.map((c) => (
              <th
                key={c.key}
                className={`${c.align === 'l' ? 'l ' : ''}${c.sortable === false ? '' : 'sortable'}`}
                onClick={() => click(c)}
                aria-sort={sort?.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
              >
                {c.label}
                {sort?.key === c.key && <span className="sortmark">{sort.dir === 'asc' ? '▲' : '▼'}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const k = rowKey(r)
            return (
              <tr
                key={k}
                className={`${onRowClick ? 'clickable' : ''} ${k === selectedKey ? 'selected' : ''}`}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
              >
                {checkable && (
                  <td className="checkcol" onClick={(e) => e.stopPropagation()}>
                    {checkable(r) && (
                      <input
                        type="checkbox"
                        checked={checkedKeys?.has(k) || false}
                        onChange={() => onCheck?.(k)}
                        aria-label={`Compare ${k}`}
                      />
                    )}
                  </td>
                )}
                {columns.map((c) => (
                  <td key={c.key} className={c.align === 'l' ? 'l' : ''}>
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
