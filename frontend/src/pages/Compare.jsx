import { useEffect, useMemo, useState } from 'react'
import { api, fmt } from '../lib/api.js'
import { useQueryState } from '../lib/useQueryState.js'
import {
  MIN_PEERS, autoPct, critPct, damageDerived, procPct, rankColor, rankScale, rankTitle,
} from '../lib/stats.js'
import { ROLE_LABEL, roleOf } from '../lib/classes.js'
import { RAID_MIN_RAIDERS, isRaid } from '../lib/raids.js'
import BreakdownTable, { ParseStrip, actorRowsOf, breakdownRows } from '../components/BreakdownTable.jsx'
import SortableTable from '../components/SortableTable.jsx'
import Tabs from '../components/Tabs.jsx'
import { ActorName, ClassChip } from '../components/Identity.jsx'

/* Any parses, side by side: a column is a run + a fight selection + a subject,
   where the subject is the whole raid or a single player. Same player on two
   nights, two players on the same fight, raid against raid — all the same
   page. The whole comparison lives in ?c so a link IS the comparison.

   Each column is the ACTUAL parse — the way people compare in practice is a
   screenshot of their ACT window lined up against somebody else's, so a
   player column is their ability breakdown (the drilldown's BreakdownTable)
   and a raid column is the zone page's parse list. A merged metric table was
   built first and replaced by this: nobody compares "Crit % rows", they
   compare parses.

   Token grammar: `<runId>:<sel>:<subject>` joined by commas, where sel is
   `all` or fight ids joined by '.' (not '+', which URLSearchParams reads as a
   space) and subject is `raid` or a player name. EQ2 names are single-word
   alphanumeric, so the delimiters can't collide; anything malformed is
   dropped, never crashed on.

   Everything is computed from `/encounters/agg` alone — the run report's rows
   are frozen whole-run and would silently mismatch a per-fight selection. */

const healedOf = (a) => (a.heals || 0) + (a.wards_absorbed || 0)

/* One kind for the whole page: comparing this column's damage to that
   column's heals isn't a comparison. Damage and Healing are the two windows
   people actually line up. */
const PAGE_KINDS = [
  { key: 'damage', label: 'Damage', kinds: ['damage'] },
  { key: 'heal', label: 'Healing', kinds: ['heal', 'ward'] },
]

const parseTokens = (c) => (c || '')
  .split(',')
  .map((t) => {
    const parts = t.split(':')
    if (parts.length !== 3) return null
    const [run, sel, subject] = parts
    const runId = Number(run)
    if (!Number.isInteger(runId) || runId <= 0) return null
    if (sel !== 'all' && !/^\d+(\.\d+)*$/.test(sel)) return null
    if (!subject) return null
    return { runId, sel, subject }
  })
  .filter(Boolean)

const serialize = (tokens) =>
  tokens.map((t) => `${t.runId}:${t.sel}:${t.subject}`).join(',') || null

// success is 0/1/NULL — only a recorded 0 is a wipe; NULL is "never decided"
const fightLabel = (e) =>
  `${fmt.time(e.started_ts)}  ${e.name || 'Unknown'}${e.success === 0 ? ' (wipe)' : ''}`

/* The raid as one combatant: every raider's counters added up. A sum is right
   for the rates too — each raider's DPS runs over the same fight clock, so
   they add to the raid's DPS (see the run page's totals). */
const raidActor = (agg) => {
  const players = agg.actors.filter((a) => a.kind === 'player')
  if (!players.length) return null
  const sum = {}
  for (const k of ['damage', 'heals', 'wards_absorbed', 'cure_count',
    'power_fed', 'damage_taken', 'deaths']) {
    sum[k] = players.reduce((s, a) => s + (a[k] || 0), 0)
  }
  return sum
}

// crit/auto/proc/casts aggregate cleanly to the raid: sum the per-player rollups
const raidDerived = (agg, derived) => {
  const out = { total: 0, hits: 0, crits: 0, casts: 0, auto: 0, proc: 0, cast: 0 }
  let any = false
  for (const a of agg.actors) {
    if (a.kind !== 'player') continue
    const d = derived[a.key]
    if (!d) continue
    any = true
    for (const k of Object.keys(out)) out[k] += d[k] || 0
  }
  return any ? out : null
}

