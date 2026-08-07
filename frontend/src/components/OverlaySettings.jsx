import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'

/* Stream overlay settings — mint the URL, choose what it shows, revoke it.

   The URL is the credential (an OBS browser source sends no cookies), so the
   card treats it like one: it says out loud that anyone holding it sees your
   live meter, and Revoke is always one click away rather than behind an edit
   mode. Nothing else about the account is reachable through it — see
   backend/routers/overlay_api.py. */

const THEMES = [
  ['transparent', 'Transparent', 'For laying over the game in OBS'],
  ['dark', 'Dark', 'A panel with its own background'],
  ['light', 'Light', 'The same, on parchment'],
]

function Editor({ overlay, onChange, onRevoke }) {
  const cfg = overlay.config || {}
  const metrics = cfg.metrics?.length ? cfg.metrics : ['dps']
  const url = `${window.location.origin}/overlay/${overlay.token}`
  const [copied, setCopied] = useState(false)

  const set = (patch) => onChange({ ...cfg, ...patch })
  const toggleMetric = (m) => {
    const next = metrics.includes(m) ? metrics.filter((x) => x !== m) : [...metrics, m]
    set({ metrics: next.length ? next : ['dps'] })   // one of them has to be on
  }

  return (
    <div className="overlayrow">
      <div className="urlrow">
        <input readOnly value={url} onFocus={(ev) => ev.target.select()} />
        <button onClick={() => {
          navigator.clipboard?.writeText(url)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}>{copied ? 'Copied' : 'Copy'}</button>
        <button className="chip danger" onClick={onRevoke}>Revoke</button>
      </div>

      <label className="settingrow">
        <span className="t">Shows
          <small>Damage, healing, or both stacked</small>
        </span>
        <span className="chips">
          {[['dps', 'DPS'], ['hps', 'HPS']].map(([k, l]) => (
            <button key={k} className={`chip ${metrics.includes(k) ? 'on' : ''}`}
                    onClick={() => toggleMetric(k)}>{l}</button>
          ))}
        </span>
      </label>

      <label className="settingrow">
        <span className="t">Theme
          <small>{THEMES.find(([k]) => k === (cfg.theme || 'transparent'))?.[2]}</small>
        </span>
        <span className="chips">
          {THEMES.map(([k, l]) => (
            <button key={k} className={`chip ${(cfg.theme || 'transparent') === k ? 'on' : ''}`}
                    onClick={() => set({ theme: k })}>{l}</button>
          ))}
        </span>
      </label>

      <label className="settingrow">
        <span className="t">Rows
          <small>How many raiders fit on your layout</small>
        </span>
        <input className="rowsnum" type="number" min="1" max="40"
               value={cfg.max_rows ?? 8}
               onChange={(ev) => set({ max_rows: Number(ev.target.value) || 8 })} />
      </label>

      <label className={`settingrow ${cfg.show_timers === false ? '' : 'on'}`}>
        <span className="t">AoE timers
          <small>The countdown strip above the rows</small>
        </span>
        <span className="switch">
          <input type="checkbox" checked={cfg.show_timers !== false}
                 onChange={(ev) => set({ show_timers: ev.target.checked })} />
          <i className="track"><i className="knob" /></i>
        </span>
      </label>
    </div>
  )
}

export default function OverlaySettings() {
  const [overlays, setOverlays] = useState(null)
  const [err, setErr] = useState('')

  const load = () => api.overlayTokens()
    .then((d) => setOverlays(d.overlays))
    .catch((e) => setErr(e.message))

  useEffect(() => { load() }, [])

  async function create() {
    try {
      await api.createOverlayToken({ label: 'Stream overlay' })
      load()
    } catch (e) { setErr(e.message) }
  }

  async function change(overlay, config) {
    setOverlays((prev) => prev.map((o) => (
      o.id === overlay.id ? { ...o, config } : o)))
    try {
      await api.updateOverlayToken(overlay.id, { label: overlay.label, config })
    } catch (e) { setErr(e.message) }
  }

  async function revoke(overlay) {
    try {
      await api.revokeOverlayToken(overlay.id)
      setOverlays((prev) => prev.filter((o) => o.id !== overlay.id))
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="card">
      <h2>Stream overlay</h2>
      <p className="note" style={{ marginTop: 4 }}>
        A page showing your live parse, for an OBS browser source. Anyone with
        the link sees the fight you are in — and nothing else, ever. Revoke it
        and the link stops working.
      </p>
      {err && <p className="err">{err}</p>}
      {overlays === null && <p className="muted">Loading…</p>}
      {overlays?.map((o) => (
        <Editor key={o.id} overlay={o}
                onChange={(cfg) => change(o, cfg)}
                onRevoke={() => revoke(o)} />
      ))}
      {overlays && !overlays.length && (
        <div className="formcol" style={{ marginTop: 8 }}>
          <button onClick={create}>Create an overlay link</button>
        </div>
      )}
      {overlays?.length > 0 && (
        <div className="formcol" style={{ marginTop: 10 }}>
          <button className="chip" onClick={create}>Add another</button>
        </div>
      )}
    </div>
  )
}
