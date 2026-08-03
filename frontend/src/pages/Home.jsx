import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import UploadDrop from '../components/UploadDrop.jsx'
import { api, fmt } from '../lib/api.js'

/* Landing page: every zone run grouped by calendar day, newest first. Files
   are an ingest detail — the raid nights themselves are the navigation. */
export default function Home() {
  const [runs, setRuns] = useState(null)
  const [sessions, setSessions] = useState(null)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.zoneRuns().then((d) => setRuns(d.zone_runs)).catch((e) => setError(e.message))
    api.sessions().then((d) => setSessions(d.sessions)).catch(() => {})
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // poll while an upload is parsing — new runs appear as parses land
  const parsing = sessions?.some((s) => s.status === 'parsing' || s.status === 'receiving')
  useEffect(() => {
    if (!parsing) return
    const t = setInterval(refresh, 2000)
    return () => clearInterval(t)
  }, [parsing, refresh])

  const multiChar = useMemo(
    () => new Set((runs || []).map((r) => r.character_name)).size > 1, [runs])

  const days = useMemo(() => {
    if (!runs) return null
    const by = new Map()
    for (const r of runs) {
      const k = fmt.dayKey(r.started_ts)
      if (!by.has(k)) by.set(k, { key: k, ts: r.started_ts, runs: [] })
      by.get(k).runs.push(r)
    }
    return [...by.values()]   // API is newest-first already
  }, [runs])

  return (
    <>
      <div className="pagehead">
        <h1>Raids</h1>
        <span className="sub">Every night, organized by day and zone</span>
      </div>

      <UploadDrop compact onUploaded={refresh} />
      {parsing && <p className="muted">Parsing… new runs appear as logs finish.</p>}
      {error && <p className="err">{error}</p>}
      {days === null && !error && <p className="muted">Loading…</p>}
      {days?.length === 0 && (
        <p className="muted">Nothing yet — drop a log above, or <Link to="/uploads">manage uploads</Link>.</p>
      )}

      {days?.map((day) => (
        <section key={day.key} className="dayblock">
          <h2 className="dayhead">{fmt.dateLong(day.ts)}<span className="muted"> · {new Date(day.ts * 1000).getFullYear()}</span></h2>
          <div className="runlist">
            {day.runs.map((r) => (
              <Link key={r.id} to={`/zones/${r.id}`} className="runrow card">
                <div className="runmain">
                  <span className="runzone">{r.zone || 'Unknown zone'}</span>
                  <span className="runtime muted">{fmt.timeRange(r.started_ts, r.ended_ts)}</span>
                </div>
                <div className="runmeta">
                  <span>{r.encounter_count} fight{r.encounter_count === 1 ? '' : 's'}</span>
                  {r.named_count > 0 && (
                    <span className="named">
                      {r.success_count}/{r.named_count} named
                    </span>
                  )}
                  {r.raider_count > 1 && <span>{r.raider_count} raiders</span>}
                  <span className="muted">{fmt.dur(r.combat_s)} combat</span>
                  {multiChar && <span className="badge">{r.character_name}</span>}
                </div>
              </Link>
            ))}
          </div>
        </section>
      ))}

      <p className="note" style={{ marginTop: 24 }}>
        <Link to="/uploads">Manage uploads →</Link>
      </p>
    </>
  )
}
