# eq2advanced — Census, the wiki, and ability knowledge

Part of the architecture reference. Index: `ARCHITECTURE.md`.

## Census sync

`census/client.py` (HTTP, retrying — Census reads regularly stall; service id
from `CENSUS_SERVICE_ID`, default `s:example`), `census/sync.py` (the sync +
display payloads), `census/effects.py` (effect_list grammar → structured
effects), `routers/census_api.py` (summary / refresh / snapshot history /
spell detail). Query shapes verified live 2026-08-02 — see client.py docstring.

- `sync_character()` is the ONE entry point (manual Refresh + hourly loop in
  `main.py`, which syncs owned characters >24h stale; gate with
  `CENSUS_AUTO_REFRESH=0` — tests set it in conftest.py). It fetches the
  character doc by `name.first_lower` + `locationdata.worldid=618`, updates
  `characters.class/level/census_character_id`, snapshots a TRIMMED doc
  (identity/stats/resists/gear/spell_list/AA points — not quests/collections,
  which are huge), and fills the `census_spells` / `census_items` caches for
  any scribed-spell / equipped-item ids not yet cached.
- **Snapshots only when Census's own `last_update` moved** — so history rows
  mean "the character actually changed" and the diff endpoint (stats, gear
  slot swaps, spell tier changes computed against the previous snapshot) stays
  meaningful. Manual refresh has a 60s cooldown.
- **Damage numbers exist only in effect_list text** ("Inflicts 33 - 45 disease
  damage on target instantly and every second."). `effects.py` parses that
  grammar (damage/heal/ward/power/stat/proc, ranges, %-of-health, tick
  period); anything unrecognized is kept verbatim as kind `other`, so the
  coach can only under-use a spell, never misread it. Typed spell fields
  (`cast_secs_hundredths`, `recast_secs`, `duration.*_sec_tenths`) are
  reliable — EXCEPT `recovery_secs_tenths`, which stores HUNDREDTHS despite
  the name (every spell carries 50 = the universal 0.5s recovery; dividing by
  10 gave 5s and clamped idle% to 0 — found on a real raid night).
- The character doc's typed stats carry everything coaching needs (verified on
  Bobby: `combat.abilitymod` 1442, `basemodifier` 68.1, `critchance` 53.5,
  `ability.spelltimereusepct`/`spelltimecastpct`) — no text parsing there.
- Tests (`test_census.py`) run entirely from recorded fixtures in
  `tests/fixtures/census/` (trimmed real responses for Bobby) via a fake
  injected as `census.client._shared` — no live Census calls in CI.

### Bulk spell ingest

Census spell records are BASE, pre-stat values per tier (App1–Celestial share a
`crc`) — no wiki scrape or manual entry needed; in-game tooltips are these
numbers with the player's stats applied, which is exactly `fit.py`'s damage
model. `tools/ingest_spells.py --all --max-level 70` proactively caches the
full class books via `sync.ingest_class_spells()` →
`client.spells_by_class()` (query `classes.<cls>.level=[<max>`, lowercase
class keys, `[` = Census's <= operator, `c:start` paging — verified live
2026-08-02, wizard <=70 = 1152 records; `sync.ALL_CLASSES` holds the 24
EoF-era names). Marks `spell_line:{crc}` so upgrade advice never refetches.
`census_spells` carries typed columns (`cast_s/recast_s/recovery_s/duration_s/
power_cost/dmg_min/dmg_max/dmg_dtype/dmg_period_s`, schema v4) populated on
insert by `sync.typed_fields()` — the ONE owner of the unit conversions
(including the recovery-hundredths gotcha; `fit.spellbook()` uses it too).
`dmg_*` is the primary damage effect = largest midpoint (DoT initial hit over
its tick). `sync.backfill_typed_columns()` repairs pre-migration rows;
`spell_overrides` remains the manual escape hatch where Census text is wrong.
GOTCHA: the default `s:example` service id gets burst-throttled hard on bulk
pulls ("Missing Service ID" error after ~10 full-record requests — LESS than
one class book; it also silently CLAMPS `c:limit` to 100, so paging advances
by `len(rows)` and stops only on an empty page — a short page proves nothing).
`ingest_class_spells` therefore persists every page as it lands and stores the
offset in settings (`ingest_progress:{cls}:{max_level}`): an interrupted class
RESUMES mid-book instead of restarting from zero (a from-zero fetch could
never outrun the burst budget). Only the empty end-page clears the offset and
writes the `spell_line:{crc}` completeness markers. The tool paces 30s/page on
`s:example`; a registered service id (census.daybreakgames.com signup) in
`.env` as `CENSUS_SERVICE_ID` removes the limit — start.sh and the ingest
tool both load `.env`.


