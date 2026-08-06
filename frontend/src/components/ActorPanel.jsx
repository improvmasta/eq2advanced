import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import BreakdownTable, {
  KIND_FILTERS, PET_KINDS, actorRowsOf, breakdownRows, rateLabel, rowKeyOf,
} from './BreakdownTable.jsx'
import { ActorFacts } from './Identity.jsx'
import SelectionBar from './SelectionBar.jsx'
import Tabs from './Tabs.jsx'
import { fmt } from '../lib/api.js'
import { MELEE_BUCKETS } from '../lib/stats.js'

/* Individual parse in the right-hand column: the selected combatant's ability
   breakdown next to (not under) the raid table. The table itself is the shared
   BreakdownTable — the same one the comparison surfaces render — this panel
   adds the kind tabs, the composition strip and the summing checkboxes. */
export default function ActorPanel({
  name, actor, abilities, actorKey, duration, kind, onClose, compareTo,
}) {
  /* `kind` is the page tab translated into this panel's language — open a
     raider from Healing and you get their heals. It stays a starting point:
     changing the panel's own tab afterwards is not overruled until the page
     tab moves again. */
  const [kindFilter, setKindFilter] = useState(kind || 'damage')
  useEffect(() => { if (kind) setKindFilter(kind) }, [kind])
  const [picked, setPicked] = useState(() => new Set())
  /* Off by default: a summoner's pet rows are their own abilities with their
     own numbers, and folding them into one "Mage Pet" line hides the parse
     you opened the panel to read. Combining is a question you ask, not the
     answer you get handed. */
  const [combinePets, setCombinePets] = useState(false)

  const actorRows = useMemo(() => actorRowsOf(abilities, actorKey), [abilities, actorKey])

  const filter = KIND_FILTERS.find((k) => k.key === kindFilter) || KIND_FILTERS[0]

  const rows = useMemo(
    () => breakdownRows(actorRows, filter.kinds, combinePets),
    [actorRows, kindFilter, combinePets])

  const tabTotal = rows.reduce((s, r) => s + (r.total || 0), 0)

  /* Each tab's table has its own natural width, and letting the panel shrink
     and regrow with every tab shoves the whole page layout around. The width
     only ratchets up: it grows to fit the widest tab seen and holds there.
     The panel remounts per actor (keyed upstream), so the hold resets. */
  const panelRef = useRef(null)
  useLayoutEffect(() => {
    const el = panelRef.current
    if (!el) return
    const w = el.getBoundingClientRect().width
    if (w > (parseFloat(el.style.minWidth) || 0)) el.style.minWidth = `${Math.ceil(w)}px`
  }, [kindFilter, combinePets, rows])

  /* Composition and waste both read the damage rows regardless of which chip
     is showing — they describe the parse, not the current view. */
  const damageRows = useMemo(
    () => actorRows.filter((r) => r.kind === 'damage'), [actorRows])
  const comp = useMemo(() => {
    const out = { cast: 0, auto: 0, proc: 0, pet: 0, total: 0 }
    for (const r of damageRows) {
      const amt = r.total || 0
      out.total += amt
      if (PET_KINDS.has(r.source_kind) || r.via_pet) out.pet += amt
      if (MELEE_BUCKETS.has(r.ability)) out.auto += amt
      else if (r.proc) out.proc += amt
      else out.cast += amt
    }
    return out
  }, [damageRows])
  const waste = useMemo(() => {
    const out = { resists: 0, zero: 0 }
    for (const r of damageRows) {
      out.resists += r.resists || 0
      out.zero += r.zero_hits || 0
    }
    return out
  }, [damageRows])

  const pickedRows = rows.filter((r) => picked.has(rowKeyOf(r)))
  const togglePick = (k) => setPicked((s) => {
    const next = new Set(s)
    if (next.has(k)) next.delete(k); else next.add(k)
    return next
  })
  const pickedStats = (() => {
    const sum = (get) => pickedRows.reduce((s, r) => s + (get(r) || 0), 0)
    const total = sum((r) => r.total)
    const hits = sum((r) => r.hits)
    const crits = sum((r) => r.crits)
    return [
      { k: 'Total', v: total ? fmt.num(total) : null },
      { k: rateLabel(filter.kinds), v: total ? fmt.num2(total / duration) : null },
      {
        k: `of ${filter.label.toLowerCase()}`, v: total && tabTotal
          ? `${((total / tabTotal) * 100).toFixed(1)}%` : null,
        title: 'Share of what this combatant did on this tab',
      },
      { k: 'Casts', v: sum((r) => r.casts) || null },
      { k: 'Hits', v: hits || null },
      { k: 'Crit', v: hits ? `${Math.round((100 * crits) / hits)}%` : null },
    ]
  })()

  return (
    <aside className="actorpanel card" ref={panelRef}>
      <div className="drillhead">
        <h2 className="panelname">{name}</h2>
        <ActorFacts actor={actor} />
        <button className="chip closex" onClick={onClose} aria-label="Close panel">✕</button>
      </div>
      <Tabs
        tabs={KIND_FILTERS.map((f) => ({ key: f.key, label: f.label }))}
        value={kindFilter}
        onChange={setKindFilter}
      />
      {/* the parse's controls, one visible bar — not chips hidden in a corner */}
      <div className="optionsbar">
        {/* only players get one — comparing a mob across nights isn't a thing */}
        {compareTo && (
          <Link className="btn solid" to={compareTo}
                title={`Put ${name}'s parse beside another raid or player`}>
            ⇄ Compare
          </Link>
        )}
        <label className="optcheck" title="One line per pet kit">
          <input
            type="checkbox"
            checked={combinePets}
            onChange={(e) => setCombinePets(e.target.checked)}
          /> Combine pets
        </label>
      </div>
      {/* Counts, not advice: this panel is the parse. What a resist means is a
          judgement, and judgements live on the Insights tab. */}
      {kindFilter === 'damage' && comp.total > 0 && (
        <div className="statstrip">
          <span><b>{Math.round((100 * comp.cast) / comp.total)}%</b> cast</span>
          <span><b>{Math.round((100 * comp.auto) / comp.total)}%</b> autoattack</span>
          {comp.proc > 0 && (
            <span><b>{Math.round((100 * comp.proc) / comp.total)}%</b> procs</span>
          )}
          {comp.pet > 0 && (
            <span><b>{Math.round((100 * comp.pet) / comp.total)}%</b> pets</span>
          )}
          {waste.resists > 0 && <span><b>{fmt.num(waste.resists)}</b> resisted</span>}
          {waste.zero > 0 && <span><b>{fmt.num(waste.zero)}</b> absorbed</span>}
        </div>
      )}
      <BreakdownTable
        rows={rows}
        kinds={filter.kinds}
        duration={duration}
        prefsKey="drilldown"
        defaultHidden={['median', 'min']}
        checkable={(r) => !r.__sub && !r.__all}
        checkedKeys={picked}
        onCheck={togglePick}
      />
      {pickedRows.length > 0 && (
        <SelectionBar
          label={`${pickedRows.length} ability${pickedRows.length === 1 ? '' : 's'}`}
          stats={pickedStats}
          onClear={() => setPicked(new Set())}
        />
      )}
    </aside>
  )
}
