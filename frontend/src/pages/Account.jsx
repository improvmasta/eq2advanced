import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'

export default function Account({ user, onSignedOut, onUserChange }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [msg, setMsg] = useState(null)
  const [error, setError] = useState(null)

  const [questions, setQuestions] = useState([])
  const [sqId, setSqId] = useState('')
  const [sqPassword, setSqPassword] = useState('')
  const [answer, setAnswer] = useState('')
  const [sqMsg, setSqMsg] = useState(null)
  const [sqError, setSqError] = useState(null)

  useEffect(() => {
    api.securityQuestions().then((d) => {
      setQuestions(d.questions)
      setSqId(String(d.questions[0]?.id ?? ''))
    }).catch(() => {})
  }, [])

  async function changePw(e) {
    e.preventDefault()
    setMsg(null); setError(null)
    try {
      await api.changePassword(current, next)
      setCurrent(''); setNext('')
      setMsg('Password changed.')
    } catch (err) { setError(err.message) }
  }

  async function saveQuestion(e) {
    e.preventDefault()
    setSqMsg(null); setSqError(null)
    try {
      await api.setSecurityQuestion(sqPassword, Number(sqId), answer)
      setSqPassword(''); setAnswer('')
      setSqMsg('Security question saved.')
      onUserChange?.({ ...user, needs_security_question: false })
    } catch (err) { setSqError(err.message) }
  }

  return (
    <>
      <div className="pagehead">
        <h1>Account</h1>
        <span className="sub">
          Signed in as {user.username}
          {user.role === 'admin' && <span className="badge named">admin</span>}
        </span>
        <div className="actions">
          <button onClick={async () => { await api.logout().catch(() => {}); onSignedOut() }}>
            Sign out
          </button>
        </div>
      </div>

      {user.needs_security_question && (
        <div className="card" style={{ maxWidth: 520, borderColor: 'var(--warn, #b58900)' }}>
          <strong>Set a security question.</strong>{' '}
          <span className="muted">
            Without one, only an admin can reset a forgotten password.
          </span>
        </div>
      )}

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

      <div className="card" style={{ maxWidth: 380 }}>
        <h2>{user.needs_security_question ? 'Set security question' : 'Change security question'}</h2>
        <p className="note" style={{ marginTop: 4 }}>
          Answers ignore capitals and extra spaces.
        </p>
        <form onSubmit={saveQuestion} style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          <select value={sqId} onChange={(e) => setSqId(e.target.value)} style={{ width: '100%' }} required>
            {questions.map((q) => <option key={q.id} value={q.id}>{q.text}</option>)}
          </select>
          <input
            type="text" placeholder="Answer" value={answer} style={{ width: '100%' }}
            autoComplete="off" onChange={(e) => setAnswer(e.target.value)} required
          />
          <input
            type="password" placeholder="Confirm with your password" value={sqPassword}
            style={{ width: '100%' }} autoComplete="current-password"
            onChange={(e) => setSqPassword(e.target.value)} required
          />
          <button type="submit">Save question</button>
        </form>
        {sqMsg && <p className="status-ready" style={{ marginTop: 8 }}>{sqMsg}</p>}
        {sqError && <p className="err" style={{ marginTop: 8 }}>{sqError}</p>}
      </div>
    </>
  )
}
