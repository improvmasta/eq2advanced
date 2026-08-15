import { useCallback, useEffect, useMemo, useState } from 'react'
import Picker from '../components/Picker.jsx'
import PlanOutline from '../components/PlanOutline.jsx'
import PriorityEditor from '../components/PriorityEditor.jsx'
import SortableTable from '../components/SortableTable.jsx'
import Tabs from '../components/Tabs.jsx'
/* The examine card is SHARED with the Loot tab and /chat. There are now three
   ways to meet an item and all three must open the same window — the server
   hands this page its cards in `items.display`'s shape for exactly that
   reason (`backend/planner/catalog.py: card`). */
import { Examine, Hover, rarityClass } from '../components/ItemCard.jsx'
import { api } from '../lib/api.js'
import { useQueryState } from '../lib/useQueryState.js'

/* The Planner — what to chase in an expansion. See docs/planner.md.

   WHICH EXPANSIONS COUNT IS THE READER'S CHOICE, and it is the first control
   on the page: EoF, RoK, or both. Everything else — the facets, the scale a
   score is measured against, the sets — follows from that choice, which is why
   it sits in the rail head above the shortlist rather than among the filters.

   Two regions, permanently: a shortlist rail on the left and the main area on
   the right. That is `ZoneRun`'s geometry and it reuses its `.workspace` rules.
   The rail is the bridge — you fill it on Gear and consume it on Outline — so
   it is always visible rather than a thing you open. */

const SHORTLIST_KEY = 'eq2adv:plan:shortlist'
/* Where a raider starts: the group that applies whatever you play, because
   every class casts abilities. Not a recommendation and not a default anybody
   is stuck with — an empty priority list scores nothing at all, and a page
   that opens on an unranked table cannot show what it is for.

   POTENCY AND CRIT ARE NOT HERE AND ARE NOT OFFERED. They are on about four
   items in five in these expansions, so ordering by them separates nothing;
   the server refuses them too, so a hand-built URL cannot put them back
   (`planner/catalog.py: weights`). They remain on the examine card and are
   available as table columns. */
const OPENING_ORDER = ['abmod', 'acspeed', 'arspeed']

const KIND_LABEL = {
  raid: 'Raid', group: 'Group', solo: 'Solo', quest: 'Quest', unknown: 'Unknown',
}

function loadShortlist() {
  try {
    const saved = JSON.parse(localStorage.getItem(SHORTLIST_KEY)) || {}
    return {
      items: Array.isArray(saved.items) ? saved.items : [],
      sets: Array.isArray(saved.sets) ? saved.sets : [],
      targets: Array.isArray(saved.targets) ? saved.targets : [],
    }
  } catch { return { items: [], sets: [], targets: [] } }
}

const csv = (a) => (a && a.length ? a.join(',') : '')
const split = (s) => (s ? s.split(',').filter(Boolean) : [])

