import { useCallback, useEffect, useMemo, useState } from 'react'
import Picker from '../components/Picker.jsx'
import PlanLoadout, { eligiblePlanSlots } from '../components/PlanLoadout.jsx'
import PlanOutline from '../components/PlanOutline.jsx'
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

   Two regions, permanently: a compact plan rail on the left and the main area
   on the right. Gear choices themselves live in concrete equipment positions
   in the loadout, while the rail keeps the expansion/class frame and the two
   non-gear plan kinds visible for the Outline. */

const SHORTLIST_KEY = 'eq2adv:plan:shortlist'
/* THREE CHOICES, EACH DEFAULTING TO ANY. The priority list is still an ORDER
   and still never shows a weight — what changed is only how you say it. A
   draggable track of thirteen tokens made the reader arrange every stat in the
   game to name two, and the boundary between "ranked" and "the rest" had to be
   set with a separate control ("Score top"); three ordinary dropdowns say the
   same thing and the number of them IS the boundary.

   POTENCY AND CRIT ARE NOT OFFERED. They are on about four items in five in
   these expansions, so ordering by them separates nothing; the server refuses
   them too, so a hand-built URL cannot put them back (`catalog.weights`). They
   remain on the examine card and are available as table columns. */
const PRIORITY_SLOTS = 3

/* `zone` is a WORLD DROP: the item is in a zone's drop list and no named or
   quest in the catalog claims it, which is as much as can honestly be said
   about gear that fell off trash. It is most of what a broker search returns
   and none of it was reachable by inverting named monsters. */
const KIND_LABEL = {
  raid: 'Raid', group: 'Group', solo: 'Solo', quest: 'Quest',
  zone: 'World drop', unknown: 'Unknown',
}
/* Source is the one facet where a reader routinely wants TWO answers — "group
   or raid", "quest or solo" — and a single-choice dropdown made that two
   searches. Checkboxes say it in one, and they show the whole list without
   being opened, which four short words can afford. */
const KIND_ORDER = ['raid', 'group', 'solo', 'quest', 'zone', 'unknown']
const TRACK_LABEL = {
  abmod: 'Ability', acspeed: 'Casting', arspeed: 'Reuse', aspeed: 'Haste',
  dps: 'DPS', multi: 'Multi', flurry: 'Flurry', aeauto: 'AE Auto',
  bchance: 'Block', hategain: 'Hate', mit: 'Mit', strike: 'Strike',
  maxhealth: 'Max HP',
}

function loadShortlist() {
  try {
    const saved = JSON.parse(localStorage.getItem(SHORTLIST_KEY)) || {}
    /* The former flat shortlist did not retain an item's concrete equipment
       position or its additive stats. It cannot safely participate in a
       subtract-and-replace projection, so preserve the still-compatible sets
       and targets while dropping only those legacy gear rows. */
    const items = Array.isArray(saved.items)
      ? saved.items.filter((item) => item?.equip_slot && item?.stats)
      : []
    return {
      items,
      sets: Array.isArray(saved.sets) ? saved.sets : [],
      targets: Array.isArray(saved.targets) ? saved.targets : [],
      active: saved.active && typeof saved.active === 'object' ? saved.active : {},
      set_slots: saved.set_slots && typeof saved.set_slots === 'object'
        ? saved.set_slots : {},
    }
  } catch { return { items: [], sets: [], targets: [], active: {}, set_slots: {} } }
}

const csv = (a) => (a && a.length ? a.join(',') : '')
const split = (s) => (s ? s.split(',').filter(Boolean) : [])

