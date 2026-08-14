import SortableTable from './SortableTable.jsx'
/* The examine card and its hover are SHARED with /chat — an item linked in
   Auction opens the same window a chest drop does. See ItemCard.jsx. */
import { Examine, Hover, rarityClass } from './ItemCard.jsx'

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


/* Rarest first when sorting by rarity, which is the order a raider reads a
   loot list in. An unknown tier sorts to the bottom rather than the top. */
const RANK = { mythical: 5, fabled: 4, legendary: 3, treasured: 2, uncommon: 1 }


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
