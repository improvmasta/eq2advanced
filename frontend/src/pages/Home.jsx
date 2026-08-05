import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import RaidCompare from '../components/RaidCompare.jsx'
import ShareDialog from '../components/ShareDialog.jsx'
import SortableTable from '../components/SortableTable.jsx'
import SourceFilter from '../components/SourceFilter.jsx'
import Sparkline from '../components/Sparkline.jsx'
import { api, fmt } from '../lib/api.js'
import { RAID_MIN_RAIDERS, isRaid } from '../lib/raids.js'

/* Landing page: every zone run as a row in one sortable table. Files are an
   ingest detail — the raid nights themselves are the navigation, and they read
   like the raid page's tables because they answer the same kind of question.

   The list is editable, because segmentation is a guess. A zone re-entry the
   game logged as two visits is one raid to the people who were there, and a
   pull nobody counts is noise in every total. Merge, unmerge and delete are
   remembered per FIGHT (backend `run_edits`), so an edit survives the reparse
   that a later backfill triggers. */

const DAY_MS = 86_400_000

const SIZE_KEY = 'eq2advanced-run-size'

/* How much of a night a parse holds — the same order the backend ranks them in
   (`raidmatch._score`), so the row you land on is the one the site would have
   picked for anyone else. Ties go to the first upload. */
const byCoverage = (a, b) => (b.encounter_count || 0) - (a.encounter_count || 0)
  || (b.combat_s || 0) - (a.combat_s || 0)
  || (b.raider_count || 0) - (a.raider_count || 0)
  || a.id - b.id
/* Two toggles rather than a three-way choice, because they PARTITION the list:
   a run is a raid or it isn't. Both on is what "all" used to mean, so a third
   button for it was a synonym taking up room. Raids on, group off. */
