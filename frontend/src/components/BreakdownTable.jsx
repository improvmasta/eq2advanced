import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import SortableTable from './SortableTable.jsx'
import { fmt } from '../lib/api.js'
import { useCanCurate } from '../lib/session.jsx'
import { MELEE_BUCKETS, PET_COMMANDED, critPct } from '../lib/stats.js'

/* The ACT-style ability breakdown — one combatant's actual parse, the table
   people screenshot and line up next to somebody else's. One implementation
   serves the raid page's drilldown (ActorPanel), the raid page's checked-
   raiders comparison (ComparePanel) and the Compare page, so a parse looks
   the same everywhere it appears. */

/* No "All" view: the table is per-kind, Damage first, wards folded into Heals
   (Kind still marks ward rows so they're tellable apart). */
export const KIND_FILTERS = [
  { key: 'damage', label: 'Damage', kinds: ['damage'] },
  { key: 'heal', label: 'Heals', kinds: ['heal', 'ward'] },
  { key: 'power', label: 'Power', kinds: ['power'] },
  { key: 'threat', label: 'Threat', kinds: ['threat', 'detaunt'] },
  { key: 'cure', label: 'Cures', kinds: ['cure'] },
  { key: 'self', label: 'Self', kinds: ['self'] },
]

/* The per-second column is the same column on every tab, and it is NOT always
   DPS — a healer's breakdown headed "DPS" is reading the wrong word off a
   right number. ACT's own names where they exist (EncDPS/EncHPS); the rest
   follow the same shape. */
const RATE_LABEL = {
  damage: 'DPS', self: 'DPS', heal: 'HPS', ward: 'HPS',
  power: 'PPS', threat: 'TPS', detaunt: 'TPS',
}
export const rateLabel = (kinds) => RATE_LABEL[(kinds || [])[0]] || 'Rate'

/* An ability that lands as more than one kind (lifetaps: damage + heal) shows
   its off-kind components condensed behind the row's … expander. */
const EXTRA_KINDS = new Set(['damage', 'heal', 'ward', 'power'])

export const PET_KINDS = new Set(['own_pet', 'swarm_pet', 'named_pet'])
const AVOID_COLS = ['misses', 'parries', 'ripostes', 'dodges', 'blocks', 'resists']

/* A row that neither landed anything nor moved a number is a cast the parse
   can't say anything about (summons, stances, the buff half of an ability).
   Fully-absorbed rows still land (hits > 0, total 0) and stay. */
const didSomething = (r) => (r.total || 0) !== 0 || (r.hits || 0) !== 0

/* Which kind tabs a given parse can actually answer, in KIND_FILTERS order.
   For a surface that draws the tabs per parse rather than per page (the
   Compare page): a fury's column has no Threat tab to click. */
export const availKinds = (actorRows) => KIND_FILTERS.filter(
  (f) => actorRows.some((r) => f.kinds.includes(r.kind) && didSomething(r)))

/* Set-in-stone pet kits (the game hasn't changed): each pet ability belongs to
   one summoned-pet archetype, so a pet whose identity the log never states is
   attributed by what it casts. Ground truth, not inference — Lindsay ran one
   fight per pet on 2026-08-11 (session 127) casting nothing of his own, so
   each fight is one kit with nothing else in it. The backend's
   CURATED_PET_ABILITIES (census/catalog.py) is the flat superset. */
