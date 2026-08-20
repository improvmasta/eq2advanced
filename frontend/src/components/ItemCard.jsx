import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

/* The item card, and the thing it hangs off.

   Lives here rather than in LootPanel because there are now two ways to meet
   an item and they must produce the SAME card: a chest drop on the Loot tab,
   and a link somebody posted in Auction on /chat. The server side of that
   agreement is `items.display` — one definition of what a card's fields are —
   and this is the other half of it. A second examine window drawn slightly
   differently would be a worse bug than no second one. */

/* The same five tokens the gear list uses (pages/Character.jsx). Tiers with no
   token of their own — UNCOMMON, HANDCRAFTED — take the default text colour
   rather than borrowing somebody else's meaning. */
const RARITY = new Set(['common', 'treasured', 'legendary', 'fabled', 'mythical'])
const rarityClass = (r) => {
  const k = (r || '').toLowerCase()
  return RARITY.has(k) ? `rarity-${k}` : ''
}

/* One hover card, positioned once.

   Fixed and in `document.body`, placed from the anchor's rect — the same
   reason Picker's menu cannot live where it was opened from: the table
   scrolls sideways inside `.tablewrap`, and a card parented to a cell is
   clipped by it. Reposition on scroll with capture, so the table's own
   scrolling counts and not just the page's.

   Focus opens it as well as hover: the anchors here are links and text a
   keyboard can reach, and hover must not be the only way in.

   HOVER IS NOT `mouseenter`/`mouseleave`. A pointer only enters or leaves an
   element when the POINTER moves, and on /chat it is the page that moves: the
   box scrolls itself every time a line arrives. So a link slides out from under
   a still cursor with no leave event and the card hangs there pointing at
   nothing — and the next line slides IN under the same still cursor with no
   enter event, so you are sitting on a link and no card comes. Two opposite
   complaints, one cause. (The loot table does the same thing sideways inside
   `.tablewrap`.)

   So hover is treated as what it actually is — a fact about WHERE THE POINTER
   IS — and recomputed from the pointer's last position after every move and
   every scroll, by asking the document what is under that point. See `settle`
   below. What each card still does for itself is where to draw. */
const EDGE = 8          // how close to the viewport edge a card may sit

/* ---- who is under the pointer, for the whole page ------------------------

   One hit test per pointer move and one per scroll FRAME, whatever the page is
   made of — four hundred chat lines do not each measure themselves. The
   listeners are attached only while something on the page is hoverable, and
   are gone again the moment the last card unmounts.

   Keyboard-opened cards are not this mechanism's business: it never opens one
   and never takes one away. */
const PTR = { x: 0, y: 0, live: false }
const ANCHORS = new Map()   // anchor node -> { enter, leave, holds }
let hovered = null          // the anchor the POINTER has open, if any
let frame = 0

function settle() {
  frame = 0
  const el = PTR.live ? document.elementFromPoint(PTR.x, PTR.y) : null
  /* A card tall enough to scroll takes the pointer back (`grab`), so while it
     has it, it stands in for the anchor it belongs to. Every other card is
     `pointer-events: none` and this hit test looks straight through it. */
  const on = el && hovered && ANCHORS.get(hovered)?.holds(el)
    ? hovered
    : (el?.closest?.('.hoverbox') ?? null)
  const node = on && ANCHORS.has(on) ? on : null
  if (node === hovered) return
  ANCHORS.get(hovered)?.leave()
  hovered = node
  ANCHORS.get(node)?.enter()
}

const soon = () => { if (!frame) frame = requestAnimationFrame(settle) }
const moved = (e) => {
  PTR.x = e.clientX
  PTR.y = e.clientY
  PTR.live = true
  settle()
}
// The pointer off the document, or the window losing focus, is the pointer
// nowhere — neither of which sends a leave to the thing it was over.
const gone = () => { PTR.live = false; settle() }

function watch(on) {
  const doc = on ? document.addEventListener : document.removeEventListener
  const win = on ? window.addEventListener : window.removeEventListener
  doc.call(document, 'pointermove', moved, true)
  doc.call(document, 'mouseleave', gone)
  win.call(window, 'blur', gone)
  // Capture, so a box scrolling inside the page counts and not just the page.
  win.call(window, 'scroll', soon, true)
  win.call(window, 'resize', soon)
}

