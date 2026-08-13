import { useEffect, useState } from 'react'
import { api } from './api.js'

/* Which AoEs somebody has marked by hand, and what the unmarked ones default
   to.

   TWO THINGS ON THE AoE PANELS ARE NOT READINGS: which AoEs the raid jousts
   (`joust.js`) and which ones belong on the mini parse (`minipin.js`). Neither
   is in any log and they need exactly the same mechanics — so those are here
   once rather than twice, for the reason `MiniParse` calls `LiveMeter`'s
   `meterRows` instead of ranking a parse a second way: two copies of a thing
   are two definitions that drift.

   A mark is keyed by ABILITY NAME, never by source or by fight. Both of these
   are properties of the ABILITY — if you joust Mayong's Soul Paralysis you
   joust it on every Mayong, in every zone, next week as well — so a mark has to
   outlive the pull it was made on or it would have to be re-made every night.

   THEY ARE ON THE ACCOUNT (schema v35, `backend/marks.py`), and they were not.
   localStorage was the original call and the argument for it was real: a mark
   is a note about how somebody plays, it is worth nothing to the server, and
   the alternative is a settings table and a round trip in front of a countdown.
   What broke it is that this site now draws the same panels in THREE browsers.
   An OBS source inherits nothing, which was written off — a stream overlay
   getting the ACT-list defaults is a defensible floor, and nobody reads their
   own stream. EQ2's in-game browser window (overlay v34) is a different
   browser too, and that one is read by the person who did the marking, mid-
   pull, next to their hotbars. Opening it to none of your own marks is not a
   defensible floor; it is the dashboard's answer being thrown away on the one
   screen you are actually looking at.

   THE ROUND TRIP IS STILL NOT IN FRONT OF THE COUNTDOWN, which is the part
   worth keeping from the old design. localStorage is now a CACHE rather than
   the store: module state is seeded from it synchronously at import, so the
   first paint has last night's marks with nothing awaited, and the account's
   answer arrives afterwards and corrects it (`syncMarks`). A mark you click is
   applied locally and pushed in the background — a failed push costs the
   server's copy of one pill, never the pill.

   THREE STATES, NOT TWO, and the third one is the useful one. A set of names
   can only say "these are on", which forces everything else off and makes a
   good default impossible to overrule downwards. So what is stored is an
   ANSWER per ability — true, false, or nothing said — and an ability nobody has
   said anything about takes the default. Clicking a pill records the answer it
   is now showing, which means the first click on a defaulted-ON row turns it
   off, exactly as it looks like it should.

   Module state, so the raid page's marks and the dashboard's countdown are
   looking at one set rather than two copies that drift, and `storage` for the
   case module state cannot reach: the dashboard in one tab and the raid page in
   another is the normal way this gets used. */

/* WHAT AN UNMARKED ABILITY DEFAULTS TO: whether ACT's spell-timer list knows
   it (`aoes.reported_timers`, `reported_s` on the row).

   That list is the raid's OWN shortlist. Somebody typed those entries in
   because the raid calls those abilities out, and both marks are asking a
   question the list has already answered: you joust the things you were told to
   expect, and the things you were told to expect are the ones worth a slot on a
   strip beside the game. Everything else on these panels got there because this
   site DETECTED it, so the raid has said nothing about it either way.

   It is still what a screen with no marks on it gets — an account that has
   never marked anything, and the moments before the first read comes back.

   THE COST, stated because it is real: an ability that is genuinely raid-wide
   and simply is not in anybody's ACT config starts off the mini panel — the
   sourceless 24-target `Overnuke` is the case, and it is exactly the kind of
   thing a raid has not got round to configuring yet. It is still on the
   dashboard's full panel and on the AoE tab, one click puts it on the strip
   for good, and the alternative default (everything eligible) makes the MINI
   mark subtractive only, which in a scene capped at three rows barely does
   anything: drop the top row and the next one takes the slot. */
export const actListed = (row) => row?.reported_s != null

/* Every set that exists, by the name the server knows it as, so one hydrate
   can reach both without either of them having to be imported here — which
   would be a cycle, since both import this. */
const SETS = new Map()

/* Answer-for-answer equality, key order ignored. The comparison matters: both
   hydrate paths run on a poll, and an object that is merely NEW would push a
   re-render through every subscriber every few seconds — on the two surfaces
   whose whole design rule is that nothing moves that does not have to. */
const same = (a, b) => {
  const ka = Object.keys(a)
  return ka.length === Object.keys(b).length && ka.every((k) => a[k] === b[k])
}

