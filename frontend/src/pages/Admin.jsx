import { useCallback, useEffect, useState } from 'react'
import { Link, NavLink, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

/* Running the site without reading it.

   Everything on this page is a count, a size, a status or a setting. There is
   no route from here into anybody's parses, and that is enforced in the backend
   (`security.py` keeps the admin role out of every visibility decision), not
   just left out of this UI.

   Tabs rather than one scroll: these are six unrelated jobs — is the site
   healthy, who came to look, who has an account, what is published, what did
   somebody report, what did an admin do — and only one of them is ever the
   reason you opened the page. Each tab fetches its own data, so opening Admin
   is one request. */

const ADMIN_NAV = [
  { to: '/admin', label: 'Dashboard', end: true },
  { heading: 'Operations' },
  { to: '/admin/incidents', label: 'Incidents' },
  { to: '/admin/feedback', label: 'Feedback' },
  { to: '/admin/activity', label: 'Activity' },
  { heading: 'People' },
  { to: '/admin/accounts', label: 'Accounts' },
  { to: '/admin/groups', label: 'Deleted groups' },
  { heading: 'Game data' },
  { to: '/admin/abilities', label: 'Abilities' },
  { to: '/admin/timers', label: 'AoE timers' },
]

const PAGE_SIZE = 50
const mb = (n) => (n == null ? '—' : `${(n / (1 << 20)).toFixed(1)} MB`)
const bytesOrOff = (n) => (n ? mb(n) : 'unlimited')
const toMb = (n) => (n == null ? '' : String(Math.round(n / (1 << 20))))

/* Every tab does the same three things around a mutation: clear the last
   message, run it, refetch. */
function useAction(refresh) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [msg, setMsg] = useState(null)
  const run = useCallback(async (fn, note) => {
    setBusy(true); setError(null); setMsg(null)
    try { await fn(); if (note) setMsg(note); await refresh() }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }, [refresh])
  return { busy, error, msg, run, setError, setMsg }
}

function Flash({ error, msg }) {
  return (
    <>
      {error && <p className="err">{error}</p>}
      {msg && <p className="note flash">{msg}</p>}
    </>
  )
}

export default function Admin({ user: me }) {
  const { pathname, search } = useLocation()
  const legacy = new URLSearchParams(search).get('tab')
  if (pathname === '/admin' && legacy) {
    const old = { overview: '', visitors: 'visitors', accounts: 'accounts',
      content: 'groups', feedback: 'feedback', audit: 'activity' }[legacy]
    if (old !== undefined) return <Navigate replace to={`/admin${old ? `/${old}` : ''}`} />
  }

  let view = <Navigate replace to="/admin" />
  if (pathname === '/admin') view = <OverviewTab />
  else if (pathname === '/admin/incidents') view = <IncidentsTab />
  else if (pathname === '/admin/visitors') view = <VisitorsTab />
  else if (pathname === '/admin/accounts') view = <AccountsTab me={me} />
  else if (pathname === '/admin/groups') view = <ContentTab />
  else if (pathname === '/admin/feedback') view = <FeedbackTab />
  else if (pathname === '/admin/activity') view = <AuditTab />

  return (
    <div className="manage adminshell">
      <aside className="adminrail" aria-label="Admin sections">
        <div className="adminbrand">Admin</div>
        {ADMIN_NAV.map((item, i) => item.heading
          ? <div className="adminnavhead" key={`${item.heading}-${i}`}>{item.heading}</div>
          : item.disabled
            ? <span className="adminnav disabled" key={item.to} title="Planned">{item.label}</span>
            : <NavLink key={item.to} to={item.to} end={item.end}
                       className={({ isActive }) => `adminnav${isActive ? ' on' : ''}`}>
                {item.label}
              </NavLink>)}
      </aside>
      <section className="adminmain">{view}</section>
    </div>
  )
}

/* ---------- Overview: is anything broken, and the site-wide knobs ---------- */

