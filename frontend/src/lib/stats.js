/* Derived per-actor metrics computed from the agg payload's ability rows.
   One implementation shared by the Damage/Healing tabs and the compare panel. */

export const MELEE_BUCKETS = new Set(['(melee)', '(multi attack)', '(aoe attack)', '(flurry)'])

const AVOID_COLS = ['misses', 'parries', 'ripostes', 'dodges', 'blocks']

/* Per player-credit key: damage-kind rollup of crits/hits/casts, the
   autoattack share, and the split of where the damage came from. Pet rows
   credit their owner via rollup_key.

   The three composition buckets are mutually exclusive and cover the total:
   `auto` is the melee swing buckets, `proc` is anything the ability catalog
   flags as firing on its own (gear and buff procs), `cast` is the remainder —
   what the player actually pressed. */
export function damageDerived(abilities) {
  const by = {}
  for (const r of abilities || []) {
    if (r.kind !== 'damage') continue
    const k = r.rollup_key || r.source_key
    if (!k) continue
    const d = by[k] ??= {
      total: 0, hits: 0, crits: 0, casts: 0, auto: 0, proc: 0, cast: 0,
      pet: 0, swings: 0, avoided: 0, resists: 0,
    }
    const amt = r.total || 0
    d.total += amt
    d.hits += r.hits || 0
    d.crits += r.crits || 0
    d.casts += r.casts || 0
    d.swings += r.swings || 0
    d.resists += r.resists || 0
    for (const c of AVOID_COLS) d.avoided += r[c] || 0
    if (r.rollup_key === k && r.source_key !== k) d.pet += amt
    if (MELEE_BUCKETS.has(r.ability)) d.auto += amt
    else if (r.proc) d.proc += amt
    else d.cast += amt
  }
  return by
}

export const critPct = (d) => (d && d.hits ? (100 * d.crits) / d.hits : null)
export const autoPct = (d) => (d && d.total ? (100 * d.auto) / d.total : null)
export const procPct = (d) => (d && d.total ? (100 * d.proc) / d.total : null)
export const castsPerMin = (d, duration) => (d && d.casts && duration ? d.casts / (duration / 60) : null)

/* How far a value sits from its peers, as a signed strength in [-1, 1]:
   positive is good, negative is bad, 0 is "nothing worth saying".

   Measured against the MEDIAN as a fraction of it, not as a tercile rank. A
   rank says someone is in the bottom third even when the whole field is
   within a point of each other — which is exactly what crit becomes in later
   expansions, where everyone caps and the worst crit in the raid is 98% of
   the best. Distance-from-median keeps that field uncolored, and still pulls
   a real outlier to full strength. `full` is the fraction off the median that
   earns the strongest tint (25% by default). */
export function rankScale(value, peers, { worse = false, full = 0.25 } = {}) {
  if (value == null) return 0
  const xs = peers.filter((v) => v != null).sort((a, b) => a - b)
  if (xs.length < 4) return 0
  const h = xs.length / 2
  const mid = xs.length % 2 ? xs[Math.floor(h)] : (xs[h - 1] + xs[h]) / 2
  // a median of zero (time dead, deaths) has no scale of its own — fall back
  // to the top of the field so the spread still means something
  const denom = Math.abs(mid) || Math.abs(xs[xs.length - 1]) || 0
  if (!denom) return 0
  const t = Math.max(-1, Math.min(1, (value - mid) / denom / full))
  return worse ? -t : t
}

/* Strength -> cell color: one hue each way, mixed into the body text so the
   tint grows with the gap instead of flipping on at a threshold. Below the
   noise floor nothing is claimed at all. */
export function rankColor(t) {
  const m = Math.round(Math.abs(t || 0) * 85)
  if (m < 12) return undefined
  return `color-mix(in oklab, var(${t > 0 ? '--success' : '--danger'}) ${m}%, var(--text))`
}

/* "Why is my DPS low?" — DPS is uptime x activity x hit size x crit rate, so
   compare each factor against the best peer instead of the bottom line, and
   name the one with the biggest gap. Peers are same-role raiders.
   `deadSecondsOf` must come from the raid report: `encounter_actor_stats`
   has a `time_dead_s` column but the roller never writes it, so reading it
   off the aggregate payload would report everyone as permanently alive. */
export function decompose(actor, peers, derived, duration, deadSecondsOf = () => 0) {
  const d = derived[actor.key]
  if (!d || !d.total) return null
  const of = (a) => derived[a.key]
  const factors = [
    {
      key: 'dps', label: 'DPS', why: 'overall rate',
      get: (a) => (a.damage || 0) / duration, fmt: (v) => Math.round(v).toLocaleString(),
    },
    {
      key: 'cpm', label: 'Casts/min', why: 'activity',
      get: (a) => (of(a)?.casts ? of(a).casts / (duration / 60) : null), fmt: (v) => v.toFixed(1),
    },
    {
      key: 'avg', label: 'Average hit', why: 'gear, buffs and spell tiers',
      get: (a) => (of(a)?.hits ? of(a).total / of(a).hits : null), fmt: (v) => Math.round(v).toLocaleString(),
    },
    {
      key: 'crit', label: 'Crit %', why: 'crit chance',
      get: (a) => (of(a)?.hits ? (100 * of(a).crits) / of(a).hits : null), fmt: (v) => `${Math.round(v)}%`,
    },
    {
      key: 'uptime', label: 'Alive %', why: 'time alive',
      get: (a) => 100 * (1 - Math.min(1, (deadSecondsOf(a) || 0) / duration)),
      fmt: (v) => `${Math.round(v)}%`,
    },
  ]
  const out = []
  for (const f of factors) {
    const mine = f.get(actor)
    const others = peers.filter((p) => p.key !== actor.key).map(f.get).filter((v) => v != null)
    if (mine == null || !others.length) continue
    const best = Math.max(...others)
    out.push({ ...f, mine, best, gapPct: best > 0 ? (100 * (best - mine)) / best : 0 })
  }
  const ranked = out.filter((f) => f.key !== 'dps').sort((a, b) => b.gapPct - a.gapPct)
  return { factors: out, worst: ranked[0] || null }
}

/* Consistency across fights: a player who swings between 4% and 12% of raid
   damage has an execution problem, which is different coaching from someone
   who is evenly low. */
export function consistency(shares) {
  const xs = shares.filter((v) => v != null)
  if (xs.length < 3) return null
  const min = Math.min(...xs), max = Math.max(...xs)
  const mean = xs.reduce((s, x) => s + x, 0) / xs.length
  const sd = Math.sqrt(xs.reduce((s, x) => s + (x - mean) ** 2, 0) / xs.length)
  return { min, max, mean, sd, cv: mean ? sd / mean : null, n: xs.length }
}

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
        engage: [], engage_low: 0, engage_anchors: {}, death_dps_lost: 0,
        overheal_est: 0, saves: 0, time_dead_s: 0, deaths: 0, cures: 0, rez: [],
      }
      if (p.engage_delay_s != null && enc.encounter.is_named) {
        n.engage.push(p.engage_delay_s)
        if (p.engage_confidence === 'low') n.engage_low += 1
        // what kind of action started their fight — a 6s of heals reads
        // differently from a 6s of swings
        n.engage_anchors[p.engage_anchor] = (n.engage_anchors[p.engage_anchor] || 0) + 1
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