## The raid's guild, voted by its roster (schema v20)

Nothing in an EQ2 log says which guild a raid belongs to. The roster does, one
name at a time — and the character doc that answers "what class" already carries
"what guild", so `census/roster.py` captures both from the one request it was
already paying for. `census/guilds.py` turns that into a tag: `zone_runs.guild`,
a pill right of the zone name on the raid list and the raid page, and the Compare
picker's Guild facet.

**A wrong tag is a public claim about somebody else's guild**, so the vote
abstains twice as readily as it commits:

- **Abstain on thin evidence.** Fewer than half the roster resolved and there is
  no answer here. About 18% of a real roster never resolves at all (pets, mobs,
  names typed before the character existed), and a tag drawn from six of
  twenty-four names is a guess wearing a fact's clothes.
- **Tag only on a strict majority of what IS known, with the known-guildless
  counting against.** Twelve Freethinkers and ten pick-ups is a Freethinkers
  raid; three Freethinkers and eight unguilded friends is a pick-up group that
  happens to carry three guildies. Ties fail the strict test for free.
- Runs under `RAID_MIN_RAIDERS` are forced NULL — imported from `groups.py`,
  because "what is a raid" is one line the whole app draws once.

That needs three states, not two, which is what `roster_classes.guild_checked`
is for. `guild_checked=1` with a `guild_name` is a guild; `1` with NULL is
**known guildless**, and it votes; `0` is *never asked*, and it abstains.
Collapsing the last two would let a backfill in progress strip real tags, or
paint somebody's guild onto a night that was mostly strangers.

The tag is derived, never authored, so it is **recomputed rather than
maintained**. `retag_runs` is pure SQL over already-cached rows — zero Census
calls — and every write path that can change a roster calls it afterwards:
`pipeline/zoneruns.py` at the end of `rebuild_zone_runs` (the funnel every
upload, live close, reparse, merge, split and delete ends in), `ingest_writer`
once more after the parse-path lookups land, and the hourly loop in `main.py`.
There is therefore no staleness column and no `PARSE_VERSION` bump: nothing
about parser or rollup semantics changed, and NULL means "no majority holds"
and "not computed yet" alike — to every reader they are the same thing.

The ~1100 names cached before v20 have a class and no guild answer. They are not
stale by any TTL, just missing a field, which is what `resolve(force=True)` is
for; `backfill_stale_guilds` walks them oldest-first, 120 per hourly tick at
0.75s pacing, and `sync_roster.py --guilds` is the same pass with no budget for
when you want it now.

### The rest of the character doc: level and guild on an actor row

The same cached row carries a `level`, and until the drilldown asked for it
nothing ever read it back. `_census_facts` / `_add_census_facts`
(`routers/encounters_api.py`) hang `level` and `guild` on every PLAYER actor in
`/encounters/agg` and `/encounters/{id}`, so opening someone's parse names a
person — *Abath, Shadowknight, L70, Gin and Jumjum* — instead of a bare string.
One indexed read per selection, zero requests: the facts are already in
`roster_classes` because the class lookup paid for them.

Two limits worth keeping straight. The world comes from the sessions in the
selection (`roster_classes` is keyed by name AND world), so a name is answered
by the server the raid was on. And unlike the class, **these are undated**:
Census reports where somebody is NOW, and nothing here splits them into eras
the way `_split_eras` splits a spellbook. So they caption a name and never feed
a number — the tooltips say as much, and a raider Census never resolved gets
nothing rather than a zero.

