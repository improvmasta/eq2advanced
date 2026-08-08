import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'

/* The page you link at somebody who has no account. Signed-out reachable,
   like /compare.

   SEVEN FEATURES, ONE OR TWO LINES EACH. It is meant to be taken in at a
   glance by somebody skimming a link, so nothing here gets a paragraph and
   nothing gets added because it is interesting — an eighth feature costs the
   reader the whole page. Screenshots sit with the feature they show.

   Screenshots live in frontend/public/features/ rather than being imported,
   so a missing file is an empty slot instead of a build failure. The cost is
   that a wrong filename fails silently, so check the page after renaming one.
   Vite copies public/ at build time — a file dropped in needs a rebuild.
   `/features/x.webp` does not collide with the `/features` route: the SPA
   fallback only reaches paths that are not real files (backend/spa.py). */

const FEATURES = [
  {
    title: 'Live upload from ACT (with plugin) or drop in logs',
    line: 'The ACT plugin sends your log as you play and also enables live parse. No plugin needed if you would rather drag the log file in yourself.',
  },
  {
    title: 'Parses are private until shared.',
    line: 'Every log you upload is yours. Tells, guild chat, officer chat and chat channels are stripped. Admins have no dashboard access to uploads. Nothing is public, there is no leaderboard. I published a few raids to show how the site works.',
    shots: [['raid-parses.webp', 'Every night you have uploaded']],
  },
  {
    title: 'Parse sharing.',
    line: "Make a group, provide the code or link and parses you've shared with that group will be visible. Automatic sharing can be enabled by character or guild tag.",
    shots: [['sharing.webp', 'Groups, join codes, sharing by character']],
  },
  {
    title: 'Compare parses',
    line: 'Put players, zones and mobs side by side as full ability breakdowns. You can also paste in an ACT screenshot and it reads the table into a column next to your own parses.',
    shots: [['compare.webp', 'An ACT screenshot read into a column']],
  },
  {
    title: 'Advanced stats',
    line: 'Adjusted delay counts button presses instead of DoT ticks. Pet damage can combine to its owner. Death and AoE reports. Individual class reports in progress.',
    shots: [
      ['raid-two-raiders.webp', 'Two raiders side by side, pets combined'],
      ['deaths.webp', 'Deaths, and a tank death second by second'],
      ['aoe-timers.webp', "ACT's timer against what your log shows"],
    ],
  },
  {
    title: 'Live raiding dashboard',
    line: 'Live parse, AoE timers, and a mini parse you can dock to either edge of the screen. Add notes and screenshots to any zone or boss while the raid runs.',
    shots: [['live-dashboard.webp', 'Mid-fight, mini parse docked left']],
  },
  {
    title: 'Stream overlay',
    line: 'A transparent browser source for OBS, with layout options.',
  },
]

/* Its own viewer rather than ShotViewer, which is tied to an imported parse —
   it loads by shot id and captions itself as a re-encoded copy, neither of
   which is true here. It reuses that component's CSS and its one rule:
   clicking anywhere closes it, and so does Escape. */
function ShotModal({ shot, onClose }) {
  useEffect(() => {
    const onKey = (ev) => { if (ev.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return createPortal((
    <div className="shotmodal" role="dialog" aria-modal="true"
         aria-label={shot[1]} onClick={onClose}>
      <div className="shotmodalbody">
        <div className="shotmodalhead" onClick={(ev) => ev.stopPropagation()}>
          <b>{shot[1]}</b>
          <button className="chip" style={{ marginLeft: 'auto' }}
                  onClick={onClose}>Close</button>
        </div>
        <div className="shotmodalscroll full">
          <img src={`/features/${shot[0]}`} alt={shot[1]} />
        </div>
      </div>
    </div>
  ), document.body)
}

function Thumb({ shot, onOpen }) {
  const [ok, setOk] = useState(true)
  if (!ok) return null
  return (
    <button className="featthumb" onClick={onOpen} title="Open it full size">
      <img src={`/features/${shot[0]}`} alt={shot[1]} loading="lazy"
           onError={() => setOk(false)} />
      <span>{shot[1]}</span>
    </button>
  )
}

export default function Features() {
  const [open, setOpen] = useState(null)
  return (
    <div className="features">
      <header className="feathero">
        <h1>A parse site for EQ2 TLE raiding</h1>
        <p className="featlede">
          Stream your log live from ACT or drop the file in, and get parses
          built for raiding — deaths, AoEs, comparisons, and a live dashboard
          for raid night.
        </p>
        <p className="featcta">
          <Link className="btn solid" to="/login">Create an account</Link>
          <Link className="btn" to="/">See a real raid</Link>
          <span className="muted">Free. Private unless you share it.</span>
        </p>
      </header>

      <div className="featgrid">
        {FEATURES.map((f) => (
          <section className="featitem" key={f.title}>
            <h2>{f.title}</h2>
            <p>{f.line}</p>
            {f.shots && (
              <div className={`featthumbs${f.shots.length > 1 ? ' multi' : ''}`}>
                {f.shots.map((s) => (
                  <Thumb key={s[0]} shot={s} onOpen={() => setOpen(s)} />
                ))}
              </div>
            )}
          </section>
        ))}
      </div>

      <section className="featstart">
        <h2>Getting started</h2>
        <p>Make an account, then open <b>Import</b> to get started.</p>
        <p className="featcta">
          <Link className="btn solid" to="/login">Create an account</Link>
          <span className="muted">
            There is a feedback button in the top bar on every page.
          </span>
        </p>
      </section>

      {open && <ShotModal shot={open} onClose={() => setOpen(null)} />}
    </div>
  )
}
