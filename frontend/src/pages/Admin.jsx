import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'
import AdminShell from '../components/AdminShell.jsx'

/* Running the site without reading it.

   Everything on this page is a count, a size, a status or a setting. There is
   no route from here into anybody's parses, and that is enforced in the backend
   (`security.py` keeps the admin role out of every visibility decision), not
   just left out of this UI.

   The shell exposes four durable destinations. Rare recovery, analytics and
   audit tools live under Utilities instead of competing with daily work. */

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
  if (pathname === '/admin') view = <OverviewTab me={me} />
  else if (pathname === '/admin/work') view = <WorkTab />
  else if (pathname === '/admin/incidents') view = <IncidentsTab />
  else if (pathname === '/admin/visitors') view = <VisitorsTab />
  else if (pathname === '/admin/accounts') view = <AccountsTab me={me} />
  else if (pathname === '/admin/groups') view = <ContentTab />
  else if (pathname === '/admin/feedback') view = <FeedbackTab />
  else if (pathname === '/admin/activity') view = <AuditTab />

  return <AdminShell user={me}>{view}</AdminShell>
}

/* ---------- Overview: is anything broken, and the site-wide knobs ---------- */

function OverviewTab({ me }) {
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
  const stuck = alerts.filter((a) => a.kind === 'stuck')
  const security = d.dashboard.status.security
  const openWork = stuck.length + d.dashboard.actions.feedback_open
  const healthy = stuck.length === 0 && security.blocked_buckets === 0

  return (
    <>
      <div className="adminpagehead">
        <div><p className="adminkicker">Control center</p><h1>Overview</h1></div>
        <div className={`adminstate ${healthy ? 'good' : 'bad'}`}>
          <i /> <span><b>{healthy ? 'Operating normally' : 'Attention needed'}</b><small>{openWork} open item{openWork === 1 ? '' : 's'}</small></span>
        </div>
      </div>
      <Flash error={error} msg={msg} />

      <div className="adminhealth" aria-label="Operational status">
        <div>
          <small>Storage growth</small><b>{mb(d.dashboard.usage.storage_growth_bytes)}</b><span>{mb(d.storage.uploads_dir_bytes)} stored now · 30d growth</span>
        </div>
        <Link to="/admin/incidents?type=stuck" className={stuck.length ? 'warn' : ''}>
          <small>Stuck processing</small><b>{stuck.length ? `${stuck.length} stuck` : 'None'}</b><span>{live.parsing} parsing normally</span>
        </Link>
        <div className={security.blocked_buckets ? 'warn' : ''}>
          <small>Sign-in security · 15m</small><b>{security.failed_attempts ? `${security.failed_attempts} failed` : 'Quiet'}</b><span>{security.blocked_buckets} blocked limiter bucket{security.blocked_buckets === 1 ? '' : 's'}</span>
        </div>
        <a href="#accounts">
          <small>Accounts</small><b>{d.counts.users}</b><span>{d.dashboard.usage.active_accounts} active · 30d</span>
        </a>
      </div>

      <div id="accounts" className="adminoverviewaccounts">
        <AccountsTab me={me} embedded registrationOpen={d.settings.registration_open} />
      </div>

      <div className="admincockpit">
        <section className={`adminpanel adminqueue${openWork ? '' : ' clear'}`}>
          <div className="adminpanelhead"><div><h2>Needs attention</h2><span>{openWork ? `${openWork} items to review` : 'Your queue is clear'}</span></div>{openWork > 0 && <Link to="/admin/work">Review all</Link>}</div>
          {openWork === 0 && <div className="adminempty"><i>✓</i><div><b>Nothing needs action</b><span>Healthy activity stays in the status bar above.</span></div></div>}
          {stuck.slice(0, 4).map((a) => <div className="adminworkrow" key={a.id}><span className="workkind">Processing</span><div><b>Session {a.id} has stopped progressing</b><small>{a.username} · stuck for {fmt.dur(a.age_s)}</small></div><Link to="/admin/incidents?type=stuck">Inspect</Link></div>)}
          {d.dashboard.actions.feedback_open > 0 && <div className="adminworkrow"><span className="workkind">Feedback</span><div><b>{d.dashboard.actions.feedback_open} awaiting triage</b><small>Reports and suggestions</small></div><Link to="/admin/feedback?status=open">Review</Link></div>}
        </section>

        <aside className="adminaside">
          <section className="adminpanel">
            <div className="adminpanelhead"><div><h2>Last 30 days</h2><span>Usage and growth</span></div><Link to="/admin/visitors">Details</Link></div>
            <dl className="adminmetrics">
              {/* Browsers first and counted beneath it: the second number is
                  every user-agent that got past the bot filter, and on this
                  site most of that has been automated (v51). Showing only it
                  read as growth that was not there. */}
              <div><dt>Browser-days</dt><dd>{d.dashboard.usage.browser_days}</dd></div>
              <div><dt>Counted, bots included</dt><dd>{d.dashboard.usage.visitor_days}</dd></div>
              <div><dt>Uploads</dt><dd>{d.dashboard.usage.uploads}</dd></div>
              <div><dt>Completed raids</dt><dd>{d.dashboard.usage.completed_raids}</dd></div>
              <div><dt>Active accounts</dt><dd>{d.dashboard.usage.active_accounts}</dd></div>
            </dl>
            <details className="admindisclosure"><summary>All-time totals</summary><p>{d.counts.users} accounts · {d.counts.sessions} logs · {d.counts.zone_runs} raids · {d.counts.encounters} encounters · {d.counts.public_runs} published</p></details>
          </section>

          <section className="adminpanel">
            <div className="adminpanelhead"><div><h2>Recent changes</h2><span>Administrative activity</span></div><Link to="/admin/activity">View log</Link></div>
            <div className="adminchanges">{d.dashboard.recent_changes.slice(0, 4).map((e) => <div key={e.id}><span>{e.label}</span><time>{fmt.date(e.ts)}</time></div>)}{d.dashboard.recent_changes.length === 0 && <p className="muted">No recent changes.</p>}</div>
          </section>

          <details className="adminpanel adminsettings">
            <summary><span><b>Storage limits</b><small>{bytesOrOff(d.settings.upload_max_bytes)} upload · {bytesOrOff(d.settings.storage_max_bytes)} per account</small></span><i>Configure</i></summary>
            <div className="formcol">
              <label>Max upload, MB (0 = unlimited)<input type="number" min="0" value={form.upload_max_bytes} onChange={(e) => setForm({ ...form, upload_max_bytes: e.target.value })} /></label>
              <label>Max stored per account, MB<input type="number" min="0" value={form.storage_max_bytes} onChange={(e) => setForm({ ...form, storage_max_bytes: e.target.value })} /></label>
              <div className="row"><button disabled={busy || !dirty} onClick={() => run(() => api.adminSettings({ upload_max_bytes: Math.max(0, Number(form.upload_max_bytes) || 0) * (1 << 20), storage_max_bytes: Math.max(0, Number(form.storage_max_bytes) || 0) * (1 << 20) }), 'Settings saved.')}>Save limits</button>{dirty && <span className="muted">Unsaved</span>}</div>
            </div>
          </details>
        </aside>
      </div>
    </>
  )
}

