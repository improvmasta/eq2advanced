import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import EncounterTree from '../components/EncounterTree.jsx'
import ErrorBoundary from '../components/ErrorBoundary.jsx'
import LiveMeter from '../components/LiveMeter.jsx'
import RaidNotes from '../components/RaidNotes.jsx'
import ReplayPicker from '../components/ReplayPicker.jsx'
import { api, fmt } from '../lib/api.js'
import { useCanCurate } from '../lib/session.jsx'

/* The raid dashboard — the page you leave open on the second monitor.

   Three columns, and each answers a different question while you are playing:
   the rail on the left is the night so far (click back through it, click LIVE
   to return), the middle is the pull happening right now, and the right is
   where a note or a screenshot goes the moment it is worth keeping.

   Two feeds, deliberately not the same thing. `encounter` events are the
   RECORD — a fight the writer has committed, arriving seconds after it ends.
   `partial` events are a VIEW of the fight still running
   (backend/pipeline/livemeter.py), rebuilt from memory every couple of
   seconds and stored nowhere. When the night finalizes, the whole session is
   rebuilt from raw and the encounter ids CHANGE, so the rail is refetched
   rather than trusted.

   Going back in time renders the same meter, not a different page: a finished
   pull reads the way the live one does, and the depth (abilities, deaths,
   timeline, AoE audit) is one click away on the raid page.

   A curator or admin gets a third feed: REPLAY (backend/routers/replay_api.py)
   plays a recorded fight back through the live meter at raid speed, so this
   page can be worked on without waiting for Tuesday. It arrives as `partial`
   events on the same shape, which is the point — the component under test does
   not know which socket it is reading. */

const IDLE_POLL_MS = 15000

function useLiveSession() {
  const [sessions, setSessions] = useState(null)
  const [sessionId, setSessionId] = useState(null)
  const [error, setError] = useState(null)

  const look = useCallback(() => api.sessions()
    .then((d) => {
      const live = d.sessions.filter((s) => s.source === 'live' && s.status === 'receiving')
      setSessions(live)
      setSessionId((cur) => {
        if (cur && live.some((s) => s.id === cur)) return cur
        return live.length ? live[0].id : null
      })
    })
    .catch((e) => setError(e.message)), [])

  useEffect(() => { look() }, [look])

  /* A dashboard that needs reloading when the raid starts is a dashboard
     nobody leaves open. Keep looking while there is nothing to show. */
  useEffect(() => {
    if (sessionId) return undefined
    const id = setInterval(look, IDLE_POLL_MS)
    return () => clearInterval(id)
  }, [sessionId, look])

  return { sessions, sessionId, setSessionId, error, refresh: look }
}

