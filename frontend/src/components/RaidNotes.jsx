import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, fmt } from '../lib/api.js'
import { lexiconRaid } from '../lib/raids.js'
import ShotDrop from './ShotDrop.jsx'

/* The dashboard's right-hand column: what you write down mid-raid.

   Where a note LANDS is the whole feature. On trash it belongs to the zone; on
   a named it belongs to that boss — so tonight's "the third add spawns behind
   you" files itself with every other note anyone ever wrote about that pull,
   and six months of them read as an outline of the zone. The page picks the
   subject from what the meter is showing and says which one it chose; the
   toggle is there because the parser's guess about a one-word boss is exactly
   the case where it will be wrong.

   What it SHOWS is the whole zone, not the subject being written to. Standing
   in Emerald Halls you want the notes on the pull coming up as much as the one
   that just ended, and a column that showed only what the meter happened to be
   pointing at hid the zone's own notes the moment a named was engaged. The
   subject you are filing under sits first and says so; the rest of the zone
   follows under its own headings.

   Screenshots paste. That is not a nicety — mid-raid, the way a picture leaves
   the game is Print Screen, and asking anyone to name a file while the boss is
   up means the screenshot never gets attached at all. */

export function Lightbox({ noteId, shot, onClose }) {
  useEffect(() => {
    const onKey = (ev) => { if (ev.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  /* Into document.body, never where it is written: every `.card` carries
     backdrop-filter, which is a containing block for position:fixed AND a
     stacking context, so an overlay written inside one is sealed into it. */
  return createPortal((
    <div className="shotmodal" role="dialog" aria-modal="true"
         aria-label="Raid screenshot" onClick={onClose}>
      <div className="shotmodalbody">
        <div className="shotmodalhead" onClick={(ev) => ev.stopPropagation()}>
          <b>Raid screenshot</b>
          <button className="chip" style={{ marginLeft: 'auto' }} onClick={onClose}>
            Close
          </button>
        </div>
        <div className="shotmodalscroll">
          <img src={api.noteShotImage(noteId, shot.id)} alt="" />
        </div>
      </div>
    </div>
  ), document.body)
}

/* One filed note. Exported because the outline on the raid list draws the same
   thing — two renderings of a note is how they end up disagreeing about which
   date a note carries. `onDelete` absent makes it read-only, which is what the
   outline wants: deleting belongs beside the composer that wrote it. */
export function NoteItem({ note, onDelete, onView }) {
  return (
    <div className="noteitem">
      <div className="nh">
        <span className="when">{fmt.date(note.created_ts)}</span>
        {onDelete && (
          <button className="chip danger" onClick={() => onDelete(note.id)}>delete</button>
        )}
      </div>
      {note.body && <p>{note.body}</p>}
      {!!note.shots?.length && (
        <div className="noteshots">
          {note.shots.map((s) => (
            <button key={s.id} className="shotthumb"
                    onClick={() => onView({ noteId: note.id, shot: s })}>
              <img src={api.noteShotImage(note.id, s.id, true)} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/* A heading for one subject inside a zone, with the way out to the encounter's
   strategy page beside it. The link is only offered for a NAMED: eq2lexicon
   files strategy per boss, and a link to a zone's trash would be a promise
   about a page that has nothing to say. */
export function SubjectHead({ zone, mob, count, current }) {
  const href = mob ? lexiconRaid(zone, mob) : null
  return (
    <div className={`notesubject ${current ? 'current' : ''}`}>
      <b>{mob || zone}</b>
      {!mob && <span className="muted">zone</span>}
      {current && <span className="badge">filing here</span>}
      <span className="n">{count}</span>
      {href && (
        <a className="chip offsite" href={href} target="_blank" rel="noreferrer"
           title={`${mob} on EQ2 Lexicon — opens in a new tab`}>
          strategy
        </a>
      )}
    </div>
  )
}

/* Zone notes first, then the nameds by name — except the subject being written
   to, which is pulled to the top. What you are filing under is what you are
   looking at. */
export function groupBySubject(notes, current) {
  const by = new Map()
  for (const n of notes) {
    const key = n.mob_name || ''
    if (!by.has(key)) by.set(key, [])
    by.get(key).push(n)
  }
  return [...by.entries()]
    .map(([mob, list]) => ({ mob: mob || null, notes: list }))
    .sort((a, b) => (
      (b.mob === current) - (a.mob === current)
      || (a.mob !== null) - (b.mob !== null)
      || (a.mob || '').localeCompare(b.mob || '')))
}

const OPEN_KEY = 'eq2a.notes.open'

export default function RaidNotes({ zone, mob }) {
  /* Collapsed is a real state for this column, not a nicety: on a pull you
     want the whole width for the parse, and the notes are something you open
     when there is something to write. Remembered, like the mini rail's side —
     it is a fact about how this desk is set up. */
  const [open, setOpen] = useState(() => localStorage.getItem(OPEN_KEY) !== '0')
  useEffect(() => { localStorage.setItem(OPEN_KEY, open ? '1' : '0') }, [open])
  const [onMob, setOnMob] = useState(!!mob)
  const [mobName, setMobName] = useState(mob || '')
  const [body, setBody] = useState('')
  const [staged, setStaged] = useState([])       // File objects, not yet a note
  const [notes, setNotes] = useState([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [viewing, setViewing] = useState(null)   // {noteId, shot}
  const composing = useRef(false)

  /* Follow the fight unless the writer has taken over. Retyping the boss's
     name every pull would be worse than a wrong default; overwriting what
     somebody is halfway through typing would be worse than both. */
  useEffect(() => {
    if (composing.current) return
    setOnMob(!!mob)
    setMobName(mob || '')
  }, [mob])

  const target = useMemo(() => ({
    zone,
    mob: onMob ? (mobName.trim() || null) : null,
  }), [zone, onMob, mobName])

  /* The whole zone, not the subject: switching the composer between Zone and a
     named must not change what is on screen under it, or the column flickers
     between two piles every time the meter picks up a boss. */
  const load = useCallback(() => {
    if (!target.zone) { setNotes([]); return }
    api.notes(target.zone, null, 'zone')
      .then((d) => setNotes(d.notes))
      .catch((e) => setErr(e.message))
  }, [target.zone])

  useEffect(() => { load() }, [load])

  async function save() {
    if (busy || !target.zone) return
    if (!body.trim() && !staged.length) return
    setBusy(true)
    setErr('')
    try {
      let note = await api.addNote({
        zone: target.zone, mob_name: target.mob, body,
      })
      for (const file of staged) note = await api.addNoteShot(note.id, file)
      setBody('')
      setStaged([])
      composing.current = false
      setNotes((prev) => [note, ...prev.filter((n) => n.id !== note.id)])
    } catch (e) {
      setErr(e.message || 'Could not save that note.')
    } finally {
      setBusy(false)
    }
  }

  async function remove(id) {
    try {
      await api.deleteNote(id)
      setNotes((prev) => prev.filter((n) => n.id !== id))
    } catch (e) {
      setErr(e.message)
    }
  }

  /* The heading is the collapse control: the switch across from it, and the
     word itself taking the click too, because a title with a switch beside it
     that only answers to the switch is a target people miss. Collapsed, the
     card is this row alone — with the count on it, so it still says whether
     there is anything in there. */
  const head = (count) => {
    const hint = open ? 'Collapse the notes column' : 'Open the notes column'
    return (
      <div className="notehead">
        <h2 onClick={() => setOpen(!open)} title={hint}>Notes</h2>
        {count > 0 && <span className="n" title={`${count} in this zone`}>{count}</span>}
        {/* the site's switch, not a bespoke one: same control, same shape as
            every other on/off on the site */}
        <label className="switch" title={hint}>
          <input type="checkbox" checked={open} aria-label={hint}
                 onChange={(ev) => setOpen(ev.target.checked)} />
          <i className="track"><i className="knob" /></i>
        </label>
      </div>
    )
  }

  if (!zone) {
    return (
      <div className="card notes">
        {head(0)}
        {open && (
          <p className="note">
            Notes file themselves against the zone you are in, or the named you
            are pulling. They start once the raid does.
          </p>
        )}
      </div>
    )
  }

  const subject = target.mob || zone
  const groups = groupBySubject(notes, target.mob)
  if (!open) return <div className="card notes closed">{head(notes.length)}</div>

  return (
    <div className="card notes">
      {head(notes.length)}
      <div className="notetarget">
        <div className="chips">
          <button className={`chip ${onMob ? '' : 'on'}`}
                  onClick={() => { composing.current = true; setOnMob(false) }}>
            Zone
          </button>
          <button className={`chip ${onMob ? 'on' : ''}`}
                  onClick={() => { composing.current = true; setOnMob(true) }}>
            Named
          </button>
        </div>
        {onMob ? (
          <input
            className="mobname" value={mobName} placeholder="Which named?"
            onChange={(ev) => { composing.current = true; setMobName(ev.target.value) }}
          />
        ) : (
          <span className="zonename" title="Zone notes cover the trash between nameds">
            {zone}
          </span>
        )}
      </div>

      {/* The composer is a text box with its button under it, in the place a
          text box's button goes. It used to sit below the screenshot drop,
          which put the thing you press furthest from the thing you type.

          ENTER FILES IT. A raid note is a line, not a paragraph — you are
          typing it with one hand while the next pull forms — so the key that
          ends a line is the key that files it, and Shift+Enter is there for
          the rare note that wants two. Ctrl+Enter keeps working: it was the
          only way for a year and fingers remember. */}
      <textarea
        className="notebody" rows={2} value={body}
        placeholder={`What happened on ${subject}? — Enter files it`}
        onChange={(ev) => setBody(ev.target.value)}
        onKeyDown={(ev) => {
          if (ev.key !== 'Enter') return
          if (ev.shiftKey) return
          ev.preventDefault()
          save()
        }}
      />

      <div className="noteactions">
        <button disabled={busy || (!body.trim() && !staged.length)}
                onClick={save}>
          {busy ? 'Saving…' : `File under ${target.mob ? target.mob : 'the zone'}`}
        </button>
        {staged.length > 0 && (
          <span className="staged">
            {staged.length} shot{staged.length === 1 ? '' : 's'} attached
            {' '}
            <button className="chip" onClick={() => setStaged([])}>clear</button>
          </span>
        )}
      </div>

      {/* Small on purpose: pasting is what this is for and a paste needs no
          target, so the box only has to be big enough to say so and to be
          dropped on. It was a third of the column. */}
      <ShotDrop
        compact
        className="noteshot"
        busyLabel="Attaching…"
        /* Staged, not uploaded: a screenshot has nowhere to go until the note
           it belongs to exists, so the "upload" here is the identity and the
           real POSTs happen in `save`. */
        upload={async (file) => file}
        onImported={(file) => setStaged((prev) => [...prev, file])}
        label={<p className="muted">Paste or drop a screenshot</p>}
      />
      {err && <p className="err">{err}</p>}

      <div className="notelist">
        {!notes.length && <p className="muted">Nothing filed in {zone} yet.</p>}
        {groups.map((g) => (
          <div key={g.mob || ''} className="notegroup">
            <SubjectHead zone={zone} mob={g.mob} count={g.notes.length}
                         current={g.mob === target.mob} />
            {g.notes.map((n) => (
              <NoteItem key={n.id} note={n} onDelete={remove} onView={setViewing} />
            ))}
          </div>
        ))}
      </div>

      {viewing && (
        <Lightbox noteId={viewing.noteId} shot={viewing.shot}
                  onClose={() => setViewing(null)} />
      )}
    </div>
  )
}
