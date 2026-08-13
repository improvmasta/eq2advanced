import { useState } from 'react'
import SortableTable from './SortableTable.jsx'
import { DtypePill, MarkPills } from './AoeTimers.jsx'
import { fmt } from '../lib/api.js'
import { isJousted, useJoust } from '../lib/joust.js'
import { isMiniPinned, useMiniPins } from '../lib/minipin.js'

/* Incoming raid AoEs: what the enemy pulled, how often it really landed, and
   how much of the raid was covered when it did.

   Two timers sit side by side and the point is the distance between them.
   "Reported" is ACT's spell-timer list — what the raid was told to expect.
   "Observed" is the shortest interval between two casts that repeats, taken
   from this log. They disagree for real reasons: a timer that was never right
   for this expansion, a mob that got stunned, or several trash mobs sharing
   one name.

   What is NOT claimed: that every cast was seen. An AoE the timer list has
   never heard of has to reach five people to leave a trace wide enough to
   detect, so its cast count is a floor. A missed cast can only make a gap
   longer, never shorter, which is why the observed timer is the shortest
   repeating gap rather than the mean.

   Three things here are not readings. The JOUST and MINI pills are the two
   facts no log holds — whether the raid leaves for this one, and whether it is
   worth a slot on the mini parse — and they are marked HERE as well as on the
   dashboard because this is the tab you have open when you are working out
   what went wrong, which is when you know the answers (`AoeTimers.MarkPills`,
   `lib/joust.js`, `lib/minipin.js`). And a SUGGESTED timer is what to go and
   type into ACT: it appears only when this log measured a period that
   disagrees with the configured one by more than the noise, over enough
   agreeing intervals to mean it, and never when several mobs wearing one name
   is as good an explanation (`aoes.suggest_period`, `aoes.several_bodies`). */

/* Why a measurement with every gate cleared is still not the countdown's
   number. Spelled out per reason rather than as one sentence about "several
   mobs", because what a reader does next differs: a splitter is settled and
   written down, and `instances` is an invitation to go and check whether that
   name really did come in a pack. */
const BODIES_WHY = (r, k) => ({
  splits: `${r.source} splits — several of them wear this name at once, so`
    + ` these ${k.base_agree} intervals are the gaps between DIFFERENT mobs'`
    + " casts. The countdown keeps ACT's number, which is one body's recast.",
  instances: `${k.clean_s}s is a clean fraction of ACT's ${r.reported_s}s,`
    + ' which is what several mobs of one name look like — they cast on their'
    + ' own timers and read as one mob casting faster.',
}[k.several_bodies])

