import { useEffect, useMemo, useState } from 'react'
import Picker from './Picker.jsx'
import { Examine, Hover, rarityClass } from './ItemCard.jsx'
import { CLASS_FAMILY } from '../lib/classes.js'

/* Concrete equipment positions, rather than the catalog's broad slot names.
   Finger, Ear, Wrist and Charm each occur twice; keeping those identities is
   what makes "replace this ring, not whichever ring" possible. */
export const PLAN_SLOTS = [
  { key: 'activate1', label: 'Charm I', catalog: 'Charm', side: 'left' },
  { key: 'cloak', label: 'Cloak', catalog: 'Cloak', side: 'left' },
  { key: 'head', label: 'Head', catalog: 'Head', side: 'left' },
  { key: 'shoulders', label: 'Shoulders', catalog: 'Shoulders', side: 'left' },
  { key: 'chest', label: 'Chest', catalog: 'Chest', side: 'left' },
  { key: 'forearms', label: 'Forearms', catalog: 'Forearms', side: 'left' },
  { key: 'hands', label: 'Hands', catalog: 'Hands', side: 'left' },
  { key: 'legs', label: 'Legs', catalog: 'Legs', side: 'left' },
  { key: 'feet', label: 'Feet', catalog: 'Feet', side: 'left' },
  { key: 'primary', label: 'Primary', catalog: 'Primary', side: 'left' },
  { key: 'secondary', label: 'Secondary', catalog: 'Secondary', side: 'left' },
  { key: 'activate2', label: 'Charm II', catalog: 'Charm', side: 'right' },
  { key: 'ears', label: 'Ear I', catalog: 'Ear', side: 'right' },
  { key: 'ears2', label: 'Ear II', catalog: 'Ear', side: 'right' },
  { key: 'neck', label: 'Neck', catalog: 'Neck', side: 'right' },
  { key: 'left_ring', label: 'Finger I', catalog: 'Finger', side: 'right' },
  { key: 'right_ring', label: 'Finger II', catalog: 'Finger', side: 'right' },
  { key: 'left_wrist', label: 'Wrist I', catalog: 'Wrist', side: 'right' },
  { key: 'right_wrist', label: 'Wrist II', catalog: 'Wrist', side: 'right' },
  { key: 'waist', label: 'Waist', catalog: 'Waist', side: 'right' },
  { key: 'ranged', label: 'Ranged', catalog: 'Ranged', side: 'right' },
  { key: 'ammo', label: 'Ammo', catalog: 'Ammo', side: 'right', compact: true },
  { key: 'event_slot', label: 'Event', catalog: 'Event', side: 'right', compact: true },
]

const BY_CATALOG = PLAN_SLOTS.reduce((out, slot) => {
  ;(out[slot.catalog] ||= []).push(slot.key)
  return out
}, {})

export function eligiblePlanSlots(item) {
  const names = [item.slot, item.slot2].filter(Boolean)
  const keys = names.flatMap((name) => {
    if (name === 'Shield') return ['secondary']
    return BY_CATALOG[name] || []
  })
  // A two-hander may be filed as Primary/Secondary by the wiki, but it always
  // occupies Primary and consumes Secondary rather than being equipped there.
  return [...new Set(item.two_handed ? ['primary'] : keys)]
}

const STAT_GROUPS = [
  ['Attributes', ['str', 'agi', 'sta', 'int', 'wis']],
  ['Defense', ['mit', 'vselemental', 'vsnoxious', 'vsarcane', 'bchance']],
  ['Offense', ['crit', 'potency', 'abmod', 'acspeed', 'arspeed']],
  ['Autoattack', ['dps', 'aspeed', 'multi', 'aeauto', 'strike', 'accuracy',
    'flurry']],
]

const FALLBACK_LABEL = {
  health: 'Health', power: 'Power',
  str: 'Strength', agi: 'Agility', sta: 'Stamina', int: 'Intelligence', wis: 'Wisdom',
  mit: 'Mitigation', vselemental: 'Elemental', vsnoxious: 'Noxious', vsarcane: 'Arcane',
  bchance: 'Block Chance', crit: 'Crit Chance', potency: 'Potency', abmod: 'Ability Mod',
  dps: 'DPS', aspeed: 'Haste', multi: 'Multi Attack', aeauto: 'AE Autoattack',
  flurry: 'Flurry', strike: 'Strikethrough', accuracy: 'Accuracy',
  acspeed: 'Casting Speed', arspeed: 'Reuse Speed',
}

/* EQ2's stat window shows these as ratings, not percentages. Census metadata
   groups several modifier fields under one percent flag, so the display needs
   the game's narrower rule. */
const RATING_STATS = new Set(['dps', 'aspeed', 'multi'])
const POWER_STAT = { fighter: 'str', priest: 'wis', mage: 'int', scout: 'agi' }
const VITALS_PER_ATTRIBUTE = 8
const TLE_HEALTH_BASE = 2476

function addStats(to, stats, sign) {
  Object.entries(stats || {}).forEach(([key, value]) => {
    if (typeof value === 'number') to[key] = (to[key] || 0) + sign * value
  })
}

const isFiniteNumber = (value) => typeof value === 'number' && Number.isFinite(value)

function equippedChoice(slot, character, shortlist, active) {
  const planned = shortlist.items.find((i) => i.equip_slot === slot
    && i.page_title === active[slot])
  return planned || character?.gear?.find((g) => g.key === slot) || null
}

function socketColors(item) {
  if (!item) return []
  if (item.adornments) return item.adornments.map((a) => a.color).filter(Boolean)
  return Object.entries(item.adorns || {}).flatMap(([color, count]) =>
    Array.from({ length: count }, () => color))
}

