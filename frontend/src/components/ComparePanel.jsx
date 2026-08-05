import { useEffect, useMemo, useState } from 'react'
import BreakdownTable, {
  KIND_FILTERS, actorRowsOf, breakdownRows,
} from './BreakdownTable.jsx'
import Tabs from './Tabs.jsx'
import { ActorFacts } from './Identity.jsx'

/* Side-by-side comparison of checked combatants: each raider's ACTUAL parse —
   the same ability breakdown the drilldown shows — lined up next to the
   others, the way people screenshot two ACT windows. One kind tab rules every
   column (comparing this one's damage to that one's heals isn't a
   comparison), and Share/ToHit/totals start hidden — the Columns menu brings
   them back. Computed from the already-loaded agg data, no extra requests.

   Every control that describes the VIEW rather than one raider (the kind tab,
   Combine pets, the column layout) is shared: flipping it on one parse flips
   it on all of them, because a comparison whose halves are showing different
   things isn't one. */
export default function ComparePanel({
  actors, keys, abilities, derived, duration, kind, onRemove,
}) {
  // the page tab in this panel's language, same as the single drilldown
  const [kindFilter, setKindFilter] = useState(kind || 'damage')
  useEffect(() => { if (kind) setKindFilter(kind) }, [kind])
  const [combinePets, setCombinePets] = useState(false)

  const picked = useMemo(
    () => keys.map((k) => actors.find((a) => a.key === k)).filter(Boolean),
    [actors, keys])

  const filter = KIND_FILTERS.find((k) => k.key === kindFilter) || KIND_FILTERS[0]

  if (picked.length < 2) return null

  return (
    <aside className="actorpanel card comparepanel">
      <Tabs
        tabs={KIND_FILTERS.map((f) => ({ key: f.key, label: f.label }))}
        value={kindFilter}
        onChange={setKindFilter}
      />
      <div className="cmpraiders">
        {picked.map((a) => (
          <RaiderParse
            key={a.key}
            actor={a}
            rows={breakdownRows(actorRowsOf(abilities, a.key), filter.kinds, combinePets)}
            kinds={filter.kinds}
            duration={duration}
            combinePets={combinePets}
            onCombinePets={setCombinePets}
            onRemove={onRemove}
          />
        ))}
      </div>
    </aside>
  )
}

/* One raider's box. The head is one line: who it is, then the two controls
   that used to eat a line each — the Columns menu is placed there by CSS
   (SortableTable renders it above the table). No stat strip: the pinned All
   row under the header is the same numbers, in the same table. */
function RaiderParse({
  actor, rows, kinds, duration, combinePets, onCombinePets, onRemove,
}) {
  return (
    <div className="cmpraider">
      <div className="raiderhead">
        <button className="cmphead" title="Remove from comparison" onClick={() => onRemove(actor.key)}>
          {actor.name}
        </button>
        {/* compact: several raiders share this line, and a guild name each
            would push the controls off it */}
        <ActorFacts actor={actor} compact />
        <label className={`chip toggle big ${combinePets ? 'on' : ''}`}
               title="One line per pet kit, on every parse here">
          <input
            type="checkbox"
            checked={combinePets}
            onChange={(e) => onCombinePets(e.target.checked)}
          /> Combine pets
        </label>
      </div>
      {rows.length ? (
        <BreakdownTable
          rows={rows}
          kinds={kinds}
          duration={duration}
          linkHover
          wrapClass="parsewin"
          fitViewport
          prefsKey="compare"
          defaultHidden={['total', 'share', 'to_hit_pct', 'median', 'min', 'press_delay_s']}
        />
      ) : (
        <p className="muted">Nothing on this tab.</p>
      )}
    </div>
  )
}