const PET_KITS = [
  { label: 'Mage Pet', melee: 'piercing', abilities: [   // necromancer
    'Grim Wave', 'Grim Embrace', 'Grim Devastation', 'Grim Lifetap',
    'Grim Bolt', 'Grim Distortion'] },
  { label: 'Scout Pet', melee: 'piercing', abilities: [  // necromancer
    'Throat Gash', 'Poisoned Spike', 'Shadowy Garrote', 'Unseen Blade',
    'Shadestrike', 'Acidity'] },
  { label: 'Fighter Pet', melee: 'crushing', abilities: [  // necromancer
    'Graven Strike', 'Graven Scream', 'Graven Breath', 'Graven Frenzy',
    'Graven Assault', 'Graven Vanquishing'] },
  /* Conjuror. Lindsay gave the complement — the conjuror's OWN book — and
     Census agrees with all of it, so what is left over is the pets. Fire and
     air are an either/or, which is what let the two kits fall out of the raid
     data before he wrote the list: they anti-correlate at ≤0.15 over 419
     windows. Earth is nobody's raid pet, so its kit is only the two abilities
     that have ever shown up and its swing has never been seen. */
  { label: 'Mage Pet', melee: 'crushing', abilities: [
    'Searing Flames', 'Shocking Flames', 'Igneous Flames', 'Wave of Flames',
    'Sphere of Flames', 'Storm of Flames'] },
  { label: 'Scout Pet', melee: 'piercing', abilities: [
    'Aery Whip', 'Wisp Blade', 'Storm Surge', 'Furystorm', 'Galestorm',
    'Thunderous Attack'] },
  { label: 'Fighter Pet', melee: null, abilities: [
    'Telluric Bash', 'Telluric Retaliation'] },
  /* Illusionist Personal Reflection, from the two illusionists who uploaded
     their OWN parses (their pet splits off as `own_pet`). Deliberately absent
     from the backend's curated list: that list is what promotes an ability
     hiding under a PLAYER's name to a pet cast, and we only combine an
     illusionist's pet where we can actually see it — which takes a parse from
     that illusionist. Anyone else's stays part of their own line. */
  { label: 'Personal Reflection', melee: 'mental', abilities: [
    'Phantasmal Shock', 'Overwhelming Silence', 'Headache', 'Confusion',
    'Color Shower', 'Stunning Array', 'Lock Mind', 'Illusory Taunt'] },
]

/* The kit is what a pet is known BY, so it carries the swing too: a
   necromancer's mage pet pierces and a conjuror's crushes, which one archetype
   label cannot hold. Measured within a single conjuror rather than across
   several — the same player melees piercing with air out and crushing with
   fire out, so it is the pet's weapon and not the raider's. */
const PET_KIT_OF = Object.fromEntries(
  PET_KITS.flatMap((k) => k.abilities.map((a) => [a, k])))
const PET_ARCHETYPE = Object.fromEntries(
  PET_KITS.flatMap((k) => k.abilities.map((a) => [a, k.label])))

/* Cast by whichever pet is out, so they name no archetype — the STANCE is why
   (Lindsay, 2026-08-11): defensive casts Shout and Grisly Feedback, offensive
   casts Clawing of the Soul. All three fired for all three of his pets, which
   is exactly why the old map — Shout under Scout, Grisly Feedback under Mage —
   split single-pet fights across two rows. They join the archetype that IS
   out, and fall back to a bare "Pet" only when the parse shows more than one. */
const PET_SHARED = new Set(['Shout', 'Grisly Feedback', 'Clawing of the Soul'])

/* "Bobby's blighted horde" -> "blighted horde"; own pets share the owner's
   bare name, so they read as just "Pet". */
function petShort(r) {
  if (r.source_kind === 'own_pet') return 'Pet'
  return r.source_name.includes("'s ")
    ? r.source_name.slice(r.source_name.indexOf("'s ") + 3)
    : r.source_name
}

/* An auto-attack row's damage types, biggest first. */
export function meleeDtypes(r) {
  if (!MELEE_BUCKETS.has(r.ability) || !r.dtypes) return []
  return Object.entries(r.dtypes).filter(([, amt]) => amt > 0)
    .sort((a, b) => b[1] - a[1])
}

/* ACT names an auto-attack row by what the swing LANDED as, and on TLE that is
   not the same thing as the weapon: infusions move a player's melee onto
   disease, heat, whatever they infused. So the damage type IS the label —
   `piercing (melee)` — and a row carrying more than one keeps the plain name
   and expands into them. */
