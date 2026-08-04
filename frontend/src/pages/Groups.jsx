import { useCallback, useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

/* Groups are who you raid with, and therefore who can see your raids. Two ways
   in on purpose: a 6-digit code to read out in voice chat, and an invite
   addressed to a username for when you'd rather not.

   The link is built from the server's public address (`invite_base`), not from
   this browser's origin: the person pasting it is often on the LAN, and
   `http://10.1.1.15:8450/join/…` is a dead link to everyone else. Origin is
   only the fallback for a server that didn't say. */
const inviteUrl = (base, code) => `${base || window.location.origin}/join/${code}`

export default function Groups() {
  const location = useLocation()
  const [data, setData] = useState(null)
  // fetched once, the first time a name is typed — not per keystroke
  const [pendingCode, setPendingCode] = useState(null)
  const [open, setOpen] = useState(null)        // group id whose panel is showing
  const [detail, setDetail] = useState(null)
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [invitee, setInvitee] = useState('')
  const [error, setError] = useState(null)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => {
    api.groups().then(setData).catch((e) => setError(e.message))
  }, [])
  useEffect(() => { refresh() }, [refresh])

  // an invite link lands here after joining and says which group it was
  useEffect(() => {
    if (location.state?.joined) setMsg(`You've joined ${location.state.joined}.`)
  }, [location.state])

  // the moment there's a name to attach it to, get the code the group will have
  useEffect(() => {
    if (name.trim() && !pendingCode) {
      api.newJoinCode().then((d) => setPendingCode(d.code)).catch(() => {})
    }
  }, [name, pendingCode])

  const openGroup = useCallback((id) => {
    setOpen(id); setDetail(null); setInvitee('')
    api.group(id).then((d) => setDetail(d.group)).catch((e) => setError(e.message))
  }, [])

  async function run(fn, note) {
    setBusy(true); setError(null); setMsg(null)
    try {
      await fn()
      if (note) setMsg(note)
      refresh()
      if (open) openGroup(open)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <>
      <div className="pagehead">
        <h1>Sharing</h1>
        <span className="sub">Groups you share raid parses with</span>
      </div>

      {error && <p className="err">{error}</p>}
      {msg && <p className="note flash">{msg}</p>}

      {data?.invites?.length > 0 && (
        <div className="card">
          <h2>Invitations</h2>
          {data.invites.map((i) => (
            <div key={i.id} className="row" style={{ gap: 8, marginTop: 8 }}>
              <strong>{i.group_name}</strong>
              <span className="muted">from {i.invited_by_username}</span>
              <button className="chip" disabled={busy}
                      onClick={() => run(() => api.answerInvite(i.id, 'accept'), 'Joined.')}>
                Accept
              </button>
              <button className="chip" disabled={busy}
                      onClick={() => run(() => api.answerInvite(i.id, 'decline'))}>
                Decline
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <h2>Your groups</h2>
        {data === null && <p className="muted">Loading…</p>}
        {data?.groups?.length === 0 && (
          <p className="muted">None yet. Make one below, or join with a code.</p>
        )}
        {data?.groups?.map((g) => (
          <div key={g.id} className="row" style={{ gap: 8, marginTop: 8 }}>
            <button className="chip" onClick={() => (open === g.id ? setOpen(null) : openGroup(g.id))}>
              {open === g.id ? '▾' : '▸'} {g.name}
            </button>
            <span className="muted">
              {g.member_count} member{g.member_count === 1 ? '' : 's'} · you are {g.my_role}
            </span>
          </div>
        ))}
      </div>

      {open && detail && (
        <div className="card">
          <div className="pagehead" style={{ marginBottom: 8 }}>
            <h2>{detail.name}</h2>
            <span className="sub">{detail.description || ''}</span>
          </div>

          {detail.join_code !== undefined && (
            <p className="note">
              Join code: <strong style={{ fontSize: '1.3em', letterSpacing: '2px' }}>
                {detail.join_code || 'off'}
              </strong>
              <button className="chip" disabled={busy}
                      onClick={() => run(() => api.rotateJoinCode(detail.id, { enabled: true }),
                                         'New code set — the old one no longer works.')}>
                New code
              </button>
              {detail.join_code && (
                <button className="chip" disabled={busy}
                        onClick={() => run(() => api.rotateJoinCode(detail.id, { enabled: false }),
                                           'Code joining is off.')}>
                  Turn off
                </button>
              )}
              {/* The link carries the same code, so there is one thing to
                  rotate — and it works for someone with no account yet, who
                  gets sign-up and joins the moment they finish. */}
              {detail.join_code && (
                <>
                  <br />
                  <span className="muted">Invite link: </span>
                  <code className="tokenvalue">{inviteUrl(data?.invite_base, detail.join_code)}</code>
                  <button className="chip" disabled={busy}
                          onClick={() => {
                            navigator.clipboard?.writeText(inviteUrl(data?.invite_base, detail.join_code))
                            setMsg('Invite link copied.')
                          }}>
                    Copy link
                  </button>
                </>
              )}
              <br />
              <span className="muted">Anyone with the code or link can join.</span>
            </p>
          )}

          <h3>Members</h3>
          {detail.members.map((m) => (
            <div key={m.user_id} className="row" style={{ gap: 8, marginTop: 4 }}>
              <strong>{m.username}</strong>
              <span className="muted">{m.role} · joined {fmt.date(m.joined_ts)}</span>
              {detail.my_role === 'owner' && m.user_id !== detail.owner_user_id && (
                <button className="chip" disabled={busy}
                        onClick={() => run(() => api.setMemberRole(
                          detail.id, m.user_id, m.role === 'admin' ? 'member' : 'admin'))}>
                  {m.role === 'admin' ? 'Make member' : 'Make admin'}
                </button>
              )}
              {/* the owner can remove anyone but themselves; a group admin can
                  remove plain members (the API refuses admin-on-admin) */}
              {m.user_id !== detail.owner_user_id
                && (detail.my_role === 'owner'
                    || (detail.my_role === 'admin' && m.role !== 'admin')) && (
                <button className="chip danger" disabled={busy}
                        onClick={() => run(() => api.removeMember(detail.id, m.user_id),
                                           `${m.username} removed.`)}>
                  Remove
                </button>
              )}
            </div>
          ))}

          {detail.pending_invites?.length > 0 && (
            <>
              <h3>Invited</h3>
              {detail.pending_invites.map((i) => (
                <div key={i.id} className="muted">{i.username} — waiting</div>
              ))}
            </>
          )}

          {(detail.my_role === 'owner' || detail.my_role === 'admin') && (
            <div className="row" style={{ gap: 8, marginTop: 12 }}>
              <input type="text" placeholder="Invite by username" value={invitee}
                     autoCapitalize="none" onChange={(e) => setInvitee(e.target.value)} />
              <button disabled={busy || !invitee.trim()}
                      onClick={() => run(async () => {
                        await api.inviteToGroup(detail.id, invitee.trim())
                        setInvitee('')
                      }, 'Invitation sent.')}>
                Invite
              </button>
            </div>
          )}

          <div className="row" style={{ gap: 8, marginTop: 12 }}>
            {detail.my_role === 'owner' ? (
              <button className="chip danger" disabled={busy}
                      onClick={() => run(async () => {
                        await api.deleteGroup(detail.id); setOpen(null)
                      }, 'Group deleted.')}>
                Delete group
              </button>
            ) : (
              <button className="chip danger" disabled={busy}
                      onClick={() => run(async () => {
                        await api.leaveGroup(detail.id); setOpen(null)
                      }, 'Left the group.')}>
                Leave group
              </button>
            )}
          </div>
        </div>
      )}

      <div className="card" style={{ maxWidth: 480 }}>
        <h2>Start or join</h2>
        <div className="row" style={{ gap: 8, marginTop: 8 }}>
          <input type="text" placeholder="New group name" value={name}
                 onChange={(e) => setName(e.target.value)} />
          <button disabled={busy || !name.trim()}
                  onClick={() => run(async () => {
                    const d = await api.createGroup(name.trim(), null, pendingCode)
                    setName(''); setPendingCode(null); openGroup(d.group.id)
                  })}>
            Create
          </button>
        </div>
        {/* The code and link appear while the name is still being typed, so
            they can be pasted into chat in the same breath as "I made us a
            group". Nothing is reserved — the code is claimed on Create, and if
            it were taken in between the group comes back with a different one,
            which is why the panel below re-reads it. */}
        {name.trim() && pendingCode && (
          <p className="note" style={{ marginTop: 8 }}>
            Join code: <strong style={{ fontSize: '1.2em', letterSpacing: '2px' }}>
              {pendingCode}
            </strong>
            <br />
            <span className="muted">Invite link: </span>
            <code className="tokenvalue">{inviteUrl(data?.invite_base, pendingCode)}</code>
            <button className="chip" type="button"
                    onClick={() => {
                      navigator.clipboard?.writeText(inviteUrl(data?.invite_base, pendingCode))
                      setMsg('Invite link copied — active once you hit Create.')
                    }}>
              Copy link
            </button>
          </p>
        )}
        <div className="row" style={{ gap: 8, marginTop: 8 }}>
          <input type="text" placeholder="6-digit join code" value={code} inputMode="numeric"
                 maxLength={6} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
          <button disabled={busy || code.length !== 6}
                  onClick={() => run(async () => {
                    const d = await api.joinGroup(code)
                    setCode(''); openGroup(d.group.id)
                  }, 'Joined.')}>
            Join
          </button>
        </div>
      </div>
    </>
  )
}
