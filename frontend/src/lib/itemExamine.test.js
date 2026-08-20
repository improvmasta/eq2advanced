import assert from 'node:assert/strict'
import test from 'node:test'

import { setBonusPresentation } from './itemExamine.js'

test('a proc-only set tier puts its effect on the threshold line', () => {
  assert.deepEqual(setBonusPresentation({
    required: 2,
    stat_lines: [],
    effect: 'Applies Focus: Soulrot VIII.',
    descriptions: ['Increases the damage of Soulrot VIII by 65.'],
  }), {
    headline: 'Applies Focus: Soulrot VIII.',
    details: ['Increases the damage of Soulrot VIII by 65.'],
  })
})

test('a set tier with stats keeps its effect beneath the threshold line', () => {
  assert.deepEqual(setBonusPresentation({
    required: 4,
    stat_lines: ['2 Reuse Speed', '10 Combat Skills'],
    effect: 'Applies Focus: Lich III.',
    descriptions: ['Increases the base damage and healing amount of Lich III by 15%.'],
  }), {
    headline: '2 Reuse Speed, 10 Combat Skills',
    details: [
      'Applies Focus: Lich III.',
      'Increases the base damage and healing amount of Lich III by 15%.',
    ],
  })
})
