import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import QRCode from 'qrcode'
import UploadDrop from '../components/UploadDrop.jsx'
import AutoShare from '../components/AutoShare.jsx'
import { api, fmt } from '../lib/api.js'

/* The two ways combat data gets in, and what came in so far.

   Everything a raider needs is on this page: the plugin download, pairing, the
   dropzone, and the list. It used to send people to /characters to pair, which
   is a page they should never have to find — and to a third "ACT export" box
   for a feature that does not exist. Both are gone. */

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

/* Pairing, in the box with the download, because it is step 2 of installing the
   plugin and nothing else.

   No "which character?" prompt: a token belongs to the ACCOUNT, and the plugin
   reads the character off the log ACT is tailing. Asking at pairing time was
   asking the one question nobody can answer yet — you pair on a Tuesday and
   play whichever alt you feel like on Friday — and it made alts need a second
   token for no reason. */
function Pairing() {
  const [tokens, setTokens] = useState(null)
  const [minted, setMinted] = useState(null)     // {token, qr}
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(false)

  const load = useCallback(() => {
    api.tokens().then((d) => setTokens(d.tokens)).catch((e) => setError(e.message))
  }, [])
  useEffect(() => { load() }, [load])

  async function pair() {
    setBusy(true); setError(null)
    try {
      const d = await api.mintToken('ACT plugin')
      const qr = await QRCode.toDataURL(d.pair_payload,
        { margin: 1, width: 160, errorCorrectionLevel: 'M' })
      setMinted({ token: d.token, qr })
      load()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function revoke(id) {
    try { await api.revokeToken(id); if (minted) setMinted(null); load() }
    catch (e) { setError(e.message) }
  }

  const active = tokens?.filter((t) => !t.revoked_ts) ?? []

  return (
    <div className="pairing">
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <button onClick={pair} disabled={busy}>Create pairing code</button>
        <span className="muted">
          {active.length
            ? `${active.length} device${active.length === 1 ? '' : 's'} paired`
            : 'No device paired yet.'}
        </span>
      </div>

      {minted && (
        <div className="minted">
          <p><b>Paste this into the plugin now — it isn't shown again.</b></p>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <code className="tokenvalue">{minted.token}</code>
            <button onClick={() => {
              navigator.clipboard?.writeText(minted.token)
              setCopied(true); setTimeout(() => setCopied(false), 1500)
            }}>{copied ? 'Copied' : 'Copy'}</button>
            <img src={minted.qr} alt="Pairing QR code" width="160" height="160" />
          </div>
          <p className="fineprint">
            Covers every character you play — the plugin reads the name off the log.
            It can send logs and nothing else: it can't read your parses or change
            who sees them.
          </p>
        </div>
      )}

      {active.length > 0 && (
        <ul className="devicelist">
          {active.map((t) => (
            <li key={t.id}>
              <span>{t.label || 'device'}</span>
              <span className="muted">
                {t.last_seen_ts ? `last seen ${fmt.date(t.last_seen_ts)} ${fmt.time(t.last_seen_ts)}` : 'never used'}
              </span>
              <button className="chip danger" onClick={() => revoke(t.id)}>revoke</button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="err">{error}</p>}
    </div>
  )
}

export default function Import() {
  const [sessions, setSessions] = useState(null)
  const [chars, setChars] = useState([])
  const [error, setError] = useState(null)
  const [confirmDel, setConfirmDel] = useState(null)
  const [busy, setBusy] = useState(false)
  const [plugin, setPlugin] = useState(null)
  const seen = useRef(false)

  const refresh = useCallback(() => {
    api.sessions().then((d) => setSessions(d.sessions)).catch((e) => setError(e.message))
    api.characters().then((d) => setChars(d.characters)).catch(() => {})
  }, [])

  useEffect(() => { refresh() }, [refresh])
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
  const paired = chars.filter((c) => c.token_count > 0)
  const ready = sessions?.filter((s) => s.status === 'ready') ?? []
  const fightTotal = ready.reduce((n, s) => n + (s.encounter_count || 0), 0)
  const lineTotal = ready.reduce((n, s) => n + (s.line_count || 0), 0)
  if (sessions) seen.current = true

  return (
    <>
      <div className="pagehead">
        <h1>Import</h1>
        <span className="sub">Get your raids in. Two ways, both take a minute.</span>
        <span className="actions"><Link className="btnlink" to="/">Back to parses</Link></span>
      </div>
      {error && <p className="err">{error}</p>}

      <div className="methods">
        <Method
          title="ACT plugin"
          state={receiving.length ? 'live' : paired.length ? 'paired' : null}
          blurb="Sends your log while you raid, and uploads your old logs in bulk. Set it up once."
        >
          <ol className="steps">
            <li>
              <a className="btnlink" href="/api/plugin/download" download>
                Download EQ2Advanced.dll
              </a>
              {plugin?.available && (
                <span className="muted" style={{ marginLeft: 8 }}>
                  {fmt.bytes(plugin.size)} · built {fmt.date(plugin.built_ts)}
                </span>
              )}
            </li>
            <li>In ACT: <b>Plugins → Plugin Listing → Browse</b>, pick the file, <b>Add/Enable</b>.</li>
            <li>Make a pairing code below and paste it into the plugin's <b>eq2advanced</b> tab.</li>
            <li>
              Tick <b>Send my combat log as I play</b>. For old raids, use the plugin's{' '}
              <b>Import logs you already have</b> — point it at your whole logs folder.
            </li>
          </ol>

          <Pairing />

          {paired.length > 0 && (
            <div className="pluginshare">
              {paired.map((c) => (
                <div key={c.id} className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                  <b>{c.name}</b>
                  <AutoShare char={c} label="shares every raid with:" />
                </div>
              ))}
            </div>
          )}

          {receiving.length > 0 && (
            <p style={{ marginTop: 8 }}>
              <Link className="btnlink" to="/live">Watch the live raid →</Link>
            </p>
          )}
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
    </>
  )
}
