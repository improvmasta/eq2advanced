import { useEffect, useMemo, useState } from 'react'
import Picker from './Picker.jsx'
import { Examine, Hover, rarityClass } from './ItemCard.jsx'
import { fmt } from '../lib/api.js'

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
  ['Offense', ['crit', 'potency', 'abmod', 'dps', 'aspeed', 'multi', 'aeauto',
    'flurry', 'strike', 'accuracy']],
  ['Casting', ['acspeed', 'arspeed']],
]

const FALLBACK_LABEL = {
  str: 'Strength', agi: 'Agility', sta: 'Stamina', int: 'Intelligence', wis: 'Wisdom',
  mit: 'Mitigation', vselemental: 'Elemental', vsnoxious: 'Noxious', vsarcane: 'Arcane',
  bchance: 'Block Chance', crit: 'Crit Chance', potency: 'Potency', abmod: 'Ability Mod',
  dps: 'DPS', aspeed: 'Haste', multi: 'Multi Attack', aeauto: 'AE Autoattack',
  flurry: 'Flurry', strike: 'Strikethrough', accuracy: 'Accuracy',
  acspeed: 'Casting Speed', arspeed: 'Reuse Speed',
}

function addStats(to, stats, sign) {
  Object.entries(stats || {}).forEach(([key, value]) => {
    if (typeof value === 'number') to[key] = (to[key] || 0) + sign * value
  })
}

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

function activeSets(character, shortlist, active) {
  const byName = Object.fromEntries((shortlist.sets || []).map((set) => [set.name, set]))
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
  return (bonuses || []).map((bonus) => ({
    pieces: bonus.pieces ?? bonus.required,
    text: [bonus.text, ...(bonus.stat_lines || []), bonus.effect,
      ...(bonus.descriptions || [])]
      .filter(Boolean).map((line) => String(line).replace(/\|/g, '').trim())
      .filter(Boolean).join(' · '),
  })).filter((bonus) => bonus.pieces)
}

function wornSets(character, shortlist, active) {
  const planned = Object.fromEntries((shortlist.sets || []).map((s) => [s.name, s]))
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
    const chosen = (shortlist.set_slots || {})[def.key]
    const set = chosen ? planned[chosen] : null
    if (set && socketColors(item).includes('turquoise')
        && !(set.level && item.level && item.level < set.level)) {
      add(set.name, set.bonuses)
      return
    }
    ;(item.adornments || []).forEach((adorn) => {
      if (adorn.color !== 'turquoise' || !adorn.name) return
      add(adorn.name, adorn.stats?.adornment?.set_bonuses)
    })
  })
  return [...out.values()].sort((a, b) => b.count - a.count
    || a.name.localeCompare(b.name))
}

function WornSets({ character, shortlist, active }) {
  const sets = useMemo(() => wornSets(character, shortlist, active),
    [character, shortlist, active])
  return (
    <div className="wornsets">
      <div className="seclabel">Worn set bonuses</div>
      {!sets.length ? (
        <p className="muted">
          No set adornment in the window. A turquoise carries the set, not the
          armour it came in — click one to try a set on.
        </p>
      ) : sets.map((set) => (
        <div className="wornset" key={set.name}>
          <div className="wornsethead">
            <b>{set.name}</b>
            <em>{set.count} piece{set.count === 1 ? '' : 's'}</em>
          </div>
          {!set.bonuses.length && (
            <p className="muted">No tier text recorded for this set.</p>
          )}
          {set.bonuses.map((bonus, i) => (
            <p className={set.count >= bonus.pieces ? 'on' : ''} key={i}>
              <i aria-hidden="true">{set.count >= bonus.pieces ? '◆' : '◇'}</i>
              <b>({bonus.pieces})</b> {bonus.text}
            </p>
          ))}
        </div>
      ))}
    </div>
  )
}

function projection(character, shortlist, active) {
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
  const sets = activeSets(character, shortlist, active)
  sets.forEach((set) => (set.bonuses || []).forEach((bonus) => {
    if (set.count >= bonus.pieces) addStats(totals, bonus.stats, 1)
  }))
  return { totals, selected, current, sets }
}

