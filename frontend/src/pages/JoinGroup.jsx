import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import Login from './Login.jsx'
import { api } from '../lib/api.js'

/* An invite link — /join/<code> — for the people who won't type a 6-digit code
   into a form. The link carries the same code the group hands out in voice, so
   there is one credential to rotate, not two.

   Signed out is the normal case for a link like this, so the page names the
   group first and then puts sign-up right there; joining happens by itself the
   moment the account exists. The alternative — bouncing to a login page and
   losing the invitation — is how invite links usually go wrong. */
export default function JoinGroup({ user, onAuthed }) {
  const { code } = useParams()
  const navigate = useNavigate()
  const [group, setGroup] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.previewInvite(code).then((d) => setGroup(d.group)).catch((e) => setError(e.message))
  }, [code])

  const join = useCallback(async () => {
    setBusy(true); setError(null)
    try {
      const d = await api.joinGroup(code)
      navigate('/groups', { replace: true, state: { joined: d.group.name } })
    } catch (e) { setError(e.message); setBusy(false) }
  }, [code, navigate])

  // a brand-new account lands here already invited: join without a second click
  useEffect(() => {
    if (user && group && !group.member) join()
  }, [user, group, join])

  if (error) {
    return (
      <div className="card authcard">
        <h1>Invitation</h1>
        <p className="err">{error}</p>
        <p className="muted">
          Ask whoever sent it for a fresh link.{' '}
          <Link to="/">Back to parses</Link>
        </p>
      </div>
    )
  }
  if (!group) return <p className="muted">Loading…</p>

  if (group.member) {
    return (
      <div className="card authcard">
        <h1>{group.name}</h1>
        <p>You're already in this group.</p>
        <Link className="btnlink" to="/groups">Go to Sharing</Link>
      </div>
    )
  }

  if (user) {
    return (
      <div className="card authcard">
        <h1>{group.name}</h1>
        <p className="muted">{busy ? 'Joining…' : 'Joining you now…'}</p>
      </div>
    )
  }

  return (
    <>
      <div className="card authcard" style={{ marginBottom: 0 }}>
        <h1>You're invited to {group.name}</h1>
        <p className="note" style={{ marginTop: 4 }}>
          {group.description || `${group.member_count} member${group.member_count === 1 ? '' : 's'}`}
          {' — '}sign in or make an account below to join.
        </p>
      </div>
      <Login onAuthed={onAuthed} />
    </>
  )
}
