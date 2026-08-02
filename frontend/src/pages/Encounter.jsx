import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

function ShareBar({ value, max, kind = 'dps' }) {
  const w = max > 0 ? Math.max((value / max) * 100, 0.5) : 0
  return (
    <div className={`sharebar ${kind}`} role="img" aria-label={`${Math.round((value / (max || 1)) * 100)}% of top`}>
      <i style={{ width: `${w}%` }} />
    </div>
  )
}

export default function Encounter() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    api.encounter(id).then((d) => {
      setData(d)
      // preselect the top player
      const top = d.actors.find((a) => a.kind === 'player')
      setSelected(top?.entity_id ?? null)
    }).catch((e) => setError(e.message))
  }, [id])

  const abilityRows = useMemo(() => {
    if (!data || selected == null) return []
    return data.abilities.filter(
      (r) => (r.entity_id === selected || r.rollup_to === selected)
        && r.kind !== 'self'            // self-inflicted focus damage (e.g. Vampiric
        && !(r.kind === 'damage' && r.total === 0 && r.hits > 0 && r.max === null),
      // ^ pure zero-damage rows (fully absorbed) add noise without information
    )
  }, [data, selected])

  if (error) return <p className="err">{error}</p>
  if (!data) return <p className="muted">Loading…</p>

  const { encounter, actors } = data
  const players = actors.filter((a) => a.kind === 'player' && (a.damage > 0 || a.heals > 0))
  const raidDamage = players.reduce((s, a) => s + a.damage, 0)
  const topDamage = Math.max(...players.map((a) => a.damage), 1)
  const selName = actors.find((a) => a.entity_id === selected)?.name
  const abilityMax = Math.max(...abilityRows.map((r) => r.total), 1)

  return (
    <>
      <p style={{ marginTop: 12 }}>
        <Link to={`/sessions/${encounter.session_id}`}>← back to session</Link>
      </p>
      <h1>{encounter.name}</h1>
      <p className="muted">{encounter.zone} · {fmt.time(encounter.started_ts)}</p>
      <div className="tiles">
        <div className="tile"><div className="v">{fmt.dur(encounter.duration_s)}</div><div className="k">Length</div></div>
        <div className="tile"><div className="v">{fmt.num(raidDamage)}</div><div className="k">Raid damage</div></div>
        <div className="tile"><div className="v">{fmt.num(raidDamage / Math.max(encounter.duration_s, 1))}</div><div className="k">Raid DPS</div></div>
        <div className="tile"><div className="v">{players.length}</div><div className="k">Raiders</div></div>
      </div>

      <div className="card">
        <h2>Raiders</h2>
        <p className="muted" style={{ fontSize: '0.85rem' }}>
          Pets roll up into their owner. Click a row for the ability breakdown.
        </p>
        <table className="data">
          <thead>
            <tr>
              <th>Name</th><th>Damage</th><th style={{ textAlign: 'left' }}>Share</th><th>DPS</th>
              <th>Heals</th><th>Wards</th><th>Power fed</th><th>Deaths</th><th>Rezzes</th>
            </tr>
          </thead>
          <tbody>
            {players.map((a) => (
              <tr
                key={a.entity_id}
                className={`clickable ${a.entity_id === selected ? 'selected' : ''}`}
                onClick={() => setSelected(a.entity_id)}
              >
                <td>{a.name}</td>
                <td>{fmt.num(a.damage)}</td>
                <td><ShareBar value={a.damage} max={topDamage} /></td>
                <td>{fmt.num(a.dps)}</td>
                <td>{fmt.num(a.heals)}</td>
                <td>{fmt.num(a.wards_absorbed)}</td>
                <td>{fmt.num(a.power_fed)}</td>
                <td>{a.deaths || ''}</td>
                <td>{a.rez_casts || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected != null && (
        <div className="card">
          <h2>{selName} — abilities</h2>
          <table className="data">
            <thead>
              <tr>
                <th>Ability</th><th>Source</th><th>Kind</th><th>Total</th>
                <th style={{ textAlign: 'left' }}>Share</th>
                <th>Casts</th><th>Hits</th><th>Crit %</th><th>Avg</th><th>Min</th><th>Max</th>
                <th>Miss</th><th>Resist</th>
              </tr>
            </thead>
            <tbody>
              {abilityRows.map((r, i) => (
                <tr key={i}>
                  <td>{r.ability}</td>
                  <td className="muted">
                    {r.entity_id === selected ? '' : r.source_name}
                    {r.source_kind !== 'player' && <span className="badge pet">{r.source_kind === 'own_pet' ? 'pet' : 'swarm'}</span>}
                  </td>
                  <td className="muted">{r.kind}</td>
                  <td>{fmt.num(r.total)}</td>
                  <td><ShareBar value={r.total} max={abilityMax} kind={r.kind === 'heal' ? 'heal' : 'dps'} /></td>
                  <td>{r.casts || ''}</td>
                  <td>{r.hits}</td>
                  <td>{r.hits ? Math.round((r.crits / r.hits) * 100) : 0}%</td>
                  <td>{r.hits ? fmt.num(r.total / r.hits) : '—'}</td>
                  <td>{fmt.num(r.min)}</td>
                  <td>{fmt.num(r.max)}</td>
                  <td>{r.misses || ''}</td>
                  <td>{r.resists || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
