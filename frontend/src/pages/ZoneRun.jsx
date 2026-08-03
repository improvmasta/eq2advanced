import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import ActorPanel from '../components/ActorPanel.jsx'
import ComparePanel from '../components/ComparePanel.jsx'
import DeathRecap from '../components/DeathRecap.jsx'
import EncounterTree from '../components/EncounterTree.jsx'
import ErrorBoundary from '../components/ErrorBoundary.jsx'
import { ActorName } from '../components/Identity.jsx'
import SelectionBar from '../components/SelectionBar.jsx'
import SortableTable from '../components/SortableTable.jsx'
import Tabs from '../components/Tabs.jsx'
import TimelineChart from '../components/TimelineChart.jsx'
import { api, fmt, peek, url } from '../lib/api.js'
import { CHART_COLORS, ROLES, ROLE_LABEL, classLabel, roleOf } from '../lib/classes.js'
import {
  autoPct, castsPerMin, consistency, critPct, damageDerived, deathRows, decompose,
  procPct, rankClass, reportRollup,
} from '../lib/stats.js'
import { useQueryState } from '../lib/useQueryState.js'

const PET_KINDS = new Set(['own_pet', 'swarm_pet', 'named_pet'])

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'damage', label: 'Damage' },
  { key: 'healing', label: 'Healing' },
  { key: 'defense', label: 'Defense' },
  { key: 'timeline', label: 'Timeline' },
  { key: 'insights', label: 'Insights' },
]

const healedOf = (a) => (a.heals || 0) + (a.wards_absorbed || 0)

/* What the engage clock was stopped by. A healer's fight starts with a heal;
   scoring only hostile actions read a templar's whole pull as absence. */
const ANCHOR_LABEL = {
  cast: 'cast start', ability: 'ability', autoattack: 'swing', pet: 'pet',
  heal: 'heal', cure: 'cure', rez: 'rez',
}

function kindBadge(kind) {
  if (kind === 'mob') return <span className="badge">mob</span>
  if (kind === 'other') return <span className="badge">env</span>
  if (PET_KINDS.has(kind)) return <span className="badge pet">pet</span>
  return null
}

const SEVERITY_CLASS = { warn: 'warn', opportunity: 'opportunity' }

/* ---------- Insights: whole raid first, then any player ---------- */

function raidCallouts(rows, duration) {
  const out = []
  const players = rows.filter((n) => n.damage > 0 || n.heals > 0)
  if (!players.length) return out
  const push = (severity, title, detail) => out.push({ severity, title, detail })

  const byDeaths = [...players].sort((a, b) => b.deaths - a.deaths)
  if (byDeaths[0]?.deaths >= 3) {
    push('warn', `${byDeaths[0].name} died ${byDeaths[0].deaths} times`,
      `${fmt.num(byDeaths[0].death_dps_lost)} damage lost while dead.`)
  }
  const byLost = [...players].sort((a, b) => (b.death_dps_lost || 0) - (a.death_dps_lost || 0))
  if (byLost[0]?.death_dps_lost > 0 && byLost[0].name !== byDeaths[0]?.name) {
    push('warn', `${byLost[0].name} lost the most damage to deaths`,
      `${fmt.num(byLost[0].death_dps_lost)} over ${fmt.dur(byLost[0].time_dead_s)} dead.`)
  }
  const engaged = players.filter((n) => n.avg_engage_delay_s != null && n.engage_samples >= 2)
  if (engaged.length) {
    const slow = [...engaged].sort((a, b) => b.avg_engage_delay_s - a.avg_engage_delay_s)[0]
    if (slow.avg_engage_delay_s >= 8) {
      push('opportunity', `${slow.name} averages ${slow.avg_engage_delay_s}s to engage`,
        `Across ${slow.engage_samples} named pulls.`)
    }
  }
  const healers = players.filter((n) => n.heals > 200_000)
  for (const h of healers) {
    if (h.overheal_pct != null && h.overheal_pct >= 45) {
      push('opportunity', `${h.name} overheals ${Math.round(h.overheal_pct)}%`,
        'Estimate from HP-deficit reconstruction.')
    }
  }
  const totalCures = players.reduce((s, n) => s + (n.cures || 0), 0)
  if (totalCures > 0) {
    const top = [...players].sort((a, b) => (b.cures || 0) - (a.cures || 0))[0]
    push('info', `${totalCures} cures — ${top.name} leads with ${top.cures}`, null)
  }
  return out
}