function itemSockets(item) {
  if (!item) return []
  const installed = [...(item.adornments || [])]
  const colors = item.card?.stats?.adornments?.length
    ? item.card.stats.adornments
    : socketColors(item)
  /* Right-aligned strip, stable from the right edge. Yellow is deliberately
     first (leftmost): when it arrives on occasional later gear, adding it
     grows the strip leftward and white/turquoise do not change columns. */
  const order = ['yellow', 'black', 'green', 'orange', 'red', 'blue', 'purple',
    'cyan', 'grey', 'white', 'turquoise']
  const rank = (color) => {
    const at = order.indexOf(color)
    return at < 0 ? order.length : at
  }
  return [...colors].sort((a, b) => rank(a) - rank(b)).map((color) => {
    const at = installed.findIndex((adorn) => adorn.color === color)
    const adorn = at >= 0 ? installed.splice(at, 1)[0] : { color }
    return { color, adorn }
  })
}

function allSets(shortlist, catalog) {
  return [...new Map([...(catalog || []), ...(shortlist.sets || [])]
    .map((set) => [set.name, set])).values()]
}

function currentSetCounts(character) {
  const counts = {}
  ;(character?.gear || []).forEach((item) => (item.adornments || []).forEach((adorn) => {
    if (adorn.color !== 'turquoise' || !adorn.name) return
    const name = adorn.set_name || adorn.name
    counts[name] = (counts[name] || 0) + 1
  }))
  return counts
}

function plannedSetCounts(character, shortlist, active, catalog) {
  const byName = Object.fromEntries(allSets(shortlist, catalog)
    .map((set) => [set.name, set]))
  const counts = {}
  PLAN_SLOTS.forEach((def) => {
    const item = equippedChoice(def.key, character, shortlist, active)
    if (!item || !socketColors(item).includes('turquoise')) return
    const hasChoice = Object.prototype.hasOwnProperty.call(
      shortlist.set_slots || {}, def.key)
    const installed = hasChoice
      ? (shortlist.set_slots || {})[def.key]
      : item.set_name || (item.adornments || []).find(
        (adorn) => adorn.color === 'turquoise')?.set_name
        || (item.adornments || []).find((adorn) => adorn.color === 'turquoise')?.name
    const set = byName[installed]
    if (!set || (set.level && item.level && item.level < set.level)) return
    counts[installed] = (counts[installed] || 0) + 1
  })
  return counts
}

/* WHAT THE CHARACTER IS ACTUALLY WEARING RIGHT NOW, counted off the window
   rather than off the shortlist.

   A set bonus is the reason to care about a turquoise at all, and the reason
   it is worth moving one: the fourth piece is a different item from the third.
   That number changes with every adornment click, so it belongs under the
   equipment window where the clicking happens — not in a panel about the
   shortlist, which was the only place it appeared and which could not see the
   sets the character already had on.

   Two sources, one count. A planned set adornment is one the reader installed
   here (`set_slots`); a Census-equipped one is a turquoise the character is
   already wearing, and its tiers come from Census's own `setbonus_list`
   (`items._adornment`). SAME-NAMED ADORNMENTS ARE THE SAME SET — that is what
   a set adornment is in EoF/RoK, and it is the only join either source
   offers. */
function bonusLines(bonuses) {
  const clean = (line) => String(line).replace(/\|/g, '').trim()
  return (bonuses || []).map((bonus) => {
    const stats = (bonus.stat_lines || []).filter(Boolean).map(clean)
    const named = [bonus.text, bonus.effect].filter(Boolean).map(clean)
    /* The examine window has a headline and indented explanations. Keeping
       them as structure matters: joining a proc, trigger rate, damage and
       target caveat with middle dots produced one low-contrast paragraph
       nobody could scan. Wiki-backed tiers put flat stats on the headline;
       Census/Lexicon tiers use their effect there. */
    const summary = (stats.length ? stats : named.slice(0, 1)).join(', ')
    const details = [
      ...(stats.length ? named : named.slice(1)),
      ...(bonus.detail || []), ...(bonus.descriptions || []),
    ].filter(Boolean).map(clean).filter(Boolean)
    return { pieces: bonus.pieces ?? bonus.required, summary, details }
  }).filter((bonus) => bonus.pieces)
}

function wornSets(character, shortlist, active, catalog) {
  const planned = Object.fromEntries(allSets(shortlist, catalog)
    .map((set) => [set.name, set]))
  const twoHanded = shortlist.items.find(
    (i) => i.page_title === active.primary)?.two_handed
  const out = new Map()
  const current = new Map()
  const add = (name, bonuses) => {
    const row = out.get(name) || { name, count: 0, bonuses: [] }
    row.count += 1
    if (!row.bonuses.length) row.bonuses = bonusLines(bonuses)
    out.set(name, row)
  }
  ;(character?.gear || []).forEach((item) => (item.adornments || []).forEach((adorn) => {
    if (adorn.color !== 'turquoise' || !adorn.name) return
    const name = adorn.set_name || adorn.name
    const row = current.get(name) || { name, count: 0, bonuses: [] }
    row.count += 1
    if (!row.bonuses.length) row.bonuses = bonusLines(adorn.stats?.adornment?.set_bonuses)
    current.set(name, row)
  }))
  PLAN_SLOTS.forEach((def) => {
    if (def.key === 'secondary' && twoHanded) return
    const item = equippedChoice(def.key, character, shortlist, active)
    if (!item) return
    const hasChoice = Object.prototype.hasOwnProperty.call(
      shortlist.set_slots || {}, def.key)
    const chosen = (shortlist.set_slots || {})[def.key]
    const set = chosen ? planned[chosen] : null
    if (hasChoice) {
      if (set && socketColors(item).includes('turquoise')
          && !(set.level && item.level && item.level < set.level)) {
        add(set.name, set.bonuses)
      }
      return
    }
    ;(item.adornments || []).forEach((adorn) => {
      if (adorn.color !== 'turquoise' || !adorn.name) return
      add(adorn.set_name || adorn.name, adorn.stats?.adornment?.set_bonuses)
    })
  })
  return [...new Set([...out.keys(), ...current.keys()])].map((name) => {
    const after = out.get(name)
    const before = current.get(name)
    const count = after?.count || 0
    const currentCount = before?.count || 0
    return {
      name, count, currentCount, delta: count - currentCount,
      bonuses: after?.bonuses?.length ? after.bonuses : before?.bonuses || [],
    }
  }).sort((a, b) => b.count - a.count || b.currentCount - a.currentCount
    || a.name.localeCompare(b.name))
}

