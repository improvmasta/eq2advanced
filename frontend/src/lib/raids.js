/* A group is six, so seven raiders means the night was a raid, and two to six
   is group content — a different kind of evening, not a worse one.
   `raider_count` is the run's ROSTER, not everyone the log overheard: the
   backend (`pipeline/zoneruns.py`) drops mobs, bystanders who only ever got
   hit, and the group that fought past you, all of which used to push a six-man
   run over this line.

   The same line the backend draws (`groups.RAID_MIN_RAIDERS`), and the reason
   it lives in one module here: the raid list partitions on it, a standing share
   defaults to the raids side of it, and Compare's picker offers that side
   first. Three readings of one number is three chances to disagree. */
export const RAID_MIN_RAIDERS = 7

export const isRaid = (r) => (r.raider_count || 0) >= RAID_MIN_RAIDERS

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
  const zone = r?.zone || fallback
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

export const lexiconCharacter = (name) => name
  ? `${LEXICON}/character/${encodeURIComponent(name)}`
  : null

export const lexiconGuild = (name) => name
  ? `${LEXICON}/guild/${encodeURIComponent(name)}`
  : null

export const lexiconRaid = (zone, mob) => {
  if (!zone) return null
  const path = [zone, mob].filter(Boolean).map(encodeURIComponent).join('/')
  return `${LEXICON}/raids/${path}`
}
