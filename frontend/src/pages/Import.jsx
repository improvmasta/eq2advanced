import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import ShotDrop from '../components/ShotDrop.jsx'
import ShotViewer, { ShotThumb } from '../components/ShotViewer.jsx'
import UploadDrop from '../components/UploadDrop.jsx'
import { api, fmt, sessionLabel } from '../lib/api.js'

/* The two ways combat data gets in, and what came in so far.

   The plugin box shows one thing at a time, and which thing depends on whether
   a key is in use. Unpaired, it is a setup manual and nothing else: one
   numbered list carrying the download, the key and the ACT steps in the order
   you do them. Paired, setup is finished work — the box leads with whether the
   plugin is talking to us and folds the manual away. Sharing is deliberately
   absent here; it lives on the Sharing page, and the box says so in one line
   instead of embedding the controls.

   Privacy is a pagehead disclosure, not a card: it is a reference someone reads
   once and then wants out of the way of the thing they came to do. Two lists
   because the question is "which side is my channel on". See pipeline/redact.py. */
function Privacy({ open, onClose }) {
  if (!open) return null
  return (
    <section className="card privacy" role="region" aria-label="What is stored">
      <div className="privacygrid">
        <div className="pcol drop">
          <h3>Removed before storage</h3>
          <ul>
            <li>Tells, sent and received</li>
            <li>Guild and officer chat</li>
            <li>Channels — LFG, General, Auction, Crafting, custom</li>
            <li>Local <code>/say</code></li>
            <li>Group and raid chat outside a fight</li>
          </ul>
        </div>
        <div className="pcol keep">
          <h3>Kept</h3>
          <ul>
            <li>Combat: damage, heals, deaths, cures, zoning</li>
            <li>Group and raid chat during a fight, ±90s</li>
            <li>NPC dialogue</li>
          </ul>
        </div>
      </div>
      <p className="fineprint">
        Filtering happens as the upload streams; the unfiltered file is never
        written to disk. Unrecognised channels are dropped, not kept. Removed
        lines are ones the parser ignores, so parses are unaffected — the
        per-file count is in <b>Chat cut</b> below.
      </p>
      <p className="fineprint">
        Admin accounts get no extra visibility: an unshared raid returns 404 for
        an admin the same as for a stranger. Anyone with server access can read
        the database — the limit is what gets stored, not who asks.
      </p>
      <button className="linklike" onClick={onClose}>Close</button>
    </section>
  )
}

function Method({ title, blurb, state, children }) {
  return (
    <section className="card method">
      <div className="methodhead">
        <h2>{title}</h2>
        {state && <span className={`badge ${state === 'live' ? 'live' : ''}`}>{state}</span>}
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
          Made before keys were viewable. Still works; Refresh replaces it with
          a readable one.
        </p>
      )}
      {error && <p className="err">{error}</p>}
    </div>
  )
}

/* Setup as one list, in the order it is done. The download, the key and the
   ACT steps used to be three stacked blocks that read as three unrelated
   things; numbering them is what makes it obvious the key belongs to step 3
   and not to the box at large. */
function PluginSetup({ plugin, tokens, reloadTokens }) {
  return (
    <ol className="steps setupflow">
      {/* Unblock the zip, not the extracted DLL — Explorer copies the web mark
          onto what it unpacks and ACT won't load a marked one. */}
      <li>
        Download the plugin, then right-click the .zip → <b>Properties</b> →{' '}
        <b>Unblock</b>, and extract it.
        <div className="stepctl">
          <a className="btnlink" href="/api/plugin/download" download>
            Download {plugin?.download_name || 'EQ2Advanced.zip'}
          </a>
          {plugin?.available && (
            <span className="meta">
              {fmt.bytes(plugin.download_size ?? plugin.size)} · built {fmt.date(plugin.built_ts)}
            </span>
          )}
        </div>
      </li>
      <li>In ACT: <b>Plugins → Plugin Listing → Browse</b>, pick <b>EQ2Advanced.dll</b>, <b>Add/Enable</b>.</li>
      <li>
        Paste this key into the plugin's <b>eq2advanced</b> tab.
        <div className="stepctl">
          <ApiKey tokens={tokens} reload={reloadTokens} />
        </div>
      </li>
      <li>
        Tick <b>Send my combat log as I play</b>. For old raids, use{' '}
        <b>Import logs you already have</b> — point it at your whole logs folder.
      </li>
    </ol>
  )
}