function TierDetails({ lines }) {
  if (!lines.length) return null
  return (
    <div className="wornsetdetails">
      <div className="wornsetdetailtext">
        {lines.map((line, i) => <span key={i}>• {line}</span>)}
      </div>
    </div>
  )
}

function WornSetCard({ set, full = false }) {
  return (
    <div className={`wornset${full ? ' full' : ' preview'}`} tabIndex={full ? undefined : 0}>
      <div className="wornsethead">
        <b>{set.name}</b>
        {!!set.delta && <i className="wornsetplanned">planned</i>}
        <em>
          {set.count} piece{set.count === 1 ? '' : 's'}
          {!!set.delta && (
            <strong className={set.delta > 0 ? 'upgrade' : 'downgrade'}>
              {set.delta > 0 ? '+' : ''}{set.delta}
            </strong>
          )}
        </em>
      </div>
      {!set.bonuses.length && (
        <p className="muted">No tier text recorded for this set.</p>
      )}
      {set.bonuses.map((bonus, i) => (
        <div className={`wornsettier${set.count >= bonus.pieces ? ' on' : ''}`} key={i}>
          <div className="wornsettierhead">
            <i aria-hidden="true">{set.count >= bonus.pieces ? '◆' : '◇'}</i>
            <b>({bonus.pieces})</b>
            <span>{bonus.summary}</span>
          </div>
          <TierDetails lines={bonus.details} />
        </div>
      ))}
    </div>
  )
}

function WornSets({ character, shortlist, active, catalog }) {
  const sets = useMemo(() => wornSets(character, shortlist, active, catalog),
    [character, shortlist, active, catalog])
  const [expanded, setExpanded] = useState(true)
  return (
    <div className={`wornsets${expanded ? '' : ' collapsed'}`}>
      <div className="wornsetshead">
        <div className="seclabel">Worn set bonuses</div>
        {!!sets.length && (
          <button type="button" className="btnlink" onClick={() => setExpanded((v) => !v)}>
            {expanded ? 'Hide' : `Show ${sets.length} set${sets.length === 1 ? '' : 's'}`}
          </button>
        )}
      </div>
      {!sets.length ? (
        <p className="muted">
          No set adornment in the window. A turquoise carries the set, not the
          armour it came in — click one to try a set on.
        </p>
      ) : expanded && (
        <div className="wornsetgrid">
          {sets.map((set) => (
            <Hover className="wornsetpopup" width={390} block
                   card={<WornSetCard set={set} full />} key={set.name}>
              <WornSetCard set={set} />
            </Hover>
          ))}
        </div>
      )}
    </div>
  )
}

