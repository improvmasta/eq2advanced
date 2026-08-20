import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Picker from '../components/Picker.jsx'
import PlanLoadout, { eligiblePlanSlots, PLAN_SLOTS } from '../components/PlanLoadout.jsx'
import PlanOutline from '../components/PlanOutline.jsx'
import QuestLinks from '../components/QuestLinks.jsx'
import SortableTable from '../components/SortableTable.jsx'
/* The examine card is SHARED with the Loot tab and /chat. There are now three
   ways to meet an item and all three must open the same window — the server
   hands this page its cards in `items.display`'s shape for exactly that
   reason (`backend/planner/catalog.py: card`). */
import { Examine, Hover, rarityClass } from '../components/ItemCard.jsx'
import { api } from '../lib/api.js'
import { inheritSlotAdornments, itemSockets, setFitsHost,
  setPieceForSlot } from '../lib/planAdornments.js'
import { defaultSavedSetName, hasPlannedEquipment, savedSetInUse,
  savedSetPayloadEqual, savedSetSnapshot } from '../lib/planSavedSets.js'
import { useQueryState } from '../lib/useQueryState.js'

/* The Planner — what to chase in an expansion. See docs/planner.md.

   WHICH EXPANSIONS COUNT IS THE READER'S CHOICE, and it is the first control
   on the page: EoF, RoK, or both. Everything else — the facets, the scale a
   score is measured against, the sets — follows from that choice, which is why
   it sits in the item-search header rather than among the filters.

   Gear choices live in concrete equipment positions in the loadout. A
   persistent right work rail starts with recommendations; the Outline joins
   above them only once the plan contains a choice. */

const SHORTLIST_KEY = 'eq2adv:plan:shortlist:v2'
const SAVED_SETS_KEY = 'eq2adv:plan:saved-sets:v2'
const LEGACY_SAVED_SETS_KEY = 'eq2adv:plan:saved-sets:v1'
const RECENT_CHARACTERS_KEY = 'eq2adv:plan:recent-characters:v1'
const SAVED_SET_COUNT = 5
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
const ERA_DISPLAY_ORDER = ['eof', 'rok']
const DISCOVERY_SAMPLE_SIZE = 15

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
const LEVEL_OPTIONS = [{ value: '', label: 'Any' }, ...Array.from({ length: 200 }, (_, i) => ({
  value: String(i + 1), label: String(i + 1),
}))]
const TRACK_LABEL = {
  abmod: 'Ability', acspeed: 'Casting', arspeed: 'Reuse', aspeed: 'Haste',
  dps: 'DPS', multi: 'Multi', flurry: 'Flurry', aeauto: 'AE Auto',
  bchance: 'Block', hategain: 'Hate', mit: 'Mit', strike: 'Strike',
  maxhealth: 'Max HP',
}

/* The two set-bonus stats `/plan/meta` does not carry a label for — it lists
   what can be RANKED, and neither of these can be. */
const SET_STAT_FALLBACK = { health: 'Health', power: 'Power' }

const precise = (value) => Number(value).toLocaleString(undefined, {
  maximumFractionDigits: 2,
})

const emptyShortlist = () => ({
  owner: null, items: [], sets: [], active: {}, set_slots: {}, adorn_slots: {},
})

function normalizeShortlist(saved) {
  try {
    saved ||= {}
    /* The former flat shortlist did not retain an item's concrete equipment
       position or its additive stats. It cannot safely participate in a
       subtract-and-replace projection, so preserve the still-compatible sets
       while dropping only those legacy gear rows. */
    const items = Array.isArray(saved.items)
      ? saved.items.filter((item) => item?.equip_slot && item?.stats)
      : []
    return {
      owner: saved.owner && typeof saved.owner === 'object' ? saved.owner : null,
      items,
      sets: Array.isArray(saved.sets) ? saved.sets : [],
      active: saved.active && typeof saved.active === 'object' ? saved.active : {},
      set_slots: saved.set_slots && typeof saved.set_slots === 'object'
        ? saved.set_slots : {},
      adorn_slots: saved.adorn_slots && typeof saved.adorn_slots === 'object'
        ? saved.adorn_slots : {},
    }
  } catch { return emptyShortlist() }
}

function shortlistStorageKey(ownerKey) {
  return `${SHORTLIST_KEY}:${encodeURIComponent(ownerKey)}`
}

function loadShortlist(owner) {
  if (!owner?.key) return emptyShortlist()
  try {
    const saved = normalizeShortlist(JSON.parse(
      localStorage.getItem(shortlistStorageKey(owner.key))))
    return saved.owner?.key === owner.key ? { ...saved, owner } : { ...emptyShortlist(), owner }
  }
  catch { return { ...emptyShortlist(), owner } }
}

function ownerOf(summary) {
  const character = summary?.character
  if (!character?.name || !character?.class) return null
  const world = character.world || 'Wuoshi'
  const identity = character.census_id || character.name.trim().toLowerCase()
  return {
    key: `${world.toLowerCase()}:${identity}`,
    name: character.name,
    className: character.class.toLowerCase(),
    world,
  }
}

function itemClasses(item) {
  const raw = item?.classes?.length ? item.classes : item?.card?.classes
  return (raw || []).map((name) => String(name).toLowerCase())
}

function itemFitsOwner(item, owner) {
  const classes = itemClasses(item)
  return !!owner && (!classes.length || classes.includes(owner.className))
}

function bindShortlist(saved, owner) {
  const normalized = normalizeShortlist(saved)
  if (!owner) return emptyShortlist()
  if (normalized.owner?.key && normalized.owner.key !== owner.key) return null
  const items = normalized.items.filter((item) => itemFitsOwner(item, owner))
  const pages = new Set(items.map((item) => item.page_title))
  const active = Object.fromEntries(Object.entries(normalized.active || {})
    .filter(([, page]) => pages.has(page)))
  const validSlots = new Set(PLAN_SLOTS.map((slot) => slot.key))
  return {
    ...normalized, owner, items, active,
    set_slots: Object.fromEntries(Object.entries(normalized.set_slots || {})
      .filter(([slot]) => validSlots.has(slot))),
    adorn_slots: Object.fromEntries(Object.entries(normalized.adorn_slots || {})
      .filter(([slot]) => validSlots.has(slot))),
  }
}

const defaultSavedSets = () => Array.from({ length: SAVED_SET_COUNT }, (_, i) => ({
  slot: i + 1, name: `Set ${i + 1}`, payload: null, updated_ts: null,
}))

function normalizeSavedSets(rows) {
  const bySlot = new Map((Array.isArray(rows) ? rows : []).map((row) => [row?.slot, row]))
  return defaultSavedSets().map((fallback) => {
    const row = bySlot.get(fallback.slot)
    return row ? {
      ...fallback, ...row,
      name: String(row.name || fallback.name).slice(0, 40),
      payload: row.payload && typeof row.payload === 'object' ? row.payload : null,
    } : fallback
  })
}

const savedSetsStorageKey = (user, ownerKey) => (
  `${SAVED_SETS_KEY}:${user?.id || 'guest'}:${encodeURIComponent(ownerKey)}`
)
const legacySavedSetsStorageKey = (user) => (
  `${LEGACY_SAVED_SETS_KEY}:${user?.id || 'guest'}`
)

function readLocalSavedSets(user, owner) {
  if (!owner?.key) return defaultSavedSets()
  try {
    const direct = localStorage.getItem(savedSetsStorageKey(user, owner.key))
    if (direct) return normalizeSavedSets(JSON.parse(direct))

    /* v1 offered five slots for the entire reader. Every captured payload
       already named the public character it was built against, so adopt only
       this character's rows into its new five-slot folder. Leave v1 in place
       as a recovery copy while other character folders are opened later. */
    const legacy = normalizeSavedSets(JSON.parse(
      localStorage.getItem(legacySavedSetsStorageKey(user))))
    const adopted = normalizeSavedSets(legacy.map((row) => (
      row.payload?.shortlist?.owner?.key === owner.key ? row : null
    )))
    if (adopted.some(savedSetInUse)) writeLocalSavedSets(user, owner, adopted)
    return adopted
  }
  catch { return defaultSavedSets() }
}

function writeLocalSavedSets(user, owner, rows) {
  if (!owner?.key) return
  try { localStorage.setItem(savedSetsStorageKey(user, owner.key), JSON.stringify(rows)) }
  catch { /* private mode */ }
}

