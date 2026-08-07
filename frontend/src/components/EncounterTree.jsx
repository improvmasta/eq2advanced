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
   be called "All fights", which is a different claim.

   The head is three blocks in a fixed order: IDENTITY (title, when, whose, how
   many), then `Seen by` — who the raid reaches — then `This raid`, the things
   you can do to it. Each of the last two is a small-caps label over its own
   row, which is what stops the sharing pills from reading as more of the guild
   tag above them and what stops the buttons from reading as a page footer.

   EDIT MODE (`editing`, the raid page's Edit button) swaps the `This raid`
   section's contents rather than adding a bar under it, tints the rail so the
   state is unmistakable, and puts two more controls on every row: hide and
   delete. A HIDDEN fight is still listed here — dimmed,
   with the switch that puts it back — because this rail is the only place its
   owner can reach it; it is out of every count on the page and out of every
   payload anybody else gets (backend `security.py`). Delete asks in the same
   spot rather than opening a dialog: the second click is the confirmation, and
   the row you are deleting stays under the cursor while you make it. */

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
/* The switches are for taking things OUT of the count. Nameds had one too, and
   it never earned its place: nobody reads a raid night with the bosses removed,
   so it existed to be left on. Trash and wipes are the two people actually turn
   off. `named` stays a KIND — the rows still colour by it — it just has no
   switch. */
const KINDS = [
  { key: 'trash', label: 'Trash', title: 'Count the trash pulls' },
  { key: 'wipe', label: 'Wipes', title: 'Count the pulls the raid lost' },
]

/* The one control here whose effect you cannot see from here — the raid it
   changes is the one somebody else opens — so the tooltip states it plainly
   and stops. */
const HIDE_TITLE = "Hide this fight. It won't show when the raid is shared, "
  + "and it won't count in stats."
const SHOW_TITLE = 'Hidden. Click to show it again.'

export default function EncounterTree({
  encounters, sel, onSelect, sessionLabel, sub, who, subTitle, actions, seenBy,
  titled = false, hideZones = false, selectedIds, onToggle,
  editing = false, editbar = null, onHide, onDelete,
}) {
  const blocks = useMemo(() => buildTree(encounters), [encounters])
  const attempts = useMemo(() => attemptNumbers(encounters), [encounters])
  const [open, setOpen] = useState({})   // trash-group key -> expanded
  const [confirm, setConfirm] = useState(null)   // row key awaiting "Yes"

  const checked = selectedIds || null    // Set of encounter ids, or null = uncheckable
  const allSelected = sel === 'all'
  /* A hidden fight is listed and nothing else: it is not counted, not
     selectable, and not part of any bulk switch. Everything below that adds up
     to a number reads this list, not `encounters`. */
  const shown = useMemo(() => encounters.filter((e) => !e.hidden), [encounters])
  const hiddenCount = encounters.length - shown.length
  /* One switch per kind, ticking and unticking that kind's rows — the same
     onToggle every checkbox in the list already calls, so "the nameds only" is
     a state of the list you can see rather than a preset that fought with it. */
  const kinds = useMemo(() => {
    const by = { named: [], trash: [], wipe: [] }
    for (const e of shown) by[KIND_OF(e)].push(e.id)
    return KINDS
      .map((k) => ({ ...k, ids: by[k.key] }))
      .filter((k) => k.ids.length > 0)
      .map((k) => ({
        ...k,
        on: allSelected || (checked ? k.ids.every((i) => checked.has(i)) : false),
        some: !allSelected && checked ? k.ids.some((i) => checked.has(i)) : false,
      }))
  }, [shown, checked, allSelected])
  const countSel = allSelected ? shown.length : (checked?.size || 0)
  const durSel = allSelected
    ? sumDur(shown)
    : sumDur(shown.filter((e) => checked?.has(e.id)))

  /* One row, whatever it stands for: a fight, a zone block, a trash group or
     the All row. `encs` is what its checkbox covers, and `all` is what its edit
     buttons cover — the same fights plus the hidden ones, because putting one
     back is the thing you came to this row to do. */
  const row = (key, {
    label, name, time, dur, encs, all, extra = '', indent = 0, twist = null,
    tag = null, hidden = false, edits = false,
  }) => {
    const active = sel === key
    const pickable = encs && encs.length > 0
    const groupOn = pickable && (allSelected || (checked && encs.every((e) => checked.has(e.id))))
    const groupSome = pickable && !groupOn && checked
      && encs.some((e) => checked.has(e.id))
    const what = name || (typeof label === 'string' ? label : 'these fights')
    return (
      <div
        className={`railrow ${extra} ${active ? 'active' : ''}${hidden ? ' hidden' : ''}`}
        key={key}
      >
        {twist || <span className="twistpad" />}
        {checked && encs && (
          pickable ? (
            <input
              type="checkbox"
              className="railcheck"
              checked={!!groupOn}
              ref={(el) => { if (el) el.indeterminate = !!groupSome }}
              onChange={() => onToggle(encs.map((e) => e.id), !groupOn)}
              aria-label={`Count ${what} in the stats`}
            />
          ) : <span className="railcheck empty" />
        )}
        <button
          className="railbtn"
          style={indent ? { paddingLeft: indent } : undefined}
          /* a row with nothing left to count is a label, not a control —
             clicking it would ask the page for an empty selection, which it
             answers by counting everything */
          onClick={() => { if (!encs || pickable) onSelect(key) }}
        >
          <span className="rt">{time != null ? hhmm(time) : ''}</span>
          <span className="rl" title={name || undefined}>{label}</span>
          {tag && <span className="rn">{tag}</span>}
          <span className="rd">{dur}</span>
        </button>
        {edits && editing && (
          <span className="railedit">
            {confirm === key ? (
              /* The confirmation is the second click on the same row, so the
                 fight you are about to lose never leaves the cursor. */
              <>
                <button className="ebtn yes" title={`Delete ${what}`}
                        onClick={() => { setConfirm(null); onDelete(all) }}>
                  Yes
                </button>
                <button className="ebtn" title="Cancel"
                        onClick={() => setConfirm(null)}>✕</button>
              </>
            ) : (
              <>
                <button
                  className={`ebtn ${hidden ? 'on' : ''}`}
                  title={hidden ? SHOW_TITLE : HIDE_TITLE}
                  aria-label={hidden ? `Show ${what}` : `Hide ${what}`}
                  onClick={() => onHide(all, !hidden)}
                >{hidden ? '⊙' : '⊘'}</button>
                <button
                  className="ebtn del"
                  title={`Delete ${what}`}
                  aria-label={`Delete ${what}`}
                  onClick={() => setConfirm(key)}
                >🗑</button>
              </>
            )}
          </span>
        )}
      </div>
    )
  }

  const visible = (encs) => encs.filter((e) => !e.hidden)

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
    encs: visible([e]),
    all: [e],
    hidden: !!e.hidden,
    edits: true,
    indent,
    extra: `${e.is_named ? 'named' : 'trash'}${e.success === 0 ? ' wiped' : ''}`
      + (e.is_named && e.success === 1 ? ' won' : ''),
  })

  return (
    <nav className={`rail ${editing ? 'editing' : ''}`} aria-label="Fights">
      {/* The head is IDENTITY and nothing else — what this night was, when, and
          whose parse of it you are reading. It used to carry the sharing pills
          and the buttons too, four kinds of thing stacked with no separation,
          and the result was that a raid's guild tag and a sharing group read as
          two of the same pill on adjacent lines. Everything you can DO now
          lives in the labelled sections underneath. */}
      <div className="railhead">
        <div className="railtop">
          {/* `titled` is the raid page, where this IS the page's h1. The
              per-file debug view still keeps its own head, so there it stays a
              label and the page has exactly one h1 either way. */}
          {titled
            ? <h1 className="railtitle" title={sessionLabel || undefined}>{sessionLabel || 'Fights'}</h1>
            : <span className="railtitle">{sessionLabel || 'Fights'}</span>}
        </div>
        {/* Three captions, one per line, each a different kind of fact. WHEN
            the raid was is a timestamp; WHO it belongs to is a name; HOW BIG it
            is is a count. The count used to ride on the title's baseline, where
            it pushed a long zone name onto two lines and then crowded them. */}
        {sub && (
          <span className="railsub" title={subTitle}>
            <span className="t">{sub}</span>
          </span>
        )}
        {/* The count rides at the right end of the who line — the same line, so
            it costs no vertical space, and right-aligned so it never gets in
            the way of a long character name or a wide parse picker. */}
        <span className="railwho">
          {who}
          <span className="railcount">
            {shown.length} fight{shown.length === 1 ? '' : 's'}
            {/* the count is what the page counts, so the ones held out of it are
                said beside it rather than folded into it */}
            {hiddenCount > 0 && (
              <span className="hid" title="Hidden fights: not shared, not counted in stats.">
                {' · '}{hiddenCount} hidden
              </span>
            )}
          </span>
        </span>
      </div>

      {/* Who can see it. A small-caps label over its own row, because the pills
          in it are a different claim from the guild pill a line above: that one
          is a fact Census reported, these are decisions the owner made. They
          are filled where the guild tag is outlined — the app's rule that gold
          means "somebody else can see this" now holds inside the head too. */}
      {seenBy && (
        <div className="railsec seen">
          <span className="seclabel">Sharing</span>
          <div className="secbody">{seenBy}</div>
        </div>
      )}

      {/* What you can do to it. Editing unfolds the options INTO this row out of
          the Edit button — which becomes Done where it stood, so the click that
          opened them is the click that closes them. Compare holds the left edge
          in both states, and nothing else moves. */}
      {/* No label over this one. `SHARING` needs one because pills alone do not
          say what they are claiming; a row of buttons says what it is. */}
      {(actions || (editing && editbar)) && (
        <div className={`railsec acts ${editing ? 'onedit' : ''}`}>
          <div className="secbody">{editing && editbar ? editbar : actions}</div>
        </div>
      )}

      {/* One line, one kind of control: three switches that say which pulls are
          being counted. They are the list's own checkboxes in bulk — the state
          you see here is the state of the rows below, and a half-ticked kind
          says so rather than pretending it is off. */}
      {onToggle && kinds.length > 0 && (
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

      <div className="raillist">
        {/* The root of the tree, and it stays put while the fights scroll under
            it — the total is the thing you are comparing every row against. */}
        {/* "Zonewide" only where it is true: a zone run is one zone by
            construction (hideZones), a session file can span several. */}
        {row('all', {
          label: hideZones ? 'Zonewide' : 'All fights',
          dur: longClock(sumDur(shown)),
          encs: shown,
          extra: 'all',
        })}
        <div className="railkids">
          {blocks.map((b, bi) => (
            <div className="railzone" key={bi}>
              {!hideZones && row(idsOf(visible(b.encounters)) || `zone:${bi}`, {
                label: b.zone || 'Unknown zone',
                dur: longClock(sumDur(visible(b.encounters))),
                encs: visible(b.encounters),
                all: b.encounters,
                edits: true,
                extra: 'zone',
              })}
              {b.nodes.map((n, ni) => {
                if (n.type === 'enc') return fightRow(n.enc, 0)
                const gkey = `${bi}:${ni}`
                const expanded = open[gkey]
                return (
                  <div key={gkey}>
                    {row(idsOf(visible(n.encs)) || `trash:${gkey}`, {
                      label: `Trash ×${visible(n.encs).length || n.encs.length}`,
                      /* the group counts what it contributes, so the pulls
                         held out of it are named beside the count rather than
                         silently missing from it */
                      tag: n.encs.length > visible(n.encs).length
                        ? `⊘${n.encs.length - visible(n.encs).length}` : null,
                      time: n.encs[0].started_ts,
                      dur: clock(sumDur(visible(n.encs))),
                      encs: visible(n.encs),
                      all: n.encs,
                      hidden: visible(n.encs).length === 0,
                      edits: true,
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
            {allSelected ? 'Counting all' : `${countSel} of ${shown.length}`}
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