function projection(character, shortlist, active, catalog) {
  const current = Object.fromEntries((character?.gear || []).map((g) => [g.key, g]))
  const candidates = Object.fromEntries((shortlist.items || []).map((i) => [i.page_title, i]))
  const totals = { ...(character?.planner_stats || {}) }
  const selected = {}
  Object.entries(active || {}).forEach(([slot, page]) => {
    if (candidates[page]?.equip_slot === slot) selected[slot] = candidates[page]
  })

  const twoHanded = selected.primary?.two_handed
  if (twoHanded) delete selected.secondary
  Object.entries(selected).forEach(([slot, item]) => {
    addStats(totals, current[slot]?.planner_stats, -1)
    addStats(totals, item.stats, 1)
    ;(current[slot]?.adornments || []).forEach((adorn) => {
      if (adorn.color === 'white') addStats(totals, adorn.planner_stats, -1)
    })
  })
  // A planned two-hander replaces both currently equipped hands. Secondary is
  // not processed as another choice while it is occupied by that weapon.
  if (twoHanded) {
    addStats(totals, current.secondary?.planner_stats, -1)
    ;(current.secondary?.adornments || []).forEach((adorn) => {
      if (adorn.color === 'white') addStats(totals, adorn.planner_stats, -1)
    })
  }
  Object.entries(shortlist.adorn_slots || {}).forEach(([slot, choices]) => {
    if (twoHanded && slot === 'secondary') return
    const item = equippedChoice(slot, character, shortlist, active)
    const currentItem = current[slot]
    const sockets = itemSockets(item)
    Object.entries(choices || {}).forEach(([socket, choice]) => {
      const row = sockets[Number(socket)]
      if (!row || row.color !== 'white') return
      /* An installed white is already inside Census's character total, but a
         planned item's empty socket is not. Only subtract from the real worn
         host, then add the selected alternative's additive stats. */
      if (item === currentItem) addStats(totals, row.adorn?.planner_stats, -1)
      if (choice) addStats(totals, choice.stats, 1)
    })
  })
  /* Census totals already include the thresholds the character is wearing.
     Project the DELTA between that complete current set and the complete
     planned set: removing the second piece must remove its +Crit, while
     changing a fourth slot must still count the three untouched pieces. */
  const currentCounts = currentSetCounts(character)
  const plannedCounts = plannedSetCounts(character, shortlist, active, catalog)
  const sets = allSets(shortlist, catalog)
  sets.forEach((set) => (set.bonuses || []).forEach((bonus) => {
    const before = (currentCounts[set.name] || 0) >= bonus.pieces
    const after = (plannedCounts[set.name] || 0) >= bonus.pieces
    if (before !== after) addStats(totals, bonus.stats, after ? 1 : -1)
  }))

  // On this TLE ruleset, each Stamina point grants 8 base Health and each point
  // of the archetype's primary stat grants 8 Power. Max Health modifiers are
  // additive percentages of the underlying health pool, not the displayed total.
  // Bobby's measured naked values pin that pool to 2,476 + STA*8:
  // 2,476 + 25*8 = 2,676; +2% racial => 2,729; +2.2% item => 2,788.
  const family = CLASS_FAMILY[(character?.character?.class || '').toLowerCase()]
  const powerStat = POWER_STAT[family]
  const baseStats = character?.planner_stats || {}
  if (isFiniteNumber(totals.health) && isFiniteNumber(totals.sta)
      && isFiniteNumber(baseStats.health) && isFiniteNumber(baseStats.sta)) {
    const currentPool = TLE_HEALTH_BASE + baseStats.sta * VITALS_PER_ATTRIBUTE
    const poolDelta = (totals.sta - baseStats.sta) * VITALS_PER_ATTRIBUTE
      + (totals.health - baseStats.health)
    const projectedPool = currentPool + poolDelta
    // The game exposes these modifiers to one decimal place and floors the
    // resulting whole Health. Infer the already-active modifier from Census,
    // then anchor the projected difference back to its exact reported total so
    // a harmless rounding discrepancy can never change the current baseline.
    const currentMaxHealth = Math.round((baseStats.health / currentPool - 1) * 1000) / 10
    const maxHealthDelta = isFiniteNumber(totals.maxhealth) ? totals.maxhealth : 0
    const currentCalculated = Math.floor(currentPool * (1 + currentMaxHealth / 100))
    const projectedCalculated = Math.floor(
      projectedPool * (1 + (currentMaxHealth + maxHealthDelta) / 100),
    )
    totals.health = baseStats.health + projectedCalculated - currentCalculated
  }
  if (powerStat && isFiniteNumber(totals.power)
      && isFiniteNumber(totals[powerStat]) && isFiniteNumber(baseStats[powerStat])) {
    totals.power += (totals[powerStat] - baseStats[powerStat])
      * VITALS_PER_ATTRIBUTE
  }

  /* Explain the projected total using the loadout that is actually visible.
     Census gives us the character total, not a naked/base value, so the
     residual is deliberately named "Character snapshot": logout buffs, racial
     effects and derived Health/Power arithmetic live there. The three
     inspectable equipment sources are computed explicitly and the residual
     makes every breakdown add back to the exact projected number. */
  const breakdown = { gear: {}, adornments: {}, sets: {} }
  PLAN_SLOTS.forEach((def) => {
    if (def.key === 'secondary' && twoHanded) return
    const item = equippedChoice(def.key, character, shortlist, active)
    if (!item) return
    addStats(breakdown.gear, item.planner_stats || item.stats, 1)
    const choices = (shortlist.adorn_slots || {})[def.key] || {}
    itemSockets(item).forEach((socket, index) => {
      if (socket.color !== 'white') return
      const overridden = Object.prototype.hasOwnProperty.call(choices, index)
      if (overridden) {
        if (choices[index]) addStats(breakdown.adornments, choices[index].stats, 1)
      } else if (item === current[def.key]) {
        addStats(breakdown.adornments, socket.adorn?.planner_stats, 1)
      }
    })
  })
  sets.forEach((set) => (set.bonuses || []).forEach((bonus) => {
    if ((plannedCounts[set.name] || 0) >= bonus.pieces) {
      addStats(breakdown.sets, bonus.stats, 1)
    }
  }))
  return { totals, selected, current, sets, breakdown }
}

function iconSrc(item, current = false) {
  if (item?.icon == null) return null
  return `/api/items/icon/${item.icon}.png`
}

function GearIcon({ item, current }) {
  const src = iconSrc(item, current)
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [src])
  return src && !failed
    ? <img src={src} alt="" width="38" height="38" onError={() => setFailed(true)} />
    : <span className="planslotempty" aria-hidden="true">◇</span>
}

function SocketTile({ adorn, color, empty = false }) {
  const actual = adorn?.icon != null ? `/api/items/icon/${adorn.icon}.png` : null
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [actual])
  return (
    <span className={`plansocketart ${color || 'unknown'}${empty ? ' empty' : ''}`}>
      {!empty && (
        <img src={!failed && actual ? actual : `/api/items/adorn/${color}.png`}
             alt="" onError={() => setFailed(true)} />
      )}
    </span>
  )
}

function adornmentSummary(adorn) {
  if (!adorn) return 'Empty socket'
  if (adorn.summary) return adorn.summary
  const rows = [...(adorn.stats?.stats || []), ...(adorn.stats?.effects || [])]
  if (!rows.length) return adorn.name || 'Equipped adornment'
  return rows.map((row) => `${Number(row.value).toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}${row.pct ? '%' : ''} ${row.name}`).join(' · ')
}

function compactWhiteName(adorn) {
  if (adorn?.prefix && adorn?.grade && adorn?.family) {
    return `${adorn.prefix} · ${adorn.grade} · ${adorn.family}`
  }
  const match = String(adorn?.name || '').match(
    /^(\S+) Adornment of (.+?) \(([^)]+)\)$/i,
  )
  return match ? `${match[1]} · ${match[3]} · ${match[2]}`
    : (adorn?.name || 'Current socket')
}