function OverviewTab() {
  const [d, setD] = useState(null)
  const [form, setForm] = useState(null)
  const refresh = useCallback(() => Promise.all([api.adminOverview(), api.adminDashboard()]).then(([r, dashboard]) => {
    setD({ ...r, dashboard })
    setForm({
      upload_max_bytes: toMb(r.settings.upload_max_bytes),
      storage_max_bytes: toMb(r.settings.storage_max_bytes),
      registration_open: !!r.settings.registration_open,
    })
  }), [])
  const { busy, error, msg, run, setError } = useAction(refresh)
  useEffect(() => { refresh().catch((e) => setError(e.message)) }, [refresh, setError])

  if (!d) return <><Flash error={error} msg={msg} />{!error && <p className="muted">Loading…</p>}</>
  const { alerts, live } = d
  const dirty = Number(form.upload_max_bytes || 0) * (1 << 20) !== d.settings.upload_max_bytes
    || Number(form.storage_max_bytes || 0) * (1 << 20) !== d.settings.storage_max_bytes

  return (
    <>
      <div className="pagehead">
        <div><h1>Dashboard</h1><p className="muted">Site health and open work.</p></div>
      </div>
      <Flash error={error} msg={msg} />

      <h2 className="adminsectiontitle">Site status</h2>
      <div className="adminstatus">
        <Link className={`statustile ${alerts.length ? 'degraded' : ''}`} to="/admin/incidents">
          <span>{alerts.length ? 'Degraded' : 'Healthy'}</span>
          <b>{live.parsing} parsing</b><small>{alerts.length} open incident{alerts.length === 1 ? '' : 's'}</small>
        </Link>
        <div className="statustile">
          <span>{live.receiving ? 'Active' : 'Quiet'}</span>
          <b>{live.receiving} live stream{live.receiving === 1 ? '' : 's'}</b><small>ingest connections</small>
        </div>
        <div className="statustile">
          <span>{d.dashboard.status.reference.state}</span>
          <b>Reference data</b><small>Census and wiki cache available</small>
        </div>
        <div className="metric">
          <div className="v">{mb(d.storage.uploads_dir_bytes)}</div>
          <div className="k">stored uploads</div>
        </div>
      </div>

      {/* Only what somebody has to act on. A streaming plugin is the healthiest
          state a session has, and the old panel listed every one of them as a
          job needing attention — a 24-raider night read as 24 problems. */}
      <div className="card">
        <div className="panelhead"><h2>Action queue</h2><Link to="/admin/incidents">View all →</Link></div>
        {alerts.length === 0 && <p className="muted">No admin action needed.</p>}
        <IncidentRows alerts={alerts.slice(0, 5)} compact />
        {d.dashboard.actions.feedback_open > 0 && <div className="alertrow"><span className="badge">feedback</span><span className="who">{d.dashboard.actions.feedback_open} open</span><Link className="primaryaction" to="/admin/feedback?status=open">Review feedback</Link></div>}
        {d.dashboard.actions.abilities_open > 0 && <div className="alertrow"><span className="badge">game data</span><span className="who">{d.dashboard.actions.abilities_open} abilities unreviewed</span><Link className="primaryaction" to="/admin/abilities?status=unreviewed">Review next</Link></div>}
        {(live.receiving > 0 || live.parsing > 0) && (
          <p className="muted livenote">
            {live.receiving > 0 && `${live.receiving} live stream${live.receiving === 1 ? '' : 's'}`}
            {live.receiving > 0 && live.parsing > 0 && ', '}
            {live.parsing > 0 && `${live.parsing} parsing`}
            {' '}— working normally.
          </p>
        )}
      </div>

      <div className="card">
        <div className="panelhead"><h2>Usage and growth</h2><Link to="/admin/visitors">30-day visitors →</Link></div>
        <div className="metrics compactmetrics">
          <div className="metric"><div className="v">{d.dashboard.usage.visitor_days}</div><div className="k">visitor-days · 30d</div></div>
          <div className="metric"><div className="v">{d.dashboard.usage.uploads}</div><div className="k">uploads · 30d</div></div>
          <div className="metric"><div className="v">{d.dashboard.usage.completed_raids}</div><div className="k">completed raids · 30d</div></div>
          <div className="metric"><div className="v">{d.dashboard.usage.active_accounts}</div><div className="k">active accounts · 30d</div></div>
          <div className="metric"><div className="v">{mb(d.dashboard.usage.storage_growth_bytes)}</div><div className="k">storage growth · 30d</div></div>
        </div>
        <details><summary>All-time totals</summary><p className="muted">{d.counts.users} accounts · {d.counts.sessions} logs · {d.counts.zone_runs} raids · {d.counts.encounters} encounters · {d.counts.public_runs} published raids</p></details>
      </div>

      <div className="card"><div className="panelhead"><h2>Recent changes</h2><Link to="/admin/activity">Full activity →</Link></div>
        {d.dashboard.recent_changes.map((e) => <div className="alertrow" key={e.id}><span className="muted">{fmt.date(e.ts)}</span><span>{e.label}</span></div>)}
      </div>

      <div className="card slim">
        <h2>Storage settings</h2>
        <div className="formcol">
          <label>
            Max upload, MB (0 = unlimited)
            <input type="number" min="0" value={form.upload_max_bytes}
                   onChange={(e) => setForm({ ...form, upload_max_bytes: e.target.value })} />
          </label>
          <label>
            Max stored per account, MB (0 = unlimited)
            <input type="number" min="0" value={form.storage_max_bytes}
                   onChange={(e) => setForm({ ...form, storage_max_bytes: e.target.value })} />
          </label>
          <div className={`row ${dirty ? 'unsavedbar' : ''}`}>
            <button disabled={busy} onClick={() => run(() => api.adminSettings({
              upload_max_bytes: Math.max(0, Number(form.upload_max_bytes) || 0) * (1 << 20),
              storage_max_bytes: Math.max(0, Number(form.storage_max_bytes) || 0) * (1 << 20),
            }), 'Settings saved.')}>
              Save
            </button>
            {dirty && <b>Unsaved changes</b>}
            {/* over the cap the uploader is offered "parse it and delete the
                log" rather than a refusal, so a low cap is not a wall */}
            <span className="muted">
              now: {bytesOrOff(d.settings.upload_max_bytes)} per upload,
              {' '}{bytesOrOff(d.settings.storage_max_bytes)} stored
            </span>
          </div>
        </div>
      </div>
    </>
  )
}

