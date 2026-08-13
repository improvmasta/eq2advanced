import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import EncounterTree from '../components/EncounterTree.jsx'
import ErrorBoundary from '../components/ErrorBoundary.jsx'
import ParseView, { ANCHOR_LABEL } from '../components/ParseView.jsx'
import ShareDialog from '../components/ShareDialog.jsx'
import { api, fmt } from '../lib/api.js'
import { ROLE_LABEL, classLabel, roleOf } from '../lib/classes.js'
import { runLabel } from '../lib/raids.js'
import { consistency, decompose, reportRollup } from '../lib/stats.js'
import { useQueryState } from '../lib/useQueryState.js'

/* The raid page: one zone run, its fight rail, and THE PARSE.

   The parse itself is `ParseView` — the tables, the tabs and the drilldown,
   which the raid dashboard renders too and which therefore cannot know about
   runs, sharing or edit mode. What is left here is everything that IS about
   the run: whose night it is, who can see it, which fights are counted, and
   the hand edits that hide or delete them. */

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
  const [report, setReport] = useState(null)
  const [coach, setCoach] = useState(null)
  const [coachErr, setCoachErr] = useState(null)
  const [coachBusy, setCoachBusy] = useState(false)
  const [error, setError] = useState(null)
  const [sel, setSel] = useQueryState('sel', 'all')
  const [playerQ, setPlayerQ] = useQueryState('player')
  /* The tab lives in the URL and ParseView owns it; the only thing the page
     itself asks is whether the hidden Insights tab is open, because the coach
     engine it reads is per session and is fetched here. */
  const [tabQ] = useQueryState('tab', 'damage')
  /* Clearing an open drilldown when the fight selection changes: same URL
     param the parse reads it from. */
  const [, setActorQ] = useQueryState('actor')

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

  // the coach engine is per session; a run's log is its dominant session
  const domSession = useMemo(() => {
    if (!encounters?.length) return null
    const counts = {}
    for (const e of encounters) counts[e.session_id] = (counts[e.session_id] || 0) + 1
    return Number(Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0])
  }, [encounters])

  useEffect(() => {
    if (tabQ !== 'insights' || !domSession) return
    let gone = false
    api.coach(domSession)
      .then((d) => { if (!gone) setCoach(d.report) })
      .catch((e) => { if (!gone) setCoachErr(e.message) })
    return () => { gone = true }
  }, [tabQ, domSession])

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

  if (error) return <p className="err">{error}</p>
  if (!run || !encounters) return <p className="muted">Loading…</p>

  /* Display only — the notes are keyed by the BASE zone on the backend
     (`zones.base_name`) and nothing here is allowed to change that. */
  const zoneLabel = runLabel(run)
  // this run's fight selection as a Compare-page token ('.' joins ids there —
  // ',' is the column separator and '+' reads as a space in a query string)
  const cmpSel = sel === 'all' ? 'all' : (selIds || []).join('.')

  return (
    <ParseView
      selIds={selIds}
      report={report}
      /* the run's own clock, for the Raid time tile when the selection's
         aggregate carries no ends of its own */
      span={run}
      cmpPrefix={`${id}:${cmpSel}`}
      notice={raidHidden ? (
        /* Every fight hidden is a parse with nothing to count. Only its owner
           can be here — for anyone else the raid does not exist. */
        <p className="muted">
          Every fight in this raid is hidden. Use <strong>Edit → Show raid</strong> to
          bring it back.
        </p>
      ) : null}
      insights={({ actors, derived }) => (
        <ErrorBoundary resetKey={`${id}:${coach?.generated_ts}:${playerQ}`}>
          <Insights run={run} report={report} selIds={selIds}
                    coach={coach} coachErr={coachErr} busy={coachBusy}
                    onGenerate={generateCoach} actors={actors} derived={derived}
                    playerQ={playerQ} setPlayerQ={setPlayerQ} />
        </ErrorBoundary>
      )}
      rail={(
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
            {/* Kept out of the guild string on purpose — see the note on the
                raid list. The guild is a vote over the roster; this is about
                the one person who logged it, and it is the caption that stops
                the page reading as if they were in the fight. */}
            {run.observed && (
              <span className="badge observed"
                    title="Logged without fighting in it — no damage, heals, wards or cures">
                Observed
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
      )}
    />
  )
}
