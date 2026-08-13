# Census, the wiki, and ability knowledge

Index: `ARCHITECTURE.md`.

## Census sync

`census/client.py` (HTTP, retrying — Census reads regularly stall; service id
from `CENSUS_SERVICE_ID`), `census/sync.py` (sync + display payloads),
`census/effects.py` (effect_list grammar → structured effects),
`routers/census_api.py` (summary / refresh / snapshot history / spell detail).
Query shapes are recorded in `client.py`'s docstring.

- `sync_character()` is the ONE entry point (manual Refresh plus an hourly loop
  in `main.py` for owned characters >24h stale; gate with
  `CENSUS_AUTO_REFRESH=0`, which conftest sets). It fetches the character doc by
  `name.first_lower` + `locationdata.worldid=618`, updates
  `characters.class/level/census_character_id`, snapshots a TRIMMED doc
  (identity, stats, resists, gear, spell_list, AA points — not quests or
  collections), and fills the `census_spells` / `census_items` caches.
- **Snapshots only when Census's own `last_update` moved**, so a history row means
  the character actually changed and the diff endpoint stays meaningful. Manual
  refresh has a 60s cooldown.
- **Damage numbers exist only in effect_list text.** `effects.py` parses that
  grammar (damage/heal/ward/power/stat/proc, ranges, %-of-health, tick period);
  anything unrecognized is kept verbatim as kind `other`, so the coach can only
  under-use a spell, never misread it. Typed spell fields are reliable —
  **except `recovery_secs_tenths`, which stores HUNDREDTHS despite its name.**
- The character doc's typed stats carry everything coaching needs
  (`combat.abilitymod`, `basemodifier`, `critchance`,
  `ability.spelltimereusepct`/`spelltimecastpct`) — no text parsing there.
- Tests run entirely from recorded fixtures in `tests/fixtures/census/` via a
  fake injected as `census.client._shared`. No live Census in CI.

### Bulk spell ingest

Census spell records are BASE, pre-stat values per tier (all tiers of a line
share a `crc`) — in-game tooltips are these numbers with the player's stats
applied, which is exactly `fit.py`'s damage model, so no wiki scrape is needed.
`tools/ingest_spells.py --all --max-level 70` caches the full class books via
`sync.ingest_class_spells()` → `client.spells_by_class()`; `sync.ALL_CLASSES`
holds the 24 EoF-era names. It marks `spell_line:{crc}` so upgrade advice never
refetches. `census_spells` carries typed columns populated on insert by
`sync.typed_fields()` — the ONE owner of the unit conversions, including the
recovery-hundredths gotcha. `dmg_*` is the primary damage effect (largest
midpoint). `sync.backfill_typed_columns()` repairs pre-migration rows;
`spell_overrides` is the manual escape hatch.

GOTCHAs on the default `s:example` service id: it burst-throttles hard on bulk
pulls (less than one class book), and it silently CLAMPS `c:limit` to 100, so
paging advances by `len(rows)` and stops only on an empty page — a short page
proves nothing. `ingest_class_spells` therefore persists every page as it lands
and stores the offset in `settings` (`ingest_progress:{cls}:{max_level}`), so an
interrupted class resumes mid-book. Only the empty end-page clears the offset and
writes the completeness markers. A registered `CENSUS_SERVICE_ID` in `.env`
removes the limit.

**Census `crc=` silently rejects comma OR-lists** (`id=` accepts them), so
`spells_by_crcs` is one request per crc.

## The raid's guild, voted by its roster (schema v20)

Nothing in an EQ2 log says which guild a raid belongs to. The character doc that
answers "what class" also carries "what guild", so `census/roster.py` captures
both from one request. `census/guilds.py` turns that into `zone_runs.guild` — a
pill on the raid list and raid page, and a Compare facet.

**A wrong tag is a public claim about somebody else's guild**, so the vote
abstains twice as readily as it commits:

- **Abstain on thin evidence.** Fewer than half the roster resolved and there is
  no answer. About 18% of a real roster never resolves at all (pets, mobs, names
  typed before the character existed).
- **Tag only on a strict majority of what IS known, with the known-guildless
  counting against.** Ties fail the strict test for free.
