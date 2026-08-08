import { useEffect, useMemo, useState } from 'react'
import { api, fmt } from '../lib/api.js'
import { useQueryState } from '../lib/useQueryState.js'
import {
  MIN_PEERS, autoPct, critPct, damageDerived, procPct, rankColor, rankScale, rankTitle,
} from '../lib/stats.js'
import { ROLE_LABEL, classColor, classLabel, roleOf } from '../lib/classes.js'
import { RAID_MIN_RAIDERS, isRaid } from '../lib/raids.js'
import BreakdownTable, {
  CompositionStrip, KIND_FILTERS, ParseStrip, actorRowsOf, availKinds, breakdownRows,
} from '../components/BreakdownTable.jsx'
import Picker from '../components/Picker.jsx'
import ShotDrop from '../components/ShotDrop.jsx'
import ShotViewer, { ShotThumb, shotTitle } from '../components/ShotViewer.jsx'
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

/* Damage / Heals / Power / … is a property of the PARSE, not of the page: a
   column carries its own tabs, the same set and the same order the raid
   page's drilldown offers, because a column IS that parse. A page-wide
   Damage|Healing tab pair used to rule every column at once, on the argument
   that comparing one column's damage to another's heals is not a comparison —
   true, and still the reader's call to make. Only the tabs a parse has rows
   for are drawn (`availKinds`): a fury's column has no Threat tab to click. */

/* A raid column is the zone page's parse list, which is written for two of
   those views and only those — the list has a DPS shape and an HPS shape. */
const RAID_KINDS = KIND_FILTERS.filter((f) => f.key === 'damage' || f.key === 'heal')
const raidKinds = (agg) => {
  const players = (agg?.actors || []).filter((a) => a.kind === 'player')
  const has = {
    damage: players.some((a) => (a.damage || 0) > 0),
    heal: players.some((a) => healedOf(a) > 0 || (a.cure_count || 0) > 0
      || (a.power_fed || 0) > 0 || (a.rez_casts || 0) > 0),
  }
  return RAID_KINDS.filter((f) => has[f.key])
}

/* A column is either a parse this site holds — `<runId>:<sel>:<subject>` — or
   one imported from an ACT screenshot, `shot:<id>:parse`. The second form
   keeps the same three-field shape so the CSV, the ordering and the remove
   logic never have to care which kind a column is; only the fetch and the
   table differ. `shot` can't collide with a run id, which is always a
   number. */
const parseTokens = (c) => (c || '')
  .split(',')
  .map((t) => {
    const parts = t.split(':')
    if (parts.length !== 3) return null
    if (parts[0] === 'shot') {
      const shotId = Number(parts[1])
      return Number.isInteger(shotId) && shotId > 0 ? { shotId } : null
    }
    const [run, sel, subject] = parts
    const runId = Number(run)
    if (!Number.isInteger(runId) || runId <= 0) return null
    if (sel !== 'all' && !/^\d+(\.\d+)*$/.test(sel)) return null
    if (!subject) return null
    return { runId, sel, subject }
  })
  .filter(Boolean)

const serialize = (tokens) =>
  tokens.map((t) => (t.shotId
    ? `shot:${t.shotId}:parse`
    : `${t.runId}:${t.sel}:${t.subject}`)).join(',') || null

/* An imported parse, in the shape the breakdown table already renders. Every
   column it draws is either carried by the shot or derived the same way it is
   for a real parse (Average is total/hits, the rate is total/duration), so an
   imported column and a real one are computed alike rather than merely
   looking alike. Crit % is the exception worth naming: a shot carries the
   PERCENTAGE, so the crit COUNT is reconstructed from it — which is exactly
   what the table's own All row then re-weights. */