export default function Import() {
  const [sessions, setSessions] = useState(null)
  const [tokens, setTokens] = useState(null)
  const [error, setError] = useState(null)
  const [confirmDel, setConfirmDel] = useState(null)
  const [busy, setBusy] = useState(false)
  const [plugin, setPlugin] = useState(null)
  const [showPrivacy, setShowPrivacy] = useState(false)
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
        <span className="sub">Plugin or file upload.</span>
        <span className="actions">
          <button className="btnlink disclose" aria-expanded={showPrivacy}
                  onClick={() => setShowPrivacy((v) => !v)}>
            What is stored <span className="caret">{showPrivacy ? '▴' : '▾'}</span>
          </button>
          <Link className="btnlink" to="/">Back to parses</Link>
        </span>
      </div>
      {error && <p className="err">{error}</p>}

      <Privacy open={showPrivacy} onClose={() => setShowPrivacy(false)} />

      <div className="methods">
        <Method
          title="ACT plugin"
          state={receiving.length ? 'live' : paired ? 'paired' : null}
          blurb={paired
            ? 'Uploads while you raid, and bulk-imports an existing log folder.'
            : 'Uploads while you raid. Also bulk-imports an existing log folder. One-time setup.'}
        >
          {!paired && <PluginSetup plugin={plugin} tokens={tokens} reloadTokens={loadTokens} />}

          {paired && (
            <>
              <div className="pluginstatus">
                <span>
                  <b>{receiving.length ? 'Receiving' : 'Connected'}</b>
                  {lastSeen > 0
                    ? <span className="muted"> · last upload {fmt.date(lastSeen)} at {fmt.time(lastSeen)}</span>
                    : <span className="muted"> · no uploads yet</span>}
                </span>
              </div>

              {receiving.length > 0 && (
                <p className="livecta">
                  <Link className="btnlink" to="/live">Watch the live raid →</Link>
                </p>
              )}

              {/* The download folds away with the steps, which the old layout
                  refused to do. Someone coming back for a rebuilt DLL is
                  looking for the same drawer the steps are in, and the summary
                  names it — one click, and the box stays about whether the
                  plugin is talking to us. */}
              <details className="setupfold">
                <summary>Setup, download &amp; API key</summary>
                <PluginSetup plugin={plugin} tokens={tokens} reloadTokens={loadTokens} />
              </details>
            </>
          )}

          <p className="fineprint">
            The plugin only sends logs. Who sees your raids is set on{' '}
            <Link to="/groups">Sharing</Link>.
          </p>
        </Method>

        <Method
          title="Log files"
          blurb="Drag in log files. Nothing to install."
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
          Filtered files are kept so parser improvements can be replayed over
          them. Deleting one removes every fight it contributed.
          <b> Chat cut</b> is the private lines dropped on import.
        </p>

        {working.length > 0 && (
          <p className="muted">
            Parsing {working.length} file{working.length === 1 ? '' : 's'}…
          </p>
        )}
        {sessions === null && !seen.current && <p className="muted">Loading…</p>}
        {sessions?.length === 0 && (
          <p className="muted">No logs yet.</p>
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
                  <th title="private chat lines removed before this file was stored">
                    Chat cut
                  </th>
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
                        ? <Link to={`/sessions/${s.id}`}>{sessionLabel(s)}</Link>
                        : sessionLabel(s)}
                      {s.status === 'receiving' && <span className="badge live">live</span>}
                    </td>
                    <td className="l">{s.character_name}</td>
                    <td className="l muted">{s.source === 'live' ? 'plugin' : 'upload'}</td>
                    <td>{fmt.date(s.started_ts ?? s.created_ts)}</td>
                    <td>{s.raw_deleted_ts ? '—' : fmt.bytes(s.src_bytes)}</td>
                    <td>{fmt.num(s.line_count)}</td>
                    <td className={s.redacted_lines ? 'cut' : 'muted'}>
                      {s.redacted_lines ? fmt.num(s.redacted_lines) : '—'}
                    </td>
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

      <Parseshots />
    </div>
  )
}

