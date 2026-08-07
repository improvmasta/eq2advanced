import { Fragment, useMemo, useState } from 'react'
import DeathRecap from './DeathRecap.jsx'
import { ActorName } from './Identity.jsx'
import { fmt } from '../lib/api.js'

/* Every death, read the way a wipe actually happens.
 *
 * A flat list of deaths is the wrong shape three times over. It runs the whole
 * night together, so a raid that wiped twice on one boss and lost a healer on
 * trash reads as one undifferentiated column of names. It stamps HH:MM, so the
 * four people an AoE killed in the same second look like four unrelated
 * events. And a wipe spends twenty-four rows saying one thing: the raid died
 * to that.
 *
 * So: fights are separated, the clock runs to the second, and deaths inside
 * CLUSTER_S of each other collapse into one MOMENT — "6 players", expandable,
 * captioned with what killed them when the log agrees on one answer. The
 * recap opens inside the row it belongs to; it used to render at the foot of
 * the page, which on a bad night was under a thousand lines of list.
 */

/* Half a cast bar. Two people dying five seconds apart on a raid mob is one
   AoE landing; ten seconds apart is two separate problems, and calling those
   one moment would hide the second. */
const CLUSTER_S = 5

/* The fetch asks for the TANK's wider window; this list wants a tighter one.
   Narrowing in the browser is exact rather than approximate: `/deaths` caps
   each event list at DEATH_MAX_ENTRIES and keeps the TAIL, so the last 3s of a
   5s window is complete even when the 5s list was truncated — which is why the
   truncation flag is only carried over when nothing was actually cut. */
function clip(d, windowS) {
  const inc = (d.incoming || []).filter((e) => e.t >= -windowS)
  const heal = (d.healing || []).filter((e) => e.t >= -windowS)
  const sum = (xs) => xs.reduce((s, e) => s + (e.amount || 0), 0)
  return {
    ...d,
    incoming: inc,
    healing: heal,
    incoming_total: sum(inc),
    healing_total: sum(heal),
    incoming_truncated: inc.length === (d.incoming || []).length && !!d.incoming_truncated,
    healing_truncated: heal.length === (d.healing || []).length && !!d.healing_truncated,
  }
}

/* The last thing that hit them. `incoming` arrives ordered by (ts, seq) and a
   truncated list keeps its TAIL, so the killing blow survives the cap. */
function killingBlow(d) {
  const inc = d.incoming || []
  return inc.length ? inc[inc.length - 1] : null
}

const abilityOf = (b) => b.ability || 'melee'
const sourceOf = (b) => b.source || 'something'

/* What the whole moment died to — only when the log says one thing. An AoE
   that killed six people has one source and one ability; a slow wipe has
   several of each, and claiming a cause there would be a guess. */
function commonBlow(members) {
  const blows = members.map(killingBlow).filter(Boolean)
  if (!blows.length) return null
  const sources = new Set(blows.map(sourceOf))
  const abilities = new Set(blows.map(abilityOf))
  return {
    source: sources.size === 1 ? [...sources][0] : null,
    ability: abilities.size === 1 ? [...abilities][0] : null,
    sources: sources.size,
    abilities: abilities.size,
    // some deaths in this moment have no incoming events at all
    partial: blows.length !== members.length,
  }
}

/* "Cataclysmic Slam — Overking Ohrmzz". The ability leads: it is the thing
   somebody has to react to next pull, and the mob is usually already named by
   the fight heading above. */
function KilledBy({ members }) {
  const c = commonBlow(members)
  if (!c) return <span className="muted" title="Nothing hit them inside the window — the log kept no killing blow">—</span>
  /* A moment where some deaths have no incoming events at all is a claim about
     the ones that do, and it has to say so rather than speak for all six.
     Only ever set on a group: a lone death with no events returned null above. */
  const title = c.partial
    ? 'The last hit on the deaths the log kept events for — some here have none'
    : undefined
  if (c.ability && c.source) {
    return (
      <span title={title}>
        <span className="killab">{c.ability}</span>
        <span className="muted"> — {c.source}</span>
        {c.partial && <span className="muted"> *</span>}
      </span>
    )
  }
  if (c.source) {
    return (
      <span title={title}>
        <span className="muted">{c.source} — </span>
        {c.abilities} abilities
      </span>
    )
  }
  return <span className="muted" title={title}>{c.sources} sources</span>
}

