import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import Tabs from '../components/Tabs.jsx'
import { useQueryState } from '../lib/useQueryState.js'
import { api, fmt } from '../lib/api.js'

/* Running the site without reading it.

   Everything on this page is a count, a size, a status or a setting. There is
   no route from here into anybody's parses, and that is enforced in the backend
   (`security.py` keeps the admin role out of every visibility decision), not
   just left out of this UI.

   Tabs rather than one scroll: these are five unrelated jobs — is the site
   healthy, who has an account, what is published, what did somebody report,
   what did an admin do — and only one of them is ever the reason you opened
   the page. Each tab fetches its own data, so opening Admin is one request. */

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'accounts', label: 'Accounts' },
  { key: 'content', label: 'Content' },
  { key: 'feedback', label: 'Feedback' },
  { key: 'audit', label: 'Audit' },
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
  const [tabQ, setTabQ] = useQueryState('tab')
  const tab = TABS.some((t) => t.key === tabQ) ? tabQ : 'overview'

  return (
    <div className="manage">
      <div className="pagehead">
        <h1>Admin</h1>
        {/* The one console that edits GAME knowledge instead of site state, so
            it is a door rather than a tab: it has its own role (`curator`) and
            somebody who knows EQ2 should be able to work it without being
            handed accounts and storage too. */}
        <div className="actions">
          <Link className="btnlink" to="/admin/abilities">Abilities console →</Link>
        </div>
      </div>

      <Tabs tabs={TABS} value={tab}
            onChange={(k) => setTabQ(k === 'overview' ? null : k)} />

      {tab === 'overview' && <OverviewTab />}
      {tab === 'accounts' && <AccountsTab me={me} />}
      {tab === 'content' && <ContentTab />}
      {tab === 'feedback' && <FeedbackTab />}
      {tab === 'audit' && <AuditTab />}
    </div>
  )
}

/* ---------- Overview: is anything broken, and the site-wide knobs ---------- */

