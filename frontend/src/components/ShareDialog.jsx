import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'

/* Who can see this raid. One list of your groups with a tick each, because
   that is the whole model — plus, for an admin looking at their OWN raid, the
   publish switch, which is the only control here that reaches past the site's
   accounts entirely. It is kept visually apart and spelled out for that reason.

   A group ticked by a STANDING decision is labelled with where that decision was
   made — the character's auto-share, or the ACT uploader's "share tonight" — so
   it is clear what else it covers. Unticking either hides this one raid without
   switching the standing share off. */
export default function ShareDialog({ runId, isAdmin, onClose, onChanged }) {
  const [groups, setGroups] = useState(null)
  const [isPublic, setIsPublic] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.runShares(runId).then((d) => {
      setGroups(d.groups)
      setIsPublic(d.public)
    }).catch((e) => setError(e.message))
  }, [runId])

  const toggle = (gid) => setGroups((gs) =>
    gs.map((g) => (g.group_id === gid ? { ...g, shared: !g.shared } : g)))

  async function save() {
    setBusy(true); setError(null)
    try {
      await api.setRunShares(runId, groups.filter((g) => g.shared).map((g) => g.group_id))
      onChanged?.()
      onClose()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function togglePublic(next) {
    setBusy(true); setError(null)
    try {
      const d = await api.setRunPublic(runId, next)
      setIsPublic(d.public)
      onChanged?.()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="card confirmcard">
      <h2>Share this raid</h2>
      {groups === null && !error && <p className="muted">Loading…</p>}
      {groups?.length === 0 && (
        <p className="muted">
          You're not in any groups yet — make one on the Sharing page.
        </p>
      )}
      {groups?.map((g) => (
        <label key={g.group_id} className="row" style={{ gap: 8 }}>
          <input type="checkbox" checked={g.shared} disabled={busy}
                 onChange={() => toggle(g.group_id)} />
          {g.name}
          {g.auto && (g.source === 'session'
            ? <span className="badge" title="The ACT uploader shared this raid with this group as it was recorded">from ACT</span>
            : <span className="badge" title="This character shares every raid with this group">auto</span>)}
        </label>
      ))}
      {isAdmin && (
        <>
          <hr />
          <label className={`switch ${isPublic ? 'on' : ''}`} title="Anyone with the link, signed in or not">
            <input type="checkbox" checked={isPublic} disabled={busy}
                   onChange={(e) => togglePublic(e.target.checked)} />
            <i className="track"><i className="knob" /></i>
            Public — readable without an account
          </label>
        </>
      )}
      {error && <p className="err">{error}</p>}
      <div className="row">
        <button disabled={busy || groups === null} onClick={save}>Save</button>
        <button className="chip" onClick={onClose}>Cancel</button>
      </div>
    </div>
  )
}
