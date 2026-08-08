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

/* eq2lexicon's raid pages, which are the strategy half of what this site's
   notes are the other half of: a zone is `/raids/<zone>` and a named inside it
   is `/raids/<zone>/<named>`. Deep-linking there rather than restating any of
   it is the point — the note says what happened to US on that pull, the
   lexicon says what the encounter does.

   `away` is not decoration: eq2lexicon sends `X-Frame-Options: DENY` to every
   origin (see App.jsx), so every one of these opens in a new tab. */
export const LEXICON = 'https://wuoshi.eq2lexicon.com'

export const lexiconRaid = (zone, mob) => {
  if (!zone) return null
  const path = [zone, mob].filter(Boolean).map(encodeURIComponent).join('/')
  return `${LEXICON}/raids/${path}`
}