function OverviewTab() {
  const [d, setD] = useState(null)
  const [form, setForm] = useState(null)
  const refresh = useCallback(() => api.adminOverview().then((r) => {
    setD(r)
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

  return (
    <>
      <Flash error={error} msg={msg} />

      <div className="metrics">
        {Object.entries(d.counts).map(([k, v]) => (
          <div key={k} className="metric">
            <div className="v">{v}</div>
            <div className="k">{k.replace(/_/g, ' ')}</div>
          </div>
        ))}
        <div className="metric">
          <div className="v">{mb(d.storage.uploads_dir_bytes)}</div>
          <div className="k">on disk</div>
        </div>
      </div>

      {/* Only what somebody has to act on. A streaming plugin is the healthiest
          state a session has, and the old panel listed every one of them as a
          job needing attention — a 24-raider night read as 24 problems. */}
      <div className="card">
        <h2>Needs attention</h2>
        {alerts.length === 0 && <p className="muted">Nothing broken.</p>}
        {alerts.map((a) => (
          <div key={a.id} className="alertrow">
            <span className={`badge ${a.kind === 'error' ? 'bad' : ''}`}>
              {a.kind === 'error' ? 'error' : 'stuck'}
            </span>
            <span className="who">{a.username} / {a.character}</span>
            <span className="muted">
              session {a.id} ({a.source}), {a.kind === 'stuck'
                ? `parsing for ${fmt.dur(a.age_s)}`
                : fmt.date(a.created_ts)}
            </span>
            {a.error && <span className="err">{String(a.error).split('\n').pop()}</span>}
          </div>
        ))}
        {(live.receiving > 0 || live.parsing > 0) && (
          <p className="muted livenote">
            {live.receiving > 0 && `${live.receiving} live stream${live.receiving === 1 ? '' : 's'}`}
            {live.receiving > 0 && live.parsing > 0 && ', '}
            {live.parsing > 0 && `${live.parsing} parsing`}
            {' '}— working normally.
          </p>
        )}
      </div>

      <div className="card slim">
        <h2>Site settings</h2>
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
          <label className="checkrow">
            <input type="checkbox" checked={form.registration_open}
                   onChange={(e) => setForm({ ...form, registration_open: e.target.checked })} />
            Sign-ups open
          </label>
          <div className="row">
            <button disabled={busy} onClick={() => run(() => api.adminSettings({
              upload_max_bytes: Math.max(0, Number(form.upload_max_bytes) || 0) * (1 << 20),
              storage_max_bytes: Math.max(0, Number(form.storage_max_bytes) || 0) * (1 << 20),
              registration_open: form.registration_open ? 1 : 0,
            }), 'Settings saved.')}>
              Save
            </button>
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
    <div className="card">
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
                <td>{u.error_count ? <span className="err">{u.error_count}</span> : ''}</td>
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
    </div>
  )
}

/* One account, and every admin action that touches it. These were four chips
   and two `prompt()` boxes on every row — a browser dialog is the wrong place
   to type a password, and the row is the wrong place to keep asking. */
function AccountPanel({ u, me, act, onClose }) {
  const [password, setPassword] = useState('')
  const [name, setName] = useState(u.username)
  const [caps, setCaps] = useState({ upload: toMb(u.upload_max_bytes), storage: toMb(u.storage_max_bytes) })
  const self = u.id === me?.id
  useEffect(() => {
    setPassword(''); setName(u.username)
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
                    onClick={() => act.run(() => api.adminResetPassword(u.id, password),
                                           `Password reset — tell ${u.username} out of band.`)}>
              Reset password
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
            <select value={u.role} disabled={act.busy || self}
                    title="user — nothing. curator — the Abilities console. admin — everything here."
                    onChange={(e) => act.run(() => api.adminSetRole(u.id, e.target.value),
                                             `${u.username} is now ${e.target.value}.`)}>
              <option value="user">user</option>
              <option value="curator">curator</option>
              <option value="admin">admin</option>
            </select>
          </label>
          {/* one misclick from having no admin at all, and no route back in */}
          {self && <span className="muted">You can't change your own role or sign yourself out.</span>}
          {!self && (
            <div className="row">
              <button className="chip" disabled={act.busy}
                      onClick={() => act.run(() => api.adminSetDisabled(u.id, !u.disabled_ts),
                                             u.disabled_ts ? 'Account enabled.'
                                               : 'Account disabled and signed out.')}>
                {u.disabled_ts ? 'Enable account' : 'Disable account'}
              </button>
              <span className="muted">Their data is untouched either way.</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/* ---------- Content: what is public, and what was deleted ---------- */

function ContentTab() {
  const [runs, setRuns] = useState(null)
  const [groups, setGroups] = useState(null)
  const refresh = useCallback(() => Promise.all([
    api.adminPublicRuns().then((r) => setRuns(r.runs)),
    api.adminDeletedGroups().then((r) => setGroups(r.groups)),
  ]), [])
  const { busy, error, msg, run, setError } = useAction(refresh)
  useEffect(() => { refresh().catch((e) => setError(e.message)) }, [refresh, setError])

  return (
    <>
      <Flash error={error} msg={msg} />

      <div className="card">
        <h2>Published raids</h2>
        <p className="note">Readable by anyone on the internet, without an account.</p>
        {runs?.length === 0 && <p className="muted">None published.</p>}
        {runs?.length > 0 && (
          <table className="data slim">
            <thead>
              <tr>
                <th className="l">Zone</th>
                <th>Date</th>
                <th>Raiders</th>
                <th className="l">Published by</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.zone_run_id}>
                  <td className="name l">
                    {r.mine
                      ? <Link to={`/zones/${r.zone_run_id}`}>{r.zone || 'Unknown zone'}</Link>
                      : (r.zone || 'Unknown zone')}
                  </td>
                  <td>{fmt.date(r.started_ts)}</td>
                  <td>{r.raider_count || '—'}</td>
                  <td className="l muted">{r.publisher}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

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
  const [status, setStatus] = useState('open')
  const [kind, setKind] = useState('')
  const [shown, setShown] = useState(FB_PAGE)
  const [d, setD] = useState(null)
  const [confirm, setConfirm] = useState(null)

  const refresh = useCallback(
    () => api.adminFeedback({ status, kind, limit: shown }).then(setD),
    [status, kind, shown])
  const { busy, error, msg, run, setError } = useAction(refresh)
  useEffect(() => { refresh().catch((e) => setError(e.message)) }, [refresh, setError])
  // a new filter starts at the top of its own list, not where the last one got to
  const filterTo = (fn) => { fn(); setShown(FB_PAGE) }

  return (
    <div className="card">
      <div className="filterbar">
        {FB_STATUS.map((s) => (
          <button key={s.key} className={`chip ${status === s.key ? 'on' : ''}`}
                  onClick={() => filterTo(() => setStatus(s.key))}>{s.label}</button>
        ))}
        <span className="spacer" />
        {FB_KIND.map((k) => (
          <button key={k.key} className={`chip ${kind === k.key ? 'on' : ''}`}
                  onClick={() => filterTo(() => setKind(k.key))}>{k.label}</button>
        ))}
      </div>

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
    </div>
  )
}

/* ---------- Audit: every admin action ---------- */

const AUDIT_PAGE = 100

function AuditTab() {
  const [shown, setShown] = useState(AUDIT_PAGE)
  const [filter, setFilter] = useState('')
  const [d, setD] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    api.adminAudit({ limit: shown }).then(setD).catch((e) => setError(e.message))
  }, [shown])

  const needle = filter.trim().toLowerCase()
  const rows = (d?.entries ?? []).filter((e) => !needle
    || `${e.actor || 'system'} ${e.action} ${e.target || ''} ${e.detail || ''}`
      .toLowerCase().includes(needle))

  return (
    <div className="card">
      <div className="filterbar">
        <input type="text" placeholder="Find an action…" value={filter}
               onChange={(e) => setFilter(e.target.value)} />
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
                <td className="l muted">{e.action} {e.target || ''} {e.detail || ''}</td>
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
    </div>
  )
}
