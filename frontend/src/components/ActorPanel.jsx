import { useMemo, useState } from 'react'
import CompositionBar from './CompositionBar.jsx'
import SelectionBar from './SelectionBar.jsx'
import SortableTable from './SortableTable.jsx'
import Tabs from './Tabs.jsx'
import { fmt } from '../lib/api.js'
import { MELEE_BUCKETS } from '../lib/stats.js'

/* Where the damage came from. Three exclusive buckets, each written out under
   the bar — a rotation, a swing timer, and gear firing on its own are three
   different things to coach. */
const COMP_COLORS = { cast: '#448dd4', auto: '#a88d10', proc: '#9750a7' }

/* No "All" view: the panel is per-kind, Damage first, wards folded into Heals
   (Kind still marks ward rows so they're tellable apart). */
const KIND_FILTERS = [
  { key: 'damage', label: 'Damage', kinds: ['damage'] },
  { key: 'heal', label: 'Heals', kinds: ['heal', 'ward'] },
  { key: 'power', label: 'Power', kinds: ['power'] },
  { key: 'threat', label: 'Threat', kinds: ['threat', 'detaunt'] },
  { key: 'cure', label: 'Cures', kinds: ['cure'] },
  { key: 'self', label: 'Self', kinds: ['self'] },
]

/* An ability that lands as more than one kind (lifetaps: damage + heal) shows
   its off-kind components condensed behind the row's … expander. */
const EXTRA_KINDS = new Set(['damage', 'heal', 'ward', 'power'])

const PET_KINDS = new Set(['own_pet', 'swarm_pet', 'named_pet'])
const AVOID_COLS = ['misses', 'parries', 'ripostes', 'dodges', 'blocks', 'resists']

/* A row that neither landed anything nor moved a number is a cast the parse
   can't say anything about (summons, stances, the buff half of an ability).
   Fully-absorbed rows still land (hits > 0, total 0) and stay. */
const didSomething = (r) => (r.total || 0) !== 0 || (r.hits || 0) !== 0

/* Set-in-stone pet kits (the game hasn't changed): each pet ability belongs to
   one summoned-pet archetype, so the bare-name own pet — whose identity the
   log never states — is attributed by what it casts. Pet autoattack carries no
   ability name, so it stays its own bucket. Curated with Lindsay from real
   raid logs; the backend's CURATED_PET_ABILITIES (census/catalog.py) is the
   flat superset. Fighter-pet entries pending a fighter-pet parse. */
const PET_ARCHETYPE = {
  // necromancer mage pet (Grim Sorcerer)
  'Grim Wave': 'Mage Pet', 'Grim Embrace': 'Mage Pet',
  'Grim Devastation': 'Mage Pet', 'Grim Lifetap': 'Mage Pet',
  'Grim Bolt': 'Mage Pet', 'Grim Distortion': 'Mage Pet',
  'Grisly Feedback': 'Mage Pet',
  // necromancer scout pet
  'Throat Gash': 'Scout Pet', 'Poisoned Spike': 'Scout Pet',
  'Shadowy Garrote': 'Scout Pet', 'Unseen Blade': 'Scout Pet',
  'Shadow Step': 'Scout Pet', 'Shadestrike': 'Scout Pet',
  'Quick Strike': 'Scout Pet', 'Clawing of the Soul': 'Scout Pet',
  'Acidity': 'Scout Pet', 'Shockwave': 'Scout Pet', 'Shout': 'Scout Pet',
}

/* "Bobby's blighted horde" -> "blighted horde"; own pets share the owner's
   bare name, so they read as just "Pet". */
function petShort(r) {
  if (r.source_kind === 'own_pet') return 'Pet'
  return r.source_name.includes("'s ")
    ? r.source_name.slice(r.source_name.indexOf("'s ") + 3)
    : r.source_name
}

