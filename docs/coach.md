# eq2advanced — Coach engine and raid report

Part of the architecture reference. Index: `ARCHITECTURE.md`.

## Coach engine

`coach/` — `descriptive.py` (session currencies: DPS/crit/autoattack share,
cast estimates, idle-GCD estimate, cure latency, rez responsiveness),
`fit.py` (observed vs Census), `replay.py` (what-if stat marginals + tier
upgrades), `advisor.py` (report assembly, persisted to `coach_reports`),
`raidreport.py`. API: `GET|POST /api/sessions/{id}/coach`,
`GET /api/sessions/{id}/raid-report`, `POST /api/sessions/{id}/calibration`.
The whole engine is intact behind `coach_api` and the Insights tab, which is
hidden for now — one commented line in `TABS` (`ZoneRun.jsx`) turns it back on.
Pages: Coach, RaidReport, Calibration.

- **Census is a prior, the parse is the evidence.** Damage model (EoF era):
  `expected = base_mid*(1+basemod/100) + min(abilitymod, base_mid/2)`; the fit
  reconciles observed non-crit means into a per-ability coefficient
  (observed/expected — ~2-4.6x on real TLE data, Census drifts a LOT) and all
  what-if math scales through it. Crit multiplier fitted per ability
  (crit mean / non-crit mean), default 1.3. Multi-effect spells: each hit is
  assigned to the nearest expected effect and the biggest cluster is fitted —
  we under-use a spell rather than misread it.
- **Stat marginals are differences of `predicted_damage`**, which is monotone
  in every stat by construction. Reuse only pays on cooldown-locked abilities
  (median inter-cast gap ≤ 1.25× effective recast); cast speed only converts
  to damage when the rotation isn't idle.
- **Tier upgrades cap at Master (tier 9)** — Ancient/Celestial don't exist on
  TLE and Grandmaster is a class choice, not a scroll. Spell lines are cached
  via `ensure_spell_lines`; Census's comma OR-list works for `id=` but
  silently returns NOTHING for `crc=` (verified live 2026-08-02), so
  `spells_by_crcs` is one request per crc, marker-gated in `settings`.
- **Spellbook join**: log base name → the character's highest scribed version
  at or below their level (Bobby has above-level RoK pre-scribes), overrides
  from `spell_overrides` applied on top.
- **Calibration**: a session flagged via `POST /calibration` (auto-pins) is
  per-ability ground truth — its coefficients override every later report for
  that character (confidence `calibrated`). Uses current snapshot stats, so
  recalibrate after big gear changes.
- Report degrades gracefully with no Census snapshot (currencies + findings,
  `no_census` finding); a Census outage only costs the tier-upgrade section.

### Coach correctness — the five rules v1 was missing

1. **Cast ground truth** (`parser/flavor/`): `You prepare` lines resolve to
   ability names — generic article-strip ("the Bloodcloud" → Bloodcloud) +
   `to inflict X on <tgt>` + per-class prose maps (`necromancer.py`, verified
   on bobby.txt; every fixture flavor resolves). CRITICAL WRINKLE: only spells
   with a cast bar print prepare lines — instant casts (Lifetap, 344 hits,
   zero prepares) never do. Discriminator in `descriptive.py`: prepared →
   real count; in the spellbook but never prepared → real instant spell,
   initiation-estimated; in neither → buff/item proc, ZERO casts (Lich's
   Siphoning et al. stop polluting idle%). `currencies.cast_source` says
   which mode ran; `casts` also lands on the damage-kind rollup row.
2. **Two-point calibration** (`fit._solve_two_point`): dummy parses at two
   abmod values (≥100 apart; stats are CAPTURED per session at flag time in
   `sessions.calib_stats_json`) solve the TRUE base piecewise
   (uncapped/capped/mixed hypotheses, 10% tolerance). With a solve, the fit
   row swaps Census base for truth (`base_source: calibrated2`) — the abmod
   cap in every marginal becomes real. Until a second point exists the report
   carries a `calibration_second_point` finding.
