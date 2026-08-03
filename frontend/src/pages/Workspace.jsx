import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import EncounterTree from '../components/EncounterTree.jsx'
import SortableTable from '../components/SortableTable.jsx'
import { api, fmt } from '../lib/api.js'
import { useQueryState } from '../lib/useQueryState.js'

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

function kindBadge(kind) {
  if (kind === 'mob') return <span className="badge">mob</span>
  if (kind === 'other') return <span className="badge">env</span>
  if (PET_KINDS.has(kind)) return <span className="badge pet">pet</span>
  return null
}

export default function Workspace() {
  const { id } = useParams()
  const [session, setSession] = useState(null)
  const [encounters, setEncounters] = useState(null)
  const [detail, setDetail] = useState(null)
  const [night, setNight] = useState(null)      // raid-report night rows, All node only
  const [error, setError] = useState(null)
  const [detailErr, setDetailErr] = useState(null)
  const [sel, setSel] = useQueryState('sel', 'all')
  const [actorQ, setActorQ] = useQueryState('actor')
  const [kindFilter, setKindFilter] = useState('all')

  useEffect(() => {
    api.session(id)
      .then((d) => { setSession(d.session); setEncounters(d.encounters) })
      .catch((e) => setError(e.message))
  }, [id])

  const selIds = useMemo(() => {
    if (!encounters) return null
    if (sel === 'all') return encounters.map((e) => e.id)
    const ids = sel.split(',').map(Number).filter((n) => Number.isFinite(n))
    return ids.length ? ids : encounters.map((e) => e.id)
  }, [encounters, sel])

  useEffect(() => {
    if (!selIds || !selIds.length) { setDetail(null); return }
    let gone = false
    setDetail(null)
    setDetailErr(null)
    api.encountersAgg(selIds)
      .then((d) => { if (!gone) setDetail(d) })
      .catch((e) => { if (!gone) setDetailErr(e.message) })
    return () => { gone = true }
  }, [selIds && selIds.join(',')])

  // engagement / death-cost / overheal columns exist only in the raid report;
  // merge them into the combatant table on the whole-session node
  useEffect(() => {
    if (sel !== 'all' || !session || session.status !== 'ready') { setNight(null); return }
    let gone = false
    api.raidReport(id)
      .then((d) => { if (!gone) setNight(Object.fromEntries(d.night.map((n) => [n.name, n]))) })
      .catch(() => { if (!gone) setNight(null) })
    return () => { gone = true }
  }, [id, sel, session && session.status])

  const actors = detail?.actors ?? []
  const duration = Math.max(detail?.encounter?.duration_s || 0, 1)
  const players = useMemo(() => actors.filter((a) => a.kind === 'player'), [actors])
  const raidDamage = players.reduce((s, a) => s + (a.damage || 0), 0)

  const visibleActors = useMemo(() => actors.filter((a) =>
    (a.damage || 0) > 0 || (a.heals || 0) > 0 || (a.damage_taken || 0) > 0
    || (a.wards_absorbed || 0) > 0 || (a.power_fed || 0) > 0), [actors])

  const selectedActor = useMemo(() => {
    const q = actorQ != null ? Number(actorQ) : null
    if (q != null && actors.some((a) => a.entity_id === q)) return q
    const top = actors.find((a) => a.kind === 'player' && a.damage > 0) || actors[0]
    return top?.entity_id ?? null
  }, [actors, actorQ])

  const abilityRows = useMemo(() => {
    if (!detail || selectedActor == null) return []
    const f = KIND_FILTERS.find((k) => k.key === kindFilter)
    return detail.abilities.filter((r) =>
      (r.entity_id === selectedActor || r.rollup_to === selectedActor)
      && (f?.kinds ? f.kinds.includes(r.kind) : r.kind !== 'self')
      && !(r.total === 0 && r.hits > 0 && r.max === null && r.kind === 'damage'
           && r.swings === r.hits))
  }, [detail, selectedActor, kindFilter])

  if (error) return <p className="err">{error}</p>
  if (!session || !encounters) return <p className="muted">Loading…</p>

  const selName = actors.find((a) => a.entity_id === selectedActor)?.name
  const enc = detail?.encounter
  const title = sel === 'all'
    ? `All — ${fmt.date(session.started_ts)}`
    : enc?.name || (selIds && selIds.length > 1 ? `${selIds.length} fights` : '…')

  const actorCols = [
    {
      key: 'name', label: 'Name', align: 'l',
      render: (a) => <span className="name">{a.name}{kindBadge(a.kind)}</span>,
      sortValue: (a) => a.name,
    },
    { key: 'damage', label: 'Damage', format: fmt.num },
    {
      key: 'share', label: 'Dmg %',
      render: (a) => (a.kind === 'player' && a.damage > 0 && raidDamage
        ? `${((a.damage / raidDamage) * 100).toFixed(1)}%` : ''),
      sortValue: (a) => (a.kind === 'player' ? a.damage : -1),
    },
    { key: 'dps', label: 'DPS', format: fmt.num },
    {
      key: 'avg_delay', label: 'AvgDelay',
      render: (a) => (a.avg_delay_s != null ? a.avg_delay_s.toFixed(2) : ''),
      sortValue: (a) => a.avg_delay_s ?? null,
    },
    { key: 'damage_taken', label: 'DmgTaken', format: fmt.num },
    { key: 'heals', label: 'Heals', format: fmt.num },
    {
      key: 'hps', label: 'HPS',
      render: (a) => fmt.num((a.heals || 0) / duration),
      sortValue: (a) => (a.heals || 0) / duration,
    },
    { key: 'wards_absorbed', label: 'Wards', format: fmt.num },
    { key: 'power_fed', label: 'PowerRepl', format: fmt.num },
    { key: 'power_drain', label: 'PowerDrain', format: fmt.num },
    { key: 'cure_count', label: 'Cures', render: (a) => a.cure_count || '' },
    { key: 'deaths', label: 'Deaths', render: (a) => a.deaths || '' },
  ]
  if (night) {
    actorCols.push(
      {
        key: 'engage', label: 'Engage',
        render: (a) => {
          const n = night[a.name]
          return n?.avg_engage_delay_s != null ? `${n.avg_engage_delay_s}s` : ''
        },
        sortValue: (a) => night[a.name]?.avg_engage_delay_s ?? null,
      },
      {
        key: 'dead_loss', label: 'Dmg lost dead',
        render: (a) => (night[a.name]?.death_dps_lost ? fmt.num(night[a.name].death_dps_lost) : ''),
        sortValue: (a) => night[a.name]?.death_dps_lost ?? null,
      },
      {
        key: 'overheal', label: 'Overheal',
        render: (a) => (night[a.name]?.overheal_est ? fmt.num(night[a.name].overheal_est) : ''),
        sortValue: (a) => night[a.name]?.overheal_est ?? null,
      },
    )
  }

  const abilityCols = [
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
      key: 'encdps', label: 'DPS',
      render: (r) => (r.total ? fmt.num(r.total / duration) : '—'),
      sortValue: (r) => (r.total || 0) / duration,
    },
    {
      key: 'share', label: 'Share',
      render: (r) => {
        const sum = abilityRows.reduce((s, x) => s + (x.total || 0), 0)
        return r.total && sum ? `${((r.total / sum) * 100).toFixed(1)}%` : ''
      },
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
    <div className="workspace">
      <EncounterTree
        encounters={encounters}
        sel={selIds && selIds.length === encounters.length ? 'all' : sel}
        onSelect={(key) => { setSel(key === 'all' ? null : key); setActorQ(null) }}
        sessionLabel={fmt.date(session.started_ts)}
      />
      <div className="wsmain">
        <div className="pagehead" style={{ marginTop: 0 }}>
          <h1>{title}</h1>
          <span className="sub">
            {session.character_name}
            {enc?.zone ? ` · ${enc.zone}` : ''}
            {enc?.started_ts ? ` · ${fmt.time(enc.started_ts)}` : ''}
          </span>
        </div>
        {detailErr && <p className="err">{detailErr}</p>}
        {!detail && !detailErr && <p className="muted">Loading…</p>}
        {detail && (
          <>
            <div className="metrics">
              <div className="metric"><div className="v">{fmt.dur(enc.duration_s)}</div><div className="k">Combat</div></div>
              <div className="metric"><div className="v">{fmt.num(raidDamage)}</div><div className="k">Raid damage</div></div>
              <div className="metric"><div className="v">{fmt.num(raidDamage / duration)}</div><div className="k">Raid DPS</div></div>
              <div className="metric"><div className="v">{players.filter((p) => p.damage > 0 || p.heals > 0).length}</div><div className="k">Raiders</div></div>
              {selIds.length > 1 && <div className="metric"><div className="v">{selIds.length}</div><div className="k">Fights</div></div>}
            </div>

            <div className="card">
              <SortableTable
                columns={actorCols}
                rows={visibleActors}
                defaultSort={{ key: 'dps', dir: 'desc' }}
                rowKey={(a) => a.entity_id}
                selectedKey={selectedActor}
                onRowClick={(a) => setActorQ(a.entity_id)}
              />
            </div>

            {selectedActor != null && (
              <div className="card">
                <div className="drillhead">
                  <h2>{selName}</h2>
                  <div className="chips">
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
                </div>
                <SortableTable
                  columns={abilityCols}
                  rows={abilityRows}
                  defaultSort={{ key: 'encdps', dir: 'desc' }}
                  rowKey={(r) => `${r.entity_id}:${r.ability}:${r.kind}`}
                />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
