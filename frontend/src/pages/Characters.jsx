import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api.js'

export default function Characters() {
  const [chars, setChars] = useState(null)
  const [name, setName] = useState('')
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
      refresh()
    } catch (err) { setError(err.message) }
  }

  async function remove(id) {
    setError(null)
    try { await api.deleteCharacter(id); refresh() } catch (err) { setError(err.message) }
  }

  return (
    <div className="manage">
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
              {c.session_count === 0 && (
                <button className="danger" onClick={() => remove(c.id)}>Remove</button>
              )}
            </div>
          </div>
          {/* auto-share lives on the Sharing page — one place, not three */}
          <p className="fineprint">
            Automatic sharing for this character is set on the{' '}
            <Link to="/groups">Sharing</Link> page.
          </p>
        </div>
      ))}
    </div>
  )
}
