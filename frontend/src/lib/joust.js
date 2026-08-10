import { useEffect, useState } from 'react'

/* Which AoEs the raid jousts, and the burn window that falls out of saying so.

   The parse knows when a cast is due. It cannot know what the raid DOES about
   it — running out of a Soul Paralysis and standing in a Blanket of Eternal
   Night look identical in a log — so that one fact is marked by hand, and it
   is the only thing here that is not derived. Once it is marked the useful
   number is the other side of the same countdown: not "the AoE lands in 24s"
   but "you have 24 seconds in melee", which is the number a raid actually
   calls out.

   A mark is keyed by ABILITY NAME, not by source or by fight. Jousting is a
   property of the ability — if you joust Mayong's Soul Paralysis you joust it
   on every Mayong, in every zone, next week as well — so the mark has to
   outlive the pull it was made on or it would have to be re-made every night.

   It lives in localStorage, which is per browser and deliberately not per
   account: this is a note-to-self about how you play, it is worth nothing to
   the server, and the alternative is a settings table and a round trip before
   a countdown can draw. The consequence to know is that an OBS browser source
   is a different browser — the stream overlay will not inherit marks made on
   the dashboard, and would need them passed through the overlay config. */

const KEY = 'eq2adv:joust'

const read = () => {
  try {
    return new Set(JSON.parse(localStorage.getItem(KEY) || '[]'))
  } catch {
    return new Set()   // private mode, or somebody's hand-edited value
  }
}

/* Module state, so the raid page's checkboxes and the dashboard's countdown
   are looking at one set rather than two copies that drift. */
let marked = read()
const subs = new Set()

export function isJousted(ability) {
  return marked.has(ability)
}

export function toggleJoust(ability) {
  const next = new Set(marked)
  if (!next.delete(ability)) next.add(ability)
  marked = next
  try {
    localStorage.setItem(KEY, JSON.stringify([...next]))
  } catch { /* nothing to do — the marks are still live for this session */ }
  for (const fn of Array.from(subs)) fn(next)
}

export function useJoust() {
  const [set, setSet] = useState(marked)
  useEffect(() => {
    subs.add(setSet)
    /* The dashboard in one tab and the raid page in another are the normal
       way this gets used, and `storage` is the only event that crosses. */
    const onStorage = (e) => {
      if (e.key !== KEY) return
      marked = read()
      setSet(marked)
    }
    window.addEventListener('storage', onStorage)
    return () => {
      subs.delete(setSet)
      window.removeEventListener('storage', onStorage)
    }
  }, [])
  return set
}

/* The next jousting cast to leave for — the SOONEST of them, because the burn
   window ends at whichever one comes first and a second countdown behind it is
   a countdown to standing in the first one. Rows with no period are skipped:
   an AoE nobody can time is not a window anybody can burn in. */
export function nextJoust(aoes, marked) {
  let soonest = null
  for (const r of aoes) {
    if (!marked.has(r.ability) || !r.period_s || r.next_due_ts == null) continue
    if (!soonest || r.next_due_ts < soonest.next_due_ts) soonest = r
  }
  return soonest
}
