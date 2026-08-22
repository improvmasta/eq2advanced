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
import { mergeObtainedItems, observedPlannerItems, reconcileLocalObtained,
  reconcilePlanTargets } from '../lib/planReconciliation.js'
import { defaultSavedSetName, hasPlannedEquipment, savedSetInUse,
  firstAvailableSavedSet, restoredWorkspace, savedSetPayloadEqual,
  savedSetSnapshot, validateWorkspaceBase, workspaceSnapshot,
  workspaceStatus } from '../lib/planSavedSets.js'
import { canonicalPlannerKey, characterForRequest, chooseAccountCharacter,
  lookupName, mergeRecentCharacters, ownerOfSummary } from '../lib/plannerLifecycle.js'
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
const WORKSPACE_KEY = 'eq2adv:plan:workspace:v3'
const SAVED_SETS_KEY = 'eq2adv:plan:saved-sets:v2'
const LEGACY_SAVED_SETS_KEY = 'eq2adv:plan:saved-sets:v1'
const RECENT_CHARACTERS_KEY = 'eq2adv:plan:recent-characters:v1'
const OBTAINED_ITEMS_KEY = 'eq2adv:plan:obtained-items:v1'
const SAVED_SET_COUNT = 5
/* THREE CHOICES, EACH DEFAULTING TO ANY. The priority list is still an ORDER
   and still never shows a weight — what changed is only how you say it. A
   draggable track of fourteen tokens made the reader arrange every stat in the
   game to name two, and the boundary between "ranked" and "the rest" had to be
   set with a separate control ("Score top"); three ordinary dropdowns say the
   same thing and the number of them IS the boundary.

   POTENCY AND CRIT ARE NOT OFFERED. They are on about four items in five in
   these expansions, so ordering by them separates nothing; the server refuses
   them too, so a hand-built URL cannot put them back (`catalog.weights`). They
   remain on the examine card and are available as table columns. */
const PRIORITY_SLOTS = 3
const QUICK_PRIORITY_SLOTS = 5
const ERA_DISPLAY_ORDER = ['eof', 'rok']
const DISCOVERY_SAMPLE_SIZE = 15

/* `zone` is a WORLD DROP: the item is in a zone's drop list and no named or
   quest in the catalog claims it, which is as much as can honestly be said
   about gear that fell off trash. It is most of what a broker search returns
   and none of it was reachable by inverting named monsters. */
const KIND_LABEL = {
  raid: 'Raid', group: 'Group', solo: 'Solo', quest: 'Quest',
  crafted: 'Crafted', zone: 'World drop', unknown: 'Unknown',
}
/* Source is the one facet where a reader routinely wants TWO answers — "group
   or raid", "quest or solo" — and a single-choice dropdown made that two
   searches. Checkboxes say it in one, and they show the whole list without
   being opened, which four short words can afford. */
const KIND_ORDER = ['raid', 'group', 'solo', 'quest', 'crafted', 'zone', 'unknown']
const LEVEL_OPTIONS = [{ value: '', label: 'Any' }, ...Array.from({ length: 200 }, (_, i) => ({
  value: String(i + 1), label: String(i + 1),
}))]
const TRACK_LABEL = {
  abmod: 'Ability', acspeed: 'Casting', arspeed: 'Reuse', aspeed: 'Haste',
  dps: 'DPS', multi: 'Multi', flurry: 'Flurry', aeauto: 'AE Auto',
  bchance: 'Block', hategain: 'Hate', mit: 'Mit', strike: 'Strike',
  maxhealth: 'Max HP',
}
const TABLE_STAT_LABEL = { abmod: 'Ab Mod', acspeed: 'Cast Speed' }

/* The two set-bonus stats `/plan/meta` does not carry a label for — it lists
   what can be RANKED, and neither of these can be. */
const SET_STAT_FALLBACK = { health: 'Health', power: 'Power' }

const precise = (value) => Number(value).toLocaleString(undefined, {
  maximumFractionDigits: 2,
})
const targetFloor = (range) => Number(range?.slider_min ?? range?.min ?? 0)
const targetCeiling = (range) => Number(range?.slider_max ?? range?.max ?? 0)
const snapTarget = (value, floor, step) => {
  const snapped = floor + Math.round((value - floor) / step) * step
  return Number(snapped.toFixed(4))
}

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

function workspaceStorageKey(ownerKey) {
  return `${WORKSPACE_KEY}:${encodeURIComponent(ownerKey)}`
}

function ownerMatches(saved, owner) {
  if (!saved?.key || saved.key === owner?.key) return true
  return canonicalPlannerKey(saved.world || owner?.world,
    saved.lookup_name || saved.lookupName || saved.display_name || saved.name) === owner?.key
}

