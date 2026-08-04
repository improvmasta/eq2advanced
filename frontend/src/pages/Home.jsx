import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import SelectionBar from '../components/SelectionBar.jsx'
import ShareDialog from '../components/ShareDialog.jsx'
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

/* A group is six, so seven raiders means the night was a raid. `raider_count`
   is the run's ROSTER, not everyone the log overheard — the backend
   (pipeline/zoneruns.py) drops mobs, bystanders who only ever got hit, and the
   group that fought past you, all of which used to push a six-man run over
   this line. Solo and group runs are real parses, just not what this list is
   for, so they are off unless you ask. */
const RAID_MIN_RAIDERS = 7
const RAIDS_ONLY_KEY = 'eq2advanced-raids-only'
const SHOW_PUBLIC_KEY = 'eq2advanced-show-public'

const isRaid = (r) => (r.raider_count || 0) >= RAID_MIN_RAIDERS

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

export default function Home({ user }) {
  const navigate = useNavigate()
  const [runs, setRuns] = useState(null)
  const [sessions, setSessions] = useState(null)
  const [error, setError] = useState(null)
  const [picked, setPicked] = useState(() => new Set())
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(null)   // {kind, runs} pending action
  const [orphans, setOrphans] = useState(null)   // logs with nothing left in them
  const [undo, setUndo] = useState(null)         // {fingerprints, character_id, n}
  const [raidsOnly, setRaidsOnly] = useState(
    () => localStorage.getItem(RAIDS_ONLY_KEY) !== '0')
  /* Published raids are readable by anyone, so they turn up in the list of a
     brand-new account that has parsed nothing. `via_public` marks the ones you
     can ONLY see because they were published — a raid of your own that is also
     public is still yours and never disappears with this off. */
  const [showPublic, setShowPublic] = useState(
    () => localStorage.getItem(SHOW_PUBLIC_KEY) !== '0')
  // mine | shared | all — a group's raids sit in the same list as your own,
  // labelled, because they answer the same question
  const [scope, setScope] = useState('all')
  const [sharing, setSharing] = useState(null)   // run id whose share panel is open

  useEffect(() => {
    localStorage.setItem(RAIDS_ONLY_KEY, raidsOnly ? '1' : '0')
  }, [raidsOnly])

  useEffect(() => {
    localStorage.setItem(SHOW_PUBLIC_KEY, showPublic ? '1' : '0')
  }, [showPublic])

  const refresh = useCallback(() => {
    api.zoneRuns(scope).then((d) => setRuns(d.zone_runs)).catch((e) => setError(e.message))
    if (user) api.sessions().then((d) => setSessions(d.sessions)).catch(() => {})
  }, [scope, user])

  useEffect(() => { refresh() }, [refresh])

  // poll while an upload is parsing — new runs appear as parses land
  const parsing = sessions?.some((s) => s.status === 'parsing' || s.status === 'receiving')
  useEffect(() => {
    if (!parsing) return
    const t = setInterval(refresh, 2000)
    return () => clearInterval(t)
  }, [parsing, refresh])

  const visible = useMemo(() => {
    let list = runs || []
    if (raidsOnly) list = list.filter(isRaid)
    // signed out, everything IS public — filtering it would empty the page
    if (!showPublic && user) list = list.filter((r) => !r.via_public)
    return list
  }, [runs, raidsOnly, showPublic, user])
  const hidden = (runs?.length || 0) - visible.length
  const publicCount = useMemo(
    () => (runs || []).filter((r) => r.via_public).length, [runs])

  const multiChar = useMemo(
    () => new Set(visible.map((r) => r.character_name)).size > 1, [visible])
  const someoneElses = useMemo(() => visible.some((r) => !r.mine), [visible])

  // a run you cannot see is a run you cannot merge or delete: filtering out
  // drops it from the selection rather than leaving it armed off-screen
  const pickedRuns = useMemo(
    () => visible.filter((r) => picked.has(r.id)), [visible, picked])
  // merge/delete/share act on raids you own; someone else's shared night is
  // read-only, so it never arms those buttons
  const editable = useMemo(() => pickedRuns.filter((r) => r.mine), [pickedRuns])

  useEffect(() => {
    setPicked((s) => {
      const ids = new Set(visible.map((r) => r.id))
      const kept = [...s].filter((id) => ids.has(id))
      return kept.length === s.size ? s : new Set(kept)
    })
  }, [visible])

  async function perform(fn) {
    setBusy(true)
    setError(null)
    try { await fn() } catch (e) { setError(e.message) }
    setBusy(false)
    setConfirm(null)
    refresh()
  }

  const doMerge = () => perform(async () => {
    await api.mergeZoneRuns(editable.map((r) => r.id))
    setPicked(new Set())
  })

  const doUnmerge = () => perform(async () => {
    for (const r of editable) await api.unmergeZoneRun(r.id)
    setPicked(new Set())
  })

  const doDelete = () => perform(async () => {
    let empties = []
    const fps = []
    // restore is per character; offering Undo across a mixed selection would
    // put half the fights back and say nothing about the other half
    const chars = new Set(editable.map((r) => r.character_id))
    for (const r of editable) {
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
      /* Sorted by night (the default) the date sits in the group heading, so
         the cell would say it twice; sorted by zone or DPS there is no
         heading and the cell is the only place it appears. */
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
          {r.merged && <span className="badge" title="Merged by hand">merged</span>}
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
      render: (r) => <Sparkline values={r.spark} title="Raid DPS by fight" />,
    },
    /* Whose parse this is, named by the CHARACTER who logged it — that is who
       you raided with. Account names belong on the Groups and Admin pages, not
       in a raid list. It subsumes the Character column, so they never both
       show. */
    ...(someoneElses
      ? [{
          key: 'character_name', label: 'From', align: 'l',
          render: (r) => (r.mine
            ? <span className="muted">you</span>
            : <span>{r.character_name}</span>),
        }]
      : multiChar ? [{ key: 'character_name', label: 'Character', align: 'l' }] : []),
    {
      key: 'shared', label: 'Shared', sortable: false, align: 'l',
      // An empty cell is ambiguous — it reads as "unknown" when it means "nobody
      // else can see this". Say so. Only for raids you OWN: `shared_with` is
      // only populated for those, and printing "private" on somebody else's raid
      // that was shared *with you* would be exactly backwards.
      render: (r) => {
        const groups = r.shared_with ?? []
        if (r.mine && !r.public && groups.length === 0) {
          return <span className="muted" title="Only you can see this raid">private</span>
        }
        return (
          <span className="row" style={{ gap: 4 }}>
            {r.public && <span className="badge named" title="Readable without an account">public</span>}
            {groups.map((g) => (
              <span key={g.group_id} className="badge"
                    title={g.auto ? `${g.name} — every raid on this character` : g.name}>
                {g.name}
              </span>
            ))}
          </span>
        )
      },
    },
  ]

  return (
    <>
      <div className="pagehead">
        <h1>Raid Parses</h1>
        {!user && <span className="sub">Sign in to parse your own logs</span>}
        <span className="actions">
          {user && (
            <span className="chiprow">
              {[['all', 'All'], ['mine', 'Mine'], ['shared', 'Shared with me']].map(([k, label]) => (
                <button key={k} className={`chip ${scope === k ? 'on' : ''}`}
                        onClick={() => { setScope(k); setPicked(new Set()) }}>
                  {label}
                </button>
              ))}
            </span>
          )}
          {/* A switch, not a checkbox: it changes what the whole page is a list
              of, and you should be able to see which way it is set. */}
          <label
            className={`switch ${raidsOnly ? 'on' : ''}`}
            title={`${RAID_MIN_RAIDERS}+ raiders. Off also lists solo and group runs.`}
          >
            <input
              type="checkbox"
              checked={raidsOnly}
              onChange={(ev) => setRaidsOnly(ev.target.checked)}
            />
            <i className="track"><i className="knob" /></i>
            Raids only
            {raidsOnly && hidden > 0 && <span className="muted"> ({hidden} hidden)</span>}
          </label>
          {user && publicCount > 0 && (
            <label
              className={`switch ${showPublic ? 'on' : ''}`}
              title="Raids somebody else published. Yours stay listed either way."
            >
              <input
                type="checkbox"
                checked={showPublic}
                onChange={(ev) => setShowPublic(ev.target.checked)}
              />
              <i className="track"><i className="knob" /></i>
              Public
              <span className="muted"> ({publicCount})</span>
            </label>
          )}
          {user
            ? <Link className="btnlink" to="/import">Import a log</Link>
            : <Link className="btnlink" to="/login">Sign in</Link>}
        </span>
      </div>

      {sharing && (
        <ShareDialog runId={sharing} isAdmin={user?.role === 'admin'}
                     onClose={() => setSharing(null)} onChanged={refresh} />
      )}
      {parsing && <p className="muted">Parsing…</p>}
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
            {' '}Delete the uploaded log too?
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
            {' '}— {confirm.runs.reduce((s, r) => s + r.encounter_count, 0)} fights. The log
            stays, and you can undo right after.
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
          {!user ? 'Nothing published yet.'
            : scope === 'shared'
              ? 'Nothing shared with your groups yet.'
              : <>Nothing yet — <Link to="/import">import a log</Link>.</>}
        </p>
      )}
      {runs?.length > 0 && visible.length === 0 && (
        <p className="muted">
          Nothing with {RAID_MIN_RAIDERS}+ raiders.{' '}
          <button className="chip" onClick={() => setRaidsOnly(false)}>
            Show all {runs.length} runs
          </button>
        </p>
      )}

      {visible.length > 0 && (
        <div className="card">
          <SortableTable
            columns={columns}
            rows={visible}
            defaultSort={{ key: 'day', dir: 'desc' }}
            /* A raid night is the unit people remember, and three zones from
               one Saturday read as one night only if the list says so. Sorted
               by anything else the headings would be noise, so they go away —
               see SortableTable. */
            groupBy={{
              key: 'day',
              of: (r) => new Date(r.started_ts * 1000).toDateString(),
              label: (r) => {
                const day = visible.filter(
                  (x) => new Date(x.started_ts * 1000).toDateString()
                    === new Date(r.started_ts * 1000).toDateString())
                const fights = day.reduce((s, x) => s + x.encounter_count, 0)
                const named = day.reduce((s, x) => s + (x.named_count || 0), 0)
                const won = day.reduce((s, x) => s + (x.success_count || 0), 0)
                return (
                  <span className="daygroup">
                    <span className="d">{dayLabel(r.started_ts)}</span>
                    <span className="muted">{fmt.date(r.started_ts)}</span>
                    <span className="muted">
                      {day.length} zone{day.length === 1 ? '' : 's'} · {fights} fights
                      {named > 0 && ` · ${won}/${named} named`}
                    </span>
                  </span>
                )
              },
            }}
            rowKey={(r) => r.id}
            className="raidlist"
            wrapClass={visible.length > 14 ? 'sticky' : ''}
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
              {editable.length === 1 && (
                <button className="chip" disabled={busy}
                        onClick={() => setSharing(editable[0].id)}
                        title="Choose which groups can see this raid">
                  Share
                </button>
              )}
              {editable.length >= 2 && (
                <button className="chip" disabled={busy} onClick={doMerge}
                        title="Treat these as one raid">
                  Merge
                </button>
              )}
              {editable.some((r) => r.merged) && (
                <button className="chip" disabled={busy} onClick={doUnmerge}
                        title="Undo the merge">
                  Unmerge
                </button>
              )}
              {editable.length > 0 && (
                <button className="chip danger" disabled={busy}
                        onClick={() => setConfirm({ kind: 'delete', runs: editable })}>
                  Delete
                </button>
              )}
            </>
          }
        />
      )}
    </>
  )
}
