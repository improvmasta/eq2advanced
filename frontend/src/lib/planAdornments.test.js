import assert from 'node:assert/strict'
import test from 'node:test'
import { adornmentChanged, inheritSlotAdornments,
  setFitsHost, setPieceForSlot } from './planAdornments.js'

const host = (colors, adornments = [], extra = {}) => ({
  card: { stats: { adornments: colors } }, adornments, ...extra,
})

test('a new item inherits equipped white and turquoise adornments', () => {
  const white = { id: 17, name: 'Scintillating Adornment of Wisdom', color: 'white',
    planner_stats: { wis: 12 } }
  const turquoise = { id: 18, name: 'Focused Mind Set: Head',
    set_name: 'Focused Mind Set', color: 'turquoise' }
  const result = inheritSlotAdornments({
    slot: 'head',
    fromItem: host(['white', 'turquoise'], [white, turquoise]),
    toItem: host(['white', 'turquoise']),
  })

  assert.equal(result.setSlots.head, 'Focused Mind Set')
  assert.equal(result.adornSlots.head[0].name, white.name)
  assert.deepEqual(result.adornSlots.head[0].projection_stats, { wis: 12 })
})

test('selected slot adornments survive cycling to another item', () => {
  const selected = { key: 'swift-casting', name: 'Swift Casting', stats: { acspeed: 4 } }
  const result = inheritSlotAdornments({
    slot: 'chest',
    fromItem: host(['white', 'turquoise']),
    toItem: host(['white', 'turquoise']),
    setSlots: { chest: null },
    adornSlots: { chest: { 0: selected } },
  })

  assert.equal(result.setSlots.chest, null)
  assert.equal(result.adornSlots.chest[0], selected)
})

test('white adornments follow white socket order when other colors shift indexes', () => {
  const first = { key: 'first', name: 'First white', stats: { sta: 1 } }
  const second = { key: 'second', name: 'Second white', stats: { sta: 2 } }
  const result = inheritSlotAdornments({
    slot: 'chest',
    fromItem: host(['white', 'white']),
    toItem: host(['yellow', 'white', 'white']),
    adornSlots: { chest: { 0: first, 1: second } },
  })

  assert.equal(result.adornSlots.chest[1], first)
  assert.equal(result.adornSlots.chest[2], second)
})

test('choices are removed only when the next item has no matching sockets', () => {
  const result = inheritSlotAdornments({
    slot: 'head',
    fromItem: host(['white', 'turquoise']),
    toItem: host([]),
    setSlots: { head: 'Focused Mind Set', feet: 'Other Set' },
    adornSlots: { head: { 0: null }, feet: { 0: null } },
  })

  assert.deepEqual(result.setSlots, { feet: 'Other Set' })
  assert.deepEqual(result.adornSlots, { feet: { 0: null } })
})

test('set adornments require their actual slot-specific piece', () => {
  const set = { level: 70, pieces: [
    'Spirit Siphoning Set: Head', 'Spirit Siphoning Set: Shoulders',
  ] }
  assert.equal(setPieceForSlot(set, 'Head'), 'Spirit Siphoning Set: Head')
  assert.equal(setPieceForSlot(set, 'Shoulders'), 'Spirit Siphoning Set: Shoulders')
  assert.equal(setPieceForSlot(set, 'Finger'), null)
  assert.equal(setFitsHost(set, { level: 70 }, 'Head'), true)
  assert.equal(setFitsHost(set, { level: 70 }, 'Finger'), false)
})

test('set adornments respect the host equipment tier window', () => {
  const set = (level) => ({ level, pieces: ['Example Set: Head'] })
  assert.equal(setFitsHost(set(80), { level: 70 }, 'Head'), false)
  assert.equal(setFitsHost(set(70), { level: 70 }, 'Head'), true)
  assert.equal(setFitsHost(set(50), { level: 70 }, 'Head'), true)
  assert.equal(setFitsHost(set(40), { level: 70 }, 'Head'), false)
})

test('one-handed set pieces map to both weapon positions', () => {
  const set = { level: 70, pieces: ['Shock and Awe: One Handed'] }
  assert.equal(setFitsHost(set, { level: 70 }, 'Primary'), true)
  assert.equal(setFitsHost(set, { level: 70 }, 'Secondary'), true)
})

test('carried adornments and carried empty sockets are not visually changed', () => {
  assert.equal(adornmentChanged(
    { name: 'Scintillating Adornment of Wisdom' },
    { name: 'Scintillating Adornment of Wisdom' }), false)
  assert.equal(adornmentChanged(null, { color: 'white' }), false)
  assert.equal(adornmentChanged('Spirit Siphoning Set', {
    name: 'Spirit Siphoning Set: Head', set_name: 'Spirit Siphoning Set',
  }), false)
  assert.equal(adornmentChanged(null, { name: 'Existing adornment' }), true)
})