/* Parses imported from a screenshot. They live here beside the logs because
   this is the page that answers "what have I put in", but they are a separate
   card and say so plainly: a shot is somebody else's parse read off an image,
   it contributes no fights, and it is only ever seen on /compare. */
function Parseshots() {
  const [items, setItems] = useState(null)
  const [error, setError] = useState('')
  const [confirm, setConfirm] = useState(null)
  const [viewing, setViewing] = useState(null)   // the shot whose image is open

  const refresh = useCallback(() => {
    api.parseshots().then((d) => setItems(d.items)).catch((e) => setError(e.message))
  }, [])
  useEffect(() => { refresh() }, [refresh])

  async function remove(id) {
    try { await api.deleteParseshot(id) } catch (e) { setError(e.message) }
    setConfirm(null)
    refresh()
  }

  return (
    <div className="card">
      <div className="drillhead">
        <h2>Screenshot imports</h2>
        <span className="muted" style={{ marginLeft: 'auto' }}>
          {items?.length ?? 0} parse{items?.length === 1 ? '' : 's'}
        </span>
      </div>
      <p className="note">
        A parse read off an ACT screenshot — the way somebody else's numbers
        arrive when all you have is an image from Discord. These add no fights
        and change no totals; they exist to sit beside your own parse on{' '}
        <Link to="/compare">Compare</Link>. Click a thumbnail to see the
        screenshot it was read from — a re-encoded copy, kept private to you.
      </p>
      {error && <p className="err">{error}</p>}
      <ShotDrop onImported={refresh} />

      {items === null && <p className="muted">Loading…</p>}
      {items?.length === 0 && <p className="muted">Nothing imported yet.</p>}
      {items?.length > 0 && (
        <div className="tablewrap">
          <table className="data">
            <thead>
              <tr>
                <th className="l">Shot</th>
                <th className="l">Fight</th>
                <th className="l">Character</th>
                <th className="l">Zone</th>
                <th>Length</th>
                <th className="l">Imported</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.id}>
                  {/* The picture is the only evidence behind the columns
                      arithmetic can't check, so it sits in the row rather than
                      a detail page. */}
                  <td className="l">
                    {s.has_image
                      ? <ShotThumb shot={s} onOpen={() => setViewing(s)} />
                      : <span className="muted">—</span>}
                  </td>
                  <td className="l">
                    <Link to={`/compare?c=shot:${s.id}:parse`}>
                      {s.encounter || 'Unnamed fight'}
                    </Link>
                    {s.kind === 'heal' && <span className="muted"> healing</span>}
                  </td>
                  <td className="l">{s.character_name || '—'}</td>
                  <td className="l">{s.zone || '—'}</td>
                  <td>{s.duration_s ? fmt.dur(s.duration_s) : '—'}</td>
                  {/* when_text is what ACT PRINTED, kept as a string: an
                      undated shot has none, and inventing one from the import
                      time would date somebody else's raid to today. */}
                  <td className="l">{s.when_text || fmt.date(s.created_ts)}</td>
                  <td className="r">
                    {confirm === s.id ? (
                      <>
                        <span className="muted">Delete? </span>
                        <button className="chip danger" onClick={() => remove(s.id)}>Yes</button>
                        <button className="chip" onClick={() => setConfirm(null)}>No</button>
                      </>
                    ) : (
                      <button className="chip danger" onClick={() => setConfirm(s.id)}>
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
      {viewing && <ShotViewer shot={viewing} onClose={() => setViewing(null)} />}
    </div>
  )
}
