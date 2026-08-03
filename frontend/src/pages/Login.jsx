import { useState } from 'react'
import { api } from '../lib/api.js'

export default function Login({ onAuthed }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const d = mode === 'login'
        ? await api.login(email, password)
        : await api.register(email, password)
      onAuthed(d.user)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card authcard">
      <h1>{mode === 'login' ? 'Sign in' : 'Create account'}</h1>
      <p className="note" style={{ marginTop: 4 }}>
        {mode === 'login'
          ? 'Your sessions and coaching are private to your account.'
          : 'Free and open — pair your characters and start uploading logs.'}
      </p>
      <form onSubmit={submit}>
        <input
          type="email" placeholder="Email" value={email} autoComplete="email"
          onChange={(e) => setEmail(e.target.value)} required
        />
        <input
          type="password" placeholder={mode === 'login' ? 'Password' : 'Password (8+ characters)'}
          value={password} autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          onChange={(e) => setPassword(e.target.value)} required
        />
        <button type="submit" disabled={busy}>
          {busy ? '…' : mode === 'login' ? 'Sign in' : 'Create account'}
        </button>
      </form>
      {error && <p className="err" style={{ marginTop: 8 }}>{error}</p>}
      <p className="muted switchmode">
        {mode === 'login' ? 'New here? ' : 'Already have an account? '}
        <a onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null) }}>
          {mode === 'login' ? 'Create an account' : 'Sign in'}
        </a>
      </p>
    </div>
  )
}
