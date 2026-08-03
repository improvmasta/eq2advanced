import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

const rarityClass = (tier) => (tier ? `rarity-${tier.toLowerCase()}` : '')
// tiers a coach would flag as upgrade candidates
const LOW_TIERS = ['Apprentice', 'Journeyman']

// The census payload ships key_stats as one flat list; a coach reads them in
// groups. Split by label here rather than in the API — the backend contract is
// covered by tests and nothing else needs the grouping.
const COMBAT_STATS = ['Ability Mod', 'Base Modifier', 'Crit Chance', 'DPS Mod', 'Haste']
const CASTING_STATS = ['Cast Speed', 'Reuse Speed', 'Recovery']

const ATTR_LABELS = {
  str: 'Strength', sta: 'Stamina', agi: 'Agility', wis: 'Wisdom', int: 'Intelligence',
}
const RESIST_LABELS = {
  physical: 'Physical', elemental: 'Elemental',
  arcane: 'Arcane', noxious: 'Noxious', mana: 'Mana',
}

function StatGroup({ title, rows }) {
  if (!rows.length) return null
  return (
    <div className="statgroup">
      <h2>{title}</h2>
      {rows.map((r) => (
        <div className="statrow" key={r.k}>
          <span className="k">{r.k}</span>
          <span className="v">{r.v}</span>
        </div>
      ))}
    </div>
  )
}

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
  const [tab, setTab] = useState('gear')

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
  const stat = (label) => data.key_stats?.find((s) => s.label === label)
  const show = (s) => (s.pct ? `${s.value}%` : fmt.num(s.value))
  const pick = (labels) => labels.map(stat).filter(Boolean).map((s) => ({ k: s.label, v: show(s) }))

  const lowTiers = data.synced
    ? data.spells.scribed.filter((s) => LOW_TIERS.includes(s.tier_name)).length : 0

  const attrRows = data.synced
    ? Object.entries(ATTR_LABELS)
      .filter(([k]) => data.attributes?.[k] != null)
      .map(([k, label]) => ({ k: label, v: fmt.num(data.attributes[k]) }))
    : []
  const resistRows = data.synced
    ? Object.entries(RESIST_LABELS)
      .filter(([k]) => data.resists?.[k] != null)
      .map(([k, label]) => ({ k: label, v: fmt.num(data.resists[k]) }))
    : []

  return (
    <>
      <div className="idcard">
        <div className="who">
          <div className="n">{c.name}</div>
          <div className="m">
            {c.class ? `${c.class} ${c.level}` : 'not yet synced'} · Wuoshi
            {data.guild ? ` · <${data.guild}>` : ''}
          </div>
        </div>
        {data.synced && (
          <div className="facts">
            <div className="fact"><div className="k">Level</div><div className="v">{c.level ?? '—'}</div></div>
            <div className="fact"><div className="k">AAs</div><div className="v">{data.aa_spent ?? '—'}</div></div>
            <div className="fact"><div className="k">Health</div><div className="v">{fmt.num(data.vitals.health)}</div></div>
            <div className="fact"><div className="k">Power</div><div className="v">{fmt.num(data.vitals.power)}</div></div>
            <div className="fact">
              <div className="k">Synced</div>
              <div className="v">{c.last_census_ts ? fmt.date(c.last_census_ts) : 'never'}</div>
            </div>
          </div>
        )}
      </div>

      <div className="pagehead">
        <span className="sub">
          {c.last_census_ts ? `Synced ${fmt.time(c.last_census_ts)}` : 'Never synced'} · auto-refreshes daily
        </span>
        <div className="actions">
          <button onClick={refresh} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh from Census'}
          </button>
        </div>
      </div>
      {notice && <p className="muted" style={{ fontSize: 'var(--fs-xs)' }}>{notice}</p>}
      {error && <p className="err">{error}</p>}

      {!data.synced && (
        <div className="card">
          <p className="note">
            No Census data yet. Hit <b>Refresh from Census</b> — the character must be
            set visible in-game for Census to serve it.
          </p>
        </div>
      )}

      {data.synced && (
        <>
          <div className="tabs">
            <button className={tab === 'gear' ? 'active' : ''} onClick={() => setTab('gear')}>
              Equipment &amp; Stats
            </button>
            <button className={tab === 'spells' ? 'active' : ''} onClick={() => setTab('spells')}>
              Spells
            </button>
            <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>
              History{snapshots.length ? ` (${snapshots.length})` : ''}
            </button>
          </div>

          {tab === 'gear' && (
            <div className="railgrid" style={{ marginTop: 12 }}>
              <div className="rail">
                <StatGroup title="Attributes" rows={attrRows} />
                <StatGroup title="Combat" rows={pick(COMBAT_STATS)} />
                <StatGroup title="Casting" rows={pick(CASTING_STATS)} />
                <StatGroup title="Resists" rows={resistRows} />
              </div>
              <div className="card" style={{ marginTop: 0 }}>
                <h2>Equipment</h2>
                <div className="gearlist">
                  {data.gear.map((g) => (
                    <div className="gearrow" key={g.slot}>
                      <span className="slot">{g.slot}</span>
                      <span className="item">
                        <span className={rarityClass(g.tier)}>{g.name ?? '—'}</span>
                        {g.adorns > 0 && (
                          <span className="adorns">
                            {Array.from({ length: g.adorns }, (_, i) => (
                              <span className="chip on" key={i}>◆</span>
                            ))}
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {tab === 'spells' && (
            <div className="card">
              <h2>Scribed spells</h2>
              {lowTiers > 0 && (
                <p className="note">
                  {lowTiers} spell{lowTiers === 1 ? ' is' : 's are'} still at Apprentice or
                  Journeyman — the cheapest upgrades on the list.
                </p>
              )}
              <div className="tablewrap">
                <table className="data">
                  <thead><tr><th>Spell</th><th>Tier</th><th>Level</th></tr></thead>
                  <tbody>
                    {data.spells.scribed.map((s) => (
                      <tr key={s.id}>
                        <td className="name">{s.name}</td>
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
              </div>
              {data.spells.other_count > 0 && (
                <p className="note" style={{ marginTop: 8, marginBottom: 0 }}>
                  +{data.spells.other_count} non-{c.class} entries (tradeskill arts, languages…)
                </p>
              )}
            </div>
          )}

          {tab === 'history' && (
            <div className="card">
              <h2>Snapshot history</h2>
              <p className="note">
                One entry per time Census saw the character change. Expand to see what moved.
              </p>
              {snapshots.length === 0 && <p className="muted">No snapshots yet.</p>}
              {snapshots.map((s, i) => (
                <Snapshot key={s.id} charId={id} snap={s} isLatest={i === 0} />
              ))}
            </div>
          )}
        </>
      )}
    </>
  )
}