function iconSrc(item, current = false) {
  if (item?.icon == null) return null
  return current
    ? `https://census.daybreakgames.com/img/eq2/icons/${item.icon}/item`
    : `/api/items/icon/${item.icon}.png`
}

function GearIcon({ item, current }) {
  const src = iconSrc(item, current)
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [src])
  return src && !failed
    ? <img src={src} alt="" width="38" height="38" onError={() => setFailed(true)} />
    : <span className="planslotempty" aria-hidden="true">◇</span>
}

function AdornmentSocket({ adorn, installed, sets, onCycle }) {
  const plannedName = adorn.color === 'turquoise' ? installed : null
  const setName = adorn.color === 'turquoise' ? (plannedName || adorn.name) : null
  const set = (sets || []).find((candidate) => candidate.name === setName)
  const syntheticStats = set ? {
    stats: [], effects: [], flags: [], adornments: [],
    adornment: {
      color: 'turquoise', slots: [], requires_equip: true,
      predicate: 'In Rise of Kunark or previous expansion zones',
      set_bonuses: (set.bonuses || []).map((bonus) => ({
        required: bonus.pieces, effect: null,
        descriptions: [bonus.text.replace(/\|$/, '')],
      })),
    },
  } : null
  const name = plannedName || adorn.name || (adorn.id ? `Equipped adornment #${adorn.id}` : null)
  const card = name ? {
    name, rarity: adorn.tier ? adorn.tier[0] + adorn.tier.slice(1).toLowerCase() : null,
    icon: adorn.icon, type: adorn.type, level: adorn.level,
    stats: adorn.stats || syntheticStats, effects: null,
  } : null
  const button = (
    <button type="button" className={`planadornicon ${adorn.color || 'unknown'}`}
            disabled={adorn.color !== 'turquoise'}
            onClick={(e) => { e.stopPropagation(); onCycle() }}
            title={adorn.color === 'turquoise'
              ? `${name || 'Empty turquoise socket'} — click to change`
              : `${name || 'Empty'} ${adorn.color || 'adornment'} socket`}>
      <img src={`/api/items/adorn/${adorn.color}.png`} alt="" />
      {(plannedName || adorn.id || adorn.name) && <i aria-hidden="true">✓</i>}
    </button>
  )
  return card ? (
    <Hover className="adornhover" width={350} card={<Examine row={card} />}>
      {button}
    </Hover>
  ) : button
}

function EquipmentSlot({ def, character, shortlist, active, focused, occupied,
                         onFocus, onCycle, onSetAdornment, onResetSlot }) {
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
      <span className="plansloticon"><GearIcon item={shown} current={isCurrent} /></span>
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
        <AdornmentIcons item={shown} current={isCurrent} sets={shortlist.sets}
          installed={(shortlist.set_slots || {})[def.key]} compact
          onChange={(name) => onSetAdornment(def.key, name)} />
      )}
      {options.length > 1 && !occupied && (
        <span className="planslotcycle">
          <button type="button" aria-label={`Previous ${def.label} option`}
                  onClick={(e) => cycle(-1, e)}>‹</button>
          <em>{index + 1}/{options.length}</em>
          <button type="button" aria-label={`Next ${def.label} option`}
                  onClick={(e) => cycle(1, e)}>›</button>
        </span>
      )}
      {/* ONE SLOT AT A TIME. Cycling can reach the equipped item too, but only
          by walking past every candidate on the list — and undoing one change
          is a thing you do far more often than comparing five rings. */}
      {!isCurrent && shown && !occupied && (
        <button type="button" className="planslotreset"
                aria-label={`Reset ${def.label} to the equipped item`}
                title={`Put ${def.label} back to the equipped item`}
                onClick={(e) => { e.stopPropagation(); onResetSlot(def.key) }}>
          ↺
        </button>
      )}
      {!isCurrent && shown && <i className="plannedmark">planned</i>}
    </div>
  )
}

