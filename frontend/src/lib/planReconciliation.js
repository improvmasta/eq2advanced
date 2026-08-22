import { setPieceForSlot } from './planAdornments.js'

const exactName = (value) => String(value || '').normalize('NFKC')
  .trim().replace(/\s+/g, ' ').toLowerCase()

const itemKey = (id, name) => {
  const numeric = String(id ?? '').trim()
  return numeric && /^[1-9][0-9]*$/.test(numeric)
    ? `census:${numeric}` : name ? `name:${exactName(name)}` : ''
}

export function observedPlannerItems(summary) {
  const provider = String(summary?.character?.source || 'census')
    .toLowerCase().replace(/[^a-z0-9_-]/g, '') || 'census'
  const rows = []
  ;(summary?.gear || []).forEach((gear) => {
    const key = itemKey(gear.item_id, gear.name)
    if (key && gear.name) rows.push({
      item_key: key, item_name: gear.name, source: `equipped:gear:${provider}`,
    })
    ;(gear.adornments || []).forEach((adorn) => {
      const adornKey = itemKey(adorn.id, adorn.name)
      if (adornKey && adorn.name) rows.push({
        item_key: adornKey, item_name: adorn.name,
        source: `equipped:adornment:${provider}`,
      })
    })
  })
  return [...new Map(rows.map((row) => [row.item_key, row])).values()]
}

export function mergeObtainedItems(...groups) {
  const byKey = new Map()
  groups.flat().filter((row) => row?.item_key).forEach((row) => {
    const previous = byKey.get(row.item_key)
    if (!previous) { byKey.set(row.item_key, { ...row }); return }
    const newer = Number(row.last_seen_ts || 0) >= Number(previous.last_seen_ts || 0)
      ? { ...previous, ...row } : { ...row, ...previous }
    newer.first_seen_ts = Math.min(
      Number(previous.first_seen_ts || Number.MAX_SAFE_INTEGER),
      Number(row.first_seen_ts || Number.MAX_SAFE_INTEGER),
    )
    newer.last_seen_ts = Math.max(
      Number(previous.last_seen_ts || 0), Number(row.last_seen_ts || 0))
    byKey.set(row.item_key, newer)
  })
  return [...byKey.values()].sort((a, b) => (
    Number(a.first_seen_ts || 0) - Number(b.first_seen_ts || 0)
    || a.item_name.localeCompare(b.item_name)
  ))
}

export function reconcileLocalObtained(existing, observations, now = Math.floor(Date.now() / 1000)) {
  const have = new Set((existing || []).map((row) => row.item_key))
  const observed = (observations || []).map((row) => ({
    ...row, first_seen_ts: have.has(row.item_key) ? undefined : now,
    last_seen_ts: now,
  }))
  const items = mergeObtainedItems(existing, observed)
  return { items, added: observed.filter((row) => !have.has(row.item_key))
    .map((row) => row.item_key) }
}

function observationFor(target, kind, obtained) {
  const id = kind === 'item'
    ? target.census_id : target.adornment_id || target.census_id
  const exactKey = id ? itemKey(id, target.name) : ''
  if (exactKey) return obtained.find((row) => row.item_key === exactKey) || null
  const wanted = exactName(target.name)
  const sourceKind = kind === 'item' ? ':gear:' : ':adornment:'
  return obtained.find((row) => exactName(row.item_name) === wanted
    && String(row.source || '').includes(sourceKind)) || null
}

const CATALOG_SLOT = {
  activate1: 'Charm', activate2: 'Charm', cloak: 'Cloak', head: 'Head',
  shoulders: 'Shoulders', chest: 'Chest', forearms: 'Forearms', hands: 'Hands',
  legs: 'Legs', feet: 'Feet', primary: 'Primary', secondary: 'Secondary',
  ears: 'Ear', ears2: 'Ear', neck: 'Neck', left_ring: 'Finger',
  right_ring: 'Finger', left_wrist: 'Wrist', right_wrist: 'Wrist', waist: 'Waist',
  ranged: 'Ranged', ammo: 'Ammo', event_slot: 'Event',
}

function setSlotStillNeeded(slot, setName, completed) {
  if (!setName) return true
  const catalog = CATALOG_SLOT[slot]
  return !catalog || !completed.some((row) => row.kind === 'set'
    && row.target?.set_name === setName
    && setPieceForSlot(row.target, catalog) === row.name)
}

/* Saved/working targets stay immutable. This selector supplies the floating
   equipment window and Outline with only what is still needed. */
export function reconcilePlanTargets(shortlist, obtained) {
  const completed = []
  const remainingItems = []
  const remainingSets = []
  const donePages = new Set()
  ;(shortlist?.items || []).forEach((target) => {
    const observation = observationFor(target, 'item', obtained || [])
    if (!observation) { remainingItems.push(target); return }
    donePages.add(target.page_title)
    completed.push({
      key: `item:${target.page_title}`, kind: 'item', name: target.name,
      planned_slot: target.equip_slot, first_seen_ts: observation.first_seen_ts,
      source: observation.source, target,
    })
  })
  ;(shortlist?.sets || []).forEach((target) => {
    /* Legacy saves could track a whole set. Treat that as its bounded exact
       piece list so one observed turquoise disappears while the other pieces
       and complete bonus ladder remain. The saved broad target itself stays
       immutable and is the explicit-removal handle. */
    const broad = target.name === target.set_name
    const targets = broad && target.pieces?.length
      ? target.pieces.map((name) => ({
        ...target, name, slot_label: name.split(':').pop()?.trim() || target.slot_label,
        tracked_name: target.name,
      })) : [target]
    targets.forEach((piece) => {
      const observation = broad && targets.length === 1
        ? null : observationFor(piece, 'set', obtained || [])
      if (!observation) { remainingSets.push(piece); return }
      completed.push({
        key: `set:${piece.name}`, kind: 'set', name: piece.name,
        planned_slot: piece.slot_label, first_seen_ts: observation.first_seen_ts,
        source: observation.source, target,
      })
    })
  })
  const active = Object.fromEntries(Object.entries(shortlist?.active || {})
    .filter(([, page]) => !donePages.has(page)
      && remainingItems.some((item) => item.page_title === page)))
  /* A completed turquoise target must also stop forcing that piece into the
     projected socket. Clear every compatible physical position (either ring,
     ear, wrist, etc.) while retaining other pieces and the set's bonus data. */
  const setSlots = Object.fromEntries(Object.entries(shortlist?.set_slots || {})
    .filter(([slot, setName]) => setSlotStillNeeded(slot, setName, completed)))
  return {
    remaining: {
      ...(shortlist || {}), items: remainingItems, sets: remainingSets,
      active, set_slots: setSlots,
    },
    completed: completed.sort((a, b) => Number(a.first_seen_ts || 0)
      - Number(b.first_seen_ts || 0) || a.name.localeCompare(b.name)),
  }
}
