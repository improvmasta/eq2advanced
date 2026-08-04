import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import SortableTable from '../components/SortableTable.jsx'
import { api, fmt } from '../lib/api.js'

/* Running the site without reading it.

   Everything on this page is a count, a size, a status or a setting. There is
   no route from here into anybody's parses, and that is enforced in the backend
   (`security.py` keeps the admin role out of every visibility decision), not
   just left out of this UI. The page says so, because an admin who assumes they
   can see everything will manage the site as if they can. */

const mb = (n) => (n == null ? '—' : `${(n / (1 << 20)).toFixed(1)} MB`)
const bytesOrOff = (n) => (n ? mb(n) : 'unlimited')

export default function Admin() {
  const [overview, setOverview] = useState(null)
  const [users, setUsers] = useState(null)
  const [audit, setAudit] = useState(null)
  const [publicRuns, setPublicRuns] = useState(null)
  const [deletedGroups, setDeletedGroups] = useState(null)
  const [error, setError] = useState(null)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const [uploadMb, setUploadMb] = useState('')

  const refresh = useCallback(() => {
    api.adminOverview().then((d) => {
      setOverview(d)
      setUploadMb(String(Math.round((d.settings.upload_max_bytes || 0) / (1 << 20))))
    }).catch((e) => setError(e.message))
    api.adminUsers().then((d) => setUsers(d.users)).catch(() => {})
    api.adminAudit().then((d) => setAudit(d.entries)).catch(() => {})
    api.adminPublicRuns().then((d) => setPublicRuns(d.runs)).catch(() => {})
    api.adminDeletedGroups().then((d) => setDeletedGroups(d.groups)).catch(() => {})
  }, [])
  useEffect(() => { refresh() }, [refresh])

  async function run(fn, note) {
    setBusy(true); setError(null); setMsg(null)
    try { await fn(); if (note) setMsg(note); refresh() }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const userColumns = [
    { key: 'username', label: 'User', align: 'l',
      render: (u) => (
        <span>
          {u.username}
          {u.role === 'admin' && <span className="badge named">admin</span>}
          {u.disabled_ts && <span className="badge">disabled</span>}
          {!u.has_question && <span className="badge" title="No security question — only an admin reset can recover this account">no reset</span>}
        </span>
      ) },
    { key: 'character_count', label: 'Chars' },
    { key: 'run_count', label: 'Raids' },
    { key: 'session_count', label: 'Logs' },
    { key: 'error_count', label: 'Errors',
      render: (u) => (u.error_count ? <span className="err">{u.error_count}</span> : '') },
    { key: 'stored_bytes', label: 'Stored', render: (u) => mb(u.stored_bytes) },
    { key: 'upload_max_bytes', label: 'Upload cap',
      render: (u) => (u.upload_max_bytes == null ? <span className="muted">site</span>
        : bytesOrOff(u.upload_max_bytes)) },
    { key: 'last_login_ts', label: 'Last seen',
      render: (u) => (u.last_login_ts ? fmt.date(u.last_login_ts) : '—') },
    { key: 'act', label: '', sortable: false, align: 'l',
      render: (u) => (
        <span className="row" style={{ gap: 4 }}>
          <button className="chip" disabled={busy}
                  onClick={() => run(() => api.adminSetDisabled(u.id, !u.disabled_ts),
                                     u.disabled_ts ? 'Account enabled.' : 'Account disabled and signed out.')}>
            {u.disabled_ts ? 'Enable' : 'Disable'}
          </button>
          <button className="chip" disabled={busy}
                  onClick={() => {
                    const pw = prompt(`New password for ${u.username} (8+ characters). `
                      + 'They will be signed out everywhere; tell them out of band.')
                    if (pw) run(() => api.adminResetPassword(u.id, pw), 'Password reset.')
                  }}>
            Reset password
          </button>
          {/* a relabel: everything points at the user id, so nothing moves */}
          <button className="chip" disabled={busy}
                  onClick={() => {
                    const name = prompt(
                      `New username for ${u.username} — 3-20 letters, numbers or `
                      + 'underscore. Usernames are lower case; they stay signed in.',
                      u.username)
                    if (name && name.trim().toLowerCase() !== u.username) {
                      run(() => api.adminRenameUser(u.id, name.trim()),
                          `Renamed to ${name.trim().toLowerCase()}.`)
                    }
                  }}>
            Rename
          </button>
        </span>
      ) },
  ]

  return (
    <div className="manage">
      <div className="pagehead">
        <h1>Admin</h1>
        <span className="sub">Users, storage and job health</span>
      </div>

      <p className="note">
        <strong>No parse data is reachable from here.</strong> Storage and parse
        status only — to read someone's raid, ask them to share it.
      </p>

      {error && <p className="err">{error}</p>}
      {msg && <p className="note flash">{msg}</p>}

      {overview && (
        <>
          <div className="metrics">
            {Object.entries(overview.counts).map(([k, v]) => (
              <div key={k} className="metric">
                <div className="v">{v}</div>
                <div className="k">{k.replace(/_/g, ' ')}</div>
              </div>
            ))}
            <div className="metric">
              <div className="v">{mb(overview.storage.uploads_dir_bytes)}</div>
              <div className="k">on disk</div>
            </div>
          </div>
          {overview.jobs.length > 0 && (
            <div className="card">
              <h2>Jobs needing attention</h2>
              {overview.jobs.map((j) => (
                <div key={j.id} className="muted">
                  session {j.id} — {j.status}
                  {j.error && <span className="err"> {String(j.error).split('\n').pop()}</span>}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {overview && (
        <div className="card" style={{ maxWidth: 520 }}>
          <h2>Limits</h2>
          <p className="note" style={{ marginTop: 4 }}>
            0 means unlimited. Over the cap, the uploader is offered "parse it and
            delete the log" instead of a refusal.
          </p>
          <div className="row" style={{ gap: 8, marginTop: 8 }}>
            <label>Max upload (MB)</label>
            <input type="number" min="0" value={uploadMb} style={{ width: 100 }}
                   onChange={(e) => setUploadMb(e.target.value)} />
            <button disabled={busy}
                    onClick={() => run(() => api.adminSettings(
                      { upload_max_bytes: Math.max(0, Number(uploadMb) || 0) * (1 << 20) }),
                      'Limit saved.')}>
              Save
            </button>
            <span className="muted">now: {bytesOrOff(overview.settings.upload_max_bytes)}</span>
          </div>
        </div>
      )}

      {publicRuns?.length > 0 && (
        <div className="card">
          <h2>Published raids</h2>
          <p className="note" style={{ marginTop: 4 }}>
            Readable by anyone on the internet without an account.
          </p>
          <table className="data" style={{ maxWidth: 640 }}>
            <thead>
              <tr>
                <th className="l">Zone</th>
                <th>Date</th>
                <th>Raiders</th>
                <th className="l">Published by</th>
              </tr>
            </thead>
            <tbody>
              {publicRuns.map((r) => (
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
        </div>
      )}

      {deletedGroups?.length > 0 && (
        <div className="card">
          <h2>Deleted groups</h2>
          <p className="note" style={{ marginTop: 4 }}>
            A delete is reversible. Restoring puts the group back with its
            members, invites, join code and shares exactly as they were — the
            rows were never removed, only stopped from counting.
          </p>
          <table className="data" style={{ maxWidth: 720 }}>
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
              {deletedGroups.map((gr) => (
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
        </div>
      )}

      <div className="card">
        <h2>Accounts</h2>
        {users === null && <p className="muted">Loading…</p>}
        {users && (
          <SortableTable columns={userColumns} rows={users}
                         defaultSort={{ key: 'stored_bytes', dir: 'desc' }}
                         rowKey={(u) => u.id} />
        )}
      </div>

      <div className="card">
        <h2>Audit log</h2>
        <p className="note" style={{ marginTop: 4 }}>
          Every admin action lands here.
        </p>
        {audit?.length === 0 && <p className="muted">Nothing yet.</p>}
        {audit?.length > 0 && (
          <table className="data" style={{ maxWidth: 720 }}>
            <thead>
              <tr>
                <th className="l">When</th>
                <th className="l">Who</th>
                <th className="l">Action</th>
              </tr>
            </thead>
            <tbody>
              {audit.slice(0, 50).map((e) => (
                <tr key={e.id}>
                  <td className="l muted">{fmt.date(e.ts)} {fmt.time(e.ts)}</td>
                  <td className="l">{e.actor || 'system'}</td>
                  <td className="l muted">{e.action} {e.target || ''} {e.detail || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