const meleeLabel = (r) => {
  const d = meleeDtypes(r)
  return d.length === 1 ? `${d[0][0]} ${r.ability}` : r.ability
}

/* "Bobby's blighted horde" + "Grave Decay" -> "blighted horde's Grave Decay",
   the way ACT prints pet rows inside the owner's breakdown. The bare-name own
   pet is the exception: ACT prints its abilities with no prefix at all
   ("Poisoned Spike", not "Pet's Poisoned Spike"), and the pet badge already
   says whose swing it was. */
export function abilityLabel(r) {
  const base = meleeLabel(r)
  if (!PET_KINDS.has(r.source_kind)) return base
  const short = petShort(r)
  if (r.source_kind === 'own_pet') return base
  if (r.__dtype || r.ability.startsWith('(')) return `${short} ${base}`
  return `${short}'s ${r.ability}`
}

/* Which pet a parse actually shows, read off its kit abilities. With exactly
   one pet out, everything kit-blind in the same parse — the stances, the
   swings — belongs to it; with two, nothing can say which. */
export function activeKit(rows) {
  const seen = new Set()
  for (const r of rows) {
    if (!PET_KINDS.has(r.source_kind) && !r.via_pet) continue
    const kit = PET_KIT_OF[r.ability]
    if (kit) seen.add(kit)
  }
  return seen.size === 1 ? [...seen][0] : null
}

/* Swarm/named pets are identified by name; a same-name pet by its kit. */
function petGroupLabel(r, only) {
  if (r.source_kind === 'swarm_pet' || r.source_kind === 'named_pet') return petShort(r)
  const kit = PET_ARCHETYPE[r.ability]
  if (kit) return kit
  if (only && (PET_SHARED.has(r.ability) || MELEE_BUCKETS.has(r.ability))) return only
  if (MELEE_BUCKETS.has(r.ability)) return 'Pet Autoattack'
  return 'Pet'
}

function addInto(g, r) {
  for (const c of ['casts', 'hits', 'crits', 'zero_hits', 'total', 'swings', 'presses', 'reflects', ...AVOID_COLS]) {
    g[c] = (g[c] || 0) + (r[c] || 0)
  }
  g.min = r.min != null ? (g.min != null ? Math.min(g.min, r.min) : r.min) : g.min
  g.max = r.max != null ? (g.max != null ? Math.max(g.max, r.max) : r.max) : g.max
  if (r.dtypes) {
    g.dtypes = g.dtypes || {}
    for (const [t, amt] of Object.entries(r.dtypes)) g.dtypes[t] = (g.dtypes[t] || 0) + amt
  }
  g.to_hit_pct = g.swings ? Math.round((10000 * g.hits) / g.swings) / 100 : null
}

/* Every pet-sourced row (and pet casts conflated under the player's name)
   condenses into one Scout/Mage/Fighter Pet row per kind; the members stay
   attached for the … expander. */
function groupPets(rows) {
  const out = []
  const groups = new Map()
  const kit = activeKit(rows)
  for (const r of rows) {
    if ((!PET_KINDS.has(r.source_kind) && !r.via_pet) || PET_COMMANDED.has(r.ability)) {
      out.push(r); continue
    }
    const label = petGroupLabel(r, kit?.label)
    const key = `petgroup:${label}|${r.kind}`
    let g = groups.get(key)
    if (g == null) {
      g = {
        ...r,
        ability: label, gkey: key, members: [],
        casts: 0, hits: 0, crits: 0, zero_hits: 0, total: 0,
        min: null, max: null, median: null, avg_delay_s: null,
        presses: 0, press_delay_s: null,
        dtypes: null, swings: 0, to_hit_pct: null,
      }
      for (const c of AVOID_COLS.concat('reflects')) g[c] = 0
      groups.set(key, g)
      out.push(g)
    }
    addInto(g, r)
    g.members.push({ ...r, __sub: true })
  }
  for (const g of groups.values()) g.members.sort((a, b) => (b.total || 0) - (a.total || 0))
  return out
}

