# Coach engine and raid report

Index: `ARCHITECTURE.md`.

Coaching compares what an ability *should* do at the player's stats (Census)
against what it *did* (the parse). The whole engine is intact behind `coach_api`
and the Insights tab, which is hidden — one commented line in `ParseView.jsx`'s
`TABS` turns it back on.

## Engine (`coach/`)

- `descriptive.py` — session currencies: DPS, crit and autoattack share, cast
  estimates, idle-GCD estimate, cure latency, rez responsiveness. Also owns the
  ROLE map (tank/healer/dps/utility), which is **not** the class tree in
  `classtree.py`.
- `fit.py` — observed vs Census.
- `replay.py` — what-if stat marginals and tier upgrades.
- `advisor.py` — report assembly, persisted to `coach_reports`.
- `raidreport.py` — the per-night report.

API: `GET|POST /api/sessions/{id}/coach`, `GET /api/sessions/{id}/raid-report`,
`POST /api/sessions/{id}/calibration`. Pages: Coach, RaidReport, Calibration.

### Model

**Census is a prior; the parse is the evidence.** EoF damage model:
`expected = base_mid*(1+basemod/100) + min(abilitymod, base_mid/2)`. The fit
reconciles observed non-crit means into a per-ability coefficient
(observed/expected — Census drifts a lot on TLE), and all what-if math scales
through it. Crit multiplier is fitted per ability, default 1.3. On multi-effect
spells each hit is assigned to the nearest expected effect and the biggest
cluster is fitted — under-use a spell rather than misread it.

- **Stat marginals are differences of `predicted_damage`**, which is monotone in
  every stat by construction. Reuse only pays on cooldown-locked abilities
  (median inter-cast gap ≤ 1.25× effective recast); cast speed only converts to
  damage when the rotation is not idle.
- **Tier upgrades cap at Master (tier 9)** — higher tiers do not exist on TLE and
  Grandmaster is a class choice. Spell lines cached via `ensure_spell_lines`.
- **Spellbook join** — log base name → the character's highest scribed version at
  or below their level, with `spell_overrides` applied on top.
- **Calibration** — a session flagged via `POST /calibration` (auto-pins) is
  per-ability ground truth; its coefficients override later reports for that
  character (confidence `calibrated`). It captures stats at flag time, so
  recalibrate after big gear changes.
- With no Census snapshot the report degrades to currencies + findings
  (`no_census`); a Census outage only costs the tier-upgrade section.

### The five correctness rules

1. **Cast ground truth** (`parser/flavor/`). `You prepare` lines resolve to
   ability names via article-strip, `to inflict X on <tgt>`, and per-class prose
   maps. **Only spells with a cast bar print prepare lines** — instant casts
   never do. `descriptive.py` discriminates: prepared → real count; in the
   spellbook but never prepared → instant spell, initiation-estimated; in
   neither → buff/item proc, zero casts (so procs stop polluting idle%).
   `currencies.cast_source` says which mode ran.
2. **Two-point calibration** (`fit._solve_two_point`). Dummy parses at two abmod
   values ≥100 apart (stats captured per session in `sessions.calib_stats_json`)
   solve the true base piecewise — uncapped/capped/mixed hypotheses, 10%
   tolerance. With a solve the fit swaps Census base for truth
   (`base_source: calibrated2`) and the abmod cap in every marginal becomes real.
   Until a second point exists the report carries a
   `calibration_second_point` finding.
3. **k-spread = debuff measurement** (`fit.apply_calibration`). A dummy fit never
   overwrites a healthy raid fit: `k_dummy` rides alongside and
   `debuff_uplift = raid_k / dummy_k` is medianed per damage school into
   `report.debuff_uplift`. Dummy k substitutes only on a thin sample (<5
   non-crits).
4. **Ability catalog** (`census/catalog.py`). Populated from every cached Census
   spell, plus proc'd names flagged from the effect grammar. Curated rows always
   win. Consumers: `fit.spellbook` drops pet-kit names from the player join, and
   a k sanity gate (0.2–12) marks the rest `suspect_join` — excluded from
   marginals and upgrades, surfaced as a finding.
5. **Healer/utility currencies.** HP-deficit reconstruction in `statsroll` (full
   HP assumed at pull; wards never touch HP) yields `overheal_est`,
   `save_count` and `ward_bleedthrough` per actor. Logger-only debuff uptime
   (`descriptive._debuff_uptime`) uses real cast starts and Census durations
   against burn windows. All flagged as estimates in the UI.

Plus an **engagement classifier** (`raidreport`): catalog procs never anchor; in
the opening 2s an ability firing ≤1s after being hit is a reactive proc and is
skipped; the logger's own prepare line is an exact anchor (`anchor: cast`); the
remainder keeps the low-confidence flag.

Still open: rez/time-dead next-action proxies, the DoT tier-upgrade tick tail,
reuse-marginal rotation displacement, and the abmod marginal — which needs the
two dummy parses run and flagged.

## Raid report (`coach/raidreport.py`)

Computed on demand from stored events, no schema change. Per encounter and per
night, for every raider in the log: damage/share/DPS, deaths, time dead (death →
next own action), **death DPS cost** (alive-DPS × time dead), cures delivered,
rez delay, heals/wards/power.

**Engage timing is the gap between the pull and a raider's first action**, and
`engage_anchor` names what stopped the clock:

| anchor | line |
| --- | --- |
| `cast` | the logger's own prepare line — exact, always high confidence |
| `autoattack` / `ability` / `pet` | damage on a non-ally, or positive threat |
| `autoattack` / `ability` | an *attempted* swing the mob avoided |
| `heal` / `cure` / `rez` | a heal, a `relieves`/`dispels` cure, or a rez |

Never anchors: ward absorbs (the line prints when the mob swings), catalog procs,
and an ability inside the opening 2s that fired ≤1s after the player was hit.
Anything else inside the opening 2s is flagged low confidence — a pre-pull HoT
ticks the instant the pull lands and the line cannot say which it was. The night
rollup averages named-fight delays only and carries `engage_anchors`.

Two ways of reading a raider as absent were fixed in v3 and are pinned by
`test_engagement.py`: counting only hostile actions dated healers to their first
damage, and a missed opener dated a caster to the next spell that landed.
Remaining limit: only the uploader's cast *starts* are logged, so for everyone
else a long cast is dated when it lands.