const clock = (ts, base) => {
  const s = Math.max(0, ts - base)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function Casts({ row, base }) {
  return (
    <div className="tablewrap">
      <table className="data">
        <thead>
          <tr>
            <th className="l">Cast</th><th>Targets</th><th>Hit</th>
            <th>Avoided</th><th>Absorbed</th><th>Damage</th><th className="l">Covered</th>
          </tr>
        </thead>
        <tbody>
          {row.cast_list.map((c, i) => (
            <tr key={i}>
              <td className="name l">
                {clock(c.ts, base)}
                {/* which cycle this cast STARTED — the state at the cast is
                    what the interval after it belongs to (aoes.split_cycles),
                    so the mark reads forward down the column, not back */}
                {c.swiped && (
                  <span className="selfmark"
                        title={'A reuse debuff was on the mob when it cast this,'
                          + ' so the gap below belongs to the swiped population'}>
                    swiped
                  </span>
                )}
              </td>
              <td>{c.targets}</td>
              <td>{c.hit}</td>
              <td>{c.avoided || ''}</td>
              <td>{c.absorbed || ''}</td>
              <td>{fmt.num(c.damage)}</td>
              <td className="l muted">
                {c.blocked_by.map((k) => k.split('|')[0]).join(', ')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function AoePanel({ data, err, base }) {
  const [open, setOpen] = useState(null)
  const jousted = useJoust()
  const pinned = useMiniPins()

  if (err) return <p className="err">{err}</p>
  if (!data) return <p className="muted">Loading…</p>
  if (data.pruned) {
    return (
      <p className="muted">
        No AoEs — this run&apos;s raw events were pruned. The tables read from
        frozen rollups and are unaffected.
      </p>
    )
  }
  if (!data.aoes?.length) {
    return (
      <p className="muted">
        Nothing in this selection reached {data.min_targets} raiders at once
        more than once, and nothing in it is on the ACT spell-timer list.
      </p>
    )
  }

  const key = (r) => `${r.source}|${r.ability}`
  const known = (r) => data.learned?.[key(r)]
  /* Δ is against the number the countdown is actually going to use, which is
     the learned one wherever there is one — comparing this fight to a list
     entry the panel has already stopped believing measures nothing. */
  const baseline = (r) => known(r)?.base_s ?? r.reported_s
  const delta = (r) => (baseline(r) && r.observed_s
    ? r.observed_s - baseline(r) : null)

  const columns = [
    {
      key: 'ability', label: 'AoE', align: 'l', fixed: true,
      render: (r) => (
        <span className="name">
          {/* The marks, ahead of the name: they are inputs, and an input that
              sits after the thing it acts on reads as a result of it. The same
              stacked pair the dashboard's panel carries, from one component —
              two places to mark an ability is fine, two ways to draw the mark
              is how they come to mean different things. */}
          <MarkPills row={r} jousted={isJousted(jousted, r)}
                     pinned={isMiniPinned(pinned, r)} />
          {r.ability}
          <DtypePill row={r} />
          {/* A CONDITION, not a cast: it kept meeting the raid-wide anchor
              second after second, which is a damage shield or an aura rather
              than something the mob cast at the raid (aoes.SUSTAINED_RUN). It
              stays listed — it did reach the raid and that is what this tab
              records — but it is marked, and it never gets a countdown. */}
          {r.sustained && (
            <span className="selfmark"
                  title={`Sustained: ${r.run_s}s of raid-wide hits in a row per burst.`
                    + ' A damage shield or aura, not a timed cast — no countdown.'}>
              shield
            </span>
          )}
          <button
            className="expandcol"
            onClick={(e) => { e.stopPropagation(); setOpen(open === key(r) ? null : key(r)) }}
            title={open === key(r) ? 'Hide casts' : 'Show every cast'}
          >{open === key(r) ? '▾' : '…'}</button>
        </span>
      ),
      sortValue: (r) => r.ability,
    },
    { key: 'source', label: 'From', align: 'l', render: (r) => r.source },
    { key: 'casts', label: 'Casts' },
    {
      key: 'reported_s', label: 'ACT timer',
      render: (r) => (r.reported_s != null
        ? (
          <span>
            {r.reported_s}s
            {/* What to type into ACT instead. Only ever shown next to the
                number it disagrees with, because that is the edit. */}
            {r.suggested_s && (
              <span className="suggest"
                    title={`${r.observed_agree} intervals in this log agree on`
                      + ` ${r.suggested_s}s, not ${r.reported_s}s`}>
                ⇢{r.suggested_s}s
              </span>
            )}
          </span>
        )
        : <span className="muted" title="Not in the ACT spell-timer list">—</span>),
      sortValue: (r) => r.reported_s ?? null,
    },
    {
      /* WHAT THE SITE HAS LEARNED, from every raid on this mob rather than
         from this selection. It is the number the countdown counts with when
         it exists, so it belongs beside the ACT entry it replaced — and when
         it does not exist yet, the count of clean intervals says how far off
         it is (`aoelearn.MIN_AGREE`). */
      key: 'base_s', label: 'Measured',
      render: (r) => {
        const k = known(r)
        if (!k) return <span className="muted">—</span>
        if (k.base_s) {
          return (
            <span title={`${k.base_agree} clean intervals across ${k.base_fights}`
              + ' fights, every raid on this site. This is what the countdown uses.'}>
              {k.base_s}s
            </span>
          )
        }
        /* MEASURED AND STILL NOT USED is a third state, and it needs the
           because on the end. Everywhere else a bracketed number means "not
           enough of it yet" and one more fight fixes it; here more fights make
           it worse, because every one of them counts the same several mobs
           (`aoes.several_bodies`). Two halves of The Emerald Halls rumbler had
           21 agreeing intervals saying 28.7s against ACT's 50. */
        if (k.several_bodies && k.clean_s) {
          return (
            <span className="muted" title={BODIES_WHY(r, k)}>
              ({k.clean_s}s){' '}
              <span className="selfmark">{k.several_bodies === 'splits'
                ? 'splits' : 'not 1 mob'}</span>
            </span>
          )
        }
        return (
          <span className="muted"
                title={k.clean_s
                  ? `${k.clean_s}s so far, on ${k.base_agree} clean interval(s) across`
                    + ` ${k.base_fights} fight(s) — not enough to replace the ACT`
                    + ' number yet'
                  : 'No clean cycles yet — every one we have was under a reuse debuff'}>
            {k.clean_s ? `(${k.clean_s}s)` : '—'}
          </span>
        )
      },
      sortValue: (r) => known(r)?.base_s ?? null,
    },
    {
      key: 'observed_s', label: 'Observed',
      /* Confidence is the number of intervals that agreed, and it is printed
         rather than folded into a badge: two agreeing gaps is a guess, twenty
         is a measurement. */
      render: (r) => (r.observed_s != null
        ? <span title={`${r.observed_agree} intervals agreed`
            + (r.observed_swiped
              ? ' — and every one of them was cast under a reuse debuff, so this'
                + ' is not this mob\'s own timer'
              : '')
            + (r.missed_hint ? ` · ${r.missed_hint} cast(s) look missed` : '')}>
            {r.observed_s}s
            {r.observed_agree < 3 && <span className="selfmark">?</span>}
            {r.observed_swiped && <span className="selfmark">swiped</span>}
          </span>
        : <span className="muted" title="No interval repeated — too few casts">—</span>),
      sortValue: (r) => r.observed_s ?? null,
    },
    {
      /* Whether a reuse debuff moves THIS ability, and by how much. The whole
         reason the two columns either side of it can be trusted: before this
         was separated out, "observed disagrees with the ACT timer" had two
         explanations and no way to choose. */
      key: 'swipe', label: 'Swiped',
      render: (r) => {
        const k = known(r)
        const seen = r.swiped_casts || 0
        if (!seen && !k?.swipe_factor) return ''
        const v = k?.swipe_verdict
        return (
          <span className={v === 'affected' ? 'swipehit' : 'muted'}
                title={`${seen} of ${r.casts} casts here started under a reuse debuff.`
                  + (k?.swipe_factor
                    ? ` Site-wide this ability measures ×${k.swipe_factor}`
                      + ` (clean ${k.clean_s}s vs ${k.swiped_s}s under it).`
                    : ' Not enough of both kinds to compare yet.')
                  + (v === 'affected' ? ' The countdown is adjusted for it.'
                    : v === 'immune' ? ' Measured not to move — no adjustment.'
                      : ' Unconfirmed — the countdown plans the normal timer'
                        + ' and runs on past it.')}>
            {v === 'affected' ? `×${k.swipe_factor}`
              : v === 'immune' ? 'no'
                : seen ? `${seen}?` : '?'}
          </span>
        )
      },
      sortValue: (r) => known(r)?.swipe_factor ?? null,
    },
    {
      key: 'delta', label: 'Δ',
      render: (r) => {
        const d = delta(r)
        if (d == null) return ''
        const off = Math.abs(d) / r.reported_s
        return (
          <span
            style={off > 0.15 ? { color: 'var(--warning)' } : undefined}
            title={r.instances_hint
              ? `Or ${r.instances_hint} mobs of this name casting on their own timers`
              : undefined}
          >
            {d > 0 ? '+' : ''}{d.toFixed(1)}s
            {r.instances_hint ? <span className="selfmark">*</span> : null}
          </span>
        )
      },
      sortValue: (r) => { const d = delta(r); return d == null ? null : Math.abs(d) },
    },
    { key: 'median_targets', label: 'Targets', render: (r) => r.median_targets },
    {
      key: 'blocked_pct', label: 'Covered',
      render: (r) => (r.blocked
        ? <span title={`${r.blocked} of ${r.casts} casts' targets avoided or absorbed it`}>
            {r.blocked_pct}%
          </span>
        : ''),
      sortValue: (r) => r.blocked_pct,
    },
    { key: 'damage', label: 'Damage', render: (r) => fmt.num(r.damage) },
    {
      key: 'fights', label: 'Fights', render: (r) => (r.fights > 1 ? r.fights : ''),
      sortValue: (r) => r.fights,
    },
  ]

  return (
    <div className="card">
      <div className="drillhead">
        <h2>Incoming AoEs</h2>
        <span className="muted">
          on the ACT timer list, or {data.min_targets}+ raiders at once ·
          ACT timer vs what the log shows
        </span>
      </div>
      <SortableTable
        columns={columns}
        rows={data.aoes}
        prefsKey="zonerun:aoes"
        defaultSort={{ key: 'damage', dir: 'desc' }}
        rowKey={key}
        selectedKey={open}
        onRowClick={(r) => setOpen(open === key(r) ? null : key(r))}
      />
      {open && data.aoes.some((r) => key(r) === open) && (
        <>
          <h3 style={{ marginTop: 12 }}>
            Every cast — {data.aoes.find((r) => key(r) === open).ability}
          </h3>
          <Casts row={data.aoes.find((r) => key(r) === open)} base={base} />
        </>
      )}
      {data.pruned_encounters > 0 && (
        <p className="note">
          {data.pruned_encounters} of the selected fights had their events pruned
          and contribute nothing here.
        </p>
      )}
      <p className="note">
        An ability ACT&apos;s list knows counts every landing, however few it
        found. One it does not has to reach {data.min_targets} raiders to leave
        a trace wide enough to see, so its cast count is a floor. Observed is
        the shortest interval that repeats — a cast we missed can only make a
        gap longer.
      </p>
    </div>
  )
}
