import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { SettingRow as Row, Switch } from './Settings.jsx'

/* What a parse LINK shows — one control set, two kinds of link, three places
   it appears.

   The settings used to live only on `/account`, which is the wrong desk: the
   person adjusting them is mid-raid with OBS open, looking at the dashboard.
   So the same controls open from the dashboard bar beside the Mini switch
   (`OverlayPanel.jsx`) and stay on the account page for whoever goes looking
   there. One component, because two copies of a settings list is two settings
   lists that disagree.

   TWO KINDS (`overlay.kind`, schema v34). `overlay` is the OBS browser source.
   `ingame` is the same page in EQ2's own browser window, and the field set is
   genuinely different rather than the same one relabelled:

   - The overlay is read after an OBS downscale and a lossy encode, from a
     couch. It wants type BIGGER than 1:1, and it wants scene geometry — a
     pinned width and where the second stack goes — because it is being lined
     up against other sources.
   - The in-game window is read at 1:1, on the same monitor as the game, by the
     person playing it. It wants type SMALLER, no geometry at all (the window
     is the width), and the one thing the stream overlay has no business
     having: NOTIFICATIONS. A viewer cannot act on "main tank down"; the player
     looking at this window is who the card was always for.

   The URL is the credential (neither browser sends a cookie), so this says so
   out loud and keeps Revoke one click away rather than behind an edit mode.
   `On` is not `Revoked`: switching off blanks the page while the source stays
   connected and positioned, which is what you want for one pull. Revoke kills
   the link for good — and it kills ONE link, which is why the two kinds are
   two rows.

   DPS is always on — it is what a parse view IS — so the metric control is a
   single HPS switch plus, on the overlay, where it goes. */

const THEMES = [
  ['transparent', 'Transparent', 'For laying over the game in OBS'],
  ['dark', 'Dark', 'A panel with its own background'],
  ['light', 'Light', 'The same, on parchment'],
]

/* In-game, a transparent page is a page you cannot read: EQ2 puts the browser
   in a WINDOW and composites nothing. The choice is only how the panel is
   painted. */
const INGAME_THEMES = THEMES.filter(([k]) => k !== 'transparent')

const SCALES = [[1, '100%'], [1.25, '125%'], [1.5, '150%'], [1.75, '175%']]
/* IN PIXELS, not percentages, because in-game the number means something: the
   page rounds `15 × scale` to whole pixels (`Overlay.jsx`) and the whole point
   of doing that is that the size you pick is the size that gets rasterised.
   "60%" tells you nothing; "9px" is a size you can compare against the ACT
   window sitting next to it. The range runs DOWN from the old floor — the
   window is meant to get small.

   Two decimal places exactly, because the server stores `round(scale, 2)` — a
   chip holding 0.667 would come back as 0.67 and then never light up as the
   one you are on. */
const INGAME_SCALES = [[0.53, '8px'], [0.6, '9px'], [0.67, '10px'], [0.73, '11px']]
/* 11px: small enough to be worth the window, big enough to read while
   fighting. It has to BE one of the chips above or the panel opens with none
   of them lit. */
export const INGAME_SCALE = 0.73

/* An account's parse links, and the four things anybody does to one. Shared
   by every caller for the same reason the controls are: a settings list that
   saves differently depending on which page it is open on is two features.

   `kind` FILTERS, and every caller that owns one surface passes it. The
   dashboard's two buttons each mint and manage their own link, and a panel
   that reached for `overlays[0]` without asking which kind it was would open
   the in-game settings under the Overlay button the moment both exist.

   A change lands locally first and is PATCHed after — the page the setting
   changes is in OBS or in the game client, not here, and it re-reads its
   config on a timer. */
