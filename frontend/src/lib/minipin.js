import { actListed, markSet } from './marks.js'

/* Which AoEs belong on the mini parse — the dock and the stream overlay.

   That panel is drawn in a FIXED SCENE with the meter under it, so every
   countdown row it keeps is a raider off the bottom (`AoeTimers: miniTimers`).
   Two questions decide what is on it and they are different questions: WHICH
   abilities are eligible, which is this mark, and HOW MANY fit, which stays
   `MINI_TIMER_ROWS` and is not something a mark may overrule — a fixed scene is
   fixed however strongly somebody feels about a sixth countdown.

   AN ABILITY ON ACT'S LIST IS ELIGIBLE UNTIL SOMEBODY SAYS OTHERWISE. That
   list is what the raid decided to watch for; everything else on these panels
   is here because the site DETECTED it reaching the raid, which is a good
   reason to record it on the audit tab and a poor reason to spend a slot beside
   the game on it. So the mark's real work is the two exceptions: the listed
   ability that clutters the strip, and the unlisted one — a new mob, an
   overnuke nobody has an entry for — that has to be on it.

   How a mark is stored, why the third state exists and why it now follows the
   account rather than the browser: `lib/marks.js`, which both hand-marked sets
   share. */

const marks = markSet('eq2adv:minipin', 'mini')

export const toggleMiniPin = marks.toggle
export const useMiniPins = marks.use

/* Is this row eligible for the mini panel: what was said about it, or the
   default. Eligible, not shown — the cap has the last word. */
/* A REFLECT ROW IS ELIGIBLE BY DEFAULT, and for the same reason an ACT-listed
   ability is: somebody decided in advance that this one is worth watching for.
   `actListed` reads that decision off the raid's spell-timer list; a reflect
   row carries it from `refdata/reflect_windows.json`, which is a human ruling
   on which mobs' reflects matter at all (`aoes.reflect_windows`). Both are the
   same kind of evidence — a person said so — and neither is a detection.

   It stays a default rather than a rule: the mark can still turn it off, which
   is the whole reason marks exist. */
export const isMiniPinned = (answers, row) => (
  answers[row.ability] ?? (row.kind === 'reflect' || actListed(row))
)