export default function DeathList({ deaths, windowS, prunedEncounters, actorsByKey }) {
  // one open recap at a time, keyed by the death's index in the flat list
  const [openIdx, setOpenIdx] = useState(null)
  const [openMoments, setOpenMoments] = useState(() => new Set())

  /* Fights in the order they happened, each holding its moments. The list
     arrives sorted by time; a fight is keyed by id rather than by "the row
     before it" so an interleaved pair can never merge into one heading. */
  const fights = useMemo(() => {
    const by = new Map()
    ;(deaths || []).forEach((d, idx) => {
      let f = by.get(d.encounter_id)
      if (!f) {
        f = { id: d.encounter_id, name: d.encounter_name, ts: d.ts, moments: [], count: 0 }
        by.set(d.encounter_id, f)
      }
      f.count += 1
      const last = f.moments[f.moments.length - 1]
      const entry = { ...clip(d, windowS), idx }
      if (last && d.ts - last.lastTs <= CLUSTER_S) {
        last.members.push(entry)
        last.lastTs = d.ts
      } else {
        f.moments.push({ key: `${d.encounter_id}:${d.ts}:${idx}`, ts: d.ts, lastTs: d.ts, members: [entry] })
      }
    })
    return [...by.values()]
  }, [deaths, windowS])

  const toggleMoment = (key) => {
    const next = new Set(openMoments)
    if (next.has(key)) next.delete(key); else next.add(key)
    setOpenMoments(next)
  }
  const openRecap = (idx) => setOpenIdx(idx === openIdx ? null : idx)

  /* One death's row body, shared by a lone death and a member of a group —
     the same five cells either way, so the columns line up down the table
     whether or not anything is expanded. */
  const deathCells = (d) => (
    <>
      <td className="l">
        <ActorName actor={actorsByKey?.[d.key] || { name: d.name }} short />
      </td>
      <td className="l"><KilledBy members={[d]} /></td>
      <td>{fmt.num(d.incoming_total)}</td>
      <td>{fmt.num(d.healing_total)}</td>
      {/* A caret, not a "Recap" chip: the word cost a column of width in a
          table that now shares the page, and the row is clickable anyway. */}
      <td className="caret">{d.idx === openIdx ? '▾' : '▸'}</td>
    </>
  )

  const recapRow = (d) => (
    <tr className="recaprow">
      <td className="recapcell" colSpan={6}>
        <DeathRecap death={d} windowS={windowS} inline />
      </td>
    </tr>
  )

  return (
    <div className="card">
      <h2>Every death</h2>
      <div className="tablewrap">
        <table className="data deathlist">
          <thead>
            <tr>
              <th>Time</th>
              <th className="l">Who</th>
              <th className="l">Killed by</th>
              <th>Took<span className="colsub">last {windowS}s</span></th>
              <th>Healed<span className="colsub">last {windowS}s</span></th>
              <th />
            </tr>
          </thead>
          <tbody>
            {fights.map((f) => (
              <Fragment key={f.id}>
                <tr className="grouphead">
                  <th colSpan={6} scope="colgroup">
                    <span className="daygroup">
                      <span className="d">{f.name || 'trash'}</span>
                      <span className="muted" title="When the first death in this fight happened">
                        from {fmt.timeS(f.ts)}
                      </span>
                      <span className="muted">
                        {f.count} death{f.count > 1 ? 's' : ''}
                      </span>
                    </span>
                  </th>
                </tr>
                {f.moments.map((m) => {
                  if (m.members.length === 1) {
                    const d = m.members[0]
                    return (
                      <Fragment key={m.key}>
                        <tr className={`clickable ${d.idx === openIdx ? 'selected' : ''}`}
                            onClick={() => openRecap(d.idx)}>
                          <td>{fmt.timeS(d.ts)}</td>
                          {deathCells(d)}
                        </tr>
                        {d.idx === openIdx && recapRow(d)}
                      </Fragment>
                    )
                  }
                  const open = openMoments.has(m.key)
                  const span = m.lastTs - m.ts
                  /* Almost always one death each, but a rez inside five
                     seconds can put the same name in twice — then it is N
                     deaths, not N players, and the row has to say which. */
                  const who = new Set(m.members.map((d) => d.name)).size
                  return (
                    <Fragment key={m.key}>
                      <tr className={`clickable moment ${open ? 'selected' : ''}`}
                          onClick={() => toggleMoment(m.key)}>
                        <td title={span ? `over ${span}s` : 'all in the same second'}>
                          {fmt.timeS(m.ts)}{span ? <span className="muted"> +{span}s</span> : null}
                        </td>
                        <td className="l">
                          <button
                            className="twisty"
                            aria-expanded={open}
                            onClick={(e) => { e.stopPropagation(); toggleMoment(m.key) }}
                          >{open ? '▾' : '▸'}</button>
                          <strong>
                            {who === m.members.length
                              ? `${who} players` : `${m.members.length} deaths`}
                          </strong>
                        </td>
                        <td className="l"><KilledBy members={m.members} /></td>
                        <td>{fmt.num(m.members.reduce((s, d) => s + (d.incoming_total || 0), 0))}</td>
                        <td>{fmt.num(m.members.reduce((s, d) => s + (d.healing_total || 0), 0))}</td>
                        <td className="caret">{open ? '▾' : '▸'}</td>
                      </tr>
                      {open && m.members.map((d) => (
                        <Fragment key={d.idx}>
                          <tr className={`clickable subrow ${d.idx === openIdx ? 'selected' : ''}`}
                              onClick={() => openRecap(d.idx)}>
                            <td>{fmt.timeS(d.ts)}</td>
                            {deathCells(d)}
                          </tr>
                          {d.idx === openIdx && recapRow(d)}
                        </Fragment>
                      ))}
                    </Fragment>
                  )
                })}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {prunedEncounters > 0 && (
        <p className="note">
          {prunedEncounters} fight(s) had their events pruned — those deaths are
          counted in the table above but have no recap.
        </p>
      )}
    </div>
  )
}
