import { useEffect, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import FeedbackDialog from './components/FeedbackDialog.jsx'
import Home from './pages/Home.jsx'
import Import from './pages/Import.jsx'
import ZoneRun from './pages/ZoneRun.jsx'
import Compare from './pages/Compare.jsx'
import Live from './pages/Live.jsx'
import Workspace from './pages/Workspace.jsx'
import EncounterRedirect from './pages/EncounterRedirect.jsx'
import Characters from './pages/Characters.jsx'
import Character from './pages/Character.jsx'
import Calibration from './pages/Calibration.jsx'
import Account from './pages/Account.jsx'
import Groups from './pages/Groups.jsx'
import Admin from './pages/Admin.jsx'
import AdminAbilities from './pages/AdminAbilities.jsx'
import JoinGroup from './pages/JoinGroup.jsx'
import Login from './pages/Login.jsx'
import { api } from './lib/api.js'
import { SessionContext } from './lib/session.jsx'
import { currentTheme, toggleTheme } from './theme.js'

/* Signed out is a real state, not a wall: published raids read without an
   account (backend `public_runs`), so the shell renders the app either way and
   only the routes that act on your own data ask you to sign in. */
function NeedsAccount({ user, children }) {
  if (user === undefined) return <p className="muted">Loading…</p>
  return user ? children : <Navigate to="/login" replace />
}

export default function App() {
  const [theme, setTheme] = useState(currentTheme())
  const [user, setUser] = useState(undefined) // undefined = checking, null = signed out
  // undefined = not asked yet, null = the plugin has never been here
  const [live, setLive] = useState(undefined) // | 'idle' | 'parsing' | 'on'
  const [feedback, setFeedback] = useState(null) // the page they were on, or null
  const location = useLocation()

  useEffect(() => {
    api.me().then((d) => setUser(d.user)).catch(() => setUser(null))
  }, [])

  /* The on-air light. 'on' while the plugin is streaming, 'parsing' while any
     import is still chewing, 'idle' once you have ever streamed, and null if
     the plugin has never been here. The header shows a pill for the first two
     and the get-the-plugin link for null; 'idle' says nothing, because a
     raider at 2pm knows they have it. Polls faster while something is
     actually happening. */
  useEffect(() => {
    // signed out has no plugin story, but "still checking who you are" must
    // stay undefined or the plugin link flashes past on every load
    if (!user) { setLive(user === null ? null : undefined); return undefined }
    let dead = false
    const check = () => api.sessions()
      .then((d) => {
        if (dead) return
        const s = d.sessions
        setLive(
          s.some((x) => x.source === 'live' && x.status === 'receiving') ? 'on'
            : s.some((x) => x.status === 'parsing' || x.status === 'receiving') ? 'parsing'
              : s.some((x) => x.source === 'live') ? 'idle' : null)
      })
      .catch(() => {})
    check()
    const t = setInterval(check, live === 'on' || live === 'parsing' ? 5_000 : 30_000)
    return () => { dead = true; clearInterval(t) }
  }, [user, live])

  return (
    <SessionContext.Provider value={user ?? null}>
      <header className="topnav">
        <Link to="/" className="brand">EQ2 Advanced</Link>
        <nav>
          <NavLink to="/">Raid Parses</NavLink>
          {/* signed-out too: published runs compare like they read */}
          <NavLink to="/compare">Compare</NavLink>
          {user && <>
            <NavLink to="/groups">Sharing</NavLink>
            <NavLink to="/import">Import</NavLink>
            {user.role === 'admin' && <NavLink to="/admin">Admin</NavLink>}
            {/* a curator's only door — they have no /admin to reach it from */}
            {user.role === 'curator' && <NavLink to="/admin/abilities">Abilities</NavLink>}
          </>}
          {user === null && <NavLink to="/login">Sign in</NavLink>}
        </nav>
        {/* One slot, one question about the plugin, answered for whoever is
            asking it. Somebody who has never installed it gets the way in;
            somebody streaming right now gets the light that says so. Between
            those is a raider with the plugin sitting idle at 2pm, who needs
            neither — they know they have it, and they are not raiding. */}
        <div className="navtools">
          {user && (live === 'on' || live === 'parsing') && (
            /* A status light, not an alarm: the green dot carries the state
               because the connection working is the GOOD case. */
            <NavLink to="/live" className={`actpill ${live}`}
                     title={live === 'on' ? 'Streaming from ACT right now'
                       : 'A log is being parsed'}>
              <i className="dot" />
              Connected to ACT
            </NavLink>
          )}
          {/* Points at Import rather than straight at the download, so the
              install steps and the sharing settings arrive with the file. */}
          {/* `undefined` is "we haven't asked yet" and shows nothing: a raider
              who has the plugin should not watch a Get-the-plugin pill flash
              past on every page load while the first check comes back. */}
          {live === null && (
            <Link to="/import" className="navpill" title="Get the ACT plugin">
              ACT plugin
            </Link>
          )}
          {/* Everywhere, because a bug is noticed on the raid page and not on
              whatever page a form would otherwise live on. Signed in only —
              the report is worth much more with a name attached to it. */}
          {user && (
            <button className="iconbtn"
                    onClick={() => setFeedback(location.pathname + location.search)}
                    title="Report a bug or suggest something"
                    aria-label="Send feedback">
              <IconFeedback />
            </button>
          )}
          {user && (
            <NavLink to="/account" className="iconbtn" title="Account"
                     aria-label="Account">
              <IconAccount />
            </NavLink>
          )}
          <button className="iconbtn"
                  onClick={() => setTheme(toggleTheme())}
                  title={theme === 'dark' ? 'Switch to light' : 'Switch to dark'}
                  aria-label="Toggle light/dark theme">
            {theme === 'dark' ? <IconSun /> : <IconMoon />}
          </button>
        </div>
      </header>
      <main className="container">
        {feedback !== null && (
          <FeedbackDialog page={feedback} onClose={() => setFeedback(null)} />
        )}
        {user === undefined && <p className="muted">Loading…</p>}
        {user !== undefined && (
          <ErrorBoundary resetKey={location.key}>
          <Routes>
            <Route path="/" element={<Home user={user} />} />
            <Route path="/zones/:id" element={<ZoneRun user={user} />} />
            <Route path="/compare" element={<Compare />} />
            <Route path="/encounters/:id" element={<EncounterRedirect />} />
            {/* an invite link works signed out — it offers sign-up and joins
                the group as soon as the account exists */}
            <Route path="/join/:code" element={<JoinGroup user={user} onAuthed={setUser} />} />
            <Route path="/login" element={
              user ? <Navigate to="/" replace /> : <Login onAuthed={setUser} />} />
            <Route path="/import" element={<NeedsAccount user={user}><Import /></NeedsAccount>} />
            {/* the import hub absorbed the old uploads page */}
            <Route path="/uploads" element={<Navigate to="/import" replace />} />
            <Route path="/live" element={<NeedsAccount user={user}><Live /></NeedsAccount>} />
            <Route path="/sessions/:id" element={<NeedsAccount user={user}><Workspace /></NeedsAccount>} />
            <Route path="/calibration" element={<NeedsAccount user={user}><Calibration /></NeedsAccount>} />
            <Route path="/characters" element={<NeedsAccount user={user}><Characters /></NeedsAccount>} />
            <Route path="/characters/:id" element={<NeedsAccount user={user}><Character /></NeedsAccount>} />
            <Route path="/groups" element={<NeedsAccount user={user}><Groups /></NeedsAccount>} />
            <Route path="/admin" element={
              <NeedsAccount user={user}>
                {user?.role === 'admin' ? <Admin user={user} /> : <Navigate to="/" replace />}
              </NeedsAccount>} />
            <Route path="/admin/abilities" element={
              <NeedsAccount user={user}>
                {['admin', 'curator'].includes(user?.role)
                  ? <AdminAbilities user={user} /> : <Navigate to="/" replace />}
              </NeedsAccount>} />
            <Route path="/account" element={
              <NeedsAccount user={user}>
                <Account user={user} onSignedOut={() => setUser(null)} onUserChange={setUser} />
              </NeedsAccount>} />
          </Routes>
          </ErrorBoundary>
        )}
      </main>
    </SessionContext.Provider>
  )
}

/* Header controls that are a tool rather than a place: feedback, the account,
   the theme. One stroke weight and one box size so they read as a set, and no
   labels — three words in the corner crowded out the nav they sat beside.
   `currentColor` throughout, so hover and the active state are one CSS rule. */
const iconProps = {
  viewBox: '0 0 16 16', width: 16, height: 16, fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.4, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
}

const IconFeedback = () => (
  <svg {...iconProps}>
    <path d="M13.8 10.2a1.5 1.5 0 0 1-1.5 1.5H5.4L2.2 14V3.8a1.5 1.5 0 0 1 1.5-1.5h8.6a1.5 1.5 0 0 1 1.5 1.5z" />
  </svg>
)

const IconAccount = () => (
  <svg {...iconProps}>
    <circle cx="8" cy="5.6" r="2.6" />
    <path d="M2.9 13.9c0-2.5 2.3-4.1 5.1-4.1s5.1 1.6 5.1 4.1" />
  </svg>
)

const IconSun = () => (
  <svg {...iconProps}>
    <circle cx="8" cy="8" r="3.1" />
    <path d="M8 1v1.7M8 13.3V15M15 8h-1.7M2.7 8H1M12.9 3.1l-1.2 1.2M4.3 11.7l-1.2 1.2M12.9 12.9l-1.2-1.2M4.3 4.3L3.1 3.1" />
  </svg>
)

const IconMoon = () => (
  <svg {...iconProps}>
    <path d="M13.6 9.7A5.9 5.9 0 0 1 6.3 2.4a5.9 5.9 0 1 0 7.3 7.3z" />
  </svg>
)
