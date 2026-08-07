import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, fmt } from '../lib/api.js'
import ShotDrop from './ShotDrop.jsx'

/* The dashboard's right-hand column: what you write down mid-raid.

   Where a note LANDS is the whole feature. On trash it belongs to the zone; on
   a named it belongs to that boss — so tonight's "the third add spawns behind
   you" files itself with every other note anyone ever wrote about that pull,
   and six months of them read as an outline of the zone. The page picks the
   subject from what the meter is showing and says which one it chose; the
   toggle is there because the parser's guess about a one-word boss is exactly
   the case where it will be wrong.

   Screenshots paste. That is not a nicety — mid-raid, the way a picture leaves
   the game is Print Screen, and asking anyone to name a file while the boss is
   up means the screenshot never gets attached at all. */

function Lightbox({ noteId, shot, onClose }) {
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

export default function RaidNotes({ zone, mob }) {
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

  const load = useCallback(() => {
    if (!target.zone) { setNotes([]); return }
    api.notes(target.zone, target.mob)
      .then((d) => setNotes(d.notes))
      .catch((e) => setErr(e.message))
  }, [target.zone, target.mob])

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

  if (!zone) {
    return (
      <div className="card notes">
        <h2>Notes</h2>
        <p className="note">
          Notes file themselves against the zone you are in, or the named you
          are pulling. They start once the raid does.
        </p>
      </div>
    )
  }

  const subject = target.mob || zone
  return (
    <div className="card notes">
      <h2>Notes</h2>
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

      <textarea
        className="notebody" rows={3} value={body}
        placeholder={`What happened on ${subject}?`}
        onChange={(ev) => setBody(ev.target.value)}
        onKeyDown={(ev) => {
          // typing a note one-handed between pulls: Ctrl/Cmd+Enter files it
          if ((ev.metaKey || ev.ctrlKey) && ev.key === 'Enter') save()
        }}
      />

      <ShotDrop
        compact
        className="noteshot"
        busyLabel="Attaching…"
        /* Staged, not uploaded: a screenshot has nowhere to go until the note
           it belongs to exists, so the "upload" here is the identity and the
           real POSTs happen in `save`. */
        upload={async (file) => file}
        onImported={(file) => setStaged((prev) => [...prev, file])}
        label={<p className="muted"><b>Paste a screenshot</b> — or drop one here.</p>}
      />
      {staged.length > 0 && (
        <p className="note">
          {staged.length} screenshot{staged.length === 1 ? '' : 's'} attached to this note.
          {' '}
          <button className="chip" onClick={() => setStaged([])}>clear</button>
        </p>
      )}

      <div className="noteactions">
        <button disabled={busy || (!body.trim() && !staged.length)}
                onClick={save}>
          {busy ? 'Saving…' : `File under ${target.mob ? target.mob : 'the zone'}`}
        </button>
      </div>
      {err && <p className="err">{err}</p>}

      <div className="notelist">
        {!notes.length && <p className="muted">Nothing filed under {subject} yet.</p>}
        {notes.map((n) => (
          <div key={n.id} className="noteitem">
            <div className="nh">
              <span className="when">{fmt.date(n.created_ts)}</span>
              <button className="chip danger" onClick={() => remove(n.id)}>delete</button>
            </div>
            {n.body && <p>{n.body}</p>}
            {!!n.shots?.length && (
              <div className="noteshots">
                {n.shots.map((s) => (
                  <button key={s.id} className="shotthumb"
                          onClick={() => setViewing({ noteId: n.id, shot: s })}>
                    <img src={api.noteShotImage(n.id, s.id, true)} alt="" loading="lazy" />
                  </button>
                ))}
              </div>
            )}
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