- Runs under `RAID_MIN_RAIDERS` are forced NULL, imported from `groups.py`.

That needs three states, which is what `roster_classes.guild_checked` is for:
`1` with a name is a guild; `1` with NULL is **known guildless** and votes; `0`
is *never asked* and abstains. Collapsing the last two would let a backfill in
progress strip real tags.

**The tag is derived, so it is recomputed rather than maintained.** `retag_runs`
is pure SQL over cached rows — zero Census calls — and every write path that can
change a roster calls it: `pipeline/zoneruns.py` at the end of
`rebuild_zone_runs` (the funnel every upload, live close, reparse, merge, split
and delete ends in), `ingest_writer` after the parse-path lookups land, and the
hourly loop. So there is no staleness column and no `PARSE_VERSION` bump: NULL
means "no majority holds" and "not computed yet" alike.

Names cached before v20 have a class and no guild answer;
`backfill_stale_guilds` walks them oldest-first on the hourly tick, and
`sync_roster.py --guilds` is the same pass with no budget.

### Level and guild on an actor row

The same cached row carries a `level`. `_census_facts` / `_add_census_facts`
(`routers/encounters_api.py`) hang `level` and `guild` on every PLAYER actor in
`/encounters/agg` and `/encounters/{id}`, so opening a parse names a person
instead of a bare string. One indexed read per selection, zero requests.

Two limits. The world comes from the sessions in the selection
(`roster_classes` is keyed by name AND world). And unlike the class **these are
undated** — Census reports where somebody is NOW — so they caption a name and
never feed a number, and a raider Census never resolved gets nothing rather than
a zero.

There is no gear/AA link to go with them: EQ2U's character routes answer 200 with
an empty body, so it would be a link to nowhere.

### A class change is a DATE, not a tie (`_split_eras`, `_write_eras`)

EQ2 lets a character betray into the other half of their archetype, and pooling
forever guarantees a deadlock between two full spellbooks — which blanked those
raiders in **every raid they had ever appeared in**, more permanently with every
upload. So when the pooled vote deadlocks, the contenders' ability WINDOWS
decide: if the last ability of one class lands before the first of the other,
that is not ambiguity, because a character who betrayed cannot cast the old book
again. `_split_eras` cuts between the windows, infers each side from its own
evidence, and each session is answered from the era it falls in — which is why
these names skip the blanket write-back above.

**Disjointness is the whole test**, not "two strong classes": a raider wearing
another class's proc gear scores stray votes all night and those interleave. One
residual: `entities.class_guess` is per session, so a log spanning the changeover
gets the era it cast more of.

### Voting cannot tell you WHETHER the row is a person

EQ2 writes a summoned pet exactly like a raider — a bare capitalized name casting
its class's real spells — and it receives group buffs and gets warded like one
too, so every signal the vote reads agrees with itself. The tiebreak has to be
the file: `refine.roster_prescan` collects names appearing in a line **only a
player character can produce** — chat, raid join/leave, guild login, loot,
resurrection. A name with no such evidence is written
`{"class": null, "source": "unidentified"}` and the table says so, rather than
showing a classless raider.

Two deliberate asymmetries: the set is **over-inclusive** (server-wide chat
counts) because a name wrongly included only keeps the status quo while one
wrongly excluded strips a real raider's class; and the mark is written for that
session only and other sessions' inference will not overwrite it, because the
same name can be a raider in one log and a pet in the next.

**Coverage is the real limit, and it is a Census ingest job, not a voting
change.** Roughly half the ability names in a real raid log have no Census row at
all — AAs, gear procs and item effects are not in the spell books.

Stored as JSON in `entities.class_guess` (`{"class","confidence","matches",
"source"}`, read back by `parse_class_guess`), written at parse time and lazily
backfilled by the encounters API (`backfill_session`), so old sessions light up
without a reparse. `/agg` and `/encounters/{id}` carry `class`,
`class_confidence`, `class_source` and `archetype` on every actor — `archetype`
is NULL when the class is, since defaulting it to "dps" would be a claim we never
made. Cross-session merges keep the highest-confidence guess.

## Proc exposure

`ability_catalog.proc` is set from the Census "may cast X on…" grammar. Ability
rows carry `proc` and `ability_class`, which lets the UI split a parse into
cast / auto / proc — the difference between a player's rotation and their gear.

