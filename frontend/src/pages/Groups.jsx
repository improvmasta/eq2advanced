import { useCallback, useEffect, useState } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import AutoShare from '../components/AutoShare.jsx'
import { api, fmt } from '../lib/api.js'

/* The Sharing page: who sees your raids, in two sections.

   Automatic sharing first, because "every raid I record goes to my guild" is
   the decision most people came here to make. Then the groups themselves as a
   master–detail: the list on the left, the open group's invite panel and
   member table on the right. Nothing here is a pill — a group is a named row.

   Two ways into a group on purpose: a 6-digit code to read out in voice chat,
   and an invite addressed to a username for when you'd rather not. The link is
   built from the server's public address (`invite_base`), not this browser's
   origin: the person pasting it is often on the LAN, and a 10.x link is dead
   to everyone else. Origin is only the fallback for a server that didn't say. */
const inviteUrl = (base, code) => `${base || window.location.origin}/join/${code}`

export default function Groups() {
  const location = useLocation()
  // ?g=<id> — "Manage group" on the raid list lands on THAT group, not the
  // first one alphabetically
  const [params] = useSearchParams()
  const wanted = Number(params.get('g')) || null
  const [data, setData] = useState(null)
  const [chars, setChars] = useState(null)
  // fetched once, the first time a name is typed — not per keystroke
  const [pendingCode, setPendingCode] = useState(null)
  const [open, setOpen] = useState(null)        // group id showing in the detail pane
  const [detail, setDetail] = useState(null)
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [invitee, setInvitee] = useState('')
  // deleting a group asks for its name back, typed exactly (case included)
  const [confirming, setConfirming] = useState(false)
  const [confirmName, setConfirmName] = useState('')
  const [error, setError] = useState(null)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => {
    api.characters().then((d) => setChars(d.characters)).catch(() => setChars([]))
    // awaitable, so run() can hold `busy` until the new list is actually here
    return api.groups().then(setData).catch((e) => setError(e.message))
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
    setConfirming(false); setConfirmName('')   // never carries to the next group
    api.group(id).then((d) => setDetail(d.group)).catch((e) => setError(e.message))
  }, [])

  /* A master–detail with an empty detail looks broken; open the first group.
     Not while busy — mid-delete this would fire against the STALE list and
     re-open the group that was just deleted (a "no such group" error). */
  useEffect(() => {
    if (busy || open !== null || !data?.groups?.length) return
    const asked = data.groups.some((g) => g.id === wanted) ? wanted : null
    openGroup(asked || data.groups[0].id)
  }, [busy, data, open, openGroup, wanted])

  /* reopen=false for the actions that change WHICH group is open (create,
     join, delete, leave) — those set it themselves, and re-opening the stale
     `open` from this closure would clobber it or 404 on a deleted group. */
  async function run(fn, note, reopen = true) {
    setBusy(true); setError(null); setMsg(null)
    try {
      await fn()
      if (note) setMsg(note)
      await refresh()
      if (reopen && open) openGroup(open)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const manage = detail && (detail.my_role === 'owner' || detail.my_role === 'admin')
  // changes exactly when the set of groups does, so the auto-share rows refetch
  const groupsKey = data?.groups?.map((g) => g.id).join(',') ?? ''

  return (
    <div className="manage">
      <div className="pagehead">
        <h1>Sharing</h1>
        <span className="sub">Who sees your raids — set it once per character, or per raid from its Share control</span>
      </div>

      {error && <p className="err">{error}</p>}
      {msg && <p className="note flash">{msg}</p>}

      {data?.invites?.length > 0 && (
        <div className="card">
          <h2>Invitations</h2>
          {data.invites.map((i) => (
            <div key={i.id} className="row" style={{ gap: 10, marginTop: 8 }}>
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
        <h2>Automatic sharing</h2>
        <p className="note">
          Raids these characters record go to the switched-on groups.
        </p>
        {chars === null && <p className="muted">Loading…</p>}
        {chars?.length === 0 && (
          <p className="muted">No characters yet — they appear when you import a log.</p>
        )}
        {chars?.map((c) => (
          <div key={c.id} className="autochar">
            <div className="who">
              <span className="cardtitle">{c.name}</span>
              {c.class && <div className="muted">{c.class} {c.level ?? ''}</div>}
            </div>
            <AutoShare char={c} refreshKey={groupsKey} />
          </div>
        ))}
      </div>

      <div className="card">
        <h2>Groups</h2>

        <div className="toolrow">
          <input type="text" placeholder="New group name" value={name}
                 onChange={(e) => setName(e.target.value)} />
          <button disabled={busy || !name.trim()}
                  onClick={() => run(async () => {
                    const d = await api.createGroup(name.trim(), null, pendingCode)
                    setName(''); setPendingCode(null); openGroup(d.group.id)
                  }, null, false)}>
            Create
          </button>
          <span className="sep" />
          <input type="text" placeholder="6-digit join code" value={code} inputMode="numeric"
                 maxLength={6} style={{ width: 130 }}
                 onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
          <button disabled={busy || code.length !== 6}
                  onClick={() => run(async () => {
                    const d = await api.joinGroup(code)
                    setCode(''); openGroup(d.group.id)
                  }, 'Joined.', false)}>
            Join
          </button>
        </div>

        {/* The code and link appear while the name is still being typed, so
            they can be pasted into chat in the same breath as "I made us a
            group". Nothing is reserved — the code is claimed on Create, and if
            it was taken in between the group comes back with a different one,
            which is why the detail pane re-reads it. */}
        {name.trim() && pendingCode && (
          <div className="inviterow">
            <span className="muted">Join code</span>
            <span className="joincode">{pendingCode}</span>
            <button className="chip" type="button"
                    onClick={() => {
                      navigator.clipboard?.writeText(inviteUrl(data?.invite_base, pendingCode))
                      setMsg('Invite link copied — active once you hit Create.')
                    }}>
              Copy invite link
            </button>
            <span className="muted">— live once you hit Create</span>
          </div>
        )}

        {data === null && <p className="muted" style={{ marginTop: 8 }}>Loading…</p>}
        {data?.groups?.length === 0 && (
          <p className="muted" style={{ marginTop: 8 }}>
            None yet — make one above, or join with a code.
          </p>
        )}

        {data?.groups?.length > 0 && (
          <div className="mdgrid">
            <div className="mdlist">
              {data.groups.map((g) => (
                <button key={g.id} className={`mdrow ${open === g.id ? 'active' : ''}`}
                        onClick={() => openGroup(g.id)}>
                  <span className="n">{g.name}</span>
                  <span className="m">
                    {g.member_count} member{g.member_count === 1 ? '' : 's'} · you are {g.my_role}
                  </span>
                </button>
              ))}
            </div>

            <div className="mdpane">
              {detail === null && <p className="muted">Loading…</p>}
              {detail && (
                <>
                  <div className="panehead">
                    <span className="cardtitle">{detail.name}</span>
                    <span className="muted">
                      {detail.members.length} member{detail.members.length === 1 ? '' : 's'} ·
                      {' '}you are {detail.my_role}
                    </span>
                  </div>
                  {detail.description && <p className="note">{detail.description}</p>}

                  <h3>Members</h3>
                  <table className="data" style={{ maxWidth: 560 }}>
                    <thead>
                      <tr>
                        <th className="l">Name</th>
                        <th className="l">Role</th>
                        <th>Joined</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {detail.members.map((m) => (
                        <tr key={m.user_id}>
                          <td className="name l">{m.username}</td>
                          <td className="l muted">{m.role}</td>
                          <td>{fmt.date(m.joined_ts)}</td>
                          <td className="rowactions">
                            {detail.my_role === 'owner' && m.user_id !== detail.owner_user_id && (
                              <button className="chip" disabled={busy}
                                      onClick={() => run(() => api.setMemberRole(
                                        detail.id, m.user_id, m.role === 'admin' ? 'member' : 'admin'))}>
                                {m.role === 'admin' ? 'make member' : 'make admin'}
                              </button>
                            )}
                            {/* the owner can remove anyone but themselves; a group
                                admin can remove plain members (the API refuses
                                admin-on-admin) */}
                            {m.user_id !== detail.owner_user_id
                              && (detail.my_role === 'owner'
                                  || (detail.my_role === 'admin' && m.role !== 'admin')) && (
                              <button className="chip danger" disabled={busy}
                                      onClick={() => run(() => api.removeMember(detail.id, m.user_id),
                                                         `${m.username} removed.`)}>
                                remove
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                      {detail.pending_invites?.map((i) => (
                        <tr key={`inv-${i.id}`}>
                          <td className="name l muted">{i.username}</td>
                          <td className="l muted">invited — waiting</td>
                          <td className="muted">{fmt.date(i.created_ts)}</td>
                          <td />
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {/* Inviting is one line: the code (for voice chat), the link
                      (for Discord — same code, one thing to rotate, works for
                      someone with no account yet), and the rarely-used code
                      controls as quiet chips after it. */}
                  {detail.join_code !== undefined && (
                    <>
                      <h3>Invite people</h3>
                      {detail.join_code ? (
                        <div className="inviterow">
                          <span className="muted">Join code</span>
                          <span className="joincode">{detail.join_code}</span>
                          <button className="chip" disabled={busy}
                                  onClick={() => {
                                    navigator.clipboard?.writeText(inviteUrl(data?.invite_base, detail.join_code))
                                    setMsg('Invite link copied.')
                                  }}>
                            Copy invite link
                          </button>
                          <span className="quietacts">
                            <button className="linklike muted" disabled={busy}
                                    onClick={() => run(() => api.rotateJoinCode(detail.id, { enabled: true }),
                                                       'New code set — the old one no longer works.')}>
                              new code
                            </button>
                            ·
                            <button className="linklike muted" disabled={busy}
                                    onClick={() => run(() => api.rotateJoinCode(detail.id, { enabled: false }),
                                                       'Code joining is off.')}>
                              turn off
                            </button>
                          </span>
                        </div>
                      ) : (
                        <div className="inviterow">
                          <span className="muted">Code joining is off.</span>
                          <button className="chip" disabled={busy}
                                  onClick={() => run(() => api.rotateJoinCode(detail.id, { enabled: true }),
                                                     'Code joining is on.')}>
                            Turn on
                          </button>
                        </div>
                      )}
                      {manage && (
                        <div className="inviterow">
                          <span className="muted">or directly:</span>
                          <input type="text" placeholder="Username" value={invitee} style={{ width: 160 }}
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
                    </>
                  )}

                  <div className="row" style={{ gap: 8, marginTop: 16 }}>
                    {detail.my_role === 'owner' ? (
                      /* Deleting takes every raid that reached this group away
                         from everyone in it, and the button sits under their
                         names. So it asks for the group's name back, typed
                         exactly — the one confirmation an OK button can't be
                         clicked through. The server checks it as well. */
                      confirming ? (
                        <div className="confirmdel">
                          <span className="muted">
                            Type <strong>{detail.name}</strong> to delete this group —
                            everyone in it loses the raids it was sharing.
                          </span>
                          <div className="row" style={{ gap: 8 }}>
                            <input type="text" value={confirmName} autoFocus
                                   placeholder="Group name" style={{ width: 200 }}
                                   autoCapitalize="none" autoCorrect="off" spellCheck="false"
                                   onChange={(e) => setConfirmName(e.target.value)} />
                            <button className="chip danger"
                                    disabled={busy || confirmName !== detail.name}
                                    onClick={() => run(async () => {
                                      await api.deleteGroup(detail.id, confirmName)
                                      setOpen(null); setDetail(null); setConfirming(false)
                                    }, 'Group deleted — an admin can restore it.', false)}>
                              Delete group
                            </button>
                            <button className="linklike muted" disabled={busy}
                                    onClick={() => setConfirming(false)}>
                              cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button className="chip danger" disabled={busy}
                                onClick={() => { setConfirmName(''); setConfirming(true) }}>
                          Delete group
                        </button>
                      )
                    ) : (
                      <button className="chip danger" disabled={busy}
                              onClick={() => run(async () => {
                                await api.leaveGroup(detail.id); setOpen(null); setDetail(null)
                              }, 'Left the group.', false)}>
                        Leave group
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
