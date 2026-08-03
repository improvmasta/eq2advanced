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
   expandable Trash ×N node. */

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

export default function EncounterTree({
  encounters, sel, onSelect, sessionLabel, hideZones = false,
  selectedIds, onToggle, onSelectMany,
  wipesShown = true, onWipes,
}) {
  const blocks = useMemo(() => buildTree(encounters), [encounters])
  const [open, setOpen] = useState({})   // trash-group key -> expanded

  const checked = selectedIds || null    // Set of encounter ids, or null = uncheckable
  const allSelected = sel === 'all'
  const wipeCount = encounters.filter((e) => e.success === 0).length
  const namedIds = encounters.filter((e) => e.is_named).map((e) => e.id)
  const countSel = allSelected ? encounters.length : (checked?.size || 0)
  const durSel = allSelected
    ? sumDur(encounters)
    : sumDur(encounters.filter((e) => checked?.has(e.id)))

  /* One row, whatever it stands for: a fight, a zone block, a trash group or
     the All row. `encs` is what its checkbox covers. */
  const row = (key, {
    label, name, time, dur, encs, extra = '', indent = 0, twist = null,
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
            title="Add these fights to the combined stats"
          />
        )}
        <button
          className="railbtn"
          style={indent ? { paddingLeft: indent } : undefined}
          onClick={() => onSelect(key)}
          title="Show only this"
        >
          <span className="rt">{time != null ? hhmm(time) : ''}</span>
          <span className="rl">{label}</span>
          <span className="rd">{dur}</span>
        </button>
      </div>
    )
  }

  const fightRow = (e, indent) => row(String(e.id), {
    label: (
      <>
        {e.success === 0 && <i className="wipedot" title="Wipe — the raid lost this pull" />}
        {e.name}
      </>
    ),
    name: e.name,
    time: e.started_ts,
    dur: clock(e.duration_s),
    encs: [e],
    indent,
    extra: `${e.is_named ? 'named' : 'trash'}${e.success === 0 ? ' wiped' : ''}`
      + (wipesShown || e.success !== 0 ? '' : ' dropped'),
  })

  return (
    <nav className="rail" aria-label="Fights">
      <div className="railhead">
        <span className="railtitle">{sessionLabel || 'Fights'}</span>
        <span className="railmeta">{encounters.length} · {longClock(sumDur(encounters))}</span>
      </div>

      {onSelectMany && (
        <div className="railtools">
          <button
            className={`chip ${allSelected ? 'on' : ''}`}
            onClick={() => onSelect('all')}
          >All</button>
          {namedIds.length > 0 && (
            <button
              className="chip"
              onClick={() => onSelectMany(namedIds)}
              title="Every named pull, trash left out"
            >Nameds</button>
          )}
          {wipeCount > 0 && onWipes && (
            /* A real switch, because this one changes every number on the
               page: wipes are counted the way ACT counts them, and turning
               them off is a deliberate act you can see the state of. */
            <label
              className={`switch ${wipesShown ? 'on' : ''}`}
              title="Wipes are counted like any other pull. Off leaves them in the list but out of every total."
            >
              <input
                type="checkbox"
                checked={wipesShown}
                onChange={(ev) => onWipes(ev.target.checked)}
              />
              <i className="track"><i className="knob" /></i>
              {wipeCount} wipe{wipeCount === 1 ? '' : 's'}
            </label>
          )}
        </div>
      )}

      {/* Nothing about a list of fight names says it is a control, and the
          checkbox half is invisible until you know it is there. One line,
          shown only until you have made a selection of your own. */}
      {onSelectMany && allSelected && (
        <p className="railhint">Click a fight to focus it · tick boxes to combine</p>
      )}

      <div className="raillist">
        {row('all', {
          label: 'All fights',
          dur: longClock(sumDur(encounters)),
          encs: encounters,
          extra: 'all',
        })}
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
                  {expanded && n.encs.map((e) => fightRow(e, 26))}
                </div>
              )
            })}
          </div>
        ))}
      </div>

      {checked && !allSelected && (
        <div className="railfoot">
          <span>{countSel} of {encounters.length} · {longClock(durSel)}</span>
          <button className="chip" onClick={() => onSelect('all')}>Reset</button>
        </div>
      )}
    </nav>
  )
}
