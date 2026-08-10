import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api.js'

/* What a stream overlay shows — one control set, two places it appears.

   The settings used to live only on `/account`, which is the wrong desk: the
   person adjusting them is mid-raid with OBS open, looking at the dashboard.
   So the same controls open from the dashboard bar beside the Mini switch
   (`OverlayPanel.jsx`) and stay on the account page for whoever goes looking
   there. One component, because two copies of a settings list is two settings
   lists that disagree.

   The URL is the credential (an OBS browser source sends no cookies), so this
   says so out loud and keeps Revoke one click away rather than behind an edit
   mode. `On` is not `Revoked`: switching off blanks the page while the source
   stays connected and positioned, which is what you want for one pull. Revoke
   kills the link for good.

   DPS is always on — it is what a parse overlay IS — so the metric control is
   a single HPS switch plus where it goes: under the damage bars, or beside
   them. That pair is the whole layout question a stream scene asks. */

const THEMES = [
  ['transparent', 'Transparent', 'For laying over the game in OBS'],
  ['dark', 'Dark', 'A panel with its own background'],
  ['light', 'Light', 'The same, on parchment'],
]

/* An account's overlay links, and the four things anybody does to one. Shared
   by both callers for the same reason the controls are: a settings list that
   saves differently depending on which page it is open on is two features.

   A change lands locally first and is PATCHed after — the page the setting
   changes is in OBS, not here, and it re-reads its config on a timer. */
export function useOverlays() {
  const [overlays, setOverlays] = useState(null)
  const [err, setErr] = useState('')

  const load = useCallback(() => api.overlayTokens()
    .then((d) => setOverlays(d.overlays))
    .catch((e) => setErr(e.message)), [])

  useEffect(() => { load() }, [load])

  const create = useCallback(async (label = 'Stream overlay') => {
    try {
      await api.createOverlayToken({ label })
      await load()
    } catch (e) { setErr(e.message) }
  }, [load])

  const change = useCallback(async (overlay, config) => {
    setOverlays((prev) => prev.map((o) => (
      o.id === overlay.id ? { ...o, config } : o)))
    try {
      await api.updateOverlayToken(overlay.id, { label: overlay.label, config })
    } catch (e) { setErr(e.message) }
  }, [])

  const revoke = useCallback(async (overlay) => {
    try {
      await api.revokeOverlayToken(overlay.id)
      setOverlays((prev) => prev.filter((o) => o.id !== overlay.id))
    } catch (e) { setErr(e.message) }
  }, [])

  return { overlays, err, create, change, revoke, reload: load }
}

function Switch({ on, onChange }) {
  return (
    <span className="switch">
      <input type="checkbox" checked={on} onChange={(ev) => onChange(ev.target.checked)} />
      <i className="track"><i className="knob" /></i>
    </span>
  )
}

function Row({ label, hint, on, children }) {
  return (
    <label className={`settingrow ${on ? 'on' : ''}`}>
      <span className="t">{label}<small>{hint}</small></span>
      {children}
    </label>
  )
}

export default function OverlayOptions({ overlay, onChange, onRevoke }) {
  const cfg = overlay.config || {}
  const metrics = cfg.metrics?.length ? cfg.metrics : ['dps']
  const url = `${window.location.origin}/overlay/${overlay.token}`
  const [copied, setCopied] = useState(false)

  const set = (patch) => onChange({ ...cfg, ...patch })
  const hps = metrics.includes('hps')
  const enabled = cfg.enabled !== false
  const horizontal = cfg.layout === 'horizontal'

  return (
    <div className="overlayrow">
      <div className="urlrow">
        <input readOnly value={url} onFocus={(ev) => ev.target.select()}
               aria-label="Overlay URL" />
        <button onClick={() => {
          navigator.clipboard?.writeText(url)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}>{copied ? 'Copied' : 'Copy link'}</button>
        {onRevoke && <button className="chip danger" onClick={onRevoke}>Revoke</button>}
      </div>

      <Row label="Overlay" on={enabled}
           hint={enabled
             ? 'On — the parse is on your stream'
             : 'Off — the page is blank, the OBS source stays put'}>
        <Switch on={enabled} onChange={(v) => set({ enabled: v })} />
      </Row>

      <Row label="Healing" on={hps}
           hint="A second stack of bars, HPS instead of DPS">
        <Switch on={hps} onChange={(v) => set({ metrics: v ? ['dps', 'hps'] : ['dps'] })} />
      </Row>

      <Row label="AoE timers" on={cfg.show_timers !== false}
           hint="The countdown strip above the rows">
        <Switch on={cfg.show_timers !== false}
                onChange={(v) => set({ show_timers: v })} />
      </Row>

      <Row label="Layout" hint={horizontal ? 'Healing beside damage' : 'Healing under damage'}>
        <span className="chips">
          {[['vertical', 'Vertical'], ['horizontal', 'Horizontal']].map(([k, l]) => (
            <button key={k} className={`chip ${(cfg.layout || 'vertical') === k ? 'on' : ''}`}
                    disabled={!hps} title={hps ? undefined : 'Turn healing on to place it'}
                    onClick={() => set({ layout: k })}>{l}</button>
          ))}
        </span>
      </Row>

      <Row label="Combatants" hint="How many rows fit your scene">
        <input className="rowsnum" type="number" min="1" max="40"
               value={cfg.max_rows ?? 8}
               onChange={(ev) => set({ max_rows: Number(ev.target.value) || 8 })} />
      </Row>

      {/* The setting that decides whether anybody can READ the thing.

          What reaches a viewer is not what is on the streamer's monitor: the
          canvas is downscaled to the output resolution, and then encoded. A
          1440 scene going out at 936p is 1.5× smaller before the encoder has
          spent a bit. Nothing here can know that chain, and every scene's is
          different, so it is a knob — and the honest hint is to turn it up
          until it reads on the STREAM, not in the preview. */}
      <Row label="Text size"
           hint={`${Math.round((cfg.text_scale ?? 1.25) * 100)}% — size it on the`
             + ' stream, not in the OBS preview'}>
        <span className="chips">
          {[[1, '100%'], [1.25, '125%'], [1.5, '150%'], [1.75, '175%']].map(([v, l]) => (
            <button key={v} className={`chip ${(cfg.text_scale ?? 1.25) === v ? 'on' : ''}`}
                    onClick={() => set({ text_scale: v })}>{l}</button>
          ))}
        </span>
      </Row>

      {/* Blank means "fill the browser source", which is what OBS is already
          for. A number pins it, so the parse is the same width every night
          however the source was sized — and a source narrower than the number
          still clips instead of scrolling. */}
      <Row label="Width"
           hint={cfg.width_px ? `${cfg.width_px}px, whatever the source is`
             : 'Pixels — blank fills the OBS source'}>
        <input className="rowsnum" type="number" min="160" max="1920" step="10"
               placeholder="auto" value={cfg.width_px ?? ''}
               onChange={(ev) => set({ width_px: Number(ev.target.value) || null })} />
      </Row>

      <Row label="Theme"
           hint={THEMES.find(([k]) => k === (cfg.theme || 'transparent'))?.[2]}>
        <span className="chips">
          {THEMES.map(([k, l]) => (
            <button key={k} className={`chip ${(cfg.theme || 'transparent') === k ? 'on' : ''}`}
                    onClick={() => set({ theme: k })}>{l}</button>
          ))}
        </span>
      </Row>
    </div>
  )
}
