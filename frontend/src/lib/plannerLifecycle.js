/* Pure identity/navigation helpers for the Planner page lifecycle.

   The API supplies identity. React must never rebuild a storage key from a
   Census id or presentation label, because provider fallback would split one
   public character into two folders. */

export function lookupName(value, world = 'Wuoshi') {
  const clean = String(value || '').trim().replace(/\s+/g, ' ')
  const match = clean.match(/\s*\(([^()]*)\)\s*$/)
  return match && (!world || match[1].toLowerCase() === String(world).toLowerCase())
    ? clean.slice(0, match.index).trim() : clean
}

export function canonicalPlannerKey(world, name) {
  const cleanWorld = String(world || 'Wuoshi').trim().toLowerCase()
  const cleanName = lookupName(name, world).trim().toLowerCase()
  return cleanWorld && cleanName ? `${cleanWorld}:${cleanName}` : ''
}

export function ownerOfSummary(summary) {
  const character = summary?.character
  if (!character?.planner_key || !character?.lookup_name || !character?.class) return null
  const world = character.world || 'Wuoshi'
  const displayName = character.display_name || character.name || character.lookup_name
  const worldKey = String(world).toLowerCase()
  return {
    key: character.planner_key,
    lookup_name: character.lookup_name,
    lookupName: character.lookup_name,
    display_name: displayName,
    name: displayName,
    className: String(character.class).toLowerCase(),
    world,
    censusId: character.census_id ?? null,
    legacyKeys: [...new Set([
      character.census_id ? `${worldKey}:${character.census_id}` : '',
      character.name ? `${worldKey}:${String(character.name).trim().toLowerCase()}` : '',
      character.display_name
        ? `${worldKey}:${String(character.display_name).trim().toLowerCase()}` : '',
    ].filter((key) => key && key !== character.planner_key))],
  }
}

export function canonicalRecentCharacter(row) {
  if (!row) return null
  const world = row.world || String(row.key || '').split(':', 1)[0] || 'Wuoshi'
  const lookup = row.lookup_name || row.lookupName || lookupName(
    row.display_name || row.name, world)
  const key = canonicalPlannerKey(world, lookup)
  if (!key || !lookup) return null
  return {
    ...row, key, world,
    lookup_name: lookup, lookupName: lookup,
    display_name: row.display_name || row.name || `${lookup} (${world})`,
    name: row.display_name || row.name || `${lookup} (${world})`,
  }
}

/* Facts follow the newest timestamp; `saved` is an independent durable flag
   and cannot be erased by an older recent-search copy arriving later. */
export function mergeRecentCharacters(...groups) {
  const byKey = new Map()
  groups.flat().filter(Boolean).forEach((raw) => {
    const row = canonicalRecentCharacter(raw)
    if (!row) return
    const previous = byKey.get(row.key)
    if (!previous) {
      byKey.set(row.key, row)
      return
    }
    const previousTs = Number(previous.updated_ts || 0)
    const rowTs = Number(row.updated_ts || 0)
    const freshest = rowTs >= previousTs ? { ...previous, ...row } : { ...row, ...previous }
    freshest.saved = Boolean(previous.saved || row.saved)
    freshest.updated_ts = Math.max(previousTs, rowTs)
    byKey.set(row.key, freshest)
  })
  return [...byKey.values()].sort((a, b) => (
    Number(b.updated_ts || 0) - Number(a.updated_ts || 0)
    || a.name.localeCompare(b.name)
  ))
}

export function chooseAccountCharacter(characters, rememberedId) {
  const rows = [...(characters || [])].sort((a, b) => a.name.localeCompare(b.name))
  const remembered = rows.find((row) => String(row.id) === String(rememberedId || ''))
  return remembered ? String(remembered.id) : rows[0] ? String(rows[0].id) : ''
}

/* A character query owns the display while it exists. Until its matching
   response arrives, showing an account character or the preceding lookup
   under that URL would be lying about which record is on screen. */
export function characterForRequest(queryName, lookedUp, accountCharacter) {
  const query = lookupName(queryName)
  if (!query) return accountCharacter || null
  const responseName = lookedUp?.character?.lookup_name || ''
  return responseName.toLowerCase() === query.toLowerCase() ? lookedUp : null
}