function StaticAdornmentSocket({ adorn }) {
  const card = adorn?.name ? {
    name: adorn.name,
    rarity: adorn.tier ? String(adorn.tier).toLowerCase().replace(/^./, (c) => c.toUpperCase()) : null,
    icon: adorn.icon, type: adorn.type || `${adorn.color || ''} Adornment`,
    level: adorn.level, stats: adorn.stats, effects: adorn.effects,
  } : null
  const button = (
    <button type="button" className={`planadornicon ${adorn.color || 'unknown'}`}
            aria-disabled="true" title={adornmentSummary(adorn)}>
      <SocketTile adorn={adorn} color={adorn.color} empty={!adorn.name} />
    </button>
  )
  return card ? (
    <Hover className="adornhover" width={350} card={<Examine row={card} />}>
      {button}
    </Hover>
  ) : button
}

function WhiteAdornmentSocket({ adorn, selection, choices, onChange }) {
  const shown = selection === undefined ? adorn : selection
  const value = selection === undefined ? '__equipped__'
    : selection === null ? '__empty__' : selection.key
  const options = [{
    value: '__equipped__', label: adorn?.name ? adornmentSummary(adorn) : 'Empty socket',
    menuLabel: <strong className="adornstat">{adorn?.name
      ? adornmentSummary(adorn) : 'Empty socket'}</strong>,
    hint: adorn?.name ? compactWhiteName(adorn) : 'Equipped',
    group: 'Current', title: adorn?.name,
    icon: <SocketTile adorn={adorn} color="white" empty={!adorn?.name} />,
  }, {
    value: '__empty__', label: 'Empty socket', hint: 'Remove planned adornment',
    icon: <SocketTile color="white" empty />,
  }, ...(choices || []).map((candidate) => ({
    value: candidate.key, label: candidate.summary,
    menuLabel: <strong className="adornstat">{candidate.summary}</strong>,
    hint: compactWhiteName(candidate), group: `Level ${candidate.level}`,
    title: `${candidate.name} · ${candidate.summary} · ${candidate.slots.join(', ')}`,
    icon: <SocketTile color="white" />,
  }))]
  return (
    <span className="adornchoice" title={adornmentSummary(shown)}
          onClick={(e) => e.stopPropagation()}>
      <Picker className={`adornpicker white${selection !== undefined ? ' changed' : ''}`} value={value}
              onChange={(next) => onChange(next === '__equipped__' ? undefined
                : next === '__empty__' ? null
                  : choices.find((candidate) => candidate.key === next))}
              label="Choose white adornment" options={options}
              filterFrom={1} filterHint="Stat, tier, grade, or name…"
              maxMenuWidth={285} menuClassName="whiteadornmenu" />
    </span>
  )
}

function SetAdornmentSocket({ adorn, selection, sets, onChange }) {
  const currentName = adorn.set_name || adorn.name || null
  const value = selection === undefined ? '__equipped__'
    : selection === null ? '__empty__' : selection
  const options = [{
    value: '__equipped__', label: currentName ? `Equipped: ${adorn.name}` : 'Equipped: empty',
    hint: 'Current socket', group: 'Current',
    icon: <SocketTile adorn={adorn} color="turquoise" empty={!currentName} />,
  }, {
    value: '__empty__', label: 'Empty socket', hint: 'Remove planned adornment',
    icon: <SocketTile color="turquoise" empty />,
  }, ...(sets || []).map((candidate) => ({
    value: candidate.name, label: candidate.piece || candidate.name,
    hint: `Level ${candidate.level ?? '—'} · ${(candidate.bonuses || [])
      .map((bonus) => bonus.pieces).join('/')} pieces`,
    group: candidate.group, title: candidate.name,
    icon: <SocketTile color="turquoise" />,
  }))]
  return (
    <span onClick={(e) => e.stopPropagation()}>
      <Picker className={`adornpicker${selection !== undefined ? ' changed' : ''}`} value={value}
              onChange={(next) => onChange(next === '__equipped__' ? undefined
                : next === '__empty__' ? null : next)}
              label="Choose turquoise adornment" options={options}
              filterFrom={1} filterHint="Search compatible adornments…" />
    </span>
  )
}

function EquipmentSlot({ def, character, shortlist, active, focused, occupied,
                         adornmentSets, whiteAdornments, onFocus, onCycle,
                         onSetAdornment, onWhiteAdornment, onRemoveItem }) {
  const current = character?.gear?.find((g) => g.key === def.key) || null
  const planned = shortlist.items.filter((i) => i.equip_slot === def.key)
  const options = [{ key: null, item: current, current: true },
    ...planned.map((item) => ({ key: item.page_title, item, current: false }))]
  let index = Math.max(0, options.findIndex((o) => o.key === (active[def.key] || null)))
  if (occupied) index = 0
  const shown = occupied ? null : options[index]?.item
  const isCurrent = occupied ? false : options[index]?.current
  const cycle = (delta, ev) => {
    ev.stopPropagation()
    if (options.length < 2 || occupied) return
    const next = (index + delta + options.length) % options.length
    onCycle(def.key, options[next].key)
  }
  return (
    <div className={`planslot${focused ? ' focused' : ''}${occupied ? ' occupied' : ''}${!isCurrent && shown ? ' planned' : ''}`}
         role="button" tabIndex="0" onClick={() => onFocus(def)}
         onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onFocus(def) }}
         title={occupied ? 'Occupied by the planned two-handed weapon' : `Show ${def.catalog} choices`}>
      <span className={`plansloticon${options.length > 1 && !occupied ? ' multiple' : ''}`}>
        {options.length > 1 && !occupied && (
          <button type="button" className="plansloticoncycle"
                  aria-label={`Cycle ${def.label} option`}
                  title={`Cycle ${def.label} — choice ${index + 1} of ${options.length}`}
                  onClick={(e) => cycle(1, e)}>›</button>
        )}
        <GearIcon item={shown} current={isCurrent} />
      </span>
      <span className="planslotcopy">
        <b>{def.label}</b>
        {shown?.card && !occupied ? (
          <Hover className="examinecard" width={350} card={<Examine row={shown.card} />}>
            <span tabIndex="0" className={rarityClass(shown?.tier)}>{shown.name}</span>
          </Hover>
        ) : (
          <span className={rarityClass(shown?.tier)}>
            {occupied ? 'Two-handed weapon' : (shown?.name || 'Empty')}
          </span>
        )}
      </span>
      {shown && (
        <AdornmentIcons item={shown} current={isCurrent}
          sets={allSets(shortlist, adornmentSets)} slot={def.catalog}
          installed={(shortlist.set_slots || {})[def.key]} compact
          whiteAdornments={whiteAdornments}
          whiteInstalled={(shortlist.adorn_slots || {})[def.key]}
          onChange={(name) => onSetAdornment(def.key, name)}
          onWhiteChange={(socket, choice) => onWhiteAdornment(def.key, socket, choice)} />
      )}
      {!isCurrent && shown && !occupied && (
        <button type="button" className="planslotremove"
                aria-label={`Remove ${shown.name} from ${def.label}`}
                title={`Remove ${shown.name} from this plan`}
                onClick={(e) => { e.stopPropagation(); onRemoveItem(def.key, shown.page_title) }}>
          ×
        </button>
      )}
      {!isCurrent && shown && <i className="plannedmark">planned</i>}
    </div>
  )
}

