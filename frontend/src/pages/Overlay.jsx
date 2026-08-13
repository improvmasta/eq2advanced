import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import MiniParse from '../components/MiniParse.jsx'
import { INGAME_SCALE } from '../components/OverlayOptions.jsx'
import { hydrateMarks } from '../lib/marks.js'

/* The live meter and nothing else, behind a token in the URL. TWO SCREENS READ
   IT: an OBS browser source (`/overlay/<token>`), and EQ2's own browser window
   (`/ingame/<token>`).

   ONE PAGE, because everything that is hard here is identical for both — the
   config that has to be re-read while the page cannot be refreshed, the
   EventSource that has to survive a restart, the between-pulls state that must
   not go blank. Two copies of that is two copies to keep in step, and the parts
   that genuinely differ are three lines of `kind` (below). What differs is not
   the mechanism, it is the SIZE and the audience:

   - The OBS source is watched after a canvas downscale and a lossy encode,
     from a couch, by people who cannot act on anything. Type bigger than 1:1,
     a transparent default so OBS composites it over the game, and no
     notifications — a death card is an instruction, and the audience for one
     is not the audience for a stream.
   - The in-game window is read at 1:1 on the same monitor as the game, by the
     person playing it. Type SMALLER (every pixel is a pixel of raid), a painted
     background because EQ2 puts the browser in a window and composites
     nothing, and the notification block turned back ON — this is the one
     surface where the player who has to move IS the reader.

   It renders outside the app shell — no nav, no theme toggle, no account —
   because everything the shell provides is furniture on somebody's stream or
   somebody's UI. The token in the URL is the whole credential (neither browser
   sends cookies, and EventSource cannot set a header), and it reaches exactly
   one thing: the fight in progress. See backend/routers/overlay_api.py.

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

export default function Overlay({ kind = 'overlay' }) {
  const ingame = kind === 'ingame'
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

  /* In-game, the page IS the window: EQ2 gives it a fixed rectangle somebody
     has already decided to give up, so anything that does not fit has to
     scroll rather than push the rest off. The overlay must never do this — a
     scrollbar on an OBS source is a grey bar on somebody's stream. */
  useEffect(() => {
    if (!ingame) return undefined
    document.documentElement.dataset.ingame = '1'
    return () => { delete document.documentElement.dataset.ingame }
  }, [ingame])

  /* Settings change from a panel on the dashboard while OBS is pointed at this
     page, so the config is re-read rather than fetched once. A source nobody
     can refresh has to pick its own options up.

     WHICH IS EXACTLY WHY A FAILED READ IS NOT A DEAD LINK. This used to reject
     on anything that was not a 2xx and latch `gone` forever, which turned every
     hiccup into a permanent "this link is no longer active" on the one kind of
     page that cannot be reloaded: a 429, a 502, the server restarting, the game
     client dropping the request while it zones. It got noticed in the in-game
     window, but the OBS source has had it all along — one blown request during
     a raid and the scene reads as revoked until somebody goes into OBS.

     **404 is the only definitive answer**, and it is definitive on purpose:
     `_resolve` returns it for revoked and for never-existed alike, so nothing
     else the server can say means the token is gone. Everything else keeps
     whatever is on screen and tries again on the next tick — and a read that
     succeeds CLEARS the state, so a window that gave up while the server was
     down comes back on its own rather than waiting to be found. */
  useEffect(() => {
    /* A 404 IS FINAL, SO STOP ASKING. This is the other half of the lockout
       described in `overlay_api._resolve`: a revoked link that goes on
       requesting itself every five seconds is not a page waiting for good news,
       it is twelve failed credential attempts a minute, forever, from the same
       address as every working overlay on that machine. Nothing on the other
       end can un-revoke a token, so the poll has nothing left to learn. */
    if (gone) return undefined
    let dead = false
    let timer = null
    let misses = 0
    const again = () => {
      if (dead) return
      /* Five seconds while it is working; backing off toward a minute while it
         is not. A fixed fast poll against something that is failing is how a
         page turns somebody else's outage into its own. */
      timer = setTimeout(read, Math.min(60000, 5000 * 2 ** Math.min(misses, 4)))
    }
    const read = () => fetch(`/api/overlay/${token}`)
      .then((r) => {
        if (r.ok) return r.json()
        if (r.status === 404 && !dead) setGone(true)
        return null
      })
      .then((d) => {
        if (dead) return
        // a transient failure is not news: say nothing, change nothing, retry
        if (d) {
          misses = 0
          setConfig(d.config)
          setGone(false)
          /* THE HAND MARKS COME IN ON THIS READ (schema v35), and this is the
             whole reason they are on the account. Which AoEs get a countdown
             here and which cast owns the burn window are marked by hand on the
             dashboard, and both of these screens are a DIFFERENT BROWSER from
             the one that did the marking — so before this they ran on the
             ACT-list defaults and nothing else, which nobody noticed on a
             stream and was immediately wrong in the game window.

             On the poll rather than once, because that is what the poll is
             for: neither page can be reloaded, and a pill toggled mid-pull has
             to reach the window beside somebody's hotbars on the next tick. It
             is a no-op when nothing changed (`lib/marks.js: same`) — a panel
             whose rule is that nothing moves without news must not re-render
             every five seconds. */
          hydrateMarks(d.marks)
        } else misses += 1
        again()
      })
      .catch(() => { if (!dead) { misses += 1; again() } })
    read()
    return () => { dead = true; clearTimeout(timer) }
  }, [token, gone])

  /* The stream is opened once per token, and re-reading the config above must
     not reconnect it — so this depends on HAVING a config, not on its
     contents. `attempt` is the one other thing allowed to reopen it: see the
     error handler. */
  const ready = !!config
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!ready) return undefined
    let retry = null
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
    /* A DROPPED CONNECTION IS EVENTSOURCE'S PROBLEM; A REFUSED ONE IS OURS.
       It reconnects a stream the server merely closed — a restart, a proxy
       timeout — and that is why this used to be empty. What it will not do is
       retry a non-2xx: a 429 from the overlay bucket, or a 502 while the
       backend is coming up, and it goes to CLOSED and stays there. The page
       then sits with the last frame on screen forever, saying nothing, which
       on a source nobody can refresh is indistinguishable from a raid where
       nothing is happening. CLOSED is the only state worth acting on —
       CONNECTING means it is already handling it. */
    es.onerror = () => {
      if (es.readyState !== 2 || retry) return
      retry = setTimeout(() => setAttempt((n) => n + 1), 5000)
    }
    return () => { clearTimeout(retry); es.close() }
  }, [ready, token, attempt])

  if (gone) {
    return (
      <div className="overlaypage" data-theme-mode="dark">
        {/* Only ever shown for a 404, which means REVOKED (or never real) and
            nothing else — so it can afford to say what to do about it instead
            of hedging. It used to be shown for any failed request, where the
            honest version would have been "something went wrong, possibly
            here". */}
        <p className="muted">
          This {ingame ? 'in-game' : 'overlay'} link was revoked. Copy a new one
          from {ingame ? 'In-game' : 'Overlay'} on the raid dashboard.
        </p>
      </div>
    )
  }
  /* NOT YET, WHICH IS NOT THE SAME AS NOTHING. This drew an empty div, and an
     empty div on a page whose document is painted black is a black window —
     which is exactly how a rate-limited config read presented itself: no card,
     no error, no clue, just a rectangle. `enabled: false` is the one state that
     is genuinely meant to paint nothing, and it now has that to itself. One
     quiet word is also the only thing on either screen you can position an OBS
     source or drag a game window against before the first pull. */
  if (!config) {
    return (
      <div className="overlaypage empty waiting">
        <span className="overlaytag">connecting…</span>
      </div>
    )
  }
  // switched off: the source stays connected and paints nothing at all
  if (config.enabled === false) return <div className="overlaypage empty" />

  const inCombat = live && !!fight
  const shown = fight || lastFight.current || PLACEHOLDER
  const placeholder = !inCombat && !lastFight.current

  const metrics = config.metrics?.length ? config.metrics : ['dps']
  const theme = config.theme || (ingame ? 'dark' : 'transparent')
  const scale = config.text_scale ?? (ingame ? INGAME_SCALE : 1.25)
  return (
    <div className={`overlaypage mini theme-${theme}${ingame ? ' ingame' : ''}${
      inCombat ? '' : ' idle'}`}
         /* a pinned width, or the source's own. `max-width` in the sheet keeps
            a number wider than the source from spilling out of the scene. The
            in-game window has no pinned width at all: the WINDOW is the width,
            and it is resized by dragging it.

            `--ovl` is the Text size setting, and everything in a panel is a
            multiple of it (base.css). Its DEFAULT is the one number that points
            in opposite directions for the two screens: above 1 on a stream (the
            rail's sizes are read at 1:1 on a monitor and this is read after a
            downscale and an encode), below 1 in the game (read at 1:1 on the
            same monitor, where every pixel it takes is a pixel of raid). */
         style={{
           ...(!ingame && config.width_px ? { width: `${config.width_px}px` } : null),
           '--ovl': scale,
           /* IN WHOLE PIXELS, and that is a sharpness fix rather than a tidy
              one. `calc(15px * 0.7)` is 10.5px, every size derived from it in
              `em` lands on another fraction, and a glyph laid out on a half
              pixel is rasterised across two of them — which is most of what
              "small but mushy" was. The overlay keeps the scale factor: it is
              downscaled and re-encoded on the way to a viewer anyway, so a
              half pixel here is the least of what happens to it. */
           ...(ingame ? { '--ovl-px': `${Math.max(8, Math.round(15 * scale))}px` } : null),
         }}>
      {/* `showSuggest` is off: a suggested timer is an errand — go and edit an
          ACT config — and nobody reading either of these screens mid-pull can
          run it. The countdown itself is the same number either way.

          `notify` is the in-game window's alone, and defaults ON there. It is
          the dock's notification block (`MiniAlerts`) — tank down, a wipe, a
          marked AoE about to land — and it belongs here for the reason it does
          not belong on a stream: every card on it is an instruction, and this
          is the only one of these two screens whose reader can follow one.

          Mounted whether or not a pull is running, never gated on `inCombat`:
          deaths are counted by DIFFERENCE against a baseline the block keeps
          (docs/live.md), so a block that unmounted between pulls would open
          every fight announcing the last one's dead. */}
      <MiniParse
        fight={shown}
        metrics={metrics.map((m) => (m === 'hps' ? 'heal' : 'damage'))}
        rows={config.max_rows || (ingame ? 6 : 8)}
        layout={!ingame && config.layout === 'horizontal' ? 'horizontal' : 'vertical'}
        showAoes={!!config.show_timers && inCombat}
        showSuggest={false}
        dense={ingame}
        notify={ingame && config.notify !== false ? { aoes: true, burn: true } : null}
        stale={!inCombat}
      />
      {/* Two lines of caption is two rows of raider in a window this size, so
          in-game the tag says the shorter true thing. */}
      {!inCombat && (
        <span className="overlaytag">
          {placeholder
            ? (ingame ? 'sample parse' : 'sample parse — waiting for combat')
            : 'between pulls'}
        </span>
      )}
    </div>
  )
}