/* "Bobby's blighted horde" + "Grave Decay" -> "blighted horde's Grave Decay",
   the way ACT prints pet rows inside the owner's breakdown. */
function abilityLabel(r) {
  if (!PET_KINDS.has(r.source_kind)) return r.ability
  const short = petShort(r)
  if (r.ability === '(melee)') return short
  if (r.ability.startsWith('(')) return `${short} ${r.ability}`
  return `${short}'s ${r.ability}`
}

/* Swarm/named pets are identified by name; the bare-name pet by its kit. */
function petGroupLabel(r) {
  if (r.source_kind === 'swarm_pet' || r.source_kind === 'named_pet') return petShort(r)
  if (MELEE_BUCKETS.has(r.ability)) return 'Pet Autoattack'
  return PET_ARCHETYPE[r.ability] || 'Pet'
}

function addInto(g, r) {
  for (const c of ['casts', 'hits', 'crits', 'zero_hits', 'total', 'swings', 'presses', 'reflects', ...AVOID_COLS]) {
    g[c] = (g[c] || 0) + (r[c] || 0)
  }
  g.min = r.min != null ? (g.min != null ? Math.min(g.min, r.min) : r.min) : g.min
  g.max = r.max != null ? (g.max != null ? Math.max(g.max, r.max) : r.max) : g.max
  if (r.dtypes) {
    g.dtypes = g.dtypes || {}
    for (const [t, amt] of Object.entries(r.dtypes)) g.dtypes[t] = (g.dtypes[t] || 0) + amt
  }
  g.to_hit_pct = g.swings ? Math.round((10000 * g.hits) / g.swings) / 100 : null
}

/* Every pet-sourced row (and pet casts conflated under the player's name)
   condenses into one Scout/Mage/Fighter Pet row per kind; the members stay
   attached for the … expander. */
function groupPets(rows) {
  const out = []
  const groups = new Map()
  for (const r of rows) {
    if (!PET_KINDS.has(r.source_kind) && !r.via_pet) { out.push(r); continue }
    const label = petGroupLabel(r)
    const key = `petgroup:${label}|${r.kind}`
    let g = groups.get(key)
    if (g == null) {
      g = {
        ...r,
        ability: label, gkey: key, members: [],
        casts: 0, hits: 0, crits: 0, zero_hits: 0, total: 0,
        min: null, max: null, median: null, avg_delay_s: null,
        presses: 0, press_delay_s: null,
        dtypes: null, swings: 0, to_hit_pct: null,
      }
      for (const c of AVOID_COLS.concat('reflects')) g[c] = 0
      groups.set(key, g)
      out.push(g)
    }
    addInto(g, r)
    g.members.push({ ...r, __sub: true })
  }
  for (const g of groups.values()) g.members.sort((a, b) => (b.total || 0) - (a.total || 0))
  return out
}

/* Individual parse in the right-hand column: the selected combatant's ability
   breakdown next to (not under) the raid table. */
