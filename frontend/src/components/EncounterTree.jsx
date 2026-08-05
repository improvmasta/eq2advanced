import { useMemo, useState } from 'react'

/* The fight rail — the navigation for a raid night, and the control surface
   for what the tables on the right are counting.

   Two things happen in here, and they are deliberately different gestures:
   - clicking a fight (or the All row, or a zone block, or a Trash group) makes
     that the ONLY selection — the fast path, one click per fight;
   - ticking its checkbox adds or removes it from the current selection, so a
     handful of pulls can be merged into one set of combined stats.
   The checkboxes always show what is currently counted, which is also how you
   see at a glance that "All" really means all sixty fights.

   Zone blocks: re-entering a zone later starts a new block, exactly like ACT's
   left pane. A zone run is one zone by construction, so /zones/:id passes
   hideZones and gets a flat list. Consecutive trash pulls collapse into one
   expandable Trash ×N node.

   The rail is a tree with one root: Zonewide is the whole run, and every fight
   is indented under it. It used to read as a list whose first item happened to
   be called "All fights", which is a different claim. */

function buildTree(encounters) {
  const blocks = []
  for (const e of encounters) {
    const last = blocks[blocks.length - 1]
    if (!last || last.zone !== e.zone) blocks.push({ zone: e.zone, encounters: [e] })
    else last.encounters.push(e)
  }
  for (const b of blocks) {
    b.nodes = []
    let trash = []
    const flush = () => {
      if (trash.length > 1) b.nodes.push({ type: 'trashgroup', encs: trash })
      else if (trash.length === 1) b.nodes.push({ type: 'enc', enc: trash[0] })
      trash = []
    }
    for (const e of b.encounters) {
      // nameds and wipes each get their own row — a pull the raid lost is not
      // routine trash, and burying it in a "Trash x15" group is how the
      // Emerald Halls wipes stayed invisible
      if (e.is_named || e.success === 0) {
        flush()
        b.nodes.push({ type: 'enc', enc: e })
      } else trash.push(e)
    }
    flush()
  }
  return blocks
}

const idsOf = (encs) => encs.map((e) => e.id).join(',')
const sumDur = (encs) => encs.reduce((s, e) => s + Math.max(e.duration_s, 1), 0)

/* A raid night against one boss is seven rows reading "Vampire Lord Mayong
   Mist…" — the name is the part that survives truncation and the part that
   carries no information. Repeated nameds get an attempt number, rendered
   outside the ellipsis so it is the one thing always legible. */
function attemptNumbers(encounters) {
  const total = new Map()
  for (const e of encounters) {
    if (e.is_named) total.set(e.name, (total.get(e.name) || 0) + 1)
  }
  const seen = new Map()
  const out = new Map()
  for (const e of encounters) {
    if (!e.is_named || total.get(e.name) < 2) continue
    const n = (seen.get(e.name) || 0) + 1
    seen.set(e.name, n)
    out.set(e.id, n)
  }
  return out
}

/* Fight lengths are read down a column, so they are set as m:ss in tabular
   figures — "4m 12s" next to "12s" made a ragged column nothing lined up in. */
const clock = (s) => {
  if (s == null) return ''
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}
/* Wall-clock start, compact: "9:35p" fits the gutter that "09:35 PM" blew out,
   and down a column the minutes are what you actually scan. */
