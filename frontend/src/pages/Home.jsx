import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import SelectionBar from '../components/SelectionBar.jsx'
import SortableTable from '../components/SortableTable.jsx'
import Sparkline from '../components/Sparkline.jsx'
import { api, fmt } from '../lib/api.js'

/* Landing page: every zone run as a row in one sortable table. Files are an
   ingest detail — the raid nights themselves are the navigation, and they read
   like the raid page's tables because they answer the same kind of question.

   The list is editable, because segmentation is a guess. A zone re-entry the
   game logged as two visits is one raid to the people who were there, and a
   pull nobody counts is noise in every total. Merge, unmerge and delete are
   remembered per FIGHT (backend `run_edits`), so an edit survives the reparse
   that a later backfill triggers. */

const DAY_MS = 86_400_000

/* "Tonight" / "Yesterday" / weekday — a raid list is read by when, and the
   exact date is one column over anyway. */
function dayLabel(ts) {
  const d = new Date(ts * 1000)
  const midnight = new Date().setHours(0, 0, 0, 0)
  const days = Math.floor((midnight - new Date(d).setHours(0, 0, 0, 0)) / DAY_MS)
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days < 7) return d.toLocaleDateString([], { weekday: 'long' })
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
}

export default function Home() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState(null)
  const [sessions, setSessions] = useState(null)
  const [error, setError] = useState(null)
  const [picked, setPicked] = useState(() => new Set())
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(null)   // {kind, runs} pending action
  const [orphans, setOrphans] = useState(null)   // logs with nothing left in them
  const [undo, setUndo] = useState(null)         // {fingerprints, character_id, n}

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

  const pickedRuns = useMemo(
    () => (runs || []).filter((r) => picked.has(r.id)), [runs, picked])

  async function perform(fn) {
    setBusy(true)
    setError(null)
    try { await fn() } catch (e) { setError(e.message) }
    setBusy(false)
    setConfirm(null)
    refresh()
  }

  const doMerge = () => perform(async () => {
    await api.mergeZoneRuns(pickedRuns.map((r) => r.id))
    setPicked(new Set())
  })

  const doUnmerge = () => perform(async () => {
    for (const r of pickedRuns) await api.unmergeZoneRun(r.id)
    setPicked(new Set())
  })

  const doDelete = () => perform(async () => {
    let empties = []
    const fps = []
    // restore is per character; offering Undo across a mixed selection would
    // put half the fights back and say nothing about the other half
    const chars = new Set(pickedRuns.map((r) => r.character_id))
    for (const r of pickedRuns) {
      const d = await api.deleteZoneRun(r.id)
      fps.push(...(d.fingerprints || []))
      empties = d.empty_sessions || empties
    }
    setPicked(new Set())
    setUndo(fps.length && chars.size === 1
      ? { fingerprints: fps, character_id: [...chars][0], n: fps.length } : null)
    setOrphans(empties.length ? empties : null)
  })

  const doUndo = () => perform(async () => {
    await api.restoreEncounters(undo.fingerprints, undo.character_id)
    setUndo(null)
    setOrphans(null)
  })

  const deleteLogs = () => perform(async () => {
    for (const s of orphans) await api.deleteSession(s.id)
    setOrphans(null)
    setUndo(null)      // the log is gone; there is nothing left to restore into
  })

  const peak = (r) => (r.spark?.length ? Math.max(...r.spark) : null)

  const columns = [
    {
      key: 'day', label: 'Night', align: 'l',
      render: (r) => (
        <span className="runday">
          <span className="d">{dayLabel(r.started_ts)}</span>
          <span className="muted">{fmt.date(r.started_ts)}</span>
        </span>
      ),
      sortValue: (r) => r.started_ts,
    },
    {
      key: 'zone', label: 'Zone', align: 'l',
      render: (r) => (
        <span className="runzone">
          {r.zone || 'Unknown zone'}
          {r.merged && <span className="badge" title="Merged by hand — Unmerge puts it back">merged</span>}
        </span>
      ),
      sortValue: (r) => r.zone || '',
    },
    {
      key: 'time', label: 'Time', render: (r) => fmt.timeRange(r.started_ts, r.ended_ts),
      sortValue: (r) => r.started_ts,
    },
    { key: 'encounter_count', label: 'Fights' },
    {
      key: 'named', label: 'Named',
      render: (r) => (r.named_count > 0
        ? <span className={r.success_count === r.named_count ? 'rank-top' : ''}>
            {r.success_count}/{r.named_count}
          </span>
        : ''),
      sortValue: (r) => r.named_count,
    },
    { key: 'raider_count', label: 'Raiders', render: (r) => r.raider_count || '' },
    { key: 'combat_s', label: 'Combat', render: (r) => fmt.dur(r.combat_s), sortValue: (r) => r.combat_s },
    {
      key: 'peak', label: 'Peak DPS',
      render: (r) => (peak(r) != null ? fmt.num(peak(r)) : ''),
      sortValue: peak,
    },
    {
      key: 'spark', label: 'Shape', sortable: false,
      render: (r) => <Sparkline values={r.spark} title="Raid DPS, fight by fight" />,
    },
    ...(multiChar ? [{ key: 'character_name', label: 'Character', align: 'l' }] : []),
  ]

  return (
    <>
      <div className="pagehead">
        <h1>Raids</h1>
        <span className="sub">Every zone run you have parsed, newest first</span>
        <span className="actions">
          <Link className="btnlink" to="/import">Import a log</Link>
        </span>
      </div>

      {parsing && <p className="muted">Parsing… new runs appear as logs finish.</p>}
      {error && <p className="err">{error}</p>}
      {undo && (
        <p className="note flash">
          {undo.n} fight{undo.n === 1 ? '' : 's'} deleted.
          <button className="chip" disabled={busy} onClick={doUndo}>Undo</button>
          <button className="chip" onClick={() => setUndo(null)}>Dismiss</button>
        </p>
      )}
      {orphans && (
        <div className="card confirmcard">
          <p>
            {orphans.length === 1
              ? `${orphans[0].upload_name || `Log ${orphans[0].id}`} has no fights left in it.`
              : `${orphans.length} logs have no fights left in them.`}
            {' '}Delete the uploaded log too? The raw file goes with it.
          </p>
          <div className="row">
            <button disabled={busy} onClick={deleteLogs}>Delete the log</button>
            <button className="chip" onClick={() => setOrphans(null)}>Keep it</button>
          </div>
        </div>
      )}
      {confirm?.kind === 'delete' && (
        <div className="card confirmcard">
          <p>
            Delete {confirm.runs.length === 1
              ? <strong>{confirm.runs[0].zone || 'Unknown zone'}</strong>
              : `${confirm.runs.length} raids`}
            {' '}— {confirm.runs.reduce((s, r) => s + r.encounter_count, 0)} fights disappear
            from every total. The log stays; you can undo right after.
          </p>
          <div className="row">
            <button disabled={busy} onClick={doDelete}>Delete</button>
            <button className="chip" onClick={() => setConfirm(null)}>Cancel</button>
          </div>
        </div>
      )}

      {runs === null && !error && <p className="muted">Loading…</p>}
      {runs?.length === 0 && (
        <p className="muted">
          Nothing yet — <Link to="/import">import a log</Link> to get started.
        </p>
      )}

      {runs?.length > 0 && (
        <div className="card">
          <SortableTable
            columns={columns}
            rows={runs}
            defaultSort={{ key: 'day', dir: 'desc' }}
            rowKey={(r) => r.id}
            className="raidlist"
            wrapClass={runs.length > 14 ? 'sticky' : ''}
            onRowClick={(r) => navigate(`/zones/${r.id}`)}
            checkable={() => true}
            checkedKeys={picked}
            onCheck={(id) => setPicked((s) => {
              const next = new Set(s)
              if (next.has(id)) next.delete(id); else next.add(id)
              return next
            })}
          />
        </div>
      )}

      {pickedRuns.length > 0 && (
        <SelectionBar
          label={`${pickedRuns.length} raid${pickedRuns.length === 1 ? '' : 's'}`}
          stats={[
            { k: 'Fights', v: pickedRuns.reduce((s, r) => s + r.encounter_count, 0) },
            { k: 'Combat', v: fmt.dur(pickedRuns.reduce((s, r) => s + r.combat_s, 0)) },
            {
              k: 'Named', v: pickedRuns.some((r) => r.named_count)
                ? `${pickedRuns.reduce((s, r) => s + r.success_count, 0)}/${pickedRuns.reduce((s, r) => s + r.named_count, 0)}`
                : null,
            },
          ]}
          onClear={() => setPicked(new Set())}
          actions={
            <>
              {pickedRuns.length >= 2 && (
                <button className="chip" disabled={busy} onClick={doMerge}
                        title="Treat these as one raid — a zone you re-entered is still one night">
                  Merge
                </button>
              )}
              {pickedRuns.some((r) => r.merged) && (
                <button className="chip" disabled={busy} onClick={doUnmerge}
                        title="Undo the merge and let the segmenter decide again">
                  Unmerge
                </button>
              )}
              <button className="chip danger" disabled={busy}
                      onClick={() => setConfirm({ kind: 'delete', runs: pickedRuns })}>
                Delete
              </button>
            </>
          }
        />
      )}
    </>
  )
}
