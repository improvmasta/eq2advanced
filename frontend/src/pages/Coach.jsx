import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

const SEVERITY = { warn: 'Fix first', opportunity: 'Opportunity', info: 'Note' }

function ConfidenceBadge({ level }) {
  return <span className={`badge conf-${level}`}>{level}</span>
}

export default function Coach() {
  const { id } = useParams()
  const [report, setReport] = useState(undefined) // undefined = loading, null = none yet
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.coach(id).then((d) => setReport(d.report)).catch((e) => setError(e.message))
  }, [id])

  const generate = () => {
    setBusy(true)
    setError(null)
    api.generateCoach(id)
      .then((d) => setReport(d.report))
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false))
  }

  if (error && report === undefined) return <p className="err">{error}</p>
  if (report === undefined) return <p className="muted">Loading…</p>

  if (report === null) {
    return (
      <>
        <p style={{ marginTop: 12 }}><Link to={`/sessions/${id}`}>← back to session</Link></p>
        <h1>Coach report</h1>
        <div className="card">
          <p>
            No report yet for this session. The coach compares what each ability
            <em> should</em> do at your Census stats against what it
            <em> actually</em> did in this parse, then prices your stat and spell
            upgrades from your real casts.
          </p>
          <p className="muted">
            Sync your character on the Character page first so the fit has your
            stats and scribed tiers.
          </p>
          {error && <p className="err">{error}</p>}
          <button onClick={generate} disabled={busy}>
            {busy ? 'Analyzing…' : 'Generate report'}
          </button>
        </div>
      </>
    )
  }

  const cur = report.currencies
  const fits = (report.fit || []).filter((f) => f.noncrit_n + f.crit_n > 0)
  return (
    <>
      <p style={{ marginTop: 12 }}><Link to={`/sessions/${id}`}>← back to session</Link></p>
      <h1>Coach — {report.character.name}</h1>
      <p className="muted">
        {report.character.class} · {report.archetype} · generated {fmt.date(report.generated_ts)}{' '}
        {fmt.time(report.generated_ts)} · engine {report.engine_version}
        {' · '}
        <a onClick={generate} style={{ cursor: 'pointer' }}>{busy ? 'regenerating…' : 'regenerate'}</a>
      </p>
      {error && <p className="err">{error}</p>}

      <div className="tiles">
        <div className="tile"><div className="v">{fmt.num(cur.dps)}</div><div className="k">DPS</div></div>
        <div className="tile"><div className="v">{cur.crit_pct != null ? `${cur.crit_pct}%` : '—'}</div><div className="k">Crit rate</div></div>
        <div className="tile">
          <div className="v">{cur.idle_pct != null ? `${cur.idle_pct}%` : '—'}</div>
          <div className="k">{cur.cast_source === 'log' ? 'Idle time (cast log)' : 'Est. idle time'}</div>
        </div>
        {report.archetype === 'healer' && cur.overheal_pct != null ? (
          <div className="tile"><div className="v">{cur.overheal_pct}%</div><div className="k">Overheal (est.)</div></div>
        ) : (
          <div className="tile"><div className="v">{cur.deaths}</div><div className="k">Deaths</div></div>
        )}
      </div>

      {report.findings.length > 0 && (
        <div className="card">
          <h2>Findings</h2>
          {report.findings.map((f, i) => (
            <div key={i} className={`finding ${f.severity}`}>
              <span className="badge">{SEVERITY[f.severity] || f.severity}</span>
              <div>
                <strong>{f.title}</strong>
                <p className="muted" style={{ margin: '2px 0 0' }}>{f.detail}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {report.stat_priorities.length > 0 && (
        <div className="card">
          <h2>Stat priorities</h2>
          <p className="muted" style={{ fontSize: '0.85rem' }}>
            What one conventional step of each stat would have added to THIS
            session's casts, priced through the fitted Census coefficients.
          </p>
          <table className="data">
            <thead>
              <tr><th>Stat</th><th>Step</th><th>Damage gained</th><th>DPS gained</th><th style={{ textAlign: 'left' }}>Why</th></tr>
            </thead>
            <tbody>
              {report.stat_priorities.map((p) => (
                <tr key={p.stat}>
                  <td>{p.label}</td>
                  <td>{p.step}</td>
                  <td>{fmt.num(p.damage_gain)}</td>
                  <td>{p.dps_gain}</td>
                  <td className="muted" style={{ textAlign: 'left', whiteSpace: 'normal' }}>{p.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {report.tier_upgrades.length > 0 && (
        <div className="card">
          <h2>Spell tier upgrades</h2>
          <table className="data">
            <thead>
              <tr><th>Spell</th><th>Scribed</th><th>Upgrade to</th><th>Damage gained</th><th>DPS gained</th></tr>
            </thead>
            <tbody>
              {report.tier_upgrades.map((u, i) => (
                <tr key={i}>
                  <td>{u.spell_name}</td>
                  <td>{u.from_tier}</td>
                  <td>{u.to_tier}</td>
                  <td>{fmt.num(u.damage_gain)}</td>
                  <td>{u.dps_gain}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(cur.debuffs || []).length > 0 && (
        <div className="card">
          <h2>Debuff uptime</h2>
          <p className="muted" style={{ fontSize: '0.85rem' }}>
            From your real cast starts + Census durations. Burn windows are the
            raid's hottest {`10s`} stretches — debuffs pay the most there.
          </p>
          <table className="data">
            <thead>
              <tr><th>Debuff</th><th>Casts</th><th>Duration</th><th>Uptime</th><th>Up during burns</th></tr>
            </thead>
            <tbody>
              {cur.debuffs.map((d) => (
                <tr key={d.ability}>
                  <td>{d.ability}</td>
                  <td>{d.casts}</td>
                  <td>{d.duration_s}s</td>
                  <td>{d.uptime_pct}%</td>
                  <td>{d.burn_uptime_pct != null ? `${d.burn_uptime_pct}%` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(report.debuff_uplift || []).length > 0 && (
        <div className="card">
          <h2>Raid debuff uplift</h2>
          <p className="muted" style={{ fontSize: '0.85rem' }}>
            This session's fit over your dummy-parse baseline, per damage
            school — the measured effect of your raid's debuffs.
          </p>
          <table className="data">
            <thead><tr><th>School</th><th>Uplift</th><th>Abilities</th></tr></thead>
            <tbody>
              {report.debuff_uplift.map((d) => (
                <tr key={d.dtype}>
                  <td>{d.dtype}</td><td>{d.uplift}×</td><td>{d.abilities}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {fits.length > 0 && (
        <div className="card">
          <h2>Ability fit — observed vs Census</h2>
          <p className="muted" style={{ fontSize: '0.85rem' }}>
            Coefficient is observed ÷ expected at your stats; Census is a prior,
            the parse is the evidence. Calibrate on a training dummy to firm up
            thin samples — two dummy parses at different Ability Mod solve each
            spell's TRUE base (shown as “true base”).
          </p>
          <table className="data">
            <thead>
              <tr>
                <th>Ability</th><th>Scribed</th><th>Hits</th><th>Observed avg</th>
                <th>Expected</th><th>Coefficient</th><th>Debuff ×</th><th>Crit ×</th><th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {fits.map((f) => (
                <tr key={f.ability}>
                  <td>
                    {f.ability}
                    {f.periodic ? <span className="badge">dot</span> : null}
                    {f.base_source === 'calibrated2' ? <span className="badge named" title="base solved from two-point calibration">true base</span> : null}
                  </td>
                  <td className="muted">{f.tier_name || '—'}</td>
                  <td>{f.noncrit_n + f.crit_n}</td>
                  <td>{fmt.num(f.observed_mean)}</td>
                  <td>{f.expected != null ? fmt.num(f.expected) : '—'}</td>
                  <td>{f.coefficient != null ? `${f.coefficient.toFixed(2)}×` : '—'}</td>
                  <td>{f.debuff_uplift != null ? `${f.debuff_uplift.toFixed(2)}×` : '—'}</td>
                  <td>{f.crit_mult_fitted ? `${f.crit_mult.toFixed(2)}` : '—'}</td>
                  <td><ConfidenceBadge level={f.confidence} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {report.calibration && (report.calibration.two_point.length > 0
        || report.calibration.single_point.length > 0) && (
        <p className="muted" style={{ fontSize: '0.85rem' }}>
          Calibration: dummy parses at Ability Mod{' '}
          {report.calibration.abmod_points.join(' / ')}
          {report.calibration.two_point.length > 0 &&
            <> · true base solved for {report.calibration.two_point.join(', ')}</>}
          {report.calibration.single_point.length > 0 &&
            <> · one-point only for {report.calibration.single_point.join(', ')}</>}
        </p>
      )}

      {report.caveats.length > 0 && (
        <p className="muted" style={{ fontSize: '0.85rem' }}>
          {report.caveats.map((c, i) => <span key={i}>{c}<br /></span>)}
        </p>
      )}
    </>
  )
}