3. **k-spread = debuff measurement** (`fit.apply_calibration`): the dummy fit
   NEVER overwrites a healthy raid fit — `k_dummy` rides alongside and
   `debuff_uplift = raid_k / dummy_k` per ability, medianed per damage school
   into `report.debuff_uplift`. Dummy k substitutes only when the session
   sample is thin (<5 non-crits).
4. **Ability catalog** (`census/catalog.py`): populated from every cached
   census spell (name + base_name, class, unit=player) and — via the
   effect-grammar `proc` kind ("may cast X on ...") — the proc'd names get
   `proc=1`. Curated rows (pet kits + observed buff/item procs from
   bobby.txt) always win over census rows. Consumers: `fit.spellbook` drops
   pet-kit names from the player join; a **k sanity gate** (0.2–12; Master's
   Strike misjoined at 54.6) marks the rest `suspect_join` → excluded from
   marginals/upgrades, surfaced as a finding.
5. **Healer/utility currencies**: HP-deficit reconstruction in `statsroll`
   (full HP assumed at pull; wards never touch HP) → `overheal_est` +
   `save_count` (heal landing at ≥60% of the target's worst deficit) +
   `ward_bleedthrough`, persisted per actor and rolled into raid report +
   coach findings. Logger-only debuff uptime (`descriptive._debuff_uptime`):
   real cast starts + Census durations vs burn windows (rolling 10s raid
   damage ≥1.5× encounter mean). All flagged as estimates in the UI.
6. **Engagement classifier** (`raidreport`): catalog-proc abilities never
   anchor; inside the opening 2s an ability that fires ≤1s after being hit is
   a reactive proc (skipped); the logger's own prepare line is an exact
   high-confidence anchor (`anchor: cast`); the remainder keeps the
   low-confidence flag.

Still open: rez/time-dead next-action proxies, the DoT tier-upgrade tick tail,
reuse-marginal rotation displacement — and the abmod marginal, which is only as
good as its calibration points. Lindsay still needs to RUN the two dummy parses.

## Raid Report

`coach/raidreport.py`, computed on demand from stored events (no schema
change). Per encounter + per night, all raiders in the log: damage/share/DPS,
deaths, time dead (death → next own action), **death DPS cost** (alive-DPS ×
time dead), cures delivered, rez delay, heals/wards/power.

**Engagement timing with the proc caveat** (verified on the Zylphax pull —
pre-pull wards/procs flood the log ~1s after the real opener). Engage is the
gap between the pull and a raider's FIRST ACTION, and `engage_anchor` names
which kind of action stopped the clock:

| anchor | line that fired it |
| --- | --- |
| `cast` | the logger's own prepare line — exact, always high confidence |
| `autoattack` / `ability` / `pet` | damage on a non-ally, or positive threat |
| `autoattack` / `ability` | an *attempted* swing the mob avoided (v3) |
| `heal` / `cure` / `rez` | a heal, a `relieves`/`dispels` cure, or a rez (v3) |

Never anchors: ward absorbs (the line prints when the MOB swings), catalog
procs (any type), and an ability inside the opening 2s that fired ≤1s after
the player was hit (reactive damage-shield correlation). Anything else inside
the opening 2s is flagged low confidence — a pre-pull HoT ticks the instant
the pull lands and the line cannot say which it was. Night rollup averages
named-fight delays only and carries `engage_anchors` (kind → count).

**v3 (2026-08-03) fixed two ways of reading a raider as absent**: only hostile
actions counted, so a templar healing from the first second of Sawtooth the
Ancient scored 13s (their first *damage*) instead of 2s, and a wizard whose
opener missed was dated to the next spell that landed. `test_engagement.py`
pins both. The remaining honest limitation: only the uploader's cast STARTS
are logged, so for everyone else a 4s cast is dated when it lands.