function IncidentRows({ alerts, compact = false }) {
  const [busy, setBusy] = useState(null)
  const [results, setResults] = useState({})
  const [notes, setNotes] = useState({})
  const retry = async (id) => {
    setBusy(id)
    try {
      await api.adminRetryIncident(id)
      setResults((r) => ({ ...r, [id]: 'Parse started.' }))
    } catch (e) {
      setResults((r) => ({ ...r, [id]: e.message }))
    } finally { setBusy(null) }
  }
  const acknowledge = async (id) => {
    setBusy(id)
    try { await api.adminAcknowledgeIncident(id, notes[id]); setResults((r) => ({ ...r, [id]: 'Acknowledged.' })) }
    catch (e) { setResults((r) => ({ ...r, [id]: e.message })) } finally { setBusy(null) }
  }
  return alerts.map((a) => (
    <div key={a.id} className="alertrow incidentrow">
      <span className={`badge ${a.kind === 'error' ? 'bad' : ''}`}>{a.kind === 'error' ? 'failed' : 'stuck'}</span>
      <span className="who">Session {a.id}</span>
      <span className="muted">{a.username} / {a.character} · {a.kind === 'stuck'
        ? fmt.dur(a.age_s) : String(a.error || 'Parse failed').split('\n').pop()}</span>
      {!compact && <details><summary>Technical detail and support bundle</summary><pre>{a.error || a.summary}</pre><button className="chip" onClick={() => navigator.clipboard?.writeText(`session=${a.id}\nsource=${a.source}\ncreated=${a.created_ts}\nlast_seen=${a.last_seen_ts}\nstatus=${a.status}\nerror=${a.error || ''}`)}>Copy support bundle</button></details>}
      {a.retryable && <button className="chip primaryaction" disabled={busy === a.id}
              onClick={() => retry(a.id)}>{a.kind === 'stuck' ? 'Restart parse' : 'Retry parse'}</button>}
      {!a.retryable && <span className="muted">{a.support_instruction}</span>}
      {!compact && !a.retryable && <><input value={notes[a.id] || ''} onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })} placeholder="Acknowledgement note" /><button className="chip" disabled={busy === a.id || !notes[a.id]?.trim()} onClick={() => acknowledge(a.id)}>Acknowledge</button></>}
      {results[a.id] && <span className={results[a.id] === 'Parse started.' ? 'note' : 'err'}>{results[a.id]}</span>}
    </div>
  ))
}

function IncidentsTab() {
  const [d, setD] = useState(null)
  const [error, setError] = useState(null)
  const [state, setState] = useState('open')
  useEffect(() => { api.adminIncidents({ state }).then((r) => setD({ alerts: r.items })).catch((e) => setError(e.message)) }, [state])
  return (
    <>
      <div className="pagehead"><div><h1>Incidents</h1><p className="muted">Failed and abandoned parse jobs.</p></div></div>
      <div className="filterbar"><button className={`chip ${state === 'open' ? 'on' : ''}`} onClick={() => setState('open')}>Open</button><button className={`chip ${state === 'acknowledged' ? 'on' : ''}`} onClick={() => setState('acknowledged')}>Acknowledged</button><button className={`chip ${state === 'all' ? 'on' : ''}`} onClick={() => setState('all')}>All active</button></div>
      {error && <p className="err">{error}</p>}
      {!d && !error && <p className="muted">Loading…</p>}
      {d?.alerts.length === 0 && <div className="card"><h2>All clear</h2><p className="muted">No open incidents.</p></div>}
      {d?.alerts.length > 0 && <div className="card incidentlist"><IncidentRows alerts={d.alerts} /></div>}
    </>
  )
}