const SLOT_NAMES = {
  Ear: ['ear', 'ears'], Finger: ['finger', 'fingers'], Wrist: ['wrist', 'wrists'],
  Shoulder: ['shoulder', 'shoulders'], Shoulders: ['shoulder', 'shoulders'],
}

function setPieceForSlot(set, slot) {
  const names = SLOT_NAMES[slot] || [slot.toLowerCase()]
  return (set.pieces || []).find((piece) => {
    const suffix = piece.split(':').pop().trim().toLowerCase()
    return names.includes(suffix)
  }) || null
}

function compatibleSets(sets, item, slot) {
  const level = Number(item?.level) || null
  const floor = level ? Math.max(1, Math.floor(level / 10) * 10 - 20) : null
  return (sets || []).map((set) => ({ ...set, piece: setPieceForSlot(set, slot) }))
    .filter((set) => set.piece
      && (!level || !set.level || (set.level <= level && set.level >= floor)))
    .sort((a, b) => (b.level || 0) - (a.level || 0) || a.name.localeCompare(b.name))
    .map((set) => ({
      ...set, group: set.level ? `Tier ${Math.floor(set.level / 10) + 1}` : 'Other',
    }))
}

const ADORN_SLOT = {
  Ear: 'Earring', Finger: 'Ring', Shoulders: 'Shoulders',
}

function compatibleWhite(adornments, item, slot) {
  const level = Number(item?.level) || null
  const floor = level ? Math.max(1, Math.floor(level / 10) * 10 - 20) : null
  const wanted = ADORN_SLOT[slot] || slot
  return (adornments || []).filter((adorn) => adorn.slots.includes(wanted)
      && (!level || (adorn.level <= level && adorn.level >= floor)))
    .sort((a, b) => b.tier - a.tier || a.name.localeCompare(b.name))
}

function AdornmentIcons({ item, current, sets, whiteAdornments, slot, installed,
                          whiteInstalled, onChange, onWhiteChange, compact = false }) {
  const adornments = itemSockets(item).map((socket) => socket.adorn)
  if (!adornments.length) return compact ? null
    : <span className="muted">No adornment sockets</span>
  const legal = compatibleSets(sets, item, slot)
  const hasTurquoise = adornments.some((adorn) => adorn.color === 'turquoise')
  const equippedNames = adornments.map((adorn) => adorn.name).filter(Boolean)
  const installedCount = adornments.filter((adorn) => adorn.id).length
  return (
    <span className={`planadorns${compact ? ' compact' : ''}`} aria-label="Adornment sockets">
      {adornments.map((adorn, i) => (
        adorn.color === 'turquoise' ? (
          <SetAdornmentSocket key={`${adorn.color}-${i}`} adorn={adorn}
            selection={installed} sets={legal} onChange={onChange} />
        ) : adorn.color === 'white' ? (
          <WhiteAdornmentSocket key={`${adorn.color}-${i}`} adorn={adorn}
            selection={whiteInstalled?.[i]}
            choices={compatibleWhite(whiteAdornments, item, slot)}
            onChange={(choice) => onWhiteChange(i, choice)} />
        ) : <StaticAdornmentSocket key={`${adorn.color}-${i}`} adorn={adorn} />
      ))}
      {!compact && (
        <span className="socketchoice">
          {installed || (equippedNames.length ? equippedNames.join(' · ')
            : installedCount ? `${installedCount} equipped adornment${installedCount === 1 ? '' : 's'}`
              : hasTurquoise && legal.length ? 'Click turquoise to install a set'
                : current ? 'Open sockets' : 'Empty sockets')}
        </span>
      )}
    </span>
  )
}

