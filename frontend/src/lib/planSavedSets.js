export const defaultSavedSetName = (slot) => `Set ${slot}`

export function savedSetInUse(row) {
  return Boolean(row?.payload)
}

export function firstAvailableSavedSet(rows) {
  return (rows || []).find((row) => !savedSetInUse(row)) || null
}

/* One Save control owns every destination. Loading stays in the picker; this
   list makes updating, replacing, and creating visibly different choices. */
export function savedSetSaveDestinations(rows, activeSlot) {
  const used = (rows || []).filter(savedSetInUse)
  const current = used.find((row) => Number(row.slot) === Number(activeSlot))
  const destinations = [
    ...(current ? [{ kind: 'update', row: current }] : []),
    ...used.filter((row) => row !== current)
      .map((row) => ({ kind: 'replace', row })),
  ]
  const available = firstAvailableSavedSet(rows)
  if (available) destinations.push({ kind: 'new', row: available })
  return destinations
}

export function savedSetOwnerName(row) {
  return row?.payload?.shortlist?.owner?.display_name
    || row?.payload?.shortlist?.owner?.name || ''
}

export function hasPlannedEquipment(shortlist) {
  return Boolean(
    (shortlist?.items || []).length
    || (shortlist?.sets || []).length
    || Object.keys(shortlist?.active || {}).length
    || Object.keys(shortlist?.set_slots || {}).length
    || Object.keys(shortlist?.adorn_slots || {}).length
  )
}

/* THE SET AND ITS OUTLINE ARE ONE SAVED UNIT. Keep every shortlisted gear row
   and tracked set-adornment goal, not only the item currently shown in each
   equipment slot. Active choices say what the loadout wears; the complete
   lists say what its linked Outline contains. */
export function savedSetSnapshot(shortlist) {
  /* Socket maps already mean explicit working choices. Do not materialize
     untouched equipped sockets here: those belong to the floating Census
     baseline and freezing them would turn a target plan back into a snapshot. */
  return {
    version: 3,
    shortlist: {
      owner: shortlist?.owner || null,
      items: [...(shortlist?.items || [])],
      sets: [...(shortlist?.sets || [])],
      active: { ...(shortlist?.active || {}) },
      set_slots: { ...(shortlist?.set_slots || {}) },
      adorn_slots: { ...(shortlist?.adorn_slots || {}) },
    },
  }
}

/* Object insertion order is not part of a loadout. A socket removed and then
   restored can put the same keys back in a different order; comparing raw
   JSON would call that clean loadout dirty forever. Arrays keep their order,
   while object keys are canonicalized at every level. */
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.keys(value).sort()
    .map((key) => [key, canonical(value[key])]))
}

export function savedSetPayloadEqual(left, right) {
  return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right))
}

export function setReplacementNeedsConfirmation({
  targetSlot, activeSlot, activeDirty, hasPlanned,
}) {
  if (Number(targetSlot) === Number(activeSlot)) return false
  return Boolean(activeDirty || (!activeSlot && hasPlanned))
}

export const defaultWorkspaceBase = () => ({
  kind: 'equipped', slot: null, saved_updated_ts: 0,
})

export function workspaceSnapshot(owner, base, shortlist) {
  return {
    version: 3,
    owner: owner ? {
      key: owner.key,
      lookup_name: owner.lookup_name || owner.lookupName,
      display_name: owner.display_name || owner.name,
      className: owner.className,
      world: owner.world,
    } : null,
    base: { ...defaultWorkspaceBase(), ...(base || {}) },
    shortlist,
  }
}

export function restoredWorkspace(raw, owner, normalizeShortlist) {
  const isWorkspace = raw?.version === 3 && raw?.shortlist
  const shortlist = normalizeShortlist(isWorkspace ? raw.shortlist : raw)
  const bound = { ...shortlist, owner }
  if (!isWorkspace) {
    return workspaceSnapshot(owner, { kind: hasPlannedEquipment(bound)
      ? 'draft' : 'equipped' }, bound)
  }
  return workspaceSnapshot(owner, raw.base, bound)
}

export function validateWorkspaceBase(workspace, rows, currentPayload) {
  const base = workspace?.base || defaultWorkspaceBase()
  if (base.kind !== 'saved' || !base.slot) return base
  const row = (rows || []).find((candidate) => Number(candidate.slot) === Number(base.slot))
  const exact = row?.payload && savedSetPayloadEqual(row.payload, currentPayload)
  const timestampMatches = !base.saved_updated_ts
    || Number(base.saved_updated_ts) === Number(row?.updated_ts || 0)
  if (exact && timestampMatches) return {
    kind: 'saved', slot: row.slot, saved_updated_ts: row.updated_ts || 0,
  }
  return { kind: 'draft', slot: null, saved_updated_ts: 0 }
}

export function workspaceStatus(base, selected, dirty, modified) {
  if (base?.kind === 'saved' && selected?.payload) {
    return dirty ? `${selected.name} - changes not saved` : selected.name
  }
  return modified || base?.kind === 'draft' ? 'Draft restored' : 'Equipped'
}
