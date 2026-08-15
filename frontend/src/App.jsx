import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import FeedbackDialog from './components/FeedbackDialog.jsx'
import Home from './pages/Home.jsx'
import Import from './pages/Import.jsx'
import ZoneRun from './pages/ZoneRun.jsx'
import Compare from './pages/Compare.jsx'
import Features from './pages/Features.jsx'
import Live from './pages/Live.jsx'
import Chat from './pages/Chat.jsx'
import Overlay from './pages/Overlay.jsx'
import Workspace from './pages/Workspace.jsx'
import EncounterRedirect from './pages/EncounterRedirect.jsx'
import Characters from './pages/Characters.jsx'
import Character from './pages/Character.jsx'
import Calibration from './pages/Calibration.jsx'
import Account from './pages/Account.jsx'
import Groups from './pages/Groups.jsx'
import Admin from './pages/Admin.jsx'
import AdminAbilities from './pages/AdminAbilities.jsx'
import AdminTimers from './pages/AdminTimers.jsx'
import JoinGroup from './pages/JoinGroup.jsx'
import Login from './pages/Login.jsx'
import { api } from './lib/api.js'
import { syncMarks } from './lib/marks.js'
import { LEXICON } from './lib/raids.js'
import { SessionContext } from './lib/session.jsx'
import { currentTheme, toggleTheme } from './theme.js'

/* Signed out is a real state, not a wall: published raids read without an
   account (backend `public_runs`), so the shell renders the app either way and
   only the routes that act on your own data ask you to sign in. */
function NeedsAccount({ user, children }) {
  if (user === undefined) return <p className="muted">Loading…</p>
  return user ? children : <Navigate to="/login" replace />
}

/* The plaques, in the gap the nav leaves. The point is one door: a raider lands
   here and reaches the rest without hunting bookmarks.

   They are NOT all the same kind of link, and the bar has to say so. wikQ2 is
   ours and permits framing, so it opens as a tab inside this shell — the header
   stays put and the frame is never unmounted, so coming back lands you exactly
   where you left. eq2lexicon answers every request with `X-Frame-Options: DENY`,
   which the BROWSER enforces against every origin; no markup here can change
   that, so it opens away and wears the arrow that admits it. If they ever add
   `frame-ancestors https://eq2advanced.com`, deleting `away: true` is the whole
   change.

   IN-GAME CHAT SITS BETWEEN THEM AND IS OURS. It is a page of this site, not a
   sibling, so it needs no frame and no arrow — it wears a plaque because of
   what it IS rather than where it lives: a window onto the game's own channels,
   which is the same errand as the wiki and the lexicon and is not a view of
   your parses. That is also why it is still not a nav TAB; the tabs are the
   things you do with a log. */
const SITES = [
  { key: 'wiki', label: 'wikQ2', to: '/wiki',
    src: 'https://wikq2.jupiterns.org/', origin: 'https://wikq2.jupiterns.org',
    title: 'wikQ2 — EQ2 quest waypoints (opens here)' },
  { key: 'chat', label: 'In-game chat', to: '/chat',
    title: 'General, LFG and Auction, relayed live and kept' },
  { key: 'lexicon', label: 'EQ2 Lexicon', away: true,
    href: `${LEXICON}/`,
    title: 'EQ2 Lexicon — opens in a new tab' },
]
const WIKI = SITES.find((s) => s.key === 'wiki')

/* Browser chrome should say where this tab is, especially when a raid night
   leaves several EQ2A pages open. Keep route naming here beside the route map
   rather than scattering document.title writes through every page. */