**The name alone over-claims.** Census flags a name if ANY item or buff can cast
it, so a class's own combat art gets marked as gear the moment some proc
references it. `_proc_flag` (encounters_api) makes the flag per ROW, requiring the
catalog's claim to survive this actor's evidence: `casts` counts prepare lines,
which procs never print, and the ability being in this actor's own class
spellbook.

That second test needs `ability_catalog.scribed` (schema v8), because
`ability_catalog.class` means two different things depending on which statement
wrote it — from a spell record it is who SCRIBES the ability, from a "may cast X"
effect it is whose buff FIRES it. Only a scribed row can clear a proc flag, and
curated procs carry no class list, so they stay flagged for everyone.
`catalog.backfill_scribed` (startup) repairs pre-v8 rows.

Still open: **buff attribution.** Damage from another player's buff proccing on
you is entirely yours, and sourceless `is hit by <Effect>` lines pool under
"Unknown". Real contributed-DPS for utility classes needs buff application/expiry
tracked into uptime windows — parser work.

## Pets and procs are ruled on, never inferred (schema v22, `PARSE_VERSION` 20)

The machine used to write labels off single sightings, in both directions, with a
feedback loop:

- **Pets.** `catalog.observe_pet_abilities` wrote `unit='pet'` globally and
  permanently for anything a pet-KIND entity cast — where "pet-kind" included
  `refine_bare_pets`' *guess* at a bare capitalized name, and that pass used
  Census `found=0` as a way in while never reading `found=1` as a way out. Real
  level-70 players were filed as dumbfires, their spellbooks were learned as pet
  kits, and `refine_bare_pets` reads that same table back — so each parse brought
  more names in. It converged on hundreds of pet-flagged names, roughly half of
  which Census knows as scribed player spells.
- **Procs.** `may cast X on …` names X for every buff that references it, so a
  class's own button gets flagged the moment anything can fire it.

The fix is a precedence ladder, not a better guess:

    ability_rulings  >  curated seed  >  no label at all

`census/catalog.pet_ability_names` / `proc_ability_names` are the only doors to
both labels and they encode that ladder in SQL; `encounters_api` calls them
rather than keeping its own copy, so one ruling reaches the badges, the rollup's
press counting and the coach together. `reset_verdicts` (startup, before
`seed_curated`) demotes every machine-written label into the candidate columns
`pet_seen` / `proc_candidate` — a database that already learned wrong loses its
bad badges on the next restart with **no reparse**, because the labels live in
`ability_catalog`, not in rolled-up rows.

What the machine still does is gather evidence, in three deliberately different
strengths:

- `pet_definite` — cast under `<Owner>'s <lowercase remainder>`, the swarm form
  nothing else produces. Certain by grammar.
- `pet_own` — under the logger's own bare name, certain by the YOU/YOUR rule.
- `pet_guess` — a bare capitalized name the refiner guessed at. **Every bad label
  came out of this column**, so a handful of them against thousands of player
  casts is discarded outright (`PET_GUESS_SHARE`).

`ability_pet_sightings` is keyed by session so the `PARSE_VERSION` sweep restates
evidence instead of inflating it.

### Pet kits

A summoner's pets share their owner's name, so which pet acted is only ever
readable from WHAT IT CAST. That map was co-occurrence guesswork and is now
measured — from fights where one pet was out and the owner cast nothing of their
own, which makes each fight exactly one kit.

- **Some abilities belong to the STANCE, not to a kit.** Three fire for every
  archetype; filing them under one kit split every single-pet fight across two
  rows and made the two look ~0.99 correlated in the co-occurrence pass. They now
  join whichever archetype the parse shows, and a parse showing two pets keeps
  them on a bare "Pet".
- **A pet attack the OWNER presses is not pet damage** (`PET_COMMANDED`,
  frontend `lib/stats.js`). Cast BY the pet, pressed BY the summoner: they carry
  no pet badge, never fold into a pet row, and never count toward the pet share
  of an actor's damage. Deliberately absent from `CURATED_PET_ABILITIES`.
- **A name-keyed claim is dangerous.** One kit was filed under the wrong class,
  and one ability was claimed at all — a name that belongs to a mob and a player
  far more often than to a pet, so the claim hung a "pet cast" badge on everyone
  else's combat art.
