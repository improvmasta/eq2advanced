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
      {/* A box that explains itself needs no line above it saying so — the
          drop target for log files is a drop target for log files. */}
      {blurb && <p className="note">{blurb}</p>}
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
                <button className="chip danger armed" disabled={busy} onClick={generate}>
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
              {plugin.version && <>v{plugin.version} · </>}
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
                  <Link className="btnlink" to="/live">Live Parser →</Link>
                </p>
              )}

              {/* What the header pill was pointing at. It says what the new
                  build DOES, because "a version is available" is not a reason
                  to reinstall anything — and it names the version they are on,
                  so somebody with two machines can tell which one this is
                  about. Uploading keeps working on the old one; nothing here
                  is an error.

                  The what-it-does sentence comes from the SERVER (`notes`,
                  shipped in refdata beside the DLL), not from here. It was
                  hardcoded once, describing 0.2.0's faster cadence, and it was
                  still saying that when 0.2.1 shipped a data-loss fix — so the
                  pill led people to a paragraph about a change they already had.
                  Release copy has to travel with the release. */}
              {plugin?.update_available && (
                <p className="pluginupdate">
                  <b>Plugin {plugin.version} is ready.</b>{' '}
                  {plugin.notes || 'Download it below and re-add the DLL in ACT.'}
                  {' '}You are uploading with {plugin.your_version}.
                </p>
              )}

              {/* The download folds away with the steps, which the old layout
                  refused to do. Someone coming back for a rebuilt DLL is
                  looking for the same drawer the steps are in, and the summary
                  names it — one click, and the box stays about whether the
                  plugin is talking to us. An update is the one time it opens
                  itself: they followed a pill here to get at exactly this. */}
              <details className="setupfold" open={!!plugin?.update_available}>
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

        <Method title="Log files">
          <UploadDrop onUploaded={refresh} />
        </Method>
      </div>

      {/* Two lists of what has come in, and they are not the same size of
          thing: the logs are the site's own parses and the shots are claims
          read off somebody else's picture. Two thirds and one third, side by
          side — the shot column is a narrow strip of entries rather than a
          table, because seven columns squeezed into a third of the page is a
          table nobody can read. */}
      <div className="importcols">
      <div className="card">
        <div className="drillhead">
          <h2>Imported logs</h2>
          <span className="muted" style={{ marginLeft: 'auto' }}>
            {sessions?.length ?? 0} file{sessions?.length === 1 ? '' : 's'} ·{' '}
            {fmt.num(fightTotal)} fights · {fmt.num(lineTotal)} lines
          </span>
        </div>

        {working.length > 0 && (
          <p className="muted">
            Parsing {working.length} file{working.length === 1 ? '' : 's'}…
          </p>
        )}
        {sessions === null && !seen.current && <p className="muted">Loading…</p>}
        {sessions?.length === 0 && (
          <p className="muted">No logs yet.</p>
        )}

        {/* Ten rows and then it scrolls, header pinned. A backfill of somebody's
            logs folder is fifty files, and fifty rows of ingest detail pushed
            the screenshot imports off the bottom of the page — this card is a
            record, not the thing you came to read. */}
        {sessions?.length > 0 && (
          <div className="tablewrap sticky rows10">
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
                          <button className="chip danger armed" disabled={busy}
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
    </div>
  )
}

/* Parses imported from a screenshot. They live here beside the logs because
   this is the page that answers "what have I put in", but they are a separate
   card and say so plainly: a shot is somebody else's parse read off an image,
   it contributes no fights, and it is only ever seen on /compare.

   It is a COLUMN, a third of the page wide, and the drop slot at the top of it
   is Compare's — a + in a heavy dashed box over a dimmed ACT window, the same
   object saying "another parse goes here" and taking one. What follows is a
   list of entries rather than a table: seven columns in a third of a page is a
   table that can only be read sideways, and the thumbnail is the widest thing
   in each row anyway. */
function Parseshots() {
  const [items, setItems] = useState(null)
  const [error, setError] = useState('')
  const [confirm, setConfirm] = useState(null)
  const [viewing, setViewing] = useState(null)   // the shot whose image is open
  const [editing, setEditing] = useState(null)   // the shot being named

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
    <div className="card shotcol">
      <div className="drillhead">
        <h2>Screenshot imports</h2>
        <span className="muted" style={{ marginLeft: 'auto' }}>
          {items?.length ?? 0} parse{items?.length === 1 ? '' : 's'}
        </span>
      </div>
      {error && <p className="err">{error}</p>}
      <ShotDrop
        className="shotslot"
        label={<p className="muted">Drop an ACT screenshot to import a parse…</p>}
        onImported={refresh}
      />

      {items === null && <p className="muted">Loading…</p>}
      {items?.length === 0 && (
        <p className="fineprint">
          Somebody else's numbers, read off an image from Discord. They add no
          fights and change no totals — they sit beside your own parse on{' '}
          <Link to="/compare">Compare</Link>.
        </p>
      )}
      {items?.length > 0 && (
        <ul className="shotlist">
          {items.map((s) => (
            <li key={s.id}>
              {/* The picture is the only evidence behind the columns
                  arithmetic can't check, so it travels with the entry rather
                  than living on a detail page. */}
              {s.has_image && <ShotThumb shot={s} onOpen={() => setViewing(s)} />}
              <div className="sl">
                <Link className="t" to={`/compare?c=shot:${s.id}:parse`}>
                  {s.encounter || 'Unnamed fight'}
                </Link>
                <span className="m">
                  {[s.character_name, s.zone].filter(Boolean).join(' · ') || (
                    <i>nothing read off the title bar</i>
                  )}
                </span>
                <span className="m">
                  {s.kind === 'heal' ? 'Healing' : 'Damage'}
                  {s.duration_s ? ` · ${fmt.dur(s.duration_s)}` : ''}
                  {/* when_text is what ACT PRINTED, kept as a string: an
                      undated shot has none, and inventing one from the import
                      time would date somebody else's raid to today. */}
                  {' · '}{s.when_text || fmt.date(s.created_ts)}
                </span>
              </div>
              <span className="sa">
                <button className={`chip ${editing === s.id ? 'on' : ''}`}
                        title="Name this parse — who, where, which fight"
                        onClick={() => setEditing(editing === s.id ? null : s.id)}>
                  ✎
                </button>
                {confirm === s.id ? (
                  <>
                    <button className="chip danger armed" onClick={() => remove(s.id)}>Yes</button>
                    <button className="chip" onClick={() => setConfirm(null)}>✕</button>
                  </>
                ) : (
                  <button className="chip danger" title="Delete this import"
                          onClick={() => setConfirm(s.id)}>🗑</button>
                )}
              </span>
              {editing === s.id && (
                <ShotEdit shot={s} onDone={(saved) => {
                  setEditing(null)
                  if (saved) refresh()
                }} />
              )}
            </li>
          ))}
        </ul>
      )}
      {viewing && <ShotViewer shot={viewing} onClose={() => setViewing(null)} />}
    </div>
  )
}

