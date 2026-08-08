import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import MiniParse from '../components/MiniParse.jsx'

/* The stream overlay: the live meter and nothing else, for an OBS browser
   source.

   It renders outside the app shell — no nav, no theme toggle, no account —
   because everything the shell provides is furniture on somebody's stream. The
   token in the URL is the whole credential (a browser source sends no cookies,
   and EventSource cannot set a header), and it reaches exactly one thing: the
   fight in progress. See backend/routers/overlay_api.py.

   It draws the MINI parse (`MiniParse.jsx`), the same object the dashboard
   docks to the window edge, because a stream scene and a strip beside the game
   are the same constraint: narrow, glanceable, and read by somebody who cannot
   click it. It used to render the full dashboard meter, which was a page
   scaled down rather than a thing designed for the space.

   `transparent` is the default theme because that is what an overlay is for:
   OBS composites the page over the game, so the page must not paint a
   background over it. Dark and light exist for anyone framing it as a panel.

   The source is opened once and left running for hours, so this NEVER shows an
   error state for "nothing is streaming" — but it no longer goes BLANK either.
   A meter that only exists mid-fight cannot be positioned in OBS, and a stream
   between pulls showed a hole where the parse was. So out of combat it keeps
   the last fight on screen, dimmed; before any fight has happened it draws a
   placeholder parse, clearly marked, so there is always a rectangle to size
   and something for viewers to read.

   OFF is the one state that does go blank, and that is the point of it: the
   scene keeps its source and its position while the parse is not on screen.
   Turning it back on is a switch in the dashboard's Overlay panel, not a trip
   into OBS. */

const PLACEHOLDER = {
  zone: 'Waiting for combat',
  elapsed_s: 143,
  provisional_name: 'No fight yet',
  provisional_is_named: false,
  raid: { damage: 3521000, dps: 24622, heals: 1490000, hps: 10420, deaths: 0, raiders: 8 },
  aoes: [],
  actors: [
    ['Sableblade', 'swashbuckler', 612000, 9000],
    ['Emberlyn', 'conjuror', 545000, 4000],
    ['Frostweave', 'wizard', 498000, 2000],
    ['Grimhowl', 'berserker', 402000, 11000],
    ['Cadenza', 'troubador', 351000, 3000],
    ['Bulwark', 'guardian', 322000, 15000],
    ['Thornheart', 'fury', 208000, 391000],
    ['Lumina', 'templar', 121000, 456000],
  ].map(([name, cls, damage, heals]) => ({
    name, kind: 'player', class: cls,
    damage, dps: Math.round(damage / 143), max_hit: Math.round(damage / 9),
    heals, hps: Math.round(heals / 143), max_heal: Math.round(heals / 7),
    damage_taken: 0, deaths: 0,
  })),
}

export default function Overlay() {
  const { token } = useParams()
  const [config, setConfig] = useState(null)
  const [gone, setGone] = useState(false)
  const [fight, setFight] = useState(null)
  const [live, setLive] = useState(false)
  /* The most recent fight this source has seen — what fills the frame between
     pulls. Deliberately NOT cleared when the stream goes quiet: the numbers on
     screen say what the last pull did until the next one starts. */
  const lastFight = useRef(null)

  useEffect(() => {
    document.documentElement.dataset.overlay = '1'
    return () => { delete document.documentElement.dataset.overlay }
  }, [])

  /* The theme setting has to reach the TOKENS, not just the page background:
     a browser source has no stored preference, so the panels would otherwise
     draw dark parchment-less bars on the light theme's background. */
  useEffect(() => {
    if (!config) return
    document.documentElement.dataset.theme = config.theme === 'light' ? 'light' : 'dark'
  }, [config?.theme])

  /* Settings change from a panel on the dashboard while OBS is pointed at this
     page, so the config is re-read rather than fetched once. A source nobody
     can refresh has to pick its own options up. */
  useEffect(() => {
    let dead = false
    const read = () => fetch(`/api/overlay/${token}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then((d) => { if (!dead) setConfig(d.config) })
      .catch(() => { if (!dead) setGone(true) })
    read()
    const id = setInterval(read, 5000)
    return () => { dead = true; clearInterval(id) }
  }, [token])

  /* The stream is opened once per token, and re-reading the config above must
     not reconnect it — so this depends on HAVING a config, not on its
     contents. */
  const ready = !!config
  useEffect(() => {
    if (!ready) return undefined
    const es = new EventSource(`/api/overlay/${token}/stream`)
    es.addEventListener('partial', (ev) => {
      const f = JSON.parse(ev.data).fight
      if (f) lastFight.current = f
      setFight(f)
    })
    es.addEventListener('status', (ev) => {
      const st = JSON.parse(ev.data)
      setLive(st.live)
      if (!st.live) setFight(null)
    })
    es.onerror = () => { /* EventSource retries; an overlay must survive a restart */ }
    return () => es.close()
  }, [ready, token])

  if (gone) {
    return (
      <div className="overlaypage" data-theme-mode="dark">
        <p className="muted">This overlay link is no longer active.</p>
      </div>
    )
  }
  if (!config) return <div className="overlaypage empty" />
  // switched off: the source stays connected and paints nothing at all
  if (config.enabled === false) return <div className="overlaypage empty" />

  const inCombat = live && !!fight
  const shown = fight || lastFight.current || PLACEHOLDER
  const placeholder = !inCombat && !lastFight.current

  const metrics = config.metrics?.length ? config.metrics : ['dps']
  return (
    <div className={`overlaypage mini theme-${config.theme || 'transparent'}${
      inCombat ? '' : ' idle'}`}
         /* a pinned width, or the source's own. `max-width` in the sheet keeps
            a number wider than the source from spilling out of the scene. */
         style={config.width_px ? { width: `${config.width_px}px` } : undefined}>
      <MiniParse
        fight={shown}
        metrics={metrics.map((m) => (m === 'hps' ? 'heal' : 'damage'))}
        rows={config.max_rows || 8}
        layout={config.layout === 'horizontal' ? 'horizontal' : 'vertical'}
        showAoes={!!config.show_timers && inCombat}
        stale={!inCombat}
      />
      {!inCombat && (
        <span className="overlaytag">
          {placeholder ? 'sample parse — waiting for combat' : 'between pulls'}
        </span>
      )}
    </div>
  )
}
