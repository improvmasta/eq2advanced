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
        <h1>Live</h1>
        <div className="card">
          <p>No live session is receiving right now.</p>
          <p className="muted">
            Pair an uploader on the <Link to="/characters">Characters</Link> page (mint a
            device token), then start the ACT uploader — or replay a log with{' '}
            <code>backend/tools/simulate_live.py</code>. Fights will appear here seconds
            after each kill.
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
      <h1>Live{session ? ` — ${session.character_name}` : ''}</h1>
      <p className="muted">
        {finished ? (
          <>session finished — <Link to={`/sessions/${sessionId}`}>open the full report</Link></>
        ) : (
          <>
            <span className={`badge ${online ? 'named' : ''}`}>
              {online ? 'uploader online' : 'uploader quiet'}
            </span>
            {' '}· last data {st.last_ingest_ts ? fmt.time(st.last_ingest_ts) : '—'}
          </>
        )}
      </p>
      <div className="tiles">
        <div className="tile"><div className="v">{encounters.length}</div><div className="k">Fights</div></div>
        <div className="tile"><div className="v">{named.length}</div><div className="k">Named kills</div></div>
        <div className="tile"><div className="v">{fmt.num(st.line_count ?? session?.line_count)}</div><div className="k">Log lines</div></div>
        <div className="tile"><div className="v">{st.started_ts ? fmt.time(st.started_ts) : '—'}</div><div className="k">First pull</div></div>
      </div>

      {candidates.length > 1 && (
        <p className="muted">
          Watching session {sessionId}.{' '}
          {candidates.filter((c) => c.id !== sessionId).map((c) => (
            <button key={c.id} onClick={() => { setStatus(null); setSessionId(c.id) }}>
              switch to {c.character_name} #{c.id}
            </button>
          ))}
        </p>
      )}

      {encounters.length === 0 && (
        <div className="card"><p className="muted">Waiting for the first finished fight…</p></div>
      )}
      {[...encounters].reverse().map((e) => (
        <div className="card" key={e.id}>
          <h2>
            <Link to={`/encounters/${e.id}`}>{e.name}</Link>
            {e.is_named ? <span className="badge named">named</span> : null}
          </h2>
          <p className="muted">
            {e.zone || 'Unknown zone'} · {fmt.time(e.started_ts)} · {fmt.dur(e.duration_s)} · {e.actor_count} raiders
          </p>
          <p>
            Your damage {fmt.num(e.logger_damage)} ({fmt.num(e.logger_dps)} DPS)
            {e.logger_heals ? <> · heals {fmt.num(e.logger_heals)}</> : null}
            {(e.hints || []).map((h) => <span key={h} className="badge conf-low" style={{ marginLeft: 6 }}>{h}</span>)}
          </p>
        </div>
      ))}
    </>
  )
}
