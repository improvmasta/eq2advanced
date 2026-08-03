import { useMemo, useState } from 'react'
import { fmt } from '../lib/api.js'

/* ACT-style encounter tree: zones -> fights, oldest first.
   - Root "All" node = the whole session.
   - Each zone visit is its own block (re-entering a zone later starts a new
     block, exactly like ACT's left pane); the zone header selects the block.
   - Consecutive trash pulls collapse into one expandable "Trash ×N" node.
   Selection is a comma-joined encounter-id list (or 'all'), owned by the URL. */

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
      if (e.is_named) { flush(); b.nodes.push({ type: 'enc', enc: e }) }
      else trash.push(e)
    }
    flush()
  }
  return blocks
}

const idsOf = (encs) => encs.map((e) => e.id).join(',')
const sumDur = (encs) => encs.reduce((s, e) => s + Math.max(e.duration_s, 1), 0)

/* hideZones: a zone run is one zone by construction — the block header would
   duplicate the root node, so the rail shows just the fight list. */
export default function EncounterTree({ encounters, sel, onSelect, sessionLabel, hideZones = false }) {
  const blocks = useMemo(() => buildTree(encounters), [encounters])
  const [open, setOpen] = useState({})   // trash-group key -> expanded

  const node = (key, label, meta, active, extra = '') => (
    <button
      key={key}
      className={`treenode ${extra} ${active ? 'active' : ''}`}
      onClick={() => onSelect(key)}
    >
      <span className="tl">{label}</span>
      {meta && <span className="tm">{meta}</span>}
    </button>
  )

  return (
    <nav className="tree" aria-label="Encounters">
      {node('all', sessionLabel || 'All', `${encounters.length} fights`, sel === 'all', 'root')}
      {blocks.map((b, bi) => {
        const blockIds = idsOf(b.encounters)
        return (
          <div className="treezone" key={bi}>
            {!hideZones && node(blockIds, b.zone || 'Unknown zone',
              `[${fmt.dur(sumDur(b.encounters))}]`, sel === blockIds, 'zone')}
            {b.nodes.map((n, ni) => {
              if (n.type === 'enc') {
                const e = n.enc
                return node(String(e.id),
                  e.is_named ? e.name : `${e.name} — ${fmt.time(e.started_ts)}`,
                  `[${fmt.dur(e.duration_s)}]`,
                  sel === String(e.id), e.is_named ? 'named' : 'trash')
              }
              const gkey = `${bi}:${ni}`
              const gids = idsOf(n.encs)
              const expanded = open[gkey]
              return (
                <div key={gkey}>
                  <div className="trashrow">
                    <button
                      className="twist"
                      aria-label={expanded ? 'Collapse trash' : 'Expand trash'}
                      onClick={() => setOpen((o) => ({ ...o, [gkey]: !expanded }))}
                    >
                      {expanded ? '▾' : '▸'}
                    </button>
                    {node(gids, `Trash ×${n.encs.length}`,
                      `[${fmt.dur(sumDur(n.encs))}]`, sel === gids, 'trash group')}
                  </div>
                  {expanded && n.encs.map((e, i) => node(String(e.id),
                    `Trash ${i + 1} — ${fmt.time(e.started_ts)}`,
                    `[${fmt.dur(e.duration_s)}]`, sel === String(e.id), 'trash sub'))}
                </div>
              )
            })}
          </div>
        )
      })}
    </nav>
  )
}