/* Opened by the keyboard is a different contract: it closes on blur or Escape
   and a passing mouse must not take it away. `:focus-visible` is the browser's
   own answer to "did they tab here or click here"; where it is not understood,
   any focus counts, which is the old behaviour. */
function keyed(el) {
  try { return el.matches(':focus-visible') } catch { return true }
}

function Hover({ className, width, card, onOpen, children, block = false }) {
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState(null)
  const box = useRef(null)
  const pop = useRef(null)
  const byKey = useRef(false)  // opened by the keyboard, so hover cannot close it
  const grab = useRef(false)   // this card takes the pointer (it scrolls)
  const fill = useRef(onOpen)  // whatever the card needs fetching before it draws
  fill.current = onOpen

  const close = () => { byKey.current = false; setAt(null); setOpen(false) }

  /* Join the page's one pointer watcher. Registering also asks it to look
     again, because a link can APPEAR under a cursor that is already there —
     which on a live chat page is not the rare case. */
  useEffect(() => {
    const node = box.current
    ANCHORS.set(node, {
      enter: () => setOpen(true),
      leave: () => { if (!byKey.current) close() },
      holds: (el) => grab.current && !!pop.current?.contains(el),
    })
    if (ANCHORS.size === 1) watch(true)
    soon()
    return () => {
      ANCHORS.delete(node)
      if (hovered === node) hovered = null
      if (!ANCHORS.size) watch(false)
    }
  }, [])

  useLayoutEffect(() => {
    if (!open) return undefined
    // One request per opening, and only when something actually opened.
    fill.current?.()
    const place = () => {
      const r = box.current?.getBoundingClientRect()
      if (!r) return
      /* MEASURE the card rather than guessing its height. These cards are not
         one size — a weapon with a proc and a four-line description is more
         than twice a pattern's — so a fixed "does it fit below" threshold
         cut the tall ones off at the bottom of the window. Below if it fits,
         above if it fits there, and otherwise pinned to the top edge with a
         cap that makes it scrollable. */
      const h = pop.current?.offsetHeight || 0
      const room = window.innerHeight - 2 * EDGE
      grab.current = h > room
      let top = r.bottom + 6
      if (top + h > window.innerHeight - EDGE) {
        const above = r.top - 6 - h
        top = above >= EDGE ? above : Math.max(EDGE, window.innerHeight - EDGE - h)
      }
      setAt({
        left: Math.min(r.left, Math.max(window.innerWidth - width - 16, EDGE)),
        top,
        // Only bites when the card is taller than the window; `auto` on a
        // card that fits adds no scrollbar.
        maxHeight: room,
        overflowY: h > room ? 'auto' : undefined,
        // A card that must scroll has to be reachable, so that one — and only
        // that one — takes the pointer back.
        pointerEvents: grab.current ? 'auto' : undefined,
      })
    }
    place()
    const esc = (e) => { if (e.key === 'Escape') close() }
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    document.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
      document.removeEventListener('keydown', esc)
    }
    // `open` is the flag and `at` is where it landed; keying this on the
    // position would re-place forever.
  }, [open])

  const Anchor = block ? 'div' : 'span'
  return (
    <>
      {/* `hoverbox` is what `settle` looks for; the pointer never touches the
          card itself unless the card is one that scrolls. */}
      <Anchor ref={box} className="hoverbox"
            onFocus={(e) => {
              if (!keyed(e.target)) return
              byKey.current = true
              setOpen(true)
            }}
            onBlur={() => {
              if (!byKey.current) return
              byKey.current = false
              // Tabbing out of a link the mouse happens to be resting on hands
              // the card back to the pointer rather than shutting it.
              if (hovered !== box.current) close()
            }}>
        {children}
      </Anchor>
      {open && createPortal(
        // Hidden for the one frame between rendering (so it can be measured)
        // and being placed — otherwise a tall card flashes at the wrong spot.
        <div ref={pop} className={className} role="tooltip"
             style={{ ...at, visibility: at?.top === undefined ? 'hidden' : undefined }}>
          {card}
        </div>,
        document.body)}
    </>
  )
}

