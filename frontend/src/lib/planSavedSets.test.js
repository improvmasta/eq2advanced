import assert from 'node:assert/strict'
import test from 'node:test'

import {
  firstAvailableSavedSet,
  hasPlannedEquipment,
  savedSetInUse,
  savedSetOwnerName,
  savedSetPayloadEqual,
  savedSetSnapshot,
  setReplacementNeedsConfirmation,
} from './planSavedSets.js'

test('only committed builds consume one of the five slots', () => {
  const rows = [
    { slot: 1, name: 'Raid', payload: { version: 1 } },
    { slot: 2, name: 'Set 2', payload: null },
    { slot: 3, name: 'Tradeskill', payload: null },
  ]
  assert.equal(savedSetInUse(rows[0]), true)
  assert.equal(savedSetInUse(rows[1]), false)
  assert.equal(savedSetInUse(rows[2]), false)
  assert.equal(firstAvailableSavedSet(rows)?.slot, 2)
})

test('owner is read from the saved payload without inventing one', () => {
  assert.equal(savedSetOwnerName({
    payload: { shortlist: { owner: { name: 'Bobby' } } },
  }), 'Bobby')
  assert.equal(savedSetOwnerName({ payload: null }), '')
})

test('dirty comparison ignores object insertion order', () => {
  const left = { shortlist: { active: { primary: 'A', head: 'B' }, items: ['A', 'B'] } }
  const right = { shortlist: { items: ['A', 'B'], active: { head: 'B', primary: 'A' } } }
  assert.equal(savedSetPayloadEqual(left, right), true)
  assert.equal(savedSetPayloadEqual(left, {
    shortlist: { items: ['B', 'A'], active: { head: 'B', primary: 'A' } },
  }), false)
})

test('planned adornment removal still counts as modified equipment', () => {
  assert.equal(hasPlannedEquipment({ active: {}, set_slots: { head: null } }), true)
  assert.equal(hasPlannedEquipment({ active: {}, set_slots: {}, adorn_slots: {} }), false)
  assert.equal(hasPlannedEquipment({ items: [{ page_title: 'Hat' }], sets: [] }), true)
  assert.equal(hasPlannedEquipment({ items: [], sets: [{ name: 'Focused Mind' }] }), true)
})

test('a saved set keeps its full linked Outline and adornment state', () => {
  const active = { page_title: 'Raid Crown', equip_slot: 'head',
    card: { stats: { adornments: ['white', 'turquoise'] } } }
  const alternate = { page_title: 'Quest Crown', equip_slot: 'head' }
  const trackedSet = { name: 'Focused Mind Set' }
  const payload = savedSetSnapshot({
    owner: { key: 'wuoshi:123', name: 'Bobby' },
    items: [active, alternate], sets: [trackedSet],
    active: { head: active.page_title },
    set_slots: { head: 'Focused Mind Set' },
    adorn_slots: { head: { 0: null } },
  }, [{ key: 'head', page_title: active.page_title }])

  assert.equal(payload.version, 2)
  assert.deepEqual(payload.shortlist.items, [active, alternate])
  assert.deepEqual(payload.shortlist.sets, [trackedSet])
  assert.equal(payload.shortlist.set_slots.head, 'Focused Mind Set')
  assert.equal(payload.shortlist.adorn_slots.head[0], null)
})

test('replacement guard protects active and unsaved scratch builds', () => {
  assert.equal(setReplacementNeedsConfirmation({
    targetSlot: 2, activeSlot: 1, activeDirty: true, hasPlanned: true,
  }), true)
  assert.equal(setReplacementNeedsConfirmation({
    targetSlot: 2, activeSlot: null, activeDirty: false, hasPlanned: true,
  }), true)
  assert.equal(setReplacementNeedsConfirmation({
    targetSlot: 1, activeSlot: 1, activeDirty: true, hasPlanned: true,
  }), false)
  assert.equal(setReplacementNeedsConfirmation({
    targetSlot: 2, activeSlot: 1, activeDirty: false, hasPlanned: false,
  }), false)
})
