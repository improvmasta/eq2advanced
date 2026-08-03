import { useMemo, useState } from 'react'
import ShareBar from './ShareBar.jsx'
import SortableTable from './SortableTable.jsx'
import { fmt } from '../lib/api.js'

const KIND_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'damage', label: 'Damage', kinds: ['damage'] },
  { key: 'heal', label: 'Heals', kinds: ['heal'] },
  { key: 'ward', label: 'Wards', kinds: ['ward'] },
  { key: 'power', label: 'Power', kinds: ['power'] },
  { key: 'threat', label: 'Threat', kinds: ['threat', 'detaunt'] },
  { key: 'self', label: 'Self', kinds: ['self'] },
]

const PET_KINDS = new Set(['own_pet', 'swarm_pet', 'named_pet'])

/* "Bobby's blighted horde" + "Grave Decay" -> "blighted horde's Grave Decay",
   the way ACT prints pet rows inside the owner's breakdown. */
function abilityLabel(r) {
  if (!PET_KINDS.has(r.source_kind)) return r.ability
  const short = r.source_name.includes("'s ")
    ? r.source_name.slice(r.source_name.indexOf("'s ") + 3)
    : r.source_name
  if (r.ability === '(melee)') return short
  if (r.ability.startsWith('(')) return `${short} ${r.ability}`
  return `${short}'s ${r.ability}`
}

/* Individual parse in the right-hand column: the selected combatant's ability
   breakdown next to (not under) the raid table. */
export default function ActorPanel({ name, abilities, actorKey, duration, onClose }) {
  const [kindFilter, setKindFilter] = useState('all')

  const rows = useMemo(() => {
    const f = KIND_FILTERS.find((k) => k.key === kindFilter)
    return (abilities || []).filter((r) =>
      (r.source_key === actorKey || r.rollup_key === actorKey)
      && (f?.kinds ? f.kinds.includes(r.kind) : r.kind !== 'self')
      && !(r.total === 0 && r.hits > 0 && r.max === null && r.kind === 'damage'
           && r.swings === r.hits))
  }, [abilities, actorKey, kindFilter])
  const max = Math.max(...rows.map((r) => r.total || 0), 1)

  const cols = [
    {
      key: 'ability', label: 'Type', align: 'l',
      render: (r) => (
        <span className="name">
          {abilityLabel(r)}
          {PET_KINDS.has(r.source_kind) && <span className="badge pet">pet</span>}
          {r.via_pet && <span className="badge pet">pet cast</span>}
        </span>
      ),
      sortValue: (r) => abilityLabel(r),
    },
    { key: 'kind', label: 'Kind', render: (r) => <span className="muted">{r.kind}</span>, sortValue: (r) => r.kind },
    { key: 'total', label: 'Total', format: fmt.num },
    {
      key: 'encdps', label: 'EncDPS',
      render: (r) => (r.total ? fmt.num(r.total / duration) : '—'),
      sortValue: (r) => (r.total || 0) / duration,
    },
    {
      key: 'share', label: 'Share', align: 'l',
      render: (r) => <ShareBar value={r.total || 0} max={max} kind={r.kind === 'heal' ? 'heal' : 'dps'} />,
      sortValue: (r) => r.total || 0,
    },
    { key: 'casts', label: 'Casts', render: (r) => r.casts || '' },
    { key: 'hits', label: 'Hits' },
    { key: 'swings', label: 'Swings' },
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
    { key: 'median', label: 'Median', format: fmt.num },
    { key: 'min', label: 'MinHit', format: fmt.num },
    { key: 'max', label: 'MaxHit', format: fmt.num },
    {
      key: 'avg_delay_s', label: 'AvgDelay',
      render: (r) => (r.avg_delay_s != null ? r.avg_delay_s.toFixed(2) : '—'),
    },
    {
      key: 'dtype', label: 'Type(s)', align: 'l',
      render: (r) => (r.dtypes
        ? Object.entries(r.dtypes).sort((a, b) => b[1] - a[1]).map(([t]) => t).join('/')
        : ''),
      sortValue: (r) => (r.dtypes ? Object.keys(r.dtypes).sort().join('/') : null),
    },
    { key: 'resists', label: 'Resist', render: (r) => r.resists || '' },
  ]

  return (
    <aside className="actorpanel card">
      <div className="drillhead">
        <h2>{name}</h2>
        <button className="chip closex" onClick={onClose} aria-label="Close panel">✕</button>
      </div>
      <div className="chips" style={{ marginBottom: 8 }}>
        {KIND_FILTERS.map((f) => (
          <button
            key={f.key}
            className={`chip ${kindFilter === f.key ? 'on' : ''}`}
            onClick={() => setKindFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>
      <SortableTable
        columns={cols}
        rows={rows}
        defaultSort={{ key: 'encdps', dir: 'desc' }}
        rowKey={(r) => `${r.source_key}:${r.ability}:${r.kind}`}
      />
    </aside>
  )
}
