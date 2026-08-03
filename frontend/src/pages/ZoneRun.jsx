import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import EncounterTree from '../components/EncounterTree.jsx'
import ShareBar from '../components/ShareBar.jsx'
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

function DpsBars({ actors, duration }) {
  const players = actors.filter((a) => a.kind === 'player' && a.damage > 0)
    .sort((a, b) => b.damage - a.damage).slice(0, 16)
  if (players.length < 2) return null
  const max = Math.max(...players.map((a) => a.damage), 1)
  return (
    <div className="dpsbars" aria-label="DPS by raider">
      {players.map((a) => (
        <div className="col" key={a.key} title={`${a.name}: ${fmt.num(a.damage / Math.max(duration, 1))} DPS`}>
          <span className="v">{fmt.num(a.damage / Math.max(duration, 1))}</span>
          <i style={{ height: `${Math.max((a.damage / max) * 100, 2)}%` }} />
          <span className="n">{a.name}</span>
        </div>
      ))}
    </div>
  )
}

export default function ZoneRun() {
  const { id } = useParams()
  const [run, setRun] = useState(null)
  const [encounters, setEncounters] = useState(null)
  const [detail, setDetail] = useState(null)
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)
  const [detailErr, setDetailErr] = useState(null)
  const [sel, setSel] = useQueryState('sel', 'all')
  const [actorQ, setActorQ] = useQueryState('actor')
  const [kindFilter, setKindFilter] = useState('all')

  useEffect(() => {
    api.zoneRun(id)
      .then((d) => { setRun(d.zone_run); setEncounters(d.encounters) })
      .catch((e) => setError(e.message))
  }, [id])

  useEffect(() => {
    let gone = false
    api.zoneRunReport(id)
      .then((d) => { if (!gone) setReport(d) })
      .catch(() => { if (!gone) setReport(null) })
    return () => { gone = true }
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

  // report columns for the CURRENT selection: sum the per-encounter player
  // rows across selected fights (works on any node, not just the whole run)
  const repRows = useMemo(() => {
    if (!report || !selIds) return null
    const want = new Set(selIds)
    const by = {}
    for (const enc of report.encounters) {
      if (!want.has(enc.encounter.id)) continue
      for (const p of enc.players) {
        const n = by[p.name] ??= { engage: [], death_dps_lost: 0, overheal_est: 0, saves: 0, time_dead_s: 0, deaths: 0, cures: 0 }
        if (p.engage_delay_s != null) n.engage.push(p.engage_delay_s)
        n.death_dps_lost += p.death_dps_lost || 0
        n.overheal_est += p.overheal_est || 0
        n.saves += p.saves || 0
        n.time_dead_s += p.time_dead_s || 0
        n.deaths += p.deaths || 0
        n.cures += p.cures || 0
      }
    }
    for (const n of Object.values(by)) {
      n.avg_engage_delay_s = n.engage.length
        ? Math.round((n.engage.reduce((s, x) => s + x, 0) / n.engage.length) * 10) / 10
        : null
    }
    return by
  }, [report, selIds && selIds.join(',')])

  const actors = detail?.actors ?? []
  const duration = Math.max(detail?.encounter?.duration_s || 0, 1)
  const players = useMemo(() => actors.filter((a) => a.kind === 'player'), [actors])
  const raidDamage = players.reduce((s, a) => s + (a.damage || 0), 0)
  const topDamage = Math.max(...players.map((a) => a.damage || 0), 1)

  const visibleActors = useMemo(() => actors.filter((a) =>
    (a.damage || 0) > 0 || (a.heals || 0) > 0 || (a.damage_taken || 0) > 0
    || (a.wards_absorbed || 0) > 0 || (a.power_fed || 0) > 0), [actors])

  const selectedActor = useMemo(() => {
    if (actorQ && actors.some((a) => a.key === actorQ)) return actorQ
    const top = actors.find((a) => a.kind === 'player' && a.damage > 0) || actors[0]
    return top?.key ?? null
  }, [actors, actorQ])

  const abilityRows = useMemo(() => {
    if (!detail || selectedActor == null) return []
    const f = KIND_FILTERS.find((k) => k.key === kindFilter)
    return detail.abilities.filter((r) =>
      (r.source_key === selectedActor || r.rollup_key === selectedActor)
      && (f?.kinds ? f.kinds.includes(r.kind) : r.kind !== 'self')
      && !(r.total === 0 && r.hits > 0 && r.max === null && r.kind === 'damage'
           && r.swings === r.hits))
  }, [detail, selectedActor, kindFilter])
  const abilityMax = Math.max(...abilityRows.map((r) => r.total || 0), 1)

  if (error) return <p className="err">{error}</p>
  if (!run || !encounters) return <p className="muted">Loading…</p>

  const selName = actors.find((a) => a.key === selectedActor)?.name
  const enc = detail?.encounter
  const zoneLabel = run.zone || 'Unknown zone'
  const title = sel === 'all'
    ? zoneLabel
    : enc?.name || (selIds && selIds.length > 1 ? `${selIds.length} fights` : '…')

  const actorCols = [
    {
      key: 'name', label: 'Name', align: 'l',
      render: (a) => <span className="name">{a.name}{kindBadge(a.kind)}</span>,
      sortValue: (a) => a.name,
    },
    { key: 'damage', label: 'Damage', format: fmt.num },
    {
      key: 'share', label: 'Dmg %', align: 'l',
      render: (a) => (a.kind === 'player' && a.damage > 0
        ? <span className="sharecell"><ShareBar value={a.damage} max={topDamage} />
            <span className="pctnum">{raidDamage ? `${((a.damage / raidDamage) * 100).toFixed(1)}%` : ''}</span></span>
        : null),
      sortValue: (a) => (a.kind === 'player' ? a.damage : -1),
    },
    { key: 'dps', label: 'EncDPS', format: fmt.num },
    { key: 'damage_taken', label: 'DmgTaken', format: fmt.num },
    { key: 'heals', label: 'Heals', format: fmt.num },
    {
      key: 'hps', label: 'EncHPS',
      render: (a) => fmt.num((a.heals || 0) / duration),
      sortValue: (a) => (a.heals || 0) / duration,
    },
    { key: 'wards_absorbed', label: 'Wards', format: fmt.num },
    { key: 'power_fed', label: 'PowerRepl', format: fmt.num },
    { key: 'power_drain', label: 'PowerDrain', format: fmt.num },
    { key: 'cure_count', label: 'Cures', render: (a) => a.cure_count || '' },
    { key: 'deaths', label: 'Deaths', render: (a) => a.deaths || '' },
  ]
  if (repRows) {
    actorCols.push(
      {
        key: 'engage', label: 'Engage',
        render: (a) => {
          const n = repRows[a.name]
          return n?.avg_engage_delay_s != null ? `${n.avg_engage_delay_s}s` : ''
        },
        sortValue: (a) => repRows[a.name]?.avg_engage_delay_s ?? null,
      },
      {
        key: 'dead_loss', label: 'Dmg lost dead',
        render: (a) => (repRows[a.name]?.death_dps_lost ? fmt.num(repRows[a.name].death_dps_lost) : ''),
        sortValue: (a) => repRows[a.name]?.death_dps_lost ?? null,
      },
      {
        key: 'overheal', label: 'Overheal',
        render: (a) => (repRows[a.name]?.overheal_est ? fmt.num(repRows[a.name].overheal_est) : ''),
        sortValue: (a) => repRows[a.name]?.overheal_est ?? null,
      },
      {
        key: 'saves', label: 'Saves',
        render: (a) => repRows[a.name]?.saves || '',
        sortValue: (a) => repRows[a.name]?.saves ?? null,
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
      key: 'encdps', label: 'EncDPS',
      render: (r) => (r.total ? fmt.num(r.total / duration) : '—'),
      sortValue: (r) => (r.total || 0) / duration,
    },
    {
      key: 'share', label: 'Share', align: 'l',
      render: (r) => <ShareBar value={r.total || 0} max={abilityMax} kind={r.kind === 'heal' ? 'heal' : 'dps'} />,
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
        sessionLabel={`Whole run — ${zoneLabel}`}
        hideZones
      />
      <div className="wsmain">
        <div className="pagehead" style={{ marginTop: 0 }}>
          <h1>{title}</h1>
          <span className="sub">
            {fmt.dateLong(run.started_ts)}
            {` · ${fmt.timeRange(enc?.started_ts ?? run.started_ts, enc?.ended_ts ?? run.ended_ts)}`}
            {` · ${run.character_name}`}
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
                rowKey={(a) => a.key}
                selectedKey={selectedActor}
                onRowClick={(a) => setActorQ(a.key)}
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
                  rowKey={(r) => `${r.source_key}:${r.ability}:${r.kind}`}
                />
              </div>
            )}

            <DpsBars actors={actors} duration={duration} />
          </>
        )}
      </div>
    </div>
  )
}
