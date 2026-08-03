import { classColor, classLabel, classTitle, familyColor } from '../lib/classes.js'

/* Class chip: the tint is decorative, the word is the encoding — they always
   ship together, so identity never rests on color alone. */
export function ClassChip({ actor }) {
  if (!actor?.class) return null
  const inferred = actor.class_source !== 'census'
  const weak = inferred && (actor.class_confidence ?? 1) < 0.6
  return (
    <span className="classchip" title={classTitle(actor)}>
      <i style={{ background: classColor(actor.class) }} />
      {classLabel(actor.class)}
      {weak && <span className="q">?</span>}
    </span>
  )
}

/* Combatant name for a table cell: family-colored stripe, name, class chip. */
export function ActorName({ actor, badge, children }) {
  const stripe = familyColor(actor.class)
  return (
    <span className="actorname">
      <i className="famstripe" style={stripe ? { background: stripe } : undefined} />
      <span className="n">{actor.name}</span>
      <ClassChip actor={actor} />
      {badge}
      {children}
    </span>
  )
}