export default function Planner() {
  /* The plan lives in the URL, the way a comparison does on /compare: era,
     class and the priority order are what make this page YOURS, so a link to
     it is the plan and not just the page. */
  const [erasParam, setEras] = useQueryState('eras', 'rok')
  const [tabParam, setTab] = useQueryState('tab', 'gear')
  const [orderParam, setOrder] = useQueryState('order', OPENING_ORDER.join(','))
  const [reqParam, setReq] = useQueryState('req', '')
  const [cls, setCls] = useQueryState('class', '')
  const [slot, setSlot] = useQueryState('slot', '')
  const [tier, setTier] = useQueryState('tier', '')
  const [kind, setKind] = useQueryState('kind', '')
  const [armor, setArmor] = useQueryState('armor', '')
  /* Blank means "whatever the four-stat floor says" — the server decides and
     answers back, so the control shows a real number without the page having
     to duplicate the rule. */
  const [match, setMatch] = useQueryState('match', '')
  const [mode, setMode] = useQueryState('mode', 'items')
  const [q, setQ] = useQueryState('q', '')
  const [carries, setCarries] = useQueryState('set', '')
  const [proc, setProc] = useQueryState('proc', '')

  const eras = useMemo(() => split(erasParam), [erasParam])
  const tab = tabParam === 'outline' ? 'outline' : 'gear'
  const order = useMemo(() => split(orderParam), [orderParam])
  const required = useMemo(() => split(reqParam), [reqParam])

  /* The one control that must not reach the server on every keystroke. A
     catalog search is ~150ms over 5,000 rows, and the facets beside it are
     single clicks that should stay instant — so the debounce is on this box
     alone rather than on the query as a whole. The URL is what the request is
     built from, so the typed value is held here until it settles. */
  const [typed, setTyped] = useState(q || '')
  useEffect(() => { setTyped(q || '') }, [q])
  useEffect(() => {
    if (typed === (q || '')) return undefined
    const t = setTimeout(() => setQ(typed), 250)
    return () => clearTimeout(t)
  }, [typed])

  const [meta, setMeta] = useState(null)
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [editing, setEditing] = useState(false)
  const [shortlist, setShortlist] = useState(loadShortlist)

  useEffect(() => {
    try { localStorage.setItem(SHORTLIST_KEY, JSON.stringify(shortlist)) }
    catch { /* private mode — the shortlist just doesn't survive a reload */ }
  }, [shortlist])

  useEffect(() => {
    api.planMeta(new URLSearchParams({ eras: csv(eras) }).toString())
      .then(setMeta).catch((e) => setErr(e.message))
  }, [erasParam])

  const query = useMemo(() => {
    const p = new URLSearchParams({ eras: csv(eras), order: csv(order) })
    if (cls) p.set('classes', cls)
    if (mode === 'items') {
      if (required.length) p.set('required', csv(required))
      if (slot) p.set('slots', slot)
      if (tier) p.set('tiers', tier)
      if (kind) p.set('kinds', kind)
      if (armor) p.set('armor', armor)
      if (match !== '' && match != null) p.set('match_min', match)
      if (q) p.set('q', q)
      if (carries) p.set('carries_set', '1')
      if (proc) p.set('has_proc', '1')
    }
    return p.toString()
  }, [erasParam, orderParam, reqParam, cls, slot, tier, kind, armor, match, q,
    carries, proc, mode])

  /* Page titles can contain commas, so shortlist entries are repeated query
     parameters. The shortlist itself stays in localStorage and never enters
     the page URL; eras/class/priorities are the shareable plan, picks are this
     browser's working set. */
  const outlineQuery = useMemo(() => {
    const p = new URLSearchParams({ eras: csv(eras) })
    shortlist.items.forEach((i) => p.append('item', i.page_title))
    shortlist.sets.forEach((s) => p.append('set', s.name))
    shortlist.targets.forEach((t) => p.append('target', t.page_title))
    return p.toString()
  }, [erasParam, shortlist])

  useEffect(() => {
    if (tab !== 'gear') return undefined
    setErr(null)
    setData(null)
    const call = mode === 'sets' ? api.planSets : api.planItems
    let dead = false
    call(query).then((d) => { if (!dead) setData(d) })
      .catch((e) => { if (!dead) setErr(e.message) })
    return () => { dead = true }
  }, [tab, query, mode])

  useEffect(() => {
    if (tab !== 'outline') return undefined
    setErr(null)
    setData(null)
    let dead = false
    api.planOutline(outlineQuery).then((d) => { if (!dead) setData(d) })
      .catch((e) => { if (!dead) setErr(e.message) })
    return () => { dead = true }
  }, [tab, outlineQuery])

  /* At least one expansion always stays on. "Nothing selected" is not a plan,
     it is an empty page with no way to say why it is empty — so the last one
     standing does not turn off. */
  const toggleEra = (key) => {
    const next = eras.includes(key) ? eras.filter((e) => e !== key) : [...eras, key]
    if (next.length) setEras(csv(next))
  }

  const inList = useMemo(
    () => new Set(shortlist.items.map((i) => i.page_title)), [shortlist])
  const setsInList = useMemo(
    () => new Set(shortlist.sets.map((s) => s.name)), [shortlist])
  const targetsInList = useMemo(
    () => new Set(shortlist.targets.map((t) => t.page_title)), [shortlist])

  const toggleItem = useCallback((row) => setShortlist((s) => (
    s.items.some((i) => i.page_title === row.page_title)
      ? { ...s, items: s.items.filter((i) => i.page_title !== row.page_title) }
      : {
        ...s,
        items: [...s.items, {
          page_title: row.page_title, name: row.name, slot: row.slot_label,
          level: row.level, tier: row.tier, census_id: row.census_id,
        }],
      })), [])

  /* Shortlisting from the set view adds the ADORNMENT, never the armour it
     came in. The turquoise detaches and moves; the armour is only where you
     first find it, and confusing the two is the mistake this whole view
     exists to prevent (docs/planner.md). */
  const toggleSet = useCallback((row) => setShortlist((s) => (
    s.sets.some((x) => x.name === row.name)
      ? { ...s, sets: s.sets.filter((x) => x.name !== row.name) }
      : { ...s, sets: [...s.sets, { name: row.name, level: row.level }] })), [])

  const toggleTarget = useCallback((row) => setShortlist((s) => {
    const page = row.key || row.page_title
    return s.targets.some((t) => t.page_title === page)
      ? { ...s, targets: s.targets.filter((t) => t.page_title !== page) }
      : {
        ...s,
        targets: [...s.targets, {
          page_title: page, name: row.name, kind: row.kind,
          level: row.level, zone: row.zone, difficulty: row.difficulty,
        }],
      }
  }), [])

  const statLabel = useMemo(
    () => Object.fromEntries((meta?.stats || []).map((s) => [s.key, s.label])),
    [meta])
  const statPct = useMemo(
    () => Object.fromEntries((meta?.stats || []).map((s) => [s.key, s.pct])),
    [meta])

  const columns = useMemo(
    () => itemColumns({ order, statLabel, statPct }), [orderParam, statLabel])

  /* How many of the listed stats actually RANK. The server drops potency and
     crit whatever the URL says, so this is its count and not the raw order's
     length — "2 of 3" has to mean the same three the scorer used. */
  const ranked = data?.ranked?.length ?? order.length

  const emptyEras = meta && meta.eras
    .filter((e) => eras.includes(e.key) && !e.items).map((e) => e.label)

  return (
    <div className="workspace planner">
      <aside className="rail plannerrail">
        <div className="railhead">
          <h1>The Planner</h1>
          <span className="sub">What to chase, and where it comes from.</span>
        </div>

        <div className="railsec">
          <div className="seclabel">Expansions considered</div>
          <div className="erachips">
            {(meta?.eras || []).map((e) => (
              <button key={e.key}
                      className={`chip${eras.includes(e.key) ? ' on' : ''}`}
                      title={e.items
                        ? `${e.name} — ${e.items} items in the catalog`
                        : `${e.name} — not synced yet`}
                      onClick={() => toggleEra(e.key)}>
                {e.label}
                <em>{e.items || '—'}</em>
              </button>
            ))}
          </div>
        </div>

        <div className="railsec">
          <div className="seclabel">Class</div>
          <Picker value={cls} onChange={(v) => setCls(v)} placeholder="Any class"
                  options={[{ value: '', label: 'Any class' },
                    ...(meta?.classes || []).map((c) => ({
                      value: c, label: c[0].toUpperCase() + c.slice(1),
                    }))]} />
        </div>

        <Shortlist list={shortlist} onDropItem={toggleItem} onDropSet={toggleSet}
                   onDropTarget={toggleTarget} />
      </aside>

      <div className="wsmain">
        <Tabs tabs={[{ key: 'gear', label: 'Gear' }, { key: 'outline', label: 'Outline' }]}
              value={tab} onChange={(key) => setTab(key === 'gear' ? null : key)} />

        {tab === 'gear' && <div className="card planbar">
          <div className="priostrip">
            <span className="seclabel">Priority</span>
            {order.length === 0
              ? <span className="muted">nothing ranked</span>
              : (
                <span className="prioorder">
                  {order.map((k, i) => (
                    <span key={k}>
                      {i > 0 && <i className="sep">›</i>}
                      {statLabel[k] || k}
                      {required.includes(k) && <b title="required">*</b>}
                    </span>
                  ))}
                </span>
              )}
            <button className="chip" onClick={() => setEditing(true)}>Edit</button>
            {/* EQ2 gear in these expansions is four-stat — potency and crit,
                which everything has, plus two more — so an item can carry at
                most about two of whatever you listed. Naming three stats and
                being shown everything with ONE of them is how the list fills
                with rows that miss the point. The control says which floor is
                in force rather than dropping half the catalog silently. */}
            {ranked > 1 && (
              <span className="matchpick" title={
                'How many of your priorities an item has to actually carry. '
                + 'EQ2 items in these expansions have room for about two.'}>
                <Picker value={String(data?.match_min ?? '')}
                        onChange={(v) => setMatch(v)}
                        options={[
                          { value: '0', label: 'any item' },
                          ...Array.from({ length: ranked }, (_, i) => ({
                            value: String(i + 1),
                            label: `${i + 1} of ${ranked}`,
                          })),
                        ]} />
              </span>
            )}
            <span className="planmodes">
              <button className={`chip${mode !== 'sets' ? ' on' : ''}`}
                      onClick={() => setMode('items')}>Items</button>
              <button className={`chip${mode === 'sets' ? ' on' : ''}`}
                      onClick={() => setMode('sets')}>Set adornments</button>
            </span>
          </div>

          {mode === 'items' && (
            <div className="filterbar planfilters">
              <Facet value={slot} onChange={setSlot} label="Any slot"
                     options={meta?.slots} />
              <Facet value={armor} onChange={setArmor} label="Any armour"
                     options={meta?.armor} />
              <Facet value={tier} onChange={setTier} label="Any tier"
                     options={meta?.tiers} format={(t) => t.toLowerCase()} />
              <Facet value={kind} onChange={setKind} label="Any source"
                     options={meta?.kinds} format={(k) => KIND_LABEL[k] || k} />
              <input type="text" value={typed} placeholder="Name contains…"
                     onChange={(e) => setTyped(e.target.value)} />
              <button className={`chip${carries ? ' on' : ''}`}
                      title="Only items that ship with a set turquoise"
                      onClick={() => setCarries(carries ? '' : '1')}>
                Carries a set
              </button>
              <button className={`chip${proc ? ' on' : ''}`}
                      title="Only items with an effect that can fire"
                      onClick={() => setProc(proc ? '' : '1')}>
                Has a proc
              </button>
            </div>
          )}
        </div>}

        {err && <p className="err">{err}</p>}
        {!!emptyEras?.length && (
          <p className="muted">
            {emptyEras.join(' and ')} {emptyEras.length > 1 ? 'have' : 'has'} no
            catalog yet — run <code>backend/tools/sync_planner.py</code> for it.
          </p>
        )}

        {!data && !err && <p className="muted">Loading…</p>}

        {data && tab === 'gear' && mode === 'sets' && (
          <SetList sets={data.sets} inList={setsInList} onToggle={toggleSet} />
        )}

        {data && tab === 'gear' && mode !== 'sets' && (
          <>
            {data.total === 0 ? (
              <p className="muted">
                Nothing in {eras.length > 1 ? 'these expansions' : 'this expansion'} matches.
                {required.length > 0 && ' A required stat is a hard filter — try dropping one.'}
              </p>
            ) : (
              <>
                <SortableTable
                  className="plantable" wrapClass="tablewrap" frozen
                  prefsKey="planner" rows={data.items} rowKey={(r) => r.page_title}
                  columns={columns} defaultSort={{ key: 'score', dir: 'desc' }}
                  checkable={() => true} checkedKeys={inList} onCheck={
                    (key) => toggleItem(data.items.find((i) => i.page_title === key))}
                  defaultHidden={['dtype', 'potency', 'crit']}
                />
                {data.total > data.items.length && (
                  <p className="muted">
                    Showing the top {data.items.length} of {data.total}. Narrow it
                    with the filters — the rest are further down the same order.
                  </p>
                )}
              </>
            )}
          </>
        )}

        {data && tab === 'outline' && (
          <PlanOutline data={data} targetsInList={targetsInList}
                       onToggleTarget={toggleTarget} />
        )}
      </div>

      {tab === 'gear' && editing && (
        <PriorityEditor
          groups={meta?.groups || []} order={order} required={required}
          onClose={() => setEditing(false)}
          onChange={({ order: o, required: r }) => {
            setOrder(csv(o))
            setReq(csv(r.filter((k) => o.includes(k))))
          }} />
      )}
    </div>
  )
}

