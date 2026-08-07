import { classColor, classLabel, classShort, classTitle, familyColor } from '../lib/classes.js'

/* Class chip: the tint is decorative, the word is the encoding — they always
   ship together, so identity never rests on color alone. */
export function ClassChip({ actor, short }) {
  /* Nothing in the log proved a person was behind this name — a bare-named
     summoned pet fights and casts exactly like a raider. Say so, rather than
     leave a blank that reads as a class we have not got round to guessing. */
  if (actor?.class_source === 'unidentified') {
    return (
      <span
        className="classchip unid"
        title="No chat, roster, loot or rez line anywhere in this log — probably a summoned pet, not a raider"
      >unidentified</span>
    )
  }
  if (!actor?.class) return null
  const inferred = actor.class_source !== 'census'
  const weak = inferred && (actor.class_confidence ?? 1) < 0.6
  /* `short` is for the tight columns (the tank picker, the death list). The
     tooltip is `classTitle`, which names the class in full either way — an
     abbreviation is only allowed to be one because the full word is a hover
     away. */
  return (
    <span className="classchip" title={classTitle(actor)}>
      <i style={{ background: classColor(actor.class) }} />
      {short ? classShort(actor.class) : classLabel(actor.class)}
      {weak && <span className="q">?</span>}
    </span>
  )
}

/* Who a raider is, next to their name when the page is about THEM: the class,
   then the level and guild Census already cached for the class lookup
   (`census/roster.py` pays for the whole character doc). Both of those are
   Census's answer for NOW, not for the night — nothing dates them the way the
   log dates a class — so they caption the name and never feed a number on the
   page. `compact` drops the guild for the places that name several raiders on
   one line. Absent facts simply do not render: a raider Census has never
   resolved is not level 0. */
export function ActorFacts({ actor, compact = false }) {
  if (!actor) return null
  return (
    <span className="actorfacts">
      <ClassChip actor={actor} />
      {actor.level ? (
        <span className="lvl" title="Level, from Census — their level now, not on the night">
          L{actor.level}
        </span>
      ) : null}
      {!compact && actor.guild ? (
        <span className="badge guild" title="Guild, from Census — where they are now">
          {actor.guild}
        </span>
      ) : null}
    </span>
  )
}

/* Combatant name for a table cell: family-colored stripe, name, class chip. */
export function ActorName({ actor, badge, children, short }) {
  const stripe = familyColor(actor.class)
  return (
    <span className="actorname">
      <i className="famstripe" style={stripe ? { background: stripe } : undefined} />
      <span className="n">{actor.name}</span>
      <ClassChip actor={actor} short={short} />
      {badge}
      {children}
    </span>
  )
}
