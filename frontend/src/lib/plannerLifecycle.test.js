import assert from 'node:assert/strict'
import test from 'node:test'

import { characterForRequest, chooseAccountCharacter, mergeRecentCharacters,
  ownerOfSummary } from './plannerLifecycle.js'

const bobby = (source = 'census') => ({ character: {
  planner_key: 'wuoshi:bobby', lookup_name: 'Bobby',
  display_name: 'Bobby (Wuoshi)', name: 'Bobby (Wuoshi)',
  class: 'Necromancer', world: 'Wuoshi', census_id: source === 'census' ? 123 : null,
  source,
} })

test('Census and Lexicon summaries use the backend planner key unchanged', () => {
  assert.equal(ownerOfSummary(bobby('census')).key, 'wuoshi:bobby')
  assert.equal(ownerOfSummary(bobby('lexicon')).key, 'wuoshi:bobby')
  assert.equal(ownerOfSummary(bobby('lexicon')).lookup_name, 'Bobby')
})

test('plain account Planner keeps a valid remembered id or picks alphabetically', () => {
  const rows = [{ id: 9, name: 'Zed' }, { id: 2, name: 'Amy' }]
  assert.equal(chooseAccountCharacter(rows, 9), '9')
  assert.equal(chooseAccountCharacter(rows, 404), '2')
  assert.equal(chooseAccountCharacter([], 9), '')
})

test('a query hides unrelated old responses and query removal restores account state', () => {
  const sally = { character: { lookup_name: 'Sally' } }
  const account = { character: { lookup_name: 'Owned' } }
  const matching = bobby()
  assert.equal(characterForRequest('Bobby', sally, account), null)
  assert.equal(characterForRequest('Bobby', matching, account), matching)
  assert.equal(characterForRequest('', sally, account), account)
})

test('recent searches round-trip lookup_name and keep fresher facts', () => {
  const merged = mergeRecentCharacters(
    [{ key: 'wuoshi:123', name: 'Bobby (Wuoshi)', className: 'old', updated_ts: 5 }],
    [{ key: 'wuoshi:bobby', lookup_name: 'Bobby', name: 'Bobby (Wuoshi)',
      className: 'Necromancer', updated_ts: 10 }],
    [{ key: 'wuoshi:bobby', lookup_name: 'Bobby', name: 'Old label', saved: true,
      updated_ts: 1 }],
  )
  assert.equal(merged.length, 1)
  assert.equal(merged[0].key, 'wuoshi:bobby')
  assert.equal(merged[0].lookup_name, 'Bobby')
  assert.equal(merged[0].name, 'Bobby (Wuoshi)')
  assert.equal(merged[0].className, 'Necromancer')
  assert.equal(merged[0].saved, true)
})