/* A facet is a Picker, never a `<select>` — house rule, and the open panel
   renders into `document.body` for the backdrop-filter stacking trap. */
function Facet({ value, onChange, label, options, format }) {
  return (
    <Picker value={value || ''} onChange={onChange} placeholder={label}
            options={[{ value: '', label },
              ...(options || []).map((o) => ({
                value: o, label: format ? format(o) : o,
              }))]} />
  )
}

function itemColumns({ order, statLabel, statPct }) {
  const shown = order.slice(0, 4)
  const stat = (key) => ({
    key,
    label: statLabel[key] || key,
    sortValue: (r) => r.stats[key] || 0,
    render: (r) => (r.stats[key]
      ? `${r.stats[key]}${statPct[key] ? '%' : ''}`
      : <span className="muted">—</span>),
  })
  return [
    {
      key: 'name', label: 'Item', fixed: true,
      render: (r) => <ItemName row={r} />,
      sortValue: (r) => r.name,
    },
    {
      key: 'score', label: 'Score',
      /* RANK COLOURING IS NOT REUSED HERE. On a parse, colour is placement
         within a role among peers who did the same thing; a table of items has
         no roles and no peers, and borrowing the ramp would imply a comparison
         the data does not support. It is a number in a sortable column. */
      render: (r) => (r.score ? r.score.toFixed(1) : <span className="muted">—</span>),
      sortValue: (r) => r.score,
    },
    { key: 'level', label: 'Lv', sortValue: (r) => r.level || 0 },
    {
      key: 'tier', label: 'Tier', align: 'l',
      render: (r) => <span className={rarityClass(r.tier)}>{(r.tier || '').toLowerCase()}</span>,
    },
    /* A two-hander reads `Primary/2H`. The wiki files a greatsword and a
       dagger under the same `slot = Primary`, which invites comparing them as
       though the other hand were still free — 162 of the catalog's primaries
       take both. The label comes from the server (`wiki.slot_label`) so
       anything else showing a slot says the same thing. */
    {
      key: 'slot', label: 'Slot', align: 'l',
      render: (r) => r.slot_label || <span className="muted">—</span>,
      sortValue: (r) => r.slot_label || '',
    },
    /* The one property that can rule an item out before any stat on it
       matters: a plate tank cannot wear leather however good the numbers are.
       Blank for a weapon or a shield, which have a `dtype` and no weight. */
    {
      key: 'armor', label: 'Armour', align: 'l',
      render: (r) => r.armor || <span className="muted">—</span>,
      sortValue: (r) => r.armor || '',
    },
    ...shown.map(stat),
    /* Available, off by default. Potency and crit cannot be RANKED by — four
       items in five have them — but they are still real numbers on the item,
       and a reader who wants to see them can turn the columns on from the
       Columns menu like any other. Skipped if a hand-built URL already put
       one in the order: two columns with one key is a broken table. */
    ...['potency', 'crit'].filter((k) => !shown.includes(k)).map(stat),
    {
      key: 'source', label: 'From', align: 'l',
      render: (r) => <Sources row={r} />,
      sortValue: (r) => (r.sources[0]?.kind || 'zz'),
    },
    /* THE TWO BADGES ARE THE POINT OF THE TABLE. Both say "this row's value is
       not in its stat columns": one carries a set turquoise that detaches and
       moves, the other has an effect that can fire. */
    {
      key: 'set', label: 'Set', headAlign: 'c', align: 'c',
      render: (r) => (r.set_name
        ? <span className="planbadge set" title={`Carries a piece of ${r.set_name}`}>◆</span>
        : null),
      sortValue: (r) => (r.set_name ? 1 : 0),
    },
    {
      key: 'proc', label: 'Proc', headAlign: 'c', align: 'c',
      render: (r) => (r.effects
        ? <span className="planbadge proc" title={r.effects}>✦</span>
        : null),
      sortValue: (r) => (r.effects ? 1 : 0),
    },
    { key: 'dtype', label: 'Type', align: 'l' },
  ]
}

