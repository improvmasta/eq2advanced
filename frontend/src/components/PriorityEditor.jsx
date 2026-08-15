import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

/* What you are pushing, in the order you push it.

   THE LIST IS AN ORDER, NOT A SET OF NUMBERS, AND NO WEIGHT IS EVER SHOWN.
   You say "ability mod, then reuse, then casting speed" and the server turns
   the ranks into a decaying weight it never hands back. Sliders were
   considered and rejected: a slider invites tuning and implies the third
   decimal place means something, and this tool does not make the choice for
   you — it ranks options and you choose (docs/planner.md).

   Stats NOT in the list are not scored. Absence is a statement, which is why
   there is no "off" state and no zero — you drop a stat off the list instead.

   A stat can be marked REQUIRED, which moves it from ranking to filtering.
   That is the one control here that crosses the line on purpose: "I will not
   look at anything without ability mod" is a hard fact about what you will
   consider, and no weight can express it. The toggle is visually distinct from
   the drag handle for the same reason — they do different kinds of thing.

   THE PANEL RENDERS INTO `document.body`. Every card on this site carries
   `backdrop-filter`, which makes the card a containing block for
   `position: fixed`, so a panel written inside one is sealed into it however
   high its z-index goes — the same trap Picker.jsx and ShotViewer.jsx name. */

export default function PriorityEditor({
  groups, order, required, onChange, onClose,
}) {
  const [drag, setDrag] = useState(null)      // the key being dragged
  const [over, setOver] = useState(null)      // the key it is hovering
  const box = useRef(null)
  const label = useMemo(() => Object.fromEntries(
    groups.flatMap((g) => g.stats).map((s) => [s.key, s.label])), [groups])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    const onDown = (e) => {
      if (box.current && !box.current.contains(e.target)) onClose()
    }
    document.addEventListener('keydown', onKey)
    // `mousedown`, not `click`: a click that started inside and ended outside
    // (a drag that left the panel) must not close it.
    document.addEventListener('mousedown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onDown)
    }
  }, [onClose])

  const inList = new Set(order)

  function move(from, to) {
    if (from === to) return
    const next = order.filter((k) => k !== from)
    next.splice(next.indexOf(to) < 0 ? next.length : next.indexOf(to), 0, from)
    onChange({ order: next, required })
  }
  const add = (key) => onChange({ order: [...order, key], required })
  const drop = (key) => onChange({
    order: order.filter((k) => k !== key),
    required: required.filter((k) => k !== key),
  })
  const toggleReq = (key) => onChange({
    order,
    required: required.includes(key)
      ? required.filter((k) => k !== key)
      : [...required, key],
  })

  return createPortal(
    <div className="prioback">
      <div className="priopanel card" ref={box}>
        <div className="priohead">
          <h3>What are you pushing?</h3>
          <button className="chip" onClick={onClose}>Done</button>
        </div>
        <p className="muted">
          Drag to reorder. The order is all this tool is told — there are no
          weights to tune. Anything not on the list is not scored.
        </p>
        {/* Said once, here, because it is the question the list invites:
            potency and crit are on four items in five, so ranking by them
            ranks by nothing. Crit bonus is absent for a different reason
            again — TLE does not have the stat. */}
        <p className="muted priowhy">
          Potency and crit are not here: they are on nearly every item in these
          expansions, so ordering by them separates nothing. Crit bonus is not
          on TLE at all.
        </p>

        {order.length === 0 && (
          <p className="muted priohint">
            Nothing listed yet, so nothing is ranked. Add a stat below.
          </p>
        )}
        <ol className="priolist">
          {order.map((key) => (
            <li key={key}
                className={`${drag === key ? 'dragging' : ''}${over === key ? ' over' : ''}`}
                draggable
                onDragStart={() => setDrag(key)}
                onDragEnd={() => { setDrag(null); setOver(null) }}
                onDragOver={(e) => { e.preventDefault(); setOver(key) }}
                onDrop={(e) => { e.preventDefault(); move(drag, key); setOver(null) }}>
              <span className="priohandle" aria-hidden="true">⠿</span>
              <span className="prioname">{label[key] || key}</span>
              {/* Keyboard reaches the same reordering the pointer does — a
                  drag handle is not an accessible control on its own. */}
              <span className="priomove">
                <button className="iconbtn" title="Move up" aria-label={`Move ${label[key]} up`}
                        disabled={order[0] === key}
                        onClick={() => move(key, order[order.indexOf(key) - 1])}>↑</button>
                <button className="iconbtn" title="Move down" aria-label={`Move ${label[key]} down`}
                        disabled={order[order.length - 1] === key}
                        onClick={() => move(key, order[order.indexOf(key) + 1])}>↓</button>
              </span>
              <button className={`chip req${required.includes(key) ? ' on' : ''}`}
                      title="Only show items that have this at all"
                      onClick={() => toggleReq(key)}>Required</button>
              <button className="iconbtn" title="Remove" aria-label={`Remove ${label[key]}`}
                      onClick={() => drop(key)}>✕</button>
            </li>
          ))}
        </ol>

        {/* Grouped the way a raider already thinks about them: the first
            group reaches every class, because every class casts abilities,
            and the other two are what a melee and a tank are shopping for.
            A group with nothing left in it is gone rather than empty. */}
        <div className="prioadd">
          {groups.map((g) => {
            const left = g.stats.filter((s) => !inList.has(s.key))
            if (!left.length) return null
            return (
              <div className="priocluster" key={g.label}>
                <div className="seclabel">{g.label}</div>
                <div className="priogroup">
                  {left.map((s) => (
                    <button key={s.key} className="chip" onClick={() => add(s.key)}>
                      + {s.label}
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>,
    document.body)
}
