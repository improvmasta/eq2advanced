import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

const rarityClass = (tier) => (tier ? `rarity-${tier.toLowerCase()}` : '')
// tiers a coach would flag as upgrade candidates
const LOW_TIERS = ['Apprentice', 'Journeyman']

function Snapshot({ charId, snap, isLatest }) {
  const [diff, setDiff] = useState(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (open && diff === null) {
      api.censusDiff(charId, snap.id).then(setDiff).catch(() => setDiff({ error: true }))
    }
  }, [open, diff, charId, snap.id])

  const empty = diff && !diff.first && !diff.error
    && !diff.stats?.length && !diff.gear?.length && !diff.spells?.length

  return (
    <div className="snapshot">
      <button className="linklike" onClick={() => setOpen(!open)}>
        {open ? '▾' : '▸'} {fmt.date(snap.fetched_ts)} {fmt.time(snap.fetched_ts)}
        {isLatest && <span className="muted"> (current)</span>}
      </button>
      {open && diff === null && <p className="muted">Loading…</p>}
      {open && diff?.first && <p className="muted">First snapshot — nothing to compare against.</p>}
      {open && diff?.error && <p className="err">Couldn't load the diff.</p>}
      {open && empty && <p className="muted">No stat, gear, or spell changes.</p>}
      {open && diff && !diff.first && !diff.error && (
        <ul className="difflist">
          {diff.stats?.map((s) => (
            <li key={s.label}>
              {s.label}: {s.from}{s.pct ? '%' : ''} → <b>{s.to}{s.pct ? '%' : ''}</b>
            </li>
          ))}
          {diff.gear?.map((g) => (
            <li key={g.slot}>
              {g.slot}: {g.from ?? 'empty'} → <b>{g.to ?? 'empty'}</b>
            </li>
          ))}
          {diff.spells?.map((s) => (
            <li key={s.name}>
              {s.name}: {s.from_tier ?? 'unscribed'} → <b>{s.to_tier ?? 'unscribed'}</b>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function Character() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [snapshots, setSnapshots] = useState([])
  const [refreshing, setRefreshing] = useState(false)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    api.census(id).then(setData).catch((e) => setError(e.message))
    api.censusSnapshots(id).then((d) => setSnapshots(d.snapshots)).catch(() => {})
  }, [id])

  useEffect(() => { load() }, [load])

  async function refresh() {
    setRefreshing(true)
    setError(null)
    setNotice(null)
    try {
      const r = await api.censusRefresh(id)
      if (r.skipped) setNotice('Synced less than a minute ago — showing the cached snapshot.')
      else setNotice(r.changed ? 'Census updated.' : 'No changes since the last sync.')
      load()
    } catch (e) { setError(e.message) } finally { setRefreshing(false) }
  }

  if (error && !data) return <p className="err">{error}</p>
  if (!data) return <p className="muted">Loading…</p>

  const c = data.character
  const lowTiers = data.synced
    ? data.spells.scribed.filter((s) => LOW_TIERS.includes(s.tier_name)).length : 0

  return (
    <>
      <div className="charhead">
        <h1>{c.name}</h1>
        <span className="muted">
          {c.class ? `${c.class} ${c.level}` : 'not yet synced'} · Wuoshi
          {data.guild ? ` · <${data.guild}>` : ''}
        </span>
        <div className="charactions">
          <button onClick={refresh} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh from Census'}
          </button>
        </div>
      </div>
      <p className="muted">
        Last synced: {c.last_census_ts ? `${fmt.date(c.last_census_ts)} ${fmt.time(c.last_census_ts)}` : 'never'}
        {' '}· auto-refreshes daily
      </p>
      {notice && <p className="muted">{notice}</p>}
      {error && <p className="err">{error}</p>}

      {!data.synced && (
        <div className="card">
          <p className="muted">
            No Census data yet. Hit <b>Refresh from Census</b> — the character must be
            set visible in-game for Census to serve it.
          </p>
        </div>
      )}

      {data.synced && (
        <>
          <div className="card">
            <h2>Combat stats</h2>
            <div className="tiles">
              {data.key_stats.map((s) => (
                <div className="tile" key={s.label}>
                  <div className="v">{s.pct ? `${s.value}%` : fmt.num(s.value)}</div>
                  <div className="k">{s.label}</div>
                </div>
              ))}
            </div>
            <div className="tiles">
              <div className="tile"><div className="v">{fmt.num(data.vitals.health)}</div><div className="k">Health</div></div>
              <div className="tile"><div className="v">{fmt.num(data.vitals.power)}</div><div className="k">Power</div></div>
              {Object.entries(data.attributes).map(([k, v]) => (
                <div className="tile" key={k}><div className="v">{fmt.num(v)}</div><div className="k">{k}</div></div>
              ))}
              {data.aa_spent != null && (
                <div className="tile"><div className="v">{data.aa_spent}</div><div className="k">AA spent</div></div>
              )}
            </div>
          </div>

          <div className="card">
            <h2>Equipment</h2>
            <table className="data">
              <thead><tr><th>Slot</th><th>Item</th><th>Adorns</th></tr></thead>
              <tbody>
                {data.gear.map((g) => (
                  <tr key={g.slot}>
                    <td className="muted">{g.slot}</td>
                    <td className={rarityClass(g.tier)}>{g.name ?? '—'}</td>
                    <td>{g.adorns || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h2>Scribed spells</h2>
            {lowTiers > 0 && (
              <p className="muted">
                {lowTiers} spell{lowTiers === 1 ? ' is' : 's are'} still at Apprentice or
                Journeyman — the cheapest upgrades on the list.
              </p>
            )}
            <table className="data">
              <thead><tr><th>Spell</th><th>Tier</th><th>Level</th></tr></thead>
              <tbody>
                {data.spells.scribed.map((s) => (
                  <tr key={s.id}>
                    <td>{s.name}</td>
                    <td>
                      <span className={`tierbadge ${LOW_TIERS.includes(s.tier_name) ? 'low' : ''}`}>
                        {s.tier_name ?? '—'}
                      </span>
                    </td>
                    <td className="muted">{s.level || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.spells.other_count > 0 && (
              <p className="muted">
                +{data.spells.other_count} non-{c.class} entries (tradeskill arts, languages…)
              </p>
            )}
          </div>
        </>
      )}

      {snapshots.length > 0 && (
        <div className="card">
          <h2>Snapshot history</h2>
          <p className="muted">One entry per time Census saw the character change. Expand to see what moved.</p>
          {snapshots.map((s, i) => (
            <Snapshot key={s.id} charId={id} snap={s} isLatest={i === 0} />
          ))}
        </div>
      )}
    </>
  )
}