// every row this combatant owns, all kinds (phantom fully-avoided rows dropped)
export function actorRowsOf(abilities, actorKey) {
  return (abilities || []).filter((r) =>
    (r.source_key === actorKey || r.rollup_key === actorKey)
    && !(r.total === 0 && r.hits > 0 && r.max === null && r.kind === 'damage'
         && r.swings === r.hits))
}

/* The rows of one kind tab: pet grouping on request, and off-kind components
   of the same ability (lifetap heals under a damage row and vice versa) riding
   along as expandable sub-rows. */
export function breakdownRows(actorRows, kinds, combinePets) {
  const inTab = actorRows.filter((r) => kinds.includes(r.kind) && didSomething(r))
  const grouped = combinePets ? groupPets(inTab) : inTab.slice()
  const kit = activeKit(actorRows)
  for (const r of grouped) {
    if (r.members) continue
    const extras = actorRows.filter((x) =>
      x.ability === r.ability && x.source_key === r.source_key
      && !kinds.includes(x.kind) && EXTRA_KINDS.has(x.kind) && didSomething(x))
    if (extras.length) {
      r.extras = extras.map((x) => ({ ...x, __sub: true }))
        .sort((a, b) => (b.total || 0) - (a.total || 0))
    }
    r.dtypeRows = dtypeRowsOf(r, kit)
  }
  return grouped
}

/* A swing chain that landed as more than one damage type, split out under its
   own row. Display only, and the counts are deliberately blank: the log gives
   a per-type TOTAL and nothing else, so hits, crit rate and delay belong to
   the chain as a whole. Splitting them across the types would halve a
   dual-wielder's swing count and double their AvgDelay.

   Where one of those types is the swing of the pet that is out, the sub-row
   says so. It is the one case a conflated pet CAN be picked apart — the pet is
   in the player's row because it shares their name, but not their weapon. */
function dtypeRowsOf(r, kit) {
  const d = meleeDtypes(r)
  if (d.length < 2) return null
  const petType = kit && !PET_KINDS.has(r.source_kind) ? kit.melee : null
  return d.map(([dt, amt]) => ({
    __sub: true, __dtype: true, __petdtype: dt === petType,
    source_key: r.source_key, source_name: r.source_name,
    source_kind: r.source_kind, rollup_key: r.rollup_key,
    kind: r.kind, ability: `${dt} ${r.ability}`, total: amt,
    hits: null, crits: null, min: null, max: null, median: null,
    avg_delay_s: null, press_delay_s: null, to_hit_pct: null, swings: null,
  }))
}

export const rowKeyOf = (r) => (r.__all ? '__all'
  : (r.__sub ? 'sub:' : '') + (r.gkey || `${r.source_key}:${r.ability}:${r.kind}`))

/* Cross-parse hover: side-by-side tables that opt in (linkHover) share one
   hovered-ability channel, so pointing at Poisoned Spike in one parse lights
   it up in every parse that has it. Matched by displayed label + kind — the
   label is what makes two rows "the same spell" to the reader, and it keeps
   a lifetap's damage row from lighting its heal row. Module state, not
   context: the tables are cousins across two different pages. */
const hoverListeners = new Set()
let hoverNow = null
const setHoverNow = (k) => {
  if (k === hoverNow) return
  hoverNow = k
  for (const fn of hoverListeners) fn(k)
}
const hoverKeyOf = (r) => `${r.kind}:${r.gkey ? r.ability : abilityLabel(r)}`

/* One compact line of headline numbers above a parse — the ACT title bar's
   job. Works for a single combatant's actor row or a summed raid row, with
   `derived` the matching damageDerived rollup. */