There is no gear/AA link to go with them. EQ2U (`u.eq2wire.com`) is the obvious
target and its character routes answer 200 with an empty body for every id and
name tried on 2026-08-05, so it would have been a link to nowhere. Showing gear
would mean ingesting `equipmentslotlist` from the character doc ourselves.

**A class change is a DATE, not a tie** (`_split_eras`, `_write_eras`). EQ2
lets a character betray into the other half of their archetype, and pooling
forever then guarantees a deadlock between two full spellbooks: Klebb cast
swashbuckler abilities until 2026-07-31 and brigand abilities after it (17 v
16), Thwart was an illusionist until 2026-08-02 and a coercer from 2026-08-04
(12 v 10). Both failed the tie rule, both went blank in **every raid they had
ever appeared in**, and every further upload made it more permanent — Klebb was
blank on the Mistmoore's Inner Sanctum page for exactly this reason. So when
the pooled vote deadlocks, the contenders' ability WINDOWS decide: if the last
swashbuckler ability lands before the first brigand one, that is not ambiguity,
because a character who betrayed cannot cast the old book again. `_split_eras`
cuts between the windows, infers each side from its own evidence, and each
session is answered from the era it falls in — which is why these names skip
the blanket write-back above.

Disjointness is the whole test, not "two strong classes": a raider wearing
another class's proc gear scores stray votes all night long and those
interleave. One residual: `entities.class_guess` is per session, so a log that
spans the changeover (Klebb's falls inside one 1.2M-line file) gets the era it
cast more of, and the fights in that one file predating the switch read as the
new class.

**Voting cannot tell you WHETHER the row is a person** (`refine.roster_prescan`,
`guess_session_classes(roster=…)`). EQ2 writes a summoned pet exactly like a
raider — a bare capitalized name casting its class's real spells — and it
receives group buffs and gets warded like one too, so every signal the vote
reads agrees with itself. On 2026-08-04 that produced `Kartik — Berserker`,
`Vaser — Fury`, `Leneker — Coercer 100%`; each acted in ONE of 21 encounters
while every real raider acted in 20 or more, and the log carried no owner
possessive anywhere, which is also why `petnames` (which learns from
`Alas, <Owner>'s <Pet> has died…`) can never reach them.

So the file has to be the tiebreak: `roster_prescan` collects the names that
appear in a line **only a player character can produce** — chat, raid join /
leave, guild login, loot, resurrection. On that night it covered all 26 real
raiders and none of the seven pets. A name with no such evidence is written
`{"class": null, "source": "unidentified"}` and the table says so, rather than
showing a classless raider. Two deliberate asymmetries: the set is
over-inclusive (server-wide chat counts, so a stranger who said hello lands in
it) because a name wrongly INCLUDED only keeps the status quo while one wrongly
excluded strips a real raider's class; and the mark is written for that session
only, and other sessions' inference will not overwrite it, because the same
name can be a raider in one log and a pet in the next.

**What it still cannot do, and why it looks worse than it is.** Roughly half
the ability names in a real raid log — 433 of 919 — have no Census row at all:
AAs, gear procs and item effects are not in the spell books, and the bulk
ingest only pulled ~130 spell names per class. Of the 230 names with any
ability evidence, 38 are named pets misfiled as players (Reaper, Viber and
Zarann cast nothing but `Grim *` necro pet spells), and the rest are players
whose visible kit is entirely uncatalogued. Fixing coverage is a Census
ingest job (the `alternateadvancement` collection), not a voting change.

Stored as JSON in the long-dormant `entities.class_guess` column —
`{"class","confidence","matches","source"}`, read back by
`parse_class_guess`. No schema change. Written at parse time and lazily
backfilled by the encounters API (`backfill_session`, guarded by one indexed
lookup plus an attempted-set), so sessions parsed before any of this existed
light up without a reparse.

`/api/encounters/agg` and `/encounters/{id}` carry `class`,
`class_confidence`, `class_source`, and `archetype` on every actor —
`archetype` is NULL when the class is, since `archetype_for` defaults to
"dps" and that would read as a claim we never made. Cross-session merges keep
the highest-confidence guess.

### Proc exposure

`ability_catalog.proc` existed (set from the Census "may cast X on…" grammar)
but never left the backend. Ability rows now carry `proc` and
`ability_class`, which is what lets the UI split a parse into cast / auto /
proc — the difference between a player's rotation and their gear.

**The name alone over-claims** (fixed 2026-08-03). Census flags a name if ANY
item or buff can cast it, so a class's own combat art gets marked as gear the
moment some proc references it. `_proc_flag` (encounters_api) makes the flag
per ROW, requiring the catalog's claim to survive this actor's evidence:

- `casts` counts prepare lines, which procs never print — a cast is proof.
- the ability is in this actor's own class spellbook.

That second test needs a column: `ability_catalog.class` means two different
things depending on which statement wrote it. From a spell record it is the
classes that SCRIBE the ability; from a "may cast X" effect it is the classes
whose buff FIRES it, which says nothing about who can press it. Hence
`ability_catalog.scribed` (schema v8) — only a scribed row can clear a proc
flag, and curated procs carry no class list at all, so they stay flagged for
everyone. `catalog.backfill_scribed` (startup) repairs pre-v8 rows from
`census_spells`; without it every pre-existing catalog row reads as unscribed
and only the cast evidence bites.

Still NOT solved: **buff attribution**. Damage from another player's buff
proccing on you is entirely yours, and sourceless `is hit by <Effect>` lines
still pool under "Unknown". Real contributed-DPS for utility classes needs
buff application/expiry tracked into uptime windows — parser work, not an API
change.

### Pets and procs stop being inferred (schema v22, PARSE_VERSION 20)

Everything above was a per-row softening of a claim that should never have been
made. The claim itself was wrong, in two directions at once, and it had a
feedback loop.

**Pets.** `census/catalog.observe_pet_abilities` wrote `unit='pet'` for
anything a pet-KIND entity cast — globally, permanently, off one sighting. But
"pet-kind" includes `refine_bare_pets`' guess at a bare capitalized name, and
that pass used Census `found=0` as a way IN while never reading `found=1` as a
way out. So `Gululu` (a level 70 shadowknight), `Wudi` (wizard) and `Moklok`
(troubador, guild "Skill Issue") were filed as dumbfires, their spellbooks were
learned as pet kits, and `refine_bare_pets` reads that same table back to
decide what a pet is — which brought more names in on the next parse. It
converged on **228 pet-flagged names, 108 of which Census knows as scribed
player spells**: `Ice Comet`, `Harm Touch`, `Apocalypse`, `Raging Blow`.
Necromancer looked right only because `CURATED_PET_ABILITIES` already covered
it, which is exactly why the bug survived — the class anyone checked was the
one with a human answer.

**Procs.** `may cast X on …` names X for every buff that references it, so a
class's own button gets flagged the moment anything can fire it: `Berserk`
(berserker), `Dragon Stance` (monk), `Baffle` (brigand), `Knockdown`, `Pin`.

The fix is a precedence ladder, not a better guess:

    ability_rulings  >  curated seed  >  no label at all

`census/catalog.pet_ability_names` / `proc_ability_names` are the only doors to
both labels and they encode that ladder in SQL; `encounters_api` calls them
rather than keeping its own copy, so one ruling reaches the badges, the
rollup's press counting and the coach together. `reset_verdicts` (startup, before
`seed_curated`) demotes every machine-written label into the candidate columns
`pet_seen` / `proc_candidate` — a database that already learned wrong loses its
bad badges on the next restart with **no reparse**, because the labels live in
`ability_catalog`, not in the rolled-up rows.

