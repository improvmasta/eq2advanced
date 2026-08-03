import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api.js'

/* Shared upload dropzone: character-name input + multi-file queue. Used full-
   size on the Uploads page and compact on Home. Calls onUploaded after each
   accepted file so the parent can refresh. */
export default function UploadDrop({ compact = false, onUploaded }) {
  const [chars, setChars] = useState([])
  const [charName, setCharName] = useState(localStorage.getItem('eq2advanced-char') || '')
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const fileRef = useRef()

  useEffect(() => {
    api.characters().then((d) => {
      setChars(d.characters)
      // preselect the only paired character
      if (d.characters.length === 1) setCharName((c) => c || d.characters[0].name)
    }).catch(() => {})
  }, [])

  async function send(files) {
    const list = Array.from(files || []).filter(Boolean)
    if (!list.length) return
    if (!charName.trim()) { setError('Enter your character name first — the log is written from their point of view.'); return }
    localStorage.setItem('eq2advanced-char', charName.trim())
    setError(null)
    const failed = []
    // backfill-friendly: queue several logs; the server dedupes overlap, so
    // re-uploading old files is harmless
    for (let i = 0; i < list.length; i++) {
      setBusy(list.length > 1 ? `Uploading ${i + 1}/${list.length}…` : 'Uploading…')
      try {
        await api.upload(list[i], charName.trim())
        onUploaded?.()
      } catch (e) {
        failed.push(`${list[i].name}: ${e.message}`)
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
          Drop <b>eq2log_*.txt</b> files here — several at once is fine, overlap is deduped.
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
      {error && <p className="err" style={{ marginTop: 8 }}>{error}</p>}
    </div>
  )
}