const shotBreakdownRows = (shot) => (shot.rows || []).map((r) => ({
  ability: r.name,
  kind: shot.kind === 'heal' ? 'heal' : 'damage',
  source_key: `shot:${shot.id}`,
  source_name: shot.character_name || 'Imported parse',
  source_kind: 'player',
  total: r.damage ?? r.healed ?? 0,
  hits: r.hits ?? 0,
  crits: r.crit_pct != null && r.hits ? Math.round((r.crit_pct / 100) * r.hits) : 0,
  swings: r.swings ?? 0,
  casts: 0,
  zero_hits: 0,
  presses: 0,
  min: r.min_hit ?? null,
  max: r.max_hit ?? null,
  median: r.median ?? null,
  to_hit_pct: r.to_hit ?? null,
  avg_delay_s: r.avg_delay ?? null,
  press_delay_s: null,
}))

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
  const [combinePets, setCombinePets] = useState(false)
  /* Which tab each column is on, by position. It is deliberately NOT in the
     token: `?c` says what the comparison is OF, and a tab is how the reader
     is looking at it. Removing a column takes its view with it, or every
     column to its right would inherit the wrong one. */
  const [views, setViews] = useState([])

  const [runs, setRuns] = useState({})   // runId -> zone-run payload | {error}
  const [aggs, setAggs] = useState({})   // `${runId}:${sel}` -> agg | {error}
  const [shots, setShots] = useState({}) // shotId -> imported parse | {error}

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

  /* An imported parse fetches whole — it is one row, and it is already the
     numbers. A shot the viewer can't see (someone else's link) fails in its
     own column exactly as an unshared run does. */
  useEffect(() => {
    for (const t of tokens) {
      if (!t.shotId || shots[t.shotId]) continue
      api.parseshot(t.shotId)
        .then((d) => setShots((s) => ({ ...s, [t.shotId]: d })))
        .catch((e) => setShots((s) => ({
          ...s,
          [t.shotId]: { error: e.status === 404 ? 'notvisible' : e.message },
        })))
    }
  }, [tokens, shots])

  // the parse per (run, fight selection) — cachedGet dedupes repeats
  useEffect(() => {
    for (const t of tokens) {
      if (t.shotId) continue
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
    if (t.shotId) {
      const shot = shots[t.shotId]
      const col = { ...t, i, shot }
      if (!shot || shot.error) return col
      // A screenshot has no clock of its own beyond the one in its title bar;
      // without it the rate column would be meaningless, so it says so rather
      // than dividing by a guess.
      col.duration = shot.duration_s || null
      col.rows = shotBreakdownRows(shot)
      return col
    }
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
  }), [tokens, runs, aggs, shots])

  const patch = (i, changes) => {
    const next = tokens.map((t, j) => (j === i ? { ...t, ...changes } : t))
    setC(serialize(next))
  }
  const remove = (i) => {
    setC(serialize(tokens.filter((_, j) => j !== i)))
    setViews((v) => v.filter((_, j) => j !== i))
  }
  const add = (token) => setC(serialize([...tokens, token]))
  const setView = (i, key) => setViews((v) => {
    const next = v.slice()
    next[i] = key
    return next
  })

  return (
    <div className="comparepage">
      <div className="pagehead">
        <h1>Compare</h1>
      </div>
      {/* The search is a BAND across the top, not a column beside the parses:
          it is what you do first and then keep doing, and the parses it adds
          get the whole width to line up in underneath. */}
      <AddBar onAdd={add} anchor={tokens[0]?.subject ?? null} />
      <div className="parsecols">
        {cols.map((c) => (c.shotId ? (
          <ShotCol key={c.i} col={c} remove={remove} />
        ) : (
          <ParseCol
            key={c.i}
            col={c}
            view={views[c.i]}
            onView={setView}
            combinePets={combinePets}
            onCombinePets={setCombinePets}
            patch={patch}
            remove={remove}
          />
        )))}
        {/* The empty slot IS the drop box, and it is a COLUMN: it sits where
            the next parse will land and walks right as parses fill in from the
            left. Two things were competing to say "add one here" — a
            placeholder column and a drop target up in the search band — and
            they are the same statement, so they are one control. */}
        <ShotDrop
          className="card parsecol dropslot"
          label={<p className="muted">Search or add a screenshot to compare…</p>}
          onImported={(shot) => add({ shotId: shot.id })}
        />
      </div>
    </div>
  )
}

// every column carries the same ✕, wherever it is in the head
function CloseCol({ i, remove }) {
  return (
    <button className="chip closex" title="Remove this parse"
            aria-label="Remove this parse" onClick={() => remove(i)}>✕</button>
  )
}

/* An imported parse, as a column. Deliberately the SAME table as a real
   parse — that is the whole point of importing one — with the differences
   stated instead of hidden: where it came from, and that its numbers are only
   as good as an OCR pass over a screenshot. */
