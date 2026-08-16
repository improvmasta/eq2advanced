import { useCallback, useEffect, useMemo, useState } from 'react'
import Picker from '../components/Picker.jsx'
import PlanLoadout, { eligiblePlanSlots } from '../components/PlanLoadout.jsx'
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

   Two regions, permanently: a compact plan rail on the left and the main area
   on the right. Gear choices themselves live in concrete equipment positions
   in the loadout, while the rail keeps the expansion/class frame and the two
   non-gear plan kinds visible for the Outline. */

const SHORTLIST_KEY = 'eq2adv:plan:shortlist'
/* The initial LEFT EDGE of the draggable stat track. The first one, two or
   three positions rank; every other available stat follows on the same line.

   POTENCY AND CRIT ARE NOT HERE AND ARE NOT OFFERED. They are on about four
   items in five in these expansions, so ordering by them separates nothing;
   the server refuses them too, so a hand-built URL cannot put them back
   (`planner/catalog.py: weights`). They remain on the examine card and are
   available as table columns. */
const OPENING_ORDER = ['abmod', 'acspeed', 'arspeed']

const KIND_LABEL = {
  raid: 'Raid', group: 'Group', solo: 'Solo', quest: 'Quest', unknown: 'Unknown',
}
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
  const [topParam, setTop] = useQueryState('top', '3')
  const [reqParam, setReq] = useQueryState('req', '')
  const [cls, setCls] = useQueryState('class', '')
  const [slot, setSlot] = useQueryState('slot', '')
  const [tier, setTier] = useQueryState('tier', '')
  const [kind, setKind] = useQueryState('kind', '')
  const [armor, setArmor] = useQueryState('armor', '')
  const [levelMin, setLevelMin] = useQueryState('level_min', '')
  const [levelMax, setLevelMax] = useQueryState('level_max', '')
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
  const requestedOrder = useMemo(() => split(orderParam), [orderParam])
  const requestedRequired = useMemo(() => split(reqParam), [reqParam])

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
  const [dragStat, setDragStat] = useState(null)
  const [overStat, setOverStat] = useState(null)
  const [shortlist, setShortlist] = useState(loadShortlist)
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
  const statLine = useMemo(() => {
    if (!rankableKeys.length) return requestedOrder.filter((key) => key !== 'none')
    const requested = requestedOrder.filter((key) => rankableKeys.includes(key))
    const first = requested.length ? requested : OPENING_ORDER
    return [...new Set([...first, ...rankableKeys])]
  }, [requestedOrder, rankableKeys])
  const priorityCount = Math.max(1, Math.min(3, Number.parseInt(topParam, 10) || 3))
  const order = useMemo(() => statLine.slice(0, priorityCount), [statLine, priorityCount])
  const required = useMemo(
    () => requestedRequired.filter((key) => order.includes(key)),
    [requestedRequired, order])

  useEffect(() => {
    try { localStorage.setItem(SHORTLIST_KEY, JSON.stringify(shortlist)) }
    catch { /* private mode — the shortlist just doesn't survive a reload */ }
  }, [shortlist])

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
    api.census(charId).then((d) => {
      if (dead) return
      setCharacter(d)
      if (d.character?.class) setCls(d.character.class.toLowerCase())
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
      if (required.length) p.set('required', csv(required))
      if (slot) p.set('slots', slot)
      if (tier) p.set('tiers', tier)
      if (kind) p.set('kinds', kind)
      if (armor) p.set('armor', armor)
      if (levelMin) p.set('level_min', levelMin)
      if (levelMax) p.set('level_max', levelMax)
      if (match !== '' && match != null) p.set('match_min', match)
      if (q) p.set('q', q)
      if (carries) p.set('carries_set', '1')
      if (proc) p.set('has_proc', '1')
    }
    return p.toString()
  }, [erasParam, order, required, cls, slot, tier, kind, armor,
    levelMin, levelMax, match, q, carries, proc, mode])

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
  const movePriorityStat = useCallback((from, to, after = false) => {
    if (!from || !to || from === to) return
    const next = statLine.filter((key) => key !== from)
    let at = next.indexOf(to)
    if (at < 0) at = next.length
    else if (after) at += 1
    next.splice(at, 0, from)
    setOrderLine(csv(next))
    setReq(csv(requestedRequired.filter((key) => next.slice(0, priorityCount).includes(key))))
  }, [statLine, requestedRequired, priorityCount, setOrderLine, setReq])

  const nudgePriorityStat = useCallback((key, delta) => {
    const at = statLine.indexOf(key)
    const to = at + delta
    if (at < 0 || to < 0 || to >= statLine.length) return
    const next = [...statLine]
    ;[next[at], next[to]] = [next[to], next[at]]
    setOrderLine(csv(next))
    setReq(csv(requestedRequired.filter((candidate) =>
      next.slice(0, priorityCount).includes(candidate))))
  }, [statLine, requestedRequired, priorityCount, setOrderLine, setReq])

  const changePriorityCount = useCallback((count) => {
    setTop(String(count))
    setReq(csv(requestedRequired.filter((key) => statLine.slice(0, count).includes(key))))
  }, [requestedRequired, statLine, setTop, setReq])

  const clearCatalogFilters = useCallback(() => {
    setSlot(null); setArmor(null); setTier(null); setKind(null)
    setLevelMin(null); setLevelMax(null)
    setCarries(null); setProc(null); setQ(null); setTyped('')
  }, [setSlot, setArmor, setTier, setKind, setLevelMin, setLevelMax,
    setCarries, setProc, setQ])

  const columns = useMemo(
    () => itemColumns({ order, statLabel, statPct }), [order, statLabel, statPct])

  /* How many of the listed stats actually RANK. The server drops potency and
     crit whatever the URL says, so this is its count and not the raw order's
     length — "2 of 3" has to mean the same three the scorer used. */
  const ranked = data?.ranked?.length ?? order.length

  const emptyEras = meta && meta.eras
    .filter((e) => eras.includes(e.key) && !e.items).map((e) => e.label)
  const filterCount = [slot, armor, tier, kind, levelMin, levelMax, carries, proc, q]
    .filter(Boolean).length

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

        {tab === 'gear' && (
          <PlanLoadout characters={characters} character={character} charId={charId}
            signedIn={!!user} onCharacter={setCharId} shortlist={shortlist}
            active={shortlist.active || {}} focusSlot={focusSlot}
            onFocusSlot={focusEquipmentSlot} onCycle={cycleEquipmentSlot}
            onSetAdornment={setSlotAdornment}
            onReset={() => setShortlist((s) => ({ ...s, active: {}, set_slots: {} }))}
            statLabel={statLabel} statPct={statPct} />
        )}

        {tab === 'gear' && <div className="card planbar">
          <div className="plansearchhead">
            <div className="plansearchtitle">
              <span className="seclabel">Item search</span>
              <b>{slot ? `${slot} upgrades` : 'Find equipment'}</b>
            </div>
            <span className="planmodes">
              <button className={`chip${mode !== 'sets' ? ' on' : ''}`}
                      onClick={() => setMode('items')}>Items</button>
              <button className={`chip${mode === 'sets' ? ' on' : ''}`}
                      onClick={() => setMode('sets')}>Set adornments</button>
            </span>
          </div>

          {mode === 'items' && (
            <>
              <div className="plansearchband">
                <label className="planquicksearch">
                  <span>Search items</span>
                  <input type="search" value={typed} placeholder="Search item names…"
                         onChange={(e) => setTyped(e.target.value)} />
                </label>
                <label className="planlevelrange">
                  <span>Item level</span>
                  <span className="levelinputs">
                    <input type="number" min="1" max="200" value={levelMin}
                           aria-label="Minimum item level" placeholder="Min"
                           onChange={(e) => setLevelMin(e.target.value)} />
                    <i>to</i>
                    <input type="number" min="1" max="200" value={levelMax}
                           aria-label="Maximum item level" placeholder="Max"
                           onChange={(e) => setLevelMax(e.target.value)} />
                  </span>
                </label>
                <button type="button" className="chip clearplanfilters"
                        disabled={filterCount === 0}
                        onClick={clearCatalogFilters}>Clear filters</button>
              </div>

              <div className="planfilterband">
                <span className="planfilterlabel">Filter</span>
                <Facet value={slot} onChange={setSlot} label="Any slot"
                       options={meta?.slots} />
                <Facet value={armor} onChange={setArmor} label="Any armour"
                       options={meta?.armor} />
                <Facet value={tier} onChange={setTier} label="Any tier"
                       options={meta?.tiers} format={(t) => t.toLowerCase()} />
                <Facet value={kind} onChange={setKind} label="Any source"
                       options={meta?.kinds} format={(k) => KIND_LABEL[k] || k} />
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

              <div className="prioritytrackbox">
                <div className="prioritytrackhead">
                  <div className="priorityintro">
                    <b>Stat priority</b>
                    <span>Drag stats left. The numbered positions score in order.</span>
                  </div>
                  <div className="prioritytools">
                    <label className="prioritycount">
                      <span>Score top</span>
                      {[1, 2, 3].map((count) => (
                        <button type="button" key={count}
                                className={priorityCount === count ? 'on' : ''}
                                onClick={() => changePriorityCount(count)}>{count}</button>
                      ))}
                    </label>
                    {ranked > 1 && (
                      <label className="compactmatch" title="How many priority stats an item must carry">
                        <span>Must match</span>
                        <Picker value={String(data?.match_min ?? '')}
                                onChange={(v) => setMatch(v)}
                                options={[
                                  { value: '0', label: 'any' },
                                  ...Array.from({ length: ranked }, (_, i) => ({
                                    value: String(i + 1), label: `${i + 1} of ${ranked}`,
                                  })),
                                ]} />
                      </label>
                    )}
                    <button type="button" className={`chip requirements${required.length ? ' on' : ''}`}
                            onClick={() => setEditing(true)}>
                      Requirements{required.length ? ` (${required.length})` : ''}
                    </button>
                  </div>
                </div>
                <div className="prioritytrack" role="list" aria-label="Draggable stat priority"
                     style={{ '--stat-count': statLine.length }}>
                  {statLine.map((key, index) => {
                    const activeStat = index < priorityCount
                    return (
                      <div key={key} role="listitem" tabIndex="0" draggable
                           className={`prioritytoken${activeStat ? ' ranked' : ''}${required.includes(key) ? ' required' : ''}${index === priorityCount - 1 ? ' boundary' : ''}${overStat === key ? ' over' : ''}`}
                           aria-label={`${statLabel[key] || key}${activeStat ? `, priority ${index + 1}` : ', not prioritized'}`}
                           title="Drag to reorder; Left and Right arrows also move this stat"
                           onDragStart={(e) => {
                             setDragStat(key); e.dataTransfer.effectAllowed = 'move'
                           }}
                           onDragEnd={() => { setDragStat(null); setOverStat(null) }}
                           onDragOver={(e) => { e.preventDefault(); setOverStat(key) }}
                           onDrop={(e) => {
                             e.preventDefault()
                             const rect = e.currentTarget.getBoundingClientRect()
                             movePriorityStat(dragStat, key, e.clientX > rect.left + rect.width / 2)
                             setOverStat(null)
                           }}
                           onKeyDown={(e) => {
                             if (e.key === 'ArrowLeft') {
                               e.preventDefault(); nudgePriorityStat(key, -1)
                             } else if (e.key === 'ArrowRight') {
                               e.preventDefault(); nudgePriorityStat(key, 1)
                             }
                           }}>
                        <i>{activeStat ? index + 1 : '⠿'}</i>
                        <span>{TRACK_LABEL[key] || statLabel[key] || key}</span>
                      </div>
                    )
                  })}
                </div>
              </div>

              <div className="plansearchfooter" aria-live="polite">
                <span>{data
                  ? <><b>{data.total}</b> matching item{data.total === 1 ? '' : 's'}</>
                  : 'Loading matching items…'}</span>
                <span>{order.length
                  ? <>Scoring <b>{order.map((key) => TRACK_LABEL[key] || statLabel[key] || key).join(' › ')}</b></>
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
          groups={meta?.groups || []} order={order} required={required} fixed
          onClose={() => setEditing(false)}
          onChange={({ order: o, required: r }) => {
            const rest = statLine.filter((key) => !o.includes(key))
            setOrderLine(csv([...o, ...rest]))
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
    <span className={`planfacet${value ? ' selected' : ''}`}>
      <Picker value={value || ''} onChange={onChange} placeholder={label}
              options={[{ value: '', label },
                ...(options || []).map((o) => ({
                  value: o, label: format ? format(o) : o,
                }))]} />
    </span>
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
function Shortlist({ list, onDropSet, onDropTarget }) {
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
