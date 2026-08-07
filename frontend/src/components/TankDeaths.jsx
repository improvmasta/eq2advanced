import { useMemo, useState } from 'react'
import { ActorName } from './Identity.jsx'
import { fmt } from '../lib/api.js'
import { roleOf } from '../lib/classes.js'

/* Why the tank died, which is a different question from who died.
 *
 * Every other death on the page is an event in a list. A tank's death is a
 * FAILURE OF TWO CURVES — what was coming in and what was going out to meet it
 * — and the thing everyone argues about after the pull is whether the heals
 * were there. So this column answers exactly that, three ways at three
 * resolutions: the fact line (the whole window at once), the ledger (a row per
 * second, with NET as the verdict) and the blow by blow (every event).
 *
 * FIVE seconds, not twelve. A tank dies to a spike, and the spike is over in
 * two or three: twelve seconds of context buried the moment that killed him
 * under the rest of the pull. The raid-wide list next door is tighter still
 * (3s) — see ZoneRun's TANK_WINDOW_S / RAID_WINDOW_S.
 *
 * No chart. Six rows of two series in a 380px column is less legible than the
 * numbers it would be drawn from, and the numbers are the thing being quoted
 * back at each other in guild chat anyway.
 */

const isTank = (actor) => roleOf(actor) === 'tank'

/* One predicate, two readers: this component decides which deaths it shows and
   the page decides whether to lay the Deaths tab out in two columns at all. A
   grid whose first child rendered null would put the death list in the narrow
   column and leave the wide one empty, so the page has to know BEFORE it draws
   the grid — and it must ask the same question this file answers. */
export const hasTankDeath = (deaths, actorsByKey) =>
  (deaths || []).some((d) => isTank(actorsByKey?.[d.key]))

/* Every second of the window, present whether or not anything happened in it.
   A ledger that skipped its empty seconds was the hardest part to read: the
   rows looked like events rather than a countdown, so a two-second hole where
   nothing healed him — the whole point — rendered as no row at all.

   The log stamps WHOLE SECONDS (`(1785630623)[Sat Aug  1 20:30:23 2026]`), so
   these buckets are the log's own resolution, not a resampling of something
   finer. There is no tenth-of-a-second anywhere in an EQ2 log to bucket by. */
function ledger(death, windowS) {
  const rows = []
  for (let s = windowS; s >= 0; s--) rows.push({ s, took: 0, healed: 0, hits: 0, heals: 0 })
  const at = (t) => rows[windowS - Math.min(windowS, Math.round(Math.abs(t)))]
  for (const e of death.incoming || []) { const r = at(e.t); r.took += e.amount || 0; r.hits += 1 }
  for (const e of death.healing || []) { const r = at(e.t); r.healed += e.amount || 0; r.heals += 1 }
  return rows
}

function topBy(events, keyOf, n = 3) {
  const by = new Map()
  for (const e of events || []) {
    const k = keyOf(e)
    by.set(k, (by.get(k) || 0) + (e.amount || 0))
  }
  return [...by.entries()].sort((a, b) => b[1] - a[1]).slice(0, n)
}

const killingBlow = (d) => (d.incoming?.length ? d.incoming[d.incoming.length - 1] : null)

/* Every event in the window, in time order. This is as granular as the data
   gets: inside one second the log's line order survives per SIDE (the API
   returns each list ordered by `(ts, seq)`) but damage and healing cannot be
   interleaved against each other, because the payload carries no `seq`. */
function blowByBlow(death) {
  const inc = (death.incoming || []).map((e) => ({ ...e, dir: 'in' }))
  const heal = (death.healing || []).map((e) => ({ ...e, dir: 'heal' }))
  return [...inc, ...heal].sort((a, b) => a.t - b.t)
}

const signed = (n) => (n > 0 ? `+${fmt.num(n)}` : n < 0 ? `−${fmt.num(-n)}` : '—')