export function markSet(key, kind) {
  const read = () => {
    try {
      const raw = JSON.parse(localStorage.getItem(key) || '{}')
      /* The pre-defaults format was an array of the names that were ON.
         Reading it as such is the whole migration — an explicit yes for each
         name, nothing said about anything else, which is what it meant. */
      if (Array.isArray(raw)) return Object.fromEntries(raw.map((n) => [n, true]))
      return raw && typeof raw === 'object' ? raw : {}
    } catch {
      return {}   // private mode, or somebody's hand-edited value
    }
  }

  let marked = read()
  const subs = new Set()

  const store = () => {
    try {
      localStorage.setItem(key, JSON.stringify(marked))
    } catch { /* nothing to do — the marks are still live for this session */ }
    for (const fn of Array.from(subs)) fn(marked)
  }

  /* true | false | undefined — the answer given, not the answer in force.
     Callers pair it with a default (`isJousted`, `isMiniPinned`). */
  const get = (ability) => marked[ability]

  const toggle = (ability, on) => {
    const answer = !on
    marked = { ...marked, [ability]: answer }
    store()
    /* Fire and forget, and the response is deliberately dropped rather than
       hydrated from. It is the whole set as the server now has it, which is a
       stale view of this browser the moment somebody clicks a second pill —
       and this is a panel people click down a column of. The local answer is
       already the truth here; the account catches up on the next sign-in read
       either way.

       A rejection is swallowed for the same reason it is pushed in the
       background: signed out (the AoE tab of a public raid is readable by
       anybody), offline, or a server that is down mid-raid. None of those is a
       reason for a pill not to light. */
    api.setMarks({ [kind]: { [ability]: answer } }).catch(() => {})
  }

  /* Somebody else's copy of the answers, dropped in wholesale — the account's
     (`syncMarks`) or the one that rode in with an overlay token's config. It
     REPLACES rather than merges: the caller has already decided what the whole
     set is, and a merge here would make a mark impossible to remove from a
     second browser. Whoever calls this owes the merge; `syncMarks` is where
     that is done and why. */
  const hydrate = (answers) => {
    if (same(answers, marked)) return
    marked = answers
    store()
  }

  const use = () => {
    const [answers, setAnswers] = useState(marked)
    useEffect(() => {
      subs.add(setAnswers)
      /* Module state can have moved on between this component's first render
         and its effect — a hydrate landing in that gap would otherwise leave
         this subscriber a render behind for good. */
      setAnswers(marked)
      /* Written in another tab. Re-read rather than trusted: the event carries
         the new value, and the module state is what everything else here reads
         from. */
      const onStorage = (e) => {
        if (e.key !== key) return
        marked = read()
        setAnswers(marked)
      }
      window.addEventListener('storage', onStorage)
      return () => {
        subs.delete(setAnswers)
        window.removeEventListener('storage', onStorage)
      }
    }, [])
    return answers
  }

  const set = { get, toggle, use, hydrate, answers: () => marked }
  SETS.set(kind, set)
  return set
}

/* Both sets at once, as `{joust: {...}, mini: {...}}` — the shape the server
   sends. A kind the payload does not mention is hydrated EMPTY on purpose: the
   sender is stating the whole account, and "no answers" is a state an account
   can be in. */
export function hydrateMarks(all) {
  if (!all) return
  for (const [kind, set] of SETS) set.hydrate(all[kind] || {})
}

/* Take the account's marks, once, on sign-in — and hand it whatever this
   browser has that it does not.

   THE ADOPTION IS THE POINT AND IT ONLY GETS ONE CHANCE. Every mark anybody
   made before v35 is in a localStorage key on one machine, which is the only
   place it exists and somewhere the server can never reach; a plain "the
   account is the truth now" would have opened the first dashboard after the
   deploy with a night's marking silently gone. So an ability the account has
   NO answer for takes this browser's, and an ability it has an answer for
   keeps the account's — per ability, never wholesale, so a mark deliberately
   turned off on another machine is not resurrected by a stale tab.

   It runs on every sign-in rather than behind a one-time flag, which costs one
   read and is the honest version of the same rule: a browser that has been
   marking things while signed out is in exactly the state the migration was
   for. Once the two agree it sends nothing.

   Order matters — merge, then hydrate, then push. Hydrating first would wipe
   the very answers being adopted. */
export async function syncMarks() {
  const held = (await api.marks()).marks || {}
  const patch = {}
  const merged = {}
  for (const [kind, set] of SETS) {
    const theirs = { ...(held[kind] || {}) }
    const add = {}
    for (const [ability, answer] of Object.entries(set.answers())) {
      // `undefined` is nothing-said and is not an answer worth sending
      if (typeof answer !== 'boolean' || ability in theirs) continue
      add[ability] = answer
      theirs[ability] = answer
    }
    if (Object.keys(add).length) patch[kind] = add
    merged[kind] = theirs
  }
  hydrateMarks(merged)
  if (Object.keys(patch).length) await api.setMarks(patch)
}
