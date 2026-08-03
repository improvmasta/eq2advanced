import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import ActorPanel from '../components/ActorPanel.jsx'
import ComparePanel from '../components/ComparePanel.jsx'
import EncounterTree from '../components/EncounterTree.jsx'
import ErrorBoundary from '../components/ErrorBoundary.jsx'
import ShareBar from '../components/ShareBar.jsx'
import SortableTable from '../components/SortableTable.jsx'
import Tabs from '../components/Tabs.jsx'
import { api, fmt } from '../lib/api.js'
import { autoPct, castsPerMin, critPct, damageDerived, deathRows, reportRollup } from '../lib/stats.js'
import { useQueryState } from '../lib/useQueryState.js'

const PET_KINDS = new Set(['own_pet', 'swarm_pet', 'named_pet'])

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'damage', label: 'Damage' },
  { key: 'healing', label: 'Healing' },
  { key: 'defense', label: 'Defense' },
  { key: 'insights', label: 'Insights' },
]

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

const SEVERITY_CLASS = { warn: 'warn', opportunity: 'opportunity' }

function Insights({ report, coach, coachErr, busy, onGenerate }) {
  const cur = coach?.currencies
  const tiles = cur ? [
    ['DPS', fmt.num(cur.dps)],
    ['HPS', cur.hps ? fmt.num(cur.hps) : null],
    ['Crit', cur.crit_pct != null ? `${Math.round(cur.crit_pct)}%` : null],
    ['Autoattack', cur.autoattack_pct != null ? `${Math.round(cur.autoattack_pct)}%` : null],
    ['Casts/min', cur.cpm != null ? cur.cpm.toFixed(1) : null],
    ['Idle', cur.idle_pct != null ? `${Math.round(cur.idle_pct)}%` : null],
    ['Overheal', cur.overheal_pct != null ? `${Math.round(cur.overheal_pct)}%` : null],
    ['Time dead', cur.time_dead_s ? fmt.dur(cur.time_dead_s) : null],
    ['Cure latency', cur.cure_latency_self_s != null ? `${cur.cure_latency_self_s}s` : null],
    ['Rez delay', cur.rez_delay_s != null ? `${cur.rez_delay_s}s` : null],
  ].filter(([, v]) => v != null) : []

  return (
    <>
      {coach == null && (
        <div className="card">
          <h2>Coach</h2>
          {coachErr && <p className="err">{coachErr}</p>}
          <p className="muted">No coach report for this run's log yet.</p>
          <button disabled={busy} onClick={onGenerate}>{busy ? 'Generating…' : 'Generate coach report'}</button>
        </div>
      )}
      {coach && (
        <>
          <div className="card">
            <div className="drillhead">
              <h2>Coach — {coach.character?.name ?? coach.character}</h2>
              <span className="muted">{coach.archetype}</span>
              <button className="chip" style={{ marginLeft: 'auto' }} disabled={busy} onClick={onGenerate}>
                {busy ? 'Generating…' : 'Regenerate'}
              </button>
            </div>
            {tiles.length > 0 && (
              <div className="metrics">
                {tiles.map(([k, v]) => (
                  <div className="metric" key={k}><div className="v">{v}</div><div className="k">{k}</div></div>
                ))}
              </div>
            )}
            {coach.findings?.length > 0 && coach.findings.map((f, i) => (
              <div key={i} className={`finding ${SEVERITY_CLASS[f.severity] || ''}`}>
                <span className="badge">{f.severity}</span>
                <span><strong>{f.title}</strong>{f.detail ? ` — ${f.detail}` : ''}</span>
              </div>
            ))}
          </div>
          {coach.stat_priorities?.length > 0 && (
            <div className="card">
              <h2>Stat priorities</h2>
              <div className="tablewrap">
                <table className="data">
                  <thead>
                    <tr><th className="l">Stat</th><th>Step</th><th>Damage gain</th><th>DPS gain</th><th className="l">Why</th></tr>
                  </thead>
                  <tbody>
                    {coach.stat_priorities.map((p) => (
                      <tr key={p.stat}>
                        <td className="name l">{p.label}</td>
                        <td>{p.step}</td>
                        <td>{fmt.num(p.damage_gain)}</td>
                        <td>{p.dps_gain}</td>
                        <td className="l muted">{p.why}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {coach.caveats?.length > 0 && (
            <p className="note">{coach.caveats.join(' ')}</p>
          )}
        </>
      )}
      {report?.caveats?.length > 0 && (
        <p className="note">{report.caveats.join(' ')}</p>
      )}
    </>
  )
}

export default function ZoneRun() {
  const { id } = useParams()
  const [run, setRun] = useState(null)
  const [encounters, setEncounters] = useState(null)
  const [detail, setDetail] = useState(null)
  const [report, setReport] = useState(null)
  const [coach, setCoach] = useState(null)
  const [coachErr, setCoachErr] = useState(null)
  const [coachBusy, setCoachBusy] = useState(false)
  const [error, setError] = useState(null)
  const [detailErr, setDetailErr] = useState(null)
  const [sel, setSel] = useQueryState('sel', 'all')
  const [actorQ, setActorQ] = useQueryState('actor')
  const [tab, setTab] = useQueryState('tab', 'overview')
  const [cmpQ, setCmpQ] = useQueryState('cmp')

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

  // the coach engine is per session; a run's log is its dominant session
  const domSession = useMemo(() => {
    if (!encounters?.length) return null
    const counts = {}
    for (const e of encounters) counts[e.session_id] = (counts[e.session_id] || 0) + 1
    return Number(Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0])
  }, [encounters])

  useEffect(() => {
    if (tab !== 'insights' || !domSession) return
    let gone = false
    api.coach(domSession)
      .then((d) => { if (!gone) setCoach(d.report) })
      .catch((e) => { if (!gone) setCoachErr(e.message) })
    return () => { gone = true }
  }, [tab, domSession])

  async function generateCoach() {
    setCoachBusy(true)
    setCoachErr(null)
    try {
      const d = await api.generateCoach(domSession)
      setCoach(d.report)
    } catch (e) {
      setCoachErr(e.message)
    }
    setCoachBusy(false)
  }

  const repRows = useMemo(() => reportRollup(report, selIds), [report, selIds && selIds.join(',')])
  const derived = useMemo(() => damageDerived(detail?.abilities), [detail])
  const deaths = useMemo(() => deathRows(report, selIds), [report, selIds && selIds.join(',')])

  const actors = detail?.actors ?? []
  const duration = Math.max(detail?.encounter?.duration_s || 0, 1)
  const players = useMemo(() => actors.filter((a) => a.kind === 'player'), [actors])
  const raidDamage = players.reduce((s, a) => s + (a.damage || 0), 0)
  const topDamage = Math.max(...players.map((a) => a.damage || 0), 1)

  const visibleActors = useMemo(() => actors.filter((a) =>
    (a.damage || 0) > 0 || (a.heals || 0) > 0 || (a.damage_taken || 0) > 0
    || (a.wards_absorbed || 0) > 0 || (a.power_fed || 0) > 0), [actors])

  const selectedActor = actorQ && actors.some((a) => a.key === actorQ) ? actorQ : null
  const selName = actors.find((a) => a.key === selectedActor)?.name

  // checked-off combatants for comparison, order preserved in the URL
  const cmpList = useMemo(() => (cmpQ || '').split(',').filter(Boolean), [cmpQ])
  const cmpKeys = useMemo(() => new Set(cmpList), [cmpList])
  const toggleCmp = (key) => {
    const next = cmpKeys.has(key) ? cmpList.filter((k) => k !== key) : [...cmpList, key]
    setCmpQ(next.length ? next.join(',') : null)
  }
  const comparing = cmpList.length >= 2

  if (error) return <p className="err">{error}</p>
  if (!run || !encounters) return <p className="muted">Loading…</p>

  const enc = detail?.encounter
  const zoneLabel = run.zone || 'Unknown zone'
  const title = sel === 'all'
    ? zoneLabel
    : enc?.name || (selIds && selIds.length > 1 ? `${selIds.length} fights` : '…')

  const nameCol = {
    key: 'name', label: 'Name', align: 'l',
    render: (a) => <span className="name">{a.name}{kindBadge(a.kind)}</span>,
    sortValue: (a) => a.name,
  }
  const shareCol = {
    key: 'share', label: 'Dmg %', align: 'l',
    render: (a) => (a.kind === 'player' && a.damage > 0
      ? <span className="sharecell"><ShareBar value={a.damage} max={topDamage} />
          <span className="pctnum">{raidDamage ? `${((a.damage / raidDamage) * 100).toFixed(1)}%` : ''}</span></span>
      : null),
    sortValue: (a) => (a.kind === 'player' ? a.damage : -1),
  }
  const rep = (name, label, get, renderVal) => ({
    key: name, label,
    render: (a) => {
      const v = repRows?.[a.name] ? get(repRows[a.name]) : null
      return v != null && v !== 0 ? (renderVal ? renderVal(v) : fmt.num(v)) : ''
    },
    sortValue: (a) => (repRows?.[a.name] ? get(repRows[a.name]) : null),
  })

  const overviewCols = [
    nameCol,
    { key: 'damage', label: 'Damage', format: fmt.num },
    shareCol,
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
    { key: 'cure_count', label: 'Cures', render: (a) => a.cure_count || '' },
    { key: 'deaths', label: 'Deaths', render: (a) => a.deaths || '' },
    rep('engage', 'Engage', (n) => n.avg_engage_delay_s, (v) => `${v}s`),
    rep('dead_loss', 'Dmg lost dead', (n) => n.death_dps_lost),
    rep('overheal', 'Overheal', (n) => n.overheal_est),
    rep('saves', 'Saves', (n) => n.saves, (v) => v),
  ]

  const damageCols = [
    nameCol,
    { key: 'damage', label: 'Damage', format: fmt.num },
    shareCol,
    { key: 'dps', label: 'EncDPS', format: fmt.num },
    {
      key: 'crit', label: 'Crit %',
      render: (a) => { const v = critPct(derived[a.key]); return v != null ? `${Math.round(v)}%` : '' },
      sortValue: (a) => critPct(derived[a.key]),
    },
    {
      key: 'auto', label: 'Auto %',
      render: (a) => { const v = autoPct(derived[a.key]); return v != null ? `${Math.round(v)}%` : '' },
      sortValue: (a) => autoPct(derived[a.key]),
    },
    {
      key: 'cpm', label: 'Casts/min',
      render: (a) => { const v = castsPerMin(derived[a.key], duration); return v != null ? v.toFixed(1) : '' },
      sortValue: (a) => castsPerMin(derived[a.key], duration),
    },
    rep('engage', 'Engage', (n) => n.avg_engage_delay_s, (v) => `${v}s`),
    rep('dead_loss', 'Dmg lost dead', (n) => n.death_dps_lost),
    { key: 'deaths', label: 'Deaths', render: (a) => a.deaths || '' },
  ]

  const healingCols = [
    nameCol,
    { key: 'heals', label: 'Heals', format: fmt.num },
    {
      key: 'hps', label: 'EncHPS',
      render: (a) => fmt.num((a.heals || 0) / duration),
      sortValue: (a) => (a.heals || 0) / duration,
    },
    rep('overheal', 'Overheal', (n) => n.overheal_est),
    {
      key: 'overheal_pct', label: 'Overheal %',
      render: (a) => {
        const n = repRows?.[a.name]
        const healed = (a.heals || 0) + (n?.overheal_est || 0)
        return healed && n?.overheal_est ? `${Math.round((100 * n.overheal_est) / healed)}%` : ''
      },
      sortValue: (a) => {
        const n = repRows?.[a.name]
        const healed = (a.heals || 0) + (n?.overheal_est || 0)
        return healed && n?.overheal_est ? (100 * n.overheal_est) / healed : null
      },
    },
    rep('saves', 'Saves', (n) => n.saves, (v) => v),
    { key: 'wards_absorbed', label: 'Wards', format: fmt.num },
    { key: 'ward_bleedthrough', label: 'Bleedthrough', format: fmt.num },
    { key: 'cure_count', label: 'Cures', render: (a) => a.cure_count || '' },
    { key: 'power_fed', label: 'PowerRepl', format: fmt.num },
    { key: 'rez_casts', label: 'Rezzes', render: (a) => a.rez_casts || '' },
    rep('rez_delay', 'Rez delay', (n) => n.avg_rez_delay_s, (v) => `${v}s`),
  ]

  const defenseCols = [
    nameCol,
    { key: 'damage_taken', label: 'DmgTaken', format: fmt.num },
    { key: 'deaths', label: 'Deaths', render: (a) => a.deaths || '' },
    rep('time_dead', 'Time dead', (n) => n.time_dead_s, (v) => fmt.dur(v)),
    rep('dead_loss', 'Dmg lost dead', (n) => n.death_dps_lost),
    { key: 'power_drain', label: 'PowerDrain', format: fmt.num },
  ]

  const tabRows = {
    overview: visibleActors,
    damage: players.filter((a) => (a.damage || 0) > 0),
    healing: players.filter((a) =>
      (a.heals || 0) > 0 || (a.wards_absorbed || 0) > 0 || (a.cure_count || 0) > 0
      || (a.power_fed || 0) > 0 || (a.rez_casts || 0) > 0),
    defense: players.filter((a) => (a.damage_taken || 0) > 0 || (a.deaths || 0) > 0),
  }
  const tabCols = {
    overview: overviewCols, damage: damageCols, healing: healingCols, defense: defenseCols,
  }
  const tabSort = {
    overview: { key: 'dps', dir: 'desc' },
    damage: { key: 'dps', dir: 'desc' },
    healing: { key: 'hps', dir: 'desc' },
    defense: { key: 'damage_taken', dir: 'desc' },
  }

  const panelOpen = comparing || selectedActor
  return (
    <div className={`workspace ${panelOpen ? 'withpanel' : ''}`}>
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
            {report?.partial ? ' · partial (pruned)' : ''}
          </span>
        </div>
        <Tabs tabs={TABS} value={tab} onChange={(k) => setTab(k === 'overview' ? null : k)} />
        {detailErr && <p className="err">{detailErr}</p>}
        {!detail && !detailErr && tab !== 'insights' && <p className="muted">Loading…</p>}

        {detail && tab === 'overview' && (
          <div className="metrics">
            <div className="metric"><div className="v">{fmt.dur(enc.duration_s)}</div><div className="k">Combat</div></div>
            <div className="metric"><div className="v">{fmt.num(raidDamage)}</div><div className="k">Raid damage</div></div>
            <div className="metric"><div className="v">{fmt.num(raidDamage / duration)}</div><div className="k">Raid DPS</div></div>
            <div className="metric"><div className="v">{players.filter((p) => p.damage > 0 || p.heals > 0).length}</div><div className="k">Raiders</div></div>
            {run.named_count > 0 && sel === 'all' && (
              <div className="metric"><div className="v">{run.success_count}/{run.named_count}</div><div className="k">Named</div></div>
            )}
            {selIds.length > 1 && <div className="metric"><div className="v">{selIds.length}</div><div className="k">Fights</div></div>}
          </div>
        )}

        {detail && tab !== 'insights' && (
          <div className="card">
            {cmpList.length === 1 && (
              <p className="note" style={{ margin: '0 0 6px' }}>
                Check one more combatant to compare.
              </p>
            )}
            <SortableTable
              columns={tabCols[tab] || overviewCols}
              rows={tabRows[tab] || visibleActors}
              defaultSort={tabSort[tab] || tabSort.overview}
              rowKey={(a) => a.key}
              selectedKey={selectedActor}
              onRowClick={(a) => setActorQ(a.key === selectedActor ? null : a.key)}
              checkable={(a) => a.kind === 'player'}
              checkedKeys={cmpKeys}
              onCheck={toggleCmp}
            />
          </div>
        )}

        {detail && tab === 'defense' && deaths.length > 0 && (
          <div className="card">
            <h2>Deaths by fight</h2>
            <div className="tablewrap">
              <table className="data">
                <thead>
                  <tr><th className="l">Fight</th><th>Time</th><th className="l">Player</th><th>Deaths</th><th>Time dead</th><th>Dmg lost</th></tr>
                </thead>
                <tbody>
                  {deaths.map((d, i) => (
                    <tr key={i}>
                      <td className="name l">{d.encounter.name || 'trash'}</td>
                      <td>{fmt.time(d.encounter.started_ts)}</td>
                      <td className="l">{d.name}</td>
                      <td>{d.deaths}</td>
                      <td>{fmt.dur(d.time_dead_s)}</td>
                      <td>{fmt.num(d.death_dps_lost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === 'insights' && (
          <ErrorBoundary resetKey={`${id}:${coach?.generated_ts}`}>
            <Insights report={report} coach={coach} coachErr={coachErr}
                      busy={coachBusy} onGenerate={generateCoach} />
          </ErrorBoundary>
        )}

        {detail && tab === 'overview' && <DpsBars actors={actors} duration={duration} />}
      </div>

      {comparing && detail && (
        <ErrorBoundary resetKey={`cmp:${cmpQ}:${sel}`}>
          <ComparePanel
            actors={actors}
            keys={cmpList}
            derived={derived}
            repRows={repRows}
            duration={duration}
            onRemove={toggleCmp}
            onClear={() => setCmpQ(null)}
          />
        </ErrorBoundary>
      )}
      {!comparing && selectedActor && detail && (
        <ErrorBoundary resetKey={`actor:${selectedActor}:${sel}`}>
          <ActorPanel
            name={selName}
            abilities={detail.abilities}
            actorKey={selectedActor}
            duration={duration}
            onClose={() => setActorQ(null)}
          />
        </ErrorBoundary>
      )}
    </div>
  )
}