const hhmm = (epoch) => {
  const d = new Date(epoch * 1000)
  const h = d.getHours()
  return `${h % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')}${h >= 12 ? 'p' : 'a'}`
}
const longClock = (s) => (s >= 3600
  ? `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
  : clock(s))

/* The three kinds of pull, and every fight is exactly one of them. A wiped
   named is a WIPE and not a named: the classes have to partition the list, or
   "uncheck the wipes" and "check the nameds" would each undo part of the
   other. */
const KIND_OF = (e) => (e.success === 0 ? 'wipe' : e.is_named ? 'named' : 'trash')
const KINDS = [
  { key: 'named', label: 'Nameds', title: 'Count the named pulls the raid killed' },
  { key: 'trash', label: 'Trash', title: 'Count the trash pulls' },
  { key: 'wipe', label: 'Wipes', title: 'Count the pulls the raid lost' },
]

export default function EncounterTree({
  encounters, sel, onSelect, sessionLabel, sub, subTag, subTitle, actions,
  titled = false, hideZones = false, selectedIds, onToggle, onSelectMany,
}) {
  const blocks = useMemo(() => buildTree(encounters), [encounters])
  const attempts = useMemo(() => attemptNumbers(encounters), [encounters])
  const [open, setOpen] = useState({})   // trash-group key -> expanded

  const checked = selectedIds || null    // Set of encounter ids, or null = uncheckable
  const allSelected = sel === 'all'
  /* One switch per kind, ticking and unticking that kind's rows — the same
     onToggle every checkbox in the list already calls, so "the nameds only" is
     a state of the list you can see rather than a preset that fought with it. */
  const kinds = useMemo(() => {
    const by = { named: [], trash: [], wipe: [] }
    for (const e of encounters) by[KIND_OF(e)].push(e.id)
    return KINDS
      .map((k) => ({ ...k, ids: by[k.key] }))
      .filter((k) => k.ids.length > 0)
      .map((k) => ({
        ...k,
        on: allSelected || (checked ? k.ids.every((i) => checked.has(i)) : false),
        some: !allSelected && checked ? k.ids.some((i) => checked.has(i)) : false,
      }))
  }, [encounters, checked, allSelected])
  const countSel = allSelected ? encounters.length : (checked?.size || 0)
  const durSel = allSelected
    ? sumDur(encounters)
    : sumDur(encounters.filter((e) => checked?.has(e.id)))

  /* One row, whatever it stands for: a fight, a zone block, a trash group or
     the All row. `encs` is what its checkbox covers. */
  const row = (key, {
    label, name, time, dur, encs, extra = '', indent = 0, twist = null,
    tag = null,
  }) => {
    const active = sel === key
    const groupOn = encs && (allSelected || (checked && encs.every((e) => checked.has(e.id))))
    const groupSome = encs && !groupOn && checked
      && encs.some((e) => checked.has(e.id))
    return (
      <div className={`railrow ${extra} ${active ? 'active' : ''}`} key={key}>
        {twist || <span className="twistpad" />}
        {checked && encs && (
          <input
            type="checkbox"
            className="railcheck"
            checked={!!groupOn}
            ref={(el) => { if (el) el.indeterminate = !!groupSome }}
            onChange={() => onToggle(encs.map((e) => e.id), !groupOn)}
            aria-label={`Count ${name || (typeof label === 'string' ? label : 'these fights')} in the stats`}
          />
        )}
        <button
          className="railbtn"
          style={indent ? { paddingLeft: indent } : undefined}
          onClick={() => onSelect(key)}
        >
          <span className="rt">{time != null ? hhmm(time) : ''}</span>
          <span className="rl" title={name || undefined}>{label}</span>
          {tag && <span className="rn">{tag}</span>}
          <span className="rd">{dur}</span>
        </button>
      </div>
    )
  }

  const fightRow = (e, indent) => row(String(e.id), {
    label: (
      <>
        {e.success === 0 && <i className="wipedot" title="Wipe" />}
        {/* green alone cannot carry the outcome — red/green is the one pair a
            colourblind reader loses, so the kill gets its own mark too */}
        {/* `is_named` is 0/1 from SQLite, so it is guarded with === : `0 &&`
            renders the digit, which is how a stub read "0Wuoshi" */}
        {e.is_named === 1 && e.success === 1 && <i className="killmark" title="Killed">✓</i>}
        {e.name}
      </>
    ),
    name: e.name,
    tag: attempts.has(e.id) ? `#${attempts.get(e.id)}` : null,
    time: e.started_ts,
    dur: clock(e.duration_s),
    encs: [e],
    indent,
    extra: `${e.is_named ? 'named' : 'trash'}${e.success === 0 ? ' wiped' : ''}`
      + (e.is_named && e.success === 1 ? ' won' : ''),
  })

  return (
    <nav className="rail" aria-label="Fights">
      {/* This box is the page's title block, in every view. The raid page used
          to print the zone name here AND in a page head above the tables,
          which was the same fact twice — and the page head vanished the moment
          a drilldown opened, so the one that survives is the one that gets to
          be the title. The who/when line and the raid-level actions moved here
          with it. */}
      <div className="railhead">
        <div className="railtop">
          {/* `titled` is the raid page, where this IS the page's h1. The
              per-file debug view still keeps its own head, so there it stays a
              label and the page has exactly one h1 either way. */}
          {titled
            ? <h1 className="railtitle" title={sessionLabel || undefined}>{sessionLabel || 'Fights'}</h1>
            : <span className="railtitle">{sessionLabel || 'Fights'}</span>}
          {/* the run's length lives on the Zonewide row, in the same column
              every other fight's length is read down */}
          <span className="railmeta">
            {encounters.length} fight{encounters.length === 1 ? '' : 's'}
          </span>
        </div>
        {/* The caption ellipses; anything pinned to the END of it (the guild
            the raid was voted into) is a tag, not more caption, so it keeps
            its width instead of being the first thing truncated away. */}
        {sub && (
          <span className="railsub" title={subTitle}>
            <span className="t">{sub}</span>
            {subTag}
          </span>
        )}
        {actions && <div className="railacts">{actions}</div>}
      </div>

      {/* One line, one kind of control: three switches that say which pulls are
          being counted. They are the list's own checkboxes in bulk — the state
          you see here is the state of the rows below, and a half-ticked kind
          says so rather than pretending it is off. */}
      {onToggle && kinds.length > 1 && (
        <div className="railtools">
          {kinds.map((k) => (
            <label
              key={k.key}
              className={`switch ${k.on ? 'on' : ''} ${k.some && !k.on ? 'some' : ''}`}
              title={k.title}
            >
              <input
                type="checkbox"
                checked={k.on}
                ref={(el) => { if (el) el.indeterminate = k.some && !k.on }}
                onChange={() => onToggle(k.ids, !k.on)}
              />
              <i className="track"><i className="knob" /></i>
              {k.label}
            </label>
          ))}
        </div>
      )}

      {/* Nothing about a list of fight names says it is a control, and the
          checkbox half is invisible until you know it is there. One line,
          shown only until you have made a selection of your own. */}
      {onSelectMany && allSelected && (
        <p className="railhint">Click to focus · tick to combine</p>
      )}

      <div className="raillist">
        {/* The root of the tree, and it stays put while the fights scroll under
            it — the total is the thing you are comparing every row against. */}
        {/* "Zonewide" only where it is true: a zone run is one zone by
            construction (hideZones), a session file can span several. */}
        {row('all', {
          label: hideZones ? 'Zonewide' : 'All fights',
          dur: longClock(sumDur(encounters)),
          encs: encounters,
          extra: 'all',
        })}
        <div className="railkids">
          {blocks.map((b, bi) => (
            <div className="railzone" key={bi}>
              {!hideZones && row(idsOf(b.encounters), {
                label: b.zone || 'Unknown zone',
                dur: longClock(sumDur(b.encounters)),
                encs: b.encounters,
                extra: 'zone',
              })}
              {b.nodes.map((n, ni) => {
                if (n.type === 'enc') return fightRow(n.enc, 0)
                const gkey = `${bi}:${ni}`
                const expanded = open[gkey]
                return (
                  <div key={gkey}>
                    {row(idsOf(n.encs), {
                      label: `Trash ×${n.encs.length}`,
                      time: n.encs[0].started_ts,
                      dur: clock(sumDur(n.encs)),
                      encs: n.encs,
                      extra: 'trash group',
                      twist: (
                        <button
                          className="twist"
                          aria-label={expanded ? 'Collapse trash' : 'Expand trash'}
                          aria-expanded={!!expanded}
                          onClick={() => setOpen((o) => ({ ...o, [gkey]: !expanded }))}
                        >{expanded ? '▾' : '▸'}</button>
                      ),
                    })}
                    {expanded && n.encs.map((e) => fightRow(e, 16))}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Always rendered once the rail is selectable: it used to appear on the
          first tick, which shoved the whole list up under the cursor. */}
      {checked && (
        <div className="railfoot">
          <span>
            {allSelected ? 'Counting all' : `${countSel} of ${encounters.length}`}
            {' · '}{longClock(durSel)}
          </span>
          {!allSelected && (
            <button className="chip" onClick={() => onSelect('all')}>Reset</button>
          )}
        </div>
      )}
    </nav>
  )
}
