import { Link, useLocation } from 'react-router-dom'

const PRIMARY = [
  { to: '/admin', label: 'Overview', hint: 'Accounts and site health', match: (p) => ['/admin', '/admin/accounts'].includes(p) },
  { to: '/admin/abilities', label: 'Game data', hint: 'Abilities and timers', match: (p) => ['/admin/abilities', '/admin/timers'].includes(p) },
]

const UTILITIES = [
  { to: '/admin/visitors', label: 'Visitor analytics' },
  { to: '/admin/activity', label: 'Activity log' },
  { to: '/admin/groups', label: 'Deleted groups' },
  { to: '/admin/work', label: 'Review and diagnostics' },
]

export default function AdminShell({ user, children }) {
  const { pathname } = useLocation()
  const curator = user?.role === 'curator'
  const primary = curator ? PRIMARY.slice(1) : PRIMARY

  return (
    <div className="manage adminshell">
      <aside className="adminrail" aria-label="Admin workspace">
        <div className="adminbrand">
          <span>EQ2A</span>
          <strong>{curator ? 'Curation' : 'Admin'}</strong>
        </div>

        <nav className="adminprimary" aria-label="Primary admin sections">
          {primary.map((item) => (
            <Link key={item.to} to={item.to}
                  className={`adminnav${item.match(pathname) ? ' on' : ''}`}>
              <span>{item.label}</span>
              <small>{item.hint}</small>
            </Link>
          ))}
          {curator && (
            <Link to="/admin/timers" className={`adminnav${pathname === '/admin/timers' ? ' on' : ''}`}>
              <span>AoE timers</span><small>Evidence and rulings</small>
            </Link>
          )}
        </nav>

        {!curator && (
          <details className="adminmore" open={UTILITIES.some((item) => item.to === pathname)}>
            <summary>Utilities</summary>
            <div>
              {UTILITIES.map((item) => (
                <Link key={item.to} to={item.to} className={pathname === item.to ? 'on' : ''}>
                  {item.label}
                </Link>
              ))}
            </div>
          </details>
        )}
        <Link className="adminexit" to="/">← Back to site</Link>
      </aside>
      <section className="adminmain">{children}</section>
    </div>
  )
}
