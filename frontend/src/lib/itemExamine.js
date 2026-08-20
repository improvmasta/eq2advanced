/* A set threshold normally leads with its flat stats and puts the named Focus
   effect beneath them. Some real tiers are proc-only, though: leaving their
   stat headline empty produced a bare `(2)` followed by what looked like a
   detached bullet. Promote the first available content to the threshold line
   so every tier reads as one complete statement. */
export function setBonusPresentation(bonus = {}) {
  const stats = (bonus.stat_lines || []).filter(Boolean)
  const descriptions = (bonus.descriptions || []).filter(Boolean)
  const effect = bonus.effect || ''
  if (stats.length) {
    return {
      headline: stats.join(', '),
      details: [...(effect ? [effect] : []), ...descriptions],
    }
  }
  if (effect) return { headline: effect, details: descriptions }
  return { headline: descriptions[0] || '', details: descriptions.slice(1) }
}