export function ParseStrip({ actor, derived, kind, duration, combat }) {
  if (!actor) return null
  const healed = (actor.heals || 0) + (actor.wards_absorbed || 0)
  const items = []
  if (combat) items.push(['Combat', fmt.dur(duration)])
  if (kind === 'damage') {
    items.push(
      ['DPS', actor.damage ? fmt.num2((actor.damage || 0) / duration) : '—'],
      ['Damage', fmt.num(actor.damage || 0)])
    const crit = critPct(derived)
    if (crit != null) items.push(['Crit', `${Math.round(crit)}%`])
    if (actor.deaths) items.push(['Deaths', actor.deaths])
  } else {
    items.push(
      ['HPS', healed ? fmt.num2(healed / duration) : '—'],
      ['Healed', fmt.num(healed)])
    if (actor.cure_count) items.push(['Cures', actor.cure_count])
    if (actor.power_fed) items.push(['Power repl', fmt.num(actor.power_fed)])
  }
  return (
    <div className="statstrip">
      {items.map(([k, v]) => <span key={k}><b>{v}</b> {k}</span>)}
    </div>
  )
}

/* Where the damage came from and what the swings threw away — counts, not
   advice. Reads the DAMAGE rows of a combatant's `actorRowsOf` output
   whichever tab is showing, because it describes the parse rather than the
   current view; the caller draws it on the damage tab, where it is about what
   the reader is looking at. Silent for a parse with no damage in it.

   It lives here with ParseStrip so the drilldown and the Compare page put the
   same line above the same table — one parse, one look, wherever it appears. */
export function CompositionStrip({ rows }) {
  const comp = { cast: 0, auto: 0, proc: 0, pet: 0, total: 0 }
  const waste = { resists: 0, zero: 0 }
  for (const r of rows) {
    if (r.kind !== 'damage') continue
    const amt = r.total || 0
    comp.total += amt
    if ((PET_KINDS.has(r.source_kind) || r.via_pet) && !PET_COMMANDED.has(r.ability)) {
      comp.pet += amt
    }
    if (MELEE_BUCKETS.has(r.ability)) comp.auto += amt
    else if (r.proc) comp.proc += amt
    else comp.cast += amt
    waste.resists += r.resists || 0
    waste.zero += r.zero_hits || 0
  }
  if (comp.total <= 0) return null
  const pct = (v) => `${Math.round((100 * v) / comp.total)}%`
  return (
    <div className="statstrip">
      <span><b>{pct(comp.cast)}</b> cast</span>
      <span><b>{pct(comp.auto)}</b> autoattack</span>
      {comp.proc > 0 && <span><b>{pct(comp.proc)}</b> procs</span>}
      {comp.pet > 0 && <span><b>{pct(comp.pet)}</b> pets</span>}
      {waste.resists > 0 && <span><b>{fmt.num(waste.resists)}</b> resisted</span>}
      {waste.zero > 0 && <span><b>{fmt.num(waste.zero)}</b> absorbed</span>}
    </div>
  )
}

/* The table itself. `rows` come from breakdownRows; the pinned "All" line
   (ACT's summed first row) is built here. Share and ToHit are real columns but
   comparison surfaces hide them by default (defaultHidden) — the Columns menu
   brings them back. */
