import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import QRCode from 'qrcode'
import { api, fmt } from '../lib/api.js'

function TokenPanel({ char }) {
  const [tokens, setTokens] = useState(null)
  const [label, setLabel] = useState('')
  const [canShare, setCanShare] = useState(false)
  const [minted, setMinted] = useState(null) // {id, token, pair_payload, qr}
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.tokens(char.id).then((d) => setTokens(d.tokens)).catch((e) => setError(e.message))
  }, [char.id])

  useEffect(() => { refresh() }, [refresh])

  async function mint() {
    setError(null)
    try {
      const d = await api.mintToken(char.id, label.trim() || null, canShare)
      const qr = await QRCode.toDataURL(d.pair_payload, { margin: 1, width: 200, errorCorrectionLevel: 'M' })
      setMinted({ ...d, qr })
      setLabel('')
      setCanShare(false)
      refresh()
    } catch (e) { setError(e.message) }
  }

  async function revoke(id) {
    setError(null)
    try {
      await api.revokeToken(id)
      if (minted?.id === id) setMinted(null)
      refresh()
    } catch (e) { setError(e.message) }
  }

  const active = tokens?.filter((t) => !t.revoked_ts) ?? []

  return (
    <div className="tokenpanel">
      <h2>Device tokens</h2>
      <p className="note">
        A token pairs one uploader to {char.name}. Mint one per device — it is
        shown once and can be revoked any time.
      </p>
      <div className="mintrow">
        <input
          type="text" placeholder="Label (e.g. raid PC)" value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <button onClick={mint}>Mint token</button>
      </div>
      {/* Scope is fixed at mint: there is no route that raises it later, because
          the token lives in a config file on a gaming PC and this answer has to
          come from someone signed in here. Widening means a new token. */}
      <label className="checkrow">
        <input type="checkbox" checked={canShare}
               onChange={(e) => setCanShare(e.target.checked)} />
        <span>
          Let this device choose who sees the raids it sends
          <span className="note">
            {' '}— the ACT plugin can then tick groups for tonight's raid, and change
            {' '}{char.name}'s standing auto-share. Leave off and it can only send logs.
          </span>
        </span>
      </label>
      {minted && (
        <div className="minted">
          <p><b>Copy this now — it won't be shown again.</b></p>
          <code className="tokenvalue">{minted.token}</code>
          <button onClick={() => navigator.clipboard?.writeText(minted.token)}>Copy token</button>
          <div className="qr">
            <img src={minted.qr} alt="Pairing QR code" width="200" height="200" />
            <p className="note" style={{ marginBottom: 0 }}>
              Scan from the uploader's pairing screen, or paste the token.
            </p>
          </div>
        </div>
      )}
      {active.length > 0 && (
        <div className="tablewrap">
          <table className="data">
            <thead><tr><th>Label</th><th>Can do</th><th>Created</th><th>Last seen</th><th /></tr></thead>
            <tbody>
              {active.map((t) => (
                <tr key={t.id}>
                  <td className="name">{t.label || '—'}</td>
                  <td>{t.can_share ? 'Send logs + set sharing' : 'Send logs'}</td>
                  <td>{fmt.date(t.created_ts)}</td>
                  <td>{t.last_seen_ts ? `${fmt.date(t.last_seen_ts)} ${fmt.time(t.last_seen_ts)}` : 'never'}</td>
                  <td><button className="danger" onClick={() => revoke(t.id)}>Revoke</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {tokens !== null && active.length === 0 && !minted && (
        <p className="muted">No active tokens.</p>
      )}
      {error && <p className="err">{error}</p>}
    </div>
  )
}

/* Auto-share: a standing instruction that every raid this character records
   goes to these groups, back catalogue included. It is evaluated when a raid is
   read, not copied onto it, so unticking a group closes the old nights too — and
   a single raid can still be pulled back out from its own Share control. */
function AutoShare({ char }) {
  const [groups, setGroups] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.characterShares(char.id).then((d) => setGroups(d.groups)).catch((e) => setError(e.message))
  }, [char.id])

  async function toggle(gid) {
    const next = groups.map((g) => (g.group_id === gid ? { ...g, shared: !g.shared } : g))
    setGroups(next); setBusy(true); setError(null)
    try {
      const d = await api.setCharacterShares(
        char.id, next.filter((g) => g.shared).map((g) => g.group_id))
      setGroups(d.groups)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (groups === null) return null
  if (!groups.length) {
    return <span className="muted">No groups yet — <Link to="/groups">make one</Link>.</span>
  }
  return (
    <span className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
      <span className="muted">Auto-share every raid with:</span>
      {groups.map((g) => (
        <button key={g.group_id} className={`chip ${g.shared ? 'on' : ''}`} disabled={busy}
                onClick={() => toggle(g.group_id)}>
          {g.name}
        </button>
      ))}
      {error && <span className="err">{error}</span>}
    </span>
  )
}

export default function Characters() {
  const [chars, setChars] = useState(null)
  const [name, setName] = useState('')
  const [open, setOpen] = useState(null) // character id with token panel open
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.characters().then((d) => setChars(d.characters)).catch((e) => setError(e.message))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  async function add(e) {
    e.preventDefault()
    setError(null)
    try {
      const d = await api.addCharacter(name)
      setName('')
      setOpen(d.id)
      refresh()
    } catch (err) { setError(err.message) }
  }

  async function remove(id) {
    setError(null)
    try { await api.deleteCharacter(id); refresh() } catch (err) { setError(err.message) }
  }

  return (
    <>
      <div className="pagehead">
        <h1>Characters</h1>
        <span className="sub">Uploads and live ingest attach to a character.</span>
        <div className="actions">
          <form onSubmit={add} style={{ display: 'flex', gap: 8 }}>
            <input
              type="text" placeholder="Character first name (e.g. Bobby)" value={name}
              onChange={(e) => setName(e.target.value)} required
            />
            <button type="submit">Add character</button>
          </form>
        </div>
      </div>
      {error && <p className="err">{error}</p>}

      {chars === null && <p className="muted">Loading…</p>}
      {chars?.length === 0 && (
        <p className="muted">No characters yet — add the name you log with above.</p>
      )}

      {chars?.map((c) => (
        <div className="card" key={c.id}>
          <div className="charhead">
            <span className="cardtitle"><Link to={`/characters/${c.id}`}>{c.name}</Link></span>
            <span className="sub">
              {c.class ? `${c.class} ${c.level ?? ''} · ` : ''}
              {c.session_count} session{c.session_count === 1 ? '' : 's'} ·{' '}
              {c.token_count} active token{c.token_count === 1 ? '' : 's'}
            </span>
            <div className="charactions">
              <button onClick={() => setOpen(open === c.id ? null : c.id)}>
                {open === c.id ? 'Hide tokens' : 'Pair a device'}
              </button>
              {c.session_count === 0 && (
                <button className="danger" onClick={() => remove(c.id)}>Remove</button>
              )}
            </div>
          </div>
          <div style={{ marginTop: 8 }}><AutoShare char={c} /></div>
          {open === c.id && <TokenPanel char={c} />}
        </div>
      ))}
    </>
  )
}