function IncidentRows({ alerts, compact = false }) {
  const [busy, setBusy] = useState(null)
  const [results, setResults] = useState({})
  const [notes, setNotes] = useState({})
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
      {a.retryable && !compact && <span className="muted">Stored source is retained for engineering recovery.</span>}
      {!a.retryable && !compact && <span className="muted">{a.support_instruction}</span>}
      {!compact && !a.retryable && <><input value={notes[a.id] || ''} onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })} placeholder="Acknowledgement note" /><button className="chip" disabled={busy === a.id || !notes[a.id]?.trim()} onClick={() => acknowledge(a.id)}>Acknowledge</button></>}
      {results[a.id] && <span className="note">{results[a.id]}</span>}
    </div>
  ))
}

function WorkTab() {
  const [d, setD] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    Promise.all([
      api.adminIncidents({ state: 'open' }),
      api.adminFeedback({ status: 'open', limit: 8 }),
    ]).then(([incidents, feedback]) => setD({ incidents: incidents.items.filter((a) => a.kind === 'stuck'), feedback }))
      .catch((e) => setError(e.message))
  }, [])

  const total = (d?.incidents.length ?? 0) + (d?.feedback.total ?? 0)
  return (
    <>
      <div className="adminpagehead compact">
        <div><p className="adminkicker">Low-priority utility</p><h1>Review and diagnostics</h1><p>Stuck processing and messages that may need a decision.</p></div>
        {d && <span className="admincount">{total} open</span>}
      </div>
      {error && <p className="err">{error}</p>}
      {!d && !error && <p className="muted">Loading…</p>}
      {d && (
        <div className="adminworkgrid">
          <section className="adminpanel">
            <div className="adminpanelhead"><div><h2>Stuck processing</h2><span>{d.incidents.length ? 'Running longer than ten minutes' : 'Nothing stuck'}</span></div><Link to="/admin/incidents">Technical history</Link></div>
            {d.incidents.length ? <IncidentRows alerts={d.incidents.slice(0, 8)} compact /> : <div className="adminempty small"><i>✓</i><div><b>All clear</b><span>No parse work needs intervention.</span></div></div>}
          </section>
          <section className="adminpanel">
            <div className="adminpanelhead"><div><h2>Feedback inbox</h2><span>{d.feedback.total} open report{d.feedback.total === 1 ? '' : 's'}</span></div><Link to="/admin/feedback?status=open">Open inbox</Link></div>
            <div className="feedbackpreview">
              {d.feedback.items.map((f) => <Link key={f.id} to="/admin/feedback?status=open"><span className={`badge ${f.kind === 'bug' ? 'bad' : ''}`}>{f.kind}</span><div><b>{f.body}</b><small>{f.username || 'Anonymous'} · {fmt.date(f.created_ts)}</small></div><i>›</i></Link>)}
              {d.feedback.items.length === 0 && <div className="adminempty small"><i>✓</i><div><b>Inbox zero</b><span>No feedback is waiting.</span></div></div>}
            </div>
          </section>
        </div>
      )}
    </>
  )
}

