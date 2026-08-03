import { useState } from 'react'
import { api } from '../lib/api.js'

export default function Account({ user, onSignedOut }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [msg, setMsg] = useState(null)
  const [error, setError] = useState(null)

  async function changePw(e) {
    e.preventDefault()
    setMsg(null); setError(null)
    try {
      await api.changePassword(current, next)
      setCurrent(''); setNext('')
      setMsg('Password changed.')
    } catch (err) { setError(err.message) }
  }

  async function signOut() {
    await api.logout().catch(() => {})
    onSignedOut()
  }

  return (
    <>
      <div className="pagehead">
        <h1>Account</h1>
        <span className="sub">
          Signed in as {user.email}
          {user.role === 'admin' && <span className="badge named">admin</span>}
        </span>
        <div className="actions">
          <button onClick={signOut}>Sign out</button>
        </div>
      </div>

      <div className="card" style={{ maxWidth: 380 }}>
        <h2>Change password</h2>
        <form onSubmit={changePw} style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          <input
            type="password" placeholder="Current password" value={current} style={{ width: '100%' }}
            autoComplete="current-password" onChange={(e) => setCurrent(e.target.value)} required
          />
          <input
            type="password" placeholder="New password (8+ characters)" value={next} style={{ width: '100%' }}
            autoComplete="new-password" onChange={(e) => setNext(e.target.value)} required
          />
          <button type="submit">Change password</button>
        </form>
        {msg && <p className="status-ready" style={{ marginTop: 8 }}>{msg}</p>}
        {error && <p className="err" style={{ marginTop: 8 }}>{error}</p>}
      </div>
    </>
  )
}
