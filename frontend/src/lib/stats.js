/* Derived per-actor metrics computed from the agg payload's ability rows.
   One implementation shared by the Damage/Healing tabs and the compare panel. */

export const MELEE_BUCKETS = new Set(['(melee)', '(multi attack)', '(aoe attack)', '(flurry)'])

/* Pet attacks the OWNER presses. The log credits these to the pet because the
   pet is what swings, but a button the player pressed is the player's
   (Lindsay, 2026-08-11) — so they are never folded into a pet row and never
   counted as pet damage, wherever the parse is drawn. They are the necromancer
   abilities that appear in raids across every archetype and in NONE of the
   three fights where he cast nothing himself, which is the same fact seen from
   the other side. */
export const PET_COMMANDED = new Set([
  'Shadow Step', 'Shockwave',              // necromancer: pressed pet attacks
  /* Conjuror buffs the PET delivers. Census names each damage line in the
     caster's own effect text — Blazing Avatar "will cast Blaze on target of
     attack", Elemental Unity "will cast Force of the Elements", Plane Shift
     its three Planar procs, one per pet type. The pet swings them and the
     player cast them, which is Consume's rule (Lindsay): credit the player. */
  'Blaze', 'Force of the Elements',
  'Planar Igneous Flames', 'Planar Thunderous Roar', 'Planar Telluric Strike',
])

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
      pet: 0, swings: 0, avoided: 0, resists: 0, max: null,
    }
    const amt = r.total || 0
    d.total += amt
    d.hits += r.hits || 0
    // the biggest single line the player landed, whichever ability it was on.
    // A per-ability max is what the rollers store, so an actor's is the max of
    // theirs — and a pet's counts, the way its damage does.
    if (r.max != null) d.max = d.max == null ? r.max : Math.max(d.max, r.max)
    d.crits += r.crits || 0
    d.casts += r.casts || 0
    d.swings += r.swings || 0
    d.resists += r.resists || 0
    for (const c of AVOID_COLS) d.avoided += r[c] || 0
    if (r.rollup_key === k && r.source_key !== k && !PET_COMMANDED.has(r.ability)) d.pet += amt
    if (MELEE_BUCKETS.has(r.ability)) d.auto += amt
    else if (r.proc) d.proc += amt
    else d.cast += amt
  }
  return by
}

/* The biggest single line per actor, for a kind `damageDerived` does not do.
   The rollers store a max per ABILITY, so an actor's is the max over theirs;
   the healing tab reads this the way the damage tab reads `derived[k].max`.

   It is the one column a rate cannot stand in for — a 3M nuke and 3M of DoT
   ticks are the same DPS — which is why it is worth a column of its own. */
export function maxByActor(abilities, kind = 'heal') {
  const by = {}
  for (const r of abilities || []) {
    if (r.kind !== kind || r.max == null) continue
    const k = r.rollup_key || r.source_key
    if (!k) continue
    by[k] = by[k] == null ? r.max : Math.max(by[k], r.max)
  }
  return by
}

export const critPct = (d) => (d && d.hits ? (100 * d.crits) / d.hits : null)
export const autoPct = (d) => (d && d.total ? (100 * d.auto) / d.total : null)
export const procPct = (d) => (d && d.total ? (100 * d.proc) / d.total : null)

/* Where a value PLACES among its peers, as a signed strength in [-1, 1]:
   positive is good, negative is bad, 0 is the middle of the field.

   This used to measure distance from the peer median as a fraction of it, and
   that was unreadable in practice — the size of the gap and the size of the
   group both moved the color, so one column showed a red 9,662 DPS two rows
   above a green 1,868 and nothing on screen explained why. Position is the one
   thing a reader can check against the column they are already looking at: the
   top of the group is green, the bottom is red, the middle is neither.

   Ties share their placement, so a field where everyone is level (crit in the
   later expansions, where the whole raid caps) lands everybody near zero and
   draws no color — the honest answer, and the same one the old scale gave. */
export function rankScale(value, peers, { worse = false } = {}) {
  if (value == null) return 0
  const xs = peers.filter((v) => v != null)
  if (xs.length < MIN_PEERS) return 0
  const below = xs.filter((v) => v < value).length
  const tied = xs.filter((v) => v === value).length
  const t = (2 * (below + tied / 2)) / xs.length - 1
  return worse ? -t : t
}

/* A group smaller than this says nothing: with three tanks, "last of 3" is one
   bad pull, not a standing. Below it every cell goes uncolored rather than
   borrowing the whole raid's median, which is what used to put a templar's DPS
   on the same scale as an assassin's. */
export const MIN_PEERS = 4

/* Placement -> cell color: one hue each way, mixed into the body text so the
   tint grows with the standing instead of flipping on at a threshold. The
   middle of the field is below the noise floor and claims nothing. */
export function rankColor(t) {
  const m = Math.round(Math.abs(t || 0) * 85)
  if (m < 12) return undefined
  return `color-mix(in oklab, var(${t > 0 ? '--success' : '--danger'}) ${m}%, var(--text))`
}

/* The sentence the color is making, for the cell's tooltip — a color nobody can
   read is a color that should not be there. */
export function rankTitle(value, peers, groupLabel) {
  if (value == null) return undefined
  const xs = peers.filter((v) => v != null)
  if (xs.length < MIN_PEERS) return undefined
  const place = xs.filter((v) => v > value).length + 1
  const ord = place % 10 === 1 && place % 100 !== 11 ? 'st'
    : place % 10 === 2 && place % 100 !== 12 ? 'nd'
      : place % 10 === 3 && place % 100 !== 13 ? 'rd' : 'th'
  return `${place}${ord} of ${xs.length} ${groupLabel}`
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
