/* Class identity: colors, archetype families, and role grouping.

   Two different groupings, both useful and both used:
   - FAMILY is EQ2's own archetype (fighter/priest/mage/scout). It drives
     COLOR, because four hues can be told apart and twenty-six cannot.
   - ROLE is the coach engine's grouping (tank/healer/utility/dps, mirroring
     backend/coach/descriptive.py ARCHETYPES). It drives FILTERING, because
     "show me the healers" is the question people actually ask.

   Color rules, and why they are what they are: the four family hues are EQ2's
   own — fighter blue, priest green, mage red, scout yellow — because a raider
   already knows them from the game and a parser that recolors them is asking
   people to learn a second language for the same fact. They live in tokens.css
   as --fam-*, not as hex here, because yellow cannot clear 3:1 on both the
   dark surface and the light parchment with one value: dark mode gets the
   bright variant, light mode the deep one.

   The per-class tints below are generated inside each family's hue band, but
   six or seven hues inside one band cannot pass a colorblind-separation check
   — so a class tint is never load-bearing. It only ever appears on a chip that
   also spells the class out, and each value clears ~2.8:1 on both themes so
   the dot is at least visible. Chart series get CHART_COLORS in fixed
   selection order instead, since two raiders of the same class would otherwise
   draw the same line. */

export const CLASS_FAMILY = {
  guardian: 'fighter', berserker: 'fighter', paladin: 'fighter',
  shadowknight: 'fighter', monk: 'fighter', bruiser: 'fighter',
  templar: 'priest', inquisitor: 'priest', warden: 'priest', fury: 'priest',
  mystic: 'priest', defiler: 'priest', channeler: 'priest',
  wizard: 'mage', warlock: 'mage', conjuror: 'mage', necromancer: 'mage',
  illusionist: 'mage', coercer: 'mage',
  assassin: 'scout', ranger: 'scout', brigand: 'scout', swashbuckler: 'scout',
  troubador: 'scout', dirge: 'scout', beastlord: 'scout',
}

/* Role -> classes, mirroring backend/coach/descriptive.py ARCHETYPES. The
   backend sends `archetype` per actor; this is the fallback and the filter
   order (tanks first, the way a raid frame reads). */
export const ROLES = ['tank', 'healer', 'dps', 'utility']
export const ROLE_LABEL = { tank: 'Tanks', healer: 'Healers', dps: 'DPS', utility: 'Utility' }
export const CLASS_ROLE = {
  // All six FIGHTERS are tanks — brawlers (monk/bruiser) included. See the
  // note on ARCHETYPES in backend/coach/descriptive.py.
  guardian: 'tank', berserker: 'tank', paladin: 'tank', shadowknight: 'tank',
  monk: 'tank', bruiser: 'tank',
  templar: 'healer', inquisitor: 'healer', warden: 'healer', fury: 'healer',
  mystic: 'healer', defiler: 'healer', channeler: 'healer',
  dirge: 'utility', troubador: 'utility', coercer: 'utility', illusionist: 'utility',
  assassin: 'dps', ranger: 'dps', swashbuckler: 'dps', brigand: 'dps',
  wizard: 'dps', warlock: 'dps', necromancer: 'dps', conjuror: 'dps',
  beastlord: 'dps',
}

/* EQ2's archetype colors, per theme (tokens.css) — the load-bearing identity
   color, used for the stripe beside every combatant name. */
export const FAMILY_COLOR = {
  fighter: 'var(--fam-fighter)', priest: 'var(--fam-priest)',
  mage: 'var(--fam-mage)', scout: 'var(--fam-scout)',
}

/* The same four as rgb triplets, for the translucent one (`barFill`). Solid
   uses keep `FAMILY_COLOR` — a plain colour needs no triplet. */
const FAMILY_RGB = {
  fighter: 'var(--fam-fighter-rgb)', priest: 'var(--fam-priest-rgb)',
  mage: 'var(--fam-mage-rgb)', scout: 'var(--fam-scout-rgb)',
}