const SIZES = {
  raid: { label: 'Raids', of: isRaid, hint: `${RAID_MIN_RAIDERS}+ raiders` },
  group: {
    /* "Group" alone read as a SHARING group (the pills one control over); this
       button is about the size of the night, and a solo zone is on this side
       of the line too. */
    label: 'Solo/Group',
    of: (r) => !isRaid(r),
    hint: `Fewer than ${RAID_MIN_RAIDERS} raiders — group and solo runs`,
  },
}

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
  // which kinds of run this is a list of — a set, both on means everything
  const [sizes, setSizes] = useState(() => {
    const saved = (localStorage.getItem(SIZE_KEY) || '').split(',').filter((k) => SIZES[k])
    return new Set(saved.length ? saved : ['raid'])
  })
  const [sharing, setSharing] = useState(null)   // run id whose share panel is open
  /* WHERE a raid came from, as ONE filter: a set of `char:<id>`, `group:<id>`
     and `public` keys, OR'd, empty meaning everything you can see. The menu
     (SourceFilter) groups them into sections; this owns the answer.

     It was two controls before — a Shared-with-me switch beside a group filter
     — and they sat on the same axis. Worse, the switch silently changed what
     a group pill MEANT: on your own raid a group says "I sent it here", on
     somebody else's it says "it reached me through here". Listing sources says
     the true thing in both directions with nothing to cross-reference. */
  const [sources, setSources] = useState(() => new Set())
  const [myGroups, setMyGroups] = useState([])
  /* Which parse of a shared night to show, per raid — {raid_key: run id}, and
     only where the reader has said. Everything else follows the precedence in
     `chooseParse`. */
  const [parseOf, setParseOf] = useState(() => ({}))

  useEffect(() => { localStorage.setItem(SIZE_KEY, [...sizes].join(',')) }, [sizes])

  /* Always everything you can see; narrowing is the source filter's job, in
     the browser, so flipping it back is instant and never refetches. */
  const refresh = useCallback(() => {
    api.zoneRuns('all').then((d) => setRuns(d.zone_runs)).catch((e) => setError(e.message))
    if (user) api.sessions().then((d) => setSessions(d.sessions)).catch(() => {})
  }, [user])

  // the groups you're in, for the filter and for whether Manage is yours to offer
  useEffect(() => {
    if (!user) { setMyGroups([]); return }
    api.groups().then((d) => setMyGroups(d.groups || [])).catch(() => {})
  }, [user])

  useEffect(() => { refresh() }, [refresh])

  // poll while an upload is parsing — new runs appear as parses land
  const parsing = sessions?.some((s) => s.status === 'parsing' || s.status === 'receiving')
  useEffect(() => {
    if (!parsing) return
    const t = setInterval(refresh, 2000)
    return () => clearInterval(t)
  }, [parsing, refresh])

  /* Every group this run reaches, from whichever side you're on: yours name
     who you sent it to, somebody else's names the group that brought it here.
     One list, so the filter is one predicate. */
  const groupsOf = (r) => (r.mine ? r.shared_with : r.shared_via) ?? []

  const ofSize = (r) => [...sizes].some((k) => SIZES[k].of(r))

  const visible = useMemo(() => {
    let list = (runs || []).filter(ofSize)
    // several sources ticked is an OR: raids reaching you through ANY of them
    if (sources.size) {
      list = list.filter((r) => (r.mine && sources.has(`char:${r.character_id}`))
        || (r.via_public && sources.has('public'))
        || groupsOf(r).some((g) => sources.has(`group:${g.group_id}`)))
    }
    return list
  }, [runs, sizes, sources])
  /* One night, several uploaders: the backend has already said which rows are
     the same raid (`raid_key`) and which parse it would open (`primary`).
     Whose parse YOU open is decided here, because it depends on who you are:

       1. your own, if you have one — you uploaded it, its numbers are the ones
          you can check, and it is the one that survives the person who shared
          theirs leaving the group
       2. otherwise the site's pick, so two people talking about a raid are
          reading the same parse unless one of them says otherwise
       3. and whatever you picked from the row's menu beats both

     Clustering happens AFTER the filters, so narrowing to one group narrows the
     parses too rather than leaving a menu of raids that are no longer listed. */
  const chooseParse = useCallback((members) => {
    const mine = members.filter((r) => r.mine)
    const pool = mine.length ? mine : members
    // the site's pick can be filtered away, and two of your own characters can
    // both be in one raid — so there is always a fallback, and it is the
    // backend's rule again (raidmatch._score): the widest parse wins
    return (mine.length ? null : pool.find((r) => r.primary))
      || [...pool].sort(byCoverage)[0]
  }, [])

  /* The rows the table draws: one per raid, not one per parse. */
  const listRows = useMemo(() => {
    const byRaid = new Map()
    for (const r of visible) {
      const key = r.raid_key ?? r.id
      if (!byRaid.has(key)) byRaid.set(key, [])
      byRaid.get(key).push(r)
    }
    return [...byRaid].map(([key, members]) => {
      const shown = members.find((r) => r.id === parseOf[key]) || chooseParse(members)
      return members.length > 1 ? { ...shown, raid_key: key, alts: members } : shown
    }).sort((a, b) => b.started_ts - a.started_ts)
  }, [visible, parseOf, chooseParse])

  // switching parses keeps the row checked: it is the same raid either way
  const pickParse = (key, id, from) => {
    setParseOf((s) => ({ ...s, [key]: id }))
    setPicked((s) => {
      if (!s.has(from)) return s
      const next = new Set(s)
      next.delete(from)
      next.add(id)
      return next
    })
  }

  // what the SIZE toggles leave out, counted on their own — it is a claim
  // about those, not about the groups you happen to have checked
  const hidden = useMemo(
    () => (runs || []).filter((r) => !ofSize(r)).length, [runs, sizes])
  const publicCount = useMemo(
    () => (runs || []).filter((r) => r.via_public).length, [runs])
  /* The characters worth offering as a filter are the ones with raids in the
     list — a character you have claimed but never parsed on is a row that can
     only ever return nothing. Derived from the runs, so it costs no request. */
  const myChars = useMemo(() => {
    const seen = new Map()
    for (const r of runs || []) if (r.mine) seen.set(r.character_id, r.character_name)
    return [...seen].map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [runs])

  const toggleSize = (k) => {
    setSizes((s) => {
      const next = new Set(s)
      if (next.has(k)) next.delete(k); else next.add(k)
      return next
    })
    setPicked(new Set())
  }

  const toggleSource = (id) => {
    setSources((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
    setPicked(new Set())
  }

  const multiChar = useMemo(
    () => new Set(visible.map((r) => r.character_name)).size > 1, [visible])
  // the Parse column is the answer to a question only a shared night raises
  const anyAlts = useMemo(() => listRows.some((r) => r.alts), [listRows])

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

  const columns = [
    {
      /* Sorted by night (the default) the date sits in the group heading, so
         the cell would say it twice; sorted by zone or DPS there is no
         heading and the cell is the only place it appears. */
      key: 'day', label: 'Date', align: 'l',
      render: (r) => (
        <span className="runday">
          <span className="d">{dayLabel(r.started_ts)}</span>
          <span className="muted">{fmt.date(r.started_ts)}</span>
        </span>
      ),
      sortValue: (r) => r.started_ts,
    },
    {
      /* The widest column in the table, so a left-hugged header reads as
         belonging to the one before it — the label centres, the names don't. */
      key: 'zone', label: 'Zone', align: 'l', headAlign: 'c',
      render: (r) => (
        <span className="runzone">
          {r.zone || 'Unknown zone'}
          {r.merged && <span className="badge" title="Merged by hand">merged</span>}
          {r.guild && (
            <span className="badge guild" title="Majority guild of the roster, from Census">
              {r.guild}
            </span>
          )}
        </span>
      ),
      sortValue: (r) => r.zone || '',
    },
    {
      /* When it ran. Centred — a time range is text, not a figure, so a
         right-justified header over it reads like a mistake. */
      key: 'time', label: 'Time', align: 'c',
      render: (r) => fmt.timeRange(r.started_ts, r.ended_ts),
      sortValue: (r) => r.started_ts,
    },
    {
      /* Wall-clock length: it used to sit under the time range, where it could
         not be sorted on and read as an annotation of the range rather than as
         the figure it is. Its own column, beside the time in combat it is the
         denominator of. */
      key: 'length', label: 'Length', menuLabel: 'Raid length',
      render: (r) => (r.ended_ts ? fmt.durH(r.ended_ts - r.started_ts) : ''),
      sortValue: (r) => (r.ended_ts ? r.ended_ts - r.started_ts : null),
    },
    {
      key: 'combat_s', label: 'In Combat', menuLabel: 'Time in combat',
      render: (r) => fmt.dur(r.combat_s), sortValue: (r) => r.combat_s,
    },
    { key: 'encounter_count', label: 'Fights' },
    { key: 'raider_count', label: 'Raiders', render: (r) => r.raider_count || '' },
    {
      /* One number for the night: everything the raid did over its combat
         time. The per-fight peak was a trivia answer; this one ranks nights. */
      key: 'raid_dps', label: 'Raid DPS', menuLabel: 'Raid DPS',
      render: (r) => (r.raid_dps ? fmt.num(r.raid_dps) : ''),
      sortValue: (r) => r.raid_dps || null,
    },
    {
      key: 'spark', label: 'Timeline', sortable: false, align: 'c',
      render: (r) => <Sparkline values={r.spark} title="Raid DPS by fight" />,
    },
    /* Which of YOUR characters logged the night, and only once you have more
       than one. Whose parse a shared raid is was a column of its own ("From")
       and is gone: the Shared column already names the group it reached you
       through, which is the answer people actually wanted from it. */
    ...(multiChar ? [{ key: 'character_name', label: 'Character', align: 'l' }] : []),
    /* Whose parse you are reading, and only once a night has more than one.
       Several people in a raid all upload it, so the same evening arrives as
       several parses — the list shows ONE (yours first, otherwise the site's
       pick) and this is how you read somebody else's. A select rather than a
       row of names: the answer is one of a short list, and the current one has
       to be legible without opening anything. */
    ...(anyAlts ? [{
      key: 'parse', label: 'Parse', sortable: false, align: 'l',
      render: (r) => (r.alts ? (
        <span className="parsepick" onClick={(ev) => ev.stopPropagation()}>
          <select
            value={r.id}
            aria-label="Whose parse of this raid to show"
            title={`${r.alts.length} people parsed this raid`}
            onChange={(ev) => pickParse(r.raid_key, Number(ev.target.value), r.id)}
          >
            {[...r.alts].sort(byCoverage).map((a) => (
              <option key={a.id} value={a.id}>
                {a.character_name}{a.mine ? ' (yours)' : ''}
                {` — ${a.encounter_count} fight${a.encounter_count === 1 ? '' : 's'}`}
              </option>
            ))}
          </select>
        </span>
      ) : <span className="muted">{r.character_name}</span>),
    }] : []),
    {
      key: 'shared', label: 'Shared', sortable: false, align: 'l',
      /* Two directions, one column. Your own raid says who ELSE can see it —
         and an empty cell would read as "unknown" when it means "nobody", so
         it says "private". Somebody else's raid says which of YOUR groups
         brought it here (`shared_via`) — the row that used to sit blankest is
         exactly the one this column exists to explain. */
      render: (r) => {
        const groups = groupsOf(r)
        if (r.mine && !r.public && groups.length === 0) {
          return <span className="muted" title="Only you can see this raid">private</span>
        }
        return (
          // the pills are controls, so the row's own navigation stops here
          <span className="row" style={{ gap: 4 }} onClick={(ev) => ev.stopPropagation()}>
            {r.public && (r.mine || r.via_public) &&
              <span className="badge public" title="Readable without an account">public</span>}
            {groups.map((g) => (
              <button
                key={g.group_id}
                className={`badge grouppill ${sources.has(`group:${g.group_id}`) ? 'on' : ''}`}
                title={sources.has(`group:${g.group_id}`) ? `Stop filtering by ${g.name}`
                  : g.auto ? `${g.name} — every raid on this character. Click to filter by it.`
                    : `Show only what ${g.name} can see`}
                onClick={() => toggleSource(`group:${g.group_id}`)}
              >
                {g.name}
              </button>
            ))}
          </span>
        )
      },
    },
  ]

  /* The compare column takes real width, and the list would answer by growing
     a horizontal scrollbar. Instead it drops the columns you are least likely
     to be reading while comparing two nights — the shape, the roster size and
     time-in-combat are all in the comparison itself. */
  const comparing = pickedRuns.length >= 2
  const CONDENSED = ['spark', 'combat_s', 'raider_count', 'length']
  const listColumns = comparing
    ? columns.filter((c) => !CONDENSED.includes(c.key)) : columns

  return (
    <>
      <div className="pagehead">
        <h1>Raid Parses</h1>
        {/* Signed out, the subtitle is the pitch: what signing in gets you, and
            the three things people ask before they do it. */}
        {!user && (
          <span className="sub">
            Sign in to parse your own logs
            <span className="pitch">
              All data private unless shared by owner. No Discord or email
              address required for sign-up. ACT plugin available for automatic
              uploads.
            </span>
          </span>
        )}
        <span className="actions">
          {user
            ? <Link className="btnlink" to="/import">Import a log</Link>
            : <Link className="btnlink" to="/login">Sign in</Link>}
        </span>
      </div>

      {/* Three questions, three controls, one sticky line — and on the right,
          once anything is checked, what you can do about it. WHAT KIND of run
          (a raid, a group instance, everything), WHOSE (yours are always here,
          so the only question is whether everyone else's are too), and WHICH
          GROUPS (several at once — that's an OR, not a sequence of lists).
          Sorting is the table's own headers, where sorting always is. */}
      <div className="listtools">
        <span className="chiprow" role="group" aria-label="Run size">
          {Object.entries(SIZES).map(([k, def]) => (
            <button key={k} className={`chip ${sizes.has(k) ? 'on' : ''}`}
                    title={def.hint}
                    onClick={() => toggleSize(k)}>
              {def.label}
            </button>
          ))}
        </span>
        {hidden > 0 && <span className="muted">{hidden} hidden</span>}

        {user && <i className="vr" />}
        {user && (
          <SourceFilter
            chars={myChars}
            groups={myGroups}
            hasPublic={publicCount > 0}
            sources={sources}
            onToggle={toggleSource}
            onClear={() => { setSources(new Set()); setPicked(new Set()) }}
          />
        )}

        {pickedRuns.length > 0 && (
          <span className="seltools">
            <span className="sl">{pickedRuns.length} selected</span>
            <span className="muted">
              {pickedRuns.reduce((s, r) => s + r.encounter_count, 0)} fights ·{' '}
              {fmt.dur(pickedRuns.reduce((s, r) => s + r.combat_s, 0))}
            </span>
            {editable.length > 0 && (
              <button className="chip" disabled={busy}
                      onClick={() => setSharing(editable.map((r) => r.id))}
                      title={editable.length === 1
                        ? 'Choose which groups can see this raid'
                        : `Choose which groups can see these ${editable.length} raids`}>
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
                Delete{editable.length < pickedRuns.length ? ` ${editable.length}` : ''}
              </button>
            )}
            {/* Somebody else's raid offers nothing but reading it, and a row of
                missing buttons is not an explanation. */}
            {editable.length === 0 && (
              <span className="muted" title="You can read these, not change them">
                shared with you — read only
              </span>
            )}
            <button className="chip" onClick={() => setPicked(new Set())}>Clear</button>
          </span>
        )}
      </div>

      {sharing && (
        <ShareDialog runIds={sharing} isAdmin={user?.role === 'admin'}
                     onClose={() => setSharing(null)} onChanged={refresh} />
      )}
      {/* parsing state lives in the topnav Live pill, not under the title */}
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
            : <>Nothing yet — <Link to="/import">import a log</Link>.</>}
        </p>
      )}
      {/* Say which filter emptied the page, and offer to undo that one. */}
      {runs?.length > 0 && listRows.length === 0 && (
        <p className="muted">
          {sizes.size === 0 ? (
            <>
              Raids and Solo/Group are both off, so this is a list of nothing.{' '}
              <button className="chip" onClick={() => setSizes(new Set(['raid']))}>
                Show raids
              </button>
            </>
          ) : sources.size > 0 ? (
            <>
              Nothing from{' '}
              {[...myChars.filter((c) => sources.has(`char:${c.id}`)).map((c) => c.name),
                ...myGroups.filter((g) => sources.has(`group:${g.id}`)).map((g) => g.name),
                ...(sources.has('public') ? ['published raids'] : [])].join(' or ')}
              {' '}here.{' '}
              <button className="chip" onClick={() => setSources(new Set())}>
                Show everything
              </button>
            </>
          ) : (
            <>
              No {[...sizes].map((k) => SIZES[k].label.toLowerCase()).join(' or ')} runs here.{' '}
              <button className="chip" onClick={() => setSizes(new Set(['raid', 'group']))}>
                Show all {runs.length} runs
              </button>
            </>
          )}
        </p>
      )}

      {/* Checking a second raid opens the head-to-head beside the list, the way
          checking a second raider does on the raid page — and the list gives up
          its softer columns to make room rather than scrolling sideways. */}
      {listRows.length > 0 && (
        <div className={`raidpage ${comparing ? 'withcmp' : ''}`}>
        <div className="card">
          <SortableTable
            columns={listColumns}
            rows={listRows}
            defaultSort={{ key: 'day', dir: 'desc' }}
            /* A raid night is the unit people remember, and three zones from
               one Saturday read as one night only if the list says so. Sorted
               by zone, the same idea regrouped: every visit to Emerald Halls
               under one heading. Sorted by anything else the headings would be
               noise, so they go away — see SortableTable. */
            groupBy={[
              {
                key: 'day',
                of: (r) => new Date(r.started_ts * 1000).toDateString(),
                label: (r) => {
                  const day = listRows.filter(
                    (x) => new Date(x.started_ts * 1000).toDateString()
                      === new Date(r.started_ts * 1000).toDateString())
                  const fights = day.reduce((s, x) => s + x.encounter_count, 0)
                  return (
                    <span className="daygroup">
                      <span className="d">{dayLabel(r.started_ts)}</span>
                      <span className="muted">{fmt.date(r.started_ts)}</span>
                      <span className="muted">
                        {day.length} zone{day.length === 1 ? '' : 's'} · {fights} fights
                      </span>
                    </span>
                  )
                },
              },
              {
                key: 'zone',
                of: (r) => r.zone || 'Unknown zone',
                label: (r) => {
                  const zone = r.zone || 'Unknown zone'
                  const runs_ = listRows.filter((x) => (x.zone || 'Unknown zone') === zone)
                  const fights = runs_.reduce((s, x) => s + x.encounter_count, 0)
                  const best = Math.max(...runs_.map((x) => x.raid_dps || 0))
                  return (
                    <span className="daygroup">
                      <span className="d">{zone}</span>
                      <span className="muted">
                        {runs_.length} run{runs_.length === 1 ? '' : 's'} · {fights} fights
                        {best > 0 && ` · best ${fmt.num(best)} DPS`}
                      </span>
                    </span>
                  )
                },
              },
            ]}
            rowKey={(r) => r.id}
            className="raidlist"
            wrapClass={listRows.length > 14 ? 'sticky' : ''}
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

        {comparing && (
          // the column is only as wide as the raids in it — a two-raid table
          // has no business spanning half the screen
          <div className="panelcol" style={{ maxWidth: Math.min(180 + pickedRuns.length * 150, 620) }}>
            <RaidCompare
              runs={pickedRuns}
              // the deep comparison is a page now, so it's shareable and can
              // grow player columns — the checked raids seed its columns
              onCompareParses={() => navigate(
                `/compare?c=${pickedRuns.map((r) => `${r.id}:all:raid`).join(',')}`)}
              onRemove={(id) => setPicked((s) => {
                const next = new Set(s)
                next.delete(id)
                return next
              })}
            />
          </div>
        )}
        </div>
      )}
    </>
  )
}
