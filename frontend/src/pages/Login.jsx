import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'

/* Sign in / sign up / recover. There is no email in the system, so the security
   question picked at sign-up is the only self-service way back in — the form
   requires it rather than offering it, and the reset flow is two steps: name the
   question for a username, then answer it. */
export default function Login({ onAuthed }) {
  const [mode, setMode] = useState('login')   // login | register | reset
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [questions, setQuestions] = useState([])
  const [sqId, setSqId] = useState('')
  const [answer, setAnswer] = useState('')
  const [challenge, setChallenge] = useState(null)  // {username, question} once started
  const [error, setError] = useState(null)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (mode === 'register' && !questions.length) {
      api.securityQuestions().then((d) => {
        setQuestions(d.questions)
        setSqId(String(d.questions[0]?.id ?? ''))
      }).catch(() => {})
    }
  }, [mode, questions.length])

  function go(next) {
    setMode(next); setError(null); setMsg(null); setChallenge(null)
    setPassword(''); setAnswer('')
  }

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError(null); setMsg(null)
    try {
      if (mode === 'login') {
        onAuthed((await api.login(username, password)).user)
      } else if (mode === 'register') {
        onAuthed((await api.register(username, password, Number(sqId), answer)).user)
      } else if (!challenge) {
        setChallenge(await api.resetStart(username))
      } else {
        await api.resetComplete(challenge.username, answer, password)
        go('login')
        setMsg('Password reset — sign in with your new password.')
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const title = mode === 'login' ? 'Sign in'
    : mode === 'register' ? 'Create account' : 'Reset password'

  return (
    <div className="card authcard manage">
      <h1>{title}</h1>
      {mode !== 'login' && (
        <p className="note" style={{ marginTop: 4 }}>
          {mode === 'register' && 'Username and password — no email.'}
          {mode === 'reset' && !challenge && 'Enter your username.'}
          {mode === 'reset' && challenge && challenge.question}
        </p>
      )}
      <form onSubmit={submit}>
        {!(mode === 'reset' && challenge) && (
          <input
            type="text" placeholder="Username" value={username} autoComplete="username"
            autoCapitalize="none" spellCheck="false"
            onChange={(e) => setUsername(e.target.value)} required
          />
        )}
        {mode !== 'reset' && (
          <input
            type="password" placeholder={mode === 'login' ? 'Password' : 'Password (8+ characters)'}
            value={password} autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            onChange={(e) => setPassword(e.target.value)} required
          />
        )}
        {mode === 'register' && (
          <>
            <select value={sqId} onChange={(e) => setSqId(e.target.value)} required>
              {questions.map((q) => <option key={q.id} value={q.id}>{q.text}</option>)}
            </select>
            <input
              type="text" placeholder="Answer (you'll need this to reset)" value={answer}
              autoComplete="off" onChange={(e) => setAnswer(e.target.value)} required
            />
          </>
        )}
        {mode === 'reset' && challenge && (
          <>
            <input
              type="text" placeholder="Your answer" value={answer} autoComplete="off"
              onChange={(e) => setAnswer(e.target.value)} required
            />
            <input
              type="password" placeholder="New password (8+ characters)" value={password}
              autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} required
            />
          </>
        )}
        <button type="submit" disabled={busy}>
          {busy ? '…'
            : mode === 'login' ? 'Sign in'
            : mode === 'register' ? 'Create account'
            : challenge ? 'Set new password' : 'Continue'}
        </button>
      </form>
      {mode === 'register' && (
        <p className="note" style={{ marginTop: 8 }}>
          The answer is case-insensitive, and it is the only way to reset your
          password.
        </p>
      )}
      {msg && <p className="status-ready" style={{ marginTop: 8 }}>{msg}</p>}
      {error && <p className="err" style={{ marginTop: 8 }}>{error}</p>}
      <p className="muted switchmode">
        {mode === 'login' && <>New here? <a onClick={() => go('register')}>Create an account</a>
          {' · '}<a onClick={() => go('reset')}>Forgot password</a></>}
        {mode === 'register' && <>Already have an account? <a onClick={() => go('login')}>Sign in</a></>}
        {mode === 'reset' && <><a onClick={() => go('login')}>Back to sign in</a></>}
      </p>
    </div>
  )
}
