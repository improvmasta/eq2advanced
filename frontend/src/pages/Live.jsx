import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import EncounterTree from '../components/EncounterTree.jsx'
import ErrorBoundary from '../components/ErrorBoundary.jsx'
import LiveMeter, { METRICS } from '../components/LiveMeter.jsx'
import MiniRail from '../components/MiniRail.jsx'
import OverlayPanel from '../components/OverlayPanel.jsx'
import ParseView from '../components/ParseView.jsx'
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

   Only the pull in progress wears the meter. THE MOMENT IT ENDS THE MIDDLE
   COLUMN BECOMES THE PARSE — the same `ParseView` the raid page is made of,
   tabs and drilldowns and all — and stays there until the next pull opens.
   That is the whole point of the page: what you read ten seconds after a kill
   and what you read the next morning are the same numbers in the same table.

   It used to be a cut-down "recap" of its own (a two-table `RecordedFight`),
   on the reasoning that between pulls you only want a glance. You do not: the
   questions asked between pulls are max hit, who died, what the AoE did and
   who was late — every one of which was on the other page.

   A curator or admin gets a third feed: REPLAY (backend/routers/replay_api.py)
   plays a recorded fight back through the live meter at raid speed, so this
   page can be worked on without waiting for Tuesday. It arrives as `partial`
   events on the same shape, which is the point — the component under test does
   not know which socket it is reading. */

const IDLE_POLL_MS = 15000
/* How often the session list is re-read while more than one is receiving —
   the only way to notice that the fighting has moved to the other client, and
   cheap enough at this rate. */
const FOLLOW_POLL_MS = 6000
/* A session quiet this long has stopped being played, whoever picked it. It is
   the release on an explicit pick: choosing a character holds the page there
   while that character is still logging, and stops holding it when the log
   does. Deliberately far short of `LIVE_IDLE_S` (30 min), which is when the
   SERVER gives up on a session — this is only about which one is on screen. */
const QUIET_S = 120
/* How long the rail keeps a just-ended pull on screen while its record is
   written. Generous on purpose: it is a fallback for the fight that never
   commits, not the mechanism — the commit itself is what normally clears it. */
const HOLD_MS = 20000
const MINI_KEY = 'eq2a.mini'
const PARSE_KEY = 'eq2a.mainparse'

/* A fight that is OVER, on the dashboard: its parse, with a line above it
   saying which pull this is.

   The parse is `ParseView` — the identical component `/zones/:id` renders, so
   there is nothing to keep in step. What it needs and the raid page has for
   free is the RAID REPORT (overheal, time dead, damage lost dead, engage —
   four columns that are not in the aggregate), and mid-night there is no run
   to ask for: `/encounters/report?ids=` is the same report scoped to the
   fights in hand. It arrives after the tables do, which is right — the parse
   should not wait on the four columns that are not the reason anyone looked.

   No rail (the dashboard's own is the column to the left) and no compare link
   (that needs the run these fights will land in, and it may not exist yet —
   the way out to all of it is the bar's "Open the full parse"). */
function DashParse({ selIds, encs, note }) {
  const idKey = selIds.join(',')
  const [report, setReport] = useState(null)

  useEffect(() => {
    let dead = false
    setReport(null)
    api.encountersReport(selIds)
      .then((d) => { if (!dead) setReport(d) })
      // no report is not no parse: those four columns stay empty and every
      // other number on the page is unaffected
      .catch(() => { if (!dead) setReport(null) })
    return () => { dead = true }
  }, [idKey])

  const first = encs[0]
  const dur = encs.reduce((s, e) => s + Math.max(e.duration_s || 0, 0), 0)
  const name = encs.length > 1
    ? `${encs.length} fights` : (first?.name || 'Trash')

  return (
    <div className="dashfight">
      {/* Which fight, and how long it ran. Every figure under it is a RATE,
          and a rate you cannot see the denominator of is one you have to take
          on trust. */}
      <div className="card rechead">
        <div className="lhname">
          <h2>{name}</h2>
          {encs.length === 1 && !!first.is_named && (
            <span className="badge named">named</span>)}
          {encs.length === 1 && first.success === 0 && (
            <span className="badge warn">wipe</span>)}
          {encs.length === 1 && !!first.is_named && first.success === 1 && (
            <span className="badge named">killed</span>)}
          <span className="zone">{first?.zone || 'Unknown zone'}</span>
          <span className="muted recnote">{fmt.dur(dur)}</span>
          {note && <span className="muted recnote">{note}</span>}
        </div>
      </div>
      <ParseView selIds={selIds} report={report}
                 span={{ started_ts: first?.started_ts,
                         ended_ts: encs[encs.length - 1]?.ended_ts }} />
    </div>
  )
}

