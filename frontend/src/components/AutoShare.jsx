import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api.js'

/* Auto-share: a standing instruction that every raid this character records
   goes to these groups, back catalogue included. It is evaluated when a raid is
   read, not copied onto it, so unticking a group closes the old nights too — and
   a single raid can still be pulled back out from its own Share control.

   This is THE place sharing gets decided for an uploader. The ACT plugin
   deliberately has none of it: a device token sends log lines and cannot change
   who sees them. So it is shown both on Characters (next to the pairing) and on
   Import (next to the plugin download), because those are the two moments
   somebody is thinking about it. `label` lets the caller phrase it for context. */
export default function AutoShare({ char, label = 'Auto-share every raid with:' }) {
  const [groups, setGroups] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.characterShares(char.id).then((d) => setGroups(d.groups)).catch((e) => setError(e.message))
  }, [char.id])

  async function toggle(gid) {
    const next = groups.map((g) => (g.group_id === gid ? { ...g, shared: !g.shared } : g))
    setGroups(next); setBusy(true); setError(null)
    try {
      const d = await api.setCharacterShares(
        char.id, next.filter((g) => g.shared).map((g) => g.group_id))
      setGroups(d.groups)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (groups === null) return null
  if (!groups.length) {
    return <span className="muted">No groups yet — <Link to="/groups">make one</Link>.</span>
  }
  return (
    <span className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
      <span className="muted">{label}</span>
      {groups.map((g) => (
        <button key={g.group_id} className={`chip ${g.shared ? 'on' : ''}`} disabled={busy}
                onClick={() => toggle(g.group_id)}>
          {g.name}
        </button>
      ))}
      {error && <span className="err">{error}</span>}
    </span>
  )
}