export function useOverlays(kind = null) {
  const [overlays, setOverlays] = useState(null)
  const [err, setErr] = useState('')

  const load = useCallback(() => api.overlayTokens()
    .then((d) => setOverlays(kind
      ? d.overlays.filter((o) => (o.kind || 'overlay') === kind)
      : d.overlays))
    .catch((e) => setErr(e.message)), [kind])

  useEffect(() => { load() }, [load])

  const create = useCallback(async (label = null) => {
    try {
      const k = kind || 'overlay'
      await api.createOverlayToken({
        kind: k, label: label || (k === 'ingame' ? 'In-game window' : 'Stream overlay'),
      })
      await load()
    } catch (e) { setErr(e.message) }
  }, [load, kind])

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

export default function OverlayOptions({ overlay, onChange, onRevoke }) {
  const ingame = (overlay.kind || 'overlay') === 'ingame'
  const cfg = overlay.config || {}
  const metrics = cfg.metrics?.length ? cfg.metrics : ['dps']
  const url = `${window.location.origin}/${ingame ? 'ingame' : 'overlay'}/${overlay.token}`
  const [copied, setCopied] = useState(false)

  const set = (patch) => onChange({ ...cfg, ...patch })
  const hps = metrics.includes('hps')
  const enabled = cfg.enabled !== false
  const horizontal = cfg.layout === 'horizontal'
  const theme = cfg.theme || (ingame ? 'dark' : 'transparent')
  const themes = ingame ? INGAME_THEMES : THEMES
  const scale = cfg.text_scale ?? (ingame ? INGAME_SCALE : 1.25)
  const rows = cfg.max_rows ?? (ingame ? 6 : 8)

  return (
    <div className="overlayrow">
      <div className="urlrow">
        <input readOnly value={url} onFocus={(ev) => ev.target.select()}
               aria-label={ingame ? 'In-game window URL' : 'Overlay URL'} />
        <button onClick={() => {
          navigator.clipboard?.writeText(url)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}>{copied ? 'Copied' : 'Copy link'}</button>
        {onRevoke && <button className="chip danger" onClick={onRevoke}>Revoke</button>}
      </div>

      <Row label={ingame ? 'Window' : 'Overlay'} on={enabled}
           hint={ingame
             ? (enabled ? 'On — the parse is in the game window'
               : 'Off — the page is blank, the window stays open')
             : (enabled ? 'On — the parse is on your stream'
               : 'Off — the page is blank, the OBS source stays put')}>
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

      {/* IN-GAME ONLY, and it is the reason this kind exists rather than
          pointing EQ2's browser at the overlay URL. A death card is an
          instruction — move, cure, taunt — and a stream is watched by the one
          audience that cannot follow one. */}
      {ingame && (
        <Row label="Notifications" on={cfg.notify !== false}
             hint="Tank down, a wipe, a marked AoE about to land">
          <Switch on={cfg.notify !== false} onChange={(v) => set({ notify: v })} />
        </Row>
      )}

      {!ingame && (
        <Row as="div" label="Layout"
             hint={horizontal ? 'Healing beside damage' : 'Healing under damage'}>
          <span className="chips">
            {[['vertical', 'Vertical'], ['horizontal', 'Horizontal']].map(([k, l]) => (
              <button key={k} className={`chip ${(cfg.layout || 'vertical') === k ? 'on' : ''}`}
                      disabled={!hps} title={hps ? undefined : 'Turn healing on to place it'}
                      onClick={() => set({ layout: k })}>{l}</button>
            ))}
          </span>
        </Row>
      )}

      <Row label="Combatants"
           hint={ingame ? 'How many rows you can spare beside the game'
             : 'How many rows fit your scene'}>
        <input className="rowsnum" type="number" min="1" max="40"
               value={rows}
               onChange={(ev) => set({ max_rows: Number(ev.target.value) || rows })} />
      </Row>

      {/* THE SETTING THAT DECIDES WHETHER ANYBODY CAN READ THE THING, and the
          two kinds want it pointed in opposite directions.

          What reaches a stream VIEWER is not what is on the streamer's monitor:
          the canvas is downscaled to the output resolution, and then encoded. A
          1440 scene going out at 936p is 1.5× smaller before the encoder has
          spent a bit. Nothing here can know that chain, and every scene's is
          different, so it is a knob — and the honest hint is to turn it up
          until it reads on the STREAM, not in the preview.

          The in-game window has no such chain: it is read at 1:1 on the same
          monitor, so the only question is how much of the game it is worth
          covering, and the answer starts below 100%. */}
      <Row as="div" label="Text size"
           hint={ingame
             ? `${Math.max(8, Math.round(15 * scale))}px — every pixel here is a`
               + ' pixel of game'
             : `${Math.round(scale * 100)}% — size it on the stream, not in the`
               + ' OBS preview'}>
        <span className="chips">
          {(ingame ? INGAME_SCALES : SCALES).map(([v, l]) => (
            <button key={v} className={`chip ${scale === v ? 'on' : ''}`}
                    onClick={() => set({ text_scale: v })}>{l}</button>
          ))}
        </span>
      </Row>

      {/* Blank means "fill the browser source", which is what OBS is already
          for. A number pins it, so the parse is the same width every night
          however the source was sized — and a source narrower than the number
          still clips instead of scrolling.

          Absent in-game: the window IS the width, and it is resized by dragging
          it, which is the gesture already to hand. */}
      {!ingame && (
        <Row label="Width"
             hint={cfg.width_px ? `${cfg.width_px}px, whatever the source is`
               : 'Pixels — blank fills the OBS source'}>
          <input className="rowsnum" type="number" min="160" max="1920" step="10"
                 placeholder="auto" value={cfg.width_px ?? ''}
                 onChange={(ev) => set({ width_px: Number(ev.target.value) || null })} />
        </Row>
      )}

      <Row as="div" label="Theme"
           hint={themes.find(([k]) => k === theme)?.[2]}>
        <span className="chips">
          {themes.map(([k, l]) => (
            <button key={k} className={`chip ${theme === k ? 'on' : ''}`}
                    onClick={() => set({ theme: k })}>{l}</button>
          ))}
        </span>
      </Row>
    </div>
  )
}
