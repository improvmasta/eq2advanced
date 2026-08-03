import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

export default function Sessions() {
  const [sessions, setSessions] = useState(null)
  const [chars, setChars] = useState([])
  const [charName, setCharName] = useState(localStorage.getItem('eq2advanced-char') || '')
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const fileRef = useRef()

  const refresh = useCallback(() => {
    api.sessions().then((d) => setSessions(d.sessions)).catch((e) => setError(e.message))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    api.characters().then((d) => {
      setChars(d.characters)
      // preselect the only paired character
      if (d.characters.length === 1) setCharName((c) => c || d.characters[0].name)
    }).catch(() => {})
  }, [])

  // poll while anything is parsing
  useEffect(() => {
    if (!sessions?.some((s) => s.status === 'parsing' || s.status === 'receiving')) return
    const t = setInterval(refresh, 2000)
    return () => clearInterval(t)
  }, [sessions, refresh])

  async function send(files) {
    const list = Array.from(files || []).filter(Boolean)
    if (!list.length) return
    if (!charName.trim()) { setError('Enter your character name first — the log is written from their point of view.'); return }
    localStorage.setItem('eq2advanced-char', charName.trim())
    setError(null)
    const failed = []
    // backfill-friendly: queue several logs; the server dedupes overlap by
    // line, so re-uploading old files is harmless
    for (let i = 0; i < list.length; i++) {
      setBusy(list.length > 1 ? `Uploading ${i + 1}/${list.length}…` : 'Uploading…')
      try {
        await api.upload(list[i], charName.trim())
        refresh()
      } catch (e) {
        failed.push(`${list[i].name}: ${e.message}`)
      }
    }
    setBusy(false)
    if (failed.length) setError(failed.join(' · '))
  }

  function onDrop(e) {
    e.preventDefault(); setDrag(false)
    send(e.dataTransfer.files)
  }

  const ready = sessions?.filter((s) => s.status === 'ready') ?? []
  const encounterTotal = ready.reduce((n, s) => n + (s.encounter_count || 0), 0)

  return (
    <>
      <div className="pagehead">
        <h1>Sessions</h1>
        <span className="sub">Drop raid logs — each becomes a parsed night</span>
      </div>

      {sessions?.length > 0 && (
        <div className="metrics">
          <div className="metric"><div className="v">{sessions.length}</div><div className="k">Sessions</div></div>
          <div className="metric"><div className="v">{ready.length}</div><div className="k">Parsed</div></div>
          <div className="metric"><div className="v">{fmt.num(encounterTotal)}</div><div className="k">Encounters</div></div>
          <div className="metric"><div className="v">{chars.length}</div><div className="k">Characters</div></div>
        </div>
      )}

      <div
        className={`card dropzone ${drag ? 'drag' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
      >
        <p className="note" style={{ margin: '0 auto 8px' }}>
          Drop <b>eq2log_*.txt</b> files here — several at once is fine, overlap is deduped.
        </p>
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
          {busy || 'Choose log file(s)'}
        </button>
        <input
          ref={fileRef} type="file" accept=".txt,.log" multiple style={{ display: 'none' }}
          onChange={(e) => { send(e.target.files); e.target.value = '' }}
        />
        {error && <p className="err" style={{ marginTop: 8 }}>{error}</p>}
      </div>

      <div className="card">
        <h2>Uploaded</h2>
        {sessions === null && <p className="muted">Loading…</p>}
        {sessions?.length === 0 && <p className="muted">Nothing yet — drop a log above.</p>}
        {sessions?.length > 0 && (
          <div className="tablewrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Session</th><th className="l">Character</th><th>Date</th><th>Lines</th>
                  <th>Encounters</th><th className="l">Status</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id}>
                    <td className="name">
                      {s.status === 'ready'
                        ? <Link to={`/sessions/${s.id}`}>{s.upload_name || `session ${s.id}`}</Link>
                        : (s.upload_name || `session ${s.id}`)}
                    </td>
                    <td className="l">{s.character_name}</td>
                    <td>{fmt.date(s.started_ts ?? s.created_ts)}</td>
                    <td>{fmt.num(s.line_count)}</td>
                    <td>{s.encounter_count}</td>
                    <td className={`l status-${s.status}`}>
                      {s.status}{s.status === 'error' && s.error ? ` — ${s.error.slice(0, 80)}` : ''}
                      {s.pruned ? <span className="badge" title="old events pruned; reports frozen">pruned</span> : null}
                      {s.calibration ? <span className="badge named" title="calibration ground truth">★</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