function readRecentCharacters() {
  try {
    const rows = JSON.parse(localStorage.getItem(RECENT_CHARACTERS_KEY))
    const recent = Array.isArray(rows) ? rows.filter((row) => row?.key && row?.name) : []
    const legacy = normalizeSavedSets(JSON.parse(
      localStorage.getItem(legacySavedSetsStorageKey(null))))
    const owners = legacy.map((row) => row.payload?.shortlist?.owner)
      .filter((owner) => owner?.key && owner?.name)
      .map((owner) => ({ ...owner, saved: true }))
    return mergeRecentCharacters(recent, owners)
  } catch { return [] }
}

function writeRecentCharacters(rows) {
  try { localStorage.setItem(RECENT_CHARACTERS_KEY, JSON.stringify(rows)) }
  catch { /* private mode */ }
}

function mergeRecentCharacters(...groups) {
  const byKey = new Map()
  groups.flat().filter(Boolean).forEach((row) => {
    if (!row?.key || !row?.name) return
    const previous = byKey.get(row.key) || {}
    byKey.set(row.key, { ...previous, ...row })
  })
  return [...byKey.values()].sort((a, b) => (
    Number(b.updated_ts || 0) - Number(a.updated_ts || 0)
    || a.name.localeCompare(b.name)
  ))
}

const csv = (a) => (a && a.length ? a.join(',') : '')
const split = (s) => (s ? s.split(',').filter(Boolean) : [])