function Detail({ death, windowS }) {
  const rows = useMemo(() => ledger(death, windowS), [death, windowS])
  const events = useMemo(() => blowByBlow(death), [death])
  const healedBy = useMemo(() => topBy(death.healing, (e) => e.source || 'unknown'), [death])
  const blow = killingBlow(death)

  return (
    <div className="tankdetail">
      {blow && (
        <p className="tankblow">
          Killed by <span className="killab">{blow.ability || 'melee'}</span>
          <span className="muted"> — {blow.source || 'something'} · {fmt.num(blow.amount)}</span>
        </p>
      )}
      <div className="factline">
        <span title={`Everything that hit them in the ${windowS}s before they died`}>
          Took <b>{fmt.num(death.incoming_total)}</b>
        </span>
        <span title={`Heals and wards that landed on them in the same ${windowS}s`}>
          Healed <b>{fmt.num(death.healing_total)}</b>
        </span>
      </div>

      {/* Net is the column that makes the other two mean something: it is the
          second-by-second answer to "were the heals keeping up", which is the
          only question anybody is asking a tank death. */}
      <table className="data tankledger">
        <thead>
          <tr>
            <th className="l" title="Wall clock — the last row is the second they died in">Time</th>
            <th title="Damage that landed on them in that second">Took</th>
            <th title="Heals and wards that landed on them in that second">Healed</th>
            <th title="Healed minus took — red means they lost ground that second">Net</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const net = r.healed - r.took
            return (
              <tr key={r.s} className={r.s === 0 ? 'lastsecond' : ''}>
                <td className="l">{fmt.clockS(death.ts - r.s)}</td>
                <td className={r.took ? 'took' : 'muted'}
                    title={r.hits ? `${r.hits} hit${r.hits > 1 ? 's' : ''}` : undefined}>
                  {r.took ? fmt.num(r.took) : '—'}
                </td>
                <td className={r.healed ? 'healed' : 'muted'}
                    title={r.heals ? `${r.heals} heal${r.heals > 1 ? 's' : ''}` : undefined}>
                  {r.healed ? fmt.num(r.healed) : '—'}
                </td>
                <td className={net > 0 ? 'healed' : net < 0 ? 'took' : 'muted'}>
                  {signed(net)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <p className="tankwho">
        <span className="k">Healed by</span>
        {healedBy.length
          ? healedBy.map(([name, amt]) => (
            <span key={name} className="w">{name} <b>{fmt.num(amt)}</b></span>
          ))
          : <span className="w muted">nobody</span>}
      </p>

      {events.length > 0 && (
        <>
          <h3 className="tanksub">Log</h3>
          {/* `table-layout: fixed` and a column each. Ability and source shared
              one cell and it collapsed to seven characters — a list of
              `Fae Fir…` and `Porcup…` names nothing. Both cells carry the full
              text as a tooltip, because at this width some of them still
              ellipsize. */}
          <table className="data tankevents">
            <tbody>
              {events.map((e, i) => {
                const what = e.ability || (e.dir === 'in' ? 'melee' : e.kind)
                const who = e.source || 'unknown'
                return (
                  <tr key={i} className={e.dir === 'in' ? 'inrow' : 'healrow'}>
                    <td className="l when">
                      {fmt.clockS(death.ts + Math.round(e.t))}
                    </td>
                    <td className="l what" title={what}>{what}</td>
                    <td className="l who" title={who}>{who}</td>
                    <td className="amt">{e.dir === 'in' ? '−' : '+'}{fmt.num(e.amount)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}

      {(death.incoming_truncated || death.healing_truncated) && (
        <p className="note">
          The totals count every event in the window; the rows carry only the
          most recent ones the API returns.
        </p>
      )}
    </div>
  )
}

export default function TankDeaths({ deaths, windowS, actorsByKey }) {
  const tanks = useMemo(
    () => (deaths || []).filter((d) => isTank(actorsByKey?.[d.key])),
    [deaths, actorsByKey])
  /* Opens on the first one rather than on nothing: a picker whose whole list
     is one click from the same answer should just show it. */
  const [pick, setPick] = useState(0)
  if (!tanks.length) return null
  const at = Math.min(pick, tanks.length - 1)
  const death = tanks[at]

  return (
    <div className="card tankdeaths">
      <div className="drillhead">
        <h2>Tank deaths</h2>
        <span className="muted">{tanks.length}</span>
      </div>
      <div className="tankpicks">
        {tanks.map((d, i) => (
          <button
            key={i}
            className={`tankpick ${i === at ? 'on' : ''}`}
            onClick={() => setPick(i)}
            aria-pressed={i === at}
          >
            <span className="t">{fmt.timeS(d.ts)}</span>
            <ActorName actor={actorsByKey?.[d.key] || { name: d.name }} short />
            <span className="z">{d.encounter_name || 'trash'}</span>
          </button>
        ))}
      </div>
      <Detail key={at} death={death} windowS={windowS} />
    </div>
  )
}
