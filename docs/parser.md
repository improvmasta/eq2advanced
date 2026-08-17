# Parser, segmentation, attribution and ACT parity

Index: `ARCHITECTURE.md`. **Read this before touching the parser or
segmentation, and rerun the golden test after any change to either.**

Truth sources: `backend/tests/test_parser.py` (verbatim log lines) and
`test_golden.py` (the full `/home/lindsay/bobby.txt` fixture — 275,822 lines, 10
segments, 0 unmatched damage lines).

## The subject model

- `YOU` / `YOUR <Ability>` = the logging player, always.
- **A bare logger-name is the logger's own pet** (pets can share the owner's
  exact name). `<logger>'s <Ability>` = pet ability.
- Other single-token capitalized names are players; their pets conflate into them
  (community parse convention) until they upload their own logs.
- `<Owner>'s <lowercase pet>['s <Ability>]` = swarm-pet chain.
- Possessive hazard: an apostrophe inside a capitalized remainder is part of the
  name, not a possessive split.
- `focus` dtype = self-inflicted; excluded from DPS, rolled up as kind `self`.
- Logger deaths: `<Killer> has killed you.` plus `You regain consciousness!`.

## Encounter segmentation

EQ2 logs have no encounter markers. A segment is a combat-silence gap of **≥7s**
(ACT's ~6s idle timeout). Damage *and* avoided swings hold a fight open; zone
lines hard-cut. Corollary: a real mid-fight lull over the threshold splits the
fight, which is what ACT does too.

### `/act end`

EQ2 has no `/act` command, so the client writes its refusal into the log
(`Unknown command: 'act end'`) — the same channel ACT's own EQ2 plugin hears it
on. The uploader sends every line verbatim and `pipeline/redact.py` governs chat
prefixes only, so the line arrives.

`parser/classify.py` turns it into an `encounter_end` event and `segment_events`
hard-cuts on it like a zone line: the marker joins no segment, the next damage
line opens a new one, and **nothing trails into it**. Live, a segment carrying
`Segment.ended_by_cmd` commits at the next flush instead of waiting out
`CLOSE_S`, and the rebuild from raw makes the same cut. Only `end` is honoured.

### Naming and outcome

`pipeline.encounters.encounter_label` titles a segment after the **mob that took
the most damage in it** and sets `success` to whether that mob died. Labelling
from the kill line instead could never name a wipe (no kill line), which also
meant `success` could never record a 0.

This reproduces ACT's titles row for row, including pulls where the raid's damage
went into an add rather than the boss. Naming needs resolved entities, so it runs
in the write path (`ingest_writer.parse_session`, `live._flush`) rather than in
the pure segmenter. `zone_runs.success_count` counts named kills specifically.

**The rule is only as good as the mob/player split feeding it** — a boss
misclassified as a player is invisible to a rule that ranks mobs, and takes the
fight title, the raider table and `success` with it. See the self-heal veto below.

### A segment is only a FIGHT if the raid engaged it

`success` is 0/1/**NULL** and the rail marks only `success === 0`, so NULL reads
like a kill. The segmenter cuts on silence, so a night also produces segments
nobody fought (a DoT ticking between pulls, a proc pet being one-shot).
`encounter_label` requires damage into a mob from `_ENGAGE_KINDS` (`player`,
`own_pet`, `named_pet`) — **a swarm pet is a proc, not a decision**. Without it a
segment keeps a readable name but is `is_named` False, `success` NULL, and falls
into Trash.

The exception is why `success` cannot simply be NULL when the raid dealt no
damage: **a wipe where the boss kills everyone before a hit lands is an
attempt**. Ally deaths decide it — dead raiders mean the raid was there and lost.

The rail says the outcome in the fight's own name: green killed, red lost, each
with its own mark (red/green alone fails a colourblind reader). Trash stays muted
whatever happened to it. Green-for-killed is ours, not ACT's.

Known residual: on the reference night ACT cuts 61 encounters and we make 60. No
single threshold reproduces its set, and the EQ2 parser plugin does not decide
boundaries — its kill-ends-encounter branch is commented out and it only calls
`SetEncounter(...)`, so boundaries come from ACT core's inactivity timer.

## Class inference (`pipeline/classguess.py`)

The log never states a class but it states what people cast, and
`ability_catalog.class` knows who can cast what. Per player: take the distinct
ability names used, drop autoattack buckets, pet-kit names and **procs** (gear
fires those), then let each remaining name vote. A `characters` row with a Census
class overrides the vote outright.

- **Evidence pools across sessions, keyed by NAME** (`_evidence`, one query over
  the database). Per-file guessing gave the same person different answers in
  different raids. The answer is written back to every entity row with that name,
  so it reaches older raids without a reparse.
- **Shared spells vote in fractions.** A two-class ability is half a vote each —
  it cannot pick between the two but it is real evidence against the rest.
- **A margin rule beside the share rule.** A winner needs whole-vote evidence
  (`MIN_STRONG`), `MIN_SCORE` of weight, and either a majority of the weight cast
  or double the runner-up.

**Census by NAME is ground truth** (`census/roster.py`, `roster_classes`, schema
v19) — the game answering directly, without caring that half a log's ability
names have no Census spell row. A miss is an answer too and is cached the same
way (`found=0`), which `refine_bare_pets` needs.

Census is authoritative about NOW, never about the night of the raid, so it is
layered UNDER the timeline. **Order of authority per fight** (`resolve_class`):
what the fights on screen prove > the era the fight falls in > Census > the
pooled vote. Local evidence chooses WHEN, never WHAT, so a charmed pet casting
another class's abilities cannot promote its owner.

Requests are budgeted (`ROSTER_LOOKUP_BUDGET`) and run outside the parse's write
transaction; a Census outage costs a retry, never a parse and never a `found=0`
over a real character. Bulk backfill: `backend/tools/sync_roster.py --all`
(`--guilds` for guild tags). Needs a real `CENSUS_SERVICE_ID`.

## ACT parity

The reference encounter matches ACT exactly — all 25 players to the point of
damage (`test_act_parity_zylphax`). Two traps from that work:

- **`lastrowid` after `ON CONFLICT DO NOTHING` is garbage** — it is the
  connection-wide last-insert id from any table. Harmless on a fresh DB, corrupts
  ability attribution on any second session or reparse. Guard with `rowcount`.
- In the possessive owner slot the logger's name means the PLAYER, not the
  bare-name-is-pet rule, or their swarm pets never roll up.

### The ACT model, rule by rule

1. **Ward absorbs fold into the hit they mitigated** (`classify._pair_wards`).
   The log prints the absorb line before its hit line, and the hit line shows
   only bleed-through. ACT reconstructs the pre-ward hit: absorbed damage counts
   for the attacker AND the target's damage taken, while the warder separately
   keeps the full absorb as healing (asymmetric on purpose). The pairing key is
   the raw target string — normalize the logger's self-hit form to `YOU`. An
   absorb that never pairs (a fully-absorbed DoT tick prints no hit line) still
   counts as damage taken.
2. **Self-inflicted damage is excluded from DamageTaken as well as Damage.**
3. **Cures are `relieves` + `dispels` lines, any target, credited to the caster.**
   The real cure grammar is `X's Ability relieves Effect from Y`; parsing only
   `dispels` (buff strips) missed most of them.
4. **Deaths: the logger's bare-name pet death counts as the player** — ACT cannot
   tell same-name pets apart.
5. **PowerReplenish includes self-gains.** The drain grammar is a verb family
   (`confounds|zaps|smites|diseases|… draining N points of power`), not one verb.
6. **Damage-taken does NOT roll possessive pets into owners** — a possessive pet
   is its own combatant on the taken side; outgoing still rolls up
   (`statsroll.taken_key`).
7. **EncDPS/EncHPS denominator is Σ encounter durations**, the same for everyone.
   The EncHPS numerator is heals + wards, because ACT's Healed column includes
   wards.

Residuals: damage-taken runs 1–3% light (suspected intercepts — the log carries
no amount for one) plus boundary seconds. **Trailing-event trimming was tried
here and regressed cures/EncHPS — do not re-add it.**

### The corpse tail (`encounters.split_trailing_corpse`)

A mob's DoT keeps ticking for a few seconds after it dies and the silence rule
counted those ticks as combat. ACT ends the fight at the kill and opens a new
encounter for the tick.

**The rule is a SUFFIX operation and that is the whole design.** Walk back from
the end of a gap-cut segment over events that are only a dead mob still ticking;
the clock stops at the last real beat (an ally acting, a kill, or a live mob
swinging), and if the tail carries damage it becomes its own segment. It cannot
cut a chain pull in half.

Everything else was measured against ACT's reference zone view and rejected:
cutting when every engaged mob is dead, cutting at `You stop fighting.`, both
together, and cutting at a kill the logger stopped fighting on. `You stop
fighting.` is in the log and is **not** ACT's boundary — the logger drops combat
constantly while the rest of the raid keeps swinging.

ACT's XML exports then showed that the clock and the membership are different
questions: an encounter holds everything the timeout keeps together, but its
duration runs only to the GROUP's last action. So `last` is the last ALLY action
(ally damage/avoid, or a mob kill); mob damage never extends the clock and heals
never did. A corpse tick opens a new segment; a live mob's swing does not.

Still open, with evidence:
- ACT starts an encounter ~3s earlier when a pull opens with THREAT. Adding
  threat as a segment anchor was tried and moved the reference night the wrong
  way, so it needs its own investigation.
- The boss's own Damage column runs ~10% light — a `statsroll` question, not a
  segmentation one.
- ACT counts a mob's death on the mob's row, and does not roll another player's
  pet death into its owner.

## Rezzes, revives, intercepts, adjusted delay (schema v10, `PARSE_VERSION` 13)

- **Every rez family counts** (`RE_REZ`). The old regex matched one class's
  flavor only. It now takes an open verb
  (`petitions|calls forth|beseeches|invokes|implores|summons`) and identifies the
  line by its trailing `…resurrection.`, keeping the flavor in `extra` so an
  unseen family shows up as data rather than as a gap.
- **Revives.** The landing side prints for everyone in range (`X is revived!`,
  `X is resurrected!`, `You are revived!`), which fills
  `encounter_actor_stats.time_dead_s`: death → the first of {revive, acting
  again, end of fight}, clamped to the encounter. The raid report uses the same
  three-way rule and `test_agg_time_dead_matches_the_report` pins them together.
  **"Acting again" means THEIR OWN action.** A swarm keeps swinging over its
  owner's corpse and every one of those ticks rolls up to them, so a pet class's
  dead clock stopped the second they died: Bobby's 27s on Mayong's killing pull
  (2026-08-16) read as **0s**, while the 20s on Malkonis read true only because
  the same swarm had died with him. That also zeroed the raid report's
  damage-lost-while-dead, which is the number a death is actually judged by.
  Engagement timing is unaffected and still counts a pet swing as a deliberate
  opener.
- **`You lose consciousness!` is NOT a death** (`RE_KO` → type `ko`). It means
  incapacitated at 0 HP, and a heal that beats the timer undoes it with nothing
  lost. Bronir's 2026-08-16 log settles it: KO 14:13:28 → `You regain
  consciousness!` 14:13:29, then a real death 4s later with its killer named.
  Counting the KO line recorded 2 deaths in that fight for the one death in it.
  A `ko` is evidence, not a casualty: it counts nowhere and no longer makes a
  no-damage segment a wipe.
- **THE LOGGER'S OWN KILLER-LESS DEATH IS NOT IN THE LOG AT ALL**, and
  `pipeline/downs.py` recovers it. EQ2 announces a death two ways and neither
  covers this one: `<Killer> has killed you.` needs a killer to credit (a
  necromancer's Lifeburn leaves them at 1 HP and their own choker proc finishes
  it), and `Alas, <name> has died from pain and suffering.` is a broadcast about
  OTHER PEOPLE — zero `Alas, Bobby` in Bobby's logs, zero `Alas, Oktavia` in
  Oktavia's. With the KO line meaning something else, nothing prints.

  What does survive is the shape of a corpse: the logger stops acting, for the
  whole hole **nothing lands on them** — no damage, no heal, no ward, the same
  signature as their killer-credited deaths — and the hole ends with `You regain
  consciousness!`. So an **unpaired revive is a death**, dated to the last
  moment the log proves they were up (their own action, or anything landing on
  them; a swarm pet swinging over the corpse is not proof), flagged
  `F_INFERRED`, and counted in `deaths` with `deaths_inferred` beside it so the
  column can mark it and the recap can decline to name a killing blow.

  Measured on session 301 (Bobby, 2026-08-16): two holes, 27s on Mayong's
  killing pull and 20s on Malkonis D'Morte, each recorded as zero deaths, zero
  time dead, and a fight the raider was "active" for all but 3 seconds of.
  `MIN_DOWN_S` (5s) is the floor because a healed incapacitation is a
  ONE-second hole, and `outstanding` is deliberately not cleared by the logger
  acting again — a DoT of theirs ticking on a corpse would otherwise unpair a
  real death and invent a second one. The pass is **idempotent** (the death it
  inserts is what pairs the revive next time), which is what lets the live path
  re-run it over its unflushed tail on every flush and stay identical to the
  rebuild. ACT has the same blind spot, so this is one place the site is ahead
  of it rather than at parity.
- **Intercepts** (`RE_INTERCEPT`, `encounter_actor_stats.intercepts`). Three
  structural limits, not bugs: the log carries **no amount** (so this is a count,
  and the tooltip says so), the victim is only ever named from the logger's seat,
  and the two variants are the same event printed twice, so `_dedupe_repeats`
  keys on (type, who, second). Two intercepts in one second are
  indistinguishable; one is the honest floor. This is the suspected residual
  behind ACT-parity damage-taken running light — ACT cannot see moved damage
  either.
- **"AvgDelay adj"** (`_activations`) answers "how often did they press
  something", where ACT's Avg Delay is swing span ÷ swings and counts DoT ticks
  and AoE spread as separate actions. Rules: hits of one ability in the same
  second are one press; a hit within one tick period of the previous hit **on the
  same target** continues a chain; autoattack is not a button and `kind='self'`
  rows are a cost; catalog procs fire themselves and are out of the per-actor
  total. The tick period comes from Census `dmg_period_s` when known and is
  otherwise inferred from the ability's own hits — **the discriminator is modal
  dominance, not average regularity**: the modal gap must carry half the chain
  and appear at least four times before anything is folded away, because folding
  real presses away would understate a player. Stored per ability (`presses`,
  `press_delay_s`) and per actor (`presses`, `press_span_s`, deduped by second).

## Attribution and the stats engine

**Pet knowledge base** (`parser/petnames.py`, global `pet_names`). Named pets are
grammatically identical to abilities with internal possessives, so ONLY names in
the knowledge base decompose as pets (`Subject.unit == "named_pet"`). Sources:
curated seed, plus learning — every parse prescans raw lines for
`Alas, <Owner>'s <Capitalized> has died…` (with a kill-victim guard against mob
adds), unions it with the global table, and after the parse persists new names
plus every ability actually cast by a pet entity (`ability_catalog`,
`source='observed'`; curated > observed > census). Knowledge applies **backwards**
two ways: the prescan covers the current file from line 1, and
`sessions.parse_version` plus the startup reparse sweep (`main._reparse_stale`,
`POST /api/sessions/{id}/reparse`) re-attribute old sessions on a
`PARSE_VERSION` bump. A session stuck at `parsing` on startup is an orphan and is
swept too. Conflated pets cannot be split; ability rows whose name is a known pet
ability get `via_pet` at read time instead, and damage stays with the owner.

**Behavioral mob refinement** (`pipeline/refine.py`). Single-token capitalized
names default to "player", but a kill-victim of a player-credited kill line, or a
name that trades damage with confirmed players (≥2 hit / ≥3 hitting) without ever
touching a heal, reclassifies to `mob`. Target-side resolution decomposes
possessives exactly like source-side.

**Everything that vetoes a reclassing is a claim that the name is a PERSON**, so
each veto is a hole if a mob can produce it. Two found in real logs: a boss's
self-heal (fixed by resolving heal edges only between distinct names), and owning
a swarm pet — an encounter holding the raid's pets prints possessive dumbfire
lines for the boss, which promoted it to a confirmed player and vetoed its own
kill-victim reclassing. The pet-owner rule is now applied only to names the raid
never killed.

`roster_prescan` is the player-side authority (`refine_known_mobs(events, logger,
roster)`): a name in it is never a mob, whatever the rest of the evidence says.
It is the only player signal a mob cannot manufacture, and it is what protects a
mind-controlled raider now that softer signals no longer veto alone.

**Bare-named summoned pets** (`refine_bare_pets`) are the mirror image of a
one-word boss: EQ2 writes a dumbfire with no owner possessive anywhere in the
file, so `petnames` can never reach it and the grammar makes it a raider. Two
independent tells, either sufficient — its KIT (`ability_catalog` already knows
those abilities are `unit='pet'`), or Census having never heard of the name while
the log shows it never chatted, looted, joined a raid or was resurrected.
`roster` vetoes both, `known_mobs` wins outright (mobs cast pet kits too), and the
row lands as `swarm_pet` with no owner.

- TRAP: `roster_prescan` matched `^<Name> receives ` as loot, and a debuff landing
  on a summoned pet uses the same verb. Loot carries an `\aITEM` link; the pattern
  now requires it.

**Stats engine v2** (schema v5, `pipeline/statsroll.py`): per-ability avoid
breakdown (`misses/parries/ripostes/dodges/blocks/reflects/resists`), `zero_hits`
(absorbed hits stay inside `hits` for ACT parity), `median`, `avg_delay_s`,
`dtypes` (JSON per-school split including dual-type components), autoattack split
into melee / multi attack / aoe attack / flurry, crits on ward/power/threat,
threat split into `threat`/`detaunt`, casts attached to the ability's busiest row,
actor `damage_taken`/`power_drain`/`cure_count`, and actor rows for mobs and
Unknown. The API derives `swings` and `to_hit_pct`.
`GET /api/encounters/agg?ids=…` sums any set of one session's encounters into the
same payload shape (single-id fast path; medians recomputed from events, null when
pruned) and powers every tree node.

Per-actor AvgDelay: `encounter_actor_stats` stores `atk_swings` and `atk_span_s`,
and the API derives `avg_delay_s = span/(swings-1)`, which aggregates exactly
across encounters.

`/sessions/:id` (`pages/Workspace.jsx`) survives as the ACT-style per-FILE debug
view — left tree, sortable combatant table, per-actor drilldown, URL selection.
Everything a reader wants is on the zone-run pages; this exists for looking at one
upload in isolation.