What the machine still does is gather evidence, and the three columns are
deliberately different strengths:

- `pet_definite` — cast under `<Owner>'s <lowercase remainder>`, the swarm form
  nothing else produces. Certain by grammar.
- `pet_own` — under the logger's own bare name, certain by the YOU/YOUR rule.
- `pet_guess` — a bare capitalized name the refiner guessed at. **Every bad
  label came out of this column**, so it is named as the weak one and a handful
  of them against thousands of player casts is discarded outright
  (`PET_GUESS_SHARE`).

`ability_pet_sightings` is keyed by session so the PARSE_VERSION sweep restates
evidence instead of inflating it — "seen in 4 raids" has to keep meaning four
raids.

### The pet kits, and the stance that is not one (2026-08-11)

A summoner's pets share their owner's name, so which pet acted is only ever
readable from WHAT IT CAST. That map used to be co-occurrence guesswork; it is
now measured. Lindsay fought **one pet per fight with none of his own spells
cast** (session 127, three fights), which makes each fight one kit and nothing
else:

| pet | kit |
| --- | --- |
| mage | Grim Wave / Embrace / Devastation / Lifetap / Bolt / Distortion |
| scout | Throat Gash, Poisoned Spike, Shadowy Garrote, Unseen Blade, Shadestrike, Acidity |
| fighter | Graven Strike / Scream / Breath / Frenzy / Assault / Vanquishing |

