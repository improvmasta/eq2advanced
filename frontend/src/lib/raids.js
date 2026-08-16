/* Raid is a fact about the CONTENT, not the crowd standing nearby. The backend
   classifies a raid instance outright and promotes a mixed public zone only
   for its explicit raid target. The roster threshold remains a brief fallback
   while an upgraded database's startup relink fills the new field. */
export const RAID_MIN_RAIDERS = 7

export const isRaid = (r) => r?.is_raid == null
  ? (r?.raider_count || 0) >= RAID_MIN_RAIDERS
  : !!r.is_raid

export const zoneName = (r, fallback = 'Unknown zone') => (
  r?.display_zone || r?.zone || fallback
)

/* What a run is CALLED. Usually its zone, because in an instance the zone is
   the event — "The Emerald Halls" books a night, names it and is what anybody
   asks about.

   A public zone is not that. It is a place several guilds pass through, so
   "Rivervale" says only where somebody was standing, and four visits to a
   halfling town read as four identical rows. What happened there was the
   Avatar of Mischief, so it is what the run wears. The backend decides WHEN
   this applies (`zoneruns_api._headline_named`: a public zone off the wiki's
   reference data, and exactly one distinct named) — this only formats it, so
   that the list, the raid page and anything later all say it the same way. */
export const runLabel = (r, fallback = 'Unknown zone') => {
  const zone = zoneName(r, fallback)
  return r?.headline_named ? `${zone} - ${r.headline_named}` : zone
}

/* eq2lexicon's raid pages, which are the strategy half of what this site's
   notes are the other half of: a zone is `/raids/<zone>` and a named inside it
   is `/raids/<zone>/<named>`. Deep-linking there rather than restating any of
   it is the point — the note says what happened to US on that pull, the
   lexicon says what the encounter does.

   `away` is not decoration: eq2lexicon sends `X-Frame-Options: DENY` to every
   origin (see App.jsx), so every one of these opens in a new tab. */
export const LEXICON = 'https://wuoshi.eq2lexicon.com'

/* Character names belong to the planner now: it can load Census, fall back to
   Lexicon, and immediately put the toon beside candidate gear. Keep this
   helper beside the remaining EQ2 links so every player-name surface builds
   the same shareable internal destination. */
export const plannerCharacter = (name) => name
  ? `/plan?character=${encodeURIComponent(name)}`
  : null

export const lexiconGuild = (name) => name
  ? `${LEXICON}/guild/${encodeURIComponent(name)}`
  : null

export const lexiconRaid = (zone, mob) => {
  if (!zone) return null
  const path = [zone, mob].filter(Boolean).map(encodeURIComponent).join('/')
  return `${LEXICON}/raids/${path}`
}
