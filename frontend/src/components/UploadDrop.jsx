import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api.js'

const mb = (b) => Math.round(b / (1 << 20))

/* Shared upload dropzone: character-name input + multi-file queue. Used full-
   size on the Uploads page and compact on Home. Calls onUploaded after each
   accepted file so the parent can refresh. */
export default function UploadDrop({ compact = false, onUploaded }) {
  const [chars, setChars] = useState([])
  const [charName, setCharName] = useState(localStorage.getItem('eq2advanced-char') || '')
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [limits, setLimits] = useState(null)
  // "parse it, don't keep it": the deal offered when a log is too big to store.
  // The stats are permanent either way; what you give up is the ability to
  // reparse that night when the parser gets better.
  const [keepLog, setKeepLog] = useState(true)
  const fileRef = useRef()
  const edgeCap = limits?.edge_max_bytes || 0

  useEffect(() => {
    api.characters().then((d) => {
      setChars(d.characters)
      // preselect the only paired character
      if (d.characters.length === 1) setCharName((c) => c || d.characters[0].name)
    }).catch(() => {})
    api.uploadLimits().then(setLimits).catch(() => {})
  }, [])

  async function send(files) {
    const list = Array.from(files || []).filter(Boolean)
    if (!list.length) return
    if (!charName.trim()) { setError('Enter your character name first.'); return }
    localStorage.setItem('eq2advanced-char', charName.trim())
    setError(null)
    const failed = []
    // backfill-friendly: queue several logs; the server dedupes overlap, so
    // re-uploading old files is harmless
    for (let i = 0; i < list.length; i++) {
      // The proxy's cap is checked here because it can only be checked here:
      // it rejects the body on the way in, so uploading to find out costs the
      // whole file and answers with an HTML page nobody can read.
      if (edgeCap && list[i].size > edgeCap) {
        failed.push(`${list[i].name}: ${mb(list[i].size)} MB is over the ${mb(edgeCap)} MB `
          + 'limit on a single upload — split it into smaller files and send them '
          + 'all; the overlap is deduped.')
        continue
      }
      setBusy(list.length > 1 ? `Uploading ${i + 1}/${list.length}…` : 'Uploading…')
      try {
        await api.upload(list[i], charName.trim(), keepLog)
        onUploaded?.()
      } catch (e) {
        // Our own 413 comes with the way through it. Anything else that big is
        // somebody else's refusal, and quietly agreeing to delete the log would
        // not have helped.
        if (e.status === 413 && e.parseOnlyAllowed) {
          failed.push(`${list[i].name}: ${e.message}`)
          setKeepLog(false)
        } else if (e.status === 413) {
          failed.push(`${list[i].name}: too big to reach the app — it was refused `
            + 'by the proxy in front of the site, before the upload finished.')
        } else {
          failed.push(`${list[i].name}: ${e.message}`)
        }
      }
    }
    setBusy(false)
    if (failed.length) setError(failed.join(' · '))
  }

  return (
    <div
      className={`card dropzone ${compact ? 'compact' : ''} ${drag ? 'drag' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files) }}
    >
      {!compact && (
        <p className="note" style={{ margin: '0 auto 8px' }}>
          Drop <b>eq2log_*.txt</b> files here — overlap is deduped.
        </p>
      )}
      <input
        type="text"
        placeholder="Character name (e.g. Bobby)"
        value={charName}
        list="own-characters"
        onChange={(e) => setCharName(e.target.value)}
      />
      <datalist id="own-characters">
        {chars.map((c) => <option key={c.id} value={c.name} />)}
      </datalist>
      <button disabled={!!busy} onClick={() => fileRef.current?.click()}>
        {busy || (compact ? 'Drop or choose logs' : 'Choose log file(s)')}
      </button>
      <input
        ref={fileRef} type="file" accept=".txt,.log" multiple style={{ display: 'none' }}
        onChange={(e) => { send(e.target.files); e.target.value = '' }}
      />
      {limits?.upload_max_bytes > 0 && (
        <p className="note" style={{ marginTop: 8 }}>
          Logs are kept up to {mb(limits.upload_max_bytes)} MB.
        </p>
      )}
      {edgeCap > 0 && (
        <p className="note" style={{ marginTop: 8 }}>
          One upload at a time can be at most {mb(edgeCap)} MB. A longer log has to
          be split — ACT starts a new file each day, so this is usually a backfill.
        </p>
      )}
      <label className="row" style={{ gap: 6, justifyContent: 'center', marginTop: 8 }}
             title="The parse is permanent either way; the raw log is what allows a reparse.">
        <input type="checkbox" checked={!keepLog}
               onChange={(e) => setKeepLog(!e.target.checked)} />
        <span className="muted">Parse it and delete the log (saves space, can't be re-parsed later)</span>
      </label>
      {error && <p className="err" style={{ marginTop: 8 }}>{error}</p>}
    </div>
  )
}
