import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api.js'

/* One box for "this is broken" and "this should exist".

   The page comes along uncaptured — "the numbers look wrong" is unanswerable
   without knowing which raid, and asking afterwards costs a round trip most
   people don't come back for. Captured when the dialog OPENS, not when it
   sends, so a link inside the box can't rewrite where they were. The box is a
   non-modal header dropdown: Cancel, Escape, the trigger, and an outside click
   all dismiss it without moving the page underneath. */
export default function FeedbackDialog({ page, triggerRef, onClose }) {
  const [kind, setKind] = useState('bug')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [sent, setSent] = useState(false)
  const box = useRef(null)

  useEffect(() => {
    const away = (event) => {
      if (box.current?.contains(event.target)
          || triggerRef?.current?.contains(event.target)) return
      onClose()
    }
    const escape = (event) => {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      onClose()
      triggerRef?.current?.focus()
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [onClose, triggerRef])

  async function send() {
    setBusy(true); setError(null)
    try {
      await api.sendFeedback(kind, body.trim(), page)
      setSent(true)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="card confirmcard feedbackcard" ref={box} role="dialog"
         aria-labelledby="feedback-title">
      <div className="feedbackcardhead">
        <h2 id="feedback-title">Send feedback</h2>
        <button type="button" className="iconbtn" onClick={onClose}
                aria-label="Close feedback">×</button>
      </div>
      {sent ? (
        <>
          <p>Thanks — that landed on the admin page.</p>
          <button type="button" onClick={onClose}>Close</button>
        </>
      ) : (
        <>
          <div className="row">
            {[['bug', 'Something is broken'], ['suggestion', 'Something should exist']]
              .map(([k, label]) => (
                <button key={k} className={`chip ${kind === k ? 'on' : ''}`}
                        type="button"
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
            <button type="button" disabled={busy || !body.trim()} onClick={send}>Send</button>
            <button type="button" className="chip" onClick={onClose}>Cancel</button>
          </div>
        </>
      )}
    </div>
  )
}
