import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'

/* Auto-share: a standing instruction that raids this character records go to
   these groups. Each share carries two choices of its own, both starting at the
   narrow reading, because a standing instruction should never surprise you with
   what it swept up: only raids recorded from now on (the back catalogue is a
   tick), and only RAIDS — a six-man zone is not what "share my raids with the
   guild" meant, so group content is a tick too. Evaluated when a raid is read,
   not copied onto it, so changes apply to old nights instantly — and a single
   raid can still be pulled back from its own Share control.

   This is THE place sharing gets decided for an uploader, and it lives on the
   Sharing page only. The ACT plugin deliberately has none of it: a device token
   sends log lines and cannot change who sees them. */
export default function AutoShare({ char, refreshKey = 0 }) {
  const [groups, setGroups] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.characterShares(char.id).then((d) => setGroups(d.groups)).catch((e) => setError(e.message))
  }, [char.id, refreshKey])

  async function save(next) {
    setGroups(next); setBusy(true); setError(null)
    try {
      const d = await api.setCharacterShares(
        char.id,
        next.filter((g) => g.shared)
            .map((g) => ({ group_id: g.group_id, history: !!g.history,
                           group_content: !!g.group_content })))
      setGroups(d.groups)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  // switching a share ON starts it at the narrow reading of both choices
  const toggle = (gid) => save(groups.map((g) => (g.group_id === gid
    ? { ...g,
        shared: !g.shared,
        history: g.shared ? g.history : false,
        group_content: g.shared ? g.group_content : false }
    : g)))
  const toggleOpt = (gid, key) => save(groups.map((g) =>
    (g.group_id === gid ? { ...g, [key]: !g[key] } : g)))

  if (groups === null) return null
  if (!groups.length) return <span className="muted">No groups yet.</span>
  return (
    <div className="togglelist">
      {groups.map((g) => (
        <div key={g.group_id} className="togglerow">
          <label className={`switch ${g.shared ? 'on' : ''}`}>
            <input type="checkbox" checked={g.shared} disabled={busy}
                   onChange={() => toggle(g.group_id)} />
            <i className="track"><i className="knob" /></i>
            <span className="gname">{g.name}</span>
          </label>
          {g.shared && (
            <>
              <label className="chip toggle pastraids"
                     title="Unticked, the group gets raids recorded from when sharing was turned on. Tick to also share everything from before.">
                <input type="checkbox" checked={!!g.history} disabled={busy}
                       onChange={() => toggleOpt(g.group_id, 'history')} />
                include past raids
              </label>
              <label className="chip toggle pastraids"
                     title="Unticked, only raids (7+ raiders) reach the group. Tick to send group and solo zones too.">
                <input type="checkbox" checked={!!g.group_content} disabled={busy}
                       onChange={() => toggleOpt(g.group_id, 'group_content')} />
                include group content
              </label>
            </>
          )}
        </div>
      ))}
      {error && <span className="err">{error}</span>}
    </div>
  )
}