/* Which of several receiving sessions is the one being PLAYED.

   Two EQ2 clients logging at once is two live sessions, and it used to take
   whichever was created last — which is how a second account, parked in town
   with ninety lines of chat in it, took the dashboard off a raid in progress
   and kept it. Newest-created says nothing about which log is moving.

   In combat wins, because only one character can be fighting at a time and
   that is the one you are looking at the screen for. Failing that, the one
   that spoke most recently. */
const liveliest = (live) => {
  const fighting = live.filter((s) => s.in_combat)
  const pool = fighting.length ? fighting : live
  return pool.reduce((best, s) => (
    (s.last_ingest_ts || 0) > (best.last_ingest_ts || 0) ? s : best), pool[0])
}

function useLiveSession() {
  const [sessions, setSessions] = useState(null)
  const [sessionId, setSessionId] = useState(null)
  const [error, setError] = useState(null)
  /* A character picked BY HAND. It holds the page there while that log is
     still moving — following the action is a default, not a rule you cannot
     get out of — and lets go once it goes quiet. */
  const picked = useRef(null)

  const look = useCallback(() => api.sessions()
    .then((d) => {
      const live = d.sessions.filter((s) => s.source === 'live' && s.status === 'receiving')
      setSessions(live)
      setSessionId((cur) => {
        const row = live.find((s) => s.id === cur)
        if (!row) return live.length ? liveliest(live).id : null
        /* Never mid-pull: a page that jumps out of the fight you are in is
           worse than one showing the wrong character between them. */
        if (row.in_combat) return cur
        const quiet = (Date.now() / 1000) - (row.last_ingest_ts || 0) > QUIET_S
        if (picked.current === cur && !quiet) return cur
        // somebody else is fighting, or this log has stopped while another
        // one is still going
        const best = liveliest(live)
        if (best.id === cur) return cur
        if (best.in_combat || quiet) { picked.current = null; return best.id }
        return cur
      })
    })
    .catch((e) => setError(e.message)), [])

  useEffect(() => { look() }, [look])

  /* A dashboard that needs reloading when the raid starts is a dashboard
     nobody leaves open — and one that needs reloading when you switch clients
     is the same bug wearing a different hat. So it keeps looking while there
     is nothing to show, and keeps looking FASTER while there is more than one
     thing it could be showing. */
  // sessions that are actually sending — a `receiving` row that has gone quiet
  // is not another client to watch, it is one the server has not reaped yet
  const alive = (sessions || []).filter(
    (s) => (Date.now() / 1000) - (s.last_ingest_ts || 0) <= QUIET_S).length
  useEffect(() => {
    // one live log and we are on it: there is nothing left to find out
    if (sessionId && alive === 1) return undefined
    const id = setInterval(look, sessionId ? FOLLOW_POLL_MS : IDLE_POLL_MS)
    return () => clearInterval(id)
  }, [sessionId, alive, look])

  const choose = useCallback((id) => { picked.current = id; setSessionId(id) }, [])

  return { sessions, sessionId, setSessionId: choose, error, refresh: look }
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
  /* Switches, not tabs: any subset of the meters can be on at once. */
  const [metrics, setMetrics] = useState(['damage'])
  /* The mini overlays docked to the window's edge (MiniRail.jsx). Remembered,
     because whether the game or the dashboard owns this monitor is a fact
     about the desk and not about tonight. */
  const [mini, setMini] = useState(() => localStorage.getItem(MINI_KEY) === '1')
  useEffect(() => { localStorage.setItem(MINI_KEY, mini ? '1' : '0') }, [mini])
  /* The middle column's meter, switchable OFF: dimmed to dark and paused, so
     the mini overlay is the only thing moving while you play. Remembered for
     the same reason `mini` is — it is a fact about the desk. Switching it off
     does NOT switch off the page: a fight that has ended still lands in the
     middle as its record, which is the between-pulls view either way. */
  const [parseOn, setParseOn] = useState(() => localStorage.getItem(PARSE_KEY) !== '0')
  useEffect(() => { localStorage.setItem(PARSE_KEY, parseOn ? '1' : '0') }, [parseOn])
  /* The rail's own key grammar: a fight is its id, and a zone block or a
     collapsed Trash ×N is the comma-joined ids under it. `/encounters/agg`
     takes a set either way, so a trash group opens as one combined parse
     instead of being a click that does nothing. null is LIVE. */
  const [sel, setSel] = useState(null)

  const toggleMetric = useCallback((k) => setMetrics((cur) => {
    const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]
    // at least one stays on, in the METRICS declaration order
    return next.length ? Object.keys(METRICS).filter((x) => next.includes(x)) : cur
  }), [])

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

  const finished = status?.status === 'ready' || session?.status === 'ready'
  /* Dimming says "nothing is arriving" — the UPLOADER has gone quiet, so what
     is on screen may be out of date. It used to also mean "no fight in
     progress", which washed the panels out every time a pull ended and made
     the numbers everybody reads right after a kill the hardest ones to read.
     A pull that has merely ENDED is not stale; it says so itself (`ended`).
     A replay has no uploader to be quiet, so there the only question is
     whether it has started. */
  const stale = replay ? !partial?.fight : !!(status && !status.uploader_online)
  // the replay block the server attaches to a replayed `partial` — what is
  // playing, where the cursor is, and whether it has run out
  const rep = partial?.replay || null

  const liveFight = partial?.fight || null
  const lastEnc = encounters.length ? encounters[encounters.length - 1] : null

  /* THE GAP, and why the rail's live row is never empty during a raid.

     A fight stops being `partial` the moment combat ends and only becomes an
     `encounter` row when the writer commits it, which is a second or two
     later. In between, the pull you were just watching existed nowhere in the
     navigation — click back to an earlier fight mid-pull and there was nothing
     to click to get back to. So the last live fight is HELD here until its
     record actually lands, and the row says `saving` while it waits.

     What normally clears it is the encounter count going up — a commit is the
     only thing that makes the held fight redundant. The timeout is the second
     exit and it is not belt-and-braces: a segment the raid never ENGAGED is
     not a fight and never becomes a row (`_ENGAGE_KINDS`, docs/parser.md), so
     without a cap, walking away from something that did not fight back would
     leave the rail saying `saving` until the next pull. A fresh pull clears it
     too, by replacing it. */
  const [held, setHeld] = useState(null)
  const committed = encounters.length
  useEffect(() => {
    if (liveFight) setHeld({ fight: liveFight, at: committed })
  }, [liveFight, committed])
  useEffect(() => {
    // a record arrived after the fight we are holding — that is this one
    setHeld((h) => (h && !liveFight && committed > h.at ? null : h))
  }, [committed, liveFight])
  useEffect(() => {
    if (!held || liveFight) return undefined
    const t = setTimeout(() => setHeld(null), HOLD_MS)
    return () => clearTimeout(t)
  }, [held, liveFight])
  // the session going away (or finalizing) takes the held fight with it
  useEffect(() => { if (!sessionId || finished || replay) setHeld(null) },
    [sessionId, finished, replay])

  /* The last pull, kept for as long as the page is about this night. `held` is
     about the rail's gap and expires; this is what the SCREEN falls back to,
     and between pulls the answer to "what is on the meter" is the fight that
     just happened, never an empty panel. Cleared only when the page changes
     what it is about. */
  const [lastFight, setLastFight] = useState(null)
  useEffect(() => { if (liveFight) setLastFight(liveFight) }, [liveFight])
  // a finalized night is not a night with a pull on the screen: the session was
  // rebuilt from raw and every fight in it is a record now
  useEffect(() => { setLastFight(null) }, [sessionId, replay, finished])

  /* What the middle column shows while the meter is switched off: the payload
     as it stood when the switch was thrown. Paused means the numbers STOP —
     not that the panel empties, which would make the switch read as a close
     button. A ref, because nothing on screen changes while it is parked. */
  const shown = liveFight || lastFight
  const parked = useRef(null)
  // `!parked.current` covers the page being OPENED with the meter already off:
  // park the first fight it sees, then hold it
  useEffect(() => {
    if (parseOn || !parked.current) parked.current = shown
  }, [parseOn, shown])

  /* What the rail's last row shows. Absent only when there is nothing to be
     live about: a finished night, or a replay, which owns the meter instead
     and says so in the bar.

     `combat` is a pull actually running: the server says a fight is open AND
     that combat has not stopped in it (`ended`). A pull whose damage has
     stopped is already `saving` — the writer holds the segment open for ten
     more seconds in case a late kill line joins it, and a rail row calling
     that "live" with a clock still running is the whole complaint. */
  const liveRow = useMemo(() => {
    if (replay || finished) return null
    const f = liveFight || held?.fight || null
    return {
      state: liveFight && !liveFight.ended ? 'combat' : (f ? 'saving' : 'idle'),
      name: f?.provisional_is_named ? f.provisional_name : (f ? 'Trash' : null),
      started_ts: f?.started_ts ?? null,
      elapsed_s: f?.elapsed_s ?? null,
      stale,
    }
  }, [replay, finished, liveFight, held, stale])

  const selEnc = useMemo(() => (selIds.length === 1
    ? encounters.find((e) => e.id === selIds[0]) || null : null), [encounters, selIds])
  /* The rows behind the selection — a zone block or a collapsed Trash ×N is
     several fights, and the parse's head is about all of them. */
  const selEncs = useMemo(() => selIds
    .map((i) => encounters.find((e) => e.id === i))
    .filter(Boolean), [encounters, selIds])

  /* The other clients you could be watching right now. A session the server
     still calls `receiving` is not one of them once it has stopped sending:
     it keeps that status for half an hour, which is right for the record and
     wrong for a row of buttons — an evening of alts logging in and out left
     five chips up, four of them pointing at nothing. */
  const others = useMemo(() => (sessions || []).filter((s) => (
    s.id !== sessionId && (Date.now() / 1000) - (s.last_ingest_ts || 0) <= QUIET_S
  )), [sessions, sessionId])

  /* Between pulls, the screen belongs to the fight that just ended — in its
     RECORDED form, because that is when the AoE audit and the death report are
     the thing everyone is asking about.

     `!held` is what keeps it the RIGHT fight. A pull whose record has not
     landed yet would otherwise put the PREVIOUS fight's parse on screen for
     the second or two in between, which reads as the meter jumping backwards.
     While the rail says `saving`, the middle keeps the pull that just ended —
     frozen, since `ended` stopped it — and swaps to the record when it
     arrives. */
  const showLastRecorded = sel == null && !replay && !liveFight && !held
    && lastEnc != null

  /* What a note filed right now is ABOUT: the named being pulled, or the zone
     if this is trash. The panel lets you override it; nothing is guessed
     server-side. */
  const context = useMemo(() => {
    const fallback = lastEnc ? lastEnc.zone : null
    if (sel == null && liveFight) {
      return {
        zone: liveFight.zone || fallback,
        mob: liveFight.provisional_is_named ? liveFight.provisional_name : null,
      }
    }
    const e = sel != null
      ? (selEnc || encounters.find((x) => x.id === selIds[0]) || null)
      : lastEnc
    if (!e) return { zone: fallback, mob: null }
    return {
      zone: e.zone || fallback,
      mob: selIds.length <= 1 && e.is_named ? e.name : null,
    }
  }, [sel, liveFight, selEnc, encounters, selIds, lastEnc])

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
          {/* Setting the overlay up is a BEFORE-the-raid job — the scene has
              to be positioned while nothing is happening — so the panel is on
              the idle page too. */}
          <div className="dashbar">
            <ErrorBoundary resetKey="overlaypanel-idle"><OverlayPanel /></ErrorBoundary>
            {canCurate && (
              <>
                <ReplayPicker active={null}
                              onStart={(id, speed) => setReplay({ id, speed })}
                              onStop={() => setReplay(null)} />
                <span className="muted">plays a recorded fight through this meter</span>
              </>
            )}
          </div>
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

  return (
    <div className="dashgrid">
      <div className="dashrail">
        {/* The rail is the whole navigation, live row included — there is no
            separate Live button above it any more. One list, in the order the
            night happened, and the pull in progress is its last row. */}
        <EncounterTree
          encounters={encounters}
          live={liveRow}
          sel={sel == null ? 'live' : String(sel)}
          onSelect={(key) => setSel(
            key === 'all' || key === 'live' || key == null ? null : String(key))}
          sessionLabel={session ? session.character_name : 'Tonight'}
          sub={session?.started_ts ? fmt.time(session.started_ts) : null}
          /* What is on screen while the raid runs, across from the name of the
             character whose night it is. All three are the same question —
             which parse is showing where — so they are one group, and the rail
             head is the only place on this page that is about the night as a
             whole rather than about one fight. */
          headActions={(
            <>
              <button className={`chip ${mini ? 'on' : ''}`} onClick={() => setMini(!mini)}
                      title={mini
                        ? 'Hide the mini timers and parse'
                        : 'Dock small timers and parse to the edge of the window'}>
                Mini
              </button>
              <button className={`chip ${parseOn ? 'on' : ''}`}
                      onClick={() => setParseOn(!parseOn)}
                      title={parseOn
                        ? 'Dim and pause the parse in the middle — the mini overlay keeps running'
                        : 'Bring the live parse back'}>
                Parse
              </button>
              {/* the stream overlay IS the mini parse, pointed at OBS instead
                  of at this window — so its options belong beside Mini */}
              <ErrorBoundary resetKey="overlaypanel"><OverlayPanel /></ErrorBoundary>
            </>
          )}
        />
      </div>

      <div className="dashmain">
        {/* The bar is for what is happening to this PAGE — a replay running, a
            second character to switch to, the way out to the full parse. It is
            not a status readout: the site header already carries whether ACT is
            connected and whether a fight is up, and a second pill saying the
            same thing in a different shape, beside a line count nobody acts on,
            was three of the same fact in three styles. */}
        {(replay || canCurate || others.length > 0 || selEnc) && (
        <div className="dashbar">
          {replay && (
            <>
              <span className="badge warn">replay</span>
              <span className="muted">
                {rep ? `${rep.name || 'fight'} · ${rep.zone || 'unknown zone'}` : 'loading the log…'}
                {rep ? ` · ${fmt.dur(rep.elapsed_s)} of ${fmt.dur(rep.span_s)}` : ''}
                {rep && rep.speed !== 1 ? ` · ${rep.speed}×` : ''}
                {rep?.done ? ' · finished' : ''}
              </span>
            </>
          )}
          {canCurate && (
            <ReplayPicker active={replay}
                          onStart={(id, speed) => { setSel(null); setReplay({ id, speed }) }}
                          onStop={() => { setReplay(null); setPartial(null) }} />
          )}
          {/* The page follows whichever log is being played on its own; these
              are for overriding that, and the dot says which one the follow
              would pick. Choosing one holds the page there until that log
              goes quiet (`QUIET_S`). */}
          {others.map((s) => (
            <button key={s.id} className="chip"
                    onClick={() => { setSel(null); setSessionId(s.id) }}
                    title={s.in_combat
                      ? `${s.character_name} is fighting right now`
                      : `Watch ${s.character_name} instead`}>
              {s.character_name}
              {s.in_combat && <i className="dot on" aria-hidden="true" />}
            </button>
          ))}
          {selEnc && (
            <Link className="chip" to={`/encounters/${selEnc.id}`}>Open the full parse</Link>
          )}
        </div>
        )}

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

        {sel != null ? (
          <ErrorBoundary resetKey={`rec-${sel}`}>
            <DashParse selIds={selIds} encs={selEncs} />
          </ErrorBoundary>
        ) : showLastRecorded ? (
          <ErrorBoundary resetKey={`rec-last-${lastEnc.id}`}>
            <DashParse selIds={[lastEnc.id]} encs={[lastEnc]}
                       note={finished ? null : 'last pull — waiting for the next one'} />
          </ErrorBoundary>
        ) : (
          <ErrorBoundary resetKey={`meter-live-${metrics.join('+')}`}>
            <LiveMeter
              fight={parseOn ? shown : parked.current}
              metrics={metrics}
              onToggle={toggleMetric}
              stale={stale}
              paused={!parseOn}
              showChart
              showTimers
            />
          </ErrorBoundary>
        )}
      </div>

      <div className="dashside">
        <ErrorBoundary resetKey={`notes-${context.zone}-${context.mob}`}>
          <RaidNotes zone={context.zone} mob={context.mob} />
        </ErrorBoundary>
      </div>

      {/* Docked to the window's edge, and deliberately NOT tied to what the
          main column is showing: the rail is what you read while looking at
          the game, so it stays on the pull in progress even when the middle
          of the page has been clicked back to an earlier fight. */}
      {mini && !finished && (
        <ErrorBoundary resetKey="mini">
          {/* the last pull, not just the running one: the rail is what you
              read while looking at the game, and between pulls "what just
              happened" is the question it is there to answer */}
          <MiniRail fight={shown} metrics={metrics} stale={stale}
                    onClose={() => setMini(false)} />
        </ErrorBoundary>
      )}
    </div>
  )
}