export default function BreakdownTable({
  rows, kinds, duration, prefsKey, defaultHidden,
  checkable, checkedKeys, onCheck, linkHover, wrapClass, fitViewport, syncScroll,
}) {
  const [open, setOpen] = useState({})   // expandable row key -> open
  const canCurate = useCanCurate()

  const [hover, setHover] = useState(null)
  useEffect(() => {
    if (!linkHover) return
    hoverListeners.add(setHover)
    return () => {
      hoverListeners.delete(setHover)
      if (!hoverListeners.size) hoverNow = null
    }
  }, [linkHover])

  const tabTotal = rows.reduce((s, r) => s + (r.total || 0), 0)

  /* ACT's "All" line: the whole tab summed into one pinned row, so the table
     answers "what did this add up to?" before any scrolling. Delays are the
     one thing a sum can't carry per-row, so they're the fight-wide gap
     between landings / activations. */
  const allRow = useMemo(() => {
    if (!rows.length) return null
    const a = {
      __all: true, ability: 'All', kind: kinds[0], source_kind: 'player',
      casts: 0, hits: 0, crits: 0, zero_hits: 0, total: 0, swings: 0,
      presses: 0, min: null, max: null, avg_delay_s: null, press_delay_s: null,
      median: null,
    }
    for (const r of rows) addInto(a, r)
    a.avg_delay_s = a.hits > 1 ? duration / a.hits : null
    a.press_delay_s = a.presses > 1 ? duration / a.presses : null
    return a
  }, [rows, duration])

  const toggle = (k) => setOpen((o) => ({ ...o, [k]: !o[k] }))
  /* Pet members, off-kind components and damage-type splits all hang off the
     same … expander — a row has at most one KIND of child in practice, and a
     second control on the name cell would cost more than it explains. */
  const kidsOf = (r) => (r.members
    || (r.extras || r.dtypeRows ? [...(r.extras || []), ...(r.dtypeRows || [])] : null))
  const childrenOf = (r) => {
    const kids = kidsOf(r)
    return kids && open[rowKeyOf(r)] ? kids : []
  }

  const cols = [
    {
      key: 'ability', label: 'Type', align: 'l', fixed: true,
      render: (r) => {
        const k = rowKeyOf(r)
        const kids = kidsOf(r)
        return (
          <span className="name">
            {/* The one part of the row that is allowed to be shortened, and
                only when the table cannot fit (see useFrozen): the badges,
                the ⚙ and the expander beside it are controls and must stay
                whole. `title` is what makes an ellipsis readable — the full
                name is one hover away, always, shortened or not. */}
            <span className="abname" title={r.gkey ? r.ability : abilityLabel(r)}>
              {r.gkey ? r.ability : abilityLabel(r)}
            </span>
            {/* A commanded attack wears no pet badge: the pet swung it, but
                the parse is about who pressed it. */}
            {(r.gkey || PET_KINDS.has(r.source_kind)) && !PET_COMMANDED.has(r.ability)
              && <span className="badge pet">pet</span>}
            {!r.gkey && r.via_pet && !PET_COMMANDED.has(r.ability)
              && <span className="badge pet">pet cast</span>}
            {r.__petdtype && (
              <span
                className="badge pet"
                title="The pet out in this fight swings for this damage type and the player does not"
              >
                pet swing
              </span>
            )}
            {!r.gkey && r.proc && (
              <span
                className="badge proc"
                title={`Fires on its own, not something cast${r.proc_why ? ` — ${r.proc_why}` : ''}`}
              >
                proc
              </span>
            )}
            {r.kind !== kinds[0] && <span className="muted"> {r.kind}</span>}
            {/* Wrong badge? Fix it here. The place you NOTICE that `Ice Comet`
                is not a pet ability is a parse, not an admin page, and making
                someone go find it by name is how a wrong label survives. Only
                a curator sees it, and the page it opens checks that itself. */}
            {canCurate && !r.gkey && r.ability && (
              <Link
                className="fixability"
                to={`/admin/abilities?q=${encodeURIComponent(r.ability)}`}
                onClick={(e) => e.stopPropagation()}
                title={`Look up "${r.ability}" — is it a pet's, a proc, and what grants it?`}
                aria-label={`Look up ${r.ability} in the Abilities console`}
              >⚙</Link>
            )}
            {!r.__sub && kids && (
              <button
                className="expandcol"
                onClick={(e) => { e.stopPropagation(); toggle(k) }}
                title={open[k] ? 'Collapse'
                  : r.members ? 'Expand pet abilities'
                  : r.extras ? 'Expand components' : 'Expand damage types'}
              >{open[k] ? '▾' : '…'}</button>
            )}
          </span>
        )
      },
      sortValue: (r) => (r.gkey ? r.ability : abilityLabel(r)),
    },
    {
      key: 'encdps', label: rateLabel(kinds),
      render: (r) => (r.total ? fmt.num2(r.total / duration) : '—'),
      sortValue: (r) => (r.total || 0) / duration,
    },
    // ACT's second column — the totals people actually read off a screenshot
    {
      key: 'total', label: kinds[0] === 'damage' ? 'Damage' : 'Total',
      render: (r) => (r.total ? fmt.num(r.total) : '—'),
      sortValue: (r) => r.total || 0,
    },
    {
      key: 'share', label: 'Share',
      render: (r) => (!r.__sub && r.total && tabTotal
        ? `${((r.total / tabTotal) * 100).toFixed(1)}%` : ''),
      sortValue: (r) => r.total || 0,
    },
    { key: 'hits', label: 'Hits' },
    {
      key: 'avg', label: 'Average',
      render: (r) => (r.hits ? fmt.num(r.total / r.hits) : '—'),
      sortValue: (r) => (r.hits ? r.total / r.hits : null),
    },
    {
      key: 'median', label: 'Median',
      render: (r) => (r.median != null ? fmt.num(r.median) : '—'),
      sortValue: (r) => r.median ?? null,
    },
    { key: 'min', label: 'MinHit', format: fmt.num },
    { key: 'max', label: 'MaxHit', format: fmt.num },
    {
      key: 'crit', label: 'Crit %',
      render: (r) => (r.hits ? `${Math.round((r.crits / r.hits) * 100)}%` : ''),
      sortValue: (r) => (r.hits ? r.crits / r.hits : null),
    },
    {
      key: 'avg_delay_s', label: 'AvgDelay',
      render: (r) => (r.avg_delay_s != null ? r.avg_delay_s.toFixed(2) : '—'),
    },
    /* Between LANDINGS (AvgDelay) vs between ACTIVATIONS: for a DoT the first
       is its tick rate and the second is how often it was recast, which is
       the number you can actually do something about. */
    {
      key: 'press_delay_s', label: 'AvgDelay adj',
      render: (r) => (r.press_delay_s != null
        ? <span title={`${r.presses} activation${r.presses === 1 ? '' : 's'} of ${r.hits} landings`}>
            {r.press_delay_s.toFixed(2)}
          </span>
        : '—'),
    },
    {
      key: 'to_hit_pct', label: 'ToHit',
      render: (r) => (r.to_hit_pct != null ? `${r.to_hit_pct.toFixed(1)}%` : '—'),
    },
  ]

  return (
    <SortableTable
      columns={cols}
      rows={rows}
      topRows={allRow ? [allRow] : []}
      defaultSort={{ key: 'encdps', dir: 'desc' }}
      prefsKey={prefsKey}
      defaultHidden={defaultHidden}
      rowKey={rowKeyOf}
      childrenOf={childrenOf}
      rowClass={(r) => [
        r.__sub ? 'subrow' : r.__all ? 'allrow' : '',
        linkHover && hover && hoverKeyOf(r) === hover ? 'rowlink' : '',
      ].filter(Boolean).join(' ')}
      onRowHover={linkHover ? (r) => setHoverNow(r ? hoverKeyOf(r) : null) : undefined}
      wrapClass={wrapClass}
      fitViewport={fitViewport}
      /* A parse is ALWAYS a frozen table, wherever it is rendered: the
         ability name is what every other cell on the row is about, and the
         header is what tells you which number you are looking at. */
      frozen
      syncScroll={syncScroll}
      checkable={checkable}
      checkedKeys={checkedKeys}
      onCheck={onCheck}
    />
  )
}
