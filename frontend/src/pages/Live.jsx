import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

// Live raid view: picks the newest receiving live session and subscribes to its
// SSE stream — fight cards appear seconds after each kill. When the session
// finalizes (uploader sends done, or it goes stale) the incremental rows are
// rebuilt, so we refetch instead of trusting streamed encounter ids.
export default function Live() {
  const [candidates, setCandidates] = useState(null)
  const [sessionId, setSessionId] = useState(null)
  const [session, setSession] = useState(null)
  const [status, setStatus] = useState(null)
  const [encounters, setEncounters] = useState([])
  const [error, setError] = useState(null)
  const source = useRef(null)

  useEffect(() => {
    api.sessions()
      .then((d) => {
        const live = d.sessions.filter((s) => s.source === 'live' && s.status === 'receiving')
        setCandidates(live)
        if (live.length) setSessionId(live[0].id)
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (!sessionId) return undefined
    let dead = false
    api.session(sessionId).then((d) => {
      if (dead) return
      setSession(d.session)
      setEncounters(d.encounters)
    }).catch((e) => setError(e.message))

    const es = new EventSource(`/api/sessions/${sessionId}/stream`)
    source.current = es
    es.addEventListener('encounter', (ev) => {
      const enc = JSON.parse(ev.data)
      setEncounters((prev) => (prev.some((e) => e.id === enc.id) ? prev : [...prev, enc]))
    })
    es.addEventListener('status', (ev) => {
      const st = JSON.parse(ev.data)
      setStatus(st)
      if (st.status === 'ready' || st.status === 'error') {
        es.close()
        // finalization rebuilt the session from raw — refetch the real rows
        api.session(sessionId).then((d) => {
          setSession(d.session)
          setEncounters(d.encounters)
        }).catch(() => {})
      }
    })
    es.onerror = () => { /* EventSource retries itself; final close comes via status */ }
    return () => { dead = true; es.close() }
  }, [sessionId])

  if (error) return <p className="err">{error}</p>
  if (candidates === null) return <p className="muted">Loading…</p>

  if (!sessionId) {
    return (
      <>
        <div className="pagehead"><h1>Live</h1></div>
        <div className="card">
          <p>No live session is receiving right now.</p>
          <p className="note" style={{ marginTop: 6, marginBottom: 0 }}>
            Mint a device token on the <Link to="/characters">Characters</Link> page,
            then start the uploader.
          </p>
        </div>
      </>
    )
  }

  const st = status || {}
  const online = st.uploader_online
  const finished = st.status === 'ready' || session?.status === 'ready'
  const named = encounters.filter((e) => e.is_named)

  return (
    <>
      <div className="pagehead">
        <h1>Live{session ? ` — ${session.character_name}` : ''}</h1>
        <span className="sub">
          {finished ? (
            <>session finished — <Link to={`/sessions/${sessionId}`}>open the full report</Link></>
          ) : (
            <>
              <span className={`badge ${online ? 'named' : ''}`} style={{ marginLeft: 0 }}>
                {online ? 'uploader online' : 'uploader quiet'}
              </span>
              {' '}· last data {st.last_ingest_ts ? fmt.time(st.last_ingest_ts) : '—'}
            </>
          )}
        </span>
        {candidates.length > 1 && (
          <div className="actions">
            {candidates.filter((c) => c.id !== sessionId).map((c) => (
              <button key={c.id} onClick={() => { setStatus(null); setSessionId(c.id) }}>
                switch to {c.character_name} #{c.id}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="metrics">
        <div className="metric"><div className="v">{encounters.length}</div><div className="k">Fights</div></div>
        <div className="metric"><div className="v">{named.length}</div><div className="k">Named kills</div></div>
        <div className="metric"><div className="v">{fmt.num(st.line_count ?? session?.line_count)}</div><div className="k">Log lines</div></div>
        <div className="metric"><div className="v">{st.started_ts ? fmt.time(st.started_ts) : '—'}</div><div className="k">First pull</div></div>
      </div>

      {encounters.length === 0 && (
        <div className="card"><p className="muted">Waiting for the first finished fight…</p></div>
      )}

      {encounters.length > 0 && (
        <div className="card">
          <h2>Fights</h2>
          <div className="tablewrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Fight</th><th className="l">Zone</th><th>Start</th><th>Length</th>
                  <th>Your damage</th><th>Your DPS</th><th>Heals</th><th>Raiders</th>
                </tr>
              </thead>
              <tbody>
                {[...encounters].reverse().map((e) => (
                  <tr key={e.id}>
                    <td className="name">
                      <Link to={`/encounters/${e.id}`}>{e.name}</Link>
                      {e.is_named ? <span className="badge named">named</span> : null}
                      {(e.hints || []).map((h) => <span key={h} className="badge conf-low">{h}</span>)}
                    </td>
                    <td className="l muted">{e.zone || 'Unknown zone'}</td>
                    <td>{fmt.time(e.started_ts)}</td>
                    <td>{fmt.dur(e.duration_s)}</td>
                    <td>{fmt.num(e.logger_damage)}</td>
                    <td>{fmt.num(e.logger_dps)}</td>
                    <td>{e.logger_heals ? fmt.num(e.logger_heals) : ''}</td>
                    <td>{e.actor_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