**Three abilities fired for all three pets, and they are the STANCE, not a
kit** — defensive casts `Shout` and `Grisly Feedback`, offensive casts `Clawing
of the Soul` (Lindsay). The old map had `Shout` under the scout pet and `Grisly
Feedback` under the mage pet, which split every single-pet fight across two
rows and made the two look 0.99-correlated in the co-occurrence pass. They now
join whichever archetype the parse shows, and a parse showing two pets keeps
them on a bare "Pet" — with two pets out, nothing can say which one shouted.

**A pet attack the OWNER presses is not pet damage** (`PET_COMMANDED`, frontend
`lib/stats.js`): `Shadow Step` and `Shockwave` are cast BY the pet and pressed
BY the summoner, which is exactly why they appear across all three archetypes'
raid fights and in none of the three where Lindsay cast nothing himself. His
ruling — "even though they're technically pet attacks, i dont count them under
pet because im pressing the button" — so they carry no pet badge, never fold
into a pet row, and never count toward the pet share of an actor's damage. They
are deliberately absent from `CURATED_PET_ABILITIES` for the same reason.

The same measurement corrected two claims made from sightings: the whole
`Graven` kit was filed as a CONJUROR's (it is the necromancer fighter pet — all
21 of its windows also carry the defensive stance), and `Quick Strike` was
claimed at all. This table is keyed by NAME alone, and that name is a mob's and
a player's far more than a pet's (454 and 518 rows against 6), so the claim hung
a "pet cast" badge on everyone else's combat art.

**`seed_curated` RETIRES a name dropped from either tuple.** `reset_verdicts`
spares `source='curated'` by design, so without the retirement pass an edit to
those lists is a no-op on any database that has already been seeded, and the
dropped name keeps its badge forever. A human ruling still outranks both.

**The illusionist's Personal Reflection is deliberately NOT in the curated
list** (`Phantasmal Shock`, `Overwhelming Silence`, `Headache`, `Confusion`,
`Color Shower`, `Stunning Array`, `Lock Mind`, `Illusory Taunt`). Its kit is
just as well evidenced — two illusionists uploaded their own parses, where the
pet splits off as `own_pet` — but that list is what promotes an ability hiding
under a PLAYER's name to a pet cast. Combining a pet we cannot see means
claiming a remote illusionist's own line is their pet's, so the archetype is
carried in the frontend map alone and only groups a pet with rows of its own
(Lindsay: "only offering illusionist pet combine if we have a parse from that
illy, otherwise we can't").

### The conjuror kits, settled from the COMPLEMENT (2026-08-11)

No conjuror has ever uploaded a parse, so their pet has never split off as
`own_pet` and no fight isolates a kit. It did not need one: Lindsay wrote down
the conjuror's OWN book, **Census confirms every entry** (`Crystal Blast`,
`Fiery Annihilation`, `Earthquake`, `Shattered Earth`, `Ice Storm`, `Petrify`,
`Winds of Velious`, `Aqueous Swarm`, `Roaring Flames`, `Plane Shift`, `Fire
Seed`, `Elemental Unity`, `Blazing Avatar`, `Flameshield` — all conjuror-
scribed), and what is left over on a conjuror's line is the pet. The two kits
that fell out of the raid data before the list existed — fire and air,
anti-correlating at ≤0.15 across 419 windows because they are an either/or —
are exactly the remainder. Earth is nobody's raid pet; `Telluric Bash` and
`Telluric Retaliation` are all it has ever been seen to cast.