function ProjectedStats({ character, shortlist, active, catalog, statLabel, statPct }) {
  const out = useMemo(() => projection(character, shortlist, active, catalog),
    [character, shortlist, active, catalog])
  const base = character?.planner_stats || {}
  const statState = (delta) => delta > 0 ? 'upgrade' : delta < 0 ? 'downgrade' : 'equipped'
  const precise = (value) => Number(value).toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })
  const signed = (value, pct) => `${value > 0 ? '+' : ''}${precise(value)}${pct ? '%' : ''}`
  if (!character?.synced) {
    return (
      <div className="planstats empty">
        <div className="planstathead"><b>Projected Stats</b></div>
        <p>Load a character to compare planned gear against current totals.</p>
      </div>
    )
  }
  const statRow = (key) => {
    const value = out.totals[key] ?? base[key]
    const delta = value - base[key]
    const pct = statPct[key] && !RATING_STATS.has(key)
    const state = statState(delta)
    const gear = out.breakdown.gear[key] || 0
    const adornments = out.breakdown.adornments[key] || 0
    const sets = out.breakdown.sets[key] || 0
    const other = value - gear - adornments - sets
    const label = statLabel[key] || FALLBACK_LABEL[key] || key
    const card = (
      <div className="statbreakdowncard">
        <div className="statbreakdownhead">
          <b>{label}</b><strong>{precise(value)}{pct ? '%' : ''}</strong>
        </div>
        <div><span>Character snapshot</span><em>{signed(other, pct)}</em></div>
        <div><span>Gear</span><em>{signed(gear, pct)}</em></div>
        <div><span>White adornments</span><em>{signed(adornments, pct)}</em></div>
        <div><span>Set bonuses</span><em>{signed(sets, pct)}</em></div>
      </div>
    )
    return (
      <Hover className="statbreakdown" width={280} card={card} block key={key}>
        <div className="planstatrow" tabIndex="0">
          <span>{label}</span>
          <b className={state}>{precise(value)}{pct ? '%' : ''}</b>
          <em className={state}>
            {delta ? signed(delta, pct) : '—'}
          </em>
        </div>
      </Hover>
    )
  }
  return (
    <div className="planstats">
      <div className="planstathead">
        <b>Projected Stats</b>
        <span className="planstatlegend">
          <i className="equipped">Equipped</i>
          <i className="upgrade">Upgrade</i>
          <i className="downgrade">Downgrade</i>
        </span>
      </div>
      {STAT_GROUPS.map(([title, keys]) => {
        const rows = keys.filter((key) => base[key] != null)
        if (!rows.length) return null
        return (
          <section key={title}>
            {title === 'Attributes' && ['health', 'power'].map(statRow)}
            <h3>{title}</h3>
            {rows.map(statRow)}
          </section>
        )
      })}
      {/* The set TIERS moved under the equipment window (`WornSets`), where the
          adornment clicks are and where what is actually worn can be counted.
          What stays here is only their arithmetic contribution, which is a
          stat like any other. */}
      <p className="planstatnote">
        Estimate: current {character?.character?.source === 'lexicon' ? 'EQ2 Lexicon' : 'Census'} total
        {' '}− equipped item stats + active planned item stats.
        Additive white adornments and set thresholds are included; procs,
        named effects and caps are not simulated.
      </p>
    </div>
  )
}

/* Look a toon up by name. A submit, never a keystroke: this is the one control
   on the page that can reach a public character service, so it runs when
   somebody asks it to. */
function CharacterLookup({ onLoad, busy, err, placeholder = 'Look up a name' }) {
  const [name, setName] = useState('')
  return (
    <form className="plancharacterlookup"
          onSubmit={(e) => { e.preventDefault(); if (name.trim()) onLoad(name.trim()) }}>
      <input value={name} onChange={(e) => setName(e.target.value)}
             placeholder={placeholder} aria-label="Character name"
             maxLength={40} spellCheck={false} />
      <button className="chip" type="submit" disabled={busy || name.trim().length < 2}>
        {busy ? '…' : 'Load'}
      </button>
      {err && <span className="lookuperr" role="status">{err}</span>}
    </form>
  )
}

export default function PlanLoadout({ characters, character, charId, onCharacter,
                                     shortlist, adornmentSets, whiteAdornments,
                                     active, focusSlot, onFocusSlot,
                                     onCycle, onReset, onSetAdornment, onWhiteAdornment,
                                     onRemoveItem, signedIn,
                                     onLookup, lookupBusy, lookupErr,
                                     savedSets, savedSetSlot, savedSetBusy, savedSetStatus,
                                     savedSetDirty,
                                     onSavedSetSlot, onSaveSet,
                                     onLoadSet,
                                     statLabel, statPct }) {
  const twoHanded = shortlist.items.find((i) => i.page_title === active.primary)?.two_handed
  const left = PLAN_SLOTS.filter((slot) => slot.side === 'left')
  const right = PLAN_SLOTS.filter((slot) => slot.side === 'right' && !slot.compact)
  const compact = PLAN_SLOTS.filter((slot) => slot.side === 'right' && slot.compact)
  const charOptions = (characters || []).map((c) => ({
    value: String(c.id), label: c.name,
    hint: c.class ? `${c.class} ${c.level ?? ''}` : 'not synced',
  }))
  const guestNeedsCharacter = !signedIn && !character?.character
  const slots = (rows) => rows.map((def) => (
    <EquipmentSlot key={def.key} def={def} character={character} shortlist={shortlist}
      active={active} focused={focusSlot === def.key}
      occupied={def.key === 'secondary' && twoHanded}
      adornmentSets={adornmentSets} whiteAdornments={whiteAdornments}
      onFocus={onFocusSlot} onCycle={onCycle} onSetAdornment={onSetAdornment}
      onWhiteAdornment={onWhiteAdornment}
      onRemoveItem={onRemoveItem} />
  ))
  return (
    <div className="card planloadout">
      <div className="loadouthead">
        {/* THE CHARACTER IS THE ONLY HEADLINE. "EQUIPMENT & STATS" first
            outweighed the toon's name, then survived as a redundant eyebrow.
            The card already visibly contains gear and stats; only who is in
            the window needs naming. */}
        <div className="loadoutwho">
          <h2>{character?.character?.name || 'No character loaded'}</h2>
          <div className="loadoutidentityline">
            <span className="loadoutmeta">{character?.character
              ? <>Level {character.character.level ?? '—'}{' '}{character.character.class || ''}</>
              : 'Look one up, or plan against an empty window'}</span>
            <SavedSetControls sets={savedSets} slot={savedSetSlot}
              signedIn={signedIn} busy={savedSetBusy} status={savedSetStatus}
              dirty={savedSetDirty}
              onSlot={onSavedSetSlot} onSave={onSaveSet}
              onLoad={onLoadSet} />
          </div>
        </div>
        {/* THE LOOKUP IS A WAY IN, NOT THE HEADLINE. It occupies the compact
            top-right row; reset and the account picker share the row below,
            aligned with level and Gear sets on the left.

            CHARACTER RECORDS ARE PUBLIC, so looking one up needs no account.
            Trying gear on your own toon is the whole point of this panel, and
            making that the one part of a signed-out page that demands signing
            up is backwards. Signed-in readers keep the picker AND get this,
            because an alt you never added is still an alt. */}
        <div className="loadoutactions">
          <div className="loadoutsearchrow">
            {!guestNeedsCharacter && (
              <CharacterLookup onLoad={onLookup} busy={lookupBusy} err={lookupErr} />
            )}
          </div>
          <div className="loadoutcharacterrow">
            {signedIn && characters === null && <span className="muted">Loading…</span>}
            <button className="chip resetgear" type="button" onClick={onReset}
                    disabled={!Object.keys(active).length
                      && !Object.keys(shortlist.set_slots || {}).length
                      && !Object.keys(shortlist.adorn_slots || {}).length}>Reset to Equipped</button>
            {characters?.length > 0 && (
              <label className="plancharacterpick">
                <span>Select Char</span>
                <Picker className="characterpicker" value={charId ? String(charId) : ''}
                        onChange={onCharacter} placeholder="Choose character"
                        options={charOptions} />
              </label>
            )}
          </div>
        </div>
      </div>
      <div className="loadoutbody">
        <div className={`loadoutgear${guestNeedsCharacter ? ' awaitingcharacter' : ''}`}>
          <div className="equipmentwindow">
            <div className="planslots left">{slots(left)}</div>
            <div className="planslots right">
              {slots(right)}
              <div className="planslotpair">{slots(compact)}</div>
            </div>
          </div>
          {guestNeedsCharacter && (
            <div className="guestgearlookup">
              <CharacterLookup onLoad={onLookup} busy={lookupBusy} err={lookupErr}
                placeholder="Look up a character on the Wuoshi server..." />
            </div>
          )}
        </div>
        <ProjectedStats character={character} shortlist={shortlist} active={active}
                        catalog={adornmentSets}
                        statLabel={statLabel} statPct={statPct} />
        <WornSets character={character} shortlist={shortlist} active={active}
                  catalog={adornmentSets} />
      </div>
    </div>
  )
}

