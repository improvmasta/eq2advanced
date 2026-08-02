import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

function ShareBar({ value, max }) {
  const w = max > 0 ? Math.max((value / max) * 100, 0.5) : 0
  return <div className="sharebar dps" role="img" aria-label={`${Math.round((value / (max || 1)) * 100)}% of top`}><i style={{ width: `${w}%` }} /></div>
}

function Engage({ delay, confidence, anchor, samples }) {
  if (delay == null) return <span className="muted">—</span>
  return (
    <>
      {delay}s
      {anchor ? <span className="muted"> ({anchor})</span> : null}
      {samples != null && samples > 0 ? <span className="muted"> ×{samples}</span> : null}
      {confidence === 'low' ? <span className="badge conf-low" title="first action may be a pre-pull buff proc">?</span> : null}
    </>
  )
}

export default function RaidReport() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [view, setView] = useState('night') // 'night' | encounter index

  useEffect(() => {
    api.raidReport(id).then(setData).catch((e) => setError(e.message))
  }, [id])

  const rows = useMemo(() => {
    if (!data) return []
    if (view === 'night') return data.night
    return data.encounters[Number(view)]?.players ?? []
  }, [data, view])

  if (error) return <p className="err">{error}</p>
  if (!data) return <p className="muted">Loading…</p>

  const night = view === 'night'
  const enc = night ? null : data.encounters[Number(view)]
  const topDamage = Math.max(...rows.map((r) => r.damage), 1)

  return (
    <>
      <p style={{ marginTop: 12 }}><Link to={`/sessions/${id}`}>← back to session</Link></p>
      <h1>Raid report{data.frozen ? <span className="badge" title="events were pruned; this report is frozen"> frozen</span> : null}</h1>
      <p className="muted">
        {data.character_name}'s log · {fmt.dur(data.combat_s)} of combat ·{' '}
        {fmt.num(data.raid_damage)} raid damage
      </p>

      <div className="card">
        <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="muted">View</span>
          <select value={view} onChange={(e) => setView(e.target.value)}>
            <option value="night">Whole night</option>
            {data.encounters.map((er, i) => (
              <option key={er.encounter.id} value={i}>
                {er.encounter.name || 'trash'}{er.encounter.is_named ? ' ★' : ''} · {fmt.time(er.encounter.started_ts)}
              </option>
            ))}
          </select>
        </label>
        {enc && (
          <p className="muted" style={{ margin: '8px 0 0' }}>
            {enc.encounter.zone} · {fmt.dur(enc.encounter.duration_s)} · {fmt.num(enc.raid_damage)} raid damage
          </p>
        )}

        <table className="data">
          <thead>
            <tr>
              <th>Raider</th><th>Damage</th><th style={{ textAlign: 'left' }}>Share</th>
              <th>DPS</th><th>Engage</th><th>Deaths</th><th>Dead</th>
              <th>Damage lost dead</th><th>Cures</th><th>Rez delay</th>
              <th>Heals</th><th>Overheal</th><th>Saves</th><th>Wards</th><th>Power fed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.entity_id}>
                <td>{p.name}</td>
                <td>{fmt.num(p.damage)}</td>
                <td><ShareBar value={p.damage} max={topDamage} /></td>
                <td>{fmt.num(p.dps)}</td>
                <td>
                  {night
                    ? <Engage delay={p.avg_engage_delay_s} samples={p.engage_samples} />
                    : <Engage delay={p.engage_delay_s} confidence={p.engage_confidence} anchor={p.engage_anchor} />}
                </td>
                <td>{p.deaths || ''}</td>
                <td>{p.time_dead_s ? fmt.dur(p.time_dead_s) : ''}</td>
                <td>{p.death_dps_lost ? fmt.num(p.death_dps_lost) : ''}</td>
                <td>{p.cures || ''}</td>
                <td>{(night ? p.avg_rez_delay_s : p.rez_delay_s) != null ? `${night ? p.avg_rez_delay_s : p.rez_delay_s}s` : ''}</td>
                <td>{fmt.num(p.heals)}</td>
                <td>
                  {p.overheal_est ? fmt.num(p.overheal_est) : ''}
                  {night && p.overheal_pct != null && p.overheal_est
                    ? <span className="muted"> ({p.overheal_pct}%)</span> : null}
                </td>
                <td>{p.saves || ''}</td>
                <td>
                  {fmt.num(p.wards_absorbed)}
                  {p.ward_bleedthrough
                    ? <span className="muted" title="damage that punched through wards"> +{fmt.num(p.ward_bleedthrough)} thru</span>
                    : null}
                </td>
                <td>{fmt.num(p.power_fed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="muted" style={{ fontSize: '0.85rem' }}>
        {data.caveats.map((c, i) => <span key={i}>{c}<br /></span>)}
      </p>
    </>
  )
}