function ShotCol({ col, remove }) {
  const { shot } = col
  // declared before the early returns — a column that failed to load has no
  // picture to open, but hooks do not get to be conditional
  const [viewing, setViewing] = useState(false)
  if (shot?.error) {
    return (
      <div className="card parsecol">
        <div className="pchead">
          <div className="pctitle">
            <span className="z">Imported parse #{col.shotId}</span>
            <CloseCol i={col.i} remove={remove} />
          </div>
          <span className="muted">
            {shot.error === 'notvisible'
              ? 'Not yours — an imported parse belongs to whoever imported it'
              : shot.error}
          </span>
        </div>
      </div>
    )
  }
  if (!shot) return <div className="card parsecol"><p className="muted">Reading…</p></div>

  /* A screenshot is of ONE view, so that view is the column and there are no
     tabs to offer: what the image says is all there is. */
  const kind = shot.kind === 'heal' ? 'heal' : 'damage'
  const kinds = kind === 'heal' ? ['heal', 'ward'] : ['damage']

  return (
    <div className="card parsecol">
      {/* The picture beside the words, not under them: some of these columns
          cannot be checked by arithmetic, and the screenshot is the only other
          evidence there is — so it travels with the parse rather than staying
          behind on the Import page. It sits in the HEAD, right of the title
          block, because that block is three short lines and a thumbnail is
          about three short lines tall: stacked they cost two bands of every
          imported column, side by side they cost one. Click enlarges it. */}
      <div className="pchead pcheadshot">
        <div className="pcheadtext">
          <div className="pctitle">
            {/* Who, where, which fight — see `shotTitle`. A column headed `All`
                because that is what ACT's title bar said names nothing at all
                when there are two imported parses side by side. */}
            <h3 className="panelname">{shotTitle(shot)}</h3>
            <span className="badge" title="Read from a screenshot, not from a log">
              imported
            </span>
          </div>
          <span className="muted">
            {/* what is LEFT once the title has said the rest */}
            {shot.when_text || 'from a screenshot'}
            {' · '}{kind === 'heal' ? 'Heals' : 'Damage'}
          </span>
        </div>
        <ShotThumb shot={shot} onOpen={() => setViewing(true)} className="headshot" />
        {/* Past the picture, in the card's own top-right corner. Everywhere
            else the ✕ ends the title LINE, which is the same statement — the
            far end of the head — and here the head's far end is beyond the
            screenshot, not between it and the words. */}
        <CloseCol i={col.i} remove={remove} />
      </div>
      <div className="pcmeta">
        {col.duration && (
          <>
            <ShotStrip shot={shot} rows={col.rows} kind={kind} duration={col.duration} />
            <ShotCoverage shot={shot} rows={col.rows} />
          </>
        )}
      </div>
      <div className="pcbody">
        {!col.duration ? (
          /* No clock, no rates. Its title bar carried no [mm:ss], so a
             per-second column would be invented rather than read. */
          <p className="muted">
            No fight length in this screenshot's title bar, so per-second numbers
            can't be worked out from it.
          </p>
        ) : (
          <BreakdownTable
            rows={col.rows}
            kinds={kinds}
            duration={col.duration}
            linkHover
            wrapClass="parsewin"
            fitViewport
            prefsKey="compare"
            defaultHidden={['total', 'share', 'to_hit_pct', 'median', 'min', 'press_delay_s']}
          />
        )}
      </div>
      {viewing && <ShotViewer shot={shot} onClose={() => setViewing(false)} />}
    </div>
  )
}

/* The headline for an imported parse comes from ACT's `All` line, NOT from
   summing the rows.

   A screenshot only contains the rows that fitted on screen. The Zylphax
   fixture shows 26 abilities with a scrollbar beside them, and the ~15 below
   the fold are 10% of the damage — so summing what is visible reported 29,715
   DPS for a parse ACT itself totalled at 33,017. The `All` row is the one
   number in the image that describes the WHOLE fight, which makes it the
   only honest headline; `ShotCoverage` states the gap rather than leaving two
   totals to disagree quietly. Falls back to the rows when a shot carries no
   `All` line at all. */
function ShotStrip({ shot, rows, kind, duration }) {
  const t = shot.total || {}
  const sum = (f) => rows.reduce((s, r) => s + (r[f] || 0), 0)
  const total = (kind === 'damage' ? t.damage : t.healed) ?? sum('total')
  const hits = t.hits ?? sum('hits')
  const crits = t.crit_pct != null && t.hits
    ? Math.round((t.crit_pct / 100) * t.hits)
    : sum('crits')
  const actor = kind === 'damage' ? { damage: total } : { heals: total }
  return <ParseStrip actor={actor} derived={{ hits, crits }}
                     kind={kind} duration={duration} combat />
}

/* How much of the parse the screenshot actually caught. Silent when the rows
   account for the `All` line, which is the usual case — this only speaks when
   the image was scrolled, and then it says so plainly, because a table that
   is 90% of a parse is fine to compare against as long as nobody reads its
   own All row as the player's total. */
