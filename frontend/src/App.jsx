import { useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes } from 'react-router-dom'
import Sessions from './pages/Sessions.jsx'
import Live from './pages/Live.jsx'
import Workspace from './pages/Workspace.jsx'
import EncounterRedirect from './pages/EncounterRedirect.jsx'
import Characters from './pages/Characters.jsx'
import Character from './pages/Character.jsx'
import Calibration from './pages/Calibration.jsx'
import Account from './pages/Account.jsx'
import Login from './pages/Login.jsx'
import { api } from './lib/api.js'
import { currentTheme, toggleTheme } from './theme.js'

export default function App() {
  const [theme, setTheme] = useState(currentTheme())
  const [user, setUser] = useState(undefined) // undefined = checking, null = signed out

  useEffect(() => {
    api.me().then((d) => setUser(d.user)).catch(() => setUser(null))
  }, [])

  return (
    <>
      <header className="topnav">
        <Link to="/" className="brand">EQ2 Advanced<span>combat parsing for TLE</span></Link>
        {user && (
          <nav>
            <NavLink to="/">Sessions</NavLink>
            <NavLink to="/live">Live</NavLink>
            <NavLink to="/characters">Characters</NavLink>
            <NavLink to="/calibration">Calibration</NavLink>
            <NavLink to="/account">Account</NavLink>
          </nav>
        )}
        <div style={{ marginLeft: 'auto' }}>
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
        {user === null && <Login onAuthed={setUser} />}
        {user && (
          <Routes>
            <Route path="/" element={<Sessions />} />
            <Route path="/live" element={<Live />} />
            <Route path="/sessions/:id" element={<Workspace />} />
            <Route path="/calibration" element={<Calibration />} />
            <Route path="/encounters/:id" element={<EncounterRedirect />} />
            <Route path="/characters" element={<Characters />} />
            <Route path="/characters/:id" element={<Character />} />
            <Route path="/account" element={<Account user={user} onSignedOut={() => setUser(null)} />} />
          </Routes>
        )}
      </main>
    </>
  )
}