export default function ActorPanel({ name, abilities, actorKey, duration, onClose }) {
  const [kindFilter, setKindFilter] = useState('damage')
  const [open, setOpen] = useState({})   // expandable row key -> open
  const [picked, setPicked] = useState(() => new Set())
  /* Off by default: a summoner's pet rows are their own abilities with their
     own numbers, and folding them into one "Mage Pet" line hides the parse
     you opened the panel to read. Combining is a question you ask, not the
     answer you get handed. */
  const [combinePets, setCombinePets] = useState(false)

  // every row this combatant owns, all kinds (phantom fully-avoided rows dropped)
  const actorRows = useMemo(() => (abilities || []).filter((r) =>
    (r.source_key === actorKey || r.rollup_key === actorKey)
    && !(r.total === 0 && r.hits > 0 && r.max === null && r.kind === 'damage'
         && r.swings === r.hits)), [abilities, actorKey])

  const filter = KIND_FILTERS.find((k) => k.key === kindFilter) || KIND_FILTERS[0]

  const rows = useMemo(() => {
    const inTab = actorRows.filter((r) => filter.kinds.includes(r.kind) && didSomething(r))
    const grouped = combinePets ? groupPets(inTab) : inTab.slice()
    // off-kind components of the same ability (lifetap heals under a damage
    // row and vice versa) ride along as expandable sub-rows
    for (const r of grouped) {
      if (r.members) continue
      const extras = actorRows.filter((x) =>
        x.ability === r.ability && x.source_key === r.source_key
        && !filter.kinds.includes(x.kind) && EXTRA_KINDS.has(x.kind) && didSomething(x))
      if (extras.length) {
        r.extras = extras.map((x) => ({ ...x, __sub: true }))
          .sort((a, b) => (b.total || 0) - (a.total || 0))
      }
    }
    return grouped
  }, [actorRows, kindFilter, combinePets])

  const tabTotal = rows.reduce((s, r) => s + (r.total || 0), 0)

  /* Composition and waste both read the damage rows regardless of which chip
     is showing — they describe the parse, not the current view. */
  const damageRows = useMemo(
    () => actorRows.filter((r) => r.kind === 'damage'), [actorRows])
  const comp = useMemo(() => {
    const out = { cast: 0, auto: 0, proc: 0, total: 0 }
    for (const r of damageRows) {
      const amt = r.total || 0
      out.total += amt
      if (MELEE_BUCKETS.has(r.ability)) out.auto += amt
      else if (r.proc) out.proc += amt
      else out.cast += amt
    }
    return out
  }, [damageRows])
  const waste = useMemo(() => {
    const out = { swings: 0, resists: 0, avoided: 0, zero: 0 }
    for (const r of damageRows) {
      out.swings += r.swings || 0
      out.resists += r.resists || 0
      out.zero += r.zero_hits || 0
      for (const c of AVOID_COLS) if (c !== 'resists') out.avoided += r[c] || 0
    }
    return out
  }, [damageRows])

  const rowKey = (r) => (r.__sub ? 'sub:' : '') + (r.gkey || `${r.source_key}:${r.ability}:${r.kind}`)
  const pickedRows = rows.filter((r) => picked.has(rowKey(r)))
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
      { k: 'DPS', v: total ? fmt.num2(total / duration) : null },
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
  const toggle = (k) => setOpen((o) => ({ ...o, [k]: !o[k] }))
  const childrenOf = (r) => {
    const kids = r.members || r.extras
    return kids && open[rowKey(r)] ? kids : []
  }

  const cols = [
    {
      key: 'ability', label: 'Type', align: 'l',
      render: (r) => {
        const k = rowKey(r)
        const kids = r.members || r.extras
        return (
          <span className="name">
            {r.gkey ? r.ability : abilityLabel(r)}
            {(r.gkey || PET_KINDS.has(r.source_kind)) && <span className="badge pet">pet</span>}
            {!r.gkey && r.via_pet && <span className="badge pet">pet cast</span>}
            {!r.gkey && r.proc && (
              <span
                className="badge proc"
                title={`Fires on its own, not something cast${r.proc_why ? ` — ${r.proc_why}` : ''}`}
              >
                proc
              </span>
            )}
            {r.kind !== filter.kinds[0] && <span className="muted"> {r.kind}</span>}
            {!r.__sub && kids && (
              <button
                className="expandcol"
                onClick={(e) => { e.stopPropagation(); toggle(k) }}
                title={open[k] ? 'Collapse' : (r.members ? 'Expand pet abilities' : 'Expand components')}
              >{open[k] ? '▾' : '…'}</button>
            )}
          </span>
        )
      },
      sortValue: (r) => (r.gkey ? r.ability : abilityLabel(r)),
    },
    {
      key: 'encdps', label: 'DPS',
      render: (r) => (r.total ? fmt.num2(r.total / duration) : '—'),
      sortValue: (r) => (r.total || 0) / duration,
    },
    {
      key: 'share', label: 'Share',
      render: (r) => (!r.__sub && r.total && tabTotal
        ? `${((r.total / tabTotal) * 100).toFixed(1)}%` : ''),
      sortValue: (r) => r.total || 0,
    },
    { key: 'hits', label: 'Hits' },
    {
      key: 'to_hit_pct', label: 'ToHit',
      render: (r) => (r.to_hit_pct != null ? `${r.to_hit_pct.toFixed(1)}%` : '—'),
    },
    {
      key: 'crit', label: 'Crit %',
      render: (r) => (r.hits ? `${Math.round((r.crits / r.hits) * 100)}%` : ''),
      sortValue: (r) => (r.hits ? r.crits / r.hits : null),
    },
    {
      key: 'avg', label: 'Average',
      render: (r) => (r.hits ? fmt.num(r.total / r.hits) : '—'),
      sortValue: (r) => (r.hits ? r.total / r.hits : null),
    },
    { key: 'max', label: 'MaxHit', format: fmt.num },
    {
      key: 'avg_delay_s', label: 'AvgDelay',
      render: (r) => (r.avg_delay_s != null ? r.avg_delay_s.toFixed(2) : '—'),
    },
    /* Between LANDINGS (AvgDelay) vs between ACTIVATIONS: for a DoT the first
       is its tick rate and the second is how often it was recast, which is
       the number you can actually do something about. */
    {
      key: 'press_delay_s', label: 'AvgDelay adj',
      render: (r) => (r.press_delay_s != null
        ? <span title={`${r.presses} activation${r.presses === 1 ? '' : 's'} of ${r.hits} landings`}>
            {r.press_delay_s.toFixed(2)}
          </span>
        : '—'),
    },
  ]

  return (
    <aside className="actorpanel card">
      <div className="drillhead">
        <h2>{name}</h2>
        <button className="chip closex" onClick={onClose} aria-label="Close panel">✕</button>
      </div>
      <Tabs
        tabs={KIND_FILTERS.map((f) => ({ key: f.key, label: f.label }))}
        value={kindFilter}
        onChange={setKindFilter}
      />
      <div className="filterbar">
        <label className="chip toggle" title="One line per pet kit">
          <input
            type="checkbox"
            checked={combinePets}
            onChange={(e) => setCombinePets(e.target.checked)}
          /> Combine pets
        </label>
      </div>
      {kindFilter === 'damage' && comp.total > 0 && (
        <CompositionBar
          total={comp.total}
          parts={[
            { key: 'cast', label: 'Cast abilities', value: comp.cast, color: COMP_COLORS.cast },
            { key: 'auto', label: 'Autoattack', value: comp.auto, color: COMP_COLORS.auto },
            { key: 'proc', label: 'Procs', value: comp.proc, color: COMP_COLORS.proc },
          ]}
        />
      )}
      {/* Counts, not advice: this panel is the parse. What a resist means is a
          judgement, and judgements live on the Insights tab. */}
      {kindFilter === 'damage' && waste.swings > 0
        && (waste.avoided || waste.resists || waste.zero) > 0 && (
        <div className="wastestrip">
          <span><b>{fmt.num(waste.swings)}</b> swings</span>
          {waste.avoided > 0 && <span><b>{fmt.num(waste.avoided)}</b> avoided</span>}
          {waste.resists > 0 && <span><b>{fmt.num(waste.resists)}</b> resisted</span>}
          {waste.zero > 0 && <span><b>{fmt.num(waste.zero)}</b> absorbed</span>}
        </div>
      )}
      <SortableTable
        columns={cols}
        rows={rows}
        defaultSort={{ key: 'encdps', dir: 'desc' }}
        rowKey={rowKey}
        childrenOf={childrenOf}
        rowClass={(r) => (r.__sub ? 'subrow' : '')}
        checkable={(r) => !r.__sub}
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