/* Naming a shot the reader can read and the OCR could not.

   A screenshot cropped to the table carries no title bar, so the character,
   the zone and the fight arrive empty and the import stays `Unnamed fight` for
   the rest of its life — while the person who dropped it knows perfectly well
   whose parse it is. These are all CLAIMS, which is what the row already was;
   nothing here touches a figure in the table. Those are checked against each
   other at import and a typed cell would be the only number on the page with
   no evidence behind it — the review step this feature deliberately does not
   have (docs/compare-import.md).

   The LENGTH is the one number, and only where there isn't one: it is fitted
   from the table (the mode of Damage/EncDPS over every row), which beats both
   the title bar and any human — so a shot that has one shows it and won't take
   a replacement. A shot with none refuses to show per-second numbers at all,
   and a length off the reader's own clock beats that refusal. */
/* `mm:ss` or `h:mm:ss`, which is how ACT prints it and how anybody reading a
   screenshot would type it back. Null for anything else — a length that failed
   to parse must not silently become a number. */
function parseClock(text) {
  const parts = text.trim().split(':')
  if (parts.length < 2 || parts.length > 3) return null
  if (!parts.every((p) => /^\d{1,3}$/.test(p))) return null
  const n = parts.map(Number)
  if (n.slice(1).some((v) => v > 59)) return null
  return parts.length === 3 ? (n[0] * 3600) + (n[1] * 60) + n[2] : (n[0] * 60) + n[1]
}

function ShotEdit({ shot, onDone }) {
  const [form, setForm] = useState(() => ({
    character_name: shot.character_name || '',
    zone: shot.zone || '',
    encounter: shot.encounter || '',
    when_text: shot.when_text || '',
    kind: shot.kind === 'heal' ? 'heal' : 'damage',
    length: shot.duration_s ? fmt.clock(shot.duration_s) : '',
  }))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k) => (ev) => setForm((f) => ({ ...f, [k]: ev.target.value }))
  const fitted = shot.duration_s != null

  async function save() {
    const patch = {
      character_name: form.character_name.trim() || null,
      zone: form.zone.trim() || null,
      encounter: form.encounter.trim() || null,
      when_text: form.when_text.trim() || null,
      kind: form.kind,
    }
    if (!fitted && form.length.trim()) {
      const secs = parseClock(form.length)
      if (!secs) { setErr('Length is mm:ss — 4:30, or 1:04:30 for an hour.'); return }
      patch.duration_s = secs
    }
    setBusy(true)
    setErr('')
    try {
      await api.updateParseshot(shot.id, patch)
      onDone(true)
    } catch (e) {
      setErr(e.message)
      setBusy(false)
    }
  }

  return (
    // Enter files it, Escape drops it — from a FIELD only, so Enter on the
    // Cancel button still cancels.
    <div className="shotedit" onKeyDown={(ev) => {
      if (ev.key === 'Enter' && ev.target.tagName === 'INPUT' && !busy) save()
      if (ev.key === 'Escape') onDone(false)
    }}>
      <label>Character
        <input type="text" value={form.character_name} onChange={set('character_name')}
               placeholder="whose parse this is" autoFocus />
      </label>
      <label>Zone
        <input type="text" value={form.zone} onChange={set('zone')} placeholder="where" />
      </label>
      <label>Fight
        <input type="text" value={form.encounter} onChange={set('encounter')}
               placeholder="which pull" />
      </label>
      <label>When
        <input type="text" value={form.when_text} onChange={set('when_text')}
               placeholder="as ACT printed it" />
      </label>
      <label>Shows
        <select value={form.kind} onChange={set('kind')}>
          <option value="damage">Damage</option>
          <option value="heal">Healing</option>
        </select>
      </label>
      <label>Length
        <input type="text" value={form.length} onChange={set('length')} disabled={fitted}
               placeholder="mm:ss"
               title={fitted
                 ? 'Read off the table — every row agreeing on Damage ÷ EncDPS, '
                   + 'which is a better clock than the title bar'
                 : 'The shot carries no clock, so per-second numbers cannot be '
                   + 'worked out until you give it one'} />
      </label>
      {err && <p className="err">{err}</p>}
      <div className="row">
        <button disabled={busy} onClick={save}>Save</button>
        <button className="chip" disabled={busy} onClick={() => onDone(false)}>Cancel</button>
      </div>
    </div>
  )
}