function IncidentsTab() {
  const [d, setD] = useState(null)
  const [error, setError] = useState(null)
  const [state, setState] = useState('open')
  useEffect(() => { api.adminIncidents({ state }).then((r) => setD({ alerts: r.items })).catch((e) => setError(e.message)) }, [state])
  return (
    <>
      <div className="adminpagehead compact"><div><p className="adminkicker">Diagnostics</p><h1>Processing history</h1><p>Stuck and failed parses for engineering diagnosis. Routine retries are not an admin task.</p></div>{d && <span className="admincount">{d.alerts.length} shown</span>}</div>
      <div className="filterbar"><button className={`chip ${state === 'open' ? 'on' : ''}`} onClick={() => setState('open')}>Open</button><button className={`chip ${state === 'acknowledged' ? 'on' : ''}`} onClick={() => setState('acknowledged')}>Acknowledged</button><button className={`chip ${state === 'all' ? 'on' : ''}`} onClick={() => setState('all')}>All active</button></div>
      {error && <p className="err">{error}</p>}
      {!d && !error && <p className="muted">Loading…</p>}
      {d?.alerts.length === 0 && <div className="adminpanel"><h2>All clear</h2><p className="muted">No open incidents.</p></div>}
      {d?.alerts.length > 0 && <div className="adminpanel incidentlist"><IncidentRows alerts={d.alerts} /></div>}
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

function AccountsTab({ me, embedded = false, registrationOpen = null }) {
  const pageSize = embedded ? 8 : PAGE_SIZE
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
    { q, sort: sort.key, dir: sort.dir, limit: pageSize, offset },
  ).then((r) => {
    setD(r)
    // the panel is showing a row that was just refetched; keep it in step
    setSel((s) => (s ? r.users.find((u) => u.id === s.id) ?? s : s))
  }), [q, sort, offset, pageSize])
  const act = useAction(refresh)
  useEffect(() => { refresh().catch((e) => act.setError(e.message)) }, [refresh]) // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (key) => setSort((s) => {
    setOffset(0)
    // numbers are interesting from the top; names from the front
    return s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
      : { key, dir: key === 'username' ? 'asc' : 'desc' }
  })

  return (
    <>{!embedded && <div className="adminpagehead compact"><div><p className="adminkicker">People</p><h1>Accounts</h1><p>Identity, access, limits, and recovery.</p></div><RegistrationSettings initial={registrationOpen} /></div>}
    <div className={`accountworkspace${sel ? ' open' : ''}`}>
    <section className="adminpanel accountlist">
      {embedded && <div className="adminpanelhead"><div><h2>Accounts</h2><span>Access, limits, and recovery</span></div><RegistrationSettings initial={registrationOpen} /></div>}
      <div className="accounttoolbar">
        <input type="text" placeholder="Find an account…" value={typed}
               onChange={(e) => setTyped(e.target.value)} />
        {d && !embedded && <span className="muted">{d.total} account{d.total === 1 ? '' : 's'}</span>}
      </div>

      <Flash error={act.error} msg={act.msg} />

      {d === null && <p className="muted">Loading…</p>}
      {d?.users.length === 0 && <p className="muted">No account matches that.</p>}
      {d?.users.length > 0 && (
        <table className="data">
          <thead>
            <tr>
              {USER_COLS.map((c) => (
                <th key={c.key} className={`sort col-${c.key} ${c.align === 'l' ? 'l' : ''}`}
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
              <tr key={u.id} className={`accountrow ${sel?.id === u.id ? 'on' : ''}`}
                  onClick={() => setSel(u)}>
                <td className="name l col-username">
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
                <td className="col-character_count">{u.character_count}</td>
                <td className="col-run_count">{u.run_count}</td>
                <td className="col-session_count">{u.session_count}</td>
                <td className="col-error_count">{u.error_count ? <Link className="err" to="/admin/incidents">{u.error_count}</Link> : ''}</td>
                <td className="col-stored_bytes">{mb(u.stored_bytes)}</td>
                <td className="col-last_login_ts">{u.last_login_ts ? fmt.date(u.last_login_ts) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {d && d.total > pageSize && (embedded ? (
        <div className="pager"><Link to="/admin/accounts">View all {d.total} accounts</Link></div>
      ) : (
        <div className="pager">
          <button className="chip" disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - pageSize))}>‹ Prev</button>
          <span>{offset + 1}–{offset + d.users.length} of {d.total}</span>
          <button className="chip" disabled={offset + d.users.length >= d.total}
                  onClick={() => setOffset(offset + pageSize)}>Next ›</button>
        </div>
      ))}
    </section>
    {sel && <AccountPanel u={sel} me={me} act={act} onClose={() => setSel(null)} />}
    </div></>
  )
}

function RegistrationSettings({ initial = null }) {
  const [open, setOpen] = useState(initial == null ? null : !!initial)
  const [message, setMessage] = useState(null)
  useEffect(() => { if (initial == null) api.adminOverview().then((d) => setOpen(!!d.settings.registration_open)) }, [initial])
  if (open == null) return null
  return <div className="registrationsetting"><span><small>Registration</small><b>{open ? 'Open' : 'Closed'}</b></span><button className={`switch ${open ? 'on' : ''}`} aria-label="Toggle registration" aria-pressed={open} onClick={async () => { const next = !open; await api.adminSettings({ registration_open: next ? 1 : 0 }); setOpen(next); setMessage('Saved.') }}><i /></button>{message && <span className="ok">{message}</span>}</div>
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
    <aside className="adminpanel confirmcard accountpanel">
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
    </aside>
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
      <div className="adminpagehead compact"><div><p className="adminkicker">Utility</p><h1>Deleted groups</h1><p>Recover groups and their sharing state.</p></div></div>
      <Flash error={error} msg={msg} />
      <div className="adminpanel">
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
  const [selected, setSelected] = useState(null)
  const [confirm, setConfirm] = useState(null)

  const refresh = useCallback(
    () => api.adminFeedback({ status, kind, q, assignee, limit: shown }).then((next) => {
      setD(next)
      setSelected((current) => next.items.find((f) => f.id === current?.id) || next.items[0] || null)
    }),
    [status, kind, q, assignee, shown])
  const { busy, error, msg, run, setError } = useAction(refresh)
  useEffect(() => { refresh().catch((e) => setError(e.message)) }, [refresh, setError])
  // a new filter starts at the top of its own list, not where the last one got to
  const filterTo = (key, value) => { const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); setParams(next, { replace: true }); setShown(FB_PAGE) }

  return (
    <><div className="adminpagehead compact"><div><p className="adminkicker">Work queue</p><h1>Feedback inbox</h1><p>Read, assign, and resolve one report at a time.</p></div>{d && <span className="admincount">{d.total} matching</span>}</div>
      <div className="feedbacktoolbar">
        <input value={q} onChange={(e) => filterTo('q', e.target.value)} placeholder="Search feedback…" />
        <label>Status<select value={statusParam} onChange={(e) => filterTo('status', e.target.value)}>{FB_STATUS.map((s) => <option key={s.key} value={s.key || 'all'}>{s.label}{d?.counts && ` · ${d.counts[s.key] ?? d.total}`}</option>)}</select></label>
        <label>Type<select value={kind} onChange={(e) => filterTo('kind', e.target.value)}>{FB_KIND.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}</select></label>
        <label>Owner<select value={assignee} onChange={(e) => filterTo('assignee', e.target.value)}><option value="">Anyone</option><option value="unassigned">Unassigned</option>{d?.admins.map((a) => <option key={a}>{a}</option>)}</select></label>
      </div>

      <Flash error={error} msg={msg} />

      {d === null && <p className="muted">Loading…</p>}
      {d?.items.length === 0 && <p className="muted">Nothing here.</p>}
      {d?.items.length > 0 && (
        <div className="feedbackworkspace">
          <div className="feedbackmaster">
            {d.items.map((f) => (
              <button key={f.id} className={selected?.id === f.id ? 'on' : ''} onClick={() => { setSelected(f); setConfirm(null) }}>
                <span><b>{f.body}</b><small>{f.username || 'Anonymous'} · {fmt.date(f.created_ts)}</small></span>
                <span><i className={`badge ${f.kind === 'bug' ? 'bad' : ''}`}>{f.kind}</i><small>{f.status}</small></span>
              </button>
            ))}
          </div>
          {selected && <aside className="feedbackdetail" key={selected.id}>
            <div className="feedbackdetailhead"><span className={`badge ${selected.kind === 'bug' ? 'bad' : ''}`}>{selected.kind}</span><time>{fmt.date(selected.created_ts)} {fmt.time(selected.created_ts)}</time></div>
            <h2>{selected.body}</h2>
            <p className="muted">From {selected.username || 'Anonymous'}{selected.page ? <> · {selected.page.startsWith('/') ? <Link to={selected.page}>{selected.page}</Link> : selected.page}</> : ''}</p>
            <div className="feedbackfields formcol">
              <label>Status<select value={selected.status} disabled={busy} onChange={(e) => run(() => api.adminSetFeedbackStatus(selected.id, e.target.value))}><option value="open">Open</option><option value="planned">Planned</option><option value="closed">Closed</option></select></label>
              <label>Assignee<select value={selected.assignee || ''} onChange={(e) => run(() => api.adminUpdateFeedback(selected.id, { assignee: e.target.value }))}><option value="">Unassigned</option>{d.admins.map((a) => <option key={a}>{a}</option>)}</select></label>
              <label>Private admin note<textarea defaultValue={selected.admin_note || ''} placeholder="Context for the next admin…" onBlur={(e) => { if (e.target.value !== (selected.admin_note || '')) run(() => api.adminUpdateFeedback(selected.id, { admin_note: e.target.value })) }} /></label>
            </div>
            <div className="feedbackactions"><span className="muted">Closing keeps the report in history.</span><button className="chip danger" disabled={busy} onClick={() => (confirm === selected.id ? run(() => api.adminDeleteFeedback(selected.id), 'Deleted.') : setConfirm(selected.id))} onBlur={() => setConfirm(null)}>{confirm === selected.id ? 'Confirm delete' : 'Delete permanently'}</button></div>
          </aside>}
        </div>
      )}

      {d && d.items.length < d.total && (
        <div className="pager">
          <button className="chip" disabled={busy} onClick={() => setShown(shown + FB_PAGE)}>
            Load more
          </button>
          <span>{d.items.length} of {d.total}</span>
        </div>
      )}
    </>
  )
}

/* ---------- Audit: every admin action ---------- */

const AUDIT_PAGE = 100

/* Who came to look, where they went, and when.

   A TIMELINE OF DAYS, WRITTEN OUT (the request: no chart). One row per day,
   newest first, and the columns are the whole question: how many distinct
   people, how many of those were real browsers, how many had no account, how
   many opened /chat, and how many page loads it all came to.

   READ `Browsers`, NOT `Counted` (v51). `Counted` is every visitor that got
   past the user-agent filter, and a user-agent is a string a crawler can set
   to anything — on this site most of that column was automated. `Browsers` is
   the ones that ran the app's beacon, which a scraper does not. The wide gap
   between the two columns is not a bug in either; it is the honest size of the
   bot traffic, and it was there before it was visible.

   THE ONE FIGURE THAT IS NOT HERE is "unique visitors this month". It cannot be
   computed and must not be faked: a visitor id belongs to ONE day by design
   (`backend/visitors.py` — the salt behind it is deleted two days later), so
   the same person on Tuesday and Friday is two rows with nothing in common.
   Summing the column gives visitor-DAYS, which is what the footer calls it.

   WHERE and WHEN are VIEW counts and never people counts, and the tables say
   so rather than leaving it to be assumed. `visit_paths` has no visitor column
   at all, so two hundred Planner views may be four raiders on a Tuesday and
   there is deliberately no way to tell from here. */
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
  const dest = d?.destinations?.routes ?? []
  const hours = d?.arrivals?.hours ?? []

  return (
    <><div className="adminpagehead compact"><div><p className="adminkicker">Analytics</p><h1>Visitors</h1><p>Arrival trends measured in visitor-days.</p></div></div><div className="adminpanel">
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
                <th title="Ran the app's beacon, so a browser rendered the page">
                  Browsers
                </th>
                <th title="Everything that got past the user-agent filter — crawlers included">
                  Counted
                </th>
                <th>Signed out</th>
                <th>Opened /chat</th>
                <th>Page loads</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.day}>
                  <td className="l">{r.day}</td>
                  <td>{fmt.num(r.browsers)}</td>
                  <td className="muted">{fmt.num(r.visitors)}</td>
                  <td className="muted">{fmt.num(r.anon)}</td>
                  <td className="muted">{fmt.num(r.chat)}</td>
                  <td className="muted">{fmt.num(r.hits)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">
            {fmt.num(d.totals.browser_days)} browser-days of{' '}
            {fmt.num(d.totals.visitor_days)} counted, over{' '}
            {fmt.num(d.totals.days_counted)} days ·{' '}
            {fmt.num(d.totals.anon_days)} signed out ·{' '}
            {fmt.num(d.totals.chat_days)} opened /chat ·{' '}
            {fmt.num(d.totals.hits)} page loads. Visitor-days, not people: a
            visitor id is scoped to its day on purpose, so the same person on two
            days counts twice and cannot be matched up. `Counted` is filtered by
            user-agent only, which anything can set; `Browsers` ran the app's
            beacon, which is the closest thing here to proof of a person. Days
            before this build have no beacon to have run, so their Browsers
            column reads 0 and means "never asked".
          </p>
        </>
      )}

      {/* WHERE. Views, not people — see the tab comment. `Entries` is the route
          a visit started on, and the gap between the two columns is the useful
          part: entries with no further views is a link people bounce off, and
          views far above entries is where the app sends people once they are
          inside it. */}
      {dest.length > 0 && (
        <>
          <div className="adminpanelhead section"><div><h2>Where they went</h2>
            <span>Route views, not people</span></div></div>
          <table className="data mid">
            <thead>
              <tr>
                <th className="l">Route</th>
                <th title="Every time the route was opened, in-app moves included">
                  Views
                </th>
                <th title="Views that were the first route of a visit">Entries</th>
              </tr>
            </thead>
            <tbody>
              {dest.map((r) => (
                <tr key={r.route}>
                  <td className="l">{r.route}</td>
                  <td>{fmt.num(r.views)}</td>
                  <td className="muted">{fmt.num(r.entries)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">
            {fmt.num(d.destinations.totals.views)} views ·{' '}
            {fmt.num(d.destinations.totals.entries)} entries. Route patterns, not
            URLs: `/zones/:id` is every zone run and the id is never stored.
            Anything outside the app's own routes — scanners looking for
            WordPress, mostly — is one `(other)` row. These are counts of page
            views with no visitor attached to them at all, so they cannot be
            crossed with the days above to follow anybody.
          </p>
        </>
      )}

      {/* WHEN. The server's clock, every hour present including the empty ones:
          a reader is comparing the shape of a day, and a missing 4am is not the
          same as a quiet one. */}
      {hours.some((h) => h.views > 0) && (
        <>
          <div className="adminpanelhead section"><div><h2>When they came</h2>
            <span>The server's hours</span></div></div>
          <table className="data mid">
            <thead>
              <tr>
                <th className="l">Hour</th><th>Views</th><th>Entries</th>
              </tr>
            </thead>
            <tbody>
              {hours.map((h) => (
                <tr key={h.hour}>
                  <td className="l">{String(h.hour).padStart(2, '0')}:00</td>
                  <td>{fmt.num(h.views)}</td>
                  <td className="muted">{fmt.num(h.entries)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">
            The server's hours, not each reader's — this is when the site is
            busy in local terms, and says nothing about where anybody is.
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
    <><div className="adminpagehead compact"><div><p className="adminkicker">History</p><h1>Activity log</h1><p>Immutable admin and curator changes.</p></div></div><div className="adminpanel">
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
