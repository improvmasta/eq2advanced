import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import QRCode from 'qrcode'
import { api, fmt } from '../lib/api.js'

function TokenPanel({ char }) {
  const [tokens, setTokens] = useState(null)
  const [label, setLabel] = useState('')
  const [minted, setMinted] = useState(null) // {id, token, pair_payload, qr}
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.tokens(char.id).then((d) => setTokens(d.tokens)).catch((e) => setError(e.message))
  }, [char.id])

  useEffect(() => { refresh() }, [refresh])

  async function mint() {
    setError(null)
    try {
      const d = await api.mintToken(char.id, label.trim() || null)
      const qr = await QRCode.toDataURL(d.pair_payload, { margin: 1, width: 200, errorCorrectionLevel: 'M' })
      setMinted({ ...d, qr })
      setLabel('')
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
        A token pairs one uploader (the ACT plugin, when it lands) to {char.name}. Mint one
        per device — it is shown once, and you can revoke it any time.
      </p>
      <div className="mintrow">
        <input
          type="text" placeholder="Label (e.g. raid PC)" value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <button onClick={mint}>Mint token</button>
      </div>
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
            <thead><tr><th>Label</th><th>Created</th><th>Last seen</th><th /></tr></thead>
            <tbody>
              {active.map((t) => (
                <tr key={t.id}>
                  <td className="name">{t.label || '—'}</td>
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
        <span className="sub">Uploads and live ingest attach to a character</span>
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
          {open === c.id && <TokenPanel char={c} />}
        </div>
      ))}
    </>
  )
}