function loadWorkspace(owner) {
  if (!owner?.key) return workspaceSnapshot(null, null, emptyShortlist())
  try {
    const direct = localStorage.getItem(workspaceStorageKey(owner.key))
    let raw = direct ? JSON.parse(direct) : null
    if (!raw) {
      const oldKeys = [owner.key, ...(owner.legacyKeys || [])]
      for (const oldKey of oldKeys) {
        const legacy = localStorage.getItem(shortlistStorageKey(oldKey))
        if (legacy) { raw = JSON.parse(legacy); break }
      }
    }
    const restored = restoredWorkspace(raw || emptyShortlist(), owner, normalizeShortlist)
    const bound = bindShortlist(restored.shortlist, owner)
    return workspaceSnapshot(owner, restored.base,
      bound || { ...emptyShortlist(), owner })
  } catch {
    return workspaceSnapshot(owner, null, { ...emptyShortlist(), owner })
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
  if (normalized.owner?.key && !ownerMatches(normalized.owner, owner)) return null
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

function mergeAccountSavedSets(serverRows, localRows) {
  const server = normalizeSavedSets(serverRows)
  const local = normalizeSavedSets(localRows)
  return server.map((row, index) => {
    const cached = local[index]
    if (!savedSetInUse(cached)) return row
    if (!savedSetInUse(row)) return { ...cached, _needs_sync: true }
    if (savedSetPayloadEqual(row.payload, cached.payload)) {
      return Number(cached.updated_ts || 0) > Number(row.updated_ts || 0)
        ? { ...cached, _needs_sync: true } : row
    }
    return Number(cached.updated_ts || 0) > Number(row.updated_ts || 0)
      ? { ...cached, _needs_sync: true } : row
  })
}

function mergeGuestSavedSets(accountRows, guestRows) {
  const account = normalizeSavedSets(accountRows)
  const guest = normalizeSavedSets(guestRows)
  return account.map((row, index) => {
    const copy = guest[index]
    if (!savedSetInUse(copy)) return row
    if (!savedSetInUse(row)) return { ...copy, _needs_sync: true }
    if (savedSetPayloadEqual(row.payload, copy.payload)) return row
    return { ...row, _guest_conflict: copy }
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
    for (const key of [owner.key, ...(owner.legacyKeys || [])]) {
      const direct = localStorage.getItem(savedSetsStorageKey(user, key))
      if (!direct) continue
      const rows = normalizeSavedSets(JSON.parse(direct))
      if (key !== owner.key) writeLocalSavedSets(user, owner, rows)
      return rows
    }

    /* v1 offered five slots for the entire reader. Every captured payload
       already named the public character it was built against, so adopt only
       this character's rows into its new five-slot folder. Leave v1 in place
       as a recovery copy while other character folders are opened later. */
    const legacy = normalizeSavedSets(JSON.parse(
      localStorage.getItem(legacySavedSetsStorageKey(user))))
    const adopted = normalizeSavedSets(legacy.map((row) => (
      ownerMatches(row.payload?.shortlist?.owner, owner) ? row : null
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
    const recent = Array.isArray(rows) ? rows : []
    const legacy = normalizeSavedSets(JSON.parse(
      localStorage.getItem(legacySavedSetsStorageKey(null))))
    const owners = legacy.map((row) => row.payload?.shortlist?.owner)
      .filter((owner) => owner?.key && (owner?.lookup_name || owner?.name))
      .map((owner) => ({ ...owner, saved: true }))
    return mergeRecentCharacters(recent, owners)
  } catch { return [] }
}

function writeRecentCharacters(rows) {
  try { localStorage.setItem(RECENT_CHARACTERS_KEY, JSON.stringify(rows)) }
  catch { /* private mode */ }
}

function obtainedStorageKey(user, ownerKey) {
  return `${OBTAINED_ITEMS_KEY}:${user?.id || 'guest'}:${encodeURIComponent(ownerKey)}`
}

function readLocalObtained(user, owner) {
  if (!owner?.key) return []
  try {
    const rows = JSON.parse(localStorage.getItem(obtainedStorageKey(user, owner.key)))
    return Array.isArray(rows) ? rows : []
  } catch { return [] }
}

function writeLocalObtained(user, owner, rows) {
  if (!owner?.key) return
  try { localStorage.setItem(obtainedStorageKey(user, owner.key), JSON.stringify(rows)) }
  catch { /* private mode */ }
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
  const [quickClass, setQuickClass] = useState('')
  const [quickMaxLevel, setQuickMaxLevel] = useState('')
  const [quickOrder, setQuickOrder] = useState([])
  const [quickRequired, setQuickRequired] = useState([])
  const [quickKinds, setQuickKinds] = useState([])
  const [quickArmor, setQuickArmor] = useState([])
  const [quickRanges, setQuickRanges] = useState(null)
  const [quickRangesBusy, setQuickRangesBusy] = useState(false)
  const [quickRangeErr, setQuickRangeErr] = useState('')
  const [quickTargets, setQuickTargets] = useState({})
  const [quickResult, setQuickResult] = useState(null)
  const [quickBusy, setQuickBusy] = useState(false)
  const [quickErr, setQuickErr] = useState('')
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
  const [workspaceBase, setWorkspaceBase] = useState({
    kind: 'equipped', slot: null, saved_updated_ts: 0,
  })
  const [obtainedItems, setObtainedItems] = useState([])
  const [planNotice, setPlanNotice] = useState('')
  const [savedSets, setSavedSets] = useState(defaultSavedSets)
  const [savedSetsLoaded, setSavedSetsLoaded] = useState(false)
  const [savedSetsOwnerKey, setSavedSetsOwnerKey] = useState('')
  const [recentCharacters, setRecentCharacters] = useState(readRecentCharacters)
  const [activeSavedSetSlot, setActiveSavedSetSlot] = useState(null)
  const [savedSetBusy, setSavedSetBusy] = useState(false)
  const [savedSetStatus, setSavedSetStatus] = useState('')
  const savedSetsRef = useRef(savedSets)
  savedSetsRef.current = savedSets
  const savedSetSlotRevisions = useRef({})
  const shortlistRef = useRef(shortlist)
  shortlistRef.current = shortlist
  const workspaceBaseRef = useRef(workspaceBase)
  workspaceBaseRef.current = workspaceBase
  const workspaceValidationOwner = useRef('')
  const workspaceValidationPayload = useRef(null)
  const reconciledPlan = useMemo(
    () => reconcilePlanTargets(shortlist, obtainedItems), [shortlist, obtainedItems])
  const effectiveShortlist = reconciledPlan.remaining
  const completedTargets = reconciledPlan.completed
  const planCount = shortlist.owner?.key
    ? effectiveShortlist.items.length + effectiveShortlist.sets.length : 0
  const trackedCount = shortlist.owner?.key
    ? shortlist.items.length + shortlist.sets.length : 0
  const [outlineOpen, setOutlineOpen] = useState(trackedCount > 0)
  const previousTrackedCount = useRef(trackedCount)
  useEffect(() => {
    if (trackedCount > previousTrackedCount.current) setOutlineOpen(true)
    if (trackedCount === 0) setOutlineOpen(false)
    previousTrackedCount.current = trackedCount
  }, [trackedCount])
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
    const planned = hasPlannedEquipment(shortlist)
    if (workspaceBase.kind === 'equipped' && planned) {
      setWorkspaceBase({ kind: 'draft', slot: null, saved_updated_ts: 0 })
      return
    }
    if (workspaceBase.kind === 'draft' && !planned) {
      setWorkspaceBase({ kind: 'equipped', slot: null, saved_updated_ts: 0 })
      return
    }
    try {
      localStorage.setItem(workspaceStorageKey(shortlist.owner.key), JSON.stringify(
        workspaceSnapshot(shortlist.owner, workspaceBase, shortlist)))
    } catch { /* private mode — the workspace just doesn't survive a reload */ }
  }, [shortlist, workspaceBase])

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
  const lookupRequest = useRef({ generation: 0, controller: null })
  const invalidatePublicLookup = useCallback(() => {
    lookupRequest.current.controller?.abort()
    lookupRequest.current = {
      generation: lookupRequest.current.generation + 1,
      controller: null,
    }
  }, [])
  const loadLookedUpCharacter = useCallback((name) => {
    invalidatePublicLookup()
    const generation = lookupRequest.current.generation
    const controller = new AbortController()
    lookupRequest.current.controller = controller
    setLookupBusy(true)
    setLookupErr(null)
    setLookedUp(null)
    api.planCharacter(name, { signal: controller.signal })
      .then((d) => {
        if (lookupRequest.current.generation !== generation) return
        setLookedUp(d)
      })
      .catch((error) => {
        if (lookupRequest.current.generation !== generation
            || error?.name === 'AbortError') return
        setLookupErr(`No character record for “${name}”`)
      })
      .finally(() => {
        if (lookupRequest.current.generation === generation) setLookupBusy(false)
      })
  }, [invalidatePublicLookup])

  /* Character links across the site land on this URL state. Guard the effect
     because React StrictMode deliberately replays it in development; a link
     should still be one lookup. Back/forward changes the name and loads the
     newly selected toon. */
  useEffect(() => {
    const name = characterParam.trim()
    if (!name) {
      loadedLookup.current = ''
      invalidatePublicLookup()
      setLookedUp(null)
      setLookupBusy(false)
      setLookupErr(null)
      return
    }
    if (loadedLookup.current === name) return
    loadedLookup.current = name
    loadLookedUpCharacter(name)
  }, [characterParam, invalidatePublicLookup, loadLookedUpCharacter])

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
      if (!charId || !selectedExists) setCharId(chooseAccountCharacter(d.characters, charId))
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

  const planningCharacter = characterForRequest(characterParam, lookedUp, character)
  const planningOwner = useMemo(
    () => ownerOfSummary(planningCharacter), [planningCharacter])
  const quickMeta = meta?.quick_equip
  const quickWearableArmor = quickMeta?.class_armor?.[quickClass] || []
  const quickLevelOptions = useMemo(() => {
    const maximum = Math.max(1, Math.min(200, Number(meta?.level_max) || 80))
    return Array.from({ length: maximum }, (_, index) => {
      const level = String(maximum - index)
      return { value: level, label: level }
    })
  }, [meta?.level_max])

  /* Quick Equip starts from the public character facts but keeps them as
     ordinary controls. Changing the class or level here never rewrites the
     character snapshot or the Equipment catalog filters. */
  useEffect(() => {
    const current = planningCharacter?.character
    if (!current?.class || !current?.level) return
    setQuickClass(String(current.class).toLowerCase())
    setQuickMaxLevel(String(current.level))
  }, [planningOwner?.key])

  useEffect(() => {
    if (!quickClass || !quickMeta) return
    const allowed = quickMeta.class_armor?.[quickClass] || []
    setQuickArmor((current) => {
      const retained = current.filter((name) => allowed.includes(name))
      return retained.length ? retained : [...allowed]
    })
  }, [quickClass, quickMeta])

  useEffect(() => {
    if (!quickKinds.length && quickMeta?.source_kinds?.length) {
      setQuickKinds([...quickMeta.source_kinds])
    }
    if (!quickMaxLevel && meta?.level_max) setQuickMaxLevel(String(meta.level_max))
  }, [quickMeta, meta?.level_max])

  useEffect(() => {
    if (mode === 'quick' && !quickOrder.length && order.length) {
      setQuickOrder(order.slice(0, QUICK_PRIORITY_SLOTS))
    }
  }, [mode])

  useEffect(() => {
    setQuickResult(null)
    setQuickErr('')
  }, [erasParam, quickClass, quickMaxLevel, quickOrder, quickRequired,
    quickKinds, quickArmor, quickTargets])

  /* A target's scale belongs to the complete filtered loadout, not to a
     generic stat dictionary. Recalculate it when any eligibility control
     changes, debounce rapid picker work, and discard responses for criteria
     the reader has already left. */
  useEffect(() => {
    setQuickRanges(null)
  }, [mode, erasParam, quickClass, quickMaxLevel, quickRequired,
    quickKinds, quickArmor])

  useEffect(() => {
    if (mode !== 'quick') {
      setQuickRangesBusy(false)
      return undefined
    }
    const previousRanges = quickRanges?.ranges || {}
    setQuickRangeErr('')
    if (!quickClass || !quickMaxLevel || !quickOrder.length) {
      setQuickRangesBusy(false)
      return undefined
    }
    let stale = false
    setQuickRangesBusy(true)
    const timer = setTimeout(() => {
      api.planQuickEquipRanges({
        eras, class: quickClass, max_level: Number(quickMaxLevel),
        order: quickOrder, required: quickRequired,
        kinds: quickKinds, armor: quickArmor,
      }).then((next) => {
        if (stale) return
        setQuickRanges(next)
        setQuickTargets((current) => Object.fromEntries(quickOrder.map((key) => {
          const bounds = next.ranges?.[key]
          if (!bounds) return [key, current[key] ?? 0]
          const previous = previousRanges[key]
          const lower = targetFloor(bounds)
          const upper = targetCeiling(bounds)
          if (current[key] == null || !previous) return [key, upper]
          const previousLower = targetFloor(previous)
          const previousUpper = targetCeiling(previous)
          const ratio = previousUpper === previousLower ? 1
            : (current[key] - previousLower) / (previousUpper - previousLower)
          const scaled = lower + Math.max(0, Math.min(1, ratio)) * (upper - lower)
          const value = snapTarget(scaled, lower, Number(bounds.step) || 1)
          return [key, Math.max(lower, Math.min(upper, value))]
        })))
      }).catch((error) => {
        if (!stale) setQuickRangeErr(error.message)
      }).finally(() => {
        if (!stale) setQuickRangesBusy(false)
      })
    }, 220)
    return () => { stale = true; clearTimeout(timer) }
  }, [mode, erasParam, quickClass, quickMaxLevel, quickOrder, quickRequired,
    quickKinds, quickArmor])

  const currentSetPayload = useMemo(
    () => savedSetSnapshot(shortlist), [shortlist])
  const activeSavedSet = savedSets.find((row) => row.slot === activeSavedSetSlot)
  /* Normalize old payloads through the target-only v3 shape before comparing.
     Untouched equipped sockets remain implicit and continue to float. */
  const comparableSavedSetPayload = useMemo(() => {
    if (!activeSavedSet?.payload?.shortlist || !planningOwner) {
      return activeSavedSet?.payload
    }
    const bound = bindShortlist(activeSavedSet.payload.shortlist, planningOwner)
    return bound
      ? savedSetSnapshot(bound)
      : activeSavedSet.payload
  }, [activeSavedSet, planningOwner])
  const savedSetDirty = Boolean(activeSavedSet?.payload)
    && !savedSetPayloadEqual(comparableSavedSetPayload, currentSetPayload)
  const savedSetModified = hasPlannedEquipment(shortlist)
  const savedSetWorkingStatus = workspaceStatus(
    workspaceBase, activeSavedSet, savedSetDirty, savedSetModified)
  const planningOwnerKeyRef = useRef('')
  planningOwnerKeyRef.current = planningOwner?.key || ''
  /* Signing in or out changes which five private rows back the same public
     character. Validate the held workspace once against that new reader's
     rows; an account/guest collision must become a draft, not silently borrow
     the other copy's name. Owner changes are handled by loadWorkspace below. */
  useEffect(() => {
    if (!planningOwner) return
    workspaceValidationOwner.current = planningOwner.key
    workspaceValidationPayload.current = currentSetPayload
  }, [user?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!savedSetsLoaded || !activeSavedSetSlot) return
    const row = savedSets.find((set) => set.slot === activeSavedSetSlot)
    if (!savedSetInUse(row)) {
      setActiveSavedSetSlot(null)
      setWorkspaceBase({ kind: 'draft', slot: null, saved_updated_ts: 0 })
    }
  }, [activeSavedSetSlot, savedSets, savedSetsLoaded])
  useEffect(() => {
    setPlanNotice('')
    setSavedSetStatus('')
    setObtainedItems([])
    workspaceValidationOwner.current = planningOwner?.key || ''
    const workspace = loadWorkspace(planningOwner)
    workspaceValidationPayload.current = planningOwner
      ? savedSetSnapshot(workspace.shortlist) : null
    setShortlist(workspace.shortlist)
    setWorkspaceBase(workspace.base)
    setActiveSavedSetSlot(workspace.base.kind === 'saved' ? workspace.base.slot : null)
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

  /* Reconcile only a successful, current character response. The ledger is
     additive, so an item remains obtained after a later snapshot unequips it.
     Browser rows are the immediate/failure path and signed-in readers adopt
     only this same canonical character folder into their private account. */
  const observedItems = useMemo(
    () => observedPlannerItems(planningCharacter), [planningCharacter])
  useEffect(() => {
    if (!planningOwner || !planningCharacter?.synced) {
      setObtainedItems([])
      return undefined
    }
    let dead = false
    const accountLocal = readLocalObtained(user, planningOwner)
    const guestLocal = readLocalObtained(null, planningOwner)
    const starting = user
      ? mergeObtainedItems(accountLocal, guestLocal) : guestLocal
    setObtainedItems(starting)

    const announceProgress = (before, after) => {
      const target = shortlistRef.current
      const beforeCount = reconcilePlanTargets(target, before).completed.length
      const afterCount = reconcilePlanTargets(target, after).completed.length
      const advanced = Math.max(0, afterCount - beforeCount)
      if (advanced) setSavedSetStatus(
        `Plan updated - ${advanced} equipped target${advanced === 1 ? '' : 's'} completed`)
    }

    if (!user) {
      const reconciled = reconcileLocalObtained(starting, observedItems)
      setObtainedItems(reconciled.items)
      writeLocalObtained(null, planningOwner, reconciled.items)
      announceProgress(starting, reconciled.items)
      return () => { dead = true }
    }

    ;(async () => {
      let rows = starting
      try {
        const response = await api.planObtainedItems(planningOwner.key)
        if (dead) return
        rows = mergeObtainedItems(rows, response.items || [])
        const locallyReconciled = reconcileLocalObtained(rows, observedItems)
        const toAdopt = locallyReconciled.items
          .map((row) => ({
            item_key: row.item_key, item_name: row.item_name, source: row.source,
            first_seen_ts: row.first_seen_ts || undefined,
            last_seen_ts: row.last_seen_ts || undefined,
          }))
        /* The API bounds each write; batching keeps a long-lived additive
           ledger adoptable without ever sending an unbounded character doc. */
        for (let index = 0; index < toAdopt.length; index += 80) {
          const reconciled = await api.reconcilePlanObtainedItems(
            planningOwner, toAdopt.slice(index, index + 80))
          if (dead) return
          rows = mergeObtainedItems(rows, reconciled.items || [])
        }
        setObtainedItems(rows)
        writeLocalObtained(user, planningOwner, rows)
        announceProgress(starting, rows)
      } catch {
        if (!dead) writeLocalObtained(user, planningOwner, rows)
      }
    })()
    return () => { dead = true }
  }, [observedItems, planningCharacter?.synced, planningOwner?.key, user?.id])

  useEffect(() => {
    if (!user) return undefined
    let dead = false
    api.planSavedSetOwners().then(({ characters: rows }) => {
      if (dead) return
      const remembered = (rows || []).map((row) => ({
        key: row.owner_key, lookup_name: row.lookup_name,
        display_name: row.owner_name, name: row.owner_name,
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
    setSavedSetsLoaded(false)
    setSavedSetsOwnerKey('')
    if (!planningOwner) {
      setSavedSets(defaultSavedSets())
      setSavedSetsLoaded(true)
      return undefined
    }
    const accountLocal = readLocalSavedSets(user, planningOwner)
    const guestLocal = readLocalSavedSets(null, planningOwner)
    if (!user) {
      setSavedSets(guestLocal)
      setSavedSetsOwnerKey(planningOwner.key)
      setSavedSetsLoaded(true)
      return undefined
    }
    const localMerged = mergeGuestSavedSets(accountLocal, guestLocal)
    setSavedSets(localMerged)
    const startingRevisions = { ...savedSetSlotRevisions.current }
    api.planSavedSets(planningOwner.key).then(async (response) => {
      if (dead) return
      const server = normalizeSavedSets(response.sets)
      let adopted = mergeGuestSavedSets(
        mergeAccountSavedSets(server, accountLocal), guestLocal)
      adopted = adopted.map((row) => {
        if ((savedSetSlotRevisions.current[row.slot] || 0)
            === (startingRevisions[row.slot] || 0)) return row
        return savedSetsRef.current.find((current) => current.slot === row.slot) || row
      })
      setSavedSets(adopted)
      setSavedSetsOwnerKey(planningOwner.key)
      setSavedSetsLoaded(true)
      writeLocalSavedSets(user, planningOwner, adopted)
      const pending = adopted.filter((row) => row._needs_sync
        && (savedSetSlotRevisions.current[row.slot] || 0)
          === (startingRevisions[row.slot] || 0))
      if (pending.length) {
        const written = await Promise.all(pending.map((row) => (
          api.putPlanSavedSet(planningOwner, row.slot, row.name, row.payload,
            server[row.slot - 1]?.updated_ts ?? -1)
            .catch(() => null)
        )))
        if (dead) return
        const bySlot = new Map(written.filter(Boolean)
          .map((result) => [result.set.slot, result.set]))
        const synced = adopted.map((row) => {
          if ((savedSetSlotRevisions.current[row.slot] || 0)
              !== (startingRevisions[row.slot] || 0)) {
            return savedSetsRef.current.find((current) => current.slot === row.slot) || row
          }
          const saved = bySlot.get(row.slot)
          return saved ? { ...saved, _guest_conflict: row._guest_conflict } : row
        })
        setSavedSets(synced)
        const base = workspaceBaseRef.current
        const syncedBase = bySlot.get(base.slot)
        if (base.kind === 'saved' && syncedBase
            && savedSetPayloadEqual(syncedBase.payload, currentSetPayload)) {
          setWorkspaceBase({
            kind: 'saved', slot: base.slot,
            saved_updated_ts: syncedBase.updated_ts || 0,
          })
        }
        writeLocalSavedSets(user, planningOwner, synced)
        if (written.some((result) => !result)) setSavedSetStatus('Saved here; sync failed')
      }
    }).catch(() => {
      if (!dead) {
        setSavedSets(localMerged)
        setSavedSetsOwnerKey(planningOwner.key)
        setSavedSetsLoaded(true)
        setSavedSetStatus('Using browser saves')
      }
    })
    return () => { dead = true }
  }, [planningOwner?.key, user?.id])

  /* A v3 workspace remembers which named row it came from. Validate only
     after that character's five rows arrive; until then the provisional base
     pointer keeps the chooser stable instead of flashing `Choose set`. */
  useEffect(() => {
    if (!planningOwner || !savedSetsLoaded
        || savedSetsOwnerKey !== planningOwner.key
        || workspaceValidationOwner.current !== planningOwner.key) return
    /* This is restoration validation, not dirty tracking. Consume it once for
       this owner after their rows arrive; later working edits intentionally
       retain the named base and render "changes not saved". */
    workspaceValidationOwner.current = ''
    const restoredPayload = workspaceValidationPayload.current || currentSetPayload
    workspaceValidationPayload.current = null
    const comparableRows = savedSets.map((row) => {
      if (!row.payload?.shortlist) return row
      const bound = bindShortlist(row.payload.shortlist, planningOwner)
      return bound ? { ...row,
        payload: savedSetSnapshot(bound) } : row
    })
    const validated = validateWorkspaceBase(
      { base: workspaceBase }, comparableRows, restoredPayload)
    const same = validated.kind === workspaceBase.kind
      && Number(validated.slot || 0) === Number(workspaceBase.slot || 0)
      && Number(validated.saved_updated_ts || 0)
        === Number(workspaceBase.saved_updated_ts || 0)
    if (!same) {
      if (workspaceBase.kind === 'saved') {
        setSavedSetStatus('Saved set changed; draft restored')
      }
      setWorkspaceBase(validated)
    }
    setActiveSavedSetSlot(validated.kind === 'saved' ? validated.slot : null)
  }, [currentSetPayload, planningCharacter, planningOwner, savedSets,
    savedSetsLoaded, savedSetsOwnerKey, workspaceBase])

  const writeSavedSet = useCallback((slotNumber, nextName, nextPayload,
                                      successStatus = 'Saved',
                                      clearGuestConflict = false) => {
    const held = savedSetsRef.current.find((row) => row.slot === slotNumber)
    if (!held || !planningOwner) return Promise.resolve(null)
    const revision = (savedSetSlotRevisions.current[slotNumber] || 0) + 1
    savedSetSlotRevisions.current[slotNumber] = revision
    const nextRow = {
      ...held, name: (nextName ?? held.name).trim() || defaultSavedSetName(slotNumber),
      payload: nextPayload,
      updated_ts: Math.floor(Date.now() / 1000),
      _needs_sync: false,
    }
    if (clearGuestConflict) delete nextRow._guest_conflict
    const next = savedSetsRef.current.map(
      (row) => row.slot === slotNumber ? nextRow : row)
    setSavedSets(next)
    writeLocalSavedSets(user, planningOwner, next)
    setSavedSetStatus(user ? 'Saving…'
      : successStatus === 'Deleted' ? 'Deleted in this browser' : 'Saved in this browser')
    if (!user) return Promise.resolve(nextRow)
    setSavedSetBusy(true)
    const request = nextPayload === null
      ? api.deletePlanSavedSet(planningOwner.key, slotNumber).then(() => ({
        set: { slot: slotNumber, name: defaultSavedSetName(slotNumber),
          payload: null, updated_ts: null },
      }))
      : api.putPlanSavedSet(planningOwner, slotNumber, nextRow.name, nextRow.payload)
    return request.then(({ set }) => {
        if (savedSetSlotRevisions.current[slotNumber] !== revision) return null
        const synced = savedSetsRef.current.map((row) => row.slot === slotNumber
          ? { ...set, _guest_conflict: clearGuestConflict ? undefined : row._guest_conflict }
          : row)
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
  }, [planningOwner, user?.id])

  const saveSavedSet = useCallback((slotNumber, name) => {
    return writeSavedSet(slotNumber, name, currentSetPayload).then((row) => {
      if (!row) return row
      setActiveSavedSetSlot(slotNumber)
      setWorkspaceBase({
        kind: 'saved', slot: slotNumber, saved_updated_ts: row.updated_ts || 0,
      })
      return row
    })
  }, [currentSetPayload, writeSavedSet])

  const renameSavedSet = useCallback((slotNumber, name) => {
    const held = savedSetsRef.current.find((row) => row.slot === slotNumber)
    return writeSavedSet(slotNumber, name, held?.payload || null).then((row) => {
      if (row && Number(activeSavedSetSlot) === Number(slotNumber)) {
        setWorkspaceBase({
          kind: 'saved', slot: slotNumber, saved_updated_ts: row.updated_ts || 0,
        })
      }
      return row
    })
  }, [activeSavedSetSlot, writeSavedSet])

  const deleteSavedSet = useCallback((slotNumber) => {
    if (Number(activeSavedSetSlot) === Number(slotNumber)) {
      setActiveSavedSetSlot(null)
      setWorkspaceBase({ kind: 'draft', slot: null, saved_updated_ts: 0 })
    }
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
    setWorkspaceBase({
      kind: 'saved', slot: slotNumber, saved_updated_ts: held.updated_ts || 0,
    })
    setSavedSetStatus(`Loaded ${held.name}`)
    return true
  }, [planningOwner, savedSets])

  const copyQuickToSavedSet = useCallback((slotNumber, name, items) => {
    if (!planningOwner) return Promise.resolve(null)
    const quickShortlist = {
      owner: planningOwner,
      items: items.map((item) => ({ ...item })),
      sets: [],
      active: Object.fromEntries(items.map((item) => [item.equip_slot, item.page_title])),
      set_slots: {},
      adorn_slots: {},
    }
    /* Quick Equip makes no adornment decisions. Store exactly the generated
       gear and leave both socket maps empty; loading the set can inherit or
       edit adornments through the normal equipment window later. */
    const payload = { version: 3, shortlist: quickShortlist }
    /* Copying writes the named destination but does not replace the working
       loadout. Existing unsaved planning work stays on screen; the generated
       set can be loaded from the stable Gear Sets control when wanted. */
    return writeSavedSet(slotNumber, name, payload, 'Copied')
  }, [planningOwner, writeSavedSet])

  const importGuestSavedSet = useCallback((slotNumber) => {
    const held = savedSetsRef.current.find((row) => row.slot === slotNumber)
    const guest = held?._guest_conflict
    if (!guest?.payload) return Promise.resolve(null)
    return writeSavedSet(slotNumber, guest.name, guest.payload,
      'Guest copy imported', true)
  }, [writeSavedSet])

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
    if (mode === 'quick') return ''
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
  const catalogKey = mode === 'quick' ? null : `${mode}:${query}`
  const exactData = catalogKey ? catalogResults[catalogKey] || null : null
  /* A query must not replace a full results table with one line of loading
     copy. Retain the last result for each catalog task until its exact
     replacement arrives; mode separation prevents item rows ever standing in
     for set rows (or vice versa). */
  const retainedCatalog = useRef({ items: null, sets: null })
  if (exactData) retainedCatalog.current[mode] = exactData
  const data = mode === 'quick' ? null : exactData || retainedCatalog.current[mode]
  const catalogUpdating = !exactData && !!data && !err

  /* Page titles can contain commas, so shortlist entries are repeated query
     parameters. The shortlist itself stays in localStorage and never enters
     the page URL; eras/class/priorities are the shareable plan, picks are this
     browser's working set. */
  const outlineQuery = useMemo(() => {
    const p = new URLSearchParams({ eras: csv(eras) })
    if (planningOwner?.className) p.set('class', planningOwner.className)
    effectiveShortlist.items.forEach((i) => p.append('item', i.page_title))
    effectiveShortlist.sets.forEach((s) => p.append('set', s.name))
    return p.toString()
  }, [erasParam, effectiveShortlist, planningOwner?.className])

  useEffect(() => {
    if (mode === 'quick' || !catalogKey) return undefined
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
    () => new Set(shortlist.sets.flatMap((set) => (
      set.name === set.set_name && set.pieces?.length
        ? [set.name, ...set.pieces] : [set.name]
    ))), [shortlist])

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
    const trackedName = shortlist.sets.find((set) => set.name === row.name
      || (set.name === set.set_name && set.pieces?.includes(row.name)))?.name
      || row.tracked_name || row.name
    const removing = shortlist.sets.some((set) => set.name === trackedName)
    if (!removing && !planningOwner) {
      setPlanNotice('Load a character before tracking sources for an adornment set.')
      return
    }
    setPlanNotice('')
    setShortlist((s) => {
    const currentName = s.sets.find((set) => set.name === row.name
      || (set.name === set.set_name && set.pieces?.includes(row.name)))?.name
      || row.tracked_name || row.name
    if (s.sets.some((x) => x.name === currentName)) {
      return {
        ...s,
        sets: s.sets.filter((x) => x.name !== currentName),
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
    setOrderLine(null)
    setCls(null); setSlot(null); setArmor(null); setTier(null); setKind(null)
    setLevelMin(null); setLevelMax(null)
    setCarries(null); setProc(null); setQ(''); setTyped('')
  }, [setOrderLine, setCls, setSlot, setArmor, setTier, setKind, setLevelMin,
    setLevelMax, setCarries, setProc])

  const resetCatalogFilters = useCallback(() => {
    if (mode === 'sets') {
      setSetSearch('')
      setShowAllSets(true)
      return
    }
    clearCatalogFilters()
  }, [mode, clearCatalogFilters])

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

  const setQuickPriority = useCallback((at, key) => {
    const next = [...quickOrder]
    next[at] = key || ''
    const compact = next.filter((candidate, index) => (
      candidate && next.indexOf(candidate) === index
    )).slice(0, QUICK_PRIORITY_SLOTS)
    setQuickOrder(compact)
    // Crit has its own independent rule below the ordering controls. Removing
    // Crit from the priority list must not silently turn that rule off.
    setQuickRequired((required) => required.filter(
      (stat) => stat === 'crit' || compact.includes(stat)))
    setQuickTargets((targets) => Object.fromEntries(
      Object.entries(targets).filter(([stat]) => compact.includes(stat))))
  }, [quickOrder])

  const toggleQuickRequired = useCallback((key) => {
    if (!key) return
    setQuickRequired((required) => required.includes(key)
      ? required.filter((stat) => stat !== key) : [...required, key])
  }, [])

  const toggleQuickKind = useCallback((key) => {
    setQuickKinds((current) => {
      const next = current.includes(key)
        ? current.filter((kind) => kind !== key) : [...current, key]
      return next.length ? next : current
    })
  }, [])

  const toggleQuickArmor = useCallback((name) => {
    setQuickArmor((current) => {
      const next = current.includes(name)
        ? current.filter((armorName) => armorName !== name) : [...current, name]
      return next.length ? next : current
    })
  }, [])

  const useCurrentQuickCharacter = useCallback(() => {
    const current = planningCharacter?.character
    if (!current) return
    setQuickClass(String(current.class || '').toLowerCase())
    setQuickMaxLevel(String(current.level || ''))
  }, [planningCharacter])

  const runQuickEquip = useCallback(() => {
    if (!quickClass) { setQuickErr('Choose a class.'); return }
    if (!quickMaxLevel) { setQuickErr('Choose a maximum gear level.'); return }
    if (!quickOrder.length) { setQuickErr('Choose at least one stat priority.'); return }
    if (!quickRanges || quickOrder.some((key) => quickTargets[key] == null)) {
      setQuickErr('Wait for the achievable target ranges.'); return
    }
    setQuickBusy(true)
    setQuickErr('')
    api.planQuickEquip({
      eras, class: quickClass, max_level: Number(quickMaxLevel),
      order: quickOrder, required: quickRequired,
      kinds: quickKinds, armor: quickArmor, targets: quickTargets,
    }).then(setQuickResult)
      .catch((error) => setQuickErr(error.message))
      .finally(() => setQuickBusy(false))
  }, [erasParam, quickClass, quickMaxLevel, quickOrder, quickRequired,
    quickKinds, quickArmor, quickRanges, quickTargets])

  /* How many of the listed stats actually RANK. The server drops potency and
     crit whatever the URL says, so this is its count and not the raw order's
     length — "2 of 3" has to mean the same three the scorer used. */
  const ranked = data?.ranked?.length ?? order.length

  const columns = useMemo(
    () => itemColumns({ order, statLabel, statPct,
      character: planningCharacter, shortlist: effectiveShortlist, focusSlot }),
    [order, statLabel, statPct, planningCharacter, effectiveShortlist, focusSlot])

  /* Every rankable stat, grouped the way a raider already thinks about them
     ("Abilities", "Melee", "Tanking") — the groups are the server's
     (`wiki.STAT_GROUPS`) and the headers are lines in the list, not options. */
  const priorityOptions = useMemo(() => [
    { value: '', label: 'Any' },
    ...(meta?.groups || []).flatMap((group) => group.stats.map((stat) => ({
      value: stat.key, label: stat.label, group: group.label,
    }))),
  ], [meta])
  const quickPriorityOptions = useMemo(() => [
    { value: '', label: 'Any' },
    ...(quickMeta?.groups || []).flatMap((group) => group.stats.map((stat) => ({
      value: stat.key, label: stat.label, group: group.label,
    }))),
  ], [quickMeta])

  const emptyEras = meta && meta.eras
    .filter((e) => eras.includes(e.key) && !e.items).map((e) => e.label)
  const filterCount = mode === 'sets'
    ? [setSearch.trim(), !showAllSets].filter(Boolean).length
    : mode === 'quick' ? 0
      : [...order, cls, slot, armor, tier, kindParam, levelMin, levelMax,
        carries, proc, typed.trim()].filter(Boolean).length
  /* The catalog head is its live scope, not a promise that every result is an
     upgrade. Only active facets appear, in the same game-language the controls
     use; the untouched catalog needs one quiet, honest name. */
  const catalogScope = useMemo(() => {
    if (mode === 'sets') {
      const scope = []
      if (!showAllSets) scope.push('Fits my gear')
      if (setSearch.trim()) scope.push(`“${setSearch.trim()}”`)
      return scope.length ? scope : ['All Set Adornments']
    }
    if (mode === 'quick') {
      const scope = []
      if (quickClass) scope.push(quickClass[0].toUpperCase() + quickClass.slice(1))
      if (quickMaxLevel) scope.push(`Level ≤${quickMaxLevel}`)
      if (quickArmor.length && quickArmor.length < quickWearableArmor.length) {
        scope.push(quickArmor.join(' / '))
      }
      return scope.length ? scope : ['Build a complete loadout']
    }
    const scope = []
    if (slot) scope.push(slot)
    if (cls) scope.push(cls[0].toUpperCase() + cls.slice(1))
    if (armor) scope.push(`${armor[0].toUpperCase() + armor.slice(1)} armor`)
    if (tier) scope.push(TIER_LABEL(meta, tier))
    if (levelMin && levelMax) scope.push(`Level ${levelMin}–${levelMax}`)
    else if (levelMin) scope.push(`Level ${levelMin}+`)
    else if (levelMax) scope.push(`Level ≤${levelMax}`)
    kinds.forEach((kind) => scope.push(KIND_LABEL[kind] || kind))
    if (carries) scope.push('Set pieces')
    if (proc) scope.push('Has proc')
    if (typed.trim()) scope.push(`“${typed.trim()}”`)
    if (order.length) scope.push(`Priority: ${order.map(priorityLabel).join(' › ')}`)
    return scope.length ? scope : ['All equipment']
  }, [mode, setSearch, showAllSets, quickClass, quickMaxLevel, quickArmor,
    quickWearableArmor, slot, cls, armor, tier, meta, levelMin, levelMax,
    kinds, carries, proc, typed, order, priorityLabel])
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
    const planned = Object.fromEntries((effectiveShortlist.items || [])
      .map((item) => [item.page_title, item]))
    const current = Object.fromEntries((planningCharacter?.gear || [])
      .map((item) => [item.key, item]))
    const selectedPrimary = planned[(effectiveShortlist.active || {}).primary]
    return PLAN_SLOTS.flatMap((def) => {
      if (def.key === 'secondary' && selectedPrimary?.two_handed) return []
      const item = planned[(effectiveShortlist.active || {})[def.key]] || current[def.key]
      if (!item) return []
      if (!itemSockets(item).some((socket) => socket.color === 'turquoise')) return []
      const overridden = Object.prototype.hasOwnProperty.call(
        effectiveShortlist.set_slots || {}, def.key)
      const installed = overridden ? (effectiveShortlist.set_slots || {})[def.key]
        : item.set_name || (item.adornments || []).find(
          (adorn) => adorn.color === 'turquoise')?.set_name
        || (item.adornments || []).find(
          (adorn) => adorn.color === 'turquoise')?.name || null
      return [{
        key: def.key, label: def.label, catalog: def.catalog,
        item, level: item.level, installed,
      }]
    })
  }, [planningCharacter, effectiveShortlist])
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
        {!outlineOpen && trackedCount > 0 && (
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
            characterValue={characterParam.trim()
              ? planningOwner ? `recent:${planningOwner.key}` : ''
              : charId ? `account:${charId}` : ''}
            signedIn={!!user}
            onCharacter={(value) => {
              if (value.startsWith('recent:')) {
                const key = value.slice('recent:'.length)
                const row = recentCharacters.find((recent) => recent.key === key)
                if (row) lookUpCharacter(row.lookup_name || row.lookupName
                  || lookupName(row.name, row.world))
                return
              }
              const id = value.startsWith('account:')
                ? value.slice('account:'.length) : value
              invalidatePublicLookup()
              loadedLookup.current = ''
              setCharacterParam('')
              setLookedUp(null)
              setLookupBusy(false)
              setLookupErr(null)
              setCharId(id)
            }}
            onLookup={lookUpCharacter} lookupBusy={lookupBusy} lookupErr={lookupErr}
            shortlist={effectiveShortlist} adornmentSets={adornmentSets}
            whiteAdornments={whiteAdornments}
            active={effectiveShortlist.active || {}} focusSlot={focusSlot}
            onFocusSlot={focusEquipmentSlot} onCycle={cycleEquipmentSlot}
            onSetAdornment={setSlotAdornment} onWhiteAdornment={setWhiteAdornment}
            onRemoveItem={removeEquipmentItem} onToggleTrackedSet={toggleSet}
            onClearSetContents={clearSetContents}
            onReset={clearPlannedGear}
            savedSets={savedSets} activeSavedSetSlot={activeSavedSetSlot}
            savedSetBusy={savedSetBusy} savedSetStatus={savedSetStatus}
            savedSetWorkingStatus={savedSetWorkingStatus}
            savedSetDirty={savedSetDirty} savedSetModified={savedSetModified}
            completedTargets={completedTargets}
            onSaveSet={saveSavedSet} onRenameSet={renameSavedSet}
            onDeleteSet={deleteSavedSet} onLoadSet={loadSavedSet}
            onImportGuestSet={importGuestSavedSet}
            requestedCharacter={characterParam.trim()}
            statLabel={statLabel} statPct={statPct} />

        <div className="card planbar">
          {/* These are the Planner's three primary jobs, so they lead the
              search block as a compact tab set instead of reading like a
              small filter beneath the catalog title. */}
          <div className="planmodes" role="tablist" aria-label="Gear Planner feature">
            <span className="planmodetabs" role="presentation">
              <button className={mode === 'items' ? 'on' : ''} role="tab"
                      aria-selected={mode === 'items'} onClick={() => setMode('items')}>
                Equipment
              </button>
              <button className={mode === 'sets' ? 'on' : ''} role="tab"
                      aria-selected={mode === 'sets'} onClick={() => setMode('sets')}>
                Set Adorns
              </button>
              <button className={mode === 'quick' ? 'on' : ''} role="tab"
                      aria-selected={mode === 'quick'} onClick={() => setMode('quick')}>
                Quick Equip
              </button>
            </span>
          </div>

          <div className="plansearchhead">
            <div className="plansearchtitle">
              <span className="seclabel">{mode === 'quick'
                ? 'Whole-loadout builder'
                : mode === 'sets' ? 'Set adornment catalog' : 'Equipment catalog'}</span>
              <strong className="plansearchscope" aria-label="Current catalog filters">
                {catalogScope.map((part, index) => (
                  <span key={`${part}:${index}`} title={part}>{part}</span>
                ))}
              </strong>
            </div>
            {/* THE EXPANSION CHOICE LIVES IN THE SEARCH BLOCK (Lindsay), on its
                head rather than among the facets: it is not one narrowing
                among several — it is what the catalog IS, and both the item
                view and the set view are drawn from it. */}
            <EraFacet meta={meta} eras={eras} onToggle={toggleEra} />
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
                <div className="planbandrow searchbandrow">
                  {/* A NAME SEARCH IS A NAME, so the box is the size of one. */}
                  <input type="search" className="planq" value={typed}
                         aria-label="Search item names" placeholder="Item name…"
                         onChange={(e) => setTyped(e.target.value)} />
                  <button type="button" className="resetplanfilters"
                          disabled={filterCount === 0}
                          aria-label={filterCount
                            ? `Reset ${filterCount} active filter${filterCount === 1 ? '' : 's'}`
                            : 'No active filters to reset'}
                          onClick={resetCatalogFilters}>
                    Reset {filterCount} filter{filterCount === 1 ? '' : 's'}
                  </button>
                </div>

                <span className="planbandlabel prioritybandlabel">
                  <span>Stat priority</span>
                  <small>Rank up to 3 stats</small>
                </span>
                <div className="planbandrow prioritybandrow">
                  <span className="prioritysequence">
                    {Array.from({ length: PRIORITY_SLOTS }, (_, i) => (
                      <span key={i} className={`prioritypick${order[i] ? ' on' : ''}`}>
                        <i aria-hidden="true">{i + 1}</i>
                        <Picker value={order[i] || ''} options={priorityOptions}
                                label={`Priority ${i + 1}`} placeholder="Any"
                                filterFrom={99}
                                onChange={(v) => setPriority(i, v)} />
                      </span>
                    ))}
                  </span>
                </div>

                {/* CLASS IS A FILTER AND NOW SITS WITH THE FILTERS. It was
                    alone in the rail, which read as a page-wide setting — and
                    it is the one control most likely to be what emptied a
                    table (`EmptyTable` names it for exactly that reason). */}
                <span className="planbandlabel filterbandlabel">
                  <span>Filters</span>
                  <button type="button" className="currentfilter"
                          disabled={!planningCharacter?.character}
                          title="Use this character's class and level, plus or minus 10"
                          onClick={useCurrentCharacterFilters}>Current Class/Level</button>
                </span>
                <div className="planbandrow filterbandrow">
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
                  <span className="filterextras">
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
                    <button className={`chip filterflag${carries ? ' on' : ''}`}
                            title="Only items that ship with a set turquoise"
                            onClick={() => setCarries(carries ? '' : '1')}>
                      Set Pieces
                    </button>
                    <button className={`chip filterflag${proc ? ' on' : ''}`}
                            title="Only items with an effect that can fire"
                            onClick={() => setProc(proc ? '' : '1')}>
                      Has Proc
                    </button>
                  </span>
                </div>
              </div>

              <div className="plansearchfooter" aria-live="polite">
                <span>{data?.sampled
                  ? <><b>{data.items.length}</b> random items, just for fun</>
                  : data
                    ? <><b>{data.total}</b> matching item{data.total === 1 ? '' : 's'}</>
                    : 'Loading matching items…'}
                  {catalogUpdating && <i className="catalogupdating">Updating…</i>}
                </span>
                {!!order.length && (
                  <span>Scoring <b>{order.map(priorityLabel).join(' › ')}</b>
                    {ranked > 1 && <> — items carrying all {ranked} first</>}</span>
                )}
              </div>
            </>
          )}
          {mode === 'sets' && (
            <>
              <div className="plansetsearch">
                <div className="plansetsearchfield">
                  <span className="planbandlabel">Search sets</span>
                  <span className="plansetsearchrow">
                    <input type="search" className="planq" value={setSearch}
                           aria-label="Search set adornments"
                           placeholder="Set name or bonus…"
                           onChange={(e) => setSetSearch(e.target.value)} />
                    <button type="button" className="resetplanfilters"
                            disabled={filterCount === 0}
                            aria-label={filterCount
                              ? `Reset ${filterCount} active filter${filterCount === 1 ? '' : 's'}`
                              : 'No active filters to reset'}
                            onClick={resetCatalogFilters}>
                      Reset {filterCount} filter{filterCount === 1 ? '' : 's'}
                    </button>
                  </span>
                </div>
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
                  {hiddenSets ? ` · ${hiddenSets} more don't fit this loadout` : ''}
                  {catalogUpdating && <i className="catalogupdating">Updating…</i>}
                </span>
                <span>{setSocketTargets.length
                  ? 'Open a set to equip a piece or track where it drops'
                  : 'Load a character to see which sets fit its sockets'}</span>
              </div>
            </>
          )}
          {mode === 'quick' && (
            <>
              <div className="planbands quickequipbands">
                <span className="planbandlabel filterbandlabel">
                  <span>Build for</span>
                  <button type="button" className="currentfilter"
                          disabled={!planningCharacter?.character}
                          title="Restore this character's class and level"
                          onClick={useCurrentQuickCharacter}>Current Character</button>
                </span>
                <div className="planbandrow quickidentityrow">
                  <Facet name="Class" value={quickClass} onChange={setQuickClass}
                         options={meta?.classes}
                         format={(name) => name[0].toUpperCase() + name.slice(1)} />
                  <span className={`planfacet quicklevelfacet${quickMaxLevel ? ' selected' : ''}`}>
                    <span className="facetlab">Maximum Gear Level</span>
                    <span className="quicklevelcontrol">
                      <Picker value={quickMaxLevel || ''} options={quickLevelOptions}
                              label="Maximum gear level" placeholder="Choose"
                              filterFrom={8} filterHint="Maximum level…"
                              onChange={setQuickMaxLevel} />
                      <button type="button" className="currentfilter"
                              aria-pressed={String(quickMaxLevel) === String(meta?.level_max)}
                              disabled={!meta?.level_max}
                              title={`Use catalog maximum level${meta?.level_max ? ` ${meta.level_max}` : ''}`}
                              onClick={() => setQuickMaxLevel(String(meta.level_max))}>
                        Max Lvl
                      </button>
                    </span>
                  </span>
                </div>

                <span className="planbandlabel prioritybandlabel">
                  <span>Stat priorities</span>
                  <small>Targeted order · up to {quickMeta?.max_priorities || QUICK_PRIORITY_SLOTS}</small>
                  <label className={`quickcritrule${quickRequired.includes('crit') ? ' on' : ''}`}>
                    <input type="checkbox" checked={quickRequired.includes('crit')}
                           onChange={() => toggleQuickRequired('crit')} />
                    <span>Require Crit Chance</span>
                  </label>
                </span>
                <div className="planbandrow quickpriorityrow">
                  {Array.from({ length: quickMeta?.max_priorities || QUICK_PRIORITY_SLOTS }, (_, i) => {
                    const stat = quickOrder[i] || ''
                    return (
                      <span key={i} className={`quickprioritypick${stat ? ' on' : ''}`}>
                        <span className="quickprioritytop">
                          <span className={`prioritypick${stat ? ' on' : ''}`}>
                            <i aria-hidden="true">{i + 1}</i>
                            <Picker value={stat} options={quickPriorityOptions}
                                    label={`Quick Equip priority ${i + 1}`} placeholder="Any"
                                    filterFrom={10}
                                    onChange={(value) => setQuickPriority(i, value)} />
                          </span>
                          {stat === 'crit' ? (
                            <span className={`quickrequired quickrequiredlinked${quickRequired.includes('crit') ? ' on' : ''}`}>
                              Crit ↙
                            </span>
                          ) : (
                            <label className={`quickrequired${quickRequired.includes(stat) ? ' on' : ''}`}>
                              <input type="checkbox" disabled={!stat}
                                     checked={!!stat && quickRequired.includes(stat)}
                                     onChange={() => toggleQuickRequired(stat)} />
                              Req.
                            </label>
                          )}
                        </span>
                        <QuickTarget stat={stat} range={quickRanges?.ranges?.[stat]}
                                     value={quickTargets[stat]} busy={quickRangesBusy}
                                     pct={!!statPct[stat]}
                                     onChange={(value) => setQuickTargets((current) => ({
                                       ...current, [stat]: value,
                                     }))} />
                      </span>
                    )
                  })}
                </div>

                <span className="planbandlabel prioritybandlabel">
                  <span>Armor</span>
                  <small>Only weights you want considered</small>
                </span>
                <QuickChecks values={quickArmor} options={quickWearableArmor}
                             label="Allowed armor types" onToggle={toggleQuickArmor}
                             format={(name) => name} />

                <span className="planbandlabel prioritybandlabel">
                  <span>Sources</span>
                  <small>Uncheck Raid to exclude raid gear</small>
                </span>
                <QuickChecks values={quickKinds} options={quickMeta?.source_kinds || []}
                             label="Allowed gear sources" onToggle={toggleQuickKind}
                             format={(kind) => KIND_LABEL[kind] || kind} />
              </div>
              <div className="plansearchfooter quickequipfooter" aria-live="polite">
                <span>{quickErr || quickRangeErr
                  ? <b className="quickequiperror">{quickErr || quickRangeErr}</b>
                  : quickRangesBusy
                    ? 'Calculating achievable full-loadout ranges…'
                    : 'Each priority is valued to its target, then the next priority takes over.'}</span>
                <button type="button" className="chip on quickequiprun"
                        disabled={quickBusy || quickRangesBusy || !quickRanges
                          || !quickClass || !quickMaxLevel || !quickOrder.length}
                        onClick={runQuickEquip}>
                  {quickBusy ? 'Building…' : quickResult ? 'Build Again' : 'Build Loadout'}
                </button>
              </div>
            </>
          )}
        </div>

        {planNotice && <p className="err" role="status">{planNotice}</p>}

        {mode !== 'quick' && err && <p className="err">{err}</p>}
        {!!emptyEras?.length && (
          <p className="muted">
            {emptyEras.join(' and ')} {emptyEras.length > 1 ? 'have' : 'has'} no
            catalog yet — run <code>backend/tools/sync_planner.py</code> for it.
          </p>
        )}

        {mode !== 'quick' && !data && !err && <p className="muted">Loading…</p>}

        {mode === 'quick' && quickResult && (
          <QuickEquipResults result={quickResult} statLabel={statLabel}
                             statPct={statPct} savedSets={savedSets}
                             canCopy={!!planningOwner} busy={savedSetBusy}
                             onCopy={copyQuickToSavedSet} />
        )}

        {data && mode === 'sets' && (
          <SetList sets={shownSets} inList={setsInList} canPlan={!!planningOwner}
                   targets={setSocketTargets} onToggle={toggleSet}
                   onEquipAdornment={setSlotAdornment} eraLabel={eraLabel}
                   statLabel={statLabel} statPct={statPct} />
        )}

        {data && mode === 'items' && (
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
        {outlineOpen && trackedCount > 0 && (
          <section className="planneroutline">
            <header className="planneroutlinehead">
              <h2>Outline{planningOwner?.name && <small>{planningOwner.name}</small>}</h2>
              <button type="button" className="iconbtn" aria-label="Collapse outline"
                      title="Collapse outline" onClick={() => setOutlineOpen(false)}>›</button>
            </header>
            <Shortlist list={effectiveShortlist} completed={completedTargets}
              onDropItem={toggleItem} onDropSet={toggleSet} />
            {outlineErr && <p className="err">{outlineErr}</p>}
            {planCount > 0 && !outlineData && !outlineErr
              && <p className="muted">Building outline…</p>}
            {planCount > 0 && outlineData && <PlanOutline key={planningOwner?.key}
              data={outlineData} ownerKey={planningOwner?.key}
              items={effectiveShortlist.items} />}
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

function QuickTarget({ stat, range, value, busy, pct, onChange }) {
  const lower = targetFloor(range)
  const upper = targetCeiling(range)
  const disabled = !stat || !range || lower === upper
  const shown = range && value != null ? value : upper
  const suffix = pct ? '%' : ''
  return (
    <label className={`quicktarget${stat && range ? ' ready' : ''}`}
           title={range ? `Calculated gear range: ${precise(range.min)}${suffix}–${precise(range.max)}${suffix}` : ''}>
      <span><b>Target</b><output>{shown == null ? '—' : `${precise(shown)}${suffix}`}</output></span>
      <input type="range" disabled={disabled}
             min={lower} max={upper} step={range?.step ?? 1}
             value={shown ?? 0} aria-label={`${stat || 'Stat'} target`}
             onChange={(event) => onChange(Number(event.target.value))} />
      <small>
        <i>{range ? `${precise(lower)}${suffix}` : busy && stat ? '…' : 'Min'}</i>
        <i>{range ? `${precise(upper)}${suffix}` : 'Max'}</i>
      </small>
    </label>
  )
}

function QuickChecks({ values, options, label, onToggle, format }) {
  return (
    <div className="planbandrow quickcheckrow" role="group" aria-label={label}>
      {(options || []).map((option) => {
        const checked = values.includes(option)
        return (
          <label key={option} className={checked ? 'on' : ''}>
            <input type="checkbox" checked={checked}
                   onChange={() => onToggle(option)} />
            <span>{format ? format(option) : option}</span>
          </label>
        )
      })}
    </div>
  )
}

function QuickEquipResults({ result, statLabel, statPct, savedSets,
                             canCopy, busy, onCopy }) {
  const initial = useCallback(() => Object.fromEntries(
    (result?.slots || []).map((slot) => [slot.key, slot.selected])), [result])
  const [selected, setSelected] = useState(initial)
  useEffect(() => setSelected(initial()), [result, initial])

  const selectedFor = useCallback((slot) => {
    const index = selected[slot.key]
    return index == null ? null : slot.options[index] || null
  }, [selected])
  const primary = selectedFor((result.slots || []).find((slot) => slot.key === 'primary') || {})
  const twoHanded = !!primary?.two_handed
  const chosen = useMemo(() => (result.slots || []).flatMap((slot) => {
    if (slot.key === 'secondary' && twoHanded) return []
    const index = selected[slot.key]
    const item = index == null ? null : slot.options[index]
    return item ? [{ ...item, equip_slot: slot.key }] : []
  }), [result, selected, twoHanded])
  const totals = useMemo(() => Object.fromEntries((result.criteria.order || []).map((key) => [
    key,
    Math.round(chosen.reduce((sum, item) => sum + Number(item.stats?.[key] || 0), 0) * 100) / 100,
  ])), [chosen, result.criteria.order])
  const filledPositions = chosen.length + (twoHanded ? 1 : 0)

  const cycle = (slot, direction) => {
    if (slot.options.length < 2) return
    setSelected((current) => {
      const at = current[slot.key] == null ? 0 : current[slot.key]
      const nextIndex = (at + direction + slot.options.length) % slot.options.length
      const next = { ...current, [slot.key]: nextIndex }
      if (slot.key === 'primary' && !slot.options[nextIndex]?.two_handed
          && next.secondary == null) next.secondary = 0
      return next
    })
  }

  const slotRow = (slot) => {
    const occupied = slot.key === 'secondary' && twoHanded
    const item = occupied ? null : selectedFor(slot)
    const index = selected[slot.key]
    const hasOptions = slot.options.length > 1 && !occupied
    const statLine = item ? (result.criteria.order || []).flatMap((key) => (
      item.stats?.[key] ? [`${statLabel[key] || key} ${precise(item.stats[key])}${statPct[key] ? '%' : ''}`] : []
    )).join(' · ') : ''
    return (
      <div key={slot.key}
           className={`quickgearslot${occupied ? ' occupied' : ''}${hasOptions ? ' hasoptions' : ''}`}>
        <span className="quickgearicon">
          {item?.card ? (
            <Hover className="examinecard" width={350} card={<Examine row={item.card} />}>
              <span tabIndex="0" aria-label={`Examine ${item.name}`}>
                {item.card.icon
                  ? <img src={item.card.icon} alt="" />
                  : <i aria-hidden="true">◆</i>}
              </span>
            </Hover>
          ) : <i aria-hidden="true">{occupied ? '—' : '◆'}</i>}
        </span>
        <span className="quickgearcopy">
          <b>{slot.label}</b>
          {item?.card ? (
            <Hover className="examinecard" width={350} card={<Examine row={item.card} />}>
              <span tabIndex="0" className={rarityClass(item.tier)}>{item.name}</span>
            </Hover>
          ) : <span>{occupied ? 'Occupied by two-handed weapon' : 'No match'}</span>}
          {item && <small>Level {item.level} · {statLine || 'Priority tie'}</small>}
        </span>
        {hasOptions && (
          <span className="quickgearcycle" role="group"
                aria-label={`${slot.label} gear options`}>
            <small>Gear options</small>
            <span>
              <button type="button" aria-label={`Previous ${slot.label} option`}
                      title={`Previous ${slot.label} option`}
                      onClick={() => cycle(slot, -1)}>‹</button>
              <b>{(index ?? 0) + 1} of {slot.options.length}</b>
              <button type="button" aria-label={`Next ${slot.label} option`}
                      title={`Next ${slot.label} option`}
                      onClick={() => cycle(slot, 1)}>›</button>
            </span>
          </span>
        )}
      </div>
    )
  }

  const left = result.slots.filter((slot) => slot.side === 'left')
  const right = result.slots.filter((slot) => slot.side === 'right')
  return (
    <section className="card quickequipresults">
      <header className="quickequipresultshead">
        <div>
          <span className="seclabel">Generated gear set</span>
          <h2>{filledPositions} of {result.slots.length} slots filled</h2>
          <p>{result.candidates} eligible items evaluated. Use the Gear options controls
            to compare up to three choices in each slot.</p>
        </div>
        <QuickCopyControls sets={savedSets} items={chosen} canCopy={canCopy}
                           busy={busy} onCopy={onCopy} />
      </header>
      <div className="quicktotals" aria-label="Generated priority totals">
        {(result.criteria.order || []).map((key, index) => {
          const target = Number(result.criteria.targets?.[key] ?? result.ranges?.[key]?.max ?? 0)
          const total = Number(totals[key] || 0)
          const met = total + 0.001 >= target
          const achievable = Number(result.ranges?.[key]?.max ?? target)
          const atCeiling = !met && target > achievable && total + 0.001 >= achievable
          const suffix = statPct[key] ? '%' : ''
          const required = result.criteria.required.includes(key)
          return (
            <span key={key} className={met ? 'met' : atCeiling ? 'ceiling' : 'short'}>
              <i>{index + 1}</i><b>{statLabel[key] || key}</b>
              <strong>{precise(total)}{suffix}<small> / {precise(target)}{suffix}</small></strong>
              <em>{met ? 'Target met' : atCeiling ? 'Best gear ceiling'
                : `${precise(target - total)}${suffix} short`}
                {required ? ' · Required' : ''}</em>
            </span>
          )
        })}
      </div>
      <div className="quickgearwindow">
        <div>{left.map(slotRow)}</div>
        <div>{right.map(slotRow)}</div>
      </div>
      {!!result.missing.length && (
        <p className="quickequipwarning">
          No qualifying item for: <b>{result.missing.join(', ')}</b>. Try another source,
          armor restriction, required stat, or maximum level.
        </p>
      )}
    </section>
  )
}

function QuickCopyControls({ sets, items, canCopy, busy, onCopy }) {
  const available = firstAvailableSavedSet(sets)
  const used = (sets || []).filter(savedSetInUse)
  const [panel, setPanel] = useState(null)
  const [draft, setDraft] = useState('')
  const [target, setTarget] = useState(null)
  const [copying, setCopying] = useState(false)
  const [done, setDone] = useState('')
  const root = useRef(null)

  useEffect(() => {
    if (!panel) return undefined
    const away = (event) => { if (!root.current?.contains(event.target)) setPanel(null) }
    const escape = (event) => { if (event.key === 'Escape') setPanel(null) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [panel])

  const finish = async (slot, name) => {
    setCopying(true)
    await onCopy(slot, name, items)
    setCopying(false)
    setPanel(null)
    setDone(`Copied to ${name}`)
  }

  const openNew = () => {
    if (!available) return
    setDraft(available.name)
    setPanel('new')
  }

  return (
    <div className="quickcopy" ref={root}>
      <span className="plansavedanchor quickcopyanchor">
        <button type="button" className="chip on quickcopybutton"
                disabled={!canCopy || !items.length || busy || copying}
                title={canCopy ? 'Save this generated gear-only loadout'
                  : 'Load a character before copying to a Gear Set'}
                onClick={() => setPanel(panel ? null : 'choose')}>
          {copying ? 'Copying…' : 'Copy to Gear Set'}
        </button>
        {panel === 'choose' && (
          <div className="plansavedpopover quickcopypopover" role="dialog"
               aria-label="Copy Quick Equip result">
            <b>Copy to Gear Set</b>
            <p>Create a set, or replace one of this character's saved sets.</p>
            <div className="quickcopychoices">
              {available && <button type="button" className="chip on"
                                    onClick={openNew}>Create new set</button>}
              {used.map((row) => (
                <button key={row.slot} type="button" className="chip"
                        onClick={() => { setTarget(row); setPanel('confirm') }}>
                  Overwrite {row.name}
                </button>
              ))}
              {!available && !used.length && <span className="muted">No Gear Set slots available.</span>}
              <button type="button" className="btnlink" onClick={() => setPanel(null)}>Cancel</button>
            </div>
          </div>
        )}
        {panel === 'new' && available && (
          <div className="plansavedpopover quickcopypopover" role="dialog"
               aria-label="New Quick Equip gear set">
            <label><span>Name this set</span>
              <input value={draft} maxLength={40} autoFocus
                     onFocus={(event) => event.target.select()}
                     onChange={(event) => setDraft(event.target.value)}
                     onKeyDown={(event) => {
                       if (event.key === 'Enter') finish(
                         available.slot, draft.trim() || available.name)
                     }} />
            </label>
            <div className="plansavedpopoveractions">
              <button type="button" className="chip on" disabled={copying}
                      onClick={() => finish(available.slot, draft.trim() || available.name)}>
                Create set
              </button>
              <button type="button" className="btnlink" onClick={() => setPanel('choose')}>Back</button>
            </div>
          </div>
        )}
        {panel === 'confirm' && target && (
          <div className="plansavedpopover quickcopypopover" role="alertdialog"
               aria-label={`Overwrite ${target.name}`}>
            <b>Overwrite {target.name}?</b>
            <p>The saved copy will be replaced. Your current working loadout stays open.</p>
            <div className="plansavedpopoveractions">
              <button type="button" className="chip on" disabled={copying}
                      onClick={() => finish(target.slot, target.name)}>Overwrite</button>
              <button type="button" className="btnlink" onClick={() => setPanel('choose')}>Cancel</button>
            </div>
          </div>
        )}
      </span>
      {!canCopy && <small>Load a character to save this set.</small>}
      {done && <small className="quickcopydone">{done}</small>}
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
      <Picker className="sourcepicker" value={kinds} onChange={onToggle}
              options={shown.map((kind) => ({
                value: kind, label: KIND_LABEL[kind] || kind,
                icon: <input type="checkbox" tabIndex="-1" aria-hidden="true"
                             readOnly checked={kinds.includes(kind)} />,
              }))}
              label="Source" placeholder="Any" multiple
              buttonLabel={kinds.length ? `${kinds.length} selected` : 'Any'}
              filterFrom={Number.MAX_SAFE_INTEGER} maxMenuWidth={220}
              menuClassName="sourcefacetmenu" />
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

function itemColumns({ order, statLabel, statPct,
                       character, shortlist, focusSlot }) {
  const shown = order.slice(0, 4)
  const stat = (key) => ({
    key,
    label: TABLE_STAT_LABEL[key] || statLabel[key] || key,
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
         the third choice was made to find the three-stat one. The footer says
         that tiering once; repeating it beside every score adds noise. */
      render: (r) => (r.score ? r.score.toFixed(1) : <span className="muted">—</span>),
      sortValue: (r) => (r.matched || 0) * 1000 + (r.score || 0),
    },
    { key: 'level', label: 'Lvl', sortValue: (r) => r.level || 0 },
    {
      key: 'tier', label: 'Tier', align: 'l',
      render: (r) => <span className={rarityClass(r.tier)}>{(r.tier || '').toUpperCase()}</span>,
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
      key: 'armor', label: 'Armor', align: 'l',
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

function ItemComparison({ cards, characterClass, tradeskillClass }) {
  return (
    <div className="planitemcompare">
      {cards.map(({ label, card }) => (
        <section key={`${label}-${card.name}`}>
          <div className="plancomparelabel">{label}</div>
          <Examine row={card} characterClass={characterClass}
                   tradeskillClass={tradeskillClass} />
        </section>
      ))}
    </div>
  )
}

function ItemName({ row, character, shortlist, focusSlot }) {
  const label = <span className={rarityClass(row.tier)}>{row.name}</span>
  const cards = comparisonCards(row, character, shortlist, focusSlot)
  const characterClass = character?.character?.class || null
  const tradeskillClass = character?.character?.ts_class || null
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
             card={<ItemComparison cards={cards} characterClass={characterClass}
                                   tradeskillClass={tradeskillClass} />}>
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
    <span className={`plansource${first.kind === 'quest' ? ' quest' : ''}`} title={row.sources
      .map((s) => `${KIND_LABEL[s.kind]}: ${s.source}`
        // A world drop's source IS its zone, so the parenthetical would repeat
        // the name it just printed.
        + (s.zone && s.zone !== s.source ? ` (${s.zone})` : '')
        + (s.detail ? ` — ${s.detail}` : ''))
      .join('\n')}>
      <i className={`skind ${first.kind}`}>{KIND_LABEL[first.kind]}</i>
      <span className="plansourcename">{first.source}</span>
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

function Shortlist({ list, completed = [], onDropItem, onDropSet }) {
  const total = list.items.length + list.sets.length
  return (
    <>
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
      {!!completed.length && (
        <details className="shortlist completedlist">
          <summary>
            <span>Completed</span>
            <small>{completed.length}</small>
          </summary>
          <div className="shortloaded">
            {completed.map((row) => (
              <div className="shortrow completed" key={row.key}>
                <span className="shortitemidentity">
                  <span className="shortrowicon" aria-hidden="true">✓</span>
                  <span className="shortrowname">
                    <small>{row.kind === 'item' ? 'Item' : 'Set piece'}</small>
                    {row.name}
                  </span>
                </span>
                <em>{row.first_seen_ts
                  ? new Date(row.first_seen_ts * 1000).toLocaleDateString()
                  : 'Equipped'}</em>
                <button className="iconbtn" aria-label={`Remove ${row.name}`}
                        onClick={() => (row.kind === 'item'
                          ? onDropItem(row.target) : onDropSet(row.target))}>✕</button>
              </div>
            ))}
          </div>
        </details>
      )}
    </>
  )
}