function AdornmentIcons({ item, current, sets, installed, onChange, compact = false }) {
  const adornments = current
    ? (item?.adornments || [])
    : Object.entries(item?.adorns || {}).flatMap(([color, count]) =>
      Array.from({ length: count }, () => ({
        color, name: color === 'turquoise' ? item.set_name : null,
      })))
  if (!adornments.length) return <span className="muted">No adornment sockets</span>
  const legal = (sets || []).filter((set) => !set.level || !item?.level || item.level >= set.level)
  const hasTurquoise = adornments.some((adorn) => adorn.color === 'turquoise')
  const equippedNames = adornments.map((adorn) => adorn.name).filter(Boolean)
  const installedCount = adornments.filter((adorn) => adorn.id).length
  const cycle = () => {
    const names = [null, ...legal.map((set) => set.name)]
    const at = names.indexOf(installed || null)
    onChange(names[(at + 1) % names.length])
  }
  return (
    <span className={`planadorns${compact ? ' compact' : ''}`} aria-label="Adornment sockets">
      {adornments.map((adorn, i) => (
        <AdornmentSocket key={`${adorn.color}-${i}`} adorn={adorn} installed={installed}
                         sets={sets} onCycle={cycle} />
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

function ProjectedStats({ character, shortlist, active, statLabel, statPct }) {
  const out = useMemo(() => projection(character, shortlist, active),
    [character, shortlist, active])
  const base = character?.planner_stats || {}
  if (!character?.synced) {
    return (
      <div className="planstats empty">
        <div className="planstathead"><b>Projected Stats</b></div>
        <p>Load a Census character to compare planned gear against current totals.</p>
      </div>
    )
  }
  return (
    <div className="planstats">
      <div className="planstathead">
        <b>Projected Stats</b>
        <span>{Object.keys(out.selected).length
          ? `${Object.keys(out.selected).length} slot${Object.keys(out.selected).length === 1 ? '' : 's'} changed`
          : 'Current equipment'}</span>
      </div>
      <div className="planstatgeneral">
        {['health', 'power'].map((key) => {
          const value = out.totals[key] ?? base[key]
          const delta = value - base[key]
          return <span key={key}>{key === 'health' ? 'Health' : 'Power'} <b>{fmt.num(value)}</b>
            {delta !== 0 && <em className={delta > 0 ? 'up' : 'down'}>
              {delta > 0 ? '+' : ''}{fmt.num(delta)}
            </em>}</span>
        })}
      </div>
      {STAT_GROUPS.map(([title, keys]) => {
        const rows = keys.filter((key) => base[key] != null)
        if (!rows.length) return null
        return (
          <section key={title}>
            <h3>{title}</h3>
            {rows.map((key) => {
              const value = out.totals[key] ?? base[key]
              const delta = value - base[key]
              const pct = statPct[key]
              return (
                <div className="planstatrow" key={key}>
                  <span>{statLabel[key] || FALLBACK_LABEL[key] || key}</span>
                  <b>{fmt.num(Math.round(value * 10) / 10)}{pct ? '%' : ''}</b>
                  <em className={delta > 0 ? 'up' : delta < 0 ? 'down' : ''}>
                    {delta ? `${delta > 0 ? '+' : ''}${Math.round(delta * 10) / 10}${pct ? '%' : ''}` : '—'}
                  </em>
                </div>
              )
            })}
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
                                     shortlist, active, focusSlot, onFocusSlot,
                                     onCycle, onReset, onSetAdornment,
                                     onResetSlot, signedIn,
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
      onFocus={onFocusSlot} onCycle={onCycle} onSetAdornment={onSetAdornment}
      onResetSlot={onResetSlot} />
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
                  disabled={!Object.keys(active).length}>Reset all</button>
        </div>
      </div>
      <div className="loadoutbody">
        <div className="loadoutgear">
          <div className="equipmentwindow">
            <div className="planslots left">{slots(left)}</div>
            <div className="planslots right">{slots(right)}</div>
          </div>
          <WornSets character={character} shortlist={shortlist} active={active} />
        </div>
        <ProjectedStats character={character} shortlist={shortlist} active={active}
                        statLabel={statLabel} statPct={statPct} />
      </div>
    </div>
  )
}
