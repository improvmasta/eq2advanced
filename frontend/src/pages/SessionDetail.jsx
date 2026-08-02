import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

export default function SessionDetail() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.session(id).then(setData).catch((e) => setError(e.message))
  }, [id])

  if (error) return <p className="err">{error}</p>
  if (!data) return <p className="muted">Loading…</p>

  const { session, encounters } = data
  const zones = []
  for (const e of encounters) {
    const last = zones[zones.length - 1]
    if (!last || last.zone !== e.zone) zones.push({ zone: e.zone, encounters: [e] })
    else last.encounters.push(e)
  }
  const named = encounters.filter((e) => e.is_named)

  return (
    <>
      <h1>{session.upload_name || `Session ${session.id}`}</h1>
      <p className="muted">
        {session.character_name} · {fmt.date(session.started_ts)} {fmt.time(session.started_ts)}–{fmt.time(session.ended_ts)}
      </p>
      <div className="tiles">
        <div className="tile"><div className="v">{encounters.length}</div><div className="k">Encounters</div></div>
        <div className="tile"><div className="v">{named.length}</div><div className="k">Named kills</div></div>
        <div className="tile"><div className="v">{fmt.dur(session.ended_ts - session.started_ts)}</div><div className="k">Logged</div></div>
        <div className="tile"><div className="v">{fmt.num(session.line_count)}</div><div className="k">Log lines</div></div>
      </div>

      {session.status === 'ready' && (
        <p style={{ display: 'flex', gap: 16 }}>
          <Link to={`/sessions/${session.id}/coach`}>Coach report →</Link>
          <Link to={`/sessions/${session.id}/raid-report`}>Raid report →</Link>
        </p>
      )}

      {zones.map((z, i) => (
        <div className="card" key={i}>
          <h2>{z.zone || 'Unknown zone'}</h2>
          <table className="data">
            <thead>
              <tr>
                <th>Encounter</th><th>Start</th><th>Length</th>
                <th>Your damage</th><th>Your DPS</th><th>Your heals</th><th>Raiders</th>
              </tr>
            </thead>
            <tbody>
              {z.encounters.map((e) => (
                <tr key={e.id}>
                  <td>
                    <Link to={`/encounters/${e.id}`}>{e.name}</Link>
                    {e.is_named ? <span className="badge named">named</span> : null}
                  </td>
                  <td>{fmt.time(e.started_ts)}</td>
                  <td>{fmt.dur(e.duration_s)}</td>
                  <td>{fmt.num(e.logger_damage)}</td>
                  <td>{fmt.num(e.logger_dps)}</td>
                  <td>{fmt.num(e.logger_heals)}</td>
                  <td>{e.actor_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  )
}
