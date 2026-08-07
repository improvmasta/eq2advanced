import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, sessionLabel } from '../lib/api.js'

export default function Calibration() {
  const [sessions, setSessions] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [how, setHow] = useState(false)

  const load = () => api.sessions().then((d) => setSessions(d.sessions)).catch((e) => setError(e.message))
  useEffect(() => { load() }, [])

  const toggle = (s) => {
    setBusy(s.id)
    api.setCalibration(s.id, !s.calibration)
      .then(load)
      .catch((e) => setError(e.message))
      .finally(() => setBusy(null))
  }

  if (error) return <p className="err">{error}</p>
  if (!sessions) return <p className="muted">Loading…</p>

  const ready = sessions.filter((s) => s.status === 'ready')
  const calibrated = ready.filter((s) => s.calibration)

  return (
    <>
      <div className="pagehead">
        <h1>Calibration</h1>
        <span className="sub">
          {calibrated.length
            ? `${calibrated.length} session${calibrated.length === 1 ? '' : 's'} pinned as ground truth`
            : 'no calibration sessions yet'}
        </span>
        <div className="actions">
          <button onClick={() => setHow(!how)}>{how ? 'Hide' : 'How it works'}</button>
        </div>
      </div>

      {how && (
        <div className="card">
          <p className="note">
            Census spell values are a <em>prior</em> — TLE tuning can differ from the
            live-game data Census exports. A <strong>calibration session</strong> is a
            parse you trust as ground truth per ability: hit a training dummy (or any
            steady target) with your full rotation for a few minutes, upload the log,
            and mark it here.
          </p>
          <p className="note">
            <strong>Two-parse flow (recommended):</strong> parse the dummy once in your
            normal gear, then swap enough gear to move Ability Mod by 100+ and parse
            again — flag both. Two points at different Ability Mod solve each spell's
            <em> true</em> base damage and the real abmod cap, which one point
            mathematically cannot. The raid fit is never overwritten: the spread between
            your dummy baseline and a raid night is reported as the raid-debuff uplift
            per damage school.
          </p>
          <p className="note" style={{ marginBottom: 0 }}>
            Your stats are captured the moment you flag a session, so each dummy parse is
            fitted at the gear it actually ran with. Calibration sessions are pinned —
            their events are never pruned. Re-flag after big gear or tier changes.
          </p>
        </div>
      )}

      <div className="card">
        <h2>Parsed sessions</h2>
        {ready.length === 0 && <p className="muted">No parsed sessions yet.</p>}
        {ready.length > 0 && (
          <div className="tablewrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Session</th><th className="l">Character</th><th>Date</th>
                  <th>Encounters</th><th className="l">Calibration</th>
                </tr>
              </thead>
              <tbody>
                {ready.map((s) => (
                  <tr key={s.id} className={s.calibration ? 'selected' : ''}>
                    <td className="name">
                      <Link to={`/sessions/${s.id}`}>{sessionLabel(s)}</Link>
                    </td>
                    <td className="l">{s.character_name}</td>
                    <td>{fmt.date(s.started_ts)}</td>
                    <td>{s.encounter_count}</td>
                    <td className="l">
                      <button onClick={() => toggle(s)} disabled={busy === s.id}>
                        {s.calibration ? '★ calibration — unmark' : 'mark as calibration'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
