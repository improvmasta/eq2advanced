import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import UploadDrop from '../components/UploadDrop.jsx'
import { api, fmt } from '../lib/api.js'

/* File management: raw uploaded logs and their parse status. Navigation lives
   on Home (zone runs) — a session link here opens the per-file debug view. */
export default function Uploads() {
  const [sessions, setSessions] = useState(null)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.sessions().then((d) => setSessions(d.sessions)).catch((e) => setError(e.message))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (!sessions?.some((s) => s.status === 'parsing' || s.status === 'receiving')) return
    const t = setInterval(refresh, 2000)
    return () => clearInterval(t)
  }, [sessions, refresh])

  async function reparse(id) {
    try { await api.reparse(id); refresh() } catch (e) { setError(e.message) }
  }

  const ready = sessions?.filter((s) => s.status === 'ready') ?? []
  const encounterTotal = ready.reduce((n, s) => n + (s.encounter_count || 0), 0)

  return (
    <>
      <div className="pagehead">
        <h1>Uploads</h1>
        <span className="sub">Raw log files — fights land on <Link to="/">Raids</Link> organized by day and zone</span>
      </div>

      {sessions?.length > 0 && (
        <div className="metrics">
          <div className="metric"><div className="v">{sessions.length}</div><div className="k">Files</div></div>
          <div className="metric"><div className="v">{ready.length}</div><div className="k">Parsed</div></div>
          <div className="metric"><div className="v">{fmt.num(encounterTotal)}</div><div className="k">Encounters</div></div>
        </div>
      )}

      <UploadDrop onUploaded={refresh} />
      {error && <p className="err">{error}</p>}

      <div className="card">
        <h2>Uploaded</h2>
        {sessions === null && <p className="muted">Loading…</p>}
        {sessions?.length === 0 && <p className="muted">Nothing yet — drop a log above.</p>}
        {sessions?.length > 0 && (
          <div className="tablewrap">
            <table className="data">
              <thead>
                <tr>
                  <th>File</th><th className="l">Character</th><th>Date</th><th>Lines</th>
                  <th>Encounters</th><th className="l">Status</th><th></th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id}>
                    <td className="name">
                      {s.status === 'ready'
                        ? <Link to={`/sessions/${s.id}`}>{s.upload_name || `session ${s.id}`}</Link>
                        : (s.upload_name || `session ${s.id}`)}
                    </td>
                    <td className="l">{s.character_name}</td>
                    <td>{fmt.date(s.started_ts ?? s.created_ts)}</td>
                    <td>{fmt.num(s.line_count)}</td>
                    <td>{s.encounter_count}</td>
                    <td className={`l status-${s.status}`}>
                      {s.status}{s.status === 'error' && s.error ? ` — ${s.error.slice(0, 80)}` : ''}
                      {s.pruned ? <span className="badge" title="old events pruned; reports frozen">pruned</span> : null}
                      {s.calibration ? <span className="badge named" title="calibration ground truth">★</span> : null}
                    </td>
                    <td>
                      {s.status === 'ready' && !s.pruned && (
                        <button className="chip" onClick={() => reparse(s.id)}>reparse</button>
                      )}
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