**Four leftovers are the player's, and Census says so in the CASTER's effect
text** — the same `may cast X` grammar that used to invent proc labels, read
the right way round (from a named spell to its damage line, not from a damage
line to a guess):

| damage line | cast by | verdict |
| --- | --- | --- |
| `Blaze` | Blazing Avatar — *"will cast Blaze on target of attack"* | player (Consume's rule) |
| `Force of the Elements` | Elemental Unity | player (Consume's rule) |
| `Seed of Fire`, `Blooming Flames` | Fire Seed, a buff on an ALLY | proc |
| `Planar Igneous Flames`, `Planar Thunderous Roar`, `Planar Telluric Strike` | Plane Shift, one per pet type | player |

`Blaze` is why this had to be read and not guessed: it is the conjuror's second
biggest damage line, it is delivered by the pet, and Census ALSO knows it as a
level 10 **warlock/wizard** spell. A name-keyed pet claim would have been wrong
for two classes at once. `Ro's Flames` and `Incinerate` are deity procs — cast
by the player, worshipper-gated rather than class-gated (Lindsay uses Ro's
Flames on a necromancer).

**A pet's swing type belongs to the KIT, not to the archetype label.** A
necromancer's mage pet pierces and a conjuror's crushes, so `PET_KITS` carries
`melee` per kit. Measured WITHIN one conjuror rather than across several
(pet choice correlates with who the player is, so the across-players split is
confounded): Beavera melees 100% piercing over 31 air windows and 99% crushing
over 113 fire windows, and Roku and Flume agree. That is the pet's weapon, not
the raider's.

**The coercer's Possessed Essence cannot be separated by analysis and needs a
coercer's log.** Conjuror pets split because air and fire are an either/or —
their two kits anti-correlate at ≤0.15 across 419 raid windows. A coercer
always has the essence out, so its abilities co-occur with the coercer's own
book at 0.85–1.00 and no block falls out. Subtracting the Census class book
does not rescue it either: only ~40 base names per class are ingested, so real
coercer spells (`Lash`, `Convulsions`, `Despotic Mind`) read as "not in the
book".

### Provenance comes from Census, not from a guess

`Fae Fires` is not "a gear proc". Census holds `Fae Fire`, a level 35 **fury**
spell whose effect text reads *"On any combat or spell hit this spell will cast
Fae Fires on target of attack."* Every spell record carries `given_by`, `type`,
`alternate_advancement` and `deity`, and `census/abilityreview.proc_sources`
finds a proc's source spell by that effect text, so the answer to "spell, AA,
gear or deity" is usually already cached.

Two things had to change to read it. `census/effects.py` matched only `may
cast`, dropping every guaranteed `will cast` — half the grammar, and the half
holding `Shout`, `Thorns`, `Grisly Feedback`, `Prismatic Shock`, `Thunder
Fist`. It now keeps the whole clause: `trigger`, `mode` and `per_min`, because
"on a melee hit, ~3/min" is a proc and "on a kill" is a consequence of one, and
they read identically once the condition is thrown away.

What Census genuinely cannot answer is **gear**: `census_items` holds 143 rows
(an item is only fetched when something already referenced it) and exactly 2
spells carry the deity flag, because the ingest walks class spell pages. AAs
and deities are now covered by `gamewiki.py` (see "The wiki as reference
data"), so "no cached spell casts it" means GEAR — and gear is a closed
question, not a pending pull (see "Gear was investigated and dropped" below).

**Self vs granted is a per-ROW question**, not a per-ability one, and that is
why `ability_rulings.grant_class` exists. `Fae Fires` on a fury is their own
buff; on the warlock beside them it is the fury's. Same ability, two answers,
decided against the actor's class — which is the buff-attribution problem
above, now with the data it needs.

### The Abilities console (`/admin/abilities`, role `curator`)

Inference stops at "here is the evidence, here is how sure I am"; a person
answers the rest. `scope=open` is the queue — unruled and under full confidence
— and the class rail is the ergonomics: 565 undecided abilities is a wall, 56
under `assassin` is an afternoon. An ability sits under **every** class that
might own it (who scribes it, whose buff fires it, who was seen using it), so
one name appearing three times is correct until it is ruled on, and `Unclassed`
(160) is where the gear and AA procs collect. The search box reaches every
ability ever tracked, settled ones included — that is how a wrong answer gets
fixed later.

`curator` is a separate role because the two jobs are unrelated: deciding what
`Fae Fires` is needs someone who knows EQ2, and running the site needs someone
with the disk. Granting `admin` to get the first would hand over accounts,
storage limits and the audit log with it. It widens nothing about who can read
a raid — the payload is ability names, site-wide sums and class names, never a
player name, an entity or a row from anybody's parse, which keeps the admin
console's promise intact.

**The lookup button.** Every ability row in a parse carries a ⚙ for a curator
(`BreakdownTable`, hidden until the row is hovered) linking to
`/admin/abilities?q=<ability>`. The place you NOTICE that `Ice Comet` is not a
pet ability is a raid page, not an admin queue, and making someone go find it
by name is how a wrong label survives. `?q=` is the address rather than
component state so the link works from anywhere; `BreakdownTable` reads the
viewer from `lib/session.jsx` because it renders in three places that don't own
a user, and that context carries no authority — everything it gates re-checks
server-side, so a stale value shows a link that 403s, never data.

### A grant is to a TIER, not a class (`classtree.py`)

AAs are granted at every level of EQ2's tree — the Predator line belongs to
rangers **and** assassins, a Scout AA to all seven scouts — so "who gets this"
cannot be one class name. `classtree.expand` is the single translation:
`predator` → {ranger, assassin}, `scout` → all seven, `ranger` → itself. A
ruling stored against `predator` groups under both subclasses on the Abilities
page and will compare against both when self-vs-granted lands, without being
written twice or drifting.

Census does not need this — it only ever writes subclass names, having expanded
groups before we see them (its `class` column runs `assassin,beastlord,brigand,
dirge,ranger,swashbuckler,troubador` where the game says "Scout"). The tree is
for the side Census does not cover: what a person types. `normalize` is
therefore strict — an unrecognized target is REJECTED at the API rather than
dropped, because `predatr` saved silently is a grant that reaches nobody and
looks like a decision.

Note this is EQ2's own tree and NOT the role grouping in `coach/descriptive.py`.
They answer different questions and must not be merged: that all six Fighter
subclasses are TANKS is a fact about role; that Paladin and Shadowknight are
both Crusaders is a fact about the tree, and only the second says who an AA
reaches.

The tier names came from the wiki's own category tree, which files spells and
AAs at every tier the game grants at (`Category:Predator Spells` sits under
`Category:Scout Spells`). That is also where two of them were corrected:
Beastlord's tier-2 is **Animalist** and Channeler's is **Shaper**, not their own
subclass names.

### The wiki as reference data (`gamewiki.py`, schema v23)

Census stays authoritative for SPELLS — 26,082 records with damage ranges,
periods and the effect grammar, more than any wiki page holds. What it was
never asked for is **AAs** (256 incidental rows against the wiki's 1215) and
items, and between them they are most of what a raid log names and nothing
could explain: 479 ability names with no Census row at all.

**`activated` is what earns the table.** `You prepare <X>` prints for spells and
combat arts and **not** for AA activations. So the log's only proof that
something was PRESSED is missing exactly where AAs live, and an activated AA is
indistinguishable from a gear proc: logger hits, no prepare line. That read
`Lifeburn` — a five-minute recast the necromancer presses — as gear, along with
`Mana Flow`, `Counterblade`, `Nullifying Staff` and 8 more confirmed, out of 45
rows resting on the same silence. A recast timer settles it, and it exists
nowhere in the log. `suggest()` therefore checks `activated` BEFORE the
prepare-line test; the ordering is the fix, not an optimization.

**The wiki is also a proc SOURCE.** Its effect bullets use the same trigger
grammar Census does — "On a melee hit this spell has a X% chance to cast Pirate
Stab on target of attack" — so `census.effects` reads them unchanged, with
letter placeholders swapped for a zero because a wiki page covers every rank at
once. That is how `Avast Ye` (rogue AA) is identified as the source of `Pirate
Stab`, which Census cannot do. 42 abilities are sourced this way.

**Era is a hard filter, not a preference.** The wiki separates its AA trees by
expansion and they do not overlap at all — verified 1215 EoF pages against 407
later ones, zero shared. `DEFAULT_ERAS = ("eof",)` because this is a level-70
TLE server; ingesting Heroic (RoK), Shadows (TSO) or Dragon (DoV) would label
raids with content that does not exist here, the same class of mistake as
inferring a pet from one sighting. Adding RoK when the server gets there is one
entry in `AA_TREES` and a re-sync.

Run by hand (`backend/tools/sync_wiki.py`), never on a schedule: AA trees change
once an expansion, and a nightly job against someone else's wiki buys nothing
and costs them bandwidth. Content is CC-BY-SA — fine as internal reference data,
and anything surfaced to a reader should carry attribution. Tests use pages
recorded verbatim in `fixtures/wiki/` and never touch the network.

**Deities are the other half** (`--what deity`): 139 blessings and miracles,
always EoF because deities arrived with it. Each carries the god that grants it
(`Rallos' Devastation` → Rallos Zek) in the `line` column — the same slot an
AA's line uses, answering the same question — and no class tier, because a god
grants to whoever worships it. All 139 are activated: a miracle is a button on
an hour recast.

**A name is not a key**, and getting that wrong cost real data twice:

- `wiki_abilities` is keyed on **(name, kind)**. One name really is two
  abilities — the fury spell `Tempest` and Karana's miracle both print the same
  in a log, and 37 AA names collide with a blessing. The first single-column
  key let the deity sync silently overwrite them.
- The same AA on several classes gets one page each (`Enhance: Cure (Mystic)`,
  `(Templar)`, `(Warden)`), and those **merge their tiers** rather than
  overwriting. 66 pages were collapsing to 29 names, keeping one class and
  losing the rest.
- A wiki row only speaks when **Census does not contradict it**. Matching by
  name is the weakest join available; a Census spell record is the game saying
  a class scribes it. So `scribed_by` wins, and the wiki stays on screen as
  evidence — which is what keeps `Tempest` a fury spell instead of handing it
  to Karana. `by_name` marks a name the wiki itself holds twice as `ambiguous`
  and `suggest` refuses to be confident about it.
- Disambiguation pages are skipped outright. They are pointers, not abilities,
  and ingesting one is exactly how a god claims a class spell.

Result: the open queue fell 560 → 478 and high-confidence rows rose 944 → 1029,
but the number that matters is 11 verdicts that were confidently wrong and are
now right.

**Gear was investigated and dropped** (2026-08-05), and the reasons are worth
keeping so nobody spends the day again. EQ2 has ~212,000 items, so a crawl is
out. `{{EquipmentEffect|<Ability>|}}` on an item page is a template PARAMETER,
not a wiki link, so `list=backlinks` returns nothing and there is no reverse
index to walk. What is left is full-text search with verification — search the
ability name, read the candidate pages, accept only one whose own effect text
casts that exact ability — and measured against the 381 abilities nothing can
explain, sampling the 60 largest by damage, that found **13**. Two were out of
era (`Caustic Poison` → a Level 90 crate item), which item pages do allow
filtering because they carry `level`, dropping the yield to roughly 15%.

~1500 requests for maybe 55 of 381 is a much worse trade than the AA pull, and
unlike AAs it corrects nothing — those rows are already honestly marked
unknown. An unresolved gear proc is a curator's job. Reopen only if Fandom
enables CirrusSearch: `insource:` would turn that structured `effectlist` into
a precise one-call reverse index and change the arithmetic entirely.

**This does NOT close items in general** — v32's loot tab looks items up by the
hundred and it is cheap, because it is a different question. What was refused
above is *ability name → which item casts it*, a reverse lookup with no index.
Loot asks *item id → what is this item*, and the raid log hands over the id:
`\aITEM -1813422462 …:Hoop of War\/a` is Census item 2481544834 written signed,
confirmed against Census's own `gamelink`. One exact request per hundred ids,
no search, no verification pass, no era filter needed. See
`backend/items.py` and `docs/zoneruns.md` → "Items as reference data".