function ItemName({ row }) {
  const label = <span className={rarityClass(row.tier)}>{row.name}</span>
  return (
    <span className="lootitem">
      <span className="looticon">
        {row.card.icon != null && (
          <img src={`/api/items/icon/${row.card.icon}.png`} alt="" width="24"
               height="24" loading="lazy" />
        )}
      </span>
      <Hover className="examinecard" width={350} card={<Examine row={row.card} />}>
        <a href={row.card.wiki} target="_blank" rel="noreferrer noopener">{label}</a>
      </Hover>
    </span>
  )
}

/* A raid drop and a solo quest reward are both true. The hardest claim leads,
   because that is the one a reader is deciding on, and the rest are a count
   rather than a list — "also 3 others" beats a cell that wraps to four lines. */
function Sources({ row }) {
  const first = row.sources[0]
  if (!first) return <span className="muted">—</span>
  const more = row.sources.length - 1
  return (
    <span className="plansource" title={row.sources
      .map((s) => `${KIND_LABEL[s.kind]}: ${s.source}${s.zone ? ` (${s.zone})` : ''}`)
      .join('\n')}>
      <i className={`skind ${first.kind}`}>{KIND_LABEL[first.kind]}</i>
      {first.source}
      {more > 0 && <span className="muted"> +{more}</span>}
    </span>
  )
}