/* The examine window, drawn rather than photographed.

   It is a replica of EQ2i's item box, which is itself a replica of the
   in-game examine window: black, Times, a glowing rarity word, yellow
   uppercase flags, a green block of flat stats and a light-blue one of
   property modifiers. The class names and the colours are the wiki's own
   (`MediaWiki:ExamineWindow.css`, mirrored into base.css under `.ew-*`), so
   this looks like the page it is quoting.

   Nothing is scraped. EQ2i builds that box out of the same Census record we
   already hold, so the card is OUR data in THEIR clothes — which means no
   third-party HTML in the page, no sanitiser to get wrong, no request on
   hover, and it still works for the items whose wiki page does not exist.
   `backend/items.py` decides what is in each block; this only paints it.

   It does NOT theme. An examine window is black in a light client too — this
   is a quotation of the game's own UI, and recolouring it would be the one
   change that stops it looking like the thing it is. */
const num = (r, showPercent = true) => `${r.value}${showPercent && r.pct ? '%' : ''}`

function SetBonusLadder({ bonuses }) {
  return (bonuses || []).map((bonus, index) => {
    const stats = (bonus.stat_lines || []).filter(Boolean)
    const headline = stats.join(', ')
    const details = [
      ...(bonus.effect ? [bonus.effect] : []),
      ...(bonus.descriptions || []),
    ]
    return (
      <div className="ew-set-bonus" key={`${bonus.required}-${index}`}>
        <div className="ew-set-tierhead">
          <span className="ew-set-required">({bonus.required})</span>
          {headline && <span>{headline}</span>}
        </div>
        {!!details.length && (
          <div className="ew-set-details">
            {details.map((line, detailIndex) => (
              <div key={detailIndex} className={detailIndex ? 'nested' : ''}>
                <span aria-hidden="true">•</span><span>{line}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  })
}

function Examine({ row, characterClass = null, tradeskillClass = null }) {
  const s = row.stats
  const w = s?.weapon
  const fx = row.effects
  const quality = (row.rarity || '').toLowerCase()
  const normalizedClass = String(characterClass || '').trim().toLowerCase()
  const allowedClasses = (row.classes || []).map((name) => ({
    name, matches: String(name).trim().toLowerCase() === normalizedClass,
  }))
  const normalizedTradeskill = String(tradeskillClass || '').trim().toLowerCase()
  const allowedTradeskills = (row.tradeskill_classes || []).map((name) => ({
    name, matches: String(name).trim().toLowerCase() === normalizedTradeskill,
  }))
  const unusableByClass = !!normalizedClass && !!allowedClasses.length
    && !allowedClasses.some((entry) => entry.matches)
  const unusableByTradeskill = !!normalizedTradeskill && !!allowedTradeskills.length
    && !allowedTradeskills.some((entry) => entry.matches)
  const unusable = unusableByClass || unusableByTradeskill
  const adorn = s?.adornment
  const included = s?.included_adornment
  const installed = row.installed_adornments || []
  const socketAdorns = (() => {
    const left = [...installed]
    return (s?.adornments || []).map((color) => {
      const at = left.findIndex((item) => item.stats?.adornment?.color === color)
      return at >= 0 ? left.splice(at, 1)[0] : null
    })
  })()
  const installedStats = installed.flatMap((item) => item.stats?.stats || [])
  const installedEffects = installed.flatMap((item) => item.stats?.effects || [])
  const installedNames = installed.flatMap((item) => item.effects?.names || [])
  const procNames = [...(fx?.names || []), ...installedNames]
  const effectDesc = [
    ...(fx?.desc || []),
    ...installed.flatMap((item) => item.effects?.desc || []),
  ]
  const installedSets = installed.filter(
    (item) => item.stats?.adornment?.set_bonuses?.length,
  )
  const installedSetFlags = installedSets.flatMap((item) => item.stats?.flags || [])
  const hostRequiresEquip = !adorn && (
    included?.requires_equip
    || installedSets.some((item) => item.stats?.adornment?.requires_equip))
  const bottomFlags = Array.from(new Set(adorn
    ? [...(s?.flags || []), ...(adorn.requires_equip ? ['Requires-Equip'] : [])]
    : [...(s?.flags || []), ...installedSetFlags,
        ...(hostRequiresEquip ? ['Requires-Equip'] : [])]))
  const compactSets = (() => {
    if (adorn) return []
    const rows = row.set_progress?.length
      ? row.set_progress
      : [
          ...installedSets.map((item) => ({
            name: item.set_name || item.name.replace(/:\s*[^:]+$/, ''),
          })),
          ...(included ? [{ name: included.name, total: included.total }] : []),
        ]
    const seen = new Set()
    return rows.filter((set) => set?.name && !seen.has(set.name) && seen.add(set.name))
  })()
  const modifierColumns = (() => {
    const base = s?.effects || []
    const all = [...base, ...installedEffects]
    const anchored = ['Potency', 'Crit Chance'].flatMap(
      (name) => all.filter((entry) => entry.name === name),
    )
    if (anchored.length) {
      const left = [...anchored, ...installedEffects.filter(
        (entry) => entry.name !== 'Potency' && entry.name !== 'Crit Chance')]
      const right = base.filter(
        (entry) => entry.name !== 'Potency' && entry.name !== 'Crit Chance')
      /* RoK gear does not always carry Crit Chance. Fill that missing anchor
         with the first remaining modifier rather than leaving Potency alone
         opposite a long right column. */
      if (anchored.length === 1 && left.length === 1 && right.length > 1) {
        left.push(right.shift())
      }
      return [left, right]
    }
    const cut = Math.ceil(all.length / 2)
    return [all.slice(0, cut), all.slice(cut)]
  })()
  return (
    <div className="examinewindow">
      <div className="ew-top">
        <div className="ew-titles">
          <div className={`ew-title${unusable ? ' ew-unusable' : ''}`}>{row.name}</div>
          {row.description && <div className="ew-description">{row.description}</div>}
          {row.rarity && (
            <div className={`itemquality xqc-${quality}`}>
              {row.rarity.toUpperCase()}
            </div>
          )}
        </div>
        {row.icon != null && (
          <img className="ew-icon" src={`/api/items/icon/${row.icon}.png`}
               alt="" width="42" height="42" />
        )}
      </div>

      {!!s?.adornments.length && (
        <div className="ew-adorn">
          {s.adornments.map((c, i) => {
            const item = socketAdorns[i]
            return (
              <img key={i}
                   src={item?.icon != null
                     ? `/api/items/icon/${item.icon}.png`
                     : `/api/items/adorn/${c}.png`}
                   width="24" height="24" alt={`${c} adornment slot`}
                   title={item?.name || `${c} adornment slot`} />
            )
          })}
        </div>
      )}

      {/* Green attributes read across: Primary Attributes beside Stamina. The
          blue modifier block below fills down; Potency and Crit Chance anchor
          the left column when present, with another modifier filling a missing
          second anchor. */}
      {(!!s?.stats.length || !!installedStats.length) && (
        <div className="ew-stats ew-cols">
          {[...(s?.stats || []), ...installedStats].map((r, i) => (
            <div key={`${r.name}-${i}`}>{num(r)}&nbsp;{r.name}</div>
          ))}
        </div>
      )}
      {/* Numeric modifiers stay in the blue block. Proc names are item facts,
          not figures, and appear in the centered game-style heading below. */}
      {(!!s?.effects.length || !!installedEffects.length) && (
        <div className="ew-effectlist">
          <div className="ew-cols">
            {modifierColumns.map((column, columnIndex) => (
              <div className="ew-effectcol" key={columnIndex}>
                {column.map((r, i) => (
                  <div key={`${r.name}-${i}`}>{num(r, false)}&nbsp;{r.name}</div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {!!compactSets.length && (
        <div className="ew-setprogress">
          {compactSets.map((set) => (
            <div key={set.name}>
              {set.name}
              {set.count != null && set.total ? ` [${set.count}/${set.total}]` : ''}
            </div>
          ))}
        </div>
      )}

      <table className="ew-facts">
        <tbody>
          {/* A weapon leads with what it hits for. The range is the BASE
              damage and the figure beside the delay is the rating, which is
              how the item box reads it. */}
          {w && (
            <tr>
              <td className="ew-low">Damage</td>
              <td className="ew-high">
                {w.low} - {w.high}{w.style ? <> &nbsp;&nbsp;{w.style}</> : null}
              </td>
            </tr>
          )}
          {w?.delay != null && (
            <tr>
              <td className="ew-low">Delay</td>
              <td className="ew-high">
                {w.delay.toFixed(1)} seconds
                {w.rating != null && <> &nbsp;&nbsp;({w.rating} Rating)</>}
              </td>
            </tr>
          )}
          {adorn && (
            <tr><td className="ew-low">Item Type</td><td className="ew-high">{adorn.slots.join(', ') || 'Adornment'}</td></tr>
          )}
          {adorn?.color && (
            <tr>
              <td className="ew-low">Slot Type</td>
              <td className={`ew-high ew-adorn-${adorn.color}`}>{adorn.color[0].toUpperCase() + adorn.color.slice(1)} Adornment Slot</td>
            </tr>
          )}
          {adorn?.predicate && (
            <tr><td className="ew-low">Predicates</td><td className="ew-high">{adorn.predicate}</td></tr>
          )}
          {row.slot && !adorn && (
            <tr><td className="ew-low">Slot</td><td className="ew-high">{row.slot}</td></tr>
          )}
          {row.type && !w && !adorn && (
            <tr><td className="ew-low">Type</td><td className="ew-high">{row.type}</td></tr>
          )}
          {!!row.level && (
            <tr>
              <td className="ew-low">Level</td>
              <td className="ew-high">
                {row.level}{row.tier && !adorn ? <sup> (Tier {row.tier})</sup> : null}
              </td>
            </tr>
          )}
          {/* One line is ours and not EQ2i's: the wiki cannot know which
              chest on which pull this came out of, and the raid log does. An
              item LINKED in chat has no such line — nobody dropped it, someone
              was selling it — so the row is absent rather than empty. */}
          {row.mob && (
            <tr>
              <td className="ew-low">Dropped by</td>
              <td className="ew-high">{row.mob}</td>
            </tr>
          )}
        </tbody>
      </table>

      {/* WHO CAN WEAR IT. The one property that rules an item out before any
          number on it matters, and the reason a search can come back empty
          with the broker full of the thing you asked for. Drawn only when it
          is a RESTRICTION: the source sends nothing when every class on the
          server can equip it, the same silence the game keeps. */}
      {!!row.classes?.length && (
        <div className={`ew-classes${adorn ? ' ew-adornclasses' : ''}`}>
          {allowedClasses.map((entry, index) => (
            <span key={entry.name}
                  className={normalizedClass && !entry.matches ? 'ew-class-unusable' : ''}>
              {index ? ', ' : ''}{entry.name}
            </span>
          ))}
        </div>
      )}

      {!!allowedTradeskills.length && (
        <div className="ew-classes ew-artisan">
          <b>Artisan:</b>{' '}
          {allowedTradeskills.map((entry, index) => (
            <span key={entry.name}
                  className={normalizedTradeskill && !entry.matches
                    ? 'ew-class-unusable' : ''}>
              {index ? ', ' : ''}{entry.name}
            </span>
          ))}
        </div>
      )}

      {!!procNames.length && (
        <div className="ew-procnames">
          {procNames.map((name, index) => (
            <div className="ew-procname" key={`${name}-${index}`}>{name}</div>
          ))}
        </div>
      )}

      {!!adorn?.set_bonuses.length && (
        <div className="ew-set">
          {!!row.set_progress?.length && (
            <div className="ew-adornsetprogress">
              {row.set_progress.map((set) => (
                <div key={set.name}>
                  {set.name}
                  {set.count != null && set.total ? ` ${set.count}/${set.total}` : ''}
                </div>
              ))}
            </div>
          )}
          <div className="ew-set-name">
            {fx?.set || row.name.replace(/:\s*[^:]+$/, '')}:
          </div>
          <SetBonusLadder bonuses={adorn.set_bonuses} />
        </div>
      )}

      {!!effectDesc.length && (
        <>
          <div className="ew-effects">Effects:</div>
          <div className="ew-effectdesc">
            {effectDesc.map((d, i) => (
              <div className="ew-effectline" key={i}
                   style={{ marginLeft: `${Math.max(0, d.depth - 1) * 12}px` }}>
                <span className="ew-effectbullet" aria-hidden="true">•</span>
                <span>{d.text}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {!!bottomFlags.length && <div className="ew-flags">{bottomFlags.join(',  ')}</div>}

      {!s && (
        <div className="ew-none">
          Census lists no equipment stats — scrolls, patterns and harvestables
          have none.
        </div>
      )}
    </div>
  )
}

/* The two things a card needs that are not the item: `mob` (which chest, on
   which pull) is the loot list's alone, and its absence is how a chat link
   draws the same window with one fewer row. */
export { Hover, Examine, rarityClass }
