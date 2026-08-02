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
      <h1>Account</h1>
      <div className="card">
        <p>
          Signed in as <b>{user.email}</b>
          {user.role === 'admin' && <span className="badge named" style={{ marginLeft: 8 }}>admin</span>}
        </p>
        <button onClick={signOut}>Sign out</button>
      </div>
      <div className="card">
        <h2>Change password</h2>
        <form onSubmit={changePw} className="authcard">
          <input
            type="password" placeholder="Current password" value={current}
            autoComplete="current-password" onChange={(e) => setCurrent(e.target.value)} required
          />
          <input
            type="password" placeholder="New password (8+ characters)" value={next}
            autoComplete="new-password" onChange={(e) => setNext(e.target.value)} required
          />
          <button type="submit">Change password</button>
        </form>
        {msg && <p className="status-ready">{msg}</p>}
        {error && <p className="err">{error}</p>}
      </div>
    </>
  )
}
