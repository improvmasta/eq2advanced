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

/* The rows themselves, shared by both standing rules — a character's shares and
   a guild tag's — so the two halves of the Sharing page look and behave
   identically.

   A phone settings list: one line per thing, its name on the left and a switch
   on the right, and the two choices a share carries as indented lines of
   exactly the same shape with a line of explanation under each. Every row here
   answers "is this on", which is a switch's question and not a checkbox's, and
   mixing the two idioms is what made the choices read as status rather than as
   something you can change. */
export function ShareRows({ rows, subject, busy, onToggle, onOption, error }) {
  if (!rows?.length) return <span className="muted">No groups yet.</span>
  return (
    <div className="togglelist">
      {rows.map((g) => (
        <div key={g.group_id} className="sharegroup">
          <label className={`settingrow ${g.shared ? 'on' : ''}`}>
            <span className="t"><span className="gname">{g.name}</span></span>
            <span className="switch">
              <input type="checkbox" checked={!!g.shared} disabled={busy}
                     onChange={() => onToggle(g.group_id)} />
              <i className="track"><i className="knob" /></i>
            </span>
          </label>
          {g.shared && (
            <div className="shareopts">
              <label className={`settingrow sub ${g.history ? 'on' : ''}`}>
                <span className="t">
                  Past Raids
                  <small>Share past raids by {subject}</small>
                </span>
                <span className="switch">
                  <input type="checkbox" checked={!!g.history} disabled={busy}
                         onChange={() => onOption(g.group_id, 'history')} />
                  <i className="track"><i className="knob" /></i>
                </span>
              </label>
              <label className={`settingrow sub ${g.group_content ? 'on' : ''}`}>
                <span className="t">Share Solo/Group Content</span>
                <span className="switch">
                  <input type="checkbox" checked={!!g.group_content} disabled={busy}
                         onChange={() => onOption(g.group_id, 'group_content')} />
                  <i className="track"><i className="knob" /></i>
                </span>
              </label>
            </div>
          )}
        </div>
      ))}
      {error && <span className="err">{error}</span>}
    </div>
  )
}

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
  return <ShareRows rows={groups} subject={char.name} busy={busy} onToggle={toggle}
                    onOption={toggleOpt} error={error} />
}

/* The same standing rule keyed on a guild TAG instead of one character, so a
   new alt is covered without a new switch. Stored per GROUP (the API takes the
   whole set of my tags for one group at a time), so a row here edits that
   group's set rather than this guild's — `edit` hands the caller a function
   from the group's current set to its next one. */
export function GuildShare({ guildName, groups, shares, busy, onEdit, error }) {
  const rows = groups.map((g) => {
    const s = shares.find((x) => x.group_id === g.id && x.guild_name === guildName)
    return { group_id: g.id, name: g.name, shared: !!s,
             history: !!s?.history, group_content: !!s?.group_content }
  })
  const toggle = (gid) => onEdit(gid, (cur) => (
    cur.some((s) => s.guild_name === guildName)
      ? cur.filter((s) => s.guild_name !== guildName)
      : [...cur, { guild_name: guildName, history: false, group_content: false }]))
  const toggleOpt = (gid, key) => onEdit(gid, (cur) => cur.map((s) => (
    s.guild_name === guildName ? { ...s, [key]: !s[key] } : s)))
  return <ShareRows rows={rows} subject={guildName} busy={busy} onToggle={toggle}
                    onOption={toggleOpt} error={error} />
}