- **`seed_curated` RETIRES a name dropped from either tuple.** `reset_verdicts`
  spares `source='curated'` by design, so without the retirement pass an edit to
  those lists is a no-op on any seeded database.
- **One class's pet kit is deliberately NOT in the curated list**, though it is
  just as well evidenced. The curated list promotes an ability hiding under a
  PLAYER's name to a pet cast, so combining a pet we cannot see means claiming a
  remote player's own line is their pet's. That archetype is carried in the
  frontend map alone and only groups a pet with rows of its own — the rule is
  "only offer the combine if we have a parse from that player".

**A kit with no uploader can still be settled from the COMPLEMENT.** Where no
player of a class has ever uploaded, write down the class's OWN book, confirm
every entry against Census, and what is left over on that class's line is the
pet. Census's `may cast X` grammar read the right way round — from a named spell
to its damage line, not from a damage line to a guess — settles the leftovers.

**A pet's swing type belongs to the KIT, not to the archetype label**, so
`PET_KITS` carries `melee` per kit. Measure it WITHIN one player rather than
across several: pet choice correlates with who the player is, so the
across-players split is confounded.

**Some kits cannot be separated by analysis and need that class's own log.** Kits
separate when they are an either/or and anti-correlate; a pet that is always out
co-occurs with its owner's own book at 0.85–1.00 and no block falls out.
Subtracting the Census class book does not rescue it, because only ~40 base names
per class are ingested.

## Provenance comes from Census, not from a guess

Every spell record carries `given_by`, `type`, `alternate_advancement` and
`deity`, and `census/abilityreview.proc_sources` finds a proc's source spell by
its effect text — so the answer to "spell, AA, gear or deity" is usually already
cached.

`census/effects.py` had to keep the whole trigger clause, not just `may cast`:
the guaranteed `will cast` form is half the grammar. It now keeps `trigger`,
`mode` and `per_min`, because "on a melee hit, ~3/min" is a proc and "on a kill"
is a consequence of one, and they read identically once the condition is thrown
away.

What Census genuinely cannot answer is **gear** — `census_items` only holds items
something already referenced, and the ingest walks class spell pages. AAs and
deities are covered by `gamewiki.py`, so **"no cached spell casts it" means
GEAR**, and gear is closed (below).

**Self vs granted is a per-ROW question**, which is why
`ability_rulings.grant_class` exists: the same buff is a fury's own on a fury and
the fury's on the warlock beside them.

## The Abilities console (`/admin/abilities`, role `curator`)

Inference stops at "here is the evidence, here is how sure I am"; a person
answers the rest. `scope=open` is the queue — unruled and under full confidence —
and the class rail is the ergonomics: hundreds of undecided abilities is a wall,
a few dozen under one class is an afternoon. An ability sits under **every** class
that might own it (who scribes it, whose buff fires it, who was seen using it),
so one name appearing three times is correct until it is ruled on, and
`Unclassed` is where gear and AA procs collect. The search box reaches every
ability ever tracked, settled ones included.

`curator` is a separate role because the two jobs are unrelated: deciding what an
ability is needs someone who knows EQ2, and running the site needs someone with
the disk. It widens nothing about who can read a raid — the payload is ability
names, site-wide sums and class names, never a player name, an entity or a row
from anybody's parse.

**The lookup button.** Every ability row in a parse carries a ⚙ for a curator
(`BreakdownTable`, hidden until hover) linking to `/admin/abilities?q=<ability>`.
The place you NOTICE a wrong label is a raid page, not an admin queue. `?q=` is
the address rather than component state so the link works from anywhere;
`BreakdownTable` reads the viewer from `lib/session.jsx` because it renders in
three places that do not own a user, and that context carries no authority —
everything it gates re-checks server-side.

## A grant is to a TIER, not a class (`classtree.py`)

AAs are granted at every level of EQ2's tree, so "who gets this" cannot be one
class name. `classtree.expand` is the single translation: a tier name expands to
its subclasses, a subclass to itself. A ruling stored against a tier groups under
every subclass and will compare against all of them, without being written twice.

