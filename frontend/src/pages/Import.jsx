import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import UploadDrop from '../components/UploadDrop.jsx'
import { api, fmt } from '../lib/api.js'

/* The two ways combat data gets in, and what came in so far.

   The plugin box shows one thing at a time: the install manual until a device
   is paired, a quiet status line after — the steps stay one click away. Sharing
   is deliberately absent here; it lives on the Sharing page, and the box says
   so in one line instead of embedding the controls. */

function Method({ title, blurb, state, children }) {
  return (
    <section className="card method">
      <div className="methodhead">
        <h2>{title}</h2>
        {state && <span className={`badge ${state === 'live' ? 'named' : ''}`}>{state}</span>}
      </div>
      <p className="note">{blurb}</p>
      {children}
    </section>
  )
}

/* The API key, the way Sonarr/Radarr present theirs, because that is what
   everyone running a home server already knows: a persistent masked field with
   Show / Copy / Refresh. One key per account — every device pastes the same
   one, and Refresh revokes them all at once. No QR, nothing shown-once: the
   key is readable here whenever you need it (it can send logs and nothing
   else, which is what makes that fine). */
function ApiKey({ tokens, reload }) {
  const [show, setShow] = useState(false)
  const [copied, setCopied] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  if (tokens === null) return null
  const active = tokens.filter((t) => !t.revoked_ts)
  const key = active[0]   // newest first; Refresh keeps this the only one

  async function generate() {
    setBusy(true); setError(null)
    try { await api.refreshToken('ACT plugin'); setShow(true); setConfirm(false); reload() }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="apikey">
      <div className="keyrow">
        <span className="k">API key</span>
        {key ? (
          <>
            <code className="keyfield">
              {show && key.token ? key.token : '•'.repeat(28)}
            </code>
            <button className="chip" disabled={!key.token}
                    title={key.token ? '' : 'This key predates viewable keys — Refresh to replace it'}
                    onClick={() => setShow((v) => !v)}>
              {show ? 'Hide' : 'Show'}
            </button>
            <button className="chip" disabled={!key.token}
                    onClick={() => {
                      navigator.clipboard?.writeText(key.token)
                      setCopied(true); setTimeout(() => setCopied(false), 1500)
                    }}>
              {copied ? 'Copied' : 'Copy'}
            </button>
            {confirm ? (
              <>
                <button className="chip danger" disabled={busy} onClick={generate}>
                  refresh — old key stops working
                </button>
                <button className="chip" onClick={() => setConfirm(false)}>cancel</button>
              </>
            ) : (
              <button className="chip" disabled={busy} onClick={() => setConfirm(true)}>
                Refresh
              </button>
            )}
          </>
        ) : (
          <button disabled={busy} onClick={generate}>Generate API key</button>
        )}
      </div>
      {key && !key.token && (
        <p className="fineprint">
          This key was made before keys were viewable. It still works —
          Refresh replaces it with one you can read here.
        </p>
      )}
      {error && <p className="err">{error}</p>}
    </div>
  )
}

