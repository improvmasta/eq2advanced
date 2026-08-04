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
    <div className="manage">
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
        <div className="card" style={{ maxWidth: 720, borderColor: 'var(--warning)' }}>
          <strong>Set a security question.</strong>{' '}
          <span className="muted">
            Without one, only an admin can reset a forgotten password.
          </span>
        </div>
      )}

      <div className="panelgrid" style={{ maxWidth: 720, marginTop: 12 }}>
        <div className="card">
          <h2>Change password</h2>
          <form onSubmit={changePw} className="formcol">
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
          {msg && <p className="status-ready" style={{ marginTop: 8 }}>{msg}</p>}
          {error && <p className="err" style={{ marginTop: 8 }}>{error}</p>}
        </div>

        <div className="card">
          <h2>{user.needs_security_question ? 'Set security question' : 'Change security question'}</h2>
          <p className="note" style={{ marginTop: 4 }}>
            The only self-service way back into a locked account. Answers ignore
            capitals and extra spaces.
          </p>
          <form onSubmit={saveQuestion} className="formcol">
            <select value={sqId} onChange={(e) => setSqId(e.target.value)} required>
              {questions.map((q) => <option key={q.id} value={q.id}>{q.text}</option>)}
            </select>
            <input
              type="text" placeholder="Answer" value={answer}
              autoComplete="off" onChange={(e) => setAnswer(e.target.value)} required
            />
            <input
              type="password" placeholder="Confirm with your password" value={sqPassword}
              autoComplete="current-password"
              onChange={(e) => setSqPassword(e.target.value)} required
            />
            <button type="submit">Save question</button>
          </form>
          {sqMsg && <p className="status-ready" style={{ marginTop: 8 }}>{sqMsg}</p>}
          {sqError && <p className="err" style={{ marginTop: 8 }}>{sqError}</p>}
        </div>
      </div>
    </div>
  )
}