export default function Compare() {
  const [cQ, setC] = useQueryState('c')
  const tokens = useMemo(() => parseTokens(cQ), [cQ])
  const [kQ, setK] = useQueryState('k')
  const pageKind = PAGE_KINDS.find((k) => k.key === kQ) || PAGE_KINDS[0]
  const [combinePets, setCombinePets] = useState(false)

  const [runs, setRuns] = useState({})   // runId -> zone-run payload | {error}
  const [aggs, setAggs] = useState({})   // `${runId}:${sel}` -> agg | {error}

  // the run itself (meta + fight list); a 404 is the deep-link case — that
  // column says so, the rest of the page keeps working
  useEffect(() => {
    for (const t of tokens) {
      if (runs[t.runId]) continue
      api.zoneRun(t.runId)
        .then((d) => setRuns((s) => ({ ...s, [t.runId]: d })))
        .catch((e) => setRuns((s) => ({
          ...s,
          [t.runId]: { error: e.status === 404 ? 'notvisible' : e.message },
        })))
    }
  }, [tokens, runs])

  // the parse per (run, fight selection) — cachedGet dedupes repeats
  useEffect(() => {
    for (const t of tokens) {
      const run = runs[t.runId]
      if (!run || run.error) continue
      const key = `${t.runId}:${t.sel}`
      if (aggs[key]) continue
      const ids = t.sel === 'all'
        ? run.encounters.map((e) => e.id)
        : t.sel.split('.').map(Number)
      if (!ids.length) continue
      api.encountersAgg(ids)
        .then((d) => setAggs((s) => ({ ...s, [key]: d })))
        .catch((e) => setAggs((s) => ({ ...s, [key]: { error: e.message } })))
    }
  }, [tokens, runs, aggs])

  const cols = useMemo(() => tokens.map((t, i) => {
    const run = runs[t.runId]
    const agg = aggs[`${t.runId}:${t.sel}`]
    const col = { ...t, i, run, agg }
    if (!agg || agg.error) return col
    col.duration = Math.max(agg.encounter?.duration_s || 0, 1)
    const derived = damageDerived(agg.abilities)
    if (t.subject === 'raid') {
      col.actor = raidActor(agg)
      col.derived = raidDerived(agg, derived)
    } else {
      col.actor = agg.actors.find((a) => a.kind === 'player' && a.name === t.subject) || null
      col.derived = derived[col.actor?.key] || null
    }
    return col
  }), [tokens, runs, aggs])

  const patch = (i, changes) => {
    const next = tokens.map((t, j) => (j === i ? { ...t, ...changes } : t))
    setC(serialize(next))
  }
  const remove = (i) => setC(serialize(tokens.filter((_, j) => j !== i)))
  const add = (token) => setC(serialize([...tokens, token]))

  return (
    <div className="comparepage">
      <div className="pagehead">
        <h1>Compare</h1>
      </div>
      <p className="note">
        Any parses, side by side — whole raids or single players, from any
        night you can see. The link in the address bar is the comparison.
      </p>
      {cols.length > 0 && (
        <Tabs
          tabs={PAGE_KINDS.map((k) => ({ key: k.key, label: k.label }))}
          value={pageKind.key}
          onChange={(k) => setK(k === 'damage' ? null : k)}
        />
      )}
      <div className="comparelayout">
        {/* The picker comes FIRST, so it stays put on the left edge as parses
            stack up beside it. It used to trail the columns, which meant the
            control you use again and again walked further right with every
            raid you added — and off the screen entirely by the third. */}
        <AddColumn
          onAdd={add}
          prominent={cols.length <= 1}
          anchor={tokens[0]?.subject ?? null}
        />
        {cols.length > 0 && (
          <div className="parsecols">
            {cols.map((c) => (
              <ParseCol
                key={c.i}
                col={c}
                kinds={pageKind.kinds}
                combinePets={combinePets}
                onCombinePets={setCombinePets}
                patch={patch}
                remove={remove}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/* One parse, one card: the pickers that define the column, then the table. */
function ParseCol({ col, kinds, combinePets, onCombinePets, patch, remove }) {
  const { run, agg } = col
  if (run?.error) {
    return (
      <div className="card parsecol">
        <div className="pchead">
          <span className="z">Run #{col.runId}</span>
          <span className="muted">
            {run.error === 'notvisible'
              ? 'Not visible to you — the owner may need to share or publish it'
              : run.error}
          </span>
          <button className="chip" onClick={() => remove(col.i)}>Remove</button>
        </div>
      </div>
    )
  }
  return (
    <div className="card parsecol">
      <ColHead col={col} patch={patch} remove={remove}
               combinePets={col.subject === 'raid' ? null : combinePets}
               onCombinePets={onCombinePets} />
      {!agg && <p className="muted">Loading parse…</p>}
      {agg?.error && <p className="err">{agg.error}</p>}
      {agg && !agg.error && (col.subject === 'raid' ? (
        <>
          {/* a raid list has no pinned All row, so this is the only place its
              totals appear — a player column gets them from the table */}
          <ParseStrip actor={col.actor} derived={col.derived}
                      kind={kinds[0]} duration={col.duration} combat />
          <RaidList agg={agg} duration={col.duration} kinds={kinds} />
        </>
      ) : col.actor ? (
        <PlayerParse col={col} kinds={kinds} combinePets={combinePets} />
      ) : (
        <p className="muted">Not in these fights.</p>
      ))}
    </div>
  )
}

function PlayerParse({ col, kinds, combinePets }) {
  const rows = useMemo(
    () => breakdownRows(actorRowsOf(col.agg.abilities, col.actor.key), kinds, combinePets),
    [col.agg, col.actor.key, kinds, combinePets])
  return (
    <>
      {rows.length ? (
        <BreakdownTable
          rows={rows}
          kinds={kinds}
          duration={col.duration}
          linkHover
          wrapClass="parsewin"
          fitViewport
          prefsKey="compare"
          defaultHidden={['total', 'share', 'to_hit_pct', 'median', 'min', 'press_delay_s']}
        />
      ) : (
        <p className="muted">Nothing on this tab.</p>
      )}
    </>
  )
}

/* A raid column is the zone page's parse list — same columns, same rank
   coloring — because that list already IS how a whole raid's parse is read. */
function RaidList({ agg, duration, kinds }) {
  const derived = useMemo(() => damageDerived(agg.abilities), [agg])
  const players = useMemo(() => agg.actors.filter((a) => a.kind === 'player'), [agg])
  const damage = kinds[0] === 'damage'
  const rows = useMemo(() => (damage
    ? players.filter((a) => (a.damage || 0) > 0)
    : players.filter((a) => healedOf(a) > 0 || (a.cure_count || 0) > 0
        || (a.power_fed || 0) > 0 || (a.rez_casts || 0) > 0)), [players, damage])
  const raidDamage = players.reduce((s, a) => s + (a.damage || 0), 0)

  // same peer rule as the zone page: a row's PLACE among the same-role raiders
  // in this column, and no color at all without a role or without MIN_PEERS of
  // them — the old fallback to "everyone" put a healer and an unclassified
  // raider on different yardsticks in the same column
  const rankPool = (a) => {
    const role = roleOf(a)
    if (!role) return null
    const pool = rows.filter((p) => roleOf(p) === role)
    return pool.length >= MIN_PEERS ? { pool, label: ROLE_LABEL[role].toLowerCase() } : null
  }
  const rankAgainst = (get, opts) => (a) => {
    const group = rankPool(a)
    if (!group) return undefined
    const color = rankColor(rankScale(get(a), group.pool.map(get), opts))
    return color ? { color } : undefined
  }
  const rankTitleAgainst = (get) => (a) => {
    const group = rankPool(a)
    return group ? rankTitle(get(a), group.pool.map(get), group.label) : undefined
  }

  const nameCol = {
    key: 'name', label: 'Name', align: 'l', fixed: true,
    render: (a) => <ActorName actor={a} />,
    sortValue: (a) => a.name,
  }
  const dpsOf = (a) => (a.damage || 0) / duration
  const damageCols = [
    nameCol,
    {
      key: 'dps', label: 'DPS',
      render: (a) => (a.damage ? fmt.num2(dpsOf(a)) : ''),
      sortValue: dpsOf,
      cellStyle: rankAgainst(dpsOf),
      cellTitle: rankTitleAgainst(dpsOf),
    },
    { key: 'damage', label: 'Damage', render: (a) => fmt.num(a.damage), sortValue: (a) => a.damage || 0 },
    {
      key: 'share', label: 'Dmg %',
      render: (a) => (a.damage > 0 && raidDamage
        ? `${Math.round((a.damage / raidDamage) * 100)}%` : ''),
      sortValue: (a) => a.damage || 0,
    },
    {
      key: 'crit', label: 'Crit %',
      render: (a) => { const v = critPct(derived[a.key]); return v != null ? `${Math.round(v)}%` : '' },
      sortValue: (a) => critPct(derived[a.key]),
      cellStyle: rankAgainst((a) => critPct(derived[a.key])),
      cellTitle: rankTitleAgainst((a) => critPct(derived[a.key])),
    },
    {
      key: 'auto', label: 'Auto %',
      render: (a) => { const v = autoPct(derived[a.key]); return v != null ? `${Math.round(v)}%` : '' },
      sortValue: (a) => autoPct(derived[a.key]),
    },
    {
      key: 'proc', label: 'Proc %',
      render: (a) => { const v = procPct(derived[a.key]); return v != null && v > 0 ? `${Math.round(v)}%` : '' },
      sortValue: (a) => procPct(derived[a.key]),
    },
    {
      key: 'avg_delay', label: 'AvgDelay',
      render: (a) => (a.avg_delay_s != null ? a.avg_delay_s.toFixed(2) : ''),
      sortValue: (a) => a.avg_delay_s ?? null,
    },
    { key: 'deaths', label: 'Deaths', render: (a) => a.deaths || '' },
  ]
  const healingCols = [
    nameCol,
    {
      key: 'hps', label: 'HPS',
      render: (a) => (healedOf(a) ? fmt.num2(healedOf(a) / duration) : ''),
      sortValue: (a) => healedOf(a) / duration,
      cellStyle: rankAgainst((a) => healedOf(a) / duration),
      cellTitle: rankTitleAgainst((a) => healedOf(a) / duration),
    },
    { key: 'healed', label: 'Healed', render: (a) => (healedOf(a) ? fmt.num(healedOf(a)) : ''), sortValue: healedOf },
    { key: 'heals', label: 'Heals', render: (a) => (a.heals ? fmt.num(a.heals) : ''), sortValue: (a) => a.heals || 0 },
    { key: 'wards', label: 'Wards', render: (a) => (a.wards_absorbed ? fmt.num(a.wards_absorbed) : ''), sortValue: (a) => a.wards_absorbed || 0 },
    { key: 'cure_count', label: 'Cures', render: (a) => a.cure_count || '' },
    { key: 'power_fed', label: 'PowerRepl', render: (a) => (a.power_fed ? fmt.num(a.power_fed) : '') },
    { key: 'rez_casts', label: 'Rezzes', render: (a) => a.rez_casts || '' },
  ]

  if (!rows.length) return <p className="muted">Nothing on this tab.</p>
  return (
    <SortableTable
      columns={damage ? damageCols : healingCols}
      rows={rows}
      defaultSort={{ key: damage ? 'dps' : 'hps', dir: 'desc' }}
      wrapClass="parsewin"
      fitViewport
      prefsKey={`compareraid:${kinds[0]}`}
      defaultHidden={damage
        ? ['damage', 'share', 'auto', 'proc', 'avg_delay']
        : ['healed', 'heals', 'wards']}
      rowKey={(a) => a.key}
    />
  )
}

function ColHead({ col, patch, remove, combinePets, onCombinePets }) {
  const { run, agg } = col
  const zr = run?.zone_run
  const fights = run?.encounters
  const players = agg && !agg.error
    ? agg.actors.filter((a) => a.kind === 'player') : []
  const multi = col.sel !== 'all' && col.sel.includes('.')
  return (
    <div className="pchead">
      {/* one line: who this parse is, what they are, and the pet control —
          Columns is placed on the same line by CSS (SortableTable renders it
          just above its table) */}
      <div className="pctitle">
        <button className="cmphead" title="Remove from comparison" onClick={() => remove(col.i)}>
          {col.subject === 'raid' ? (zr?.zone || `Run #${col.runId}`) : col.subject}
        </button>
        {col.subject !== 'raid' && col.actor && <ClassChip actor={col.actor} />}
        {combinePets != null && col.actor && (
          <label className={`chip toggle big ${combinePets ? 'on' : ''}`}
                 title="One line per pet kit, on every parse here">
            <input
              type="checkbox"
              checked={combinePets}
              onChange={(e) => onCombinePets(e.target.checked)}
            /> Combine pets
          </label>
        )}
      </div>
      <span className="muted">
        {zr ? `${fmt.date(zr.started_ts)} — ${zr.character_name}'s parse` : '…'}
      </span>
      <select
        value={col.sel}
        disabled={!fights}
        aria-label="Which fights"
        onChange={(ev) => patch(col.i, { sel: ev.target.value })}
      >
        <option value="all">
          Whole raid{fights ? ` — ${fights.length} fights` : ''}
        </option>
        {multi && (
          <option value={col.sel}>Selection — {col.sel.split('.').length} fights</option>
        )}
        {fights?.some((e) => e.is_named) && (
          <optgroup label="Named">
            {fights.filter((e) => e.is_named).map((e) => (
              <option key={e.id} value={e.id}>{fightLabel(e)}</option>
            ))}
          </optgroup>
        )}
        <optgroup label="Every fight">
          {fights?.map((e) => (
            <option key={e.id} value={e.id}>{fightLabel(e)}</option>
          ))}
        </optgroup>
      </select>
      <select
        value={col.subject}
        disabled={!players.length}
        aria-label="Whole raid or one player"
        onChange={(ev) => patch(col.i, { subject: ev.target.value })}
      >
        <option value="raid">Whole raid</option>
        {players.map((a) => (
          <option key={a.key} value={a.name}>{a.name}</option>
        ))}
        {/* keep a name that isn't in this fight selectable, so flipping
            fights doesn't silently rewrite who the column is about */}
        {col.subject !== 'raid' && !players.some((a) => a.name === col.subject) && (
          <option value={col.subject}>{col.subject}</option>
        )}
      </select>
    </div>
  )
}

const RESULT_CAP = 12       // a picker is a shortlist; past this, narrow it
const GROUPRUNS_KEY = 'eq2advanced-compare-groupruns'
const dayKey = (ts) => new Date(ts * 1000).toDateString()

/* The picker is a faceted view over the list the page already holds — the whole
   visible list with rosters is ~100 KB, smaller than one parse it will fetch
   afterwards, so there is nothing to ask the server and nothing to debounce.
   Typing narrows; the dropdowns pin; each dropdown only offers values that
   still leave results, so no combination can strand you on an empty list.

   A raid click ADDS, because the anchor column already said what kind of
   comparison this is. A player click SELECTS instead and fills the dropdowns
   in, because "which of them" is still an open question at that point. */
function AddColumn({ onAdd, prominent, anchor }) {
  const playerMode = !!anchor && anchor !== 'raid'
  const [list, setList] = useState(null)
  const [listErr, setListErr] = useState(null)
  const [q, setQ] = useState('')
  const [zone, setZone] = useState('')
  const [date, setDate] = useState('')
  const [guild, setGuild] = useState('')
  const [player, setPlayer] = useState(playerMode ? anchor : '')
  const [picked, setPicked] = useState(null)   // player flow: the night, not yet the name
  const [subject, setSubject] = useState('')
  /* Raids only, until asked otherwise. Comparing parses is a raid activity —
     a solo dummy parse next to a raid night is noise, and there are far more of
     them, so they crowd the shortlist a 12-row cap already makes tight. It is a
     global toggle rather than a facet: it says what this picker is a picker
     OF, so it never gets skipped when a dropdown asks what would still be here
     without it, and it is remembered because the answer doesn't change from
     one visit to the next. */
  const [groupRuns, setGroupRuns] = useState(
    () => localStorage.getItem(GROUPRUNS_KEY) === '1')
  useEffect(() => { localStorage.setItem(GROUPRUNS_KEY, groupRuns ? '1' : '0') },
            [groupRuns])

  useEffect(() => {
    api.zoneRuns('all', { roster: true })
      .then((d) => {
        // one row per NIGHT, same rule as the raid list: your own parse of a
        // shared night wins, otherwise the site's pick
        const byNight = new Map()
        for (const r of d.zone_runs) {
          const k = r.raid_key ?? r.id
          const cur = byNight.get(k)
          if (!cur || (r.mine && !cur.mine) || (!cur.mine && r.primary)) byNight.set(k, r)
        }
        setList([...byNight.values()].sort((a, b) => b.started_ts - a.started_ts))
      })
      .catch((e) => setListErr(e.message))
  }, [])

  // "me across nights" is the zero-setup default when a player anchors the page
  useEffect(() => { setPlayer(playerMode ? anchor : '') }, [anchor, playerMode])

  const ql = q.trim().toLowerCase()

  /* One night against one set of conditions. `skip` leaves a facet out so a
     dropdown can ask "what would still be here without me" — that is what makes
     the options cross-narrow instead of offering dead ends. */
  const passes = (r, skip) => {
    // not skippable: `skip` is for FACETS asking what they'd leave behind, and
    // this one isn't a facet — a dropdown must not offer a solo night the list
    // will then refuse to show
    if (!groupRuns && !isRaid(r)) return false
    if (skip !== 'zone' && zone && (r.zone || '') !== zone) return false
    if (skip !== 'date' && date && dayKey(r.started_ts) !== date) return false
    if (skip !== 'guild' && guild && (r.guild || '') !== guild) return false
    if (skip !== 'player' && player
        && !(r.roster || []).some((n) => n.toLowerCase() === player.toLowerCase())) return false
    if (skip !== 'q' && ql) {
      // zones and guilds get typed from the middle ("freeth", "unrest"); a
      // person's name gets typed from the front
      const text = `${r.zone || ''} ${r.guild || ''}`.toLowerCase()
      if (!text.includes(ql)
          && !(r.roster || []).some((n) => n.toLowerCase().startsWith(ql))) return false
    }
    return true
  }

  const results = useMemo(
    () => (list || []).filter((r) => passes(r)),
    [list, ql, zone, date, guild, player, groupRuns])

  const optionsFor = (facet) => (list || []).filter((r) => passes(r, facet))
  const zones = useMemo(() => [...new Set(
    optionsFor('zone').map((r) => r.zone || ''))].filter(Boolean).sort(),
  [list, ql, date, guild, player, groupRuns])
  const dates = useMemo(() => [...new Map(
    optionsFor('date').map((r) => [dayKey(r.started_ts), r.started_ts])).entries()]
    .sort((a, b) => b[1] - a[1]),
  [list, ql, zone, guild, player, groupRuns])
  const guilds = useMemo(() => [...new Set(
    optionsFor('guild').map((r) => r.guild || ''))].filter(Boolean).sort(),
  [list, ql, zone, date, player, groupRuns])
  const players = useMemo(() => [...new Set(
    optionsFor('player').flatMap((r) => r.roster || []))].sort(),
  [list, ql, zone, date, guild, groupRuns])

  // nothing is tagged yet (a fresh backfill) — then the control simply does not
  // exist, rather than sitting there empty asking to be clicked
  const anyGuild = (list || []).some((r) => r.guild)

  const clear = () => {
    setQ(''); setZone(''); setDate(''); setGuild('')
    setPlayer(playerMode ? anchor : ''); setPicked(null)
  }

  const addRaid = (r) => {
    onAdd({ runId: r.id, sel: 'all', subject: 'raid' })
    // the facets stay: stacking three nights of the same guild is three clicks
    setQ('')
  }

  const selectNight = (r) => {
    setPicked(r)
    setZone(r.zone || '')
    setDate(dayKey(r.started_ts))
    if (r.guild) setGuild(r.guild)
    const roster = r.roster || []
    const inRoster = (n) => n && roster.some((m) => m.toLowerCase() === n.toLowerCase())
    setSubject(inRoster(player) ? player : inRoster(anchor) ? anchor : 'raid')
  }

  // an anchored player column, or a player facet set on an empty page, both say
  // this comparison is about a person — so the night is a step, not the answer
  const wantsPlayer = playerMode || !!player

  return (
    <div className={`card addcol${prominent ? ' prominent' : ''}`}>
      <h2>Add a parse</h2>
      <p className="note">
        Type a zone, a guild or a name — or pin it down with the dropdowns.
      </p>
      {listErr && <p className="err">{listErr}</p>}
      <input
        type="search"
        placeholder="Search raids…"
        value={q}
        aria-label="Search raids by zone, guild or player"
        onChange={(ev) => setQ(ev.target.value)}
      />
      <label className={`chip toggle big ${groupRuns ? 'on' : ''}`}
             title={`Off, only raids (${RAID_MIN_RAIDERS}+ raiders) are offered`}>
        <input type="checkbox" checked={groupRuns}
               onChange={(ev) => setGroupRuns(ev.target.checked)} /> Solo/group runs
      </label>
      <select value={zone} aria-label="Zone" disabled={!list}
              onChange={(ev) => { setZone(ev.target.value); setPicked(null) }}>
        <option value="">{list ? 'Any zone' : 'Loading raids…'}</option>
        {zones.map((z) => <option key={z} value={z}>{z}</option>)}
      </select>
      <select value={date} aria-label="Date" disabled={!list}
              onChange={(ev) => { setDate(ev.target.value); setPicked(null) }}>
        <option value="">Any date</option>
        {dates.map(([k, ts]) => <option key={k} value={k}>{fmt.date(ts)}</option>)}
      </select>
      {anyGuild && (
        <select value={guild} aria-label="Guild" disabled={!list}
                onChange={(ev) => { setGuild(ev.target.value); setPicked(null) }}>
          <option value="">Any guild</option>
          {guilds.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
      )}
      {anchor !== 'raid' && (
        <select value={player} aria-label="Player" disabled={!list}
                onChange={(ev) => { setPlayer(ev.target.value); setPicked(null) }}>
          <option value="">Any player</option>
          {players.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      )}

      {list && !results.length && (
        <>
          <p className="muted">Nothing matches.</p>
          <button className="chip" onClick={clear}>Clear</button>
        </>
      )}
      {results.slice(0, RESULT_CAP).map((r) => (
        <button
          key={r.id}
          className={`pickrow${picked?.id === r.id ? ' on' : ''}`}
          onClick={() => (wantsPlayer ? selectNight(r) : addRaid(r))}
        >
          <span>{fmt.date(r.started_ts)}</span>
          <span className="z">{r.zone || 'Unknown zone'}</span>
          {r.guild && <span className="badge guild">{r.guild}</span>}
          <span className="muted">
            {r.encounter_count} fights · {r.character_name}'s parse
          </span>
        </button>
      ))}
      {results.length > RESULT_CAP && (
        <p className="muted">+{results.length - RESULT_CAP} more — narrow it down.</p>
      )}

      {picked && wantsPlayer && (
        <div className="confirm">
          <select value={subject} aria-label="Whole raid or one player"
                  onChange={(ev) => setSubject(ev.target.value)}>
            <option value="raid">Whole raid</option>
            {(picked.roster || []).map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <button className="chip on"
                  onClick={() => { onAdd({ runId: picked.id, sel: 'all', subject }); setPicked(null) }}>
            Add
          </button>
        </div>
      )}
    </div>
  )
}