/* ---------- Accounts: search, sort and page on the server ---------- */

const USER_COLS = [
  { key: 'username', label: 'User', align: 'l' },
  { key: 'character_count', label: 'Chars' },
  { key: 'run_count', label: 'Raids' },
  { key: 'session_count', label: 'Logs' },
  { key: 'error_count', label: 'Errors' },
  { key: 'stored_bytes', label: 'Stored' },
  { key: 'last_login_ts', label: 'Last seen' },
]

function AccountsTab({ me }) {
  const [q, setQ] = useState('')
  const [typed, setTyped] = useState('')
  const [sort, setSort] = useState({ key: 'stored_bytes', dir: 'desc' })
  const [offset, setOffset] = useState(0)
  const [d, setD] = useState(null)
  const [sel, setSel] = useState(null)

  // one request per pause in typing, not one per keystroke
  useEffect(() => {
    const t = setTimeout(() => { setQ(typed); setOffset(0) }, 300)
    return () => clearTimeout(t)
  }, [typed])

  const refresh = useCallback(() => api.adminUsers(
    { q, sort: sort.key, dir: sort.dir, limit: PAGE_SIZE, offset },
  ).then((r) => {
    setD(r)
    // the panel is showing a row that was just refetched; keep it in step
    setSel((s) => (s ? r.users.find((u) => u.id === s.id) ?? s : s))
  }), [q, sort, offset])
  const act = useAction(refresh)
  useEffect(() => { refresh().catch((e) => act.setError(e.message)) }, [refresh]) // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (key) => setSort((s) => {
    setOffset(0)
    // numbers are interesting from the top; names from the front
    return s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
      : { key, dir: key === 'username' ? 'asc' : 'desc' }
  })

  return (
    <><div className="pagehead"><div><h1>Accounts</h1><p className="muted">Identity, access, limits, and recovery.</p></div></div><RegistrationSettings /><div className="card">
      <div className="filterbar">
        <input type="text" placeholder="Find an account…" value={typed}
               onChange={(e) => setTyped(e.target.value)} />
        {d && <span className="muted">{d.total} account{d.total === 1 ? '' : 's'}</span>}
      </div>

      <Flash error={act.error} msg={act.msg} />

      {sel && <AccountPanel u={sel} me={me} act={act} onClose={() => setSel(null)} />}

      {d === null && <p className="muted">Loading…</p>}
      {d?.users.length === 0 && <p className="muted">No account matches that.</p>}
      {d?.users.length > 0 && (
        <table className="data">
          <thead>
            <tr>
              {USER_COLS.map((c) => (
                <th key={c.key} className={`sort ${c.align === 'l' ? 'l' : ''}`}
                    onClick={() => pick(c.key)}>
                  {c.label}
                  {sort.key === c.key && <i className="dir">{sort.dir === 'asc' ? '▲' : '▼'}</i>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.users.map((u) => (
              /* the row is data; everything you can DO to an account is in the
                 panel, so a hundred accounts is a hundred rows and not four
                 hundred controls */
              <tr key={u.id} className={`rowlink ${sel?.id === u.id ? 'on' : ''}`}
                  onClick={() => setSel(u)}>
                <td className="name l">
                  {u.username}
                  {u.role === 'admin' && <span className="badge named">admin</span>}
                  {u.role === 'curator' && (
                    <span className="badge" title="Can decide what abilities are on the Abilities page. No site access, and no route to anyone's parses.">
                      curator
                    </span>
                  )}
                  {u.disabled_ts && <span className="badge">disabled</span>}
                  {!u.has_question && (
                    <span className="badge" title="No security question — only an admin reset can recover this account">
                      no reset
                    </span>
                  )}
                </td>
                <td>{u.character_count}</td>
                <td>{u.run_count}</td>
                <td>{u.session_count}</td>
                <td>{u.error_count ? <Link className="err" to="/admin/incidents">{u.error_count}</Link> : ''}</td>
                <td>{mb(u.stored_bytes)}</td>
                <td>{u.last_login_ts ? fmt.date(u.last_login_ts) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {d && d.total > PAGE_SIZE && (
        <div className="pager">
          <button className="chip" disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>‹ Prev</button>
          <span>{offset + 1}–{offset + d.users.length} of {d.total}</span>
          <button className="chip" disabled={offset + d.users.length >= d.total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}>Next ›</button>
        </div>
      )}
    </div></>
  )
}

function RegistrationSettings() {
  const [open, setOpen] = useState(null)
  const [message, setMessage] = useState(null)
  useEffect(() => { api.adminOverview().then((d) => setOpen(!!d.settings.registration_open)) }, [])
  if (open == null) return null
  return <div className="card slim contextualsetting"><div><h2>Registration</h2><p className="muted">Controls whether new accounts may be created.</p></div><button className={`chip ${open ? 'on' : ''}`} onClick={async () => { const next = !open; await api.adminSettings({ registration_open: next ? 1 : 0 }); setOpen(next); setMessage('Saved.') }}>{open ? 'Sign-ups open' : 'Sign-ups closed'}</button>{message && <span className="ok">{message}</span>}</div>
}

/* One account, and every admin action that touches it. These were four chips
   and two `prompt()` boxes on every row — a browser dialog is the wrong place
   to type a password, and the row is the wrong place to keep asking. */
function AccountPanel({ u, me, act, onClose }) {
  const [password, setPassword] = useState('')
  const [name, setName] = useState(u.username)
  const [caps, setCaps] = useState({ upload: toMb(u.upload_max_bytes), storage: toMb(u.storage_max_bytes) })
  const [pendingRole, setPendingRole] = useState(u.role)
  const [confirm, setConfirm] = useState(null)
  const self = u.id === me?.id
  useEffect(() => {
    setPassword(''); setName(u.username); setPendingRole(u.role); setConfirm(null)
    setCaps({ upload: toMb(u.upload_max_bytes), storage: toMb(u.storage_max_bytes) })
  }, [u.id, u.username, u.upload_max_bytes, u.storage_max_bytes])

  const capValue = (v) => (v.trim() === '' ? null : Math.max(0, Number(v) || 0) * (1 << 20))

  return (
    <div className="card confirmcard accountpanel">
      <div className="panelhead">
        <h3>{u.username}</h3>
        <span className="muted">
          {u.character_count} characters, {u.run_count} raids, {mb(u.stored_bytes)} stored
          {u.last_login_ts ? ` · last seen ${fmt.date(u.last_login_ts)}` : ''}
        </span>
        <button className="chip" onClick={onClose}>Close</button>
      </div>

      <div className="panelgrid">
        <div className="formcol">
          <label>
            New password (8+ characters)
            <input type="text" value={password} autoComplete="off"
                   onChange={(e) => setPassword(e.target.value)} />
          </label>
          {/* signed out everywhere, and there is no email to send it to */}
          <div className="row">
            <button className="chip" disabled={act.busy || password.length < 8}
                    onClick={() => confirm === 'password'
                      ? act.run(() => api.adminResetPassword(u.id, password), `Password reset — tell ${u.username} out of band.`)
                      : setConfirm('password')}>
              {confirm === 'password' ? 'Confirm: reset and sign out' : 'Reset password'}
            </button>
            <span className="muted">Signs them out everywhere.</span>
          </div>
        </div>

        <div className="formcol">
          <label>
            Username
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          {/* a relabel: characters, raids, groups and shares all point at the
              user id, so nothing moves and they stay signed in */}
          <div className="row">
            <button className="chip"
                    disabled={act.busy || !name.trim() || name.trim().toLowerCase() === u.username}
                    onClick={() => act.run(() => api.adminRenameUser(u.id, name.trim()),
                                           `Renamed to ${name.trim().toLowerCase()}.`)}>
              Rename
            </button>
            <span className="muted">3–20 letters, numbers or underscore.</span>
          </div>
        </div>

        <div className="formcol">
          <label>
            Upload cap, MB (blank = site default, 0 = unlimited)
            <input type="number" min="0" value={caps.upload}
                   onChange={(e) => setCaps({ ...caps, upload: e.target.value })} />
          </label>
          <label>
            Storage cap, MB
            <input type="number" min="0" value={caps.storage}
                   onChange={(e) => setCaps({ ...caps, storage: e.target.value })} />
          </label>
          <div className="row">
            <button className="chip" disabled={act.busy}
                    onClick={() => act.run(() => api.adminSetLimits(u.id, {
                      upload_max_bytes: capValue(caps.upload),
                      storage_max_bytes: capValue(caps.storage),
                    }), 'Limits saved.')}>
              Save limits
            </button>
          </div>
        </div>

        <div className="formcol">
          {/* Curator is a small key on purpose: it opens the Abilities console
              and nothing else. Handing out `admin` to get somebody who knows
              EQ2 deciding what a proc is would hand over accounts, storage and
              the audit log with it. */}
          <label>
            Role
            <select value={pendingRole} disabled={act.busy || self}
                    title="user — nothing. curator — the Abilities console. admin — everything here."
                    onChange={(e) => { setPendingRole(e.target.value); setConfirm(null) }}>
              <option value="user">user</option>
              <option value="curator">curator</option>
              <option value="admin">admin</option>
            </select>
          </label>
          {!self && pendingRole !== u.role && <><p className="muted">Curator permits Abilities and AoE timers. Admin also permits operations, accounts, settings, and activity.</p><button className="chip" onClick={() => confirm === 'role' ? act.run(() => api.adminSetRole(u.id, pendingRole), `${u.username} is now ${pendingRole}.`) : setConfirm('role')}>{confirm === 'role' ? `Confirm ${pendingRole} access` : 'Apply role change'}</button></>}
          {/* one misclick from having no admin at all, and no route back in */}
          {self && <span className="muted">You can't change your own role or sign yourself out.</span>}
          {!self && (
            <div className="row">
              <button className="chip" disabled={act.busy}
                      onClick={() => confirm === 'disabled'
                        ? act.run(() => api.adminSetDisabled(u.id, !u.disabled_ts), u.disabled_ts ? 'Account enabled.' : 'Account disabled and signed out.')
                        : setConfirm('disabled')}>
                {confirm === 'disabled' ? `Confirm: ${u.disabled_ts ? 'enable' : 'disable and sign out'}` : (u.disabled_ts ? 'Enable account' : 'Disable account')}
              </button>
              <span className="muted">Their data is untouched either way.</span>
            </div>
          )}
          <p className="muted small">Effective upload: {bytesOrOff(u.effective_upload_max_bytes)} ({u.upload_max_bytes_source}). Effective storage: {bytesOrOff(u.effective_storage_max_bytes)} ({u.storage_max_bytes_source}).</p>
        </div>
      </div>
    </div>
  )
}

/* ---------- Content: what is public, and what was deleted ---------- */

function ContentTab() {
  const [groups, setGroups] = useState(null)
  const refresh = useCallback(() => api.adminDeletedGroups().then((r) => setGroups(r.groups)), [])
  const { busy, error, msg, run, setError } = useAction(refresh)
  useEffect(() => { refresh().catch((e) => setError(e.message)) }, [refresh, setError])

  return (
    <>
      <div className="pagehead"><div><h1>Deleted groups</h1><p className="muted">Recover groups and their sharing state.</p></div></div>
      <Flash error={error} msg={msg} />
      <div className="card">
        <h2>Deleted groups</h2>
        {/* the rows were never removed, only stopped from counting, so a
            restore puts back members, invites, join code and shares as they
            were — the one support request a metadata-only admin can answer */}
        <p className="note">Restoring brings back members, invites, the join code and shares.</p>
        {groups?.length === 0 && <p className="muted">None deleted.</p>}
        {groups?.length > 0 && (
          <table className="data mid">
            <thead>
              <tr>
                <th className="l">Group</th>
                <th className="l">Owner</th>
                <th>Members</th>
                <th>Shares</th>
                <th>Deleted</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {groups.map((gr) => (
                <tr key={gr.id}>
                  <td className="name l">{gr.name}</td>
                  <td className="l muted">{gr.owner || '—'}</td>
                  <td>{gr.member_count}</td>
                  <td title="standing auto-shares + raids shared with this group">
                    {gr.auto_share_count + gr.run_share_count || '—'}
                  </td>
                  <td className="muted">{fmt.date(gr.deleted_ts)}</td>
                  <td>
                    <button className="chip" disabled={busy}
                            onClick={() => run(() => api.adminRestoreGroup(gr.id),
                                               `${gr.name} restored.`)}>
                      Restore
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}

/* ---------- Feedback: what people reported ---------- */

const FB_STATUS = [
  { key: '', label: 'All' },
  { key: 'open', label: 'Open' },
  { key: 'planned', label: 'Planned' },
  { key: 'closed', label: 'Closed' },
]
const FB_KIND = [
  { key: '', label: 'Everything' },
  { key: 'bug', label: 'Bugs' },
  { key: 'suggestion', label: 'Suggestions' },
]
const FB_PAGE = 50

function FeedbackTab() {
  const [params, setParams] = useSearchParams()
  const statusParam = params.get('status') ?? 'open'
  const status = statusParam === 'all' ? '' : statusParam
  const kind = params.get('kind') ?? ''
  const q = params.get('q') ?? ''
  const assignee = params.get('assignee') ?? ''
  const [shown, setShown] = useState(FB_PAGE)
  const [d, setD] = useState(null)
  const [confirm, setConfirm] = useState(null)

  const refresh = useCallback(
    () => api.adminFeedback({ status, kind, q, assignee, limit: shown }).then(setD),
    [status, kind, q, assignee, shown])
  const { busy, error, msg, run, setError } = useAction(refresh)
  useEffect(() => { refresh().catch((e) => setError(e.message)) }, [refresh, setError])
  // a new filter starts at the top of its own list, not where the last one got to
  const filterTo = (key, value) => { const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); setParams(next, { replace: true }); setShown(FB_PAGE) }

  return (
    <><div className="pagehead"><div><h1>Feedback</h1><p className="muted">Bug reports and suggestions awaiting triage.</p></div></div><div className="card">
      <div className="filterbar">
        {FB_STATUS.map((s) => (
          <button key={s.key} className={`chip ${(s.key || 'all') === statusParam ? 'on' : ''}`}
                  onClick={() => filterTo('status', s.key || 'all')}>{s.label}{d?.counts && ` ${d.counts[s.key] ?? d.total}`}</button>
        ))}
        <span className="spacer" />
        {FB_KIND.map((k) => (
          <button key={k.key} className={`chip ${kind === k.key ? 'on' : ''}`}
                  onClick={() => filterTo('kind', k.key)}>{k.label}</button>
        ))}
      </div>
      <div className="filterbar"><input value={q} onChange={(e) => filterTo('q', e.target.value)} placeholder="Search feedback…" /><select value={assignee} onChange={(e) => filterTo('assignee', e.target.value)}><option value="">Any assignee</option><option value="unassigned">Unassigned</option>{d?.admins.map((a) => <option key={a}>{a}</option>)}</select></div>

      <Flash error={error} msg={msg} />

      {d === null && <p className="muted">Loading…</p>}
      {d?.items.length === 0 && <p className="muted">Nothing here.</p>}
      {d?.items.length > 0 && (
        <table className="data">
          <thead>
            <tr>
              <th className="l">When</th>
              <th className="l">From</th>
              <th className="l">Kind</th>
              <th className="l">Page</th>
              <th className="l">What they said</th>
              <th className="l">Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {d.items.map((f) => (
              <tr key={f.id}>
                <td className="l muted">{fmt.date(f.created_ts)}</td>
                <td className="l">{f.username || '—'}</td>
                <td className="l">
                  <span className={`badge ${f.kind === 'bug' ? 'bad' : ''}`}>{f.kind}</span>
                </td>
                <td className="l muted">
                  {/* they were somewhere when they hit the button; going there
                      is usually the first thing you want to do */}
                  {f.page?.startsWith('/') ? <Link to={f.page}>{f.page}</Link> : (f.page || '—')}
                </td>
                <td className="l fbody">{f.body}</td>
                <td className="l">
                  <select className="chip" value={f.status} disabled={busy}
                          aria-label={`Status of feedback ${f.id}`}
                          onChange={(e) => run(
                            () => api.adminSetFeedbackStatus(f.id, e.target.value))}>
                    <option value="open">open</option>
                    <option value="planned">planned</option>
                    <option value="closed">closed</option>
                  </select>
                </td>
                <td>
                  <select className="chip" value={f.assignee || ''} onChange={(e) => run(() => api.adminUpdateFeedback(f.id, { assignee: e.target.value }))}><option value="">unassigned</option>{d.admins.map((a) => <option key={a}>{a}</option>)}</select>
                  <input defaultValue={f.admin_note || ''} placeholder="Admin note" onBlur={(e) => { if (e.target.value !== (f.admin_note || '')) run(() => api.adminUpdateFeedback(f.id, { admin_note: e.target.value })) }} />
                  <button className="chip" disabled={busy}
                          onClick={() => (confirm === f.id
                            ? run(() => api.adminDeleteFeedback(f.id), 'Deleted.')
                            : setConfirm(f.id))}
                          onBlur={() => setConfirm((c) => (c === f.id ? null : c))}>
                    {confirm === f.id ? 'Sure?' : 'Delete'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {d && d.items.length < d.total && (
        <div className="pager">
          <button className="chip" disabled={busy} onClick={() => setShown(shown + FB_PAGE)}>
            Load more
          </button>
          <span>{d.items.length} of {d.total}</span>
        </div>
      )}
    </div></>
  )
}

/* ---------- Audit: every admin action ---------- */

const AUDIT_PAGE = 100

/* Who came to look.

   A TIMELINE OF DAYS, WRITTEN OUT (the request: no chart). One row per day,
   newest first, and the columns are the whole question: how many distinct
   people, how many of them had no account, how many opened /chat, and how many
   page loads it all came to.

   THE ONE FIGURE THAT IS NOT HERE is "unique visitors this month". It cannot be
   computed and must not be faked: a visitor id belongs to ONE day by design
   (`backend/visitors.py` — the salt behind it is deleted two days later), so
   the same person on Tuesday and Friday is two rows with nothing in common.
   Summing the column gives visitor-DAYS, which is what the footer calls it. */
const SPANS = [7, 30, 90]

function VisitorsTab() {
  const [span, setSpan] = useState(30)
  const [d, setD] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setD(null)
    setError(null)
    api.adminVisitors(span).then(setD).catch((e) => setError(e.message))
  }, [span])

  const rows = d?.days ?? []

  return (
    <><div className="pagehead"><div><h1>Visitors</h1><p className="muted">Arrival trends measured in visitor-days.</p></div></div><div className="card">
      <div className="filterbar">
        <div className="spanpick">
          {SPANS.map((n) => (
            <button key={n} type="button"
                    className={`chip${span === n ? ' on' : ''}`}
                    onClick={() => setSpan(n)}>{n} days</button>
          ))}
        </div>
        {d && <span className="muted">since {d.since}</span>}
      </div>

      {error && <p className="err">{error}</p>}
      {d === null && !error && <p className="muted">Loading…</p>}
      {d && rows.length === 0 && (
        <p className="muted">
          Nobody counted yet. Counting starts when the site is next restarted on
          this build — nothing before it was ever written down.
        </p>
      )}
      {rows.length > 0 && (
        <>
          <table className="data mid">
            <thead>
              <tr>
                <th className="l">Day</th>
                <th>Visitors</th>
                <th>Signed out</th>
                <th>Opened /chat</th>
                <th>Page loads</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.day}>
                  <td className="l">{r.day}</td>
                  <td>{fmt.num(r.visitors)}</td>
                  <td>{fmt.num(r.anon)}</td>
                  <td>{fmt.num(r.chat)}</td>
                  <td className="muted">{fmt.num(r.hits)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">
            {fmt.num(d.totals.visitor_days)} visitor-days over{' '}
            {fmt.num(d.totals.days_counted)} days ·{' '}
            {fmt.num(d.totals.anon_days)} signed out ·{' '}
            {fmt.num(d.totals.chat_days)} opened /chat ·{' '}
            {fmt.num(d.totals.hits)} page loads. Visitor-days, not people: a
            visitor id is scoped to its day on purpose, so the same person on two
            days counts twice and cannot be matched up. Bots are filtered by
            user-agent, and a page load is somebody arriving — moving between
            tabs inside the app never touches the server.
          </p>
        </>
      )}
    </div></>
  )
}

function AuditTab() {
  const [shown, setShown] = useState(AUDIT_PAGE)
  const [filter, setFilter] = useState('')
  const [family, setFamily] = useState('')
  const [d, setD] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    const t = setTimeout(() => api.adminAudit({ limit: shown, q: filter, family }).then(setD).catch((e) => setError(e.message)), 200)
    return () => clearTimeout(t)
  }, [shown, filter, family])

  const rows = d?.entries ?? []

  return (
    <><div className="pagehead"><div><h1>Activity</h1><p className="muted">Immutable admin and curator changes.</p></div></div><div className="card">
      <div className="filterbar">
        <input type="text" placeholder="Find an action…" value={filter}
               onChange={(e) => setFilter(e.target.value)} />
        <select value={family} onChange={(e) => setFamily(e.target.value)}><option value="">Any action</option><option value="feedback">Feedback</option><option value="rule">Rulings</option><option value="retry">Retries</option><option value="set_">Account/settings changes</option></select>
        {d && <span className="muted">{d.total} entries</span>}
      </div>

      {error && <p className="err">{error}</p>}
      {d === null && !error && <p className="muted">Loading…</p>}
      {d && rows.length === 0 && <p className="muted">Nothing matches.</p>}
      {rows.length > 0 && (
        <table className="data mid">
          <thead>
            <tr>
              <th className="l">When</th>
              <th className="l">Who</th>
              <th className="l">Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id}>
                <td className="l muted">{fmt.date(e.ts)} {fmt.time(e.ts)}</td>
                <td className="l">{e.actor || 'system'}</td>
                <td className="l">{e.label}<details><summary>Raw detail</summary><code>{e.action} {e.target || ''} {e.detail || ''}</code></details></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {d && d.entries.length < d.total && (
        <div className="pager">
          <button className="chip" onClick={() => setShown(shown + AUDIT_PAGE)}>Load more</button>
          <span>{d.entries.length} of {d.total}</span>
        </div>
      )}
    </div></>
  )
}