/* Rank the SET BONUSES themselves, not the armour they arrive in.

   Each row says three different things: what the bonus IS at each tier (prose
   off the wiki, shown as written — nothing here scores a sentence), which
   items CARRY a piece, and which items can HOST one once you pull the
   turquoise out. The third is why the set is not just a column on an item. */
function SetList({ sets, inList, onToggle }) {
  if (!sets.length) {
    return <p className="muted">No adornment sets in this selection.</p>
  }
  return (
    <div className="setlist">
      {sets.map((s) => (
        <div className="card setcard" key={s.name}>
          <div className="sethead">
            <label className="setpick">
              <input type="checkbox" checked={inList.has(s.name)}
                     onChange={() => onToggle(s)} />
              <span className="cardtitle">{s.name}</span>
            </label>
            <span className="muted">
              level {s.level ?? '—'} · {s.carriers.length} carr
              {s.carriers.length === 1 ? 'ier' : 'iers'} · {s.host_count} item
              {s.host_count === 1 ? '' : 's'} can host it
            </span>
          </div>
          <ul className="setbonuses">
            {s.bonuses.map((b, i) => (
              <li key={i}><b>({b.pieces})</b> {b.text}</li>
            ))}
          </ul>
          <div className="setcarriers">
            <div className="seclabel">Comes in</div>
            {s.carriers.slice(0, 8).map((c) => (
              <span key={c.page_title} className="setpiece">
                <span className={rarityClass(c.tier)}>{c.name}</span>
                <em>{c.slot} · {c.level}</em>
              </span>
            ))}
            {s.carriers.length > 8 && (
              <span className="muted">+{s.carriers.length - 8} more</span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

/* The rail holds THREE KINDS of thing and lists them separately: items,
   adornments, and targets. A turquoise is not its host item and a raid target
   is not a slot, even when both happen to lead to the same source row. */
function Shortlist({ list, onDropItem, onDropSet, onDropTarget }) {
  const empty = !list.items.length && !list.sets.length && !list.targets.length
  return (
    <div className="railsec shortlist">
      <div className="seclabel">Shortlist</div>
      {empty && (
        <p className="muted">
          Tick a row to keep it here. It stays in this browser.
        </p>
      )}
      {!!list.items.length && (
        <>
          <div className="shortkind">Items</div>
          {list.items.map((i) => (
            <div className="shortrow" key={i.page_title}>
              <span className={rarityClass(i.tier)}>{i.name}</span>
              <em>{i.slot}</em>
              <button className="iconbtn" aria-label={`Remove ${i.name}`}
                      onClick={() => onDropItem(i)}>✕</button>
            </div>
          ))}
        </>
      )}
      {!!list.sets.length && (
        <>
          <div className="shortkind">Adornments</div>
          {list.sets.map((s) => (
            <div className="shortrow" key={s.name}>
              <span>{s.name}</span>
              <em>{s.level ? `L${s.level}` : ''}</em>
              <button className="iconbtn" aria-label={`Remove ${s.name}`}
                      onClick={() => onDropSet(s)}>✕</button>
            </div>
          ))}
        </>
      )}
      {!!list.targets.length && (
        <>
          <div className="shortkind">Targets</div>
          {list.targets.map((t) => (
            <div className="shortrow" key={t.page_title}>
              <span>{t.name}</span>
              <em>{t.level ? `L${t.level}` : t.kind}</em>
              <button className="iconbtn" aria-label={`Remove ${t.name}`}
                      onClick={() => onDropTarget(t)}>✕</button>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