export default function Planner({ user }) {
  /* The plan lives in the URL, the way a comparison does on /compare: era,
     class and the priority order are what make this page YOURS, so a link to
     it is the plan and not just the page. */
  const [erasParam, setEras] = useQueryState('eras', 'rok')
  const [orderParam, setOrderLine] = useQueryState('order', '')
  const [cls, setCls] = useQueryState('class', '')
  const [slot, setSlot] = useQueryState('slot', '')
  const [tier, setTier] = useQueryState('tier', '')
  const [kindParam, setKind] = useQueryState('kind', '')
  const [armor, setArmor] = useQueryState('armor', '')
  const [levelMin, setLevelMin] = useQueryState('level_min', '')
  const [levelMax, setLevelMax] = useQueryState('level_max', '')
  const [mode, setMode] = useQueryState('mode', 'items')
  const [carries, setCarries] = useQueryState('set', '')
  const [proc, setProc] = useQueryState('proc', '')
  const [characterParam, setCharacterParam] = useQueryState('character', '')

  const eras = useMemo(() => split(erasParam), [erasParam])
  const adornmentEras = useMemo(() => [...new Set(eras.flatMap((era) => (
    era === 'rok' ? ['rok', 'eof'] : [era]
  )))], [eras])
  const requestedOrder = useMemo(() => split(orderParam), [orderParam])
  const kinds = useMemo(() => split(kindParam), [kindParam])

  /* The one control that must not reach the server on every keystroke. A
     catalog search is ~150ms over 5,000 rows, and the facets beside it are
     single clicks that should stay instant — so the debounce is on this box
     alone rather than on the query as a whole. The URL is what the request is
     built from, so the typed value is held here until it settles. */
  /* A name search is scratch state, not part of a saved plan. It starts empty
     on every mount, refresh, and return to the route. */
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [setSearch, setSetSearch] = useState('')
  const [showAllSets, setShowAllSets] = useState(true)
  useEffect(() => {
    if (typed === q) return undefined
    const t = setTimeout(() => setQ(typed), 250)
    return () => clearTimeout(t)
  }, [typed, q])

  const [meta, setMeta] = useState(null)
  /* Keep each exact catalog result. Switching Items -> Sets -> Items used to
     throw away the item response and collapse the entire results area while
     the identical request ran again, which looked like a page reload. The
     query string is already the complete cache key for either catalog. */
  const [catalogResults, setCatalogResults] = useState({})
  const [outlineData, setOutlineData] = useState(null)
  const [outlineErr, setOutlineErr] = useState(null)
  const [err, setErr] = useState(null)
  const [shortlist, setShortlist] = useState(emptyShortlist)
  const [planNotice, setPlanNotice] = useState('')
  const [savedSets, setSavedSets] = useState(defaultSavedSets)
  const [recentCharacters, setRecentCharacters] = useState(readRecentCharacters)
  const [activeSavedSetSlot, setActiveSavedSetSlot] = useState(null)
  const [savedSetBusy, setSavedSetBusy] = useState(false)
  const [savedSetStatus, setSavedSetStatus] = useState('')
  const planCount = shortlist.owner?.key
    ? shortlist.items.length + shortlist.sets.length : 0
  const [outlineOpen, setOutlineOpen] = useState(planCount > 0)
  const previousPlanCount = useRef(planCount)
  useEffect(() => {
    if (planCount > previousPlanCount.current) setOutlineOpen(true)
    if (planCount === 0) setOutlineOpen(false)
    previousPlanCount.current = planCount
  }, [planCount])
  /* CHANGING WHO YOU ARE PLANNING FOR PUTS THE WINDOW BACK TO WHAT THEY WEAR.
     A planned choice only means anything against one character's current
     equipment — a ring that is +40 Ability Mod on the fury is a downgrade on
     the guardian, and leaving the projection populated after a switch showed
     an "upgrade" measured against somebody else's gear. The shortlist itself
     survives: those are candidates you found, and finding them again would be
     the actual work. */
  const clearPlannedGear = useCallback(
    () => setShortlist((s) => ({ ...s, active: {}, set_slots: {}, adorn_slots: {} })), [])
  const clearSetContents = useCallback(() => setShortlist((s) => ({
    ...s, items: [], sets: [], active: {}, set_slots: {}, adorn_slots: {},
  })), [])
  const [focusSlot, setFocusSlot] = useState(null)
  const wasSignedIn = useRef(!!user)
  useEffect(() => {
    if (wasSignedIn.current && !user) {
      // Logging out must land on the public catalog, not retain the concrete
      // slot that happened to be focused in the account's equipment window.
      setFocusSlot(null)
      setSlot(null)
    }
    wasSignedIn.current = !!user
  }, [user, setSlot])
  const [characters, setCharacters] = useState(null)
  const [character, setCharacter] = useState(null)
  const [adornmentSets, setAdornmentSets] = useState([])
  const [whiteAdornments, setWhiteAdornments] = useState([])
  const [epicItems, setEpicItems] = useState([])
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
    if (!shortlist.owner?.key) return
    try {
      localStorage.setItem(shortlistStorageKey(shortlist.owner.key), JSON.stringify(shortlist))
    }
    catch { /* private mode — the shortlist just doesn't survive a reload */ }
  }, [shortlist])

  useEffect(() => {
    if (!savedSetStatus || savedSetStatus === 'Saving…'
        || savedSetStatus.includes('failed') || savedSetStatus === 'Using browser saves') {
      return undefined
    }
    const timer = setTimeout(() => setSavedSetStatus(''), 2400)
    return () => clearTimeout(timer)
  }, [savedSetStatus])

  /* A LOOKED-UP CHARACTER OUTRANKS THE ACCOUNT'S. Whichever was asked for last
     is the one being planned for, and a name somebody typed is the more
     deliberate of the two — the account picker is a convenience, not a claim
     about who you are working on. Held separately so switching back to an
     owned character does not have to undo it. */
  const [lookedUp, setLookedUp] = useState(null)
  const [lookupBusy, setLookupBusy] = useState(false)
  const [lookupErr, setLookupErr] = useState(null)
  const loadedLookup = useRef('')
  const loadLookedUpCharacter = useCallback((name) => {
    setLookupBusy(true)
    setLookupErr(null)
    api.planCharacter(name)
      .then((d) => { setShortlist(emptyShortlist()); setLookedUp(d); setCharId('') })
      .catch(() => setLookupErr(`No character record for “${name}”`))
      .finally(() => setLookupBusy(false))
  }, [])

  /* Character links across the site land on this URL state. Guard the effect
     because React StrictMode deliberately replays it in development; a link
     should still be one lookup. Back/forward changes the name and loads the
     newly selected toon. */
  useEffect(() => {
    const name = characterParam.trim()
    if (!name || loadedLookup.current === name) return
    loadedLookup.current = name
    loadLookedUpCharacter(name)
  }, [characterParam, loadLookedUpCharacter])

  const lookUpCharacter = useCallback((name) => {
    const clean = name.trim()
    if (!clean) return
    if (clean === characterParam.trim()) {
      loadedLookup.current = clean
      loadLookedUpCharacter(clean)
    } else {
      loadedLookup.current = ''
      setCharacterParam(clean)
    }
  }, [characterParam, loadLookedUpCharacter, setCharacterParam])

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

  const planningCharacter = lookedUp || character
  const planningOwner = useMemo(() => ownerOf(planningCharacter), [planningCharacter])
  const currentSetPayload = useMemo(
    () => savedSetSnapshot(shortlist, planningCharacter?.gear || []),
    [shortlist, planningCharacter])
  const activeSavedSet = savedSets.find((row) => row.slot === activeSavedSetSlot)
  /* v1 saves left untouched sockets implicit. Compare their materialized
     meaning so loading one is not falsely dirty; the next Save also upgrades
     it to the v2 whole-Outline snapshot. */
  const comparableSavedSetPayload = useMemo(() => {
    if (!activeSavedSet?.payload?.shortlist || !planningOwner) {
      return activeSavedSet?.payload
    }
    const bound = bindShortlist(activeSavedSet.payload.shortlist, planningOwner)
    return bound
      ? savedSetSnapshot(bound, planningCharacter?.gear || [])
      : activeSavedSet.payload
  }, [activeSavedSet, planningCharacter, planningOwner])
  const savedSetDirty = Boolean(activeSavedSet?.payload)
    && !savedSetPayloadEqual(comparableSavedSetPayload, currentSetPayload)
  const savedSetModified = hasPlannedEquipment(shortlist)
  const planningOwnerKeyRef = useRef('')
  planningOwnerKeyRef.current = planningOwner?.key || ''
  useEffect(() => {
    setActiveSavedSetSlot(null)
    setSavedSetStatus('')
  }, [planningOwner?.key, user?.id])
  useEffect(() => {
    if (!activeSavedSetSlot) return
    const row = savedSets.find((set) => set.slot === activeSavedSetSlot)
    if (!savedSetInUse(row)) setActiveSavedSetSlot(null)
  }, [activeSavedSetSlot, savedSets])
  useEffect(() => {
    setPlanNotice('')
    setShortlist(loadShortlist(planningOwner))
  }, [planningOwner?.key])

  /* A successful public lookup becomes a durable way back into the Planner.
     This list lives in the browser and makes no ownership claim; account-backed
     saved-set folders are merged below so they also survive a new browser. */
  useEffect(() => {
    if (!lookedUp || !planningOwner) return
    const remembered = {
      ...planningOwner,
      className: planningCharacter?.character?.class || planningOwner.className,
      level: planningCharacter?.character?.level ?? null,
      updated_ts: Math.floor(Date.now() / 1000),
    }
    setRecentCharacters((rows) => {
      const next = mergeRecentCharacters(rows, [remembered])
      writeRecentCharacters(next)
      return next
    })
  }, [lookedUp, planningOwner?.key])

  useEffect(() => {
    if (!user) return undefined
    let dead = false
    api.planSavedSetOwners().then(({ characters: rows }) => {
      if (dead) return
      const remembered = (rows || []).map((row) => ({
        key: row.owner_key, name: row.owner_name,
        updated_ts: row.updated_ts, saved: true,
      }))
      setRecentCharacters((current) => {
        const next = mergeRecentCharacters(current, remembered)
        writeRecentCharacters(next)
        return next
      })
    }).catch(() => { /* local recent searches remain available */ })
    return () => { dead = true }
  }, [user?.id])

  /* Five slots repeat for each public character. Account rows win when
     present; untouched rows adopt the same character's account-local or guest
     saves. Guest copies deliberately remain in place after login. */
  useEffect(() => {
    let dead = false
    setActiveSavedSetSlot(null)
    if (!planningOwner) {
      setSavedSets(defaultSavedSets())
      return undefined
    }
    const accountLocal = readLocalSavedSets(user, planningOwner)
    const guestLocal = readLocalSavedSets(null, planningOwner)
    if (!user) {
      setSavedSets(guestLocal)
      return undefined
    }
    setSavedSets(accountLocal)
    api.planSavedSets(planningOwner.key).then(async (response) => {
      if (dead) return
      const server = normalizeSavedSets(response.sets)
      const adopted = server.map((row, i) => {
        if (savedSetInUse(row)) return row
        const local = savedSetInUse(accountLocal[i]) ? accountLocal[i] : guestLocal[i]
        return savedSetInUse(local)
          ? { ...row, name: local.name, payload: local.payload } : row
      })
      setSavedSets(adopted)
      writeLocalSavedSets(user, planningOwner, adopted)
      const pending = adopted.filter((row, i) => (
        !savedSetInUse(server[i]) && savedSetInUse(row)
      ))
      if (pending.length) {
        const written = await Promise.all(pending.map((row) => (
          api.putPlanSavedSet(planningOwner, row.slot, row.name, row.payload)
            .catch(() => null)
        )))
        if (dead) return
        const bySlot = new Map(written.filter(Boolean)
          .map((result) => [result.set.slot, result.set]))
        const synced = adopted.map((row) => bySlot.get(row.slot) || row)
        setSavedSets(synced)
        writeLocalSavedSets(user, planningOwner, synced)
        if (written.some((result) => !result)) setSavedSetStatus('Saved here; sync failed')
      }
    }).catch(() => {
      if (!dead) {
        setSavedSets(accountLocal)
        setSavedSetStatus('Using browser saves')
      }
    })
    return () => { dead = true }
  }, [planningOwner?.key, user?.id])

  const writeSavedSet = useCallback((slotNumber, nextName, nextPayload,
                                      successStatus = 'Saved') => {
    const held = savedSets.find((row) => row.slot === slotNumber)
    if (!held || !planningOwner) return Promise.resolve(null)
    const nextRow = {
      ...held, name: (nextName ?? held.name).trim() || defaultSavedSetName(slotNumber),
      payload: nextPayload,
      updated_ts: Math.floor(Date.now() / 1000),
    }
    const next = savedSets.map((row) => row.slot === slotNumber ? nextRow : row)
    setSavedSets(next)
    writeLocalSavedSets(user, planningOwner, next)
    setSavedSetStatus(user ? 'Saving…'
      : successStatus === 'Deleted' ? 'Deleted in this browser' : 'Saved in this browser')
    if (!user) return Promise.resolve(nextRow)
    setSavedSetBusy(true)
    return api.putPlanSavedSet(planningOwner, slotNumber, nextRow.name, nextRow.payload)
      .then(({ set }) => {
        const synced = next.map((row) => row.slot === slotNumber ? set : row)
        writeLocalSavedSets(user, planningOwner, synced)
        if (planningOwnerKeyRef.current === planningOwner.key) {
          setSavedSets(synced)
          setSavedSetStatus(successStatus)
        }
        return set
      })
      .catch(() => {
        if (planningOwnerKeyRef.current === planningOwner.key) {
          setSavedSetStatus(successStatus === 'Deleted'
            ? 'Deleted here; sync failed' : 'Saved here; sync failed')
        }
        return nextRow
      })
      .finally(() => setSavedSetBusy(false))
  }, [planningOwner, savedSets, user?.id])

  const saveSavedSet = useCallback((slotNumber, name) => {
    setActiveSavedSetSlot(slotNumber)
    return writeSavedSet(slotNumber, name, currentSetPayload)
  }, [currentSetPayload, writeSavedSet])

  const renameSavedSet = useCallback((slotNumber, name) => {
    const held = savedSets.find((row) => row.slot === slotNumber)
    return writeSavedSet(slotNumber, name, held?.payload || null)
  }, [savedSets, writeSavedSet])

  const deleteSavedSet = useCallback((slotNumber) => {
    if (Number(activeSavedSetSlot) === Number(slotNumber)) setActiveSavedSetSlot(null)
    return writeSavedSet(slotNumber, defaultSavedSetName(slotNumber), null, 'Deleted')
  }, [activeSavedSetSlot, writeSavedSet])

  const loadSavedSet = useCallback((slotNumber) => {
    const held = savedSets.find((row) => row.slot === slotNumber)
    if (!held?.payload?.shortlist || !planningOwner) return false
    const loaded = bindShortlist(held.payload.shortlist, planningOwner)
    if (!loaded) {
      setSavedSetStatus(`That set belongs to ${held.payload.shortlist.owner.name}`)
      return false
    }
    setShortlist(loaded)
    setActiveSavedSetSlot(slotNumber)
    setSavedSetStatus(`Loaded ${held.name}`)
    return true
  }, [planningOwner, savedSets])

  const adornmentClass = planningCharacter?.character?.class?.toLowerCase() || ''
  useEffect(() => {
    if (!adornmentClass) { setEpicItems([]); return undefined }
    let dead = false
    api.planEpics(adornmentClass)
      .then((response) => { if (!dead) setEpicItems(response.items || []) })
      .catch(() => { if (!dead) setEpicItems([]) })
    return () => { dead = true }
  }, [adornmentClass])

  const suggestedEpic = useMemo(() => {
    const fabled = epicItems.find((item) => item.epic_stage === 'fabled')
    const mythical = epicItems.find((item) => item.epic_stage === 'mythical')
    const worn = planningCharacter?.gear?.find((item) => item.key === 'primary')
    const held = (item) => {
      if (!item) return false
      if (item.census_id && worn?.item_id === item.census_id) return true
      // Several class pairs deliberately share a display name; only use the
      // name fallback when it identifies one stage unambiguously.
      return epicItems.filter((candidate) => candidate.name === item.name).length === 1
        && worn?.name === item.name
    }
    const hasFabled = held(fabled) || held(mythical)
    const next = hasFabled ? (mythical || fabled) : (fabled || mythical)
    return held(next) ? null : next
  }, [epicItems, planningCharacter])
  /* Socket choices are useful while the item table is open, so they cannot
     depend on visiting the separate Sets mode first. This is the same local
     planner catalog, narrowed to the loaded character's class when known. */
  useEffect(() => {
    /* Set adornments are useful for two expansion bands. Their gear-window
       choices must not disappear merely because item search is showing only
       the newer expansion. */
    const p = new URLSearchParams({ eras: csv(adornmentEras) })
    if (adornmentClass) p.set('classes', adornmentClass)
    let dead = false
    api.planSets(p.toString()).then((d) => {
      if (!dead) setAdornmentSets(d.sets || [])
    }).catch(() => { if (!dead) setAdornmentSets([]) })
    return () => { dead = true }
  }, [adornmentEras, adornmentClass])

  useEffect(() => {
    let dead = false
    api.planAdornments().then((d) => {
      if (!dead) setWhiteAdornments(d.adornments || [])
    }).catch(() => { if (!dead) setWhiteAdornments([]) })
    return () => { dead = true }
  }, [])

  useEffect(() => {
    api.planMeta(new URLSearchParams({ eras: csv(eras) }).toString())
      .then(setMeta).catch((e) => setErr(e.message))
  }, [erasParam])

  const query = useMemo(() => {
    const p = new URLSearchParams({
      eras: csv(eras), order: csv(order),
    })
    if (mode === 'items') {
      if (cls) p.set('classes', cls)
      if (slot) p.set('slots', slot)
      if (tier) p.set('tiers', tier)
      if (kinds.length) p.set('kinds', csv(kinds))
      if (armor) p.set('armor', armor)
      if (levelMin) p.set('level_min', levelMin)
      if (levelMax) p.set('level_max', levelMax)
      if (q) p.set('q', q)
      if (carries) p.set('carries_set', '1')
      if (proc) p.set('has_proc', '1')
      const hasFilters = [cls, slot, tier, kindParam, armor, levelMin, levelMax,
        carries, proc, q].some(Boolean)
      if (!hasFilters && !order.length) p.set('sample', String(DISCOVERY_SAMPLE_SIZE))
    } else if (adornmentClass) {
      // Sets are choices for the loaded character. The equipment-search class
      // facet is an independent catalog question and must not leak here.
      p.set('classes', adornmentClass)
    }
    return p.toString()
  }, [erasParam, order, cls, slot, tier, kindParam, armor,
    levelMin, levelMax, q, carries, proc, mode, adornmentClass])
  const catalogKey = `${mode}:${query}`
  const data = catalogResults[catalogKey] || null

  /* Page titles can contain commas, so shortlist entries are repeated query
     parameters. The shortlist itself stays in localStorage and never enters
     the page URL; eras/class/priorities are the shareable plan, picks are this
     browser's working set. */
  const outlineQuery = useMemo(() => {
    const p = new URLSearchParams({ eras: csv(eras) })
    if (planningOwner?.className) p.set('class', planningOwner.className)
    shortlist.items.forEach((i) => p.append('item', i.page_title))
    shortlist.sets.forEach((s) => p.append('set', s.name))
    return p.toString()
  }, [erasParam, shortlist, planningOwner?.className])

  useEffect(() => {
    setErr(null)
    if (catalogResults[catalogKey]) return undefined
    const call = mode === 'sets' ? api.planSets : api.planItems
    let dead = false
    call(query).then((d) => {
      if (!dead) setCatalogResults((results) => ({ ...results, [catalogKey]: d }))
    })
      .catch((e) => { if (!dead) setErr(e.message) })
    return () => { dead = true }
  }, [catalogKey, catalogResults, mode, query])

  useEffect(() => {
    if (!planCount) { setOutlineData(null); setOutlineErr(null); return undefined }
    setOutlineErr(null)
    /* Keep the current outline mounted while its replacement is read. Besides
       avoiding a rail-wide loading flash, this lets PlanOutline compare goal
       identities and open only what was just added. Unmounting here erased
       that history on every shortlist click. */
    let dead = false
    api.planOutline(outlineQuery).then((d) => { if (!dead) setOutlineData(d) })
      .catch((e) => { if (!dead) setOutlineErr(e.message) })
    return () => { dead = true }
  }, [outlineQuery, planCount])

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

  const toggleItem = useCallback((row) => {
    const removing = shortlist.items.some((item) => item.page_title === row.page_title)
    if (!removing && !planningOwner) {
      setPlanNotice('Load a character before adding gear to an outline.')
      return
    }
    if (!removing && !itemFitsOwner(row, planningOwner)) {
      setPlanNotice(`${row.name} is restricted to another class and cannot be added for ${planningOwner.name}.`)
      return
    }
    setPlanNotice('')
    setShortlist((s) => {
    if (s.items.some((i) => i.page_title === row.page_title)) {
      const items = s.items.filter((i) => i.page_title !== row.page_title)
      const active = { ...(s.active || {}) }
      let setSlots = { ...(s.set_slots || {}) }
      let adornSlots = { ...(s.adorn_slots || {}) }
      Object.entries(active).forEach(([key, page]) => {
        if (page === row.page_title) {
          const fromItem = s.items.find((item) => item.page_title === page)
          const next = items.find((item) => item.equip_slot === key)
          if (next) active[key] = next.page_title
          else delete active[key]
          const toItem = next || planningCharacter?.gear?.find((gear) => gear.key === key)
          const inherited = inheritSlotAdornments({
            slot: key, fromItem, toItem, setSlots, adornSlots,
          })
          setSlots = inherited.setSlots
          adornSlots = inherited.adornSlots
        }
      })
      return {
        ...s, active, set_slots: setSlots, adorn_slots: adornSlots,
        items,
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
      classes: row.classes,
    }
    const currentItem = s.items.find((candidate) => candidate.equip_slot === equipSlot
      && candidate.page_title === (s.active || {})[equipSlot])
      || planningCharacter?.gear?.find((gear) => gear.key === equipSlot)
      || null
    const inherited = inheritSlotAdornments({
      slot: equipSlot, fromItem: currentItem, toItem: item,
      setSlots: s.set_slots, adornSlots: s.adorn_slots,
    })
    return {
      ...s, items: [...s.items, item],
      active: { ...(s.active || {}), [equipSlot]: row.page_title },
      set_slots: inherited.setSlots,
      adorn_slots: inherited.adornSlots,
    }
    })
  }, [focusSlot, planningCharacter, planningOwner, shortlist.items])

  const focusEquipmentSlot = useCallback((def) => {
    setFocusSlot(def.key)
    setMode('items')
    setSlot(def.catalog)
  }, [setMode, setSlot])

  const cycleEquipmentSlot = useCallback((key, page) => setShortlist((s) => {
    const active = { ...(s.active || {}) }
    const fromItem = s.items.find((candidate) => candidate.equip_slot === key
      && candidate.page_title === active[key])
      || planningCharacter?.gear?.find((gear) => gear.key === key)
      || null
    if (page) active[key] = page
    else delete active[key]
    const item = page
      ? s.items.find((candidate) => candidate.page_title === page)
      : planningCharacter?.gear?.find((gear) => gear.key === key)
    const inherited = inheritSlotAdornments({
      slot: key, fromItem, toItem: item,
      setSlots: s.set_slots, adornSlots: s.adorn_slots,
    })
    return { ...s, active, set_slots: inherited.setSlots,
      adorn_slots: inherited.adornSlots }
  }), [planningCharacter])

  const setSlotAdornment = useCallback((key, setName) => setShortlist((s) => {
    const setSlots = { ...(s.set_slots || {}) }
    if (setName === null) setSlots[key] = null
    else if (setName) setSlots[key] = setName
    else delete setSlots[key]
    return { ...s, set_slots: setSlots }
  }), [])

  const setWhiteAdornment = useCallback((key, socket, adornment) => setShortlist((s) => {
    const adornSlots = { ...(s.adorn_slots || {}) }
    const slotChoices = { ...(adornSlots[key] || {}) }
    if (adornment === undefined) delete slotChoices[socket]
    else slotChoices[socket] = adornment
    if (Object.keys(slotChoices).length) adornSlots[key] = slotChoices
    else delete adornSlots[key]
    return { ...s, adorn_slots: adornSlots }
  }), [])

  /* REMOVE THE PLANNED ITEM WHERE IT IS VISIBLE. Requiring somebody to find
     the same row in search just to uncheck it traps stale candidates in the
     loadout. Promote another candidate in the concrete slot when one remains;
     otherwise the slot naturally returns to the equipped item. */
  const removeEquipmentItem = useCallback((key, page) => setShortlist((s) => {
    const items = s.items.filter((item) => item.page_title !== page)
    const active = { ...(s.active || {}) }
    if (active[key] === page) {
      const fromItem = s.items.find((item) => item.page_title === page)
      const next = items.find((item) => item.equip_slot === key)
      if (next) active[key] = next.page_title
      else delete active[key]
      const toItem = next || planningCharacter?.gear?.find((gear) => gear.key === key)
      const inherited = inheritSlotAdornments({
        slot: key, fromItem, toItem,
        setSlots: s.set_slots, adornSlots: s.adorn_slots,
      })
      return { ...s, items, active, set_slots: inherited.setSlots,
        adorn_slots: inherited.adornSlots }
    }
    return { ...s, items, active }
  }), [planningCharacter])

  /* Tracking acquisition and trying an adornment on are independent. The set
     goal asks the Outline where carrier gear comes from; set_slots says what
     is currently in the equipment window. Removing one must not undo the
     other. */
  const toggleSet = useCallback((row) => {
    const removing = shortlist.sets.some((set) => set.name === row.name)
    if (!removing && !planningOwner) {
      setPlanNotice('Load a character before tracking sources for an adornment set.')
      return
    }
    setPlanNotice('')
    setShortlist((s) => {
    if (s.sets.some((x) => x.name === row.name)) {
      return {
        ...s,
        sets: s.sets.filter((x) => x.name !== row.name),
      }
    }
    return {
      ...s,
      sets: [...s.sets, {
        name: row.name, set_name: row.set_name, slot_label: row.slot_label,
        level: row.level, pieces: row.pieces, bonuses: row.bonuses,
      }],
    }
    })
  }, [planningOwner, shortlist.sets])

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
    setCarries(null); setProc(null); setQ(''); setTyped('')
  }, [setCls, setSlot, setArmor, setTier, setKind, setLevelMin, setLevelMax,
    setCarries, setProc])

  const useCurrentCharacterFilters = useCallback(() => {
    const current = planningCharacter?.character
    if (!current) return
    const level = Number(current.level)
    setCls(String(current.class || '').toLowerCase() || null)
    if (Number.isFinite(level)) {
      setLevelMin(String(Math.max(1, level - 10)))
      setLevelMax(String(Math.min(200, level + 10)))
    }
  }, [planningCharacter, setCls, setLevelMin, setLevelMax])

  /* How many of the listed stats actually RANK. The server drops potency and
     crit whatever the URL says, so this is its count and not the raw order's
     length — "2 of 3" has to mean the same three the scorer used. */
  const ranked = data?.ranked?.length ?? order.length

  const columns = useMemo(
    () => itemColumns({ order, ranked, statLabel, statPct,
      character: planningCharacter, shortlist, focusSlot }),
    [order, ranked, statLabel, statPct, planningCharacter, shortlist, focusSlot])

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
  const visibleSets = useMemo(() => {
    const needle = setSearch.trim().toLowerCase()
    if (!needle) return data?.sets || []
    return (data?.sets || []).filter((set) => [
      set.name,
      ...(set.bonuses || []).flatMap((bonus) => [
        bonus.text, ...(bonus.stat_lines || []), ...(bonus.detail || []),
      ]),
    ].filter(Boolean).join(' ').toLowerCase().includes(needle))
  }, [data, setSearch])

  const setSocketTargets = useMemo(() => {
    const planned = Object.fromEntries((shortlist.items || [])
      .map((item) => [item.page_title, item]))
    const current = Object.fromEntries((planningCharacter?.gear || [])
      .map((item) => [item.key, item]))
    const selectedPrimary = planned[(shortlist.active || {}).primary]
    return PLAN_SLOTS.flatMap((def) => {
      if (def.key === 'secondary' && selectedPrimary?.two_handed) return []
      const item = planned[(shortlist.active || {})[def.key]] || current[def.key]
      if (!item) return []
      if (!itemSockets(item).some((socket) => socket.color === 'turquoise')) return []
      const overridden = Object.prototype.hasOwnProperty.call(
        shortlist.set_slots || {}, def.key)
      const installed = overridden ? (shortlist.set_slots || {})[def.key]
        : item.set_name || (item.adornments || []).find(
          (adorn) => adorn.color === 'turquoise')?.set_name
        || (item.adornments || []).find(
          (adorn) => adorn.color === 'turquoise')?.name || null
      return [{
        key: def.key, label: def.label, catalog: def.catalog,
        item, level: item.level, installed,
      }]
    })
  }, [planningCharacter, shortlist])
  const compatibleVisibleSets = useMemo(() => setSocketTargets.length
    ? visibleSets.filter((set) => setSocketTargets.some((target) =>
      setFitsHost(set, target.item, target.catalog)))
    : visibleSets, [setSocketTargets, visibleSets])
  /* THE SCOPE IS A CONTROL, NOT A SIDE EFFECT OF TYPING. It used to widen to
     every set the moment a search box had anything in it, which meant the list
     silently changed what it was showing while the reader was narrowing it.
     Fits my gear / All says which question is being asked and the search
     filters inside the answer; the footer names what the scope is holding
     back so nothing goes missing without a count. */
  const shownSets = setSocketTargets.length && !showAllSets
    ? compatibleVisibleSets : visibleSets
  const hiddenSets = setSocketTargets.length && !showAllSets
    ? visibleSets.length - compatibleVisibleSets.length : 0
  const eraLabel = useMemo(() => Object.fromEntries(
    (meta?.eras || []).map((era) => [era.key, era.label])), [meta])

  return (
    /* THIS IS A WORK RAIL, NOT NAVIGATION. Recommendations give it a useful
       empty-plan state; once the reader picks something, the derived Outline
       appears above them and can still be collapsed independently.

       THE EXPANSION CHOICE STAYS OUT OF THE FILTERS, in the search-window
       header. Everything else on the page follows from it — which items exist,
       what a score is measured against, which sets are offered, and which
       quests the Outline knows. */
    <div className="planner">
      {/* THE HOUSE PAGE HEAD, with only the name and the contextual reopen
          action. The old pitch repeated the job implied by Gear Planner and
          cost a line without helping a decision. */}
      <div className="pagehead">
        <h1>Gear Planner</h1>
        {!outlineOpen && planCount > 0 && (
          <span className="actions">
            <button type="button" className="btnlink disclose"
                    onClick={() => setOutlineOpen(true)}>
              Outline <b>{planCount}</b> <span className="caret">›</span>
            </button>
          </span>
        )}
      </div>

      <div className="plannerworkspace">
      <div className="wsmain plannermain">

        <PlanLoadout characters={characters} recentCharacters={recentCharacters}
            character={planningCharacter}
            characterValue={lookedUp && planningOwner
              ? `recent:${planningOwner.key}` : charId ? `account:${charId}` : ''}
            signedIn={!!user}
            onCharacter={(value) => {
              if (value.startsWith('recent:')) {
                const key = value.slice('recent:'.length)
                const row = recentCharacters.find((recent) => recent.key === key)
                if (row) lookUpCharacter(row.name)
                return
              }
              const id = value.startsWith('account:')
                ? value.slice('account:'.length) : value
              setCharacterParam('')
              setLookedUp(null)
              setCharId(id)
            }}
            onLookup={lookUpCharacter} lookupBusy={lookupBusy} lookupErr={lookupErr}
            shortlist={shortlist} adornmentSets={adornmentSets}
            whiteAdornments={whiteAdornments}
            active={shortlist.active || {}} focusSlot={focusSlot}
            onFocusSlot={focusEquipmentSlot} onCycle={cycleEquipmentSlot}
            onSetAdornment={setSlotAdornment} onWhiteAdornment={setWhiteAdornment}
            onRemoveItem={removeEquipmentItem} onToggleTrackedSet={toggleSet}
            onClearSetContents={clearSetContents}
            onReset={clearPlannedGear}
            savedSets={savedSets} activeSavedSetSlot={activeSavedSetSlot}
            savedSetBusy={savedSetBusy} savedSetStatus={savedSetStatus}
            savedSetDirty={savedSetDirty} savedSetModified={savedSetModified}
            onSaveSet={saveSavedSet} onRenameSet={renameSavedSet}
            onDeleteSet={deleteSavedSet} onLoadSet={loadSavedSet}
            statLabel={statLabel} statPct={statPct} />

        <div className="card planbar">
          <div className="plansearchhead">
            <div className="plansearchtitle">
              <span className="seclabel">Gear catalog</span>
              <b>{mode === 'sets' ? 'Set Adornments'
                : slot ? `${slot} upgrades` : 'Find equipment'}</b>
            </div>
            {/* THE EXPANSION CHOICE LIVES IN THE SEARCH BLOCK (Lindsay), on its
                head rather than among the facets: it is not one narrowing
                among several — it is what the catalog IS, and both the item
                view and the set view are drawn from it. */}
            <EraFacet meta={meta} eras={eras} onToggle={toggleEra} />
          </div>

          {/* These are two catalog tasks, not a tiny filter at the far edge of
              the expansion controls. The full-width choice names what each
              surface lets the reader do before its controls/results appear. */}
          <div className="planmodes" role="tablist" aria-label="Catalog view">
            <span>Search for</span>
            <button className={mode !== 'sets' ? 'on' : ''} role="tab"
                    aria-selected={mode !== 'sets'} onClick={() => setMode('items')}>
              Equipment
            </button>
            <button className={mode === 'sets' ? 'on' : ''} role="tab"
                    aria-selected={mode === 'sets'} onClick={() => setMode('sets')}>
              Set adornments
            </button>
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
                <span className="planbandlabel filterbandlabel">
                  Filter
                  <button type="button" className="currentfilter"
                          disabled={!planningCharacter?.character}
                          title="Use this character's class and level, plus or minus 10"
                          onClick={useCurrentCharacterFilters}>Current</button>
                </span>
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
                  <SourceFacet kinds={kinds} available={meta?.kinds}
                               onToggle={toggleKind} />
                  {/* One control, one label, one dash between two numbers —
                      the pair was a band of its own with its own heading and
                      the word "to", for a thing that is read as "70–80". */}
                  <span className={`planfacet levelfacet${levelMin || levelMax ? ' selected' : ''}`}>
                    <span className="facetlab">Level</span>
                    <span className="levelinputs">
                      <Picker value={levelMin || ''} options={LEVEL_OPTIONS}
                              label="Minimum item level" placeholder="Any"
                              filterFrom={8} filterHint="Minimum level…"
                              onChange={setLevelMin} />
                      <i>–</i>
                      <Picker value={levelMax || ''} options={LEVEL_OPTIONS}
                              label="Maximum item level" placeholder="Any"
                              filterFrom={8} filterHint="Maximum level…"
                              onChange={setLevelMax} />
                    </span>
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
                <span>{data?.sampled
                  ? <><b>{data.items.length}</b> random items, just for fun</>
                  : data
                    ? <><b>{data.total}</b> matching item{data.total === 1 ? '' : 's'}</>
                  : 'Loading matching items…'}</span>
                <span>{data?.sampled
                  ? 'Search, filter, or choose a stat priority for the full catalog'
                  : order.length
                  ? <>Scoring <b>{order.map(priorityLabel).join(' › ')}</b>
                    {ranked > 1 && <> — items carrying all {ranked} first</>}</>
                  : 'Choose a stat priority to score results'}</span>
              </div>
            </>
          )}
          {mode === 'sets' && (
            <>
              <div className="plansetsearch">
                <label>
                  <span className="planbandlabel">Search sets</span>
                  <input type="search" className="planq" value={setSearch}
                         aria-label="Search set adornments"
                         placeholder="Set name or bonus…"
                         onChange={(e) => setSetSearch(e.target.value)} />
                </label>
                {!!setSocketTargets.length && (
                  <span className="setscope" role="group"
                        aria-label="Which sets to list">
                    <button type="button" className={showAllSets ? '' : 'on'}
                            aria-pressed={!showAllSets}
                            onClick={() => setShowAllSets(false)}>Fits my gear</button>
                    <button type="button" className={showAllSets ? 'on' : ''}
                            aria-pressed={showAllSets}
                            onClick={() => setShowAllSets(true)}>All sets</button>
                  </span>
                )}
              </div>
              <div className="plansearchfooter" aria-live="polite">
                <span><b>{shownSets.length}</b> set{shownSets.length === 1 ? '' : 's'}
                  {hiddenSets ? ` · ${hiddenSets} more don't fit this loadout` : ''}</span>
                <span>{setSocketTargets.length
                  ? 'Open a set to equip a piece or track where it drops'
                  : 'Load a character to see which sets fit its sockets'}</span>
              </div>
            </>
          )}
        </div>

        {planNotice && <p className="err" role="status">{planNotice}</p>}

        {err && <p className="err">{err}</p>}
        {!!emptyEras?.length && (
          <p className="muted">
            {emptyEras.join(' and ')} {emptyEras.length > 1 ? 'have' : 'has'} no
            catalog yet — run <code>backend/tools/sync_planner.py</code> for it.
          </p>
        )}

        {!data && !err && <p className="muted">Loading…</p>}

        {data && mode === 'sets' && (
          <SetList sets={shownSets} inList={setsInList} canPlan={!!planningOwner}
                   targets={setSocketTargets} onToggle={toggleSet}
                   onEquipAdornment={setSlotAdornment} eraLabel={eraLabel}
                   statLabel={statLabel} statPct={statPct} />
        )}

        {data && mode !== 'sets' && (
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
                  checkable={(row) => inList.has(row.page_title)
                    || itemFitsOwner(row, planningOwner)} checkedKeys={inList} onCheck={
                    (key) => toggleItem(data.items.find((i) => i.page_title === key))}
                  onRowClick={toggleItem}
                  rowClass={(r) => [
                    inList.has(r.page_title) ? 'picked' : '',
                    planningOwner && !itemFitsOwner(r, planningOwner) ? 'ineligible' : '',
                  ].filter(Boolean).join(' ')}
                  defaultHidden={['dtype', 'potency', 'crit']}
                />
                {!data.sampled && data.total > data.items.length && (
                  <p className="muted">
                    Showing the top {data.items.length} of {data.total}. Narrow it
                    with the filters — the rest are further down the same order.
                  </p>
                )}
              </>
            )}
          </>
        )}

      </div>
      <aside className="plannerrail">
        {outlineOpen && planCount > 0 && (
          <section className="planneroutline">
            <header className="planneroutlinehead">
              <h2>Outline{planningOwner?.name && <small>{planningOwner.name}</small>}</h2>
              <button type="button" className="iconbtn" aria-label="Collapse outline"
                      title="Collapse outline" onClick={() => setOutlineOpen(false)}>›</button>
            </header>
            <Shortlist list={shortlist} onDropItem={toggleItem} onDropSet={toggleSet} />
            {outlineErr && <p className="err">{outlineErr}</p>}
            {!outlineData && !outlineErr && <p className="muted">Building outline…</p>}
            {outlineData && <PlanOutline key={planningOwner?.key} data={outlineData}
              ownerKey={planningOwner?.key} items={shortlist.items} />}
          </section>
        )}
        {suggestedEpic && !inList.has(suggestedEpic.page_title) && (
          <section className="card plannerrecommendations">
            <header className="plannerrecommendationshead">
              <h2>Recommended Items</h2>
            </header>
            <div className="epicsuggestion">
              <div>
                <span className="seclabel">Epic weapon</span>
                <b className={rarityClass(suggestedEpic.tier)}>{suggestedEpic.name}</b>
                <small>{suggestedEpic.epic_stage === 'mythical'
                  ? 'Fabled equipped — pursue the Mythical upgrade'
                  : 'Start with the Fabled class epic'}</small>
              </div>
              <button type="button" className="chip"
                      onClick={() => toggleItem(suggestedEpic)}>
                Add to plan
              </button>
            </div>
          </section>
        )}
      </aside>
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

