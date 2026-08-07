import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import LiveMeter from '../components/LiveMeter.jsx'

/* The stream overlay: the live meter and nothing else, for an OBS browser
   source.

   It renders outside the app shell — no nav, no theme toggle, no account —
   because everything the shell provides is furniture on somebody's stream. The
   token in the URL is the whole credential (a browser source sends no cookies,
   and EventSource cannot set a header), and it reaches exactly one thing: the
   fight in progress. See backend/routers/overlay_api.py.

   `transparent` is the default theme because that is what an overlay is for:
   OBS composites the page over the game, so the page must not paint a
   background over it. Dark and light exist for anyone framing it as a panel.

   The source is opened once and left running for hours, so this NEVER shows an
   error state for "nothing is streaming". It shows nothing at all, and starts
   drawing the moment a raid does. */

export default function Overlay() {
  const { token } = useParams()
  const [config, setConfig] = useState(null)
  const [gone, setGone] = useState(false)
  const [fight, setFight] = useState(null)
  const [live, setLive] = useState(false)

  useEffect(() => {
    document.documentElement.dataset.overlay = '1'
    return () => { delete document.documentElement.dataset.overlay }
  }, [])

  useEffect(() => {
    let dead = false
    fetch(`/api/overlay/${token}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then((d) => { if (!dead) setConfig(d.config) })
      .catch(() => { if (!dead) setGone(true) })
    return () => { dead = true }
  }, [token])

  useEffect(() => {
    if (!config) return undefined
    const es = new EventSource(`/api/overlay/${token}/stream`)
    es.addEventListener('partial', (ev) => setFight(JSON.parse(ev.data).fight))
    es.addEventListener('status', (ev) => {
      const st = JSON.parse(ev.data)
      setLive(st.live)
      if (!st.live) setFight(null)
    })
    es.onerror = () => { /* EventSource retries; an overlay must survive a restart */ }
    return () => es.close()
  }, [config, token])

  if (gone) {
    return (
      <div className="overlaypage" data-theme-mode="dark">
        <p className="muted">This overlay link is no longer active.</p>
      </div>
    )
  }
  if (!config || !live || !fight) return <div className="overlaypage empty" />

  const metrics = config.metrics?.length ? config.metrics : ['dps']
  return (
    <div className={`overlaypage theme-${config.theme || 'transparent'}`}>
      {metrics.map((m) => (
        <LiveMeter
          key={m}
          fight={fight}
          metric={m === 'hps' ? 'heal' : 'damage'}
          maxRows={config.max_rows || 8}
          showTimers={!!config.show_timers && m === metrics[0]}
          showChart={false}
        />
      ))}
    </div>
  )
}