Census does not need this — it only ever writes subclass names, having expanded
groups before we see them. The tree is for the side Census does not cover: what a
person types. `normalize` is therefore strict — an unrecognized target is
REJECTED at the API rather than dropped, because a typo saved silently is a grant
that reaches nobody and looks like a decision.

**This is EQ2's own tree and NOT the role grouping in `coach/descriptive.py`.**
They answer different questions and must not be merged: that all six Fighter
subclasses are TANKS is a fact about role; that two of them share a tier is a fact
about the tree, and only the second says who an AA reaches.

Tier names came from the wiki's own category tree; two were corrected there
(Beastlord's tier-2 is **Animalist** and Channeler's is **Shaper**, not their own
subclass names).

## The wiki as reference data (`gamewiki.py`, schema v23)

Census stays authoritative for SPELLS. What it was never asked for is **AAs** and
items, and between them they are most of what a raid log names and nothing could
explain.

**`activated` is what earns the table.** `You prepare <X>` prints for spells and
combat arts and **not** for AA activations, so the log's only proof that
something was PRESSED is missing exactly where AAs live — making an activated AA
indistinguishable from a gear proc. Dozens of rows rested on that silence.
`suggest()` therefore checks `activated` BEFORE the prepare-line test; **the
ordering is the fix, not an optimization.**

**The wiki is also a proc SOURCE.** Its effect bullets use the same trigger
grammar Census does, so `census.effects` reads them unchanged, with letter
placeholders swapped for a zero because a wiki page covers every rank at once.
That identifies proc sources Census cannot.

**Era is a hard filter, not a preference.** The wiki separates AA trees by
expansion and they do not overlap at all. `DEFAULT_ERAS = ("eof",)` because this
is a level-70 TLE server; ingesting a later expansion would label raids with
content that does not exist here. Adding one later is one entry in `AA_TREES` and
a re-sync.

**Run by hand** (`backend/tools/sync_wiki.py`), never on a schedule: AA trees
change once an expansion, and a nightly job against someone else's wiki buys
nothing and costs them bandwidth. Content is CC-BY-SA — fine as internal
reference data, and anything surfaced to a reader should carry attribution. Tests
use pages recorded verbatim in `fixtures/wiki/` and never touch the network.

**Deities are the other half** (`--what deity`): blessings and miracles, always
EoF because deities arrived with it. Each carries its god in the `line` column —
the same slot an AA's line uses — and no class tier, because a god grants to
whoever worships it. All of them are activated.

**A NAME is not a key**, and getting that wrong cost real data twice:

- `wiki_abilities` is keyed on **(name, kind)**. One name really can be two
  abilities (a class spell and a deity miracle), and dozens of AA names collide
  with blessings. A single-column key let the deity sync overwrite them.
- The same AA on several classes gets one page each, and those **merge their
  tiers** rather than overwriting.
- A wiki row only speaks when **Census does not contradict it**. Matching by name
  is the weakest join available; a Census spell record is the game saying a class
  scribes it. So `scribed_by` wins and the wiki stays on screen as evidence.
  `by_name` marks a name the wiki itself holds twice as `ambiguous` and `suggest`
  refuses to be confident about it.
- Disambiguation pages are skipped outright — they are pointers, not abilities.

## Gear proc coverage is CLOSED AS WONTFIX

EQ2 has ~212,000 items, so a crawl is out. `{{EquipmentEffect|<Ability>|}}` on an
item page is a template PARAMETER, not a wiki link, so `list=backlinks` returns
nothing and **there is no reverse index to walk.** What is left is full-text
search with verification, and a trial over the 60 largest unexplained abilities
found 13 — two of them out of era, so roughly 15% yield for ~1500 requests. Unlike
the AA pull it corrects nothing, because those rows are already honestly marked
unknown. An unresolved gear proc is a curator's job.

Reopen only if Fandom enables CirrusSearch: `insource:` would turn the structured
`effectlist` into a precise one-call reverse index.

**This does NOT close items in general.** What is refused above is *ability name
→ which item casts it*, a reverse lookup with no index. Loot asks *item id → what
is this item*, and the raid log hands over the id — one exact request per hundred
ids, no search, no verification pass. See `backend/items.py` and
`docs/zoneruns.md` → "Items as reference data".