function SourceFacet({ kinds, available, onToggle }) {
  const shown = KIND_ORDER.filter((kind) => (available || []).includes(kind))
  return (
    <span className={`planfacet sourcemultifacet${kinds.length ? ' selected' : ''}`}>
      <span className="facetlab">Source</span>
      <details className="sourcepicker">
        <summary>{kinds.length ? `${kinds.length} selected` : 'Any'}</summary>
        <span className="sourcepickermenu">
          {shown.map((kind) => (
            <label key={kind} className={`sourcebox${kinds.includes(kind) ? ' on' : ''}`}>
              <input type="checkbox" checked={kinds.includes(kind)}
                     onChange={() => onToggle(kind)} />
              <span>{KIND_LABEL[kind] || kind}</span>
            </label>
          ))}
        </span>
      </details>
    </span>
  )
}

/* THE EXPANSION CHOICE, wearing the same label-then-control dress as a facet.
   It is a multi-select and the count rides on each chip, because "RoK 3735"
   answers "is this synced?" without a second line to read. At least one always
   stays on: "nothing selected" is not a plan, it is an empty page with no way
   to say why it is empty. */
function EraFacet({ meta, eras, onToggle }) {
  const displayEras = [...(meta?.eras || [])].sort((a, b) => {
    const aRank = ERA_DISPLAY_ORDER.indexOf(a.key)
    const bRank = ERA_DISPLAY_ORDER.indexOf(b.key)
    return (aRank < 0 ? Number.MAX_SAFE_INTEGER : aRank)
      - (bRank < 0 ? Number.MAX_SAFE_INTEGER : bRank)
  })
  return (
    <span className="planfacet erafacet">
      <span className="facetlab">Expansions</span>
      <span className="erachips">
        {displayEras.map((e) => (
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

function itemColumns({ order, ranked, statLabel, statPct,
                       character, shortlist, focusSlot }) {
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
      render: (r) => <ItemName row={r} character={character}
                              shortlist={shortlist} focusSlot={focusSlot} />,
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

function comparisonCards(row, character, shortlist, focusSlot) {
  const eligible = eligiblePlanSlots(row)
  const slot = eligible.includes(focusSlot) ? focusSlot : eligible[0]
  const equipped = character?.gear?.find((item) => item.key === slot)
  const plannedPage = (shortlist.active || {})[slot]
  const planned = shortlist.items.find((item) => item.page_title === plannedPage)
  return [
    { label: 'Candidate', card: row.card },
    ...(equipped?.card ? [{ label: 'Equipped', card: equipped.card }] : []),
    ...(planned?.card && planned.page_title !== row.page_title
      ? [{ label: 'Planned', card: planned.card }] : []),
  ]
}

function ItemComparison({ cards, characterClass }) {
  return (
    <div className="planitemcompare">
      {cards.map(({ label, card }) => (
        <section key={`${label}-${card.name}`}>
          <div className="plancomparelabel">{label}</div>
          <Examine row={card} characterClass={characterClass} />
        </section>
      ))}
    </div>
  )
}

function ItemName({ row, character, shortlist, focusSlot }) {
  const label = <span className={rarityClass(row.tier)}>{row.name}</span>
  const cards = comparisonCards(row, character, shortlist, focusSlot)
  const characterClass = character?.character?.class || null
  const width = cards.length * 350 + Math.max(0, cards.length - 1) * 5 + 6
  return (
    <span className="lootitem">
      <span className="looticon">
        {row.card.icon != null && (
          <img src={`/api/items/icon/${row.card.icon}.png`} alt="" width="24"
               height="24" loading="lazy" />
        )}
      </span>
      <Hover className="examinecard plancomparecard" width={width}
             card={<ItemComparison cards={cards} characterClass={characterClass} />}>
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
      {first.kind === 'quest' && <QuestLinks page={first.source_page} />}
    </span>
  )
}

/* Rank and equip the SET ADORNMENTS themselves. Carrier armour and generic
   compatible-item lists bury the actual decision, so this surface contains
   only the bonus ladder and explicit adornment actions. */
function SetBonusList({ bonuses }) {
  return (
    <ul className="setbonuses">
      {(bonuses || []).map((bonus, index) => {
        const summary = [...(bonus.stat_lines || []), bonus.text].filter(Boolean)
        return (
          <li key={index}>
            <b>({bonus.pieces})</b>
            <span>{summary.length ? summary.join(' · ') : 'Bonus details unavailable'}</span>
            {(bonus.detail || []).map((line, lineIndex) => (
              <small key={lineIndex}>{line}</small>
            ))}
          </li>
        )
      })}
    </ul>
  )
}

/* WHAT THE WHOLE SET IS WORTH, in the app's own set arithmetic — the same
   typed per-tier stats `PlanLoadout` adds into the projection, summed up the
   ladder because EQ2's thresholds are cumulative.

   Prose tiers are not invented here. A Focus effect, or a line the typer
   refuses rather than half-read, is on the ladder one click away; a set with no
   typed stat at all leads with its top tier's own words instead of an empty
   line. PLANNER DECIMALS ARE SOURCE PRECISION: a tier written "3.5% Max
   Health" is not a 4. */
function setGrants(set, statLabel, statPct) {
  const ladder = set.bonuses || []
  const totals = {}
  ladder.forEach((bonus) => Object.entries(bonus.stats || {}).forEach(
    ([key, value]) => { totals[key] = (totals[key] || 0) + value }))
  const lines = Object.entries(totals).map(([key, value]) =>
    `${precise(value)}${statPct[key] ? '%' : ''} `
    + `${statLabel[key] || SET_STAT_FALLBACK[key] || key}`)
  return lines.length ? lines.join(' · ') : (ladder[ladder.length - 1]?.text || '')
}

/* One set against one loadout: which equipped positions can legally host it,
   which already wear it, and which of its slot-specific turquoises has
   somewhere to go. Every piece is still individually trackable whether or not
   anything can host it — where it DROPS is a separate question from whether
   you can wear it today. */
function setView(set, targets, inList) {
  const legal = targets.filter((target) =>
    setFitsHost(set, target.item, target.catalog))
  const pieces = (set.pieces || []).map((piece) => {
    const carriers = (set.carriers || []).filter((item) =>
      [item.slot, item.slot2].filter(Boolean).some(
        (slot) => setPieceForSlot(set, slot) === piece))
      .sort((a, b) => (b.level || 0) - (a.level || 0)
        || a.name.localeCompare(b.name))
    return {
      piece,
      label: piece.split(':').pop().trim(),
      tracked: inList.has(piece),
      carriers,
      targets: legal.filter((target) =>
        setPieceForSlot(set, target.catalog) === piece),
    }
  })
  return {
    set,
    pieces,
    worn: legal.filter((target) => target.installed === set.name),
    fits: pieces.filter((row) => row.targets.length).length,
  }
}

function SetTrackButton({ row, canPlan, set, onToggle }) {
  const carrier = row.carriers[0]
  const button = (
    <button type="button" aria-pressed={row.tracked}
            className={`settrackchip${row.tracked ? ' on' : ''}`}
            disabled={!canPlan && !row.tracked}
            title={!carrier && canPlan ? row.piece
              : !canPlan ? `Load a character to track ${row.piece}` : undefined}
            onClick={() => onToggle({
              name: row.piece, set_name: set.name, slot_label: row.label,
              level: set.level, pieces: set.pieces, bonuses: set.bonuses,
            })}>
      {carrier?.icon != null
        ? <img src={`/api/items/icon/${carrier.icon}.png`} alt="" width="28" height="28" />
        : <span className="settrackfallback" aria-hidden="true">◆</span>}
      <span>{row.label}</span>
    </button>
  )
  return carrier?.card ? (
    <Hover className="examinecard" width={350} card={<Examine row={carrier.card} />}>
      {button}
    </Hover>
  ) : button
}

function SetRow({ view, canPlan, hasCharacter, open, onOpen, onToggle,
                  onEquipAdornment, eraLabel, statLabel, statPct }) {
  const { set, pieces, worn, fits } = view
  const ladder = (set.bonuses || []).map((bonus) => bonus.pieces).join('/')
  const hosts = pieces.filter((row) => row.targets.length)
  return (
    <div className={`setrow${open ? ' open' : ''}${worn.length ? ' worn' : ''}`}>
      <button type="button" className="setrowhead" aria-expanded={open}
              onClick={onOpen}>
        <span className="caret" aria-hidden="true">›</span>
        <b className="setname">{set.name}</b>
        {/* The three short facts travel together, so a narrow window drops
            them under the name as one line instead of three. */}
        <span className="setfacts">
          <span className="setera">
            {eraLabel[set.era] || set.era}{set.level ? ` ${set.level}` : ''}
          </span>
          {!!ladder && (
            <span className="settiers" title="Piece thresholds">{ladder}</span>
          )}
          {hasCharacter && (
            <span className={`setfit${worn.length ? ' worn' : fits ? '' : ' none'}`}>
              {worn.length ? `${fits} fit · ${worn.length} worn`
                : fits ? `${fits} fit` : 'No fit'}
            </span>
          )}
        </span>
        <span className="setgrant">{setGrants(set, statLabel, statPct)}</span>
      </button>
      {open && (
        <div className="setdetail">
          <SetBonusList bonuses={set.bonuses} />
          <div className="setwork">
            <div className="setworkrow">
              <span className="seclabel">Equip on</span>
              <div className="setworkhosts">
                {hosts.length ? hosts.flatMap((row) => row.targets.map((target) => {
                  const isWorn = target.installed === set.name
                  return (
                    <button type="button" key={`${row.piece}:${target.key}`}
                            className={`settarget${isWorn ? ' on' : ''}`}
                            title={isWorn
                              ? `Remove ${row.piece} from ${target.item.name}`
                              : target.installed
                                ? `Replace ${target.installed} on ${target.item.name}`
                                : `Equip ${row.piece} on ${target.item.name}`}
                            onClick={() => onEquipAdornment(
                              target.key, isWorn ? null : set.name)}>
                      <b>{target.label}</b>
                      <span>{target.item.name}</span>
                      <em>{isWorn ? 'Equipped · remove'
                        : target.installed ? `Replace ${target.installed}` : 'Equip'}</em>
                    </button>
                  )
                })) : (
                  <span className="muted">{hasCharacter
                    ? 'No socket in this loadout can host a piece of this set'
                    : 'Load a character to try this set on'}</span>
                )}
              </div>
            </div>
            <div className="setworkrow">
              <span className="seclabel">Track source</span>
              <div className="setworktrack">
                {pieces.map((row) => <SetTrackButton key={row.piece} row={row}
                  canPlan={canPlan} set={set} onToggle={onToggle} />)}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ONE LINE PER SET, OPENED FOR THE WORK.

   The first pass drew all 113 of them as full cards — the whole bonus ladder,
   seven piece rows apiece, and a three-line host button on each row, most of
   which said "No compatible host". Finding one adornment in that is the entire
   job of this view and it was the thing the view was worst at.

   A reader here is scanning for two facts: what a set grants, and whether it
   fits what they are wearing. The LINE carries those two. Equipping and
   tracking are the work behind the decision, so they live in the opened row,
   where the pieces that cannot go anywhere cost a chip instead of a row. */
function SetList({ sets, inList, canPlan, targets, onToggle, onEquipAdornment,
                   eraLabel, statLabel, statPct }) {
  const [open, setOpen] = useState(() => new Set())
  /* WORN FIRST, THEN WHAT FITS. Alphabetical is the server's order and stays
     the tiebreak, but a reader with a character loaded is looking at their own
     gear — what is already on it, then what could be. */
  const views = useMemo(() => sets
    .map((set) => setView(set, targets, inList))
    .sort((a, b) => (b.worn.length ? 1 : 0) - (a.worn.length ? 1 : 0)
      || (b.fits ? 1 : 0) - (a.fits ? 1 : 0)
      || a.set.name.localeCompare(b.set.name)), [sets, targets, inList])
  if (!views.length) {
    return <p className="muted">No adornment sets in this selection.</p>
  }
  return (
    <div className="setrows">
      {views.map((view) => (
        <SetRow key={view.set.name} view={view} canPlan={canPlan}
                hasCharacter={!!targets.length}
                open={open.has(view.set.name)}
                onOpen={() => setOpen((current) => {
                  const next = new Set(current)
                  if (!next.delete(view.set.name)) next.add(view.set.name)
                  return next
                })}
                onToggle={onToggle} onEquipAdornment={onEquipAdornment}
                eraLabel={eraLabel} statLabel={statLabel} statPct={statPct} />
      ))}
    </div>
  )
}

/* Two kinds of thing, listed separately: items and adornments. A turquoise is
   not its host item even when both happen to lead to the same source row.

   It heads the Outline, which is the page that answers it — "here is what you
   picked, and here is what to do about it" reads in that order. */
function ShortItemIdentity({ item }) {
  const content = (
    <span className="shortitemidentity" tabIndex={item.card ? 0 : undefined}>
      {item.icon != null
        ? <img src={`/api/items/icon/${item.icon}.png`} alt="" width="24" height="24" />
        : <span className="shortrowicon" aria-hidden="true">◆</span>}
      <span className={`shortrowname ${rarityClass(item.tier)}`}><small>Item</small>{item.name}</span>
    </span>
  )
  return item.card ? (
    <Hover className="examinecard" width={350} card={<Examine row={item.card} />}>
      {content}
    </Hover>
  ) : content
}

function SetPieceIdentity({ piece }) {
  const card = (
    <div className="setpiecegoalcard">
      <span className="seclabel">Turquoise adornment</span>
      <h3>{piece.name}</h3>
      <p>{piece.set_name || 'Adornment set'}{piece.level ? ` · Level ${piece.level}` : ''}</p>
      <SetBonusList bonuses={piece.bonuses} />
    </div>
  )
  return (
    <Hover className="setpiecegoalpopup" width={360} card={card}>
      <span className="shortitemidentity" tabIndex="0">
        <span className="shortrowname"><small>Set piece</small>{piece.name}</span>
      </span>
    </Hover>
  )
}

function Shortlist({ list, onDropItem, onDropSet }) {
  const total = list.items.length + list.sets.length
  return (
    <details className="shortlist">
      <summary>
        <span>Tracked targets</span>
        <small>{total}</small>
      </summary>
      <div className="shortloaded">
          {list.items.map((item) => (
            <div className="shortrow" key={item.page_title}>
              <ShortItemIdentity item={item} />
              <em>{item.slot_label || item.slot || ''}</em>
              <button className="iconbtn" aria-label={`Remove ${item.name}`}
                      onClick={() => onDropItem(item)}>✕</button>
            </div>
          ))}
          {list.sets.map((set) => (
            <div className="shortrow" key={set.name}>
              <SetPieceIdentity piece={set} />
              <em>{set.slot_label || (set.level ? `L${set.level}` : '')}</em>
              <button className="iconbtn" aria-label={`Remove ${set.name}`}
                      onClick={() => onDropSet(set)}>✕</button>
            </div>
          ))}
      </div>
    </details>
  )
}
