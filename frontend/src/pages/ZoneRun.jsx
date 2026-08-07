import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import ActorPanel from '../components/ActorPanel.jsx'
import AoePanel from '../components/AoePanel.jsx'
import ClassPanel from '../components/ClassPanel.jsx'
import ComparePanel from '../components/ComparePanel.jsx'
import DeathList from '../components/DeathList.jsx'
import EncounterTree from '../components/EncounterTree.jsx'
import ShareDialog from '../components/ShareDialog.jsx'
import TankDeaths, { hasTankDeath } from '../components/TankDeaths.jsx'
import ErrorBoundary from '../components/ErrorBoundary.jsx'
import { ActorName } from '../components/Identity.jsx'
import SelectionBar from '../components/SelectionBar.jsx'
import SortableTable from '../components/SortableTable.jsx'
import Tabs from '../components/Tabs.jsx'
import TimelineChart from '../components/TimelineChart.jsx'
import { api, fmt, peek, url } from '../lib/api.js'
import { CHART_COLORS, ROLES, ROLE_LABEL, classLabel, roleOf } from '../lib/classes.js'
import {
  MIN_PEERS, autoPct, consistency, critPct, damageDerived, deathRows, decompose,
  procPct, rankColor, rankScale, rankTitle, reportRollup,
} from '../lib/stats.js'
import { useQueryState } from '../lib/useQueryState.js'

const PET_KINDS = new Set(['own_pet', 'swarm_pet', 'named_pet'])

/* Damage first: it is the tab everyone opens the page for, and an Overview
   that repeated four columns from each of the others was a stop on the way to
   the one you wanted. The metric block above the table carries what the
   Overview was actually read for, retuned per tab. */
/* Labels are the parser's own shorthand — a raider reads DPS/HPS/DEF faster
   than the words, and it is what the columns underneath are already called.
   The KEYS are untouched: ?tab=damage bookmarks and PANEL_KIND still work. */
const TABS = [
  { key: 'damage', label: 'DPS' },
  { key: 'healing', label: 'HPS' },
  { key: 'defense', label: 'DEF' },
  /* Dying is not a defensive statistic — it is the outcome the defensive ones
     were describing, and it reads as a list of events (who, in which fight,
     and what the last few seconds looked like) rather than a column. It had
     the bottom half of DEF; now it has the tab. */
  { key: 'deaths', label: 'DEATHS' },
  { key: 'aoes', label: 'AOE TIMERS' },
  { key: 'timeline', label: 'TIMELINE' },
  /* The stats only one class can answer — a troubador's buff uptime is not a
     column the other twenty-five classes can share. See ClassPanel.jsx. */
  { key: 'class', label: 'CLASS REPORT' },
  /* Insights is HIDDEN, not removed — the panel, the coach endpoint and the
     `tab === 'insights'` render below are all intact, and putting the entry
     back here is the whole of turning it on again. An old ?tab=insights
     bookmark lands on Damage while it is out, because `tab` is validated
     against this list. */
  // { key: 'insights', label: 'Insights' },
]

/* The page tab and a parse's kind tabs are the same question at two scales, so
   opening somebody from Healing opens their heals — landing on Damage and
   having to switch back was one click per raider you looked at. Only the tabs
   with a per-ability view map: from Defense or Timeline the panel keeps
   whatever it is on, which is Damage the first time. */
const PANEL_KIND = { damage: 'damage', healing: 'heal' }

/* Two windows, ONE request. A spike death is over in a couple of seconds, and
   twelve of them buried the moment that mattered under the whole pull — so the
   tank report looks at 5s and the raid list at 3s.
 
   The fetch asks for the wider of the two and the list narrows it in the
   browser, which is exact rather than approximate: `/deaths` caps each event
   list at DEATH_MAX_ENTRIES and keeps the TAIL, so the last 3s of a 5s window
   is always complete even when the 5s list was truncated. Two requests for two
   windows would have doubled the work to learn the same events twice. */
const TANK_WINDOW_S = 5
const RAID_WINDOW_S = 3

/* Offered, not shown. Each parse tab leads with its own rate and carries the
   other one folded away — the Columns menu is where you say you want it, and
   SortableTable remembers that answer for every raid you open next. Module
   scope so the array identity is stable: a fresh one per render would rebuild
   the table's hidden-column memo every time. */
