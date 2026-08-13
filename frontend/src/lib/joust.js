import { actListed, markSet } from './marks.js'

/* Which AoEs the raid jousts, and the burn window that falls out of saying so.

   The parse knows when a cast is due. It cannot know what the raid DOES about
   it — running out of a Soul Paralysis and standing in a Blanket of Eternal
   Night look identical in a log — so that one fact is marked by hand, and it
   is the only thing here that is not derived. Once it is marked the useful
   number is the other side of the same countdown: not "the AoE lands in 24s"
   but "you have 24 seconds in melee", which is the number a raid actually
   calls out.

   AN ABILITY ON ACT'S LIST IS JOUSTED UNTIL SOMEBODY SAYS OTHERWISE. Somebody
   typed that entry in because the raid calls the ability out, and a raid that
   calls an AoE out is a raid that leaves for it — so the burn window is there
   on the first pull, rather than after everyone has remembered to tick four
   rows. What the mark is FOR is the two exceptions: the listed AoE you stand
   in, and the unlisted one you run from (`lib/marks.js` on the third state).

   How a mark is stored, why it is on the account and why localStorage is still
   read first: `lib/marks.js`, which both hand-marked sets share. */

const marks = markSet('eq2adv:joust', 'joust')

export const toggleJoust = marks.toggle
export const useJoust = marks.use

/* Is this row jousted right now: what was said about it, or the default. */
export const isJousted = (answers, row) => answers[row.ability] ?? actListed(row)

/* The next jousting cast to leave for — the SOONEST of them, because the burn
   window ends at whichever one comes first and a second countdown behind it is
   a countdown to standing in the first one. Rows with no period are skipped:
   an AoE nobody can time is not a window anybody can burn in.

   AND A CAST THAT IS BADLY PAST DUE IS SKIPPED TOO, which is the whole reason
   `at` and `missedS` are here. "Soonest" was doing something silly with an
   overdue row: a cast due thirty seconds AGO is soonest by a mile, so it won
   this comparison every time and held the burn window against every real cast
   behind it. Vampire Lord Mayong Mistmoore's `Soul Paralysis` gets skipped a
   minute or two into the fight, and the window it owned then read `+0:47` —
   counting UP, through a stretch the raid could have been burning in — until
   the server finally dropped the row.

   Past `missedS` (`livemeter.MISSED_S`) the honest reading is that the cast did
   not happen: the mob was stunned, or every single person blocked it so nothing
   printed to detect it on, or the timer is wrong. None of those is a window,
   and the window moves to the next jousted ability — or there is none, which is
   also an answer. */
export function nextJoust(aoes, answers, at = null, missedS = 0) {
  let soonest = null
  for (const r of aoes) {
    if (!isJousted(answers, r) || !r.period_s || r.next_due_ts == null) continue
    if (at != null && missedS > 0 && at - r.next_due_ts > missedS) continue
    if (!soonest || r.next_due_ts < soonest.next_due_ts) soonest = r
  }
  return soonest
}
