import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'

/* Who can see these raids. One list of your groups with a tick each, because
   that is the whole model — plus, for an admin looking at ONE of their own
   raids, the publish switch, which is the only control here that reaches past
   the site's accounts entirely. It is kept visually apart and spelled out for
   that reason, and it is never offered over a multi-raid selection: publishing
   twelve nights by ticking one box is not a thing anyone means to do.

   Several raids at once share one list. A group ticked on some of them but not
   all shows as indeterminate and STAYS that way unless you touch it — saving
   a mixed selection must not quietly share the raids that weren't shared, so
   only the boxes you actually clicked are applied to every raid.

   A group reached by a STANDING share — the character's auto-share, or a guild
   tag its uploader connected — is labelled as such: unticking it hides that one
   raid without switching the standing rule off. Which rule it was doesn't
   change what the tick does, so the label doesn't try to say. */
export default function ShareDialog({ runIds, isAdmin, onClose, onChanged }) {
  const ids = runIds
  // the effect keys on the CONTENT, not the array: a caller passing `[id]`
  // inline would otherwise hand it a new array every render and refetch forever
  const idKey = ids.join(',')
  const [groups, setGroups] = useState(null)   // [{group_id, name, on: 'all'|'some'|'none', auto}]
  const [want, setWant] = useState({})         // group_id -> true|false, only what you clicked
  const [current, setCurrent] = useState({})   // run id -> Set of group ids it reaches now
  const [isPublic, setIsPublic] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let dead = false
    Promise.all(ids.map((id) => api.runShares(id).then((d) => [id, d])))
      .then((pairs) => {
        if (dead) return
        const now = {}
        for (const [id, d] of pairs) {
          now[id] = new Set(d.groups.filter((g) => g.shared).map((g) => g.group_id))
        }
        const first = pairs[0][1]
        setCurrent(now)
        setIsPublic(ids.length === 1 && first.public)
        setGroups(first.groups.map((g) => {
          const n = ids.filter((id) => now[id].has(g.group_id)).length
          return {
            group_id: g.group_id,
            name: g.name,
            on: n === 0 ? 'none' : n === ids.length ? 'all' : 'some',
            // "auto" only if it is standing for every raid in the selection
            auto: pairs.every(([, d]) =>
              d.groups.find((x) => x.group_id === g.group_id)?.auto),
          }
        }))
      })
      .catch((e) => !dead && setError(e.message))
    return () => { dead = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idKey])

  const stateOf = (g) => (want[g.group_id] === undefined
    ? g.on : want[g.group_id] ? 'all' : 'none')

  const toggle = (g) => setWant((w) => ({
    ...w,
    // a mixed group's first click turns it ON for everything — the reading
    // that adds access rather than silently taking it away
    [g.group_id]: stateOf(g) !== 'all',
  }))

  async function save() {
    setBusy(true); setError(null)
    try {
      for (const id of ids) {
        const next = new Set(current[id])
        for (const [gid, on] of Object.entries(want)) {
          if (on) next.add(Number(gid)); else next.delete(Number(gid))
        }
        await api.setRunShares(id, [...next])
      }
      onChanged?.()
      onClose()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function togglePublic(next) {
    setBusy(true); setError(null)
    try {
      const d = await api.setRunPublic(ids[0], next)
      setIsPublic(d.public)
      onChanged?.()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="card confirmcard">
      <h2>{ids.length === 1 ? 'Share this raid' : `Share ${ids.length} raids`}</h2>
      {groups === null && !error && <p className="muted">Loading…</p>}
      {groups?.length === 0 && (
        <p className="muted">
          You're not in any groups yet — make one on the Sharing page.
        </p>
      )}
      {groups?.length > 0 && (
        <div className="togglelist">
          {groups.map((g) => {
            const st = stateOf(g)
            return (
              <label key={g.group_id} className="togglerow">
                <input
                  type="checkbox"
                  checked={st === 'all'}
                  ref={(el) => { if (el) el.indeterminate = st === 'some' }}
                  disabled={busy}
                  onChange={() => toggle(g)}
                />
                <span className="gname">{g.name}</span>
                {g.auto && (
                  <span className="badge"
                        title="A standing rule on your Sharing page sends this group every raid like this one. Unticking hides this raid only.">
                    standing share
                  </span>
                )}
                {st === 'some' && (
                  <span className="muted" title="Shared on some of the selected raids">
                    some
                  </span>
                )}
              </label>
            )
          })}
        </div>
      )}
      {isAdmin && ids.length === 1 && (
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
