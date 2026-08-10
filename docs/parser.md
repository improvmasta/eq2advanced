# eq2advanced — Parser, segmentation, attribution and ACT parity

Part of the architecture reference. Index: `ARCHITECTURE.md`.

## The subject model (the crux — verified against a real raid log)

- `YOU` / `YOUR <Ability>` = the logging player, always.
- **Bare logger-name = the logger's own pet** (pets can share the owner's exact
  name). `<logger>'s <Ability>` = pet ability.
- Other single-token capitalized names = players (their pet conflates into
  them — community parse convention; fixed properly when they upload their own
  logs).
- `<Owner>'s <lowercase pet>['s <Ability>]` = swarm-pet chain.
- Possessive hazards: `Aros' Soulrot` (ability) vs `Aros's blighted horde`
  (pet); never split inside a capitalized remainder (`Autumn's Kiss`,
  `Treyloth D'Kulvith's Bloodcoil`).
- `focus` dtype = self-inflicted (Vampiric Requiem) — excluded from DPS,
  rolled up as ability kind `self`.
- Logger deaths: `<Killer> has killed you.` + `You regain consciousness!`.

Parser truth source: `backend/tests/test_parser.py` (verbatim log lines) and
`backend/tests/test_golden.py` (full /home/lindsay/bobby.txt fixture: 275,822
lines, 10 segments, 0 unmatched damage lines). **Rerun the golden test after
any parser or segmentation change.**

## Encounter segmentation

EQ2 logs have no encounter markers. Segments = combat-silence gap ≥ 7s (ACT's
~6s idle timeout, measured against Lindsay's Emerald Halls zone view: ACT cut
that night into 61 encounters totalling 1:13:12; our old 25s gap merged chain
pulls into 34 and inflated every EncDPS denominator ~8%). Damage AND avoided
swings hold a fight open; hard-cut on zone lines. Corollary: real mid-fight
lulls > 6s split the fight — Garanel's 21s lull yields two segments (the first
labeled by its named add, Garanel's Shade), which is exactly what ACT shows
for the same night.

### `/act end` — the one marker the log does have

Typing `/act end` in game ends the current fight here, the same way it does in
ACT. EQ2 has no `/act` command, so the client writes its refusal into the log:

```
(1785630981)[Sat Aug  1 20:36:21 2026] Unknown command: 'act end'
```

