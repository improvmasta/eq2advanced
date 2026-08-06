import { useEffect, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary.jsx'
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
  const [live, setLive] = useState(null) // null | 'idle' | 'parsing' | 'on'
  const location = useLocation()

  useEffect(() => {
    api.me().then((d) => setUser(d.user)).catch(() => setUser(null))
  }, [])

  /* The on-air light. 'on' while the plugin is streaming, '(parsing)' while
     any import is still chewing, '(idle)' once you have ever streamed —
     null (no pill) if the plugin has never been here. Polls faster while
     something is actually happening. */
  useEffect(() => {
    if (!user) { setLive(null); return undefined }
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
            <NavLink to="/account">Account</NavLink>
          </>}
          {user === null && <NavLink to="/login">Sign in</NavLink>}
          {/* The plugin's status light: sits apart from the tabs because it is
              a state, not a place you were going anyway. It answers one
              question — is ACT talking to us — so it says that in plain words
              and lets the green light carry the state. Red and the word "Live"
              read as an alarm for what is actually the good case. */}
          {user && live && (
            <NavLink to="/live" className={`actpill ${live}`}
                     title={live === 'on' ? 'Streaming from ACT right now'
                       : live === 'parsing' ? 'A log is being parsed'
                         : 'Plugin connected, nothing streaming'}>
              <i className="dot" />
              Connected to ACT
            </NavLink>
          )}
        </nav>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, alignItems: 'center' }}>
          {/* Said up front, on every page, because it is the question anyone
              uploading their raid logs somewhere has first. */}
          <span className="privacynote">
            All parse uploads are private until <Link to="/groups">shared</Link>
          </span>
          {/* The plugin is the fastest way in, and nothing in the nav said so.
              Points at Import rather than straight at the download so the
              install steps and the sharing settings arrive with the file. */}
          <Link to="/import" className="navpill" title="Get the ACT plugin">
            ACT plugin
          </Link>
          <button
            onClick={() => setTheme(toggleTheme())}
            title="Toggle light/dark"
            aria-label="Toggle light/dark theme"
          >
            {theme === 'dark' ? '☀ Light' : '☾ Dark'}
          </button>
        </div>
      </header>
      <main className="container">
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
