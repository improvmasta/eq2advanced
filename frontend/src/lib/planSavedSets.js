import { snapshotAdornmentState } from './planAdornments.js'

export const defaultSavedSetName = (slot) => `Set ${slot}`

export function savedSetInUse(row) {
  return Boolean(row?.payload)
}

export function firstAvailableSavedSet(rows) {
  return (rows || []).find((row) => !savedSetInUse(row)) || null
}

export function savedSetOwnerName(row) {
  return row?.payload?.shortlist?.owner?.name || ''
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
export function savedSetSnapshot(shortlist, gear = []) {
  const adornments = snapshotAdornmentState(shortlist, gear)
  return {
    version: 2,
    shortlist: {
      owner: shortlist?.owner || null,
      items: [...(shortlist?.items || [])],
      sets: [...(shortlist?.sets || [])],
      active: { ...(shortlist?.active || {}) },
      set_slots: adornments.setSlots,
      adorn_slots: adornments.adornSlots,
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