/* Decorative per-class tint, always shown next to the class name. */
export const CLASS_COLOR = {
  // fighters — blue
  monk: '#10a6ad', paladin: '#00829d', guardian: '#3096cb',
  berserker: '#276cba', bruiser: '#6d84d2', shadowknight: '#6455b0',
  // priests — green
  fury: '#739425', warden: '#60a659', templar: '#228653', defiler: '#007757',
  inquisitor: '#1ea28f', mystic: '#008d89', channeler: '#31a4af',
  // mages — red
  wizard: '#d1453f', warlock: '#b53a34', conjuror: '#d9694f',
  necromancer: '#a83a4c', illusionist: '#c4506e', coercer: '#b04a68',
  // scouts — yellow
  assassin: '#b08618', ranger: '#ad8a1e', brigand: '#9c7a10',
  swashbuckler: '#a98f22', troubador: '#8f7614', dirge: '#a87d20',
  beastlord: '#94701a',
}

/* Chart series, assigned in fixed selection order and never cycled — cap the
   series count at CHART_COLORS.length and fold the rest into the table. */
export const CHART_COLORS = [
  '#448dd4', '#c85b32', '#029e72', '#9750a7',
  '#a88d10', '#655cb8', '#10a6ad', '#c04255',
]
/* Secondary encoding so the two closest chart hues stay separable for
   dichromat readers (the validator's floor band allows 6-8 dE only with it). */
export const CHART_DASH = ['', '', '5 3', '', '2 3', '8 3', '', '5 3 2 3']

export const classLabel = (cls) => (cls ? cls[0].toUpperCase() + cls.slice(1) : null)

/* What a raider calls the class in chat, for the places where the full word
   does not fit: a chip inside a table cell that is sharing a 380px column with
   a name, a clock and a mob. `Shadowknight` alone is wider than the name it is
   captioning there.

   Only the ones with real in-game shorthand are listed — a class not in this
   map keeps its full name rather than being truncated into something nobody
   says out loud, which is why `Templar`, `Warden`, `Fury`, `Mystic`, `Defiler`
   and `Bruiser` are absent. `classLabel` stays the full word everywhere the
   width is there, and the chip's tooltip always spells it out. */
export const CLASS_SHORT = {
  guardian: 'Guard', berserker: 'Zerker', paladin: 'Pally', shadowknight: 'SK',
  inquisitor: 'Inq', channeler: 'Chan',
  wizard: 'Wiz', warlock: 'Lock', conjuror: 'Conj', necromancer: 'Necro',
  illusionist: 'Illy', coercer: 'Coercer',
  assassin: 'Sin', brigand: 'Brig', swashbuckler: 'Swash', troubador: 'Troub',
  beastlord: 'BL',
}
export const classShort = (cls) => (cls ? CLASS_SHORT[cls] || classLabel(cls) : null)

/* The bar behind a meter row, in ONE place because two surfaces draw it (the
   dashboard meter and the mini parse) and a meter whose bars are two different
   weights on one screen reads as two different meters.

   It is deliberately faint. The bar is a LENGTH — you find the row by how far
   it reaches — and the tint is only there to say which archetype reached that
   far. At the fuller mix this used to be, the fill competed with the name and
   the rate sitting on top of it, which is the text the row is actually for.

   `rgba(triplet, 0.24)` AND NOT `color-mix(… 24%, transparent)`, which is what
   this said and which made every bar INVISIBLE in EQ2's in-game browser and in
   an OBS browser source. Both are embedded CEF builds years behind a current
   Chrome, `color-mix()` needs Chrome 111, and a value an engine cannot parse
   takes its whole declaration down with it — so the element kept its size and
   its position and simply had no background, on the two surfaces nobody can
   open devtools on. See the note by `--fam-*-rgb` in tokens.css. */
export const barFill = (cls) => {
  const rgb = FAMILY_RGB[CLASS_FAMILY[cls]]
  return rgb ? `rgba(${rgb}, 0.24)` : 'var(--bar-track)'
}
export const roleOf = (actor) => actor?.archetype || CLASS_ROLE[actor?.class] || null
export const classColor = (cls) => CLASS_COLOR[cls] || null
export const familyColor = (cls) => FAMILY_COLOR[CLASS_FAMILY[cls]] || null

/* Short confidence wording for the tooltip on an inferred class. */
export function classTitle(actor) {
  if (!actor?.class) return 'Class unknown — too few recognizable abilities in this log'
  const label = classLabel(actor.class)
  if (actor.class_source === 'census') return `${label} — from the character's Census profile`
  const pct = actor.class_confidence != null ? ` (${Math.round(actor.class_confidence * 100)}% of matched abilities)` : ''
  return `${label} — inferred from the abilities they cast${pct}`
}