const TAB_HIDDEN = { damage: ['hps'], healing: ['dps'] }

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
          <div className="metric"><div className="v">{fmt.durHMS(duration)}</div><div className="k">Combat</div></div>
          <div className="metric"><div className="v">{fmt.num(raidDamage)}</div><div className="k">Raid damage</div></div>
          <div className="metric"><div className="v">{fmt.num2(raidDamage / duration)}</div><div className="k">Raid DPS</div></div>
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
        {!selected && <p className="muted">Pick a raider.</p>}
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
                {selected.engage_low} engage sample{selected.engage_low > 1 ? 's' : ''}{' '}
                low-confidence (possible pre-pull proc).
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
                              ? ' (inside the opening 2s — could be a proc)' : '')
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
                Damage share {spread.min.toFixed(1)}%–{spread.max.toFixed(1)}% across{' '}
                {spread.n} fights{spread.cv != null && spread.cv > 0.35 ? ' (uneven)' : ''}.
              </p>
            )}
          </>
        )}
      </div>

      {decomp?.worst && (
        <div className="card">
          <div className="drillhead">
            <h2>Damage breakdown</h2>
            <span className="muted">vs the best of {peerLabel}</span>
          </div>
          <p className="note">
            Biggest gap: <strong>{decomp.worst.label.toLowerCase()}</strong>.
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
        <h2>Coach</h2>
        {coachErr && <p className="err">{coachErr}</p>}
        <p className="muted">Stat priorities and tier upgrades, from Census data.</p>
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
          <h3 style={{ marginTop: 12 }}>Spell upgrades</h3>
          <p className="note">Ordered by what the higher tier was worth in this log.</p>
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
          Raid debuff uplift vs dummy, by damage school:{' '}
          {coach.debuff_uplift.map((d) => `${d.dtype} ×${d.uplift}`).join(' · ')}
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

export default function ZoneRun({ user }) {
  const { id } = useParams()
  const navigate = useNavigate()
  const [sharing, setSharing] = useState(false)
  const [run, setRun] = useState(null)
  /* Everything the API sent, hidden fights included — they are the owner's and
     the rail lists them. `encounters` below is the parse: what the tables, the
     selection and every number on the page are made of. */
  const [allEncounters, setAllEncounters] = useState(null)
  const [editing, setEditing] = useState(false)
  const [editBusy, setEditBusy] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)   // whole-raid delete, armed
  const [editErr, setEditErr] = useState(null)
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
  const [tabQ, setTab] = useQueryState('tab', 'damage')
  // ?tab=overview is a real bookmark someone still has; land it on Damage
  const tab = TABS.some((t) => t.key === tabQ) ? tabQ : 'damage'
  const [cmpQ, setCmpQ] = useQueryState('cmp')
  const [playerQ, setPlayerQ] = useQueryState('player')
  const [q, setQ] = useQueryState('q')
  const [rolesQ, setRolesQ] = useQueryState('roles')
  // which class the Class tab is showing — in the URL, so "look at what the
  // troubs did on this night" is a link like every other selection here
  const [clsQ, setClsQ] = useQueryState('cls')
  const [healedOpen, setHealedOpen] = useState(false)
  const [showNpcs, setShowNpcs] = useState(false)
  const [showPets, setShowPets] = useState(false)
  const [metric, setMetric] = useState('damage')
  const [timeline, setTimeline] = useState(null)
  const [timelineErr, setTimelineErr] = useState(null)
  const [aoeData, setAoeData] = useState(null)
  const [aoeErr, setAoeErr] = useState(null)
  const [classData, setClassData] = useState(null)
  const [classErr, setClassErr] = useState(null)
  const [recaps, setRecaps] = useState(null)

  useEffect(() => {
    api.zoneRun(id)
      .then((d) => { setRun(d.zone_run); setAllEncounters(d.encounters) })
      .catch((e) => setError(e.message))
  }, [id])

  const encounters = useMemo(
    () => (allEncounters ? allEncounters.filter((e) => !e.hidden) : null),
    [allEncounters])

  /* A streaming raid grows under the page — new fights land in the rail and the
     Live pill has to come DOWN when the plugin stops. Only this one request
     repeats; the numbers panel keeps its own cache, so re-reading the run costs
     a row and refreshes the rail. */
  useEffect(() => {
    if (!run?.live) return
    const t = setInterval(() => {
      api.zoneRun(id)
        .then((d) => { setRun(d.zone_run); setAllEncounters(d.encounters) })
        .catch(() => {})
    }, 5000)
    return () => clearInterval(t)
  }, [id, run?.live])

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

  /* Fight selection, the checkbox half: `sel` holds the id list, so a set of
     merged pulls survives a reload and is shareable as a URL. Selecting every
     fight collapses back to 'all' — a 60-id query string that means "all" is
     the same page with a worse address. */
  const selSet = useMemo(() => new Set(selIds || []), [selIds])
  const selectFights = (ids) => {
    setActorQ(null)
    if (!ids.length || !encounters) return
    setSel(ids.length === encounters.length ? null : ids.join(','))
  }
  const toggleFights = (ids, on) => {
    const next = new Set(selIds || [])
    for (const fid of ids) { if (on) next.add(fid); else next.delete(fid) }
    if (!next.size) return          // never leave the page with nothing counted
    selectFights(encounters.filter((e) => next.has(e.id)).map((e) => e.id))
  }

  /* ---------- edit mode ----------
     One path for every edit: run it, then re-read the raid. Hiding a fight
     changes the run's totals, its roster and the guild those names were voted
     into — all derived on the server — so patching the row in the browser would
     leave a stale night on screen under a fresh rail. */
  const raidHidden = !!allEncounters?.length && encounters?.length === 0

  const applyEdit = async (fn) => {
    setEditBusy(true)
    setEditErr(null)
    try {
      await fn()
      const d = await api.zoneRun(id)
      setRun(d.zone_run)
      setAllEncounters(d.encounters)
      setSel(null)     // the selection can name a fight that just went away
      api.zoneRunReport(id).then(setReport).catch(() => setReport(null))
    } catch (e) {
      // deleting the last fight of a raid takes the raid with it
      if (e.status === 404) { navigate('/'); return }
      setEditErr(e.message)
    }
    setEditBusy(false)
  }

  const hideFights = (encs, hidden) => applyEdit(
    () => api.hideEncounters(encs.map((e) => e.id), hidden))
  const deleteFights = (encs) => applyEdit(
    () => api.deleteEncounters(encs.map((e) => e.id)))
  const deleteRaid = async () => {
    setEditBusy(true)
    setEditErr(null)
    try {
      await api.deleteZoneRun(id)
      navigate('/')
    } catch (e) { setEditErr(e.message); setEditBusy(false) }
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
    if (tab !== 'aoes' || !selIds?.length) return
    let gone = false
    const hit = peek(url.aoes(selIds))
    setAoeData(hit)
    setAoeErr(null)
    if (hit) return
    api.encountersAoes(selIds)
      .then((d) => { if (!gone) setAoeData(d) })
      .catch((e) => { if (!gone) setAoeErr(e.message) })
    return () => { gone = true }
  }, [tab, selIds && selIds.join(',')])

  useEffect(() => {
    if (tab !== 'class' || !selIds?.length) return
    let gone = false
    const hit = peek(url.classStats(selIds))
    setClassData(hit)
    setClassErr(null)
    if (hit) return
    api.encountersClassStats(selIds)
      .then((d) => { if (!gone) setClassData(d) })
      .catch((e) => { if (!gone) setClassErr(e.message) })
    return () => { gone = true }
  }, [tab, selIds && selIds.join(',')])

  useEffect(() => {
    if (tab !== 'deaths' || !selIds?.length) return
    let gone = false
    // clear it: the deaths request is slower than /agg, so keeping the old
    // payload would render deaths from fights that are no longer selected
    const hit = peek(url.deaths(selIds, TANK_WINDOW_S))
    setRecaps(hit)
    if (hit) return
    api.encountersDeaths(selIds, TANK_WINDOW_S)
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
  /* Self-inflicted damage (the Bloodthirsty Choker's Vampiric Requiem, and
     every other cost-you-HP effect) is NOT damage taken — ACT excludes it from
     both Damage and DamageTaken and so does the roller. It is still real HP a
     healer had to cover, so the DmgTaken column marks it with a * and hands
     over the number on hover rather than pretending it never happened. */
  const selfDamage = useMemo(() => {
    const by = {}
    for (const r of detail?.abilities || []) {
      if (r.kind !== 'self') continue
      const k = r.rollup_key || r.source_key
      if (k) by[k] = (by[k] || 0) + (r.total || 0)
    }
    return by
  }, [detail])
  const deaths = useMemo(() => deathRows(report, selIds), [report, selIds && selIds.join(',')])

  const actors = detail?.actors ?? []
  const duration = Math.max(detail?.encounter?.duration_s || 0, 1)
  const players = useMemo(() => actors.filter((a) => a.kind === 'player'), [actors])
  /* The three raid-wide denominators, together and BEFORE their first reader:
     the selection bar's "% of raid" picks one by tab, and it renders above the
     header block that also uses them. */
  const raidDamage = players.reduce((s, a) => s + (a.damage || 0), 0)
  const raidHealed = players.reduce((s, a) => s + healedOf(a), 0)
  const raidTaken = players.reduce((s, a) => s + (a.damage_taken || 0), 0)

  // checked-off combatants for comparison, order preserved in the URL
  const cmpList = useMemo(() => (cmpQ || '').split(',').filter(Boolean), [cmpQ])
  const cmpKeys = useMemo(() => new Set(cmpList), [cmpList])
  /* The panel follows the checks — an explicit ?actor is only how a mob or a
     pet row (which has no checkbox) opens one. Clicking a name and ticking a
     box are DIFFERENT gestures: a click is "show me this one instead", a tick
     is "and this one too". */
  const soloActor = cmpList.length === 1 && actors.some((a) => a.key === cmpList[0])
    ? cmpList[0] : null
  const selectedActor = actorQ && actors.some((a) => a.key === actorQ) ? actorQ : soloActor
  const selName = actors.find((a) => a.key === selectedActor)?.name
  // this page's fight selection as a Compare-page token ('.' joins ids there —
  // ',' is the column separator and '+' reads as a space in a query string)
  const cmpSel = sel === 'all' ? 'all' : (selIds || []).join('.')

  const toggleCmp = (key) => {
    const had = cmpKeys.has(key)
    const next = had ? cmpList.filter((k) => k !== key) : [...cmpList, key]
    setCmpQ(next.length ? next.join(',') : null)
    /* Hand the panel back to the checks: unticking the raider on screen closes
       theirs, and ticking anyone drops a mob or pet drilldown that was open —
       either way what is checked is what you are looking at. */
    if (!had || actorQ === key) setActorQ(null)
  }
  /* Clicking a raider's name REPLACES what the panel is showing — reading down
     a raid table is one parse after another, and having each click add a
     column meant three names in and the parse you wanted was a third of a
     screen wide. Adding is the checkbox's job, and only its job. Clicking the
     one already open closes it, so a click still undoes itself. */
  const focusActor = (key) => {
    setActorQ(null)
    const only = cmpList.length === 1 && cmpList[0] === key
    setCmpQ(only ? null : key)
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
  /* The chart plots what you checked; with nothing checked it opens on the top
     few for the METRIC you are looking at — switching to Healing and being
     shown the top five damage dealers (who heal nothing) was an empty chart
     with lines in it. */
  const timelineKeys = useMemo(() => {
    if (cmpList.length) return cmpList.slice(0, CHART_COLORS.length)
    const field = ['heals', 'taken'].includes(metric) ? metric : 'damage'
    return (timeline?.series || [])
      .filter((s) => s.kind === 'player')
      .map((s) => ({ key: s.key, v: (s[field] || []).reduce((a, b) => a + b, 0) }))
      .filter((s) => s.v > 0)
      .sort((a, b) => b.v - a.v)
      .slice(0, 5)
      .map((s) => s.key)
  }, [cmpList, timeline, metric])

  /* The summing calculator: whatever is checked, added up. Selecting the three
     mages answers "are they carrying their share together?" without exporting
     anything to a spreadsheet. */
  const selectionStats = useMemo(() => {
    const sum = (get) => checkedActors.reduce((s, a) => s + (get(a) || 0), 0)
    const dmg = sum((a) => a.damage)
    const heal = sum((a) => (a.heals || 0) + (a.wards_absorbed || 0))
    const taken = sum((a) => a.damage_taken)
    /* "% of raid" is a share of whatever the TAB is about. Checking three
       healers on the Healing tab and being told their share of raid DAMAGE is
       a true number answering a question nobody asked. */
    const shares = {
      damage: ['damage', dmg, raidDamage],
      healing: ['healing', heal, raidHealed],
      defense: ['damage taken', taken, raidTaken],
    }
    const [what, part, whole] = shares[tab] || shares.damage
    const dps = { k: 'DPS', v: dmg ? fmt.num2(dmg / duration) : null }
    const hps = { k: 'HPS', v: heal ? fmt.num2(heal / duration) : null }
    return [
      // the tab's own rate leads; the other one still shows if it is non-zero
      ...(tab === 'healing' ? [hps, dps] : [dps, hps]),
      {
        k: '% of raid', v: part && whole ? `${((part / whole) * 100).toFixed(1)}%` : null,
        title: `Combined share of raid ${what}`,
      },
      { k: 'Deaths', v: sum((a) => a.deaths) || null },
    ]
  }, [checkedActors, duration, tab, raidDamage, raidHealed, raidTaken])

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

  /* Who gets a row, in two independent parts: the SWITCHES say which kinds of
     combatant may appear at all, and the tab says what a row has to carry to
     earn its place. Both off — the default — and every tab is the raid, which
     is what these tables were before the switches reached past Defense.

     A mob is a combatant with a real parse: the boss's damage, what it healed
     itself for, what the raid put into it. Clicking its row opens that parse
     in the panel exactly like a raider's. What a mob does NOT get is a share
     of the raid denominators or a rank color — those are questions about
     raiders — so those cells simply stay blank on its row.

     Pets are the quieter half. An owned pet's damage is already credited to
     its owner (ACT does the same), so its row usually carries only what it
     TOOK — that is why "Tragedy's unswerving hammer" turns up owning nothing
     but a DmgTaken figure. A dumbfire nobody owns keeps its own damage. */
  const kindAllowed = (a) => {
    if (a.kind === 'player') return true
    if (PET_KINDS.has(a.kind)) return showPets
    return showNpcs
  }
  const rowsFor = (carries) =>
    applyFilters(actors.filter((a) => kindAllowed(a) && carries(a)))

  const tabRows = {
    damage: rowsFor((a) => (a.damage || 0) > 0),
    healing: rowsFor((a) => healedOf(a) > 0 || (a.cure_count || 0) > 0
      || (a.power_fed || 0) > 0 || (a.rez_casts || 0) > 0),
    defense: rowsFor((a) => (a.damage_taken || 0) > 0
      || (a.kind === 'player' && (a.deaths || 0) > 0)),
    // who it happened to and who picked them back up — a raider who neither
    // died nor cast a rez has nothing to say on this tab, and neither does a
    // mob: a death is only counted against a player or their pet
    deaths: rowsFor((a) => (a.deaths || 0) > 0 || (a.rez_casts || 0) > 0),
  }
  const currentRows = tabRows[tab] || tabRows.damage

  /* ---------- shared cell helpers (tooltips live here) ---------- */

  /* A number alone says nothing — "34% crit" is good or bad only next to the
     people it should be compared against. Peers are the same-role raiders on
     screen, so the coloring answers to the current filter, and the tint is the
     row's PLACE in that group (see rankScale).

     A row with no role gets no color. It used to fall back to the whole raid's
     median, which meant one column carried up to four different yardsticks at
     once — a healer judged against healers sat beside an unclassified raider
     judged against everybody, and the reader had no way to tell which was
     which. A third of the roster has no class (Census covers about half the
     ability names), so that fallback was not an edge case. Same for a group
     under MIN_PEERS: three tanks are not a standing. */
  const rankPool = (a) => {
    if (a.kind !== 'player') return null
    const role = roleOf(a)
    if (!role) return null
    const pool = currentRows.filter((p) => roleOf(p) === role)
    return pool.length >= MIN_PEERS ? { pool, label: ROLE_LABEL[role].toLowerCase() } : null
  }
  const rankAgainst = (get, opts) => (a) => {
    const group = rankPool(a)
    if (!group) return undefined
    const color = rankColor(rankScale(get(a), group.pool.map(get), opts))
    return color ? { color } : undefined
  }
  /* Say what the color means where it is: "3rd of 7 healers" is checkable
     against the column the reader is already looking at. */
  const rankTitleAgainst = (get) => (a) => {
    const group = rankPool(a)
    return group ? rankTitle(get(a), group.pool.map(get), group.label) : undefined
  }

  const damageTitle = (a) => {
    const d = derived[a.key]
    const parts = []
    if (d?.hits) parts.push(`crit ${Math.round(critPct(d))}%`)
    const ap = autoPct(d)
    if (ap != null) parts.push(`autoattack ${Math.round(ap)}%`)
    return parts.join(' · ') || undefined
  }
  const takenTitle = (a) => {
    const self = selfDamage[a.key]
    const parts = [`${fmt.num(a.damage_taken || 0)} taken from enemies`]
    if (self) parts.push(`${fmt.num(self)} self-inflicted (choker and the like) — not counted`)
    // this cell renders its own tooltip, so the rank has to join it rather
    // than sit on the td underneath where it would never be seen
    const rank = rankTitleAgainst((p) => p.damage_taken || 0)(a)
    if (rank) parts.push(rank)
    return parts.join(' · ')
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

  /* Fixed: the name is what every other cell is about, so it never moves and
     never hides. Everything else is the reader's to arrange. */
  const nameCol = {
    key: 'name', label: 'Name', align: 'l', fixed: true,
    render: (a) => <ActorName actor={a} badge={kindBadge(a.kind)} />,
    sortValue: (a) => a.name,
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
    key: 'healed', menuLabel: 'Healed',
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
  /* "X intercepted some of the damage intended for you!" — a hit somebody
     else was supposed to take. The log never says how much, so this is a
     count and the tooltip has to say why there is no number next to it. */
  const interceptCol = {
    key: 'intercepts', label: 'Intercepts',
    render: (a) => (a.intercepts
      ? <span title="Hits taken for someone else. The log does not say how much damage was moved.">
          {a.intercepts}
        </span>
      : ''),
    sortValue: (a) => a.intercepts || 0,
  }

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

  const overhealPct = (a) => {
    const n = repRows?.[a.name]
    const healed = (a.heals || 0) + (n?.overheal_est || 0)
    return healed && n?.overheal_est ? (100 * n.overheal_est) / healed : null
  }
  /* Rate first, everywhere. DPS is the number the tables get read for, so it
     sits where the eye lands after the name instead of behind the totals it
     summarizes; the healing tab leads with its own rate for the same reason.
     Class is gone as a column — ActorName already carries the class chip, so
     it was the same fact printed twice across the widest table on the page. */
  const damageCols = [
    nameCol,
    { ...dpsCol, cellStyle: rankAgainst(dpsOf), cellTitle: rankTitleAgainst(dpsOf) },
    /* The other tab's rate, hidden by default and one tick away: a shadowknight
       who healed 400k while topping the parse is a fact about the DAMAGE tab,
       and reading it meant switching tabs and finding the row again. Next to
       the rate it belongs beside, so turning it on reads as one pair. */
    hpsCol,
    damageCol,
    shareCol,
    {
      key: 'crit', label: 'Crit %',
      render: (a) => { const v = critPct(derived[a.key]); return v != null ? `${Math.round(v)}%` : '' },
      sortValue: (a) => critPct(derived[a.key]),
      cellStyle: rankAgainst((a) => critPct(derived[a.key])),
      cellTitle: rankTitleAgainst((a) => critPct(derived[a.key])),
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
      key: 'avg_delay', label: 'AvgDelay',
      render: (a) => (a.avg_delay_s != null ? a.avg_delay_s.toFixed(2) : ''),
      sortValue: (a) => a.avg_delay_s ?? null,
    },
    /* ACT's AvgDelay is the gap between things LANDING, so a DoT ticking six
       times and an AoE hitting six mobs read as six actions. This one counts
       activations instead — the gap between button presses. */
    {
      key: 'avg_delay_adj', label: 'AvgDelay adj',
      render: (a) => (
        a.avg_delay_adj_s != null
          ? <span title={`${a.presses} activations — DoT ticks and extra AoE targets folded in`}>
              {a.avg_delay_adj_s.toFixed(2)}
            </span>
          : ''),
      sortValue: (a) => a.avg_delay_adj_s ?? null,
    },
    /* The cost of dying reads in that order: it happened, it lasted this long,
       and this is what it took off the parse. */
    deathsCol,
    timeDeadCol,
    rep('dead_loss', 'Dmg lost dead', (n) => n.death_dps_lost),
    engageCol,
  ]

  const healingCols = [
    nameCol,
    hpsCol,
    dpsCol,                       // default-hidden, same bargain as HPS on Damage
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
      cellStyle: rankAgainst(overhealPct, { worse: true }),
      cellTitle: rankTitleAgainst(overhealPct),
    },
    { key: 'ward_bleedthrough', label: 'Bleedthrough', render: (a) => (a.ward_bleedthrough ? fmt.num(a.ward_bleedthrough) : '') },
    { key: 'cure_count', label: 'Cures', render: (a) => a.cure_count || '' },
    { key: 'power_fed', label: 'PowerRepl', render: (a) => (a.power_fed ? fmt.num(a.power_fed) : '') },
    { key: 'rez_casts', label: 'Rezzes', render: (a) => a.rez_casts || '' },
    rep('rez_delay', 'Rez delay', (n) => n.avg_rez_delay_s, (v) => `${v}s`),
  ]

  const takenCol = {
    key: 'damage_taken', label: 'DmgTaken',
    render: (a) => (
      <span title={takenTitle(a)}>
        {a.damage_taken ? fmt.num(a.damage_taken) : ''}
        {selfDamage[a.key] ? <span className="selfmark">*</span> : null}
      </span>
    ),
    sortValue: (a) => a.damage_taken || 0,
    cellStyle: rankAgainst((a) => a.damage_taken || 0, { worse: true }),
  }
  /* Defense is what the raid ATE and what it stopped. What dying then cost is
     the Deaths tab's subject, not a set of columns tacked on the end here. */
  const defenseCols = [
    nameCol,
    takenCol,
    interceptCol,
    { key: 'power_drain', label: 'PowerDrain', render: (a) => (a.power_drain ? fmt.num(a.power_drain) : '') },
  ]

  /* The cost of dying reads in that order: it happened, it lasted this long,
     this is what it took off the parse — and who got them back up. */
  const deathsCols = [
    nameCol,
    deathsCol,
    timeDeadCol,
    rep('dead_loss', 'Dmg lost dead', (n) => n.death_dps_lost),
    rezCol,
    rep('rez_delay', 'Rez delay', (n) => n.avg_rez_delay_s, (v) => `${v}s`),
  ]

  const tabCols = {
    damage: damageCols, healing: healingCols, defense: defenseCols, deaths: deathsCols,
  }
  /* With a drilldown open the raid table is a picker, not a report: it keeps
     the name and the one number the tab is sorted by, and hands the width to
     the player you actually opened. Every column comes back when you close it. */
  const leadCol = {
    damage: dpsCol, healing: hpsCol, defense: takenCol, deaths: deathsCol,
  }
  const tabSort = {
    damage: { key: 'dps', dir: 'desc' },
    healing: { key: 'hps', dir: 'desc' },
    defense: { key: 'damage_taken', dir: 'desc' },
    deaths: { key: 'deaths', dir: 'desc' },
  }

  const panelOpen = comparing || selectedActor
  /* The one close gesture. A raider's parse opens two ways — click the row (or
     its checkbox), which is a CHECK, or ?actor for a mob or pet, which has no
     checkbox — and the ✕ used to only know about the second: clearing actorQ
     did nothing when the panel was standing on a single ticked raider, so the
     button looked broken on the commonest path into the panel. Closing means
     closing, whichever way it was opened. */
  const closePanel = () => {
    setActorQ(null)
    if (cmpList.length) setCmpQ(null)
  }

  /* The header block stays — it is the one place the raid is a single number
     instead of a table — but what it counts follows the tab you are on. */
  const sumRep = (get) => Object.values(repRows || {})
    .reduce((s, n) => s + (get(n) || 0), 0)
  const raidHeals = players.reduce((s, a) => s + (a.heals || 0), 0)
  const raidOverheal = sumRep((n) => n.overheal_est)
  const raidSelf = players.reduce((s, a) => s + (selfDamage[a.key] || 0), 0)
  const raidCures = players.reduce((s, a) => s + (a.cure_count || 0), 0)
  const totalDeaths = players.reduce((s, a) => s + (a.deaths || 0), 0)
  /* Two clocks lead every tab, and the gap between them is the point: how long
     the night took from first pull to last, and how much of that was combat.
     Wall-clock can never be shorter than the fights inside it — a selection of
     fights out of a longer run is still bounded by its own ends. */
  const rawSpan = (enc?.ended_ts ?? run.ended_ts) - (enc?.started_ts ?? run.started_ts)
  const raidSpan = Number.isFinite(rawSpan) ? Math.max(rawSpan, duration) : duration
  const timeTiles = [
    {
      k: 'Raid time', v: fmt.durHMS(raidSpan),
      title: 'First pull to last — combat and everything in between',
    },
    { k: 'Combat', v: fmt.durHMS(duration), title: 'The fights themselves, added up' },
  ]
  const extraTiles = selIds.length > 1 ? [{ k: 'Fights', v: selIds.length }] : []
  const damageTiles = [
    ...timeTiles,
    /* Rates carry two decimals everywhere, header included — this is the
       number people paste next to an ACT screenshot, and ACT prints
       EncDPS/EncHPS to two places. */
    { k: 'Raid DPS', v: fmt.num2(raidDamage / duration) },
    { k: 'Raid damage', v: fmt.num(raidDamage) },
    { k: 'Raiders', v: players.filter((p) => p.damage > 0 || p.heals > 0).length },
    ...extraTiles,
  ]
  const aoeRows = aoeData?.aoes || []
  const aoeCasts = aoeRows.reduce((s, a) => s + a.casts, 0)
  const aoeTargets = aoeRows.reduce(
    (s, a) => s + a.cast_list.reduce((t, c) => t + c.targets, 0), 0)
  const aoeBlocked = aoeRows.reduce((s, a) => s + a.blocked, 0)
  const headTiles = {
    damage: damageTiles,
    timeline: damageTiles,
    aoes: [
      ...timeTiles,
      { k: 'AoEs', v: aoeRows.length },
      { k: 'Casts', v: aoeCasts },
      { k: 'AoE damage', v: fmt.num(aoeRows.reduce((s, a) => s + a.damage, 0)) },
      {
        k: 'Covered', title: 'Share of AoE hits avoided or absorbed',
        v: aoeTargets ? `${Math.round((100 * aoeBlocked) / aoeTargets)}%` : '—',
      },
      ...extraTiles,
    ],
    healing: [
      ...timeTiles,
      { k: 'Raid healed', v: fmt.num(raidHealed), title: 'Heals plus wards absorbed' },
      { k: 'Raid HPS', v: fmt.num2(raidHealed / duration) },
      {
        k: 'Overheal', title: 'Estimated from HP-deficit reconstruction',
        v: raidHeals + raidOverheal
          ? `${Math.round((100 * raidOverheal) / (raidHeals + raidOverheal))}%` : '—',
      },
      { k: 'Cures', v: raidCures },
      ...extraTiles,
    ],
    defense: [
      ...timeTiles,
      { k: 'Damage taken', v: fmt.num(raidTaken) },
      ...(raidSelf ? [{
        k: 'Self-inflicted', v: fmt.num(raidSelf),
        title: 'Chokers and other costs you pay yourself — not counted as damage taken',
      }] : []),
      ...extraTiles,
    ],
    deaths: [
      ...timeTiles,
      { k: 'Deaths', v: totalDeaths },
      { k: 'Time dead', v: fmt.dur(sumRep((n) => n.time_dead_s)) },
      {
        k: 'Dmg lost dead', v: fmt.num(sumRep((n) => n.death_dps_lost)),
        title: 'What the raid would have done over the time it spent dead',
      },
      { k: 'Rezzes', v: players.reduce((s, a) => s + (a.rez_casts || 0), 0) },
      ...extraTiles,
    ],
    class: [
      ...timeTiles,
      { k: 'Classes', v: classData?.classes?.length ?? '—' },
      {
        k: 'Raiders', v: classData
          ? classData.classes.reduce((s, c) => s + c.actors.length, 0) : '—',
        title: 'Players whose class this parse could pin',
      },
      ...extraTiles,
    ],
  }

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
    <div className={`workspace ${panelOpen ? 'withpanel' : ''}${comparing && detail ? ' withcmp' : ''}`}>
      <EncounterTree
        /* the rail lists the hidden fights too — it is where their owner puts
           them back — and counts only the ones the rest of the page counts */
        encounters={allEncounters}
        sel={selIds && selIds.length === encounters.length ? 'all' : sel}
        onSelect={(key) => { setSel(key === 'all' ? null : key); setActorQ(null) }}
        selectedIds={selSet}
        onToggle={toggleFights}
        sessionLabel={zoneLabel}
        titled
        /* When it was: the short date and the clock, with the long date on the
           tooltip rather than wrapping the rail. */
        sub={`${fmt.date(run.started_ts)} · ${fmt.timeRange(run.started_ts, run.ended_ts)}`}
        /* Whose parse it is: the character, the guild the roster was voted
           into, and whether the night is still arriving. All facts, all
           outlined — the only pills in the head that are DECISIONS are the
           filled ones in `Seen by` below. */
        who={(
          <>
            <span className="whoname" title="The character whose parse this is">
              {run.character_name}
            </span>
            {run.guild && (
              <span className="badge guild" title="Majority guild of the roster, from Census">
                {run.guild}
              </span>
            )}
            {/* Beside the guild, because both caption WHO this parse is — and a
                raid still arriving is the first thing to know about the numbers
                on the page. */}
            {run.live && (
              <span className="badge live" title="Being streamed right now — the fights are still arriving">
                Live
              </span>
            )}
            {/* A shared night is somebody's own parse and reads as theirs; the
                character is already named beside this, so the badge only has to
                say the page is read-only. */}
            {run.mine === false && (
              <span className="badge" title="Someone else's raid — read only">shared</span>
            )}
            {report?.partial && <span className="partial">· partial (pruned)</span>}
            {/* Everyone in a raid runs their own ACT, so the same night can be
                here several times over. The page opens on yours if you have one
                and on the site's pick otherwise (backend `raidmatch`); this is
                how you read somebody else's — the fights, the numbers and the
                vantage point are all theirs, so it is a different page, not a
                filter on this one. It belongs with the character's name because
                that is the fact it changes. */}
            {run.alternates?.length > 0 && (
              <span className="parsepick" title="The same raid, parsed by someone else">
                <select
                  value={id}
                  aria-label="Whose parse of this raid to show"
                  onChange={(ev) => navigate(`/zones/${ev.target.value}`)}
                >
                  <option value={id}>
                    {run.character_name}{run.mine ? ' (yours)' : ''} — {run.encounter_count} fights
                  </option>
                  {run.alternates.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.character_name}{a.mine ? ' (yours)' : ''} — {a.encounter_count} fights
                    </option>
                  ))}
                </select>
              </span>
            )}
          </>
        )}
        subTitle={fmt.dateLong(run.started_ts)}
        /* Who this raid reaches — the owner's own decisions, so it is shown to
           the owner only. A viewer is told nothing about who ELSE can read it
           (that rule is `shares_for_runs`, owner-only, and this is its UI). */
        seenBy={run.mine && user ? (
          <>
            {run.public && (
              <span className="sharepill pub" title="Readable without an account">Public</span>
            )}
            {run.shared_with?.map((g) => (
              <span key={g.group_id} className="sharepill">{g.name}</span>
            ))}
            {!run.public && !run.shared_with?.length && (
              <span className="nobody">Nobody but you</span>
            )}
            {/* A toggle, not a one-way door: the same button closes what it
                opened. And what it opens lands HERE, under the pills it edits —
                it used to be a card at the top of the main column, which is a
                different part of the screen from the one you clicked in and
                often not even on it. */}
            <button className={`addshare ${sharing ? 'on' : ''}`}
                    aria-expanded={sharing}
                    title={sharing ? 'Close' : 'Choose who can see this raid'}
                    aria-label="Choose who can see this raid"
                    onClick={() => setSharing(!sharing)}>{sharing ? '×' : '+'}</button>
            {sharing && (
              <div className="sharebox">
                <ShareDialog
                  runIds={[id]} isAdmin={user?.role === 'admin'}
                  onClose={() => setSharing(false)}
                  onChanged={() => api.zoneRun(id)
                    .then((d) => setRun(d.zone_run)).catch(() => {})} />
              </div>
            )}
          </>
        ) : null}
        actions={(
          <>
            {/* Compare dresses as a filled gold button everywhere it appears;
                the default button is a gold OUTLINE, which is pixel for pixel
                what every "on" toggle in the app wears. */}
            <Link className="btn solid" to={`/compare?c=${id}:${cmpSel}:raid`}
                  title="Put this raid beside another parse">
              ⇄ Compare
            </Link>
            {run.mine && (
              <button
                className="editbtn"
                aria-pressed={false}
                title="Hide or delete fights"
                onClick={() => { setEditing(true); setConfirmDel(false) }}
              >
                ✎ Edit
              </button>
            )}
          </>
        )}
        editing={editing && run.mine}
        onHide={hideFights}
        onDelete={deleteFights}
        /* The same section, different verbs — the whole raid's copy of what
           every row below now offers for one fight. Delete asks in place. */
        /* Editing, the row is ONE right-packed cluster unfolded out of the Edit
           button, with Done last — in the spot Edit was. Compare is not in it:
           four labelled buttons do not fit across a 300px rail, and the one
           that fell off the end was Done, onto a second line at the far left,
           which is the opposite of "the click that opened this closes it".
           Compare is a click away and comes back with Done. */
        editbar={(
          <span className="editopts">
            {confirmDel ? (
              <>
                <span className="asking">Delete?</span>
                <button className="chip danger" disabled={editBusy} onClick={deleteRaid}>
                  Yes, delete
                </button>
                <button className="chip" onClick={() => setConfirmDel(false)}>Cancel</button>
              </>
            ) : (
              <>
                <button
                  className={`chip ${raidHidden ? 'on' : ''}`} disabled={editBusy}
                  title={raidHidden
                    ? 'Hidden. Click to show it again.'
                    : "Hide every fight in this raid. It won't show when shared, and it won't count in stats."}
                  onClick={() => applyEdit(() => api.hideZoneRun(id, !raidHidden))}
                >
                  {raidHidden ? '⊙ Show' : '⊘ Hide'}
                </button>
                <button className="chip danger" disabled={editBusy}
                        title="Delete this raid. The uploaded log stays."
                        onClick={() => setConfirmDel(true)}>
                  🗑 Delete
                </button>
                <button className="editbtn done" disabled={editBusy}
                        aria-pressed
                        title="Stop editing"
                        onClick={() => setEditing(false)}>
                  ✓ Done
                </button>
              </>
            )}
            {editErr && <span className="err">{editErr}</span>}
          </span>
        )}
        hideZones
      />
      <div className={`wsmain ${stale && detail ? 'stale' : ''}`}>
        {/* The raid's headline numbers come before the tabs, not after: they
            describe the night itself, and the tabs choose which view of it you
            are reading. With a panel open neither one is here — the column is
            a picker, and a stat grid stacked down it shouts over the parse
            someone opened. */}
        {detail && headTiles[tab] && !panelOpen && (
          <div className="metrics">
            {headTiles[tab].map((t) => (
              <div className="metric" key={t.k} title={t.title}>
                <div className="v">{t.v}</div><div className="k">{t.k}</div>
              </div>
            ))}
          </div>
        )}
        {!panelOpen && (
          <Tabs tabs={TABS} value={tab} onChange={(k) => setTab(k === 'damage' ? null : k)} />
        )}
        {detailErr && <p className="err">{detailErr}</p>}
        {/* Every fight hidden is a page with nothing to count, and it must not
            sit on "Loading…" forever pretending otherwise. Only its owner can
            be here — for anyone else the raid does not exist. */}
        {raidHidden && (
          <p className="muted">
            Every fight in this raid is hidden. Use <strong>Edit → Show raid</strong> to
            bring it back.
          </p>
        )}
        {!raidHidden && !detail && !detailErr && tab !== 'insights' && tab !== 'aoes' && (
          <p className="muted">Loading…</p>
        )}
        {stale && detail && <div className="stalebar" aria-live="polite">Updating…</div>}

        {detail && tabCols[tab] && (
          <div className="card">
            <SortableTable
              /* the filters ride on the table's own tools line, beside Columns
                 — they are all controls for the same table */
              tools={(
                <div className="filterbar">
                  <input
                    type="text" value={q || ''} placeholder="Find a raider…"
                    onChange={(e) => setQ(e.target.value || null)}
                    aria-label="Filter combatants by name or class"
                  />
                  {/* one control, not four loose chips — they stay on a line
                      together however narrow the column gets */}
                  <span className="roles">
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
                  </span>
                  {/* Who is in the table, right beside who is filtered out of
                      it — the role chips narrow the raid, these two decide
                      whether anything but the raid is in it at all. On every
                      parse tab, not just Defense, and off by default: a mob is
                      a combatant with a parse worth reading (click its row),
                      but the table opens as the raid. */}
                  <label
                    className="chip toggle"
                    title="Show pet rows. An owned pet's damage is credited to its owner, so its row usually carries only what it took."
                  >
                    <input
                      type="checkbox"
                      checked={showPets}
                      onChange={(e) => setShowPets(e.target.checked)}
                    /> Pets
                  </label>
                  <label
                    className="chip toggle"
                    title="Show mob and environment rows. Click one to read its parse."
                  >
                    <input
                      type="checkbox"
                      checked={showNpcs}
                      onChange={(e) => setShowNpcs(e.target.checked)}
                    /> NPCs
                  </label>
                  {(roleSet.size > 0 || q) && (
                    <button className="chip" onClick={() => { setRolesQ(null); setQ(null) }}>Reset</button>
                  )}
                </div>
              )}
              columns={panelOpen
                ? [nameCol, leadCol[tab] || dpsCol]
                : (tabCols[tab] || damageCols)}
              /* layout is per tab, and the condensed picker beside an open
                 drilldown is not a layout anyone wants remembered */
              prefsKey={panelOpen ? undefined : `zonerun:${tab}`}
              defaultHidden={TAB_HIDDEN[tab]}
              rows={currentRows}
              defaultSort={tabSort[tab] || tabSort.damage}
              rowKey={(a) => a.key}
              selectedKey={selectedActor}
              wrapClass={currentRows.length > 14 ? 'sticky' : ''}
              /* Click a raider to READ them — the panel switches to that one
                 parse, whatever was in it. Their box is what builds a
                 comparison. Mobs and pets have no checkbox, so theirs stays a
                 plain drilldown. */
              onRowClick={(a) => {
                if (a.kind === 'player') focusActor(a.key)
                else setActorQ(a.key === selectedActor ? null : a.key)
              }}
              checkable={(a) => a.kind === 'player'}
              checkedKeys={cmpKeys}
              onCheck={toggleCmp}
            />
            {!currentRows.length && (
              <p className="muted">
                {tab === 'deaths' && !q && !roleSet.size
                  ? 'Nobody died.' : 'Nothing matches that filter.'}
              </p>
            )}
          </div>
        )}

        {detail && tab === 'aoes' && (
          <ErrorBoundary resetKey={`aoes:${sel}`}>
            <AoePanel data={aoeData} err={aoeErr}
                      base={enc?.started_ts ?? run.started_ts} />
          </ErrorBoundary>
        )}

        {detail && tab === 'class' && (
          <ErrorBoundary resetKey={`class:${sel}:${clsQ}`}>
            <ClassPanel data={classData} err={classErr} cls={clsQ}
                        onPick={setClsQ} />
          </ErrorBoundary>
        )}

        {detail && tab === 'timeline' && (
          <div className="card">
            <div className="drillhead">
              <h2>Over the fight</h2>
              <span className="muted">
                {checkedActors.length
                  ? `${checkedActors.length} checked`
                  : `top 5 by ${{ heals: 'healing', taken: 'damage taken' }[metric] || 'damage'}`
                    + ' — check rows on another tab to choose'}
              </span>
            </div>
            {timelineErr && <p className="err">{timelineErr}</p>}
            {!timeline && !timelineErr && <p className="muted">Loading…</p>}
            {timeline?.pruned && (
              <p className="muted">
                No timeline — this run&apos;s raw events were pruned. The other tabs
                read from frozen rollups and are unaffected.
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

        {/* Two questions, two columns. "How did the tank die" is answered by
            one death in detail and "who died tonight" by all of them in a
            list, and the list is what was eating the page's whole width.
            Narrow when a drilldown is open — the main column is half a page
            then, and two of these inside it is four columns of nothing. */}
        {detail && tab === 'deaths' && recaps?.deaths?.length > 0 && (
          <ErrorBoundary resetKey={`deaths:${sel}`}>
            <div className={`deathcols${
              hasTankDeath(recaps.deaths, actorsByKey) && !panelOpen ? ' two' : ''}`}>
              <TankDeaths
                key={`tanks:${sel}`}
                deaths={recaps.deaths}
                windowS={recaps.window_s}
                actorsByKey={actorsByKey}
              />
              <DeathList
                /* what is expanded is indexed into THIS list of deaths, so a
                   new fight selection starts the list closed rather than
                   leaving a recap open on whatever death now sits at that
                   index */
                key={`deaths:${sel}`}
                deaths={recaps.deaths}
                windowS={Math.min(RAID_WINDOW_S, recaps.window_s)}
                prunedEncounters={recaps.pruned_encounters}
                actorsByKey={actorsByKey}
              />
            </div>
          </ErrorBoundary>
        )}
        {detail && tab === 'deaths' && !recaps?.deaths?.length && deaths.length > 0 && (
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

      {/* One column for whatever is open. A comparison and a single drilldown
          are the same move — you picked people and want their parses beside
          the raid — so they get the same element in the same grid slot, and
          the fight rail and the condensed raider list merge into the left
          column either way. */}
      {panelOpen && detail && (
        <div className="panelcol">
          {selHead}
          {comparing ? (
            <ErrorBoundary resetKey={`cmp:${cmpQ}:${sel}`}>
              <ComparePanel
                actors={actors}
                keys={cmpList}
                abilities={detail.abilities}
                derived={derived}
                duration={duration}
                kind={PANEL_KIND[tab]}
                onRemove={toggleCmp}
              />
            </ErrorBoundary>
          ) : (
            <ErrorBoundary resetKey={`actor:${selectedActor}:${sel}`}>
              <ActorPanel
                key={selectedActor}
                name={selName}
                actor={actorsByKey[selectedActor]}
                abilities={detail.abilities}
                actorKey={selectedActor}
                duration={duration}
                kind={PANEL_KIND[tab]}
                onClose={closePanel}
                compareTo={actorsByKey[selectedActor]?.kind === 'player'
                  ? `/compare?c=${id}:${cmpSel}:${selName}` : null}
              />
            </ErrorBoundary>
          )}
        </div>
      )}
    </div>
  )
}