function ShotCoverage({ shot, rows }) {
  const t = shot.total || {}
  const total = t.damage ?? t.healed
  if (!total) return null
  const shown = rows.reduce((s, r) => s + (r.total || 0), 0)
  if (shown >= total * 0.995) return null
  return (
    <p className="note partial">
      The screenshot caught {rows.length} rows — {Math.round((shown / total) * 100)}%
      of this parse's {shot.kind === 'heal' ? 'healing' : 'damage'}. The rest was
      scrolled out of frame, so the totals above are ACT's and the table's own
      All row is only what you can see.
    </p>
  )
}

/* One parse, one card. The error case is the whole card, so the body — which
   is where the hooks are — never has to render half a column. */
function ParseCol(props) {
  const { col, remove } = props
  if (col.run?.error) {
    return (
      <div className="card parsecol">
        <div className="pchead">
          <div className="pctitle">
            <span className="z">Run #{col.runId}</span>
            <CloseCol i={col.i} remove={remove} />
          </div>
          <span className="muted">
            {col.run.error === 'notvisible'
              ? 'Not visible to you — the owner may need to share or publish it'
              : col.run.error}
          </span>
        </div>
      </div>
    )
  }
  return <div className="card parsecol"><ColBody {...props} /></div>
}

/* Head, meta and table are three slots in every column so the tables line up
   across the row — see .parsecols in base.css. A column with nothing to put in
   its meta slot still renders the empty div; that whitespace IS the alignment. */