That refusal IS the channel — it is how ACT's own EQ2 plugin hears the command
(EQAditu: "EQ2 will generate an error message in the log file, the EQ2 English
parsing plugin will see that error message and forward it to ACT"), and the
format is the one the golden fixture shows for any typo (9 × `Unknown command:
'lbtell'`). Nothing is needed from the uploader: it sends every line verbatim,
and `pipeline/redact.py` governs chat prefixes only, so the line arrives.

`parser/classify.py` turns it into an `encounter_end` event and
`segment_events` hard-cuts on it like a zone line: the marker joins no segment,
the next damage line opens a new one, and — unlike a segment that merely timed
out — **nothing trails into it**, because a kill or a death after the raid
ended the pull belongs to what comes next.

Only `end` is honoured. `/act clear` operates on ACT's own window and has
nothing to do on a server.

Live, the cut also has to be immediate. `live._flush` normally holds the last
segment for `CLOSE_S` (17s) in case a late kill line joins it; a segment
carrying `Segment.ended_by_cmd` commits at once instead, so the fight card is
on the dashboard while the raid is still reading the meter, and the In Combat
light goes out with it. The rebuild from raw at session close re-reads the same
line and makes the same cut, so the live view and the finished session agree.

### Naming: the enemy fought, not the enemy that died

`pipeline.encounters.encounter_label` titles a segment after the **mob that
took the most damage in it**, and sets `success` to whether that mob died.

This replaced labeling from `has killed <Named>`, which had a hole big enough
to hide a raid night in. A wipe produces no kill line, so it could never be
named: it became an anonymous "trash" row, and `seg.success = 1` sat on the
same branch as the name, meaning **no code path could ever record a 0**. The
whole database held 181 encounters at `success=1` and 371 at NULL, zero
losses. Emerald Halls read "9/9 named", which is really nine kills out of nine
kills — while two Galiel Spirithoof wipes, a Farstride Unicorn wipe and a
Treah Greenroot wipe sat in the trash list holding most of the night's
time-dead. It now reads 10 nameds engaged, 7 killed.

Most-damaged reproduces ACT's titles on that night row for row, including the
cases where the raid's damage went into an add rather than the boss — ACT
titles the Treah Greenroot wipe "a knotted guardian", and so do we. Naming
needs resolved entities (which target is a mob), so it runs in the write path
(`ingest_writer.parse_session` and `live._flush`) rather than inside the pure
segmenter. `zone_runs.success_count` counts named kills specifically, since
trash now carries a real success flag too.

**The rule is only as good as the mob/player split feeding it.** On 2026-08-04
every Wuoshi pull came out titled "Ancient Grovebeast" — the adds — because
Wuoshi was classified a PLAYER and so was invisible to a rule that ranks mobs.
It took 5–9× the adds' damage in all five pulls (72M vs 8M on the kill), so the
title was never a naming question; see the self-heal veto below. Two other
things went wrong with it, and they are the ones to check when a title looks
off: the boss sat in the raider table at #17 damage, and `success` tracked the
ADDS, so a night of four wipes and one kill recorded three kills and two wipes.

### A segment is only a FIGHT if the raid engaged it

`success` is 0/1/**NULL**, and the rail marks only `success === 0`, so NULL
reads exactly like a kill. On 2026-08-04's Emerald Halls that put three
non-fights and two wipes in the list wearing the same face as a boss kill, and
the run header said 11 named pulls where there were 8.

The segmenter cuts on silence, so a raid night also produces segments nobody
fought: the last pull's DoT ticking on three people 13s before the next one, or
a proc pet touching the boss and being one-shot. `encounter_label` now requires
damage into a mob from `_ENGAGE_KINDS` (`player`, `own_pet`, `named_pet`) — a
**swarm pet is a proc, not a decision**. Without it the segment keeps its name
(ACT's stubs are titled `Encounter`; ours stay readable) but is `is_named`
False, `success` NULL, and falls into the Trash group. This is the same set ACT
drops to reach 61 encounters, noted below.

The exception is why `success` cannot just be NULL whenever the raid dealt no
damage: **a wipe where the boss AoEs everyone down before a single hit lands is
an attempt**, and the most emphatic kind of failure. That night had two — 24
and 17 dead to `Nature's Fury`, zero damage dealt — and both rendered clean.
Ally deaths decide it: dead raiders mean the raid was there and lost.

The rail says the outcome in the fight's own name — green killed, red lost,
each with its own mark, because red/green is the one pair a colourblind reader
loses. Trash stays muted whatever happened to it: fifteen green totem rows
drown the two lines that matter. ACT's own colouring is NOT this rule (its
Wuoshi kill and its Wuoshi wipes are the same colour); green-for-killed is
ours, at Lindsay's ask.

Known residual: ACT cut that night into 61 encounters and we make 60. ACT
split Galiel's two pulls at a **5s** gap — the only gap ≥3s in our merged 499s
segment — but no single threshold reproduces its set: at 5s we make 63 (two
extra splits ACT also makes, so ACT must additionally drop the two segments
where no enemy was ever damaged), at 6s we make 61 but split the wrong fight.
The plugin itself does not decide this — `ACT_English_Parser.cs` only calls
`SetEncounter(time, attacker, victim)` and its kill-ends-encounter branch is
commented out, so the boundaries are ACT core's inactivity timer.


## Reading the raid, not just counting it

### Class inference (`pipeline/classguess.py`)

The log never states anyone's class, but it states what they cast, and
`ability_catalog.class` knows who can cast what. Per player: take the distinct
ability names they used, drop the autoattack buckets (class-blind), pet-kit
names, and **procs** (gear fires those — they say nothing about the caster),
then let each remaining name vote for its class. A `characters` row with a
Census class overrides the vote outright (`source: "census"`, confidence 1.0).

Three rules, each answering a way the first version got it wrong (it voted per
FILE with whole votes only, and named 198 of 981 player rows):

- **Evidence pools across sessions, keyed by NAME** (`_evidence`, one 0.4s
  query over the whole database). Per-file guessing gave the same person a
  class in one raid and nothing in the next, and for 19 players two different
  answers in the same list (Zooey: defiler here, mystic there). The answer is
  written back to EVERY entity row with that name, so it reaches older raids
  without a reparse.
- **Shared spells vote in fractions.** "conjuror,necromancer" used to be
  discarded; it is half a vote each — it cannot pick between the two, but it is
  real evidence against the other twenty-two.
- **A margin rule beside the share rule.** A winner needs whole-vote evidence
  (`MIN_STRONG`, 2 single-class spells), `MIN_SCORE` of weight, and either a
  majority of the weight cast or double the runner-up. The margin is what names
  a player whose gear procs are not all flagged: Shaly scores 14.5 coercer
  against 7 bruiser and 4 each of three more — 39% of the weight, and obviously
  a coercer.

**Census by NAME is the answer; the vote is the approximation**
(`census/roster.py`, `roster_classes`, schema v19). `character/?name.first_lower=
zooey&locationdata.worldid=618` returns `Mystic` — the game answering, in 0.12s,
without caring that half a raid log's ability names have no Census spell row.
`characters` already held this for the handful of people with an account here;
`roster_classes` holds it for every name that has ever appeared in one of their
raids. On Lindsay's database that took the 8+-raider runs from 20% of raiders
resolved by inference alone to **80% from Census, 94% counting inference**, and
it agreed with every inference it overlapped (Klebb Brigand, Thwart Coercer,
Rorschach Assassin) while correcting the one that was wrong (Zooey: the vote
said Defiler, Census says Mystic).

A miss is an answer too and is cached the same way (`found=0`): `Enynti` is not
a character. That is one of the two negatives `refine_bare_pets` needs.

Census is authoritative about NOW, never about the night of the raid, so it is
layered UNDER the timeline, not over it — a Census row written today must not
relabel a raid from before a betrayal. Order of authority per fight, in
`resolve_class`: **what the fights on screen prove > the era the fight falls in
> Census > the pooled vote.** The local evidence chooses WHEN (which of the
classes this name is known to have held was live that night), never WHAT — so a
coercer whose charmed pet cast three Berserker abilities in one fight cannot be
promoted to Berserker by that fight.

Requests are budgeted (`ROSTER_LOOKUP_BUDGET`) and run OUTSIDE the parse's write
transaction; a Census outage costs a retry, never a parse and never a `found=0`
written over a real character. `backend/tools/sync_roster.py --all` does the bulk
backfill (`--guilds` for guild tags). Needs a real `CENSUS_SERVICE_ID` — `s:example` throttles after about
six requests, which will not get through one raid.


## ACT parity

The Zylphax the Shredder encounter matches ACT **exactly — all 25 players to
the point of damage** (`test_act_parity_zylphax` guards it). Two traps from
that round that are not parity rules but will bite again:

- **`lastrowid` after `ON CONFLICT DO NOTHING` is garbage** — it is the
  connection-wide last-insert id, from any table. Harmless on a fresh DB;
  corrupts ability attribution on ANY second session or reparse. Guard with
  `rowcount`.
- In the possessive owner slot (`Bobby's blighted horde`) the logger's name
  means the PLAYER, not the bare-name-is-pet rule — otherwise their swarm pets
  never roll up (Bobby read 17.3% light).

### The ACT model (round 2, Emerald Halls zone view, 25 players)

Diffed the full zone-wide combatant table against ACT. Each rule verified
numerically:

1. **Ward absorbs fold into the hit they mitigated** (`parser/classify.py
   _pair_wards`). The log prints the absorb line BEFORE its hit line; the hit
   line shows only bleed-through ("fails to inflict any damage" when fully
   absorbed). ACT reconstructs the pre-ward hit: absorbed damage counts for
   the attacker AND the target's damage taken; the warder separately keeps
   the full absorb as healing (asymmetric on purpose). Pairing key is the raw
   target string — WRINKLE: absorbs say "to YOU", the logger's mitigated
   self-hit says "hits YOURSELF"; normalize both to YOU. An absorb that never
   pairs (fully-absorbed DoT tick — no line at all) still counts as the
   target's damage taken. This also closed the old "Unknown parity" gap:
   Zylphax's warded Stench of Death pool is 1.18M ≈ ACT's ~1.17M.
2. **Self-inflicted damage is excluded from DamageTaken** too (Vampiric
   Requiem et al.) — previously only excluded from Damage. Bards' wards
   eating their own Requiem ticks was 60-80% of most players' inflation.
3. **Cures = `relieves` + `dispels` lines, any target, credited to the
   caster.** The real cure grammar is "X's Ability relieves Effect from Y" —
   we only parsed `dispels` (buff strips). 25/25 players exact.
4. **Deaths: the logger's bare-name pet death ("… has killed Bobby") counts
   as the player** — ACT can't tell same-name pets apart. 25/25 exact.
5. **PowerReplenish includes self-gains** (Lich, Savant's Intelligence). The
   drain grammar is a verb family (`confounds|zaps|smites|diseases|…
   draining N points of power`) — we only knew `confounds`; Ipax alone had
   2.5M unparsed drain. Both now match ACT (drain 20+/25 exact).
6. **Damage-taken does NOT roll possessive pets into owners** ("Tragedy's
   unswerving hammer" is its own combatant on the taken side; outgoing still
   rolls up). `statsroll.taken_key`.
7. **EncDPS/EncHPS denominator** = Σ encounter durations, same for everyone;
   EncHPS numerator = heals + wards (ACT's Healed column includes wards —
   verified via Lotus: 11.13M ≈ ACT 11.08M).

Result on the Emerald Halls night: damage 21/25 exact (rest within 0.003%),
cures 25/25, deaths 25/25, power drain ≈exact, EncDPS/EncHPS within 0.7%.
Residuals: damage-taken runs 1-3% light (Artonk -6%), suspected intercepts —
the log carries no amount for one — plus boundary seconds; and Emericant's
±6,307, which is ACT filing manastone/potion self-power as PowerDrain.
**Trailing-event trimming was tried here and REGRESSED cures/EncHPS** (ACT
keeps idle-window heals and power inside the encounter); don't re-add it. The
combat-clock gap that used to sit at the top of this list was the corpse tail,
below.

### The corpse tail

`pipeline/encounters.split_trailing_corpse` — a mob's DoT keeps ticking for a
few seconds after it dies, and the silence rule counted those ticks as combat
(the Freeport pull: ACT 28s, us 32s, same 28,634 damage, EncDPS 894.8 against
ACT's 1,022.64). ACT ends the fight at the kill and opens a new encounter for
the tick — a `[00:00]` stub 4s later.

**The rule is a SUFFIX operation and that is the whole design.** Walk back from
the end of a gap-cut segment over events that are only a dead mob still
ticking; the clock stops at the last real beat (an ally acting, a kill, or a
live mob swinging), and if the tail carries damage it becomes its own segment.
It cannot cut a chain pull in half. Everything else was measured against ACT's
Emerald Halls zone view (61 encounters, 4392s) and rejected:

| rule | encounters | clock |
| --- | --- | --- |
| ACT | 61 | 4392s |
| `>=7s` silence alone (before) | 60 | 4421s |
| cut whenever every engaged mob is dead | 132 | 4453s |
| cut at `You stop fighting.` | 149 | 4433s |
| both of those together | 120 | 4462s |
| cut at a kill the logger stopped fighting on | 83 | 4392s |
| **the corpse tail (shipped)** | **61** | **4419s** |

`You stop fighting.` is in the log and it is NOT the boundary ACT uses: cutting
on it fragments a raid, because the logger drops combat constantly while 24
other people keep swinging. A shorter silence threshold matches the count (6s
gives 61) but not the clock, and would not have fixed the Freeport pull at all
— that gap was 4s.

**Confirmed against ACT's own source** (`ACT_English_Parser.cs`, the EQ2 parser
plugin): the kill handler's `EndCombat(true)` is COMMENTED OUT, and the string
"fighting" does not appear in the file at all. ACT never ends an encounter on a
kill or on the combat-state line — boundaries come from
`ActGlobals.oFormActMain.SetEncounter(time, attacker, victim)`, i.e. ACT core's
idle timeout. So this is a behavioural match, not ACT's mechanism.

**ACT's XML exports** (`Import/Export` → XML, the ground truth to collect —
one per fight, not screenshots) then showed that the clock and the membership
are DIFFERENT questions. An encounter holds everything the timeout keeps
together, but its duration runs only to the GROUP's last action: the knotted
guardian wipe reads 40s while the mobs go on hitting bodies for 3 more seconds
(their damage still counts, only the clock stops), and the 411s Malkonis
D'Morte kill ends on the killing blow, not on the heals landing 6s later.

So `last` is the last ALLY action (ally damage/avoid, or a mob kill); mob
damage never extends the clock, and heals never did. A corpse tick opens a new
segment; a LIVE mob's swing does not (on a wipe the tail rides along). Per
combatant against those exports: Malkonis is 27 of 30 damage numbers exact with
cures exact, the wipe 24 of 26 with cures and deaths exact.

Still open, with evidence:
- **ACT starts an encounter ~3s before we do** when the pull opens with THREAT
  (Sorengail's 117,800 taunt at 21:16:42 opened ACT's Malkonis encounter; our
  first damage is at 21:16:45). Adding `threat` as a segment anchor was tried
  and moved Emerald Halls the wrong way (clock 4415 -> 4419s against ACT's
  4392), so it needs its own investigation, not a one-line change.
- **The boss's own Damage column is ~10% light** (Malkonis 1,102,897 rolled vs
  1,234,182 in our own events, ACT 1,234,857) — a `statsroll` question, not a
  segmentation one.
- **Deaths**: ACT counts a mob's death on the mob's row (Malkonis 1, a
  bloodgorger 2; we report 0), and does NOT roll another player's pet death
  into its owner (we gave Bobby/Beaux/Aros a death each for pets).

## Rezzes, revives, intercepts and the adjusted delay (schema v10)

Four things the log says that the parser was not listening for, plus one stat
ACT cannot express. `PARSE_VERSION` 13; the startup sweep rebuilds everything.

### Every rez family counts (`RE_REZ`)

`rez` matched exactly one line — `X petitions the divinities of resurrection.`
That is the CLERIC flavor. Druids "call forth primeval forces of
resurrection", shamans "primal forces", and those 73 casts (of 142 in the raid
logs) were invisible: Ramms and Squigs showed **zero** rezzes on a night they
cast 41 between them. The regex now takes an open verb (`petitions|calls
forth|beseeches|invokes|implores|summons`) and identifies the line by its
trailing `…resurrection.`, keeping the flavor text in `extra` so an unseen
family shows up as data rather than as a gap. `A resurrection spell is cast on
X.` parses as a rez with a target and no caster.

### Revives, and Time dead stops being a permanent zero

The landing side prints for everyone in range — `X is revived!`, `X is
resurrected!`, `You are revived!` — and only the logger's `You regain
consciousness!` was parsed. With all of them, `encounter_actor_stats.
time_dead_s` (a column that existed and was never written, so the aggregate
reported a confident 0) is filled: death → the first of {revive, acting
again, end of fight}, clamped to the encounter. The raid report uses the same
three-way rule, and `test_agg_time_dead_matches_the_report` pins them
together — two places printing different death times for one fight is worse
than either being slightly wrong.

`You lose consciousness!` is the logger's own death when nothing takes kill
credit. It coincides with `<Killer> has killed you.` every time in the raid
logs, so `_dedupe_repeats` collapses it — ACT death parity is unchanged (141
death events before and after).

### Intercepts (`RE_INTERCEPT`, `encounter_actor_stats.intercepts`)

`Bobby intercepted some of the damage intended for you!` — someone eating a
hit meant for someone else. Three limits are structural, not bugs to fix
later: the log carries **no amount** (so this is a count, and the UI tooltip
says so), the victim is only ever named from the logger's seat (`you` / `your
target`), and the two variants are the same event printed twice — 1270 of the
1442 intercept seconds carry both, so `_dedupe_repeats` keys on (type, who,
second). Two intercepts inside one second are indistinguishable in the log;
one is the honest floor. Credit goes through `decompose`, so the logger's
bare name resolves to their pet exactly as everywhere else.

This is also the suspected residual behind ACT-parity damage-taken running
1-3% light (see above): ACT cannot see the moved damage either.

### "AvgDelay adj" — the gap between button PRESSES (`_activations`)

ACT's Avg Delay is swing span ÷ swings, so a DoT ticking six times and an AoE
hitting five mobs read as eleven actions. Asked what it is really wanted for —
"how often did they press something" — the answer needs ACTIVATIONS:

- hits of one ability in the same second are one press (AoE across targets);
- a hit within one tick period of the previous hit **on the same target**
  continues a chain (DoT tick, multi-hit) instead of starting a press;
- autoattack is not a button and `kind='self'` rows are a cost, not an action;
- catalog procs fire themselves, so they are out of the per-actor total.

The tick period comes from Census `dmg_period_s` (via `catalog.press_inputs`,
collapsed onto `base_name`) when it is known — only ~60 base names — and is
otherwise inferred from the ability's own hits. **The discriminator is modal
dominance, not average regularity**, which the real logs settle: Bloodcoil's
same-target gaps are 3s 75% of the time and Grave Decay's are 1s 86% of the
time (EQ2 ticks these every second), while Lifetap, a nuke, spreads across
8-14s with no single gap over 15% and Dynamism's most common gap is 17% of
its gaps. So the modal gap must carry half the chain and appear at least four
times before anything is folded away — a rotation's jitter never clears that
bar, which is the failure that matters (folding real presses away would
understate a player).

Stored per ability (`presses`, `press_delay_s`) and per actor (`presses`,
`press_span_s`, with the per-actor set deduped by second — two abilities in
one second is one moment of activity). `_avg_delay_adj` divides span by
gaps, so it sums across encounters the same way ACT's does. On the Zylphax
fight ACT's AvgDelay reads 0.14-0.39s for the top parsers — a number nobody
can act on — while the adjusted delay reads 1.2-1.65s and separates them:
Spades 1.21s against Bobby 1.65s.

## Attribution and the stats engine

**Pet knowledge base** (`parser/petnames.py`, global `pet_names` table): named
pets (`Ellea's Lunar Attendant`) are grammatically identical to abilities with
internal possessives (`Banjeaux's Daro's Dull Blade`), so ONLY names in the
knowledge base decompose as pets (`Subject.unit == "named_pet"`). Sources:
curated seed, plus **learning** — every parse prescans its raw lines for
`Alas, <Owner>'s <Capitalized> has died…` evidence (kill-victim guard rejects
mob adds like `Garanel's Shade`), unions it with the global table, and after
the parse persists new names + every ability actually cast by a pet entity
(`ability_catalog`, `source='observed'`; curated > observed > census).
Knowledge applies **backwards** two ways: the prescan covers the current file
from line 1, and `sessions.parse_version` + the startup reparse sweep
(`main._reparse_stale`; also `POST /api/sessions/{id}/reparse`) re-attribute
old sessions whenever `PARSE_VERSION` bumps. A session stuck at `parsing` on
startup is an orphan (dead worker) and is swept too. Conflated pets (another
player's pet under the owner's own name) can't be split — ability rows whose
name is a known pet ability get `via_pet` at API read time instead (damage
stays with the owner, matching ACT).

**Behavioral mob refinement** (`pipeline/refine.py`): single-token capitalized
names default to "player", but a kill-victim of a player-credited kill line, or
a name that trades damage with confirmed players (≥2 hit / ≥3 hitting) without
ever touching a heal, reclassifies to `mob` — so one-word bosses ("Venekor")
stop appearing as raiders and their kills label the encounter. Target-side
resolution now decomposes possessives exactly like source-side, so damage
taken by `Ellea's blighted horde` lands on the same entity row.

Everything that vetoes a reclassing is a claim that the name is a PERSON, so
each one is a hole if a mob can produce it. Two found so far, both from real
logs: a boss's self-heal (`Wuoshi's Nature's Salve heals Wuoshi`), fixed by
resolving heal edges only between distinct names once `confirmed` is complete;
and **owning a swarm pet**. That one reads like proof — only players summon
dumbfires — but an encounter that holds the raid's pets prints `Enynti's
protoflame` and `Enynti's awaken grave` for the boss, and one such line
promoted Enynti to a confirmed player, which vetoed its own kill-victim
reclassing. It sat in the Mistmoore's Inner Sanctum raider table with 872k
damage and 24 people attacking it, credited with Ultraviolet Beam, Harm Touch
and Chromatic Shower (abilities it was HIT by, pooled into its class vote
across nine classes). The pet-owner rule is now applied only to names the raid
never killed.

`roster_prescan` is threaded in as the player-side authority (`refine_known_mobs
(events, logger, roster)`): a name in it is never a mob, whatever the rest of
the evidence says. It is the only player signal here a mob cannot manufacture,
and it is what still protects a mind-controlled raider — who produces a
player-credited kill line on their own name — now that the softer signals no
longer get to veto on their own.

**Bare-named summoned pets** (`refine_bare_pets`) are the mirror image of the
one-word boss: EQ2 writes a dumbfire with no owner possessive anywhere in the
file, so `petnames` can never reach it and the grammar makes it a raider.
`Viber`, `Knyi`, `Geker`, `Holmes` and `Reaper` sat in raid tables with no
class — the "?" rows. Two independent tells, either sufficient:

- **their KIT** — `Viber` cast Grisly Feedback (a necromancer Grim Sorcerer's),
  `Knyi` cast Confusion and Headache (an illusionist pet's), `Geker` cast Graven
  Vanquishing (a conjuror pet's). `ability_catalog` already knows those are
  `unit='pet'` because real pets under real owners taught it.
- **Census has never heard of them**, and neither has the log. `Holmes` only
  ever melees, so no kit gives it away, but no character by that name exists on
  the server and it never chatted, looted, joined a raid or was resurrected.

`roster` vetoes both, `known_mobs` wins outright (mobs cast pet kits too —
`Enynti` cast Grave Decay), and the row lands as `swarm_pet` with no owner,
which is the honest shape: an unowned dumbfire is not a raider and is not
anybody's damage.

Trap found while building it: `roster_prescan` matched `^<Name> receives ` as
loot, and `Shotar receives a transcendent injury!` is a DEBUFF landing on a
summoned pet. Four dumbfires were promoted to proven raiders by a combat
message, which then vetoed every demotion. Loot carries an `\aITEM` link; the
pattern now requires it.

**Stats engine v2** (schema v5, `pipeline/statsroll.py`): per-ability avoid
breakdown (`misses/parries/ripostes/dodges/blocks/reflects/resists` — the
`parries`→"parrie" bug is dead), `zero_hits` (absorbed hits stay inside `hits`
for ACT parity; min/max/median are non-zero), `median`, `avg_delay_s`,
`dtypes` (JSON per-school split incl. dual-type components), autoattack split
into `(melee)/(multi attack)/(aoe attack)/(flurry)` (flags `F_AOE`/`F_FLURRY`),
crits on ward/power/threat, threat split into `threat`/`detaunt` kinds, casts
attached to the ability's busiest row (no phantom damage rows), actor
`damage_taken`/`power_drain`/`cure_count`, and actor rows for mobs + Unknown.
API derives `swings` and `to_hit_pct`. `GET /api/encounters/agg?ids=…` sums
any set of one session's encounters into the same payload shape (single-id
fast-path; medians recomputed from events, null when pruned) — it powers
every tree node below.

Per-ACTOR AvgDelay: `encounter_actor_stats` stores `atk_swings` (offensive
damage events + avoids, self-hits excluded) and `atk_span_s` (first→last
swing); the API derives `avg_delay_s = span/(swings-1)`, which aggregates
exactly across encounters (sum of spans / sum of gaps). See also "AvgDelay adj"
below, which counts button presses instead of landings.

`/sessions/:id` (`pages/Workspace.jsx`) survives as the ACT-style per-FILE
debug view — left tree, sortable combatant table, per-actor drilldown, URL
selection (`?sel=`/`&actor=`). Everything a reader wants is on the zone-run
pages; this one exists for looking at one upload in isolation. Chain-pull
labels cap at 4 nameds (`A + B + C +N more`).

