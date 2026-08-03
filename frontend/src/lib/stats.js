/* Derived per-actor metrics computed from the agg payload's ability rows.
   One implementation shared by the Damage/Healing tabs and the compare panel. */

export const MELEE_BUCKETS = new Set(['(melee)', '(multi attack)', '(aoe attack)', '(flurry)'])

/* Per player-credit key: damage-kind rollup of crits/hits/casts and the
   autoattack share. Pet rows credit their owner via rollup_key. */
export function damageDerived(abilities) {
  const by = {}
  for (const r of abilities || []) {
    if (r.kind !== 'damage') continue
    const k = r.rollup_key || r.source_key
    if (!k) continue
    const d = by[k] ??= { total: 0, hits: 0, crits: 0, casts: 0, auto: 0 }
    d.total += r.total || 0
    d.hits += r.hits || 0
    d.crits += r.crits || 0
    d.casts += r.casts || 0
    if (MELEE_BUCKETS.has(r.ability)) d.auto += r.total || 0
  }
  return by
}

export const critPct = (d) => (d && d.hits ? (100 * d.crits) / d.hits : null)
export const autoPct = (d) => (d && d.total ? (100 * d.auto) / d.total : null)
export const castsPerMin = (d, duration) => (d && d.casts && duration ? d.casts / (duration / 60) : null)

/* Report columns for an arbitrary encounter selection: sum the run report's
   per-encounter player rows across the selected fights, keyed by name. */
export function reportRollup(report, selIds) {
  if (!report || !selIds) return null
  const want = new Set(selIds)
  const by = {}
  for (const enc of report.encounters || []) {
    if (!want.has(enc.encounter.id)) continue
    for (const p of enc.players) {
      const n = by[p.name] ??= {
        engage: [], engage_low: 0, death_dps_lost: 0, overheal_est: 0, saves: 0,
        time_dead_s: 0, deaths: 0, cures: 0, rez: [],
      }
      if (p.engage_delay_s != null && enc.encounter.is_named) {
        n.engage.push(p.engage_delay_s)
        if (p.engage_confidence === 'low') n.engage_low += 1
      }
      if (p.rez_delay_s != null) n.rez.push(p.rez_delay_s)
      n.death_dps_lost += p.death_dps_lost || 0
      n.overheal_est += p.overheal_est || 0
      n.saves += p.saves || 0
      n.time_dead_s += p.time_dead_s || 0
      n.deaths += p.deaths || 0
      n.cures += p.cures || 0
    }
  }
  const avg = (xs) => (xs.length ? Math.round((xs.reduce((s, x) => s + x, 0) / xs.length) * 10) / 10 : null)
  for (const n of Object.values(by)) {
    n.avg_engage_delay_s = avg(n.engage)
    n.avg_rez_delay_s = avg(n.rez)
  }
  return by
}

/* Death rows for the Defense tab: one row per (fight, player with deaths). */
export function deathRows(report, selIds) {
  if (!report || !selIds) return []
  const want = new Set(selIds)
  const out = []
  for (const enc of report.encounters || []) {
    if (!want.has(enc.encounter.id)) continue
    for (const p of enc.players) {
      if (!p.deaths) continue
      out.push({
        encounter: enc.encounter, name: p.name, deaths: p.deaths,
        time_dead_s: p.time_dead_s, death_dps_lost: p.death_dps_lost,
      })
    }
  }
  return out.sort((a, b) => a.encounter.started_ts - b.encounter.started_ts)
}
