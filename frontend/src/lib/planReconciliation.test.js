import assert from 'node:assert/strict'
import test from 'node:test'

import { observedPlannerItems, reconcileLocalObtained,
  reconcilePlanTargets } from './planReconciliation.js'

test('observed gear and installed adornments use exact Census identities', () => {
  assert.deepEqual(observedPlannerItems({ character: { source: 'census' }, gear: [{
    key: 'right_ring', item_id: 123, name: 'Raid Signet', adornments: [
      { id: 456, name: 'Grave Reckoning Set: Fingers', color: 'turquoise' },
    ],
  }] }), [
    { item_key: 'census:123', item_name: 'Raid Signet', source: 'equipped:gear:census' },
    { item_key: 'census:456', item_name: 'Grave Reckoning Set: Fingers',
      source: 'equipped:adornment:census' },
  ])
})

test('a planned ring is completed in either physical finger position', () => {
  const shortlist = { items: [{ page_title: 'Raid_Signet', name: 'Raid Signet',
    census_id: 123, equip_slot: 'left_ring' }], sets: [],
  active: { left_ring: 'Raid_Signet' }, set_slots: {}, adorn_slots: {} }
  const out = reconcilePlanTargets(shortlist, [{
    item_key: 'census:123', item_name: 'Raid Signet',
    source: 'equipped:gear:census', first_seen_ts: 10,
  }])
  assert.equal(out.remaining.items.length, 0)
  assert.deepEqual(out.remaining.active, {})
  assert.equal(out.completed[0].planned_slot, 'left_ring')
})

test('automatic completion derives remaining work without mutating saved targets', () => {
  const shortlist = { items: [{ page_title: 'Raid_Signet', name: 'Raid Signet',
    census_id: 123, equip_slot: 'left_ring' }], sets: [],
  active: { left_ring: 'Raid_Signet' }, set_slots: {}, adorn_slots: {} }
  const savedEvidence = structuredClone(shortlist)
  reconcilePlanTargets(shortlist, [{
    item_key: 'census:123', item_name: 'Raid Signet',
    source: 'equipped:gear:census', first_seen_ts: 10,
  }])
  assert.deepEqual(shortlist, savedEvidence)
})

test('completion is additive after a later snapshot no longer wears the item', () => {
  const first = reconcileLocalObtained([], [{
    item_key: 'census:123', item_name: 'Raid Signet', source: 'equipped:gear:census',
  }], 10)
  const later = reconcileLocalObtained(first.items, [], 20)
  assert.equal(later.items.length, 1)
  assert.equal(later.items[0].first_seen_ts, 10)
})

test('strict set-piece names complete only that piece, never the broad set', () => {
  const piece = { name: 'Grave Reckoning Set: Head', set_name: 'Grave Reckoning Set',
    slot_label: 'Head' }
  const broad = { name: 'Grave Reckoning Set', set_name: 'Grave Reckoning Set' }
  const out = reconcilePlanTargets({ items: [], sets: [piece, broad], active: {} }, [{
    item_key: 'census:456', item_name: 'Grave Reckoning Set: Head',
    source: 'equipped:adornment:census', first_seen_ts: 10,
  }])
  assert.deepEqual(out.remaining.sets, [broad])
  assert.equal(out.completed[0].name, piece.name)
})

test('a legacy broad set derives only its still-needed exact pieces', () => {
  const broad = { name: 'Grave Reckoning Set', set_name: 'Grave Reckoning Set',
    pieces: ['Grave Reckoning Set: Head', 'Grave Reckoning Set: Fingers'],
    bonuses: [{ pieces: 2, text: 'Bonus ladder stays available' }] }
  const out = reconcilePlanTargets({ items: [], sets: [broad], active: {} }, [{
    item_key: 'census:456', item_name: 'Grave Reckoning Set: Fingers',
    source: 'equipped:adornment:census', first_seen_ts: 10,
  }])
  assert.deepEqual(out.remaining.sets.map((row) => row.name),
    ['Grave Reckoning Set: Head'])
  assert.deepEqual(out.remaining.sets[0].bonuses, broad.bonuses)
  assert.equal(out.completed[0].name, 'Grave Reckoning Set: Fingers')
  assert.equal(out.completed[0].target, broad)
})

test('a completed turquoise piece stops forcing compatible projected sockets', () => {
  const piece = { name: 'Grave Reckoning Set: Fingers',
    set_name: 'Grave Reckoning Set', slot_label: 'Fingers',
    pieces: ['Grave Reckoning Set: Fingers', 'Grave Reckoning Set: Head'] }
  const out = reconcilePlanTargets({ items: [], sets: [piece], active: {},
    set_slots: {
      left_ring: 'Grave Reckoning Set',
      right_ring: 'Grave Reckoning Set',
      head: 'Grave Reckoning Set',
    },
  }, [{
    item_key: 'census:456', item_name: 'Grave Reckoning Set: Fingers',
    source: 'equipped:adornment:census', first_seen_ts: 10,
  }])
  assert.deepEqual(out.remaining.set_slots, { head: 'Grave Reckoning Set' })
  assert.equal(out.remaining.sets.length, 0)
})

test('similar names and gear/adornment source types never infer completion', () => {
  const out = reconcilePlanTargets({ items: [{
    page_title: 'Hat', name: 'Raid Hat', equip_slot: 'head', census_id: null,
  }], sets: [{ name: 'Raid Hat', set_name: 'Set', slot_label: 'Head' }], active: {} }, [{
    item_key: 'name:raid hats', item_name: 'Raid Hats',
    source: 'equipped:gear:lexicon', first_seen_ts: 1,
  }, {
    item_key: 'name:raid hat', item_name: 'Raid Hat',
    source: 'equipped:gear:lexicon', first_seen_ts: 1,
  }])
  assert.equal(out.remaining.items.length, 0)
  assert.equal(out.remaining.sets.length, 1)
})