function ColBody({ col, view, onView, combinePets, onCombinePets, patch, remove }) {
  const { run, agg } = col
  const raid = col.subject === 'raid'
  const ready = agg && !agg.error
  const zr = run?.zone_run

  const actorRows = useMemo(
    () => (ready && !raid && col.actor ? actorRowsOf(agg.abilities, col.actor.key) : []),
    [agg, ready, raid, col.actor])
  const avail = useMemo(
    () => (!ready ? [] : raid ? raidKinds(agg) : availKinds(actorRows)),
    [agg, ready, raid, actorRows])
  // the reader's tab if this parse still has it, otherwise the first it does
  const filter = avail.find((f) => f.key === view) || avail[0] || KIND_FILTERS[0]
  const rows = useMemo(
    () => breakdownRows(actorRows, filter.kinds, combinePets),
    [actorRows, filter, combinePets])

  return (
    <>
      <div className="pchead">
        <div className="pctitle">
          <h3 className="panelname">
            {raid ? (zr?.zone || `Run #${col.runId}`) : col.subject}
          </h3>
          {!raid && col.actor && <ClassChip actor={col.actor} />}
          <CloseCol i={col.i} remove={remove} />
        </div>
        <span className="muted">
          {zr ? `${fmt.date(zr.started_ts)} — ${zr.character_name}'s parse` : '…'}
        </span>
        <ColPickers col={col} patch={patch} />
        {avail.length > 1 && (
          <Tabs
            tabs={avail.map((f) => ({ key: f.key, label: f.label }))}
            value={filter.key}
            onChange={(k) => onView(col.i, k)}
          />
        )}
      </div>
      <div className="pcmeta">
        {ready && raid && (
          <ParseStrip actor={col.actor} derived={col.derived}
                      kind={filter.kinds[0]} duration={col.duration} combat />
        )}
        {ready && !raid && col.actor && (
          <>
            {filter.key === 'damage' && <CompositionStrip rows={actorRows} />}
            {/* One pet setting for the whole page: two tables that fold pets
                differently are not two views of the same question. */}
            <div className="optionsbar">
              <label className="optcheck" title="One line per pet kit, on every parse here">
                <input
                  type="checkbox"
                  checked={combinePets}
                  onChange={(e) => onCombinePets(e.target.checked)}
                /> Combine pets
              </label>
            </div>
          </>
        )}
      </div>
      <div className="pcbody">
        {!agg && <p className="muted">Loading parse…</p>}
        {agg?.error && <p className="err">{agg.error}</p>}
        {ready && (raid ? (
          <RaidList agg={agg} duration={col.duration} kinds={filter.kinds} />
        ) : !col.actor ? (
          <p className="muted">Not in these fights.</p>
        ) : rows.length ? (
          <BreakdownTable
            rows={rows}
            kinds={filter.kinds}
            duration={col.duration}
            linkHover
            wrapClass="parsewin"
            fitViewport
            prefsKey="compare"
            defaultHidden={['total', 'share', 'to_hit_pct', 'median', 'min', 'press_delay_s']}
          />
        ) : (
          <p className="muted">Nothing on this tab.</p>
        ))}
      </div>
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
    /* ACT's Max Hit. An imported screenshot carries one per row, so this
       column is computed the same way for a real parse and a pasted one —
       which is the whole rule this page is built on. */
    {
      key: 'max_hit', label: 'Max hit',
      render: (a) => { const v = derived[a.key]?.max; return v ? fmt.num(v) : '' },
      sortValue: (a) => derived[a.key]?.max ?? null,
      cellStyle: rankAgainst((a) => derived[a.key]?.max ?? null),
      cellTitle: rankTitleAgainst((a) => derived[a.key]?.max ?? null),
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

/* The colored dot a class chip carries, on its own. A `Picker` row is not a
   chip — the class is spelled out in the row's muted half — but the dot is what
   makes a roster scannable by archetype instead of read name by name. */
const classDot = (cls) => (
  <i className="cdot" style={classColor(cls) ? { background: classColor(cls) } : undefined} />
)

/* What this column is OF: which fights, then who. Side by side and in that
   order — the fights narrow the parse and the subject picks a parse out of
   it, so reading them left to right reads the column's own sentence.

   Both are `Picker` rather than `<select>`, for reasons the subject one shows
   best: a raider is a name AND a class, which an `<option>` cannot hold, and a
   native control is as wide as its widest row, so one 24-name roster set the
   width of a control that reads `Bobby`. See Picker.jsx. */
function ColPickers({ col, patch }) {
  const { run, agg } = col
  const fights = run?.encounters
  const players = agg && !agg.error
    ? agg.actors.filter((a) => a.kind === 'player') : []
  const ids = col.sel === 'all' ? [] : col.sel.split('.').map(Number)
  const picked = ids.length > 1 && fights
    ? fights.filter((e) => ids.includes(e.id)) : []
  // several fights against ONE mob is a boss, not an arbitrary selection —
  // say so, because that is what picking a named mob in the search hands you
  const oneMob = picked.length && picked.every((e) => e.name === picked[0].name)
    ? picked[0].name : null

  /* A fight reads NAME first and clock second here, the other way round from
     the old option string: the list is scanned for a boss, and a column of
     times with the names hanging off their right edge is a list you read
     twice. The full label stays as the row's tooltip. */
  const fightOpt = (e, group) => ({
    key: `${group}:${e.id}`,
    value: String(e.id),
    label: e.name || 'Unknown',
    hint: `${fmt.time(e.started_ts)}${e.success === 0 ? ' · wipe' : ''}`,
    group,
    title: fightLabel(e),
  })
  const fightOpts = [
    {
      value: 'all',
      label: 'Whole raid',
      hint: fights ? `${fights.length} fights` : undefined,
    },
    ...(ids.length > 1 ? [{
      value: col.sel,
      label: oneMob || 'Selection',
      hint: oneMob ? `${ids.length} pulls` : `${ids.length} fights`,
    }] : []),
    ...(fights || []).filter((e) => e.is_named).map((e) => fightOpt(e, 'Named')),
    ...(fights || []).map((e) => fightOpt(e, 'Every fight')),
  ]

  const subjectOpts = [
    {
      value: 'raid',
      label: 'Whole raid',
      hint: players.length ? `${players.length} raiders` : undefined,
    },
    ...players.map((a) => ({
      value: a.name,
      label: a.name,
      hint: classLabel(a.class) || undefined,
      icon: classDot(a.class),
    })),
    /* keep a name that isn't in this fight selectable, so flipping fights
       doesn't silently rewrite who the column is about */
    ...(col.subject !== 'raid' && !players.some((a) => a.name === col.subject)
      ? [{ value: col.subject, label: col.subject, hint: 'not in these fights' }]
      : []),
  ]

  return (
    <div className="pcpickers">
      <Picker
        className="fights"
        value={col.sel}
        options={fightOpts}
        label="Which fights"
        placeholder="Loading fights…"
        disabled={!fights}
        filterHint="Find a fight…"
        onChange={(v) => patch(col.i, { sel: v })}
      />
      <Picker
        className="who"
        value={col.subject}
        options={subjectOpts}
        label="Whole raid or one player"
        placeholder="Loading raiders…"
        disabled={!players.length}
        filterHint="Find a raider…"
        onChange={(v) => patch(col.i, { subject: v })}
      />
    </div>
  )
}

/* The two kinds of thing a result row can be clicked INTO, each with its own
   mark. A row's chips are a mob and a person side by side — the one place on
   the page where those sit in the same strip — and they used to be told apart
   by color alone, which is the same mistake the class chips are careful not to
   make. A skull and a head are the game's own shorthand and survive being 12px
   of currentColor, which a word next to every chip would not.

   Inline SVG rather than an emoji or a font glyph: it inherits the chip's
   color (so hover carries it), it is aria-hidden by construction, and it is
   the same size on every platform. */
const MobIcon = () => (
  <svg className="tico" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
    <path d="M3.4 8.4a4.6 4.6 0 0 1 9.2 0v1c0 .8-.5 1.2-1 1.5v1.1a1 1 0 0 1-1 1H5.4
             a1 1 0 0 1-1-1v-1.1c-.5-.3-1-.7-1-1.5z"
          fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    <circle cx="6.2" cy="8.4" r="1.15" fill="currentColor" />
    <circle cx="9.8" cy="8.4" r="1.15" fill="currentColor" />
  </svg>
)
const WhoIcon = () => (
  <svg className="tico" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
    <circle cx="8" cy="5.4" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.4" />
    <path d="M3 13.6a5 5 0 0 1 10 0" fill="none" stroke="currentColor"
          strokeWidth="1.4" strokeLinecap="round" />
  </svg>
)

const RESULT_CAP = 12       // a picker is a shortlist; past this, narrow it
const NAMED_CAP = 8         // ...and so is the row of bosses under one night
const GROUPRUNS_KEY = 'eq2advanced-compare-groupruns'
const dayKey = (ts) => new Date(ts * 1000).toDateString()

/* The band across the top of the page: a faceted live search over the list the
   page already holds. The whole visible list with rosters and named mobs is
   ~100 KB, smaller than one parse it will fetch afterwards, so there is nothing
   to ask the server and nothing to debounce. Typing narrows; the dropdowns pin;
   each dropdown only offers values that still leave results, so no combination
   can strand you on an empty list.

   It shows NOTHING until asked. A list of the twelve most recent raids sitting
   there on arrival looks like the page's content, when the page's content is
   the parses underneath — so results appear once you have searched, and the
   drop slot beside the columns is what speaks in the meantime.

   One click on a result IS the add: it lands as a column, already scoped to
   the named mob if one is picked and to the player if the search is about a
   person. Whatever it got wrong, the column's own two dropdowns fix — which is
   why the old confirm strip (pick a night, then pick a subject, then press
   Add) is gone. */
function AddBar({ onAdd, anchor }) {
  const playerMode = !!anchor && anchor !== 'raid'
  const [list, setList] = useState(null)
  const [listErr, setListErr] = useState(null)
  const [q, setQ] = useState('')
  const [zone, setZone] = useState('')
  const [mob, setMob] = useState('')
  const [date, setDate] = useState('')
  const [guild, setGuild] = useState('')
  const [player, setPlayer] = useState(playerMode ? anchor : '')
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
    if (skip !== 'mob' && mob && !(r.named || []).some((n) => n.name === mob)) return false
    if (skip !== 'date' && date && dayKey(r.started_ts) !== date) return false
    if (skip !== 'guild' && guild && (r.guild || '') !== guild) return false
    if (skip !== 'player' && player
        && !(r.roster || []).some((n) => n.toLowerCase() === player.toLowerCase())) return false
    if (skip !== 'q' && ql) {
      // zones, guilds and mob names get typed from the middle ("freeth",
      // "unrest", "vyk"); a person's name gets typed from the front
      const text = `${r.zone || ''} ${r.guild || ''} `
        + (r.named || []).map((n) => n.name).join(' ')
      if (!text.toLowerCase().includes(ql)
          && !(r.roster || []).some((n) => n.toLowerCase().startsWith(ql))) return false
    }
    return true
  }

  const results = useMemo(
    () => (list || []).filter((r) => passes(r)),
    [list, ql, zone, mob, date, guild, player, groupRuns])

  const optionsFor = (facet) => (list || []).filter((r) => passes(r, facet))
  const zones = useMemo(() => [...new Set(
    optionsFor('zone').map((r) => r.zone || ''))].filter(Boolean).sort(),
  [list, ql, mob, date, guild, player, groupRuns])
  const mobs = useMemo(() => [...new Set(
    optionsFor('mob').flatMap((r) => (r.named || []).map((n) => n.name)))].sort(),
  [list, ql, zone, date, guild, player, groupRuns])
  const dates = useMemo(() => [...new Map(
    optionsFor('date').map((r) => [dayKey(r.started_ts), r.started_ts])).entries()]
    .sort((a, b) => b[1] - a[1]),
  [list, ql, zone, mob, guild, player, groupRuns])
  const guilds = useMemo(() => [...new Set(
    optionsFor('guild').map((r) => r.guild || ''))].filter(Boolean).sort(),
  [list, ql, zone, mob, date, player, groupRuns])
  const players = useMemo(() => [...new Set(
    optionsFor('player').flatMap((r) => r.roster || []))].sort(),
  [list, ql, zone, mob, date, guild, groupRuns])

  /* Nothing is tagged yet (a fresh backfill) — then the control simply does
     not exist, rather than sitting there empty asking to be clicked. Both are
     asked of the WHOLE list, not of the narrowed one: a dropdown that vanishes
     mid-search because this zone has no named mobs is worse than an empty one. */
  const anyGuild = (list || []).some((r) => r.guild)
  const anyNamed = (list || []).some((r) => (r.named || []).length)

  /* You, first. Your own characters and your own guild are the values you
     reach for most, and hunting for them in three hundred alphabetical names
     is the picker failing at its one job. Taken from the runs the list already
     marks `mine` — no session lookup, no extra request, and it degrades to
     nothing at all when you are signed out. */
  const mineOf = (get) => [...new Set((list || [])
    .filter((r) => r.mine).map(get).filter(Boolean))].sort()
  const myNames = useMemo(() => mineOf((r) => r.character_name), [list])
  const myGuilds = useMemo(() => mineOf((r) => r.guild), [list])
  const yours = (all, mine) => {
    const own = mine.filter((v) => all.includes(v))
    return [own, all.filter((v) => !own.includes(v))]
  }
  const [myPlayers, otherPlayers] = yours(players, myNames)
  const [myGuildOpts, otherGuilds] = yours(guilds, myGuilds)
  // no question asked, nothing to answer with
  const asked = !!(ql || zone || mob || date || guild || player)

  const clear = () => {
    setQ(''); setZone(''); setMob(''); setDate(''); setGuild('')
    setPlayer(playerMode ? anchor : '')
  }

  /* The night, scoped by what the search was about. A named mob picks that
     boss's fights (a raid pulls one twice often enough that both belong), and
     a person picks that person — spelled the way the roster spells them, not
     the way the facet was typed. The facets survive the click, so stacking
     three nights of the same guild is three clicks. */
  const addFrom = (r, override) => {
    const roster = r.roster || []
    const inRoster = (n) => roster.find((m) => m.toLowerCase() === (n || '').toLowerCase())
    const named = mob ? (r.named || []).find((n) => n.name === mob) : null
    onAdd({
      runId: r.id,
      sel: named?.ids?.length ? named.ids.join('.') : 'all',
      subject: inRoster(player) || (playerMode ? inRoster(anchor) : null) || 'raid',
      ...override,
    })
  }

  /* What ELSE the row can be clicked into. A night matched by a mob name or a
     person's name is not really an answer of "this raid" — it is an answer of
     "that pull" or "them, that night", and the row that only offers the whole
     raid makes you add it and then narrow it in the column. So the parts that
     MATCHED come out as their own targets underneath.

     With no question about mobs, every named mob of the night is offered:
     going straight to a boss is the common move, and the fight dropdown in the
     column is the same list one step later. Raiders are not offered that way —
     twenty-four names under every row is a roster, not a shortlist. */
  const namedTargets = (r) => {
    const all = r.named || []
    if (mob) return all.filter((n) => n.name === mob)
    if (ql) {
      const hit = all.filter((n) => n.name.toLowerCase().includes(ql))
      if (hit.length) return hit
    }
    return all.slice(0, NAMED_CAP)
  }
  const playerTargets = (r) => {
    const roster = r.roster || []
    if (player) return roster.filter((n) => n.toLowerCase() === player.toLowerCase())
    return ql ? roster.filter((n) => n.toLowerCase().startsWith(ql)) : []
  }

  return (
    <div className="card addbar">
      <div className="addmain">
        <h2>Add a parse</h2>
        <p className="note">
          Search players, zones, mobs or drop an ACT screenshot. Additive.
        </p>
        {listErr && <p className="err">{listErr}</p>}
        <div className="addfacets">
          {/* The glass is the label: the box says "Search…" and nothing else,
              because what it searches is the row of dropdowns beside it. */}
          <span className="searchbox">
            <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
              <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor"
                      strokeWidth="1.6" />
              <line x1="10.4" y1="10.4" x2="14" y2="14" stroke="currentColor"
                    strokeWidth="1.6" strokeLinecap="round" />
            </svg>
            <input
              type="search"
              placeholder="Search…"
              value={q}
              aria-label="Search raids by zone, guild, named mob or player"
              onChange={(ev) => setQ(ev.target.value)}
            />
          </span>
          {/* Each dropdown is named for what it holds, not for the row it
              would leave alone: the facet is off when it reads its own name.
              They are `Picker`, the same themed list the columns use — a facet
              row here can carry an icon and a `(You)` of its own, and the row
              of six controls keeps its widths instead of one long zone name
              deciding them. */}
          {/* The Zone control carries the page's one loading state — it is the
              first dropdown, and the list every facet reads is one request. */}
          <Picker
            value={zone} label="Zone" disabled={!list}
            filterHint="Find a zone…"
            options={[{ value: '', label: list ? 'Zone' : 'Loading raids…', menuLabel: 'Any zone' },
                      ...zones.map((z) => ({ value: z, label: z }))]}
            onChange={setZone}
          />
          {anyNamed && (
            <Picker
              value={mob} label="Named mob" disabled={!list}
              placeholder="Named"
              filterHint="Find a mob…"
              options={[{ value: '', label: 'Named', menuLabel: 'Any named mob' },
                        ...mobs.map((m) => ({ value: m, label: m, icon: <MobIcon /> }))]}
              onChange={setMob}
            />
          )}
          <Picker
            value={date} label="Date" disabled={!list}
            placeholder="Date"
            filterHint="Find a date…"
            options={[{ value: '', label: 'Date', menuLabel: 'Any date' },
                      ...dates.map(([k, ts]) => ({ value: k, label: fmt.date(ts) }))]}
            onChange={setDate}
          />
          {anyGuild && (
            <Picker
              value={guild} label="Guild" disabled={!list}
              placeholder="Guild"
              filterHint="Find a guild…"
              options={[{ value: '', label: 'Guild', menuLabel: 'Any guild' },
                        ...myGuildOpts.map((g) => ({ value: g, label: g, hint: 'You', group: 'Yours' })),
                        ...otherGuilds.map((g) => ({
                          value: g, label: g, group: myGuildOpts.length ? 'Every guild' : undefined,
                        }))]}
              onChange={setGuild}
            />
          )}
          <Picker
            value={player} label="Player" disabled={!list}
            placeholder="Player"
            filterHint="Find a raider…"
            options={[{ value: '', label: 'Player', menuLabel: 'Anyone' },
                      ...myPlayers.map((p) => ({
                        value: p, label: p, hint: 'You', icon: <WhoIcon />, group: 'Yours',
                      })),
                      ...otherPlayers.map((p) => ({
                        value: p, label: p, icon: <WhoIcon />,
                        group: myPlayers.length ? 'Everyone' : undefined,
                      }))]}
            onChange={setPlayer}
          />
          <label className={`chip toggle ${groupRuns ? 'on' : ''}`}
                 title={`Off, only raids (${RAID_MIN_RAIDERS}+ raiders) are offered`}>
            <input type="checkbox" checked={groupRuns}
                   onChange={(ev) => setGroupRuns(ev.target.checked)} /> Solo/Group runs
          </label>
          {asked && <button className="chip" onClick={clear}>Clear</button>}
        </div>

        {asked && list && !results.length && <p className="muted">Nothing matches.</p>}
        {asked && !!results.length && (
          <div className="addresults">
            {results.slice(0, RESULT_CAP).map((r) => {
              const named = namedTargets(r)
              const who = playerTargets(r)
              return (
                <div key={r.id} className="pickrow">
                  <button className="pickmain" onClick={() => addFrom(r)}>
                    <span>{fmt.date(r.started_ts)}</span>
                    <span className="z">{r.zone || 'Unknown zone'}</span>
                    {r.guild && <span className="badge guild">{r.guild}</span>}
                    <span className="muted">
                      {r.encounter_count} fights · {r.character_name}'s parse
                    </span>
                  </button>
                  {(named.length > 0 || who.length > 0) && (
                    <div className="pickchips">
                      {named.map((n) => (
                        <button key={n.name} className="chip mob"
                                title={`Add just this fight${n.ids.length > 1
                                  ? ` — ${n.ids.length} pulls` : ''}`}
                                onClick={() => addFrom(r, { sel: n.ids.join('.') })}>
                          <MobIcon />{n.name}
                          {n.ids.length > 1 && <span className="x">×{n.ids.length}</span>}
                        </button>
                      ))}
                      {who.map((p) => (
                        <button key={p} className="chip who"
                                title={`Add ${p}'s parse from this night`}
                                onClick={() => addFrom(r, { subject: p })}>
                          <WhoIcon />{p}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
        {asked && results.length > RESULT_CAP && (
          <p className="muted">+{results.length - RESULT_CAP} more — narrow it down.</p>
        )}
      </div>
    </div>
  )
}
