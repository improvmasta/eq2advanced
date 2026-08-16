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
  { key: 'ammo', label: 'Ammo', catalog: 'Ammo', side: 'right' },
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

function allSets(shortlist, catalog) {
  return [...new Map([...(catalog || []), ...(shortlist.sets || [])]
    .map((set) => [set.name, set])).values()]
}

function activeSets(character, shortlist, active, catalog) {
  const byName = Object.fromEntries(allSets(shortlist, catalog)
    .map((set) => [set.name, set]))
  const counts = {}
  Object.entries(shortlist.set_slots || {}).forEach(([slot, name]) => {
    const item = equippedChoice(slot, character, shortlist, active)
    const set = byName[name]
    if (!set || !socketColors(item).includes('turquoise')) return
    if (set.level && item?.level && item.level < set.level) return
    counts[name] = (counts[name] || 0) + 1
  })
  return Object.values(byName).map((set) => ({ ...set, count: counts[set.name] || 0 }))
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
  const add = (name, bonuses) => {
    const row = out.get(name) || { name, count: 0, bonuses: [] }
    row.count += 1
    if (!row.bonuses.length) row.bonuses = bonusLines(bonuses)
    out.set(name, row)
  }
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
  return [...out.values()].sort((a, b) => b.count - a.count
    || a.name.localeCompare(b.name))
}

