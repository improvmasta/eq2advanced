import { useState } from 'react'
import { api } from '../lib/api.js'

/* One box for "this is broken" and "this should exist".

   The page comes along uncaptured — "the numbers look wrong" is unanswerable
   without knowing which raid, and asking afterwards costs a round trip most
   people don't come back for. Captured when the dialog OPENS, not when it
   sends, so a link inside the box can't rewrite where they were. */
export default function FeedbackDialog({ page, onClose }) {
  const [kind, setKind] = useState('bug')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [sent, setSent] = useState(false)

  async function send() {
    setBusy(true); setError(null)
    try {
      await api.sendFeedback(kind, body.trim(), page)
      setSent(true)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="card confirmcard feedbackcard">
      <h2>Send feedback</h2>
      {sent ? (
        <>
          <p>Thanks — that landed on the admin page.</p>
          <button onClick={onClose}>Close</button>
        </>
      ) : (
        <>
          <div className="row">
            {[['bug', 'Something is broken'], ['suggestion', 'Something should exist']]
              .map(([k, label]) => (
                <button key={k} className={`chip ${kind === k ? 'on' : ''}`}
                        onClick={() => setKind(k)}>{label}</button>
              ))}
          </div>
          <textarea value={body} maxLength={4000} autoFocus
                    placeholder={kind === 'bug'
                      ? 'What did you expect, and what happened instead?'
                      : 'What would you want it to do?'}
                    onChange={(e) => setBody(e.target.value)} />
          <p className="muted">Sent with the page you were on: {page}</p>
          {error && <p className="err">{error}</p>}
          <div className="row">
            <button disabled={busy || !body.trim()} onClick={send}>Send</button>
            <button className="chip" onClick={onClose}>Cancel</button>
          </div>
        </>
      )}
    </div>
  )
}
