/* The summing calculator: adds up whatever is checked. Deliberately dumb — the
   caller decides what "total" means for its rows, so the same component serves
   the combatant table and the ability breakdown.

   Two shapes, same numbers: the default sticky footer bar, and `head` — a box
   that sits above the table, names what is selected, and lays the totals out
   underneath in a grid instead of one long strip. */
function Stats({ stats }) {
  return (
    <div className="ss">
      {stats.filter((s) => s.v != null).map((s) => (
        <span key={s.k} className="sstat" title={s.title}>
          <b>{s.v}</b>
          <span className="k">{s.k}</span>
        </span>
      ))}
    </div>
  )
}

export default function SelectionBar({ label, stats, onClear, actions, head, chips }) {
  if (head) {
    return (
      <div className="card selhead" role="status">
        <div className="selhead-top">
          <h2>{label}</h2>
          {chips}
          <div className="sa">
            {actions}
            <button className="chip" onClick={onClear}>Clear</button>
          </div>
        </div>
        <Stats stats={stats} />
      </div>
    )
  }
  return (
    <div className="selbar" role="status">
      <span className="sl">{label}</span>
      <Stats stats={stats} />
      <div className="sa">
        {actions}
        <button className="chip" onClick={onClear}>Clear</button>
      </div>
    </div>
  )
}
