import { useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import SortableTable from './SortableTable.jsx'

/* What the chests gave, and who ended up with it.

   **Chests only.** Body drops are shards, body parts and vendor coin — a
   couple of hundred lines a night that would bury the eight items the raid
   actually remembers — so the parser never records them and there is nothing
   to filter here (`backend/pipeline/loot.py`).

   The rarity is CENSUS's, not the log's. The log does write the word — `X
   looted the Fabled <ITEM>.` — but only for people standing near you, so two
   thirds of a raid's drops would have a blank column. What the log line is
   good for is the other question: whether the winner actually TOOK it, which
   is `confirmed` and shows as the note under a roll nobody claimed.

   Two hovers, because there are two questions about a line of loot: WHAT is
   it (the examine window, over the name) and WHO ELSE WANTED IT (the roll
   list, over the looter).

   No bars, no shares. A drop is a thing that happened once; there is no rate
   to draw and nothing to be a percentage of. */

/* The same five tokens the gear list uses (pages/Character.jsx). Tiers with no
   token of their own — UNCOMMON, HANDCRAFTED — take the default text colour
   rather than borrowing somebody else's meaning. */
const RARITY = new Set(['common', 'treasured', 'legendary', 'fabled', 'mythical'])
const rarityClass = (r) => {
  const k = (r || '').toLowerCase()
  return RARITY.has(k) ? `rarity-${k}` : ''
}

/* Rarest first when sorting by rarity, which is the order a raider reads a
   loot list in. An unknown tier sorts to the bottom rather than the top. */
const RANK = { mythical: 5, fabled: 4, legendary: 3, treasured: 2, uncommon: 1 }

/* One hover card, positioned once.

   Fixed and in `document.body`, placed from the anchor's rect — the same
   reason Picker's menu cannot live where it was opened from: the table
   scrolls sideways inside `.tablewrap`, and a card parented to a cell is
   clipped by it. Reposition on scroll with capture, so the table's own
   scrolling counts and not just the page's.

   Focus opens it as well as hover: the anchors here are links and text a
   keyboard can reach, and hover must not be the only way in. */
const EDGE = 8          // how close to the viewport edge a card may sit

function Hover({ className, width, card, children }) {
  const [at, setAt] = useState(null)
  const box = useRef(null)
  const pop = useRef(null)

  useLayoutEffect(() => {
    if (!at) return undefined
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
        pointerEvents: h > room ? 'auto' : undefined,
      })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
    // `at` is the open flag; re-placing on its own change would loop
  }, [at !== null])

  return (
    <>
      <span ref={box}
            onMouseEnter={() => setAt({})} onMouseLeave={() => setAt(null)}
            onFocus={() => setAt({})} onBlur={() => setAt(null)}>
        {children}
      </span>
      {at && createPortal(
        // Hidden for the one frame between rendering (so it can be measured)
        // and being placed — otherwise a tall card flashes at the wrong spot.
        <div ref={pop} className={className} role="tooltip"
             style={{ ...at, visibility: at.top === undefined ? 'hidden' : undefined }}>
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
const num = (r) => `${r.value}${r.pct ? '%' : ''}`

function Examine({ row }) {
  const s = row.stats
  const w = s?.weapon
  const fx = row.effects
  const quality = (row.rarity || '').toLowerCase()
  return (
    <div className="examinewindow">
      <div className="ew-top">
        <div className="ew-titles">
          <div className="ew-title">{row.name}</div>
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

      {!!s?.flags.length && <div className="ew-flags">{s.flags.join(',  ')}</div>}
      {!!s?.adornments.length && (
        <div className="ew-adorn">
          {s.adornments.map((c, i) => (
            <img key={i} src={`/api/items/adorn/${c}.png`} width="24" height="24"
                 alt={`${c} adornment slot`} title={`${c} adornment slot`} />
          ))}
        </div>
      )}

      {!!s?.stats.length && (
        <div className="ew-stats">
          {s.stats.map((r) => <div key={r.name}>{num(r)}&nbsp;{r.name}</div>)}
        </div>
      )}
      {/* The proc's NAME sits with the modifiers, in the same light blue, and
          its description gets its own block below — EQ2i's own arrangement. */}
      {(!!s?.effects.length || !!fx?.names.length) && (
        <div className="ew-effectlist">
          {s?.effects.map((r) => <div key={r.name}>{num(r)}&nbsp;{r.name}</div>)}
          {fx?.names.map((n) => <div key={n}>{n}</div>)}
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
          {row.slot && (
            <tr><td className="ew-low">Slot</td><td className="ew-high">{row.slot}</td></tr>
          )}
          {row.type && !w && (
            <tr><td className="ew-low">Type</td><td className="ew-high">{row.type}</td></tr>
          )}
          {!!row.level && (
            <tr>
              <td className="ew-low">Level</td>
              <td className="ew-high">
                {row.level}{row.tier ? <sup> (Tier {row.tier})</sup> : null}
              </td>
            </tr>
          )}
          {/* One line is ours and not EQ2i's: the wiki cannot know which
              chest on which pull this came out of, and the raid log does. */}
          <tr>
            <td className="ew-low">Dropped by</td>
            <td className="ew-high">{row.mob}</td>
          </tr>
        </tbody>
      </table>

      {!!fx?.desc.length && (
        <>
          <div className="ew-effects">Effects:</div>
          <div className="ew-effectdesc">
            {fx.desc.map((d, i) => (
              <div key={i} style={{ paddingLeft: `${(d.depth - 1) * 12}px` }}>
                {d.text}
              </div>
            ))}
          </div>
        </>
      )}

      {!s && (
        <div className="ew-none">
          Census lists no equipment stats — scrolls, patterns and harvestables
          have none.
        </div>
      )}
    </div>
  )
}

/* Who else wanted it.

   Two ways a raid decides that, and the card says which one it is looking at
   because they are not equally certain:

   - LOTTO — the game's own. It prints the whole contest against the item by
     name, so it cannot be wrong, and the winner is always the top line here:
     NEED beats GREED before any number is compared, and the sort says so
     (checked against 752 real blocks, no exceptions).
   - DICE (`/random`) — a raid running loot by hand. The rolls say nothing
     about WHICH item they are for, so they are tied to one either by an
     announcement that linked it (`announced`) or by nothing more than
     proximity (`nearby`) — and a guess has to look like a guess.

   (Bold markers are spelled out rather than asterisked on purpose: a doubled
   asterisk before a slash closes a block comment, and it cost a silent build
   failure here already.)

   A roll with no number is somebody the log recorded as choosing but whose
   die it did not show. */
const ROLL_SOURCE = {
  lotto: { label: 'Lotto', note: null },
  announced: {
    label: '/random',
    note: 'Rolled after this item was called out in chat.',
  },
  nearby: {
    label: '/random',
    note: 'Rolled around this drop. Nobody linked the item, so this is the '
      + 'nearest contest in time, not a certainty.',
  },
}

function Rolls({ row }) {
  const { source, rolls } = row.rolls
  const meta = ROLL_SOURCE[source] || ROLL_SOURCE.nearby
  return (
    <div className="rollcard">
      <div className="rollhead">{meta.label} · {row.name}</div>
      <table className="rolltable">
        <tbody>
          {rolls.map((r, i) => (
            <tr key={i} className={r.who === row.looter ? 'won' : ''}>
              <td className="c">{r.choice || ''}</td>
              <td className="n">{r.value == null ? '—' : r.value}</td>
              <td>{r.who}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {meta.note && <div className="rollnote">{meta.note}</div>}
    </div>
  )
}

function Item({ row }) {
  const label = <span className={rarityClass(row.rarity)}>{row.name}</span>
  return (
    <span className="lootitem">
      {/* A missing icon leaves the slot empty rather than collapsing the
          column — the names stay on one left edge down the whole table. */}
      <span className="looticon">
        {row.icon != null && (
          <img src={`/api/items/icon/${row.icon}.png`} alt="" width="24" height="24"
               loading="lazy" />
        )}
      </span>
      <Hover className="examinecard" width={350} card={<Examine row={row} />}>
        {row.wiki
          ? <a href={row.wiki} target="_blank" rel="noreferrer noopener">{label}</a>
          : label}
      </Hover>
      {row.qty > 1 && <span className="muted"> ×{row.qty}</span>}
    </span>
  )
}

function Looter({ row }) {
  const n = row.rolls?.rolls?.length
  const name = (
    <span className={n ? `hasrolls ${row.rolls.source}` : ''}>
      {row.looter}
      {/* Won the roll and never took it: the item's `looted` line never came.
          Worth saying rather than silently crediting them. */}
      {row.method === 'lotto' && !row.confirmed && (
        <span className="muted" title="Won the roll; no loot line followed"> · won</span>
      )}
    </span>
  )
  if (!n) return name
  return (
    <Hover className="rollpop" width={240} card={<Rolls row={row} />}>
      {name}
      {/* A dotted underline you can hover, and the count beside it — a card
          nobody knows is there is a card nobody opens. */}
      <span className="muted" title={`${n} rolled for it`}> ({n})</span>
    </Hover>
  )
}

export default function LootPanel({ data, err }) {
  if (err) return <p className="err">{err}</p>
  if (!data) return <p className="muted">Loading…</p>
  if (!data.loot.length) {
    return (
      <div className="card">
        <div className="drillhead"><h2>Chest loot</h2></div>
        <p className="muted">
          No chest was opened in this selection. Only chest loot is recorded —
          what dropped off a corpse is not.
        </p>
      </div>
    )
  }

  const columns = [
    {
      key: 'name', label: 'Item', align: 'l', fixed: true,
      render: (r) => <Item row={r} />,
      sortValue: (r) => r.name.toLowerCase(),
    },
    {
      key: 'rarity', label: 'Rarity', align: 'l',
      render: (r) => (r.rarity
        ? <span className={rarityClass(r.rarity)}>{r.rarity}</span> : ''),
      sortValue: (r) => RANK[(r.rarity || '').toLowerCase()] || 0,
    },
    { key: 'slot', label: 'Slot', align: 'l', render: (r) => r.slot || r.type || '' },
    {
      key: 'level', label: 'Lvl',
      render: (r) => r.level || '', sortValue: (r) => r.level || 0,
    },
    {
      key: 'looter', label: 'Looted by', align: 'l',
      render: (r) => <Looter row={r} />,
      sortValue: (r) => r.looter.toLowerCase(),
    },
    {
      key: 'fight', label: 'Fight', align: 'l',
      render: (r) => r.fight || <span className="muted">unattached</span>,
      groupedRender: () => '',
      sortValue: (r) => (r.fight || '~').toLowerCase(),
    },
    {
      key: 'mob', label: 'Chest', align: 'l',
      /* The mob whose chest it was, which is NOT always the fight's name — a
         chain pull is named for one mob and the chest belongs to another. */
      render: (r) => (
        <span title={r.chest}>
          {r.mob}
          {r.attribution === 'nearest' && (
            <span className="muted" title="Matched to the fight before it by time, not by name">
              {' '}·&nbsp;approx
            </span>
          )}
        </span>
      ),
      sortValue: (r) => r.mob.toLowerCase(),
    },
  ]

  return (
    <div className="card">
      <div className="drillhead">
        <h2>Chest loot</h2>
        <span className="muted">
          {data.loot.length} {data.loot.length === 1 ? 'item' : 'items'} · chests
          only, not corpse drops
        </span>
      </div>
      <SortableTable
        columns={columns}
        rows={data.loot}
        prefsKey="zonerun:loot"
        defaultSort={{ key: 'fight', dir: 'asc' }}
        rowKey={(r) => r.id}
        wrapClass="sticky"
        groupBy={{
          key: 'fight',
          of: (r) => r.fight || '—',
          label: (r) => r.fight || 'Not matched to a fight',
        }}
      />
      {data.unresolved > 0 && (
        <p className="note">
          {data.unresolved} of these items have not been looked up yet — the
          name is the log's; the picture, rarity and wiki link arrive once
          Census and the wiki have been asked.
        </p>
      )}
    </div>
  )
}