function pageTitle(pathname) {
  if (pathname === '/') return 'Raid Parses'
  if (pathname === '/wiki') return 'wikQ2'
  if (pathname.startsWith('/zones/')) return 'Raid'
  if (pathname === '/compare') return 'Compare'
  if (pathname === '/features') return 'What It Does'
  if (pathname.startsWith('/encounters/')) return 'Encounter'
  if (pathname.startsWith('/join/')) return 'Join Sharing Group'
  if (pathname === '/login') return 'Sign In'
  if (pathname === '/import' || pathname === '/uploads') return 'Import'
  if (pathname === '/live') return 'Live Parser'
  if (pathname === '/chat') return 'Chat'
  if (pathname.startsWith('/sessions/')) return 'Session'
  if (pathname === '/calibration') return 'Calibration'
  if (pathname === '/characters') return 'Characters'
  if (pathname.startsWith('/characters/')) return 'Character'
  if (pathname === '/groups') return 'Sharing'
  if (pathname === '/admin/abilities') return 'Abilities'
  if (pathname === '/admin/timers') return 'Timers'
  if (pathname.startsWith('/admin')) return 'Admin'
  if (pathname === '/account') return 'Account'
  if (pathname.startsWith('/overlay/')) return 'Overlay'
  if (pathname.startsWith('/ingame/')) return 'In-Game Overlay'
  return null
}

