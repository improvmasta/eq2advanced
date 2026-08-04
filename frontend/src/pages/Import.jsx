import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import UploadDrop from '../components/UploadDrop.jsx'
import { api, fmt } from '../lib/api.js'

/* Everything that gets combat data into the app, in one place and in the order
   you would reach for them: live during the raid, log files after it, and an
   ACT export when the parse you want already exists somewhere else.

   Each way in states what it is, whether it is ready, and what to press. The
   old page hid the live path on /characters and the file path behind a
   dropzone on two other screens, which made "how do I get my raid in here"
   a question with three answers and no page that gave them. */

function Method({ title, blurb, state, children }) {
  return (
    <section className={`card method ${state === 'soon' ? 'soon' : ''}`}>
      <div className="methodhead">
        <h2>{title}</h2>
        {state && <span className={`badge ${state === 'live' ? 'named' : ''}`}>
          {state === 'live' ? 'receiving' : state === 'soon' ? 'not yet' : state}
        </span>}
      </div>
      <p className="note">{blurb}</p>
      {children}
    </section>
  )
}

export default function Import() {
  const [sessions, setSessions] = useState(null)
  const [chars, setChars] = useState([])
  const [error, setError] = useState(null)
  const [confirmDel, setConfirmDel] = useState(null)   // session id awaiting confirm
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => {
    api.sessions().then((d) => setSessions(d.sessions)).catch((e) => setError(e.message))
    api.characters().then((d) => setChars(d.characters)).catch(() => {})
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

  async function remove(id) {
    setBusy(true)
    try { await api.deleteSession(id) } catch (e) { setError(e.message) }
    setBusy(false)
    setConfirmDel(null)
    refresh()
  }

  const receiving = sessions?.filter((s) => s.source === 'live' && s.status === 'receiving') ?? []
  const paired = chars.filter((c) => c.token_count > 0)
  const ready = sessions?.filter((s) => s.status === 'ready') ?? []
  const fightTotal = ready.reduce((n, s) => n + (s.encounter_count || 0), 0)

  return (
    <>
      <div className="pagehead">
        <h1>Import</h1>
        <span className="actions"><Link className="btnlink" to="/">Back to parses</Link></span>
      </div>
      {error && <p className="err">{error}</p>}

      <div className="methods">
        <Method
          title="Live link"
          state={receiving.length ? 'live' : paired.length ? 'paired' : null}
          blurb="Streams your log as you play. Pair once per PC with a device token."
        >
          {receiving.length > 0 ? (
            <p>
              <Link className="btnlink" to="/live">Watch the live raid →</Link>
            </p>
          ) : (
            <>
              <p className="muted">
                {paired.length
                  ? `${paired.length} character${paired.length === 1 ? '' : 's'} paired — start the uploader to stream.`
                  : 'No device paired yet.'}
              </p>
              <Link className="btnlink" to="/characters">
                {paired.length ? 'Manage pairing' : 'Pair a device'}
              </Link>
            </>
          )}
        </Method>

        <Method
          title="Log files"
          blurb="Drop one or more eq2log_*.txt files. Overlap between files is deduped."
        >
          <UploadDrop compact onUploaded={refresh} />
        </Method>

        <Method
          title="ACT export"
          state="soon"
          blurb="For parses that only exist inside ACT. The XML history export is the
                 target — it carries per-combatant and per-ability rows."
        >
          <ul className="fineprint">
            <li><b>XML export</b> — the one to send.</li>
            <li><b>.act file</b> — compressed binary, may not be readable.</li>
            <li><b>SQL / ODBC export</b> — fallback if the XML is thin.</li>
          </ul>
          <p className="muted">Not built yet.</p>
        </Method>
      </div>

      <div className="card">
        <div className="drillhead">
          <h2>Imported logs</h2>
          <span className="muted" style={{ marginLeft: 'auto' }}>
            {sessions?.length ?? 0} file{sessions?.length === 1 ? '' : 's'} ·{' '}
            {fmt.num(fightTotal)} fights
          </span>
        </div>
        <p className="note">
          Raw files, kept so a parser fix can be replayed. Deleting one removes
          every fight it contributed.
        </p>
        {sessions === null && <p className="muted">Loading…</p>}
        {sessions?.length === 0 && <p className="muted">Nothing imported yet.</p>}
        {sessions?.length > 0 && (
          <div className="tablewrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="l">File</th><th className="l">Character</th><th>Date</th>
                  <th>Lines</th><th>Fights</th><th className="l">Status</th><th />
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id}>
                    <td className="name l">
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
                    <td className="rowactions">
                      {s.status === 'ready' && !s.pruned && (
                        <button className="chip" onClick={() => reparse(s.id)}>reparse</button>
                      )}
                      {confirmDel === s.id ? (
                        <>
                          <button className="chip danger" disabled={busy}
                                  onClick={() => remove(s.id)}>delete for good</button>
                          <button className="chip" onClick={() => setConfirmDel(null)}>cancel</button>
                        </>
                      ) : (
                        <button className="chip danger" onClick={() => setConfirmDel(s.id)}>
                          delete
                        </button>
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
