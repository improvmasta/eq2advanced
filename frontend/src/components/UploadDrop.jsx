import { useEffect, useRef, useState } from 'react'
import { api, fmt } from '../lib/api.js'

/* The log dropzone. Drag files on, or click anywhere on it to browse — the
   behaviour every upload box on the internet has, because that is what people
   already know.

   The character name is DERIVED, not asked for. EQ2 names its logs
   `eq2log_<Character>.txt`, and the server needs that name because the parser's
   subject model hangs off it (a bare logger-name means their PET). Making a
   newcomer type it first was a step that taught them nothing and could only be
   got wrong; now it is read off the file and only surfaced when a file doesn't
   follow the convention. That is also why the name is per-FILE rather than one
   box at the top: a backfill of somebody's whole log folder can span alts. */

const NAME_RE = /^eq2log_([A-Za-z]+)/i

export function nameFromFile(filename) {
  const m = NAME_RE.exec(filename || '')
  if (!m) return null
  // EQ2 first names are one capitalised word — match what the server stores.
  return m[1].charAt(0).toUpperCase() + m[1].slice(1).toLowerCase()
}

export default function UploadDrop({ onUploaded }) {
  const [drag, setDrag] = useState(false)
  const [queue, setQueue] = useState([])          // {name, size, character, state, detail}
  const [busy, setBusy] = useState(false)
  const [limits, setLimits] = useState(null)
  const [askName, setAskName] = useState('')      // fallback when a file isn't eq2log_*
  const fileRef = useRef()
  const edgeCap = limits?.edge_max_bytes || 0

  useEffect(() => { api.uploadLimits().then(setLimits).catch(() => {}) }, [])

  function stage(files) {
    const list = Array.from(files || []).filter(Boolean)
    if (!list.length) return
    setAskName('')   // a new batch is a new question; don't inherit the last answer
    setQueue(list.map((f) => ({
      file: f,
      name: f.name,
      size: f.size,
      character: nameFromFile(f.name) || '',
      state: 'queued',
      detail: null,
    })))
  }

  async function send() {
    const fallback = askName.trim()
    const items = queue.map((q) => ({ ...q, character: q.character || fallback }))
    const missing = items.filter((q) => !q.character)
    if (missing.length) {
      setQueue(items)
      return   // the name prompt below is already showing; nothing to do but wait
    }

    setBusy(true)
    for (let i = 0; i < items.length; i++) {
      const q = items[i]
      // The proxy's cap is checked here because it can only be checked here: it
      // rejects the body on the way in, so uploading to find out costs the whole
      // file and answers with an HTML page nobody can read.
      if (edgeCap && q.size > edgeCap) {
        items[i] = { ...q, state: 'failed',
                     detail: `${fmt.bytes(q.size)} is over the ${fmt.bytes(edgeCap)} single-upload limit — split it` }
        setQueue([...items])
        continue
      }
      items[i] = { ...q, state: 'sending' }
      setQueue([...items])
      try {
        await api.upload(q.file, q.character)
        items[i] = { ...q, state: 'done' }
        onUploaded?.()
      } catch (e) {
        items[i] = { ...q, state: 'failed', detail: e.message }
      }
      setQueue([...items])
    }
    setBusy(false)
  }

  // Two separate questions. `asksName` is whether the prompt BELONGS on screen —
  // it must not depend on what has been typed so far, or the input unmounts on
  // the first keystroke and the name can never be longer than one letter.
  // `needName` is only whether the answer is still missing.
  const asksName = queue.some((q) => !q.character)
  const needName = asksName && !askName.trim()
  const pending = queue.filter((q) => q.state === 'queued').length
  const done = queue.filter((q) => q.state === 'done').length
  const failed = queue.filter((q) => q.state === 'failed').length
  const totalBytes = queue.reduce((n, q) => n + q.size, 0)

  return (
    <div className="uploader">
      <div
        className={`dropzone logslot ${drag ? 'drag' : ''}`}
        onClick={() => !busy && fileRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); if (!busy) stage(e.dataTransfer.files) }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') fileRef.current?.click() }}
      >
        {/* The + is what makes a bordered rectangle read as somewhere you PUT
            something — the same mark the screenshot slot wears, because they
            are the same gesture. The line under it names the files and stops;
            re-uploads being safe and backfills spanning alts are things this
            box handles on its own, and a paragraph promising so was reading
            time spent on a box nobody has failed to use yet. */}
        <span className="plus" aria-hidden="true">+</span>
        <div className="dropmain">Drop log files, or click to browse</div>
        <div className="dropsub"><b>eq2log_*.txt</b></div>
        <input
          ref={fileRef} type="file" accept=".txt,.log,.act" multiple style={{ display: 'none' }}
          onChange={(e) => { stage(e.target.files); e.target.value = '' }}
        />
      </div>

      {queue.length > 0 && (
        <div className="queue">
          <div className="queuehead">
            <b>{queue.length} file{queue.length === 1 ? '' : 's'}</b>
            <span className="muted">{fmt.bytes(totalBytes)}</span>
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              {!busy && pending > 0 && (
                <button onClick={send} disabled={needName}>
                  Upload {pending} file{pending === 1 ? '' : 's'}
                </button>
              )}
              {busy && <span className="muted">Uploading…</span>}
              {!busy && <button className="chip" onClick={() => { setQueue([]); setAskName('') }}>Clear</button>}
            </span>
          </div>

          {asksName && (
            <p className="row" style={{ gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
              <span className="muted">
                Some files aren't named <code>eq2log_&lt;name&gt;.txt</code> — whose logs are they?
              </span>
              <input type="text" placeholder="Character name" value={askName} autoFocus
                     onChange={(e) => setAskName(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter' && !needName && !busy) send() }} />
            </p>
          )}

          <ul className="queuelist">
            {queue.map((q, i) => (
              <li key={i} className={`q-${q.state}`}>
                <span className="qname">{q.name}</span>
                <span className="muted">{q.character || askName.trim() || '—'}</span>
                <span className="muted">{fmt.bytes(q.size)}</span>
                <span className="qstate">
                  {q.state === 'done' ? '✓ uploaded'
                    : q.state === 'sending' ? 'uploading…'
                    : q.state === 'failed' ? '✕ failed'
                    : 'ready'}
                </span>
                {q.detail && <span className="err qdetail">{q.detail}</span>}
              </li>
            ))}
          </ul>

          {!busy && (done > 0 || failed > 0) && (
            <p className="muted" style={{ marginTop: 6 }}>
              {done > 0 && `${done} uploaded. Parsing happens in the background — the table below updates.`}
              {failed > 0 && ` ${failed} failed.`}
            </p>
          )}
        </div>
      )}

      {edgeCap > 0 && (
        <p className="fineprint">
          One upload can be at most {fmt.bytes(edgeCap)}; ACT starts a new log each
          day, so this usually only bites on a backfill. Split a bigger file and
          send the pieces — the overlap is deduped.
        </p>
      )}
    </div>
  )
}