export default function App() {
  const [theme, setTheme] = useState(currentTheme())
  const [user, setUser] = useState(undefined) // undefined = checking, null = signed out
  // undefined = not asked yet, null = the plugin has never been here
  const [live, setLive] = useState(undefined) // | 'idle' | 'parsing' | 'on'
  // whether any receiving live session has an open fight RIGHT NOW — the
  // Live tab's own light, distinct from "connected"
  const [combat, setCombat] = useState(false)
  const [feedback, setFeedback] = useState(null) // the page they were on, or null
  /* The plugin build this account is uploading with, if it is behind the one
     the site is serving. null covers everybody else, which is most people:
     never paired, up to date, or a pairing that has not sent since v30 and so
     has never said what it runs. See routers/plugin_api.py. */
  const [pluginUpdate, setPluginUpdate] = useState(null)
  const location = useLocation()
  const header = useRef(null)

  useEffect(() => {
    const page = pageTitle(location.pathname)
    document.title = page ? `EQ2Advanced - ${page}` : 'EQ2Advanced'
  }, [location.pathname])

  /* Latched, never cleared: the wikQ2 frame is created the first time somebody
     opens that tab and then lives for the rest of the visit. Leaving the page
     HIDES it (`display:none` keeps the document alive) instead of unmounting
     it, which is the whole trick — React removing the node, or merely moving it
     in the tree, reloads the frame and loses their search. Nothing renders it
     until it is asked for, so a raider who never presses wikQ2 never pays for
     it. */
  const [wikiSrc, setWikiSrc] = useState(null)
  const wikiFrame = useRef(null)
  const onWiki = location.pathname === WIKI.to
  /* The theme rides in on the URL so wikQ2's pre-paint script has it before it
     draws anything — a frame that opens white inside a dark shell and corrects
     itself a moment later is worse than no syncing at all. FROZEN once set
     (`?? `): `src` is a prop, so re-rendering a changed one reloads the frame
     and throws away the place this tab exists to keep. Later toggles travel as
     a message instead. */
  useEffect(() => {
    if (onWiki) setWikiSrc((s) => s ?? `${WIKI.src}?theme=${currentTheme()}`)
  }, [onWiki])

  /* wikQ2 ignores anything from an origin it does not know, and we name the one
     window we are talking to — a wildcard target would hand the theme (and the
     fact that this frame exists) to whatever happened to load there. */
  useEffect(() => {
    wikiFrame.current?.contentWindow
      ?.postMessage({ type: 'theme', theme }, WIKI.origin)
  }, [theme])

  /* The framed tab is pinned under the header rather than laid out after it —
     a percentage height inside normal flow gives an iframe nothing to resolve
     against, and it collapses. So the header measures itself: it WRAPS on
     narrow screens, which makes its height a live value and not a constant. */
  useEffect(() => {
    const el = header.current
    if (!el) return undefined
    const measure = () => document.documentElement.style
      .setProperty('--topnav-h', `${el.offsetHeight}px`)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    api.me().then((d) => setUser(d.user)).catch(() => setUser(null))
  }, [])

  /* The hand marks follow the ACCOUNT now (schema v35), so they are read once
     per sign-in and merged with whatever this browser already had — see
     `lib/marks.js: syncMarks` for why the merge goes that way round. Nothing
     waits on it: the panels have already drawn from localStorage, and this
     corrects them. A failure leaves this browser's own marks in force, which
     is exactly the behaviour every version before v35 had. */
  useEffect(() => {
    if (user) syncMarks().catch(() => {})
  }, [user?.id])

  /* Asked once per sign-in, not polled: a plugin release happens a few times a
     year and the answer cannot change while somebody sits on a page. */
  useEffect(() => {
    if (!user) { setPluginUpdate(null); return }
    api.plugin()
      .then((p) => setPluginUpdate(p?.update_available ? p : null))
      .catch(() => {})
  }, [user])

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
        setCombat(s.some((x) => x.source === 'live'
          && x.status === 'receiving' && x.in_combat))
      })
      .catch(() => {})
    check()
    const t = setInterval(check, live === 'on' || live === 'parsing' ? 5_000 : 30_000)
    return () => { dead = true; clearInterval(t) }
  }, [user, live])

  /* The chat light, on every page. Green means somebody is relaying General/
     LFG/Auction this minute; red means nobody is, which is the honest state for
     a box that is fed entirely by other people's plugins — the archive is still
     there to read, and the plaque still goes there.

     Slower than the plugin poll and never faster: this is a light on a door,
     not a meter. It runs signed out too, because the chat needs no account. */
  const [chatOn, setChatOn] = useState(null)   // null until the first answer
  useEffect(() => {
    let dead = false
    const check = () => api.chatStatus()
      .then((d) => { if (!dead) setChatOn(d.connected > 0) })
      // a failed poll is not "nobody is chatting" — it is not knowing, and the
      // light stays where it was rather than going red on a hiccup
      .catch(() => {})
    check()
    const t = setInterval(check, 60_000)
    return () => { dead = true; clearInterval(t) }
  }, [])

  /* The token-authorized screens render BEFORE the shell, not inside it.
     Everything the shell provides — nav, theme toggle, account icon, the
     container's own background — is furniture on somebody's stream or in the
     corner of their game UI, and the page is authorized by the token in its URL
     rather than by the session this provider carries. They are a different
     surface that happens to share a bundle.

     Two paths, one page (`pages/Overlay.jsx`): `/overlay/` is the OBS browser
     source, `/ingame/` is EQ2's own browser window. Separate URLs because they
     are separate LINKS — separate rows, separately revokable (schema v34) —
     and because the path is what tells the page which size it is being read
     at. */
  if (location.pathname.startsWith('/overlay/')
      || location.pathname.startsWith('/ingame/')) {
    return (
      <Routes>
        <Route path="/overlay/:token" element={<Overlay />} />
        <Route path="/ingame/:token" element={<Overlay kind="ingame" />} />
      </Routes>
    )
  }

  return (
    <SessionContext.Provider value={user ?? null}>
      <header className="topnav" ref={header}>
        <div className="topnavinner">
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
              {/* Last, and dressed differently on purpose: every other tab is a
                  place, this one is a state — and while a raid is being
                  uploaded it is the most important thing in the bar, so it goes
                  GREEN rather than merely underlined. The label answers the
                  question a raider actually has — is the raid in a pull right
                  now — before they click.

                  Three states, one hue: not connected is the plain tab dress
                  (there is no parser running to shout about), connected is an
                  outlined green with a dim light, and a pull in progress fills
                  it in. Colour, never motion: this sits in the corner of the eye
                  for a whole raid night. */}
              <NavLink to="/live"
                       className={`navlive ${combat ? 'combat' : live === 'on' ? 'on' : 'off'}`}
                       title={combat ? 'The live parser — a fight is in progress'
                         : live === 'on' ? 'The live parser — connected, between pulls'
                           : 'The live parser — ACT is not sending right now'}>
                {live === 'on' && <i className="dot" />}
                Live Parser
                <em>{combat ? 'In Combat' : live === 'on' ? 'Idle' : 'Off'}</em>
              </NavLink>
            </>}
            {/* Signed-out only. A raider who already uploads does not need the
                pitch in their nav every night; the URL still works for them, and
                it is the one they hand to somebody else. */}
            {user === null && <NavLink to="/features">What it does</NavLink>}
            {user === null && <NavLink to="/login">Sign in</NavLink>}
          </nav>
          {/* Sibling sites, not tabs of this one: a plaque rather than an
              underlined tab, so the bar reads as "here" and "next door" and
              nobody wonders why Compare and wikQ2 look alike. Signed out too —
              somebody who has never uploaded a log still came for the wiki. */}
          <div className="navsites">
            {SITES.map((s) => (s.away ? (
              <a key={s.key} className="sitebtn" href={s.href} title={s.title}
                 target="_blank" rel="noopener noreferrer">
                {s.label}<IconExternal />
              </a>
            ) : (
              /* The chat plaque carries a light and the others do not, because
                 chat is the only one of the three that can be EMPTY right now:
                 the wiki is always there, and whether anybody is relaying
                 General this minute is a fact about the door. The state rides
                 in the link's own title so it is not colour alone. */
              <NavLink key={s.key} className="sitebtn" to={s.to}
                       title={s.key === 'chat' && chatOn !== null
                         ? `${s.title} — ${chatOn ? 'somebody is relaying now'
                           : 'nobody is relaying right now'}`
                         : s.title}>
                {s.label}
                {s.key === 'chat' && chatOn !== null && (
                  <i aria-hidden="true" className={`chatdot${chatOn ? ' on' : ''}`} />
                )}
              </NavLink>
            )))}
          </div>
          {/* One slot, one question about the plugin, answered for whoever is
              asking it. Somebody who has never installed it gets the way in;
              somebody streaming right now gets the light that says so. Between
              those is a raider with the plugin sitting idle at 2pm, who needs
              neither — they know they have it, and they are not raiding. */}
          <div className="navtools">
            {user && live === 'parsing' && (
              /* The one thing the Live Parser tab does NOT say: a log sitting
                 in the parser. Streaming used to light this pill too, and with
                 a green tab three inches to the left saying the same thing it
                 was two objects making one statement. */
              <NavLink to="/import" className="actpill parsing"
                       title="A log is being parsed">
                <IconSignal />
                Parsing
                <i className="dot" />
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
            {/* The other half of that slot: somebody who HAS the plugin, on a
                build older than the one being served. It is the only way to
                reach those people — the plugin has no updater and nobody
                re-reads an install page they finished with months ago — and it
                is deliberately the same shape as the get-it link rather than a
                banner, because it is an offer and not a problem. It cannot
                appear for anyone who has never paired: the version it compares
                came off their own uploads. */}
            {pluginUpdate && (
              <Link to="/import" className="navpill upd"
                    title={`ACT plugin ${pluginUpdate.version} is ready — you are `
                      + `uploading with ${pluginUpdate.your_version}`}>
                Plugin {pluginUpdate.version}
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
        </div>
      </header>
      <main className={`container${onWiki ? ' away' : ''}`}>
        {feedback !== null && (
          <FeedbackDialog page={feedback} onClose={() => setFeedback(null)} />
        )}
        {user === undefined && <p className="muted">Loading…</p>}
        {user !== undefined && (
          <ErrorBoundary resetKey={location.key}>
          <Routes>
            <Route path="/" element={<Home user={user} />} />
            {/* the wikQ2 tab is a real route so the URL and the back button
                work, but it renders nothing here — the frame lives outside
                <Routes> so that navigating away cannot unmount it */}
            <Route path="/wiki" element={null} />
            <Route path="/zones/:id" element={<ZoneRun user={user} />} />
            <Route path="/compare" element={<Compare />} />
            {/* Signed-out, like /compare and for a stronger reason: this is the
                page you send to somebody who has no account yet. */}
            <Route path="/features" element={<Features />} />
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
            {/* Deliberately NOT in the nav — its door is the In-game chat
                plaque with the sibling sites. NO account needed:
                the record has no user in it and every line was broadcast to a
                whole server by the game, so there is nothing here to gate.
                An account is what lets you FILL it and own private Discord
                alert rules, not what lets you read it. */}
            <Route path="/chat" element={<Chat user={user} />} />
            <Route path="/sessions/:id" element={<NeedsAccount user={user}><Workspace /></NeedsAccount>} />
            <Route path="/calibration" element={<NeedsAccount user={user}><Calibration /></NeedsAccount>} />
            <Route path="/characters" element={<NeedsAccount user={user}><Characters /></NeedsAccount>} />
            <Route path="/characters/:id" element={<NeedsAccount user={user}><Character /></NeedsAccount>} />
            <Route path="/groups" element={<NeedsAccount user={user}><Groups /></NeedsAccount>} />
            <Route path="/admin/*" element={
              <NeedsAccount user={user}>
                {user?.role === 'admin' ? <Admin user={user} /> : <Navigate to="/" replace />}
              </NeedsAccount>} />
            <Route path="/admin/abilities" element={
              <NeedsAccount user={user}>
                {['admin', 'curator'].includes(user?.role)
                  ? <AdminAbilities user={user} /> : <Navigate to="/" replace />}
              </NeedsAccount>} />
            <Route path="/admin/timers" element={
              <NeedsAccount user={user}>
                {['admin', 'curator'].includes(user?.role)
                  ? <AdminTimers user={user} /> : <Navigate to="/" replace />}
              </NeedsAccount>} />
            <Route path="/account" element={
              <NeedsAccount user={user}>
                <Account user={user} onSignedOut={() => setUser(null)} onUserChange={setUser} />
              </NeedsAccount>} />
          </Routes>
          </ErrorBoundary>
        )}
      </main>
      {/* Hidden on the wiki tab, where the frame owns the viewport and a bar
          under it would be a second page's furniture on somebody else's site.
          The overlay never reaches here at all — it returns before the shell. */}
      {!onWiki && (
        <footer className="sitefoot">
          <span><b>EQ2 Advanced</b> — a parse site for EverQuest II TLE</span>
          <nav>
            {SITES.map((s) => (s.away ? (
              <a key={s.key} href={s.href} target="_blank" rel="noopener noreferrer">
                {s.label}
              </a>
            ) : (
              <Link key={s.key} to={s.to}>{s.label}</Link>
            )))}
          </nav>
          <span className="muted">
            Not affiliated with Daybreak Game Company.
          </span>
        </footer>
      )}
      {/* Outside <Routes> on purpose. A route element is unmounted the moment
          you navigate off it, and an unmounted frame is a reloaded frame. */}
      {wikiSrc && (
        <div className={`siteframe${onWiki ? '' : ' away'}`}>
          <iframe ref={wikiFrame} src={wikiSrc} title="wikQ2" />
        </div>
      )}
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

const IconSignal = () => (
  <svg {...iconProps} width={12} height={12}>
    <path d="M8 13.2v.1" strokeWidth={2.2} />
    <path d="M5.2 10.4a4 4 0 0 1 5.6 0" />
    <path d="M2.9 7.8a7.2 7.2 0 0 1 10.2 0" />
  </svg>
)

/* Only on the link that actually leaves. The one that opens in place gets no
   glyph, and the difference between them is the message. */
const IconExternal = () => (
  <svg {...iconProps} width={11} height={11} strokeWidth={1.7}>
    <path d="M7.2 3H3v10h10V8.8" />
    <path d="M9.6 3H13v3.4M13 3L7.8 8.2" />
  </svg>
)

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
