import { useEffect, useRef, useState } from 'react'
import { CLASS_ROLE } from '../lib/classes.js'
import { nextJoust, useJoust } from '../lib/joust.js'
import { useMiniPins } from '../lib/minipin.js'
import AoeTimers, { miniTimers } from './AoeTimers.jsx'

/* THE NOTIFICATION BLOCK: the part of the rail you are meant to catch out of
   the corner of an eye, directly under the last stack of bars.

   Everything above it is a READING, laid out so a glance finds the same thing
   in the same place every time and sized to fit a 244px strip. This block is
   the same facts at the size somebody who is FIGHTING will actually catch, and
   it is where anything that cannot wait to be read arrives.

   TWO KINDS OF CONTENT, and they behave differently on purpose:

   - The COUNTDOWNS — the AoE rows and the burn window — are the panel above,
     drawn a size up. They are `AoeTimers` itself, not a copy of it: same rows
     (`miniTimers` picks them), same drain on the compositor, same JOUST flash,
     same HIT flash, one size larger through CSS alone. A second implementation
     of a countdown is a second set of numbers to keep in step, and the whole
     reason the mini parse calls `LiveMeter`'s `meterRows` is that two of
     anything drift. They are PERSISTENT: a countdown that appeared only when
     it was nearly due would be a countdown nobody could plan around.
   - The DEATH cards pop in above them, hold for a few seconds and go. They are
     the only thing on the rail that appears and disappears, because they are
     the only thing here that is an EVENT rather than a clock.

   WHICH DEATHS ARE WORTH A CARD is the design of that half: a single dps dying
   is a fact for the parse, not an interruption, and the deaths figure in the
   numbers strip already carries it. A TANK dying changes the shape of the
   fight, and who that is comes off the parse rather than off a setting — the
   fighter who has taken the most damage this fight is the main tank and the
   second is the off tank (`tankOrder`). It is the answer the raid would give
   out loud, it needs nothing configured, and being noisy about it early costs
   nothing: in the first seconds of a pull the ranking is unsettled, and in the
   first seconds of a pull nobody is dead.

   Deaths are counted by DIFFERENCE, because the payload carries a running
   total per actor and no death events (`livemeter.py`). The first payload of a
   pull is a BASELINE and announces nothing — otherwise every fight would open
   by announcing the deaths of the fight before it — and the baseline is retaken
   whenever `started_ts` changes.

   The death half runs on the WALL clock, unlike everything else on this rail:
   a card that shows for seven seconds is seven seconds of somebody's
   attention, not of log time. */

/* More than five raiders down at once, which is what was asked for: five is a
   group eating an AoE, six is the raid coming apart. */
const MULTI_DEATHS = 6
/* Deaths this far apart are two events rather than one. Generous, because a
   wipe arrives over several payloads and the point of the card is to name the
   whole thing rather than each of its parts. */
const MULTI_WINDOW_MS = 8000
/* How long a card holds its slot. Long enough to be read by somebody who was
   looking at the game, short enough to be gone before it stops being current. */
const DEATH_MS = 7000
/* How many cards may be up at once. Two, and the cap is the point: a block
   that stacks is a panel, and the countdowns under it are the panel. */
const ALERT_ROWS = 2

/* Who the tanks are, off the parse and nothing else: the FIGHTERS, ranked by
   what they have taken. All six fighter classes are tanks — brawlers included
   (`CLASS_ROLE`) — and a raider whose class has not resolved yet is nobody's
   tank, because guessing would name the wrong person at the worst moment. */
const tankOrder = (players) => players
  .filter((a) => CLASS_ROLE[a.class] === 'tank')
  .sort((a, b) => (b.damage_taken || 0) - (a.damage_taken || 0))

/* Who has died since the last payload, and whether that is a tank or a wipe.
   Returns the live cards and expires them on the wall clock. */