export default function Planner({ user }) {
  /* The plan lives in the URL, the way a comparison does on /compare: era,
     class and the priority order are what make this page YOURS, so a link to
     it is the plan and not just the page. */
  const [erasParam, setEras] = useQueryState('eras', 'rok')
  const [tabParam, setTab] = useQueryState('tab', 'gear')
  const [orderParam, setOrderLine] = useQueryState('order', '')
  const [cls, setCls] = useQueryState('class', '')
  const [slot, setSlot] = useQueryState('slot', '')
  const [tier, setTier] = useQueryState('tier', '')
  const [kindParam, setKind] = useQueryState('kind', '')
  const [armor, setArmor] = useQueryState('armor', '')
  const [levelMin, setLevelMin] = useQueryState('level_min', '')
  const [levelMax, setLevelMax] = useQueryState('level_max', '')
  const [mode, setMode] = useQueryState('mode', 'items')
  const [q, setQ] = useQueryState('q', '')
  const [carries, setCarries] = useQueryState('set', '')
  const [proc, setProc] = useQueryState('proc', '')

  const eras = useMemo(() => split(erasParam), [erasParam])
  const tab = tabParam === 'outline' ? 'outline' : 'gear'
  const requestedOrder = useMemo(() => split(orderParam), [orderParam])
  const kinds = useMemo(() => split(kindParam), [kindParam])

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
  const [shortlist, setShortlist] = useState(loadShortlist)
  /* CHANGING WHO YOU ARE PLANNING FOR PUTS THE WINDOW BACK TO WHAT THEY WEAR.
     A planned choice only means anything against one character's current
     equipment — a ring that is +40 Ability Mod on the fury is a downgrade on
     the guardian, and leaving the projection populated after a switch showed
     an "upgrade" measured against somebody else's gear. The shortlist itself
     survives: those are candidates you found, and finding them again would be
     the actual work. */
  const clearPlannedGear = useCallback(
    () => setShortlist((s) => ({ ...s, active: {}, set_slots: {} })), [])
  const [focusSlot, setFocusSlot] = useState(null)
  const [characters, setCharacters] = useState(null)
  const [character, setCharacter] = useState(null)
  const [charId, setCharId] = useState(() => {
    try { return localStorage.getItem('eq2adv:plan:character') || '' }
    catch { return '' }
  })

  const rankableKeys = useMemo(
    () => (meta?.groups || []).flatMap((group) => group.stats.map((stat) => stat.key)),
    [meta])
  /* The three boxes ARE this list, so an empty box is a hole that closes: a
     stat named in the third box while the first two say Any is the first
     priority, and pretending otherwise would put a gap in an ordering that
     only exists as an ordering. Until meta arrives there is nothing to check a
     key against, so the URL's own list stands. */
  const order = useMemo(() => {
    const asked = [...new Set(requestedOrder)]
    return (rankableKeys.length
      ? asked.filter((key) => rankableKeys.includes(key))
      : asked).slice(0, PRIORITY_SLOTS)
  }, [requestedOrder, rankableKeys])

  useEffect(() => {
    try { localStorage.setItem(SHORTLIST_KEY, JSON.stringify(shortlist)) }
    catch { /* private mode — the shortlist just doesn't survive a reload */ }
  }, [shortlist])

  /* A LOOKED-UP CHARACTER OUTRANKS THE ACCOUNT'S. Whichever was asked for last
     is the one being planned for, and a name somebody typed is the more
     deliberate of the two — the account picker is a convenience, not a claim
     about who you are working on. Held separately so switching back to an
     owned character does not have to undo it. */
  const [lookedUp, setLookedUp] = useState(null)
  const [lookupBusy, setLookupBusy] = useState(false)
  const [lookupErr, setLookupErr] = useState(null)
  const lookUpCharacter = useCallback((name) => {
    setLookupBusy(true)
    setLookupErr(null)
    api.planCharacter(name)
      .then((d) => { setLookedUp(d); setCharId(''); clearPlannedGear() })
      .catch(() => setLookupErr(`No Census record for “${name}”`))
      .finally(() => setLookupBusy(false))
  }, [clearPlannedGear])

  useEffect(() => {
    if (!user) { setCharacters([]); setCharacter(null); return }
    let dead = false
    api.characters().then((d) => {
      if (dead) return
      setCharacters(d.characters)
      const selectedExists = d.characters.some(
        (candidate) => String(candidate.id) === String(charId),
      )
      if ((!charId || !selectedExists) && d.characters.length) {
        setCharId(String(d.characters[0].id))
      }
    }).catch(() => { if (!dead) setCharacters([]) })
    return () => { dead = true }
  }, [user])

  useEffect(() => {
    if (!user || !charId) { setCharacter(null); return undefined }
    try { localStorage.setItem('eq2adv:plan:character', String(charId)) } catch { /* no persistence */ }
    let dead = false
    /* LOADING A CHARACTER DOES NOT SET THE CLASS FILTER. It used to, and the
       result was a search that silently answered a narrower question than the
       one on screen: a level-70 Head search for Reuse Speed came back empty
       because exactly one such item exists in EoF and it is illusionist-only,
       with nothing on the table saying a class was applied. The loadout panel
       is about one character; the item table is about the expansion, and the
       reader narrows it when they mean to. */
    api.census(charId).then((d) => {
      if (dead) return
      setCharacter(d)
    }).catch(() => { if (!dead) setCharacter(null) })
    return () => { dead = true }
  }, [user, charId])

  useEffect(() => {
    api.planMeta(new URLSearchParams({ eras: csv(eras) }).toString())
      .then(setMeta).catch((e) => setErr(e.message))
  }, [erasParam])

  const query = useMemo(() => {
    const p = new URLSearchParams({ eras: csv(eras), order: csv(order) })
    if (cls) p.set('classes', cls)
    if (mode === 'items') {
      if (slot) p.set('slots', slot)
      if (tier) p.set('tiers', tier)
      if (kinds.length) p.set('kinds', csv(kinds))
      if (armor) p.set('armor', armor)
      if (levelMin) p.set('level_min', levelMin)
      if (levelMax) p.set('level_max', levelMax)
      if (q) p.set('q', q)
      if (carries) p.set('carries_set', '1')
      if (proc) p.set('has_proc', '1')
    }
    return p.toString()
  }, [erasParam, order, cls, slot, tier, kindParam, armor,
    levelMin, levelMax, q, carries, proc, mode])

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

  const toggleItem = useCallback((row) => setShortlist((s) => {
    if (s.items.some((i) => i.page_title === row.page_title)) {
      const active = { ...(s.active || {}) }
      const setSlots = { ...(s.set_slots || {}) }
      Object.entries(active).forEach(([key, page]) => {
        if (page === row.page_title) { delete active[key]; delete setSlots[key] }
      })
      return {
        ...s, active, set_slots: setSlots,
        items: s.items.filter((i) => i.page_title !== row.page_title),
      }
    }
    const eligible = eligiblePlanSlots(row)
    const equipSlot = eligible.includes(focusSlot) ? focusSlot : eligible[0]
    if (!equipSlot) return s
    const item = {
      page_title: row.page_title, name: row.name, slot: row.slot,
      slot2: row.slot2, slot_label: row.slot_label, equip_slot: equipSlot,
      two_handed: row.two_handed, level: row.level, tier: row.tier,
      census_id: row.census_id, icon: row.icon, stats: row.stats,
      adorns: row.adorns, set_name: row.set_name, card: row.card,
    }
    const setSlots = { ...(s.set_slots || {}) }
    if (row.set_name && s.sets.some((set) => set.name === row.set_name)) {
      setSlots[equipSlot] = row.set_name
    }
    return {
      ...s, items: [...s.items, item],
      active: { ...(s.active || {}), [equipSlot]: row.page_title },
      set_slots: setSlots,
    }
  }), [focusSlot])

  const focusEquipmentSlot = useCallback((def) => {
    setFocusSlot(def.key)
    setMode('items')
    setSlot(def.catalog)
  }, [setMode, setSlot])

  const cycleEquipmentSlot = useCallback((key, page) => setShortlist((s) => {
    const active = { ...(s.active || {}) }
    if (page) active[key] = page
    else delete active[key]
    return { ...s, active }
  }), [])

  const setSlotAdornment = useCallback((key, setName) => setShortlist((s) => {
    const setSlots = { ...(s.set_slots || {}) }
    if (setName) setSlots[key] = setName
    else delete setSlots[key]
    return { ...s, set_slots: setSlots }
  }), [])

  /* ONE SLOT BACK TO WHAT IS ACTUALLY WORN. Cycling could already reach the
     equipped item, but only by counting positions past every candidate in that
     slot — and "put this ring back" is a thing you want to say directly once
     you have three rings on the list. The candidates stay on the shortlist;
     this is about the window, not about the search. */
  const resetEquipmentSlot = useCallback((key) => setShortlist((s) => {
    const active = { ...(s.active || {}) }
    const setSlots = { ...(s.set_slots || {}) }
    delete active[key]
    delete setSlots[key]
    return { ...s, active, set_slots: setSlots }
  }), [])

  /* Shortlisting from the set view adds the ADORNMENT, never the armour it
     came in. The turquoise detaches and moves; the armour is only where you
     first find it, and confusing the two is the mistake this whole view
     exists to prevent (docs/planner.md). */
  const toggleSet = useCallback((row) => setShortlist((s) => {
    if (s.sets.some((x) => x.name === row.name)) {
      const setSlots = { ...(s.set_slots || {}) }
      Object.entries(setSlots).forEach(([key, name]) => {
        if (name === row.name) delete setSlots[key]
      })
      return {
        ...s, set_slots: setSlots,
        sets: s.sets.filter((x) => x.name !== row.name),
      }
    }
    return {
      ...s,
      sets: [...s.sets, {
        name: row.name, level: row.level, bonuses: row.bonuses,
      }],
    }
  }), [])

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
  /* The short word first — the footer reads "Ability › Reuse" and the empty
     state has to name the same thing, in the same words. */
  const priorityLabel = useCallback(
    (key) => TRACK_LABEL[key] || statLabel[key] || key, [statLabel])

  /* Choosing a stat that is already in another box MOVES it rather than
     listing it twice — the same gesture a reader means by dragging it. */
  const setPriority = useCallback((at, key) => {
    const next = [...order]
    next[at] = key || ''
    setOrderLine(csv(next.filter(
      (candidate, i) => candidate && !(i !== at && candidate === key))))
  }, [order, setOrderLine])

  const toggleKind = useCallback((key) => {
    setKind(csv(kinds.includes(key)
      ? kinds.filter((k) => k !== key)
      : [...kinds, key]) || null)
  }, [kinds, setKind])

  /* Class is cleared with the rest now that it sits with the rest. While it
     was a rail control it read as a page-wide setting and Clear filters left
     it alone; a reader who presses Clear beside it means it. */
  const clearCatalogFilters = useCallback(() => {
    setCls(null); setSlot(null); setArmor(null); setTier(null); setKind(null)
    setLevelMin(null); setLevelMax(null)
    setCarries(null); setProc(null); setQ(null); setTyped('')
  }, [setCls, setSlot, setArmor, setTier, setKind, setLevelMin, setLevelMax,
    setCarries, setProc, setQ])

  /* How many of the listed stats actually RANK. The server drops potency and
     crit whatever the URL says, so this is its count and not the raw order's
     length — "2 of 3" has to mean the same three the scorer used. */
  const ranked = data?.ranked?.length ?? order.length

  const columns = useMemo(
    () => itemColumns({ order, ranked, statLabel, statPct }),
    [order, ranked, statLabel, statPct])

  /* Every rankable stat, grouped the way a raider already thinks about them
     ("Abilities", "Melee", "Tanking") — the groups are the server's
     (`wiki.STAT_GROUPS`) and the headers are lines in the list, not options. */
  const priorityOptions = useMemo(() => [
    { value: '', label: 'Any' },
    ...(meta?.groups || []).flatMap((group) => group.stats.map((stat) => ({
      value: stat.key, label: stat.label, group: group.label,
    }))),
  ], [meta])

  const emptyEras = meta && meta.eras
    .filter((e) => eras.includes(e.key) && !e.items).map((e) => e.label)
  const filterCount = [cls, slot, armor, tier, kindParam, levelMin, levelMax,
    carries, proc, q].filter(Boolean).length

  return (
    /* NO RAIL. This page is a wide table and a wide equipment window, and a
       fixed left column was spending a fifth of the screen on three controls
       and a list — the item table then scrolled sideways and the projected
       stats scrolled vertically, both of them for room the rail was holding.
       The three controls moved into the bands that were already there.

       THE EXPANSION CHOICE STAYS OUT OF THE FILTERS, in the head beside the
       tabs. Everything else on the page follows from it — which items exist,
       what a score is measured against, which sets are offered, which quests
       the Outline knows — and the Outline has no filter band to put it in. A
       control that governs both tabs lives where both tabs can see it. */
    <div className="planner">
      {/* THE HOUSE PAGE HEAD, unmodified: `h1`, `.sub`, right-aligned
          `.actions`, then the tabs on their own line under it. Every other page
          on this site opens that way, and a hand-built head line — small title,
          tabs floated into the middle of it — read as a different site. "The
          Planner" was this page's working name and went with it; the nav says
          Gear Planner and so does the page. */}
      <div className="pagehead">
        <h1>Gear Planner</h1>
        <span className="sub">What to chase, and where it comes from.</span>
      </div>

      <Tabs tabs={[{ key: 'gear', label: 'Gear' }, { key: 'outline', label: 'Outline' }]}
            value={tab} onChange={(key) => setTab(key === 'gear' ? null : key)} />

      <div className="wsmain">

        {tab === 'gear' && (
          <PlanLoadout characters={characters} character={lookedUp || character}
            charId={charId} signedIn={!!user}
            onCharacter={(id) => {
              setLookedUp(null); setCharId(id); clearPlannedGear()
            }}
            onLookup={lookUpCharacter} lookupBusy={lookupBusy} lookupErr={lookupErr}
            shortlist={shortlist}
            active={shortlist.active || {}} focusSlot={focusSlot}
            onFocusSlot={focusEquipmentSlot} onCycle={cycleEquipmentSlot}
            onSetAdornment={setSlotAdornment} onResetSlot={resetEquipmentSlot}
            onReset={clearPlannedGear}
            statLabel={statLabel} statPct={statPct} />
        )}

        {tab === 'gear' && <div className="card planbar">
          <div className="plansearchhead">
            <div className="plansearchtitle">
              <span className="seclabel">Item search</span>
              <b>{slot ? `${slot} upgrades` : 'Find equipment'}</b>
            </div>
            {/* THE EXPANSION CHOICE LIVES IN THE SEARCH BLOCK (Lindsay), on its
                head rather than among the facets: it is not one narrowing
                among several — it is what the catalog IS, and both the item
                view and the set view are drawn from it. */}
            <EraFacet meta={meta} eras={eras} onToggle={toggleEra} />
            <span className="planmodes">
              <button className={`chip${mode !== 'sets' ? ' on' : ''}`}
                      onClick={() => setMode('items')}>Items</button>
              <button className={`chip${mode === 'sets' ? ' on' : ''}`}
                      onClick={() => setMode('sets')}>Set adornments</button>
            </span>
          </div>

          {mode === 'items' && (
            <>
              {/* LABELLED ROWS IN A TWO-COLUMN GRID. Every row names itself in
                  the same gutter, so the controls all start on one line down
                  the left and the block reads as a form. The first pass split
                  this band into a priority column beside a filter column,
                  which left a dead quarter under the three dropdowns whenever
                  the filters wrapped to two lines — and Lindsay's read of it
                  was the right one: it was all over the place. */}
              <div className="planbands">
                <span className="planbandlabel">Search</span>
                <div className="planbandrow">
                  {/* A NAME SEARCH IS A NAME, so the box is the size of one. */}
                  <input type="search" className="planq" value={typed}
                         aria-label="Search item names" placeholder="Item name…"
                         onChange={(e) => setTyped(e.target.value)} />
                  <button type="button" className="chip clearplanfilters"
                          disabled={filterCount === 0}
                          onClick={clearCatalogFilters}>Clear filters</button>
                </div>

                <span className="planbandlabel">Stat priority</span>
                <div className="planbandrow">
                  {Array.from({ length: PRIORITY_SLOTS }, (_, i) => (
                    <span key={i} className={`prioritypick${order[i] ? ' on' : ''}`}>
                      <i aria-hidden="true">{i + 1}</i>
                      <Picker value={order[i] || ''} options={priorityOptions}
                              label={`Priority ${i + 1}`} placeholder="Any"
                              filterFrom={99}
                              onChange={(v) => setPriority(i, v)} />
                    </span>
                  ))}
                  <span className="bandnote">
                    {order.length
                      ? <>Items carrying all {ranked || order.length} lead the table.</>
                      : 'Rank up to three stats; the table scores on them.'}
                  </span>
                </div>

                {/* CLASS IS A FILTER AND NOW SITS WITH THE FILTERS. It was
                    alone in the rail, which read as a page-wide setting — and
                    it is the one control most likely to be what emptied a
                    table (`EmptyTable` names it for exactly that reason). */}
                <span className="planbandlabel">Filter</span>
                <div className="planbandrow">
                  <Facet name="Class" value={cls} onChange={setCls}
                         options={meta?.classes}
                         format={(c) => c[0].toUpperCase() + c.slice(1)} />
                  <Facet name="Slot" value={slot} onChange={setSlot}
                         options={meta?.slots} />
                  <Facet name="Armor Type" value={armor} onChange={setArmor}
                         options={meta?.armor} />
                  <Facet name="Tier" value={tier} onChange={setTier}
                         options={(meta?.tiers || []).map((t) => t.key)}
                         format={(t) => TIER_LABEL(meta, t)} />
                  {/* One control, one label, one dash between two numbers —
                      the pair was a band of its own with its own heading and
                      the word "to", for a thing that is read as "70–80". */}
                  <span className={`planfacet levelfacet${levelMin || levelMax ? ' selected' : ''}`}>
                    <span className="facetlab">Level</span>
                    <span className="levelinputs">
                      <input type="number" min="1" max="200" value={levelMin}
                             aria-label="Minimum item level" placeholder="Any"
                             onChange={(e) => setLevelMin(e.target.value)} />
                      <i>–</i>
                      <input type="number" min="1" max="200" value={levelMax}
                             aria-label="Maximum item level" placeholder="Any"
                             onChange={(e) => setLevelMax(e.target.value)} />
                    </span>
                  </span>
                </div>

                <span className="planbandlabel">Source</span>
                <div className="planbandrow">
                  <span className="sourceboxes">
                    {KIND_ORDER.filter((k) => (meta?.kinds || []).includes(k))
                      .map((k) => (
                        <label key={k}
                               className={`sourcebox${kinds.includes(k) ? ' on' : ''}`}>
                          <input type="checkbox" checked={kinds.includes(k)}
                                 onChange={() => toggleKind(k)} />
                          <span>{KIND_LABEL[k] || k}</span>
                        </label>
                      ))}
                  </span>
                  <button className={`chip${carries ? ' on' : ''}`}
                          title="Only items that ship with a set turquoise"
                          onClick={() => setCarries(carries ? '' : '1')}>
                    Set Pieces
                  </button>
                  <button className={`chip${proc ? ' on' : ''}`}
                          title="Only items with an effect that can fire"
                          onClick={() => setProc(proc ? '' : '1')}>
                    Has Proc
                  </button>
                </div>
              </div>

              <div className="plansearchfooter" aria-live="polite">
                <span>{data
                  ? <><b>{data.total}</b> matching item{data.total === 1 ? '' : 's'}</>
                  : 'Loading matching items…'}</span>
                <span>{order.length
                  ? <>Scoring <b>{order.map(priorityLabel).join(' › ')}</b>
                    {ranked > 1 && <> — items carrying all {ranked} first</>}</>
                  : 'Choose a stat priority to score results'}</span>
              </div>
            </>
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
              <EmptyTable data={data} eras={eras} cls={cls} order={order}
                          label={priorityLabel} />
            ) : (
              <>
                {/* THE WHOLE ROW PUTS THE ITEM IN THE WINDOW. A checkbox is
                    where the state lives, but nobody arrives at a table of
                    gear looking for a checkbox — they click the thing they
                    want. The name is still a link to the wiki and still opens
                    it, which is why that one cell stops the row's click. */}
                <SortableTable
                  className="plantable" wrapClass="tablewrap" frozen
                  prefsKey="planner" rows={data.items} rowKey={(r) => r.page_title}
                  columns={columns} defaultSort={{ key: 'score', dir: 'desc' }}
                  checkable={() => true} checkedKeys={inList} onCheck={
                    (key) => toggleItem(data.items.find((i) => i.page_title === key))}
                  onRowClick={toggleItem}
                  rowClass={(r) => (inList.has(r.page_title) ? 'picked' : '')}
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

        {/* THE SHORTLIST IS ON THE TAB THAT CONSUMES IT. It was a rail
            section visible from both, but on the Gear tab it repeated what
            the equipment window and the worn-set panel already showed, and
            targets can only be added over here in the first place. */}
        {tab === 'outline' && (
          <>
            {/* The Outline is drawn from the same expansions and has no search
                block to carry them, so it gets its own copy of the one
                control — never two of them in the same view. */}
            <div className="outlineeras">
              <EraFacet meta={meta} eras={eras} onToggle={toggleEra} />
            </div>
            <Shortlist list={shortlist} onDropSet={toggleSet}
                       onDropTarget={toggleTarget} />
          </>
        )}

        {data && tab === 'outline' && (
          <PlanOutline data={data} targetsInList={targetsInList}
                       onToggleTarget={toggleTarget} />
        )}
      </div>
    </div>
  )
}

/* AN EMPTY TABLE HAS TO SAY WHICH CONTROL EMPTIED IT.

   "Nothing in this expansion matches" reads as "no such item exists", and that
   is a claim about EverQuest II this page is in no position to make: the
   catalog is a crawl of the wiki, and the wiki is somebody else's incomplete
   notes. The real level-70 Head search that prompted this said nothing matched
   while the broker was full of them — the class filter had done it, and the
   page gave the reader no way to tell.

   So the server answers how many rows survived everything EXCEPT the stat
   priorities (`before_priorities`), and this splits the two cases apart: the
   stats found nothing among rows that DO exist, or the plain filters had
   already left nothing to score. Either way it ends by saying the catalog is a
   crawl, because the last possibility is always that we simply never walked to
   the item. */
function EmptyTable({ data, eras, cls, order, label }) {
  const where = eras.length > 1 ? 'these expansions' : 'this expansion'
  const stats = (order || []).map(label)
  const narrowed = data.before_priorities || 0
  return (
    <div className="muted planempty">
      {narrowed > 0 ? (
        <p>
          <b>{narrowed}</b> item{narrowed === 1 ? '' : 's'} in {where}{' '}
          {narrowed === 1 ? 'matches' : 'match'} your filters, but{' '}
          {narrowed === 1 ? 'it does not carry' : 'none of them carry'}{' '}
          {data.match_min > 1 ? `${data.match_min} of ` : ''}
          <b>{stats.join(', ')}</b>.{' '}
          {data.match_min > 1
            ? 'Set one of the priorities back to Any to see them.'
            : 'Score a different stat to see them.'}
        </p>
      ) : (
        <p>
          Nothing in {where} matches these filters
          {cls && <> for a <b>{cls}</b></>}.
          {cls && ' Clearing the class is usually the one that did it.'}
        </p>
      )}
      <p className="planemptynote">
        The catalog is {data.catalog ? <><b>{data.catalog}</b> items</> : 'a crawl'} of
        the wiki for {where} — every named, quest reward and zone drop it could be
        walked to. An item the wiki never filed under a zone we know is not in
        here, so an empty table is a statement about this table and not about the
        game.
      </p>
    </div>
  )
}

/* A facet is a Picker, never a `<select>` — house rule, and the open panel
   renders into `document.body` for the backdrop-filter stacking trap.

   THE FACET'S NAME IS OUTSIDE THE BOX. Folding it in ("Any armour") made the
   control say what it was only while it was doing nothing: pick Chain and the
   word "armour" left the screen, so a band of set facets read "Chest, Chain,
   Fabled" with nothing saying which was which. The label is a standing part of
   the row and the box holds the ANSWER, which is "Any" until you give one. */
function Facet({ name, value, onChange, options, format }) {
  return (
    <span className={`planfacet${value ? ' selected' : ''}`}>
      <span className="facetlab">{name}</span>
      <Picker value={value || ''} onChange={onChange} placeholder="Any"
              label={name}
              options={[{ value: '', label: 'Any' },
                ...(options || []).map((o) => ({
                  value: o, label: format ? format(o) : o,
                }))]} />
    </span>
  )
}

/* THE EXPANSION CHOICE, wearing the same label-then-control dress as a facet.
   It is a multi-select and the count rides on each chip, because "RoK 3735"
   answers "is this synced?" without a second line to read. At least one always
   stays on: "nothing selected" is not a plan, it is an empty page with no way
   to say why it is empty. */
function EraFacet({ meta, eras, onToggle }) {
  return (
    <span className="planfacet erafacet">
      <span className="facetlab">Expansions</span>
      <span className="erachips">
        {(meta?.eras || []).map((e) => (
          <button key={e.key} className={`chip${eras.includes(e.key) ? ' on' : ''}`}
                  title={e.items ? `${e.name} — ${e.items} items in the catalog`
                    : `${e.name} — not synced yet`}
                  onClick={() => onToggle(e.key)}>
            {e.label}
            <em>{e.items || '—'}</em>
          </button>
        ))}
      </span>
    </span>
  )
}

/* The rarity words come from the server with the facet (`catalog.meta`), so
   the page does not keep its own copy of the game's vocabulary. */
function TIER_LABEL(meta, key) {
  return (meta?.tiers || []).find((t) => t.key === key)?.label || key
}

function itemColumns({ order, ranked, statLabel, statPct }) {
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
      /* LEFT. `table.data` is a parse table and right-aligns its cells because
         its cells are figures; the item name is not one, and at full width the
         column got wide enough that right-aligned names sat a third of the
         page away from the checkbox that selects them. */
      key: 'name', label: 'Item', fixed: true, align: 'l',
      render: (r) => <ItemName row={r} />,
      sortValue: (r) => r.name,
    },
    {
      key: 'score', label: 'Score',
      /* RANK COLOURING IS NOT REUSED HERE. On a parse, colour is placement
         within a role among peers who did the same thing; a table of items has
         no roles and no peers, and borrowing the ramp would imply a comparison
         the data does not support. It is a number in a sortable column.
         HOW MANY OF YOUR STATS THE ROW CARRIES SORTS AHEAD OF THE SCORE, the
         same way the server ordered them (`catalog.search`) — a two-stat item
         with big numbers outscores a three-stat item with modest ones, and
         the third choice was made to find the three-stat one. The "2/3"
         beside the figure is what says the table is in two tiers. */
      render: (r) => (r.score
        ? (
          <span className="planscore">
            {r.score.toFixed(1)}
            {ranked > 1 && (
              <em title={`Carries ${r.matched} of your ${ranked} priority stats`}>
                {r.matched}/{ranked}
              </em>
            )}
          </span>
        )
        : <span className="muted">—</span>),
      sortValue: (r) => (r.matched || 0) * 1000 + (r.score || 0),
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
        {/* The row equips the item; the NAME still goes to the wiki, so its
            click must not also do the row's job. */}
        <a href={row.card.wiki} target="_blank" rel="noreferrer noopener"
           onClick={(e) => e.stopPropagation()}>{label}</a>
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
      .map((s) => `${KIND_LABEL[s.kind]}: ${s.source}`
        // A world drop's source IS its zone, so the parenthetical would repeat
        // the name it just printed.
        + (s.zone && s.zone !== s.source ? ` (${s.zone})` : '')
        + (s.detail ? ` — ${s.detail}` : ''))
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

/* THREE KINDS of thing, listed separately: items, adornments, and targets. A
   turquoise is not its host item and a raid target is not a slot, even when
   both happen to lead to the same source row.

   It heads the Outline, which is the page that answers it — "here is what you
   picked, and here is what to do about it" reads in that order. */
function Shortlist({ list, onDropSet, onDropTarget }) {
  const empty = !list.items.length && !list.sets.length && !list.targets.length
  return (
    <div className="card shortlist">
      <div className="seclabel">Shortlist</div>
      {empty && (
        <p className="muted">
          Pick gear on the Gear tab and it is kept here. It stays in this
          browser and is never written to an account.
        </p>
      )}
      {!!list.items.length && (
        <p className="shortgearcount">
          <b>{list.items.length}</b> gear choice{list.items.length === 1 ? '' : 's'} in the
          equipment window. Cycle each slot to compare builds.
        </p>
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