function TierDetails({ lines }) {
  const [expanded, setExpanded] = useState(false)
  if (!lines.length) return null
  const collapsible = lines.length > 2 || lines.join(' ').length > 110
  return (
    <div className={`wornsetdetails${collapsible ? ' collapsible' : ''}`
      + `${expanded || !collapsible ? ' expanded' : ''}`}>
      <div className="wornsetdetailtext">
        {lines.map((line, i) => <span key={i}>• {line}</span>)}
      </div>
      {collapsible && (
        <button type="button" className="btnlink"
                aria-label={expanded ? 'Collapse bonus details' : 'Expand bonus details'}
                onClick={() => setExpanded((value) => !value)}>
          {expanded ? 'less' : '…'}
        </button>
      )}
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
            <div className="wornset" key={set.name}>
              <div className="wornsethead">
                <b>{set.name}</b>
                <em>{set.count} piece{set.count === 1 ? '' : 's'}</em>
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
  })
  // A planned two-hander replaces both currently equipped hands. Secondary is
  // not processed as another choice while it is occupied by that weapon.
  if (twoHanded) {
    addStats(totals, current.secondary?.planner_stats, -1)
  }
  const sets = activeSets(character, shortlist, active, catalog)
  sets.forEach((set) => (set.bonuses || []).forEach((bonus) => {
    if (set.count >= bonus.pieces) addStats(totals, bonus.stats, 1)
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
  return { totals, selected, current, sets }
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

function adornmentCard(adorn, set) {
  const syntheticStats = set ? {
    stats: [], effects: [], flags: [], adornments: [],
    adornment: {
      color: 'turquoise', slots: [], requires_equip: true,
      predicate: 'In Rise of Kunark or previous expansion zones',
      set_bonuses: (set.bonuses || []).map((bonus) => ({
        required: bonus.pieces, effect: (bonus.stat_lines || []).join(', ') || null,
        descriptions: [bonus.text, ...(bonus.detail || [])]
          .filter(Boolean).map((line) => line.replace(/\|$/, '')),
      })),
    },
  } : null
  if (!adorn?.name && !set) return null
  return {
    name: adorn?.name || set.name,
    rarity: adorn?.tier ? adorn.tier[0] + adorn.tier.slice(1).toLowerCase() : null,
    icon: adorn?.icon, type: adorn?.type || 'Turquoise Adornment',
    level: adorn?.level ?? set?.level,
    stats: adorn?.stats || syntheticStats, effects: null,
  }
}

function StaticAdornmentSocket({ adorn }) {
  const card = adornmentCard(adorn, null)
  const button = (
    <button type="button" className={`planadornicon ${adorn.color || 'unknown'}`}
            aria-disabled="true" title={`${adorn.name || 'Empty'} ${adorn.color || 'adornment'} socket`}>
      <SocketTile adorn={adorn} color={adorn.color} empty={!adorn.id && !adorn.name} />
    </button>
  )
  return card ? (
    <Hover className="adornhover" width={350} card={<Examine row={card} />}>
      {button}
    </Hover>
  ) : button
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
      <Picker className="adornpicker" value={value}
              onChange={(next) => onChange(next === '__equipped__' ? undefined
                : next === '__empty__' ? null : next)}
              label="Choose turquoise adornment" options={options}
              filterFrom={1} filterHint="Search compatible adornments…" />
    </span>
  )
}

function EquipmentSlot({ def, character, shortlist, active, focused, occupied,
                         adornmentSets, onFocus, onCycle, onSetAdornment, onRemoveItem }) {
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
          onChange={(name) => onSetAdornment(def.key, name)} />
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
  const floor = level ? Math.max(1, Math.floor(level / 10) * 10 - 10) : null
  return (sets || []).map((set) => ({ ...set, piece: setPieceForSlot(set, slot) }))
    .filter((set) => set.piece
      && (!level || !set.level || (set.level <= level && set.level >= floor)))
    .sort((a, b) => (b.level || 0) - (a.level || 0) || a.name.localeCompare(b.name))
    .map((set) => ({
      ...set, group: set.level ? `Tier ${Math.floor(set.level / 10) + 1}` : 'Other',
    }))
}

function AdornmentIcons({ item, current, sets, slot, installed, onChange, compact = false }) {
  const adornments = current
    ? (item?.adornments || [])
    : Object.entries(item?.adorns || {}).flatMap(([color, count]) =>
      Array.from({ length: count }, () => ({
        color, name: color === 'turquoise' ? item.set_name : null,
      })))
  if (!adornments.length) return <span className="muted">No adornment sockets</span>
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
  if (!character?.synced) {
    return (
      <div className="planstats empty">
        <div className="planstathead"><b>Projected Stats</b></div>
        <p>Load a Census character to compare planned gear against current totals.</p>
      </div>
    )
  }
  const statRow = (key) => {
    const value = out.totals[key] ?? base[key]
    const delta = value - base[key]
    const pct = statPct[key] && !RATING_STATS.has(key)
    const state = statState(delta)
    return (
      <div className="planstatrow" key={key}>
        <span>{statLabel[key] || FALLBACK_LABEL[key] || key}</span>
        <b className={state}>{precise(value)}{pct ? '%' : ''}</b>
        <em className={state}>
          {delta ? `${delta > 0 ? '+' : ''}${precise(delta)}${pct ? '%' : ''}` : '—'}
        </em>
      </div>
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
        Estimate: current Census total − equipped item stats + active planned item stats.
        Procs, named set effects, moved adornments and caps are not simulated.
      </p>
    </div>
  )
}

/* Look a toon up by name. A submit, never a keystroke: this is the one control
   on the page that can reach Census, so it runs when somebody asks it to. */
function CharacterLookup({ onLoad, busy, err }) {
  const [name, setName] = useState('')
  return (
    <form className="plancharacterlookup"
          onSubmit={(e) => { e.preventDefault(); if (name.trim()) onLoad(name.trim()) }}>
      <input value={name} onChange={(e) => setName(e.target.value)}
             placeholder="Look up a name" aria-label="Character name"
             maxLength={40} spellCheck={false} />
      <button className="chip" type="submit" disabled={busy || name.trim().length < 2}>
        {busy ? '…' : 'Load'}
      </button>
      {err && <span className="lookuperr" role="status">{err}</span>}
    </form>
  )
}

export default function PlanLoadout({ characters, character, charId, onCharacter,
                                     shortlist, adornmentSets, active, focusSlot, onFocusSlot,
                                     onCycle, onReset, onSetAdornment,
                                     onRemoveItem, signedIn,
                                     onLookup, lookupBusy, lookupErr,
                                     statLabel, statPct }) {
  const twoHanded = shortlist.items.find((i) => i.page_title === active.primary)?.two_handed
  const left = PLAN_SLOTS.filter((slot) => slot.side === 'left')
  const right = PLAN_SLOTS.filter((slot) => slot.side === 'right')
  const charOptions = (characters || []).map((c) => ({
    value: String(c.id), label: c.name,
    hint: c.class ? `${c.class} ${c.level ?? ''}` : 'not synced',
  }))
  const slots = (rows) => rows.map((def) => (
    <EquipmentSlot key={def.key} def={def} character={character} shortlist={shortlist}
      active={active} focused={focusSlot === def.key}
      occupied={def.key === 'secondary' && twoHanded}
      adornmentSets={adornmentSets}
      onFocus={onFocusSlot} onCycle={onCycle} onSetAdornment={onSetAdornment}
      onRemoveItem={onRemoveItem} />
  ))
  return (
    <div className="card planloadout">
      <div className="loadouthead">
        {/* THE CHARACTER IS THE HEADLINE. It was the other way round —
            "EQUIPMENT & STATS" in gold display caps with the toon's name in
            small muted type after it — so the loudest words on the page named
            the panel instead of the person, and the one fact that changes
            (who this is) was the one set in the quietest type. The panel's
            name is a label; the character is the subject. */}
        <div className="loadoutwho">
          <span className="seclabel">Equipment &amp; Stats</span>
          <h2>
            {character?.character?.name || 'No character loaded'}
            {character?.character ? (
              <small>Level {character.character.level ?? '—'}{' '}
                {character.character.class || ''}</small>
            ) : (
              <small>Look one up, or plan against an empty window</small>
            )}
          </h2>
        </div>
        {/* THE LOOKUP IS A WAY IN, NOT THE HEADLINE. It sits before the picker
            and stays small: who you are planning for is the answer this row
            exists to show, and a full-width search box beside it read as the
            more important of the two.

            A CENSUS CHARACTER IS PUBLIC, so looking one up needs no account.
            Trying gear on your own toon is the whole point of this panel, and
            making that the one part of a signed-out page that demands signing
            up is backwards. Signed-in readers keep the picker AND get this,
            because an alt you never added is still an alt. */}
        <div className="loadoutactions">
          <CharacterLookup onLoad={onLookup} busy={lookupBusy} err={lookupErr} />
          {signedIn && characters === null && <span className="muted">Loading…</span>}
          {characters?.length > 0 && (
            <label className="plancharacterpick">
              <span>Planning for</span>
              <Picker className="characterpicker" value={charId ? String(charId) : ''}
                      onChange={onCharacter} placeholder="Choose character"
                      options={charOptions} />
            </label>
          )}
          <button className="chip resetgear" type="button" onClick={onReset}
                  disabled={!Object.keys(active).length
                    && !Object.keys(shortlist.set_slots || {}).length}>Reset all</button>
        </div>
      </div>
      <div className="loadoutbody">
        <div className="loadoutgear">
          <div className="equipmentwindow">
            <div className="planslots left">{slots(left)}</div>
            <div className="planslots right">{slots(right)}</div>
          </div>
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