export default function Live() {
  const { sessions, sessionId, setSessionId, error, refresh } = useLiveSession()
  const canCurate = useCanCurate()
  /* {id, speed} while a recorded fight is playing. Replay and the live feed
     are exclusive: both write `partial`, and a dashboard showing two fights at
     once would be lying about one of them. */
  const [replay, setReplay] = useState(null)
  const [replayErr, setReplayErr] = useState(null)
  const [session, setSession] = useState(null)
  const [encounters, setEncounters] = useState([])
  const [status, setStatus] = useState(null)
  const [partial, setPartial] = useState(null)
  const [metric, setMetric] = useState('damage')
  /* The rail's own key grammar: a fight is its id, and a zone block or a
     collapsed Trash ×N is the comma-joined ids under it. `/encounters/agg`
     takes a set either way, so a trash group opens as one combined parse
     instead of being a click that does nothing. null is LIVE. */
  const [sel, setSel] = useState(null)
  const [recorded, setRecorded] = useState(null)
  const [recErr, setRecErr] = useState(null)

  // --- the session's own fights, and the live feed --------------------------
  useEffect(() => {
    if (!sessionId || replay) {
      if (!sessionId) { setSession(null); setEncounters([]); setPartial(null) }
      return undefined
    }
    let dead = false
    const load = () => api.session(sessionId).then((d) => {
      if (dead) return
      setSession(d.session)
      setEncounters(d.encounters)
    }).catch(() => {})
    load()

    const es = new EventSource(`/api/sessions/${sessionId}/stream`)
    es.addEventListener('encounter', (ev) => {
      const enc = JSON.parse(ev.data)
      setEncounters((prev) => (prev.some((e) => e.id === enc.id) ? prev : [...prev, enc]))
    })
    es.addEventListener('partial', (ev) => setPartial(JSON.parse(ev.data)))
    es.addEventListener('status', (ev) => {
      const st = JSON.parse(ev.data)
      setStatus(st)
      if (st.status === 'ready' || st.status === 'error') {
        es.close()
        setPartial(null)
        // finalization rebuilt the session from raw: the ids we were handed
        // are gone, so refetch rather than patch
        load()
        refresh()
      }
    })
    es.onerror = () => { /* EventSource retries itself; the close comes via status */ }
    return () => { dead = true; es.close() }
  }, [sessionId, refresh, replay])

  // --- a recorded fight, played back at raid speed ---------------------------
  useEffect(() => {
    if (!replay) return undefined
    setPartial(null)
    setReplayErr(null)
    const es = new EventSource(
      `/api/replay/${replay.id}/stream?speed=${replay.speed}`)
    es.addEventListener('partial', (ev) => {
      const d = JSON.parse(ev.data)
      setPartial(d)
      /* A fight ENDS, and an EventSource whose server closed the stream
         reconnects by default — which would quietly start the replay again
         from the top and look like the meter had reset itself. The last frame
         is the one worth keeping on screen, so close it here. */
      if (d.replay?.done) es.close()
    })
    // a 403/404/409 arrives as the same anonymous error; the browser does not
    // retry a failed HTTP status, so this is the end of that replay
    es.onerror = () => {
      es.close()
      setReplayErr('That fight could not be replayed — its raw log may be gone.')
      setReplay(null)
    }
    return () => es.close()
  }, [replay])

  const selIds = useMemo(() => (sel == null ? []
    : String(sel).split(',').map(Number).filter(Number.isFinite)), [sel])

  // --- a fight picked out of the rail --------------------------------------
  useEffect(() => {
    if (!selIds.length) { setRecorded(null); setRecErr(null); return undefined }
    let dead = false
    setRecErr(null)
    api.encountersAgg(selIds)
      .then((d) => { if (!dead) setRecorded(d) })
      .catch((e) => { if (!dead) setRecErr(e.message) })
    return () => { dead = true }
  }, [selIds.join(',')])

  const finished = status?.status === 'ready' || session?.status === 'ready'
  /* Dimming says "this picture has stopped moving". A replay has no uploader
     to be quiet, so there the only question is whether it has started. */
  const stale = replay ? !partial?.fight
    : (!partial?.fight || (status && !status.uploader_online))
  // the replay block the server attaches to a replayed `partial` — what is
  // playing, where the cursor is, and whether it has run out
  const rep = partial?.replay || null

  /* A finished fight, shaped like a live one so the same meter draws it. */
  const recordedFight = useMemo(() => {
    if (!selIds.length || !recorded) return null
    const enc = recorded.encounter || {}
    const dur = Math.max(enc.duration_s || 1, 1)
    const actors = (recorded.actors || []).map((a) => ({
      name: a.name, kind: a.kind === 'player' ? 'player' : a.kind, class: a.class,
      damage: a.damage || 0, dps: a.dps || 0,
      heals: a.heals || 0, hps: (a.heals || 0) / dur,
      deaths: a.deaths || 0,
    }))
    const players = actors.filter((a) => a.kind === 'player')
    const damage = players.reduce((s, a) => s + a.damage, 0)
    const heals = players.reduce((s, a) => s + a.heals, 0)
    return {
      zone: enc.zone, elapsed_s: dur,
      // a multi-fight selection has no single name; the agg payload says so by
      // leaving it null, and the count is the honest label
      provisional_name: enc.name
        || (selIds.length > 1 ? `${selIds.length} fights` : 'Fight'),
      provisional_is_named: !!enc.is_named,
      raid: {
        damage, dps: damage / dur, heals, hps: heals / dur,
        deaths: players.reduce((s, a) => s + a.deaths, 0),
        raiders: players.filter((a) => a.damage || a.heals).length,
      },
      actors, aoes: [],
    }
  }, [selIds, recorded])

  const selEnc = useMemo(() => (selIds.length === 1
    ? encounters.find((e) => e.id === selIds[0]) || null : null), [encounters, selIds])

  /* What a note filed right now is ABOUT: the named being pulled, or the zone
     if this is trash. The panel lets you override it; nothing is guessed
     server-side. */
  const context = useMemo(() => {
    const f = selIds.length ? recordedFight : partial?.fight
    // between pulls there is no fight to read a zone off; the last one the
    // night recorded is still where the raid is standing
    const fallback = encounters.length ? encounters[encounters.length - 1].zone : null
    if (!f) return { zone: fallback, mob: null }
    return {
      zone: f.zone || fallback,
      mob: f.provisional_is_named ? f.provisional_name : null,
    }
  }, [selIds, partial, recordedFight, encounters])

  if (error) return <p className="err">{error}</p>
  if (sessions === null) return <p className="muted">Loading…</p>

  if (!sessionId && !replay) {
    return (
      <div className="dashgrid idle">
        <div className="dashmain">
          <div className="pagehead"><h1>Raid dashboard</h1></div>
          <div className="card">
            <p>Nothing is streaming right now.</p>
            <p className="note" style={{ marginTop: 6, marginBottom: 0 }}>
              Set the ACT plugin up on the <Link to="/import">Import</Link> page and
              tick “Send my combat log as I play”. This page picks the raid up on
              its own — leave it open.
            </p>
          </div>
          {canCurate && (
            <div className="dashbar">
              <ReplayPicker active={null}
                            onStart={(id, speed) => setReplay({ id, speed })}
                            onStop={() => setReplay(null)} />
              <span className="muted">plays a recorded fight through this meter</span>
            </div>
          )}
          {replayErr && <p className="err">{replayErr}</p>}
        </div>
        <div className="dashside">
          <ErrorBoundary resetKey="notes-idle">
            <RaidNotes zone={null} mob={null} />
          </ErrorBoundary>
        </div>
      </div>
    )
  }

  const fight = selIds.length ? recordedFight : partial?.fight

  return (
    <div className="dashgrid">
      <div className="dashrail">
        <button className={`liveswitch ${sel == null ? 'on' : ''} ${replay ? 'replaying' : ''}`}
                onClick={() => setSel(null)}>
          <span className={`dot ${replay ? 'on' : finished ? '' : 'on'}`} />
          {replay ? 'Replay' : 'Live'}
          <em>
            {replay ? (rep?.done ? 'finished' : rep?.name || 'loading…')
              : finished ? 'night finished'
                : partial?.fight ? 'in combat' : 'between pulls'}
          </em>
        </button>
        <EncounterTree
          encounters={encounters}
          sel={sel == null ? null : String(sel)}
          onSelect={(key) => setSel(key === 'all' || key == null ? null : String(key))}
          sessionLabel={session ? session.character_name : 'Tonight'}
          sub={session?.started_ts ? fmt.time(session.started_ts) : null}
        />
      </div>

      <div className="dashmain">
        <div className="dashbar">
          {replay ? (
            <>
              <span className="badge warn">replay</span>
              <span className="muted">
                {rep ? `${rep.name || 'fight'} · ${rep.zone || 'unknown zone'}` : 'loading the log…'}
                {rep ? ` · ${fmt.dur(rep.elapsed_s)} of ${fmt.dur(rep.span_s)}` : ''}
                {rep && rep.speed !== 1 ? ` · ${rep.speed}×` : ''}
                {rep?.done ? ' · finished' : ''}
              </span>
            </>
          ) : (
            <>
              <span className={`badge ${status?.uploader_online ? 'named' : ''}`}>
                {finished ? 'finished' : status?.uploader_online ? 'uploader online' : 'uploader quiet'}
              </span>
              <span className="muted">
                {encounters.length} fight{encounters.length === 1 ? '' : 's'}
                {status?.line_count ? ` · ${fmt.num(status.line_count)} lines` : ''}
              </span>
            </>
          )}
          {canCurate && (
            <ReplayPicker active={replay}
                          onStart={(id, speed) => { setSel(null); setReplay({ id, speed }) }}
                          onStop={() => { setReplay(null); setPartial(null) }} />
          )}
          {sessions.length > 1 && sessions.filter((s) => s.id !== sessionId).map((s) => (
            <button key={s.id} className="chip" onClick={() => { setSel(null); setSessionId(s.id) }}>
              {s.character_name}
            </button>
          ))}
          {selEnc && (
            <Link className="chip" to={`/encounters/${selEnc.id}`}>Open the full parse</Link>
          )}
        </div>

        {replayErr && <p className="err">{replayErr}</p>}

        {finished && sel == null && !replay && (
          <div className="card">
            <p>This night has finalized — the live meter is done.</p>
            <p className="note" style={{ marginTop: 6, marginBottom: 0 }}>
              The rail still works, and the full report is on the{' '}
              <Link to="/">raid list</Link>.
            </p>
          </div>
        )}

        {recErr && <p className="err">{recErr}</p>}

        <ErrorBoundary resetKey={`meter-${sel ?? 'live'}-${metric}`}>
          <LiveMeter
            fight={fight}
            metric={metric}
            onMetric={setMetric}
            stale={sel == null && stale}
            showChart={sel == null}
            showTimers={sel == null}
          />
        </ErrorBoundary>
      </div>

      <div className="dashside">
        <ErrorBoundary resetKey={`notes-${context.zone}-${context.mob}`}>
          <RaidNotes zone={context.zone} mob={context.mob} />
        </ErrorBoundary>
      </div>
    </div>
  )
}