function useDeathAlerts(fight, on) {
  const seen = useRef({ key: null, counts: new Map() })
  const recent = useRef([])
  const [cards, setCards] = useState([])

  useEffect(() => {
    const players = (fight?.actors || []).filter((a) => a.kind === 'player')
    const key = fight && on ? (fight.started_ts ?? null) : null

    if (!fight || !on || seen.current.key !== key) {
      // a new pull (or none): this payload is the baseline, and says nothing
      seen.current = { key, counts: new Map(players.map((a) => [a.name, a.deaths || 0])) }
      recent.current = []
      setCards((cur) => (cur.length ? [] : cur))
      return
    }

    const fresh = []
    let n = 0
    for (const a of players) {
      const was = seen.current.counts.get(a.name) ?? 0
      const now = a.deaths || 0
      seen.current.counts.set(a.name, now)
      // a raider who appears mid-pull is taken as having been at zero: they got
      // into the actor list somehow, and if that was by dying, that is news
      if (now > was) { n += now - was; fresh.push(a) }
    }
    if (!n) return

    const at = Date.now()
    recent.current = [...recent.current.filter((e) => at - e.at < MULTI_WINDOW_MS), { at, n }]
    const total = recent.current.reduce((s, e) => s + e.n, 0)

    /* A wipe SUPERSEDES the tank card rather than stacking over it: when six
       people are on the floor, which of them was the main tank is not what the
       next two seconds are for. */
    const made = []
    if (total >= MULTI_DEATHS) {
      made.push({
        id: `wipe|${at}`, kind: 'wipe', what: `${total} DOWN`,
        sub: 'the raid is going down', at,
      })
    } else {
      const order = tankOrder(players)
      for (const a of fresh) {
        const rank = order.indexOf(a)
        if (rank === 0) {
          made.push({ id: `mt|${a.name}|${at}`, kind: 'tank', what: 'MAIN TANK DOWN', sub: a.name, at })
        } else if (rank === 1) {
          made.push({ id: `ot|${a.name}|${at}`, kind: 'tank', what: 'OFF TANK DOWN', sub: a.name, at })
        }
      }
    }
    if (made.length) setCards((cur) => [...made, ...cur].slice(0, ALERT_ROWS))
  }, [fight, on])

  /* Expiry, on one timeout rather than a poll — and returning the SAME array
     when nothing dropped, because a fresh array here would re-arm this effect
     forever. */
  useEffect(() => {
    if (!cards.length) return undefined
    const oldest = Math.min(...cards.map((c) => c.at))
    const t = setTimeout(() => setCards((cur) => {
      const next = cur.filter((c) => Date.now() - c.at < DEATH_MS)
      return next.length === cur.length ? cur : next
    }), Math.max(80, DEATH_MS - (Date.now() - oldest)))
    return () => clearTimeout(t)
  }, [cards])

  return cards
}

export default function MiniAlerts({ fight, running = true, notify }) {
  const pins = useMiniPins()
  const jousts = useJoust()
  /* A tank on the floor is worth announcing whether or not the countdown
     switches are on — those two say which COUNTDOWNS are drawn big, and a
     death is not a countdown. */
  const deaths = useDeathAlerts(fight, !!notify)

  const aoes = fight?.aoes || []
  /* Asked HERE as well as inside `AoeTimers` for the reason `MiniParse` asks
     it: the frame is this component's and the rows are not, so "will there be
     a row" has to be asked with the same rule that will answer it. The burn
     window is checked separately because it can be the ONLY row — its switch
     and the abilities' switch are independent. */
  const rows = notify ? miniTimers(aoes, running, pins) : []
  const timers = !!((notify?.aoes && rows.length)
    || (notify?.burn && running && nextJoust(rows, jousts)))

  if (!deaths.length && !timers) return null

  return (
    <div className="minipanel minialerts">
      {/* Deaths above the clocks: an AoE landing in four seconds and a tank
          already dead are both true, and only one of them is still going to
          matter in four seconds. */}
      {deaths.map((c) => (
        /* Keyed on the moment it landed, so a card that is still the same card
           does not remount and replay its fade on the next payload. */
        <div key={c.id} className={`minialert ${c.kind}`} role="status">
          <b className="what">{c.what}</b>
          <span className="who">{c.sub}</span>
        </div>
      ))}
      {timers && (
        /* THE SAME PANEL AS ABOVE, ONE SIZE UP — not a second countdown.
           `showSuggest` is off: a suggested timer is an errand (go and edit an
           ACT config) and this block is read mid-pull. */
        <AoeTimers aoes={aoes} logTs={fight?.log_ts ?? fight?.last_ts}
                   running={running} dropS={fight?.aoe_drop_s} missedS={fight?.aoe_missed_s}
                   showRows={!!notify.aoes} showBurn={!!notify.burn}
                   showSuggest={false} compact />
      )}
    </div>
  )
}