export default function Import() {
  const [sessions, setSessions] = useState(null)
  const [tokens, setTokens] = useState(null)
  const [error, setError] = useState(null)
  const [confirmDel, setConfirmDel] = useState(null)
  const [busy, setBusy] = useState(false)
  const [plugin, setPlugin] = useState(null)
  const [showSteps, setShowSteps] = useState(false)
  const seen = useRef(false)

  const refresh = useCallback(() => {
    api.sessions().then((d) => setSessions(d.sessions)).catch((e) => setError(e.message))
  }, [])
  const loadTokens = useCallback(() => {
    api.tokens().then((d) => setTokens(d.tokens)).catch((e) => setError(e.message))
  }, [])

  useEffect(() => { refresh(); loadTokens() }, [refresh, loadTokens])
  useEffect(() => { api.plugin().then(setPlugin).catch(() => {}) }, [])

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
  const working = sessions?.filter((s) => s.status === 'parsing') ?? []
  const active = tokens?.filter((t) => !t.revoked_ts) ?? []
  const paired = active.length > 0
  const lastSeen = active.reduce((m, t) => Math.max(m, t.last_seen_ts || 0), 0)
  const ready = sessions?.filter((s) => s.status === 'ready') ?? []
  const fightTotal = ready.reduce((n, s) => n + (s.encounter_count || 0), 0)
  const lineTotal = ready.reduce((n, s) => n + (s.line_count || 0), 0)
  if (sessions) seen.current = true

  return (
    <div className="manage">
      <div className="pagehead">
        <h1>Import</h1>
        <span className="sub">Get your raids in. Two ways, both take a minute.</span>
        <span className="actions"><Link className="btnlink" to="/">Back to parses</Link></span>
      </div>
      {error && <p className="err">{error}</p>}

      <div className="methods">
        <Method
          title="ACT plugin"
          state={receiving.length ? 'live' : paired ? 'paired' : null}
          blurb="Sends your log while you raid, and uploads your old logs in bulk. Set it up once."
        >
          {paired && (
            <div className="pluginstatus">
              <span>
                <b>Key in use</b>
                {lastSeen > 0
                  ? <span className="muted"> · last upload {fmt.date(lastSeen)} {fmt.time(lastSeen)}</span>
                  : <span className="muted"> · nothing uploaded with it yet</span>}
              </span>
              <button className="linklike" onClick={() => setShowSteps((v) => !v)}>
                {showSteps ? 'Hide install steps' : 'Install steps'}
              </button>
            </div>
          )}

          {/* The file itself is never collapsed: someone already paired is who
              comes back for it when the DLL is rebuilt. Only the steps hide. */}
          <p style={{ marginTop: paired ? 10 : 4 }}>
            <a className="btnlink" href="/api/plugin/download" download>
              Download {plugin?.download_name || 'EQ2Advanced.zip'}
            </a>
            {plugin?.available && (
              <span className="muted" style={{ marginLeft: 8, fontSize: 'var(--fs-xs)' }}>
                {fmt.bytes(plugin.download_size ?? plugin.size)} · built {fmt.date(plugin.built_ts)}
              </span>
            )}
          </p>

          {(!paired || showSteps) && (
            <ol className="steps">
              {/* Unblock the zip, not the extracted DLL — Explorer copies the
                  web mark onto what it unpacks and ACT won't load a marked one. */}
              <li>Right-click the .zip → <b>Properties</b> → <b>Unblock</b>, then extract.</li>
              <li>In ACT: <b>Plugins → Plugin Listing → Browse</b>, pick <b>EQ2Advanced.dll</b>, <b>Add/Enable</b>.</li>
              <li>Copy the API key below into the plugin's <b>eq2advanced</b> tab.</li>
              <li>
                Tick <b>Send my combat log as I play</b>. For old raids, use{' '}
                <b>Import logs you already have</b> — point it at your whole logs folder.
              </li>
            </ol>
          )}

          <ApiKey tokens={tokens} reload={loadTokens} />

          {receiving.length > 0 && (
            <p style={{ marginTop: 8 }}>
              <Link className="btnlink" to="/live">Watch the live raid →</Link>
            </p>
          )}

          <p className="fineprint">
            The plugin only sends logs. Who sees your raids is set on the{' '}
            <Link to="/groups">Sharing</Link> page.
          </p>
        </Method>

        <Method
          title="Log files"
          blurb="Already have logs? Drop them in. Nothing to install."
        >
          <UploadDrop onUploaded={refresh} />
        </Method>
      </div>

      <div className="card">
        <div className="drillhead">
          <h2>Imported logs</h2>
          <span className="muted" style={{ marginLeft: 'auto' }}>
            {sessions?.length ?? 0} file{sessions?.length === 1 ? '' : 's'} ·{' '}
            {fmt.num(fightTotal)} fights · {fmt.num(lineTotal)} lines
          </span>
        </div>
        <p className="note">
          Every log you've sent. The raw file is kept so a parser improvement can be
          replayed over it; deleting one removes every fight it contributed.
        </p>

        {working.length > 0 && (
          <p className="muted">
            Parsing {working.length} file{working.length === 1 ? '' : 's'}…
          </p>
        )}
        {sessions === null && !seen.current && <p className="muted">Loading…</p>}
        {sessions?.length === 0 && (
          <p className="muted">
            Nothing yet — drop a log above, or set up the plugin and it fills itself.
          </p>
        )}

        {sessions?.length > 0 && (
          <div className="tablewrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="l">File</th>
                  <th className="l">Character</th>
                  <th className="l">Via</th>
                  <th>Date</th>
                  <th>Size</th>
                  <th>Lines</th>
                  <th>Fights</th>
                  <th className="l">Status</th>
                  <th />
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
                    <td className="l muted">{s.source === 'live' ? 'plugin' : 'upload'}</td>
                    <td>{fmt.date(s.started_ts ?? s.created_ts)}</td>
                    <td>{s.raw_deleted_ts ? '—' : fmt.bytes(s.src_bytes)}</td>
                    <td>{fmt.num(s.line_count)}</td>
                    <td>{s.encounter_count}</td>
                    <td className={`l status-${s.status}`}>
                      {s.status === 'receiving' ? 'receiving…'
                        : s.status === 'parsing' ? 'parsing…'
                        : s.status}
                      {s.status === 'error' && s.error ? ` — ${s.error.slice(0, 80)}` : ''}
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
    </div>
  )
}