function Insights({
  run, report, selIds, coach, coachErr, busy, onGenerate, playerQ, setPlayerQ,
  actors, derived,
}) {
  const rows = useMemo(() => {
    // night rollup scoped to the selected fights
    if (!report) return []
    const roll = reportRollup(report, selIds) || {}
    const want = new Set(selIds || [])
    const base = {}
    for (const enc of report.encounters || []) {
      if (!want.has(enc.encounter.id)) continue
      for (const p of enc.players) {
        const n = base[p.name] ??= { name: p.name, damage: 0, heals: 0, wards: 0, cures: 0, encounters: 0 }
        n.damage += p.damage || 0
        n.heals += p.heals || 0
        n.wards += p.wards_absorbed || 0
        n.cures += p.cures || 0
        n.encounters += 1
      }
    }
    return Object.values(base).map((n) => {
      const r = roll[n.name] || {}
      const healed = n.heals + (r.overheal_est || 0)
      return {
        ...n,
        deaths: r.deaths || 0,
        time_dead_s: r.time_dead_s || 0,
        death_dps_lost: r.death_dps_lost || 0,
        overheal_est: r.overheal_est || 0,
        overheal_pct: healed ? (100 * (r.overheal_est || 0)) / healed : null,
        saves: r.saves || 0,
        avg_engage_delay_s: r.avg_engage_delay_s,
        engage_samples: r.engage?.length ?? 0,
        engage_low: r.engage_low || 0,
        avg_rez_delay_s: r.avg_rez_delay_s,
      }
    }).sort((a, b) => b.damage - a.damage)
  }, [report, selIds && selIds.join(',')])

  const duration = useMemo(() => {
    if (!report) return 1
    const want = new Set(selIds || [])
    return Math.max((report.encounters || [])
      .filter((e) => want.has(e.encounter.id))
      .reduce((s, e) => s + Math.max(e.encounter.duration_s, 1), 0), 1)
  }, [report, selIds && selIds.join(',')])

  const raidDamage = rows.reduce((s, n) => s + n.damage, 0)
  const totalDeaths = rows.reduce((s, n) => s + n.deaths, 0)
  const totalLost = rows.reduce((s, n) => s + n.death_dps_lost, 0)
  const callouts = useMemo(() => raidCallouts(rows, duration), [rows, duration])

  const selected = rows.find((n) => n.name === playerQ) || null
  const perFight = useMemo(() => {
    if (!selected || !report) return []
    const want = new Set(selIds || [])
    const out = []
    for (const enc of report.encounters || []) {
      if (!want.has(enc.encounter.id)) continue
      const p = enc.players.find((x) => x.name === selected.name)
      if (p) out.push({ enc: enc.encounter, p })
    }
    return out
  }, [selected, report, selIds && selIds.join(',')])

  // decomposition + consistency work off the aggregate actor rows, which carry
  // the class (and therefore the role that decides who counts as a peer)
  const players = useMemo(() => (actors || []).filter((a) => a.kind === 'player'), [actors])
  const selActor = players.find((a) => a.name === selected?.name) || null
  // time dead comes from the raid report; the aggregate's column is never written
  const deadOf = useMemo(() => {
    const by = new Map(rows.map((n) => [n.name, n.time_dead_s || 0]))
    return (a) => by.get(a.name) || 0
  }, [rows])
  const decomp = useMemo(() => {
    if (!selActor) return null
    const role = roleOf(selActor)
    const same = role ? players.filter((a) => roleOf(a) === role) : []
    return decompose(
      selActor, same.length >= 3 ? same : players, derived || {}, duration, deadOf)
  }, [selActor, players, derived, duration, deadOf])
  const peerLabel = selActor && roleOf(selActor)
    ? `other ${ROLE_LABEL[roleOf(selActor)].toLowerCase()}` : 'the rest of the raid'
  const spread = useMemo(
    () => consistency(perFight.map(({ p }) => p.damage_share_pct)), [perFight])

  if (!report) return <p className="muted">No report available for this run.</p>

  return (
    <>
      <div className="card">
        <h2>Raid</h2>
        <div className="metrics">
          <div className="metric"><div className="v">{fmt.dur(duration)}</div><div className="k">Combat</div></div>
          <div className="metric"><div className="v">{fmt.num(raidDamage)}</div><div className="k">Raid damage</div></div>
          <div className="metric"><div className="v">{fmt.num(raidDamage / duration)}</div><div className="k">Raid DPS</div></div>
          <div className="metric"><div className="v">{totalDeaths}</div><div className="k">Deaths</div></div>
          <div className="metric"><div className="v">{fmt.num(totalLost)}</div><div className="k">Dmg lost dead</div></div>
          <div className="metric"><div className="v">{rows.reduce((s, n) => s + n.cures, 0)}</div><div className="k">Cures</div></div>
        </div>
        {callouts.map((c, i) => (
          <div key={i} className={`finding ${SEVERITY_CLASS[c.severity] || ''}`}>
            <span className="badge">{c.severity}</span>
            <span><strong>{c.title}</strong>{c.detail ? ` — ${c.detail}` : ''}</span>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="drillhead">
          <h2>Player insights</h2>
          <select
            value={selected?.name || ''}
            onChange={(e) => setPlayerQ(e.target.value || null)}
            style={{ marginLeft: 'auto' }}
          >
            <option value="">Pick a raider…</option>
            {rows.map((n) => {
              const cls = players.find((a) => a.name === n.name)?.class
              return (
                <option key={n.name} value={n.name}>
                  {n.name}{cls ? ` — ${classLabel(cls)}` : ''}
                </option>
              )
            })}
          </select>
        </div>
        {!selected && <p className="muted">Any raider in the log — deaths, engagement, overheal, cures, fight by fight.</p>}
        {selected && (
          <>
            <div className="metrics">
              <div className="metric"><div className="v">{fmt.num(selected.damage)}</div><div className="k">Damage</div></div>
              <div className="metric"><div className="v">{raidDamage ? `${((selected.damage / raidDamage) * 100).toFixed(1)}%` : '—'}</div><div className="k">Share</div></div>
              <div className="metric"><div className="v">{fmt.num2(selected.damage / duration)}</div><div className="k">DPS</div></div>
              <div className="metric"><div className="v">{selected.deaths}</div><div className="k">Deaths</div></div>
              {selected.death_dps_lost > 0 && (
                <div className="metric"><div className="v">{fmt.num(selected.death_dps_lost)}</div><div className="k">Dmg lost dead</div></div>
              )}
              {selected.avg_engage_delay_s != null && (
                <div className="metric"><div className="v">{selected.avg_engage_delay_s}s</div><div className="k">Avg engage</div></div>
              )}
              {(selected.heals + selected.wards) > 0 && (
                <div className="metric"><div className="v">{fmt.num(selected.heals + selected.wards)}</div><div className="k">Healed</div></div>
              )}
              {selected.overheal_pct != null && selected.overheal_est > 0 && (
                <div className="metric"><div className="v">{Math.round(selected.overheal_pct)}%</div><div className="k">Overheal</div></div>
              )}
              {selected.cures > 0 && (
                <div className="metric"><div className="v">{selected.cures}</div><div className="k">Cures</div></div>
              )}
              {selected.avg_rez_delay_s != null && (
                <div className="metric"><div className="v">{selected.avg_rez_delay_s}s</div><div className="k">Rez delay</div></div>
              )}
            </div>
            {selected.engage_low > 0 && (
              <p className="note">
                {selected.engage_low} engage sample{selected.engage_low > 1 ? 's' : ''} flagged
                low-confidence (possible pre-pull buff proc).
              </p>
            )}
            {perFight.length > 0 && (
              <div className="tablewrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th className="l">Fight</th><th>Time</th><th>Damage</th><th>Share</th>
                      <th>Engage</th><th>Deaths</th><th>Cures</th>
                    </tr>
                  </thead>
                  <tbody>
                    {perFight.map(({ enc, p }, i) => (
                      <tr key={i}>
                        <td className="name l">{enc.name || 'trash'}</td>
                        <td>{fmt.time(enc.started_ts)}</td>
                        <td>{fmt.num(p.damage)}</td>
                        <td>{p.damage_share_pct != null ? `${p.damage_share_pct}%` : ''}</td>
                        <td title={p.engage_anchor
                          ? `first ${ANCHOR_LABEL[p.engage_anchor] || p.engage_anchor}`
                            + (p.engage_confidence === 'low'
                              ? ' — inside the opening 2s, could be a proc or a HoT tick' : '')
                          : undefined}
                        >{p.engage_delay_s != null ? `${p.engage_delay_s}s` : ''}</td>
                        <td>{p.deaths || ''}</td>
                        <td>{p.cures || ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {spread && (
              <p className="note" style={{ marginTop: 8 }}>
                Share of raid damage swings between {spread.min.toFixed(1)}% and{' '}
                {spread.max.toFixed(1)}% across {spread.n} fights
                {spread.cv != null && spread.cv > 0.35
                  ? ' — uneven enough that execution, not gear, is the likely gap.'
                  : ' — steady fight to fight.'}
              </p>
            )}
          </>
        )}
      </div>

      {decomp?.worst && (
        <div className="card">
          <div className="drillhead">
            <h2>Why {selected.name}&apos;s damage lands where it does</h2>
            <span className="muted">vs the best of {peerLabel}</span>
          </div>
          <p className="note">
            DPS is activity times hit size times crit rate, minus the time you spend dead.
            Comparing the parts says which one to work on — the biggest gap is{' '}
            <strong>{decomp.worst.label.toLowerCase()}</strong>.
          </p>
          <div className="tablewrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="l">Factor</th><th>{selected.name}</th><th>Best peer</th>
                  <th>Gap</th><th className="l">What it means</th>
                </tr>
              </thead>
              <tbody>
                {decomp.factors.map((f) => (
                  <tr key={f.key} className={f.key === decomp.worst.key ? 'selected' : ''}>
                    <td className="name l">{f.label}</td>
                    <td>{f.fmt(f.mine)}</td>
                    <td>{f.fmt(f.best)}</td>
                    <td className={f.gapPct >= 15 ? 'rank-low' : f.gapPct <= 2 ? 'rank-top' : ''}>
                      {f.gapPct > 0.5 ? `−${Math.round(f.gapPct)}%` : '—'}
                    </td>
                    <td className="l muted">{f.why}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {selected && selected.name === run?.character_name && (
        <CoachCard coach={coach} coachErr={coachErr} busy={busy} onGenerate={onGenerate} />
      )}
      {report?.caveats?.length > 0 && (
        <p className="note">{report.caveats.join(' ')}</p>
      )}
    </>
  )
}

function CoachCard({ coach, coachErr, busy, onGenerate }) {
  const cur = coach?.currencies
  const tiles = cur ? [
    ['Crit', cur.crit_pct != null ? `${Math.round(cur.crit_pct)}%` : null],
    ['Autoattack', cur.autoattack_pct != null ? `${Math.round(cur.autoattack_pct)}%` : null],
    ['Casts/min', cur.cpm != null ? cur.cpm.toFixed(1) : null],
    ['Idle', cur.idle_pct != null ? `${Math.round(cur.idle_pct)}%` : null],
    ['Cure latency', cur.cure_latency_self_s != null ? `${cur.cure_latency_self_s}s` : null],
  ].filter(([, v]) => v != null) : []

  if (coach == null) {
    return (
      <div className="card">
        <h2>Coach (this log's character)</h2>
        {coachErr && <p className="err">{coachErr}</p>}
        <p className="muted">Census-backed coaching — stat priorities and tier upgrades.</p>
        <button disabled={busy} onClick={onGenerate}>{busy ? 'Generating…' : 'Generate coach report'}</button>
      </div>
    )
  }
  return (
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
      {coach.stat_priorities?.length > 0 && (
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
      )}
      {coach.tier_upgrades?.length > 0 && (
        <>
          <h3 style={{ marginTop: 12 }}>Spell upgrades worth buying</h3>
          <p className="note">
            Scribing a higher tier of these raises the base damage the fit already
            measured on your own hits — ordered by what it would have been worth
            in this log.
          </p>
          <div className="tablewrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="l">Ability</th><th className="l">Tier</th>
                  <th>Damage gain</th><th>DPS gain</th>
                </tr>
              </thead>
              <tbody>
                {coach.tier_upgrades.slice(0, 12).map((u) => (
                  <tr key={u.ability}>
                    <td className="name l">{u.ability}</td>
                    <td className="l muted">{u.from_tier} → <strong>{u.to_tier}</strong></td>
                    <td>{fmt.num(u.damage_gain)}</td>
                    <td className="rank-top">+{u.dps_gain}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {coach.debuff_uplift?.length > 0 && (
        <p className="note" style={{ marginTop: 10 }}>
          Raid debuff uplift by damage school:{' '}
          {coach.debuff_uplift.map((d) => `${d.dtype} ×${d.uplift}`).join(' · ')} — how
          much harder your hits landed in the raid than against a dummy.
        </p>
      )}
      <CoachFit fit={coach.fit} />
      {coach.caveats?.length > 0 && <p className="note">{coach.caveats.join(' ')}</p>}
    </div>
  )
}

/* The measured-vs-expected table behind a toggle: it is the evidence for every
   number above, and it is also the first place to look when one of them seems
   wrong. */
function CoachFit({ fit }) {
  const [open, setOpen] = useState(false)
  const rows = (fit || []).filter((f) => f.coefficient != null)
  if (!rows.length) return null
  return (
    <>
      <button className="chip" style={{ marginTop: 10, cursor: 'pointer' }} onClick={() => setOpen((v) => !v)}>
        {open ? 'Hide' : 'Show'} per-ability fit ({rows.length})
      </button>
      {open && (
        <div className="tablewrap">
          <table className="data">
            <thead>
              <tr>
                <th className="l">Ability</th><th className="l">Tier</th><th>Hits</th>
                <th>Observed</th><th>Coefficient</th><th>Resists</th><th className="l">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.ability}>
                  <td className="name l">{f.ability}</td>
                  <td className="l muted">{f.tier_name || '—'}</td>
                  <td>{(f.noncrit_n || 0) + (f.crit_n || 0)}</td>
                  <td>{fmt.num(f.observed_mean)}</td>
                  <td>{f.coefficient}</td>
                  <td className={f.resists ? 'rank-low' : ''}>{f.resists || ''}</td>
                  <td className="l">
                    <span className={`badge conf-${f.confidence}`}>{f.confidence}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
  const [stale, setStale] = useState(false)
  const [sel, setSel] = useQueryState('sel', 'all')
  const [actorQ, setActorQ] = useQueryState('actor')
  const [tab, setTab] = useQueryState('tab', 'overview')
  const [cmpQ, setCmpQ] = useQueryState('cmp')
  const [playerQ, setPlayerQ] = useQueryState('player')
  const [q, setQ] = useQueryState('q')
  const [rolesQ, setRolesQ] = useQueryState('roles')
  const [showWipes, setShowWipes] = useQueryState('wipes')
  const [healedOpen, setHealedOpen] = useState(false)
  const [showNpcs, setShowNpcs] = useState(false)
  const [showPets, setShowPets] = useState(false)
  const [metric, setMetric] = useState('damage')
  const [timeline, setTimeline] = useState(null)
  const [timelineErr, setTimelineErr] = useState(null)
  const [recaps, setRecaps] = useState(null)
  const [recapIdx, setRecapIdx] = useState(null)

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

  const rawSelIds = useMemo(() => {
    if (!encounters) return null
    if (sel === 'all') return encounters.map((e) => e.id)
    const ids = sel.split(',').map(Number).filter((n) => Number.isFinite(n))
    return ids.length ? ids : encounters.map((e) => e.id)
  }, [encounters, sel])

  /* Wipes count by default — that is what ACT shows you, and a night where the
     raid wiped twice on Galiel IS a night with two Galiel wipes in it. The
     switch in the rail takes them out when you want to see the clean pulls on
     their own; selecting a wipe on purpose always shows it, so the filter can
     never leave you staring at an empty page. */
  const includeWipes = showWipes !== '0'
  const wipeIds = useMemo(
    () => new Set((encounters || []).filter((e) => e.success === 0).map((e) => e.id)),
    [encounters])
  const selIds = useMemo(() => {
    if (!rawSelIds) return null
    if (includeWipes || !wipeIds.size) return rawSelIds
    const kept = rawSelIds.filter((id) => !wipeIds.has(id))
    return kept.length ? kept : rawSelIds
  }, [rawSelIds, wipeIds, includeWipes])

  /* Fight selection, the checkbox half: `sel` holds the id list, so a set of
     merged pulls survives a reload and is shareable as a URL. Selecting every
     fight collapses back to 'all' — a 60-id query string that means "all" is
     the same page with a worse address. */
  const selSet = useMemo(() => new Set(rawSelIds || []), [rawSelIds])
  const selectFights = (ids) => {
    setActorQ(null)
    if (!ids.length || !encounters) return
    setSel(ids.length === encounters.length ? null : ids.join(','))
  }
  const toggleFights = (ids, on) => {
    const next = new Set(rawSelIds || [])
    for (const fid of ids) { if (on) next.add(fid); else next.delete(fid) }
    if (!next.size) return          // never leave the page with nothing counted
    selectFights(encounters.filter((e) => next.has(e.id)).map((e) => e.id))
  }

  /* Clicking a fight you have already opened repaints from the cache in the
     same frame — no "Loading…", no scroll jump. A fight you haven't opened
     keeps the previous numbers on screen, dimmed, instead of blanking the page
     for the length of a round trip: the columns and sort you were reading stay
     put, and only the values change under them. */
  useEffect(() => {
    if (!selIds || !selIds.length) { setDetail(null); return }
    let gone = false
    const hit = peek(url.agg(selIds))
    if (hit) { setDetail(hit); setDetailErr(null); setStale(false); return }
    setStale(true)
    setDetailErr(null)
    api.encountersAgg(selIds)
      .then((d) => { if (!gone) { setDetail(d); setStale(false) } })
      .catch((e) => { if (!gone) { setDetailErr(e.message); setDetail(null); setStale(false) } })
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

  useEffect(() => {
    if (tab !== 'timeline' || !selIds?.length) return
    let gone = false
    const hit = peek(url.timeline(selIds))
    setTimeline(hit)
    setTimelineErr(null)
    if (hit) return
    api.encountersTimeline(selIds)
      .then((d) => { if (!gone) setTimeline(d) })
      .catch((e) => { if (!gone) setTimelineErr(e.message) })
    return () => { gone = true }
  }, [tab, selIds && selIds.join(',')])

  useEffect(() => {
    if (tab !== 'defense' || !selIds?.length) return
    let gone = false
    // clear both: the deaths request is slower than /agg, so keeping the old
    // payload would render deaths from fights that are no longer selected
    const hit = peek(url.deaths(selIds))
    setRecaps(hit)
    setRecapIdx(null)
    if (hit) return
    api.encountersDeaths(selIds)
      .then((d) => { if (!gone) setRecaps(d) })
      .catch(() => { if (!gone) setRecaps(null) })
    return () => { gone = true }
  }, [tab, selIds && selIds.join(',')])

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
  // checking rows sums them in the header box; a second check is already the
  // ask to compare, so the table comes up with it — no extra button
  const checkedActors = useMemo(
    () => cmpList.map((k) => actors.find((a) => a.key === k)).filter(Boolean),
    [cmpList, actors])
  // gate on actors that exist in THIS selection — a checked raider who wasn't
  // in the fight you just clicked must not reserve an empty panel column
  const comparing = checkedActors.length >= 2
  const actorsByKey = useMemo(
    () => Object.fromEntries(actors.map((a) => [a.key, a])), [actors])
  // the chart plots what you checked; with nothing checked it opens on the top
  // few by damage so the tab is never an empty box
  const timelineKeys = useMemo(() => {
    if (cmpList.length) return cmpList.slice(0, CHART_COLORS.length)
    return (timeline?.series || [])
      .filter((s) => s.kind === 'player').slice(0, 5).map((s) => s.key)
  }, [cmpList, timeline])

  /* The summing calculator: whatever is checked, added up. Selecting the three
     mages answers "are they carrying their share together?" without exporting
     anything to a spreadsheet. */
  const selectionStats = useMemo(() => {
    const sum = (get) => checkedActors.reduce((s, a) => s + (get(a) || 0), 0)
    const dmg = sum((a) => a.damage)
    const heal = sum((a) => (a.heals || 0) + (a.wards_absorbed || 0))
    return [
      { k: 'DPS', v: dmg ? fmt.num2(dmg / duration) : null },
      { k: 'HPS', v: heal ? fmt.num2(heal / duration) : null },
      {
        k: '% of raid', v: dmg && raidDamage ? `${((dmg / raidDamage) * 100).toFixed(1)}%` : null,
        title: 'Combined share of raid DPS',
      },
      { k: 'Deaths', v: sum((a) => a.deaths) || null },
    ]
  }, [checkedActors, duration, raidDamage])

  const roleSet = useMemo(
    () => new Set((rolesQ || '').split(',').filter(Boolean)), [rolesQ])
  const toggleRole = (r) => {
    const next = new Set(roleSet)
    if (next.has(r)) next.delete(r); else next.add(r)
    setRolesQ(next.size ? [...next].join(',') : null)
  }
  const rolesPresent = useMemo(() => {
    const seen = new Set(players.map((a) => roleOf(a)).filter(Boolean))
    return ROLES.filter((r) => seen.has(r))
  }, [players])
  /* Search and role are view filters, not row semantics — they narrow whatever
     the active tab already decided to show. */
  const applyFilters = (rows) => {
    const needle = (q || '').trim().toLowerCase()
    return rows.filter((a) => {
      if (needle && !a.name.toLowerCase().includes(needle)
          && !(a.class || '').includes(needle)) return false
      if (roleSet.size && a.kind === 'player' && !roleSet.has(roleOf(a))) return false
      return true
    })
  }

  if (error) return <p className="err">{error}</p>
  if (!run || !encounters) return <p className="muted">Loading…</p>

  const enc = detail?.encounter
  const zoneLabel = run.zone || 'Unknown zone'
  const title = sel === 'all'
    ? zoneLabel
    : enc?.name || (selIds && selIds.length > 1 ? `${selIds.length} fights` : '…')

  /* Who gets a row. A pet's damage is already credited to its owner (ACT does
     the same), so a pet row can only ever carry what the pet TOOK — that is
     why "Tragedy's unswerving hammer" turns up owning nothing but a
     DmgTaken figure, and why it is off by default rather than gone: the
     defensive number is real, it just isn't a raider. Mobs and environment
     rows are the same kind of clutter behind their own switch. */
  const tabRows = {
    overview: applyFilters(visibleActors.filter((a) => {
      if (a.kind === 'player') return true
      if (PET_KINDS.has(a.kind)) return showPets
      return showNpcs
    })),
    damage: applyFilters(players.filter((a) => (a.damage || 0) > 0)),
    healing: applyFilters(players.filter((a) =>
      healedOf(a) > 0 || (a.cure_count || 0) > 0
      || (a.power_fed || 0) > 0 || (a.rez_casts || 0) > 0)),
    defense: applyFilters(players.filter((a) =>
      (a.damage_taken || 0) > 0 || (a.deaths || 0) > 0)),
  }
  const currentRows = tabRows[tab] || tabRows.overview

  /* ---------- shared cell helpers (tooltips live here) ---------- */

  /* A number alone says nothing — "34% crit" is good or bad only next to the
     people it should be compared against. Peers are the same-role raiders on
     screen, so the coloring answers to the current filter. */
  const rankAgainst = (get, opts) => (a) => {
    if (a.kind !== 'player') return ''
    const role = roleOf(a)
    const pool = role ? currentRows.filter((p) => roleOf(p) === role) : []
    const peers = pool.length >= 4 ? pool : currentRows.filter((p) => p.kind === 'player')
    return rankClass(get(a), peers.map(get), opts)
  }

  const damageTitle = (a) => {
    const d = derived[a.key]
    const parts = []
    if (d?.hits) parts.push(`crit ${Math.round(critPct(d))}%`)
    const ap = autoPct(d)
    if (ap != null) parts.push(`autoattack ${Math.round(ap)}%`)
    const cpm = castsPerMin(d, duration)
    if (cpm != null) parts.push(`${cpm.toFixed(1)} casts/min`)
    return parts.join(' · ') || undefined
  }
  const healedTitle = (a) => {
    const n = repRows?.[a.name]
    const parts = [`heals ${fmt.num(a.heals || 0)}`, `wards ${fmt.num(a.wards_absorbed || 0)}`]
    if (n?.overheal_est) parts.push(`overheal ${fmt.num(n.overheal_est)}`)
    if (a.ward_bleedthrough) parts.push(`bleedthrough ${fmt.num(a.ward_bleedthrough)}`)
    return parts.join(' · ')
  }
  const deathsTitle = (a) => {
    const n = repRows?.[a.name]
    if (!n?.deaths) return undefined
    return `dead ${fmt.dur(n.time_dead_s)} · ~${fmt.num(n.death_dps_lost)} damage lost`
  }
  /* Engage is a claim about someone's opener, so the tooltip says what it was
     measured from — first hit, first heal, first cure — and how many pulls are
     behind the average. A number this easy to argue with has to be able to
     answer "says who?". */
  const engageTitle = (a) => {
    const n = repRows?.[a.name]
    if (!n?.engage?.length) return undefined
    const mix = Object.entries(n.engage_anchors || {})
      .sort((x, y) => y[1] - x[1])
      .map(([k, v]) => `${v} × ${ANCHOR_LABEL[k] || k}`)
      .join(', ')
    const low = n.engage_low ? ` · ${n.engage_low} inside the opening 2s (may be a proc or a HoT tick)` : ''
    return `first action on ${n.engage.length} named pull${n.engage.length > 1 ? 's' : ''}`
      + `${mix ? `: ${mix}` : ''}${low}`
  }

  const nameCol = {
    key: 'name', label: 'Name', align: 'l',
    render: (a) => <ActorName actor={a} badge={kindBadge(a.kind)} />,
    sortValue: (a) => a.name,
  }
  const classCol = {
    key: 'class', label: 'Class', align: 'l',
    render: (a) => (a.class ? classLabel(a.class) : ''),
    sortValue: (a) => (a.class ? `${roleOf(a)} ${a.class}` : null),
  }
  const shareCol = {
    key: 'share', label: 'Dmg %',
    render: (a) => (a.kind === 'player' && a.damage > 0 && raidDamage
      ? `${Math.round((a.damage / raidDamage) * 100)}%` : ''),
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
  const engageCol = {
    key: 'engage', label: 'Engage',
    render: (a) => {
      const v = repRows?.[a.name]?.avg_engage_delay_s
      return v != null ? <span title={engageTitle(a)}>{v}s</span> : ''
    },
    sortValue: (a) => repRows?.[a.name]?.avg_engage_delay_s ?? null,
  }
  const damageCol = {
    key: 'damage', label: 'Damage',
    render: (a) => <span title={damageTitle(a)}>{fmt.num(a.damage)}</span>,
  }
  const healedCol = {
    key: 'healed',
    label: (
      <span>
        Healed{' '}
        <button
          className="expandcol"
          onClick={(e) => { e.stopPropagation(); setHealedOpen((v) => !v) }}
          title={healedOpen ? 'Collapse heal breakdown' : 'Expand into Heals / Wards / Overheal'}
        >{healedOpen ? '⊟' : '⊞'}</button>
      </span>
    ),
    render: (a) => (healedOf(a) ? <span title={healedTitle(a)}>{fmt.num(healedOf(a))}</span> : ''),
    sortValue: healedOf,
  }
  const healedBreakdown = healedOpen ? [
    { key: 'heals', label: '· Heals', render: (a) => (a.heals ? fmt.num(a.heals) : ''), sortValue: (a) => a.heals || 0 },
    { key: 'wards_absorbed', label: '· Wards', render: (a) => (a.wards_absorbed ? fmt.num(a.wards_absorbed) : ''), sortValue: (a) => a.wards_absorbed || 0 },
    rep('overheal', '· Overheal', (n) => n.overheal_est),
  ] : []
  const deathsCol = {
    key: 'deaths', label: 'Deaths',
    render: (a) => (a.deaths ? <span title={deathsTitle(a)}>{a.deaths}</span> : ''),
  }
  /* Time dead and rezzes belong next to the deaths that caused them, on the
     tab people actually land on — a death costs the raid twice, once in the
     damage nobody dealt and once in the healer who stopped healing to fix it,
     and neither cost is visible from a Deaths count alone. */
  const timeDeadCol = rep('time_dead', 'Time dead', (n) => n.time_dead_s, (v) => fmt.dur(v))
  const rezCol = { key: 'rez_casts', label: 'Rezzes', render: (a) => a.rez_casts || '' }

  const dpsOf = (a) => (a.damage || 0) / duration
  const dpsCol = {
    key: 'dps', label: 'DPS',
    render: (a) => (a.damage ? fmt.num2(dpsOf(a)) : ''),
    sortValue: dpsOf,
  }
  const hpsCol = {
    key: 'hps', label: 'HPS',
    render: (a) => (healedOf(a) ? fmt.num2(healedOf(a) / duration) : ''),
    sortValue: (a) => healedOf(a) / duration,
  }

  /* Rate first, everywhere. DPS is the number the tables get read for, so it
     sits where the eye lands after the name instead of behind the totals it
     summarizes; the healing tab leads with its own rate for the same reason. */
  const overviewCols = [
    nameCol,
    dpsCol,
    damageCol,
    shareCol,
    healedCol,
    ...healedBreakdown,
    hpsCol,
    { key: 'cure_count', label: 'Cures', render: (a) => a.cure_count || '' },
    { key: 'power_fed', label: 'PowerRepl', render: (a) => (a.power_fed ? fmt.num(a.power_fed) : '') },
    {
      key: 'damage_taken', label: 'DmgTaken',
      render: (a) => (a.damage_taken ? fmt.num(a.damage_taken) : ''),
    },
    deathsCol,
    timeDeadCol,
    rezCol,
    engageCol,
  ]

  const overhealPct = (a) => {
    const n = repRows?.[a.name]
    const healed = (a.heals || 0) + (n?.overheal_est || 0)
    return healed && n?.overheal_est ? (100 * n.overheal_est) / healed : null
  }
  const damageCols = [
    nameCol,
    classCol,
    { ...dpsCol, cellClass: rankAgainst(dpsOf) },
    damageCol,
    shareCol,
    {
      key: 'crit', label: 'Crit %',
      render: (a) => { const v = critPct(derived[a.key]); return v != null ? `${Math.round(v)}%` : '' },
      sortValue: (a) => critPct(derived[a.key]),
      cellClass: rankAgainst((a) => critPct(derived[a.key])),
    },
    {
      key: 'auto', label: 'Auto %',
      render: (a) => { const v = autoPct(derived[a.key]); return v != null ? `${Math.round(v)}%` : '' },
      sortValue: (a) => autoPct(derived[a.key]),
    },
    {
      key: 'proc', label: 'Proc %',
      render: (a) => { const v = procPct(derived[a.key]); return v != null && v > 0 ? `${Math.round(v)}%` : '' },
      sortValue: (a) => procPct(derived[a.key]),
    },
    {
      key: 'cpm', label: 'Casts/min',
      render: (a) => { const v = castsPerMin(derived[a.key], duration); return v != null ? v.toFixed(1) : '' },
      sortValue: (a) => castsPerMin(derived[a.key], duration),
      cellClass: rankAgainst((a) => castsPerMin(derived[a.key], duration)),
    },
    {
      key: 'avg_delay', label: 'AvgDelay',
      render: (a) => (a.avg_delay_s != null ? a.avg_delay_s.toFixed(2) : ''),
      sortValue: (a) => a.avg_delay_s ?? null,
    },
    rep('dead_loss', 'Dmg lost dead', (n) => n.death_dps_lost),
    deathsCol,
    timeDeadCol,
    engageCol,
  ]

  const healingCols = [
    nameCol,
    classCol,
    hpsCol,
    healedCol,
    ...healedBreakdown,
    ...(healedOpen ? [] : [
      { key: 'heals_plain', label: 'Heals', render: (a) => (a.heals ? fmt.num(a.heals) : ''), sortValue: (a) => a.heals || 0 },
      { key: 'wards_plain', label: 'Wards', render: (a) => (a.wards_absorbed ? fmt.num(a.wards_absorbed) : ''), sortValue: (a) => a.wards_absorbed || 0 },
    ]),
    {
      key: 'overheal_pct', label: 'Overheal %',
      render: (a) => {
        const n = repRows?.[a.name]
        const healed = (a.heals || 0) + (n?.overheal_est || 0)
        return healed && n?.overheal_est ? `${Math.round((100 * n.overheal_est) / healed)}%` : ''
      },
      sortValue: overhealPct,
      cellClass: rankAgainst(overhealPct, { worse: true }),
    },
    { key: 'ward_bleedthrough', label: 'Bleedthrough', render: (a) => (a.ward_bleedthrough ? fmt.num(a.ward_bleedthrough) : '') },
    { key: 'cure_count', label: 'Cures', render: (a) => a.cure_count || '' },
    { key: 'power_fed', label: 'PowerRepl', render: (a) => (a.power_fed ? fmt.num(a.power_fed) : '') },
    { key: 'rez_casts', label: 'Rezzes', render: (a) => a.rez_casts || '' },
    rep('rez_delay', 'Rez delay', (n) => n.avg_rez_delay_s, (v) => `${v}s`),
  ]

  const takenCol = {
    key: 'damage_taken', label: 'DmgTaken', format: fmt.num,
    cellClass: rankAgainst((a) => a.damage_taken || 0, { worse: true }),
  }
  const defenseCols = [
    nameCol,
    classCol,
    takenCol,
    deathsCol,
    timeDeadCol,
    rep('dead_loss', 'Dmg lost dead', (n) => n.death_dps_lost),
    rezCol,
    { key: 'power_drain', label: 'PowerDrain', render: (a) => (a.power_drain ? fmt.num(a.power_drain) : '') },
  ]

  const tabCols = {
    overview: overviewCols, damage: damageCols, healing: healingCols, defense: defenseCols,
  }
  /* With a drilldown open the raid table is a picker, not a report: it keeps
     the name and the one number the tab is sorted by, and hands the width to
     the player you actually opened. Every column comes back when you close it. */
  const leadCol = { overview: dpsCol, damage: dpsCol, healing: hpsCol, defense: takenCol }
  const tabSort = {
    overview: { key: 'dps', dir: 'desc' },
    damage: { key: 'dps', dir: 'desc' },
    healing: { key: 'hps', dir: 'desc' },
    defense: { key: 'damage_taken', dir: 'desc' },
  }

  const panelOpen = comparing || selectedActor

  /* The totals for what is checked — the head of the comparison column, and
     only that. One checked raider has nothing to add up, so it stays away
     until there are two. */
  const selHead = comparing ? (
    <SelectionBar
      head
      label={`${checkedActors.length} selected`}
      stats={selectionStats}
      onClear={() => setCmpQ(null)}
      chips={(
        <div className="selnames">
          {checkedActors.map((a) => (
            <button
              key={a.key}
              className="chip selname"
              title="Remove from selection"
              onClick={() => toggleCmp(a.key)}
            >
              {a.name} <span className="x">✕</span>
            </button>
          ))}
        </div>
      )}
    />
  ) : null

  return (
    <div className={`workspace ${panelOpen ? 'withpanel' : ''}`}>
      <EncounterTree
        encounters={encounters}
        sel={rawSelIds && rawSelIds.length === encounters.length ? 'all' : sel}
        onSelect={(key) => { setSel(key === 'all' ? null : key); setActorQ(null) }}
        selectedIds={selSet}
        onToggle={toggleFights}
        onSelectMany={selectFights}
        wipesShown={includeWipes}
        onWipes={(on) => setShowWipes(on ? null : '0')}
        sessionLabel={zoneLabel}
        hideZones
      />
      <div className={`wsmain ${stale && detail ? 'stale' : ''}`}>
        <div className="pagehead" style={{ marginTop: 0 }}>
          <h1>{title}</h1>
          <span className="sub">
            {fmt.dateLong(run.started_ts)}
            {` · ${fmt.timeRange(enc?.started_ts ?? run.started_ts, enc?.ended_ts ?? run.ended_ts)}`}
            {` · ${run.character_name}`}
            {report?.partial ? ' · partial (pruned)' : ''}
          </span>
          {!includeWipes && wipeIds.size > 0 && (
            <button
              className="chip toggle"
              style={{ marginLeft: 'auto' }}
              onClick={() => setShowWipes(null)}
              title="Put the wipes back into every total on this page"
            >
              {wipeIds.size} wipe{wipeIds.size === 1 ? '' : 's'} left out
            </button>
          )}
        </div>
        <Tabs tabs={TABS} value={tab} onChange={(k) => setTab(k === 'overview' ? null : k)} />
        {detailErr && <p className="err">{detailErr}</p>}
        {!detail && !detailErr && tab !== 'insights' && <p className="muted">Loading…</p>}
        {stale && detail && <div className="stalebar" aria-live="polite">Updating…</div>}

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

        {detail && tab !== 'insights' && tab !== 'timeline' && (
          <div className="card">
            <div className="filterbar">
              <input
                type="text" value={q || ''} placeholder="Find a raider…"
                onChange={(e) => setQ(e.target.value || null)}
                aria-label="Filter combatants by name or class"
              />
              {rolesPresent.map((r) => (
                <button
                  key={r}
                  className={`chip role ${roleSet.has(r) ? 'on' : ''}`}
                  onClick={() => toggleRole(r)}
                  title={`Show only ${ROLE_LABEL[r].toLowerCase()}`}
                >
                  {ROLE_LABEL[r]}
                </button>
              ))}
              {(roleSet.size > 0 || q) && (
                <button className="chip" onClick={() => { setRolesQ(null); setQ(null) }}>Reset</button>
              )}
              {tab === 'overview' && (
                <>
                  <label
                    className="chip toggle spacer"
                    title="Summoned pets get a row for what they TOOK — their damage is already counted under their owner"
                  >
                    <input
                      type="checkbox"
                      checked={showPets}
                      onChange={(e) => setShowPets(e.target.checked)}
                    /> Pets
                  </label>
                  <label className="chip toggle" title="Show mob and environment rows">
                    <input
                      type="checkbox"
                      checked={showNpcs}
                      onChange={(e) => setShowNpcs(e.target.checked)}
                    /> NPCs
                  </label>
                </>
              )}
            </div>
            <SortableTable
              columns={panelOpen
                ? [nameCol, leadCol[tab] || dpsCol]
                : (tabCols[tab] || overviewCols)}
              rows={currentRows}
              defaultSort={tabSort[tab] || tabSort.overview}
              rowKey={(a) => a.key}
              selectedKey={selectedActor}
              wrapClass={currentRows.length > 14 ? 'sticky' : ''}
              onRowClick={(a) => setActorQ(a.key === selectedActor ? null : a.key)}
              checkable={(a) => a.kind === 'player'}
              checkedKeys={cmpKeys}
              onCheck={toggleCmp}
            />
            {!currentRows.length && (
              <p className="muted">Nothing matches that filter.</p>
            )}
          </div>
        )}

        {detail && tab === 'timeline' && (
          <div className="card">
            <div className="drillhead">
              <h2>Over the fight</h2>
              <span className="muted">
                {checkedActors.length
                  ? `${checkedActors.length} checked`
                  : 'top raiders by damage — check rows on another tab to choose'}
              </span>
            </div>
            {timelineErr && <p className="err">{timelineErr}</p>}
            {!timeline && !timelineErr && <p className="muted">Loading…</p>}
            {timeline?.pruned && (
              <p className="muted">
                Timeline unavailable — this run&apos;s raw events were pruned. The totals
                in the other tabs come from frozen rollups and are unaffected.
              </p>
            )}
            {timeline && !timeline.pruned && (
              <>
                <TimelineChart
                  data={timeline}
                  keys={timelineKeys}
                  actorsByKey={actorsByKey}
                  metric={metric}
                  onMetric={setMetric}
                />
                {timeline.pruned_encounters > 0 && (
                  <p className="note">
                    {timeline.pruned_encounters} of the selected fights had their events
                    pruned and are missing from the plot.
                  </p>
                )}
              </>
            )}
          </div>
        )}

        {detail && tab === 'defense' && recaps?.deaths?.length > 0 && (
          <div className="card">
            <h2>Every death</h2>
            <p className="note">
              Pick one to see the last {recaps.window_s} seconds before it — what was
              landing, and what was healing.
            </p>
            <div className="tablewrap">
              <table className="data">
                <thead>
                  <tr>
                    <th className="l">Fight</th><th>Time</th><th className="l">Player</th>
                    <th>Damage taken</th><th>Healing</th><th />
                  </tr>
                </thead>
                <tbody>
                  {recaps.deaths.map((d, i) => (
                    <tr key={i} className={`clickable ${i === recapIdx ? 'selected' : ''}`}
                        onClick={() => setRecapIdx(i === recapIdx ? null : i)}>
                      <td className="name l">{d.encounter_name || 'trash'}</td>
                      <td>{fmt.time(d.ts)}</td>
                      <td className="l">
                        <ActorName actor={actorsByKey[d.key] || { name: d.name }} />
                      </td>
                      <td>{fmt.num(d.incoming_total)}</td>
                      <td>{fmt.num(d.healing_total)}</td>
                      <td><span className="chip">{i === recapIdx ? 'Hide' : 'Recap'}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {recaps.pruned_encounters > 0 && (
              <p className="note">
                {recaps.pruned_encounters} fight(s) had their events pruned — deaths in
                those are counted in the table above but have no recap.
              </p>
            )}
          </div>
        )}
        {detail && tab === 'defense' && recapIdx != null && recaps?.deaths?.[recapIdx] && (
          <ErrorBoundary resetKey={`recap:${recapIdx}:${sel}`}>
            <DeathRecap
              death={recaps.deaths[recapIdx]}
              windowS={recaps.window_s}
              onClose={() => setRecapIdx(null)}
            />
          </ErrorBoundary>
        )}
        {detail && tab === 'defense' && !recaps?.deaths?.length && deaths.length > 0 && (
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
          <ErrorBoundary resetKey={`${id}:${coach?.generated_ts}:${playerQ}`}>
            <Insights run={run} report={report} selIds={selIds}
                      coach={coach} coachErr={coachErr} busy={coachBusy}
                      onGenerate={generateCoach} actors={actors} derived={derived}
                      playerQ={playerQ} setPlayerQ={setPlayerQ} />
          </ErrorBoundary>
        )}

      </div>

      {comparing && detail && (
        // the column hugs its columns: a two-raider table has no business
        // spanning half a 4K screen
        <div className="cmpcol" style={{ maxWidth: 250 + checkedActors.length * 160 }}>
          {selHead}
          <ErrorBoundary resetKey={`cmp:${cmpQ}:${sel}`}>
            <ComparePanel
              actors={actors}
              keys={cmpList}
              derived={derived}
              repRows={repRows}
              duration={duration}
              onRemove={toggleCmp}
            />
          </ErrorBoundary>
        </div>
      )}
      {!comparing && selectedActor && detail && (
        <ErrorBoundary resetKey={`actor:${selectedActor}:${sel}`}>
          <ActorPanel
            key={selectedActor}
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