function SavedSetControls({ sets, slot, signedIn, busy, status, dirty,
                            onSlot, onSave, onLoad }) {
  const selected = sets?.find((row) => row.slot === slot)
  const meaningfulCount = Math.max(1, ...(sets || [])
    .filter((row) => row.payload || row.name !== `Set ${row.slot}`)
    .map((row) => row.slot))
  const [visibleCount, setVisibleCount] = useState(meaningfulCount)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(selected?.name || '')
  useEffect(() => {
    setVisibleCount((count) => Math.max(count, meaningfulCount))
  }, [meaningfulCount])
  useEffect(() => {
    setDraft(selected?.name || '')
    setEditing(false)
  }, [selected?.name, slot])
  const choose = (row) => {
    onSlot(row.slot)
    setDraft(row.name)
    setEditing(false)
    if (row.payload) onLoad(row.slot)
  }
  const leave = () => {
    setDraft(selected?.name || '')
    setEditing(false)
  }
  const add = () => {
    const nextCount = Math.min(visibleCount + 1, sets?.length || 0)
    const row = sets?.[nextCount - 1]
    if (!row) return
    setVisibleCount(nextCount)
    onSlot(row.slot)
    setDraft(row.name)
    setEditing(true)
  }
  const save = () => {
    onSave(slot, draft)
    setEditing(false)
  }
  return (
    <div className="plansavedsets">
      <span className="seclabel">Gear sets</span>
      <div className="plansavedtabs" aria-label="Saved equipment sets">
        {(sets || []).slice(0, visibleCount).map((row) => (
          <button type="button" key={row.slot}
                  className={row.slot === slot ? 'on' : ''}
                  title={row.payload ? `Load ${row.name}` : `Select ${row.name}`}
                  onClick={() => choose(row)}>{row.name}</button>
        ))}
        {visibleCount < (sets?.length || 0) && (
          <button type="button" className="plansavedadd" disabled={busy}
                  aria-label="Add gear set" title="Add gear set"
                  onClick={add}>+</button>
        )}
      </div>
      {!editing && selected && (
        <div className="plansavedactions">
          {dirty && (
            <button type="button" className="chip on" disabled={busy}
                    onClick={() => onSave(slot, selected.name)}>Save...</button>
          )}
          <button type="button" className="iconbtn plansavededit" disabled={busy}
                  aria-label={selected.payload ? `Edit ${selected.name}` : `Save ${selected.name}`}
                  title={selected.payload ? `Edit ${selected.name}` : `Save ${selected.name}`}
                  onClick={() => setEditing(true)}>
            ✎
          </button>
          {status && <span className="plansavedstatus">{status}</span>}
        </div>
      )}
      {editing && (
        <div className="plansavedpanel">
          <input value={draft} maxLength={40} aria-label={`Name for gear set ${slot}`}
                 onChange={(event) => setDraft(event.target.value)} />
          <button type="button" className="chip on" disabled={busy}
                  onClick={save}>Save set</button>
          <button type="button" className="btnlink" onClick={leave}>Cancel</button>
          <span className="plansavedstatus">{status}</span>
          {!signedIn && (
            <span className="plansavedhint">Saved by cookie. <a href="/login">Create an account</a> to save long-term.</span>
          )}
        </div>
      )}
    </div>
  )
}
