# The Gear Planner — gear targets and a leveling outline

A page that answers three questions for one character on one expansion:

- **What should I be chasing?** Given the stats you are pushing, which drops,
  quest rewards and set adornments in this era are worth your time — and where do
  they come from.
- **What should I be doing?** Given what you picked, an ordered outline of the
  quests and targets that get you there, with the expansion's standard work
  hoisted to the front and the trips that overlap painted on top.
- **What would that build change?** Optionally load one of your cached Census
  characters, cycle current and planned items in concrete equipment slots, and
  project additive stat changes before chasing the gear.

**Which expansions count is the READER's choice** — a set of toggles in the
item-search header, not a build-time constant. EoF and RoK are what
exist today, either or both; RoK is the priority and the default. A third
expansion is a re-sync and one entry in `wiki.ERAS`, never a migration.

**PUBLISHED 2026-08-16.** It was a route (`/plan`) with no nav entry while it
was being built, on the plan that adding the tab would be the whole publish
step. It was: `Gear Planner` now sits in the top nav to the right of Compare
and, like Compare, it is there **signed out**. That is safe for a stronger
reason than Compare's — nothing here reaches a parse, a session or an account,
every row is reference data about the GAME, and there is no POST on the router
at all.

---

## Table of contents

1. [The three data layers](#the-three-data-layers)
2. [What comes from where](#what-comes-from-where)
3. [What we take from wikq2, and the language boundary](#what-we-take-from-wikq2-and-the-language-boundary)
4. [Scoring — an order, not a model](#scoring--an-order-not-a-model)
5. [The item is not the unit of value](#the-item-is-not-the-unit-of-value)
6. [Concurrency — what can actually be done together](#concurrency--what-can-actually-be-done-together)
7. [Clusters, and why pairwise proximity is unusable](#clusters-and-why-pairwise-proximity-is-unusable)
8. [UX](#ux)
9. [Schema](#schema)
10. [Ingest](#ingest)
11. [Phases](#phases)
12. [Risks and open questions](#risks-and-open-questions)

---

## The three data layers

Everything here sorts into one of three layers, and the layer decides how much
the machine is allowed to assert.

**Layer 1 — scraped fields.** Wiki templates (`EquipInformation`,
`NamedInformation`, `QuestInformation`, `TLInformation`, `AdornmentSet`), Census
item records, and the client-extracted map corpus. Named fields, mechanically
parsed, refreshable. **Layer 1 is era-safe** — a `level`, a `slot`, a `prereq`,
a coordinate. Nothing here needs a human.

**Layer 2 — claims nominated from prose.** "You can do the next 4 steps in any
order." "More than one update will drop if there are multiple necros in the
group." "Gives you The Greenmist Orb which you need to successfully kill The
Leviathan." These are stated in sentences and nowhere else, and they are the
difference between a list and a plan.

**A layer-2 claim is nominated with its sentence attached, and a human confirms
it** — the `ability_rulings` ladder, applied to a second domain. The machine
proposes an edge and quotes the line it came from; a curator accepts or rejects
it in the admin console. Nothing unconfirmed reaches the page.

**Era contamination lives in layer 2, never in layer 1.** The necromancer epic
page carries live-era strategy notes — Resolve gear, mercenaries, "mentor to
90" (a cap RoK does not have) — sitting directly beside RoK-accurate zones and
mobs. So the prose pass needs an era check on what it pulls and the field pass
does not.

**A quest level above the era's cap is a TAG, not a drift signal.** Journal
levels routinely run above the cap; a yellow or red quest is normal and usually
pays better. Comparing `level` to the cap to detect a stale page would mis-flag
correct pages and would flag exactly the ones worth surfacing. Show it as
"cons above you, pays better" and never as a warning.

**Layer 3 — hand-curated game knowledge.** The "everyone does this first" list
with a reason on each line. Sokokar before anything else because every other
line on the outline gets shorter. Languages, epics, the faction grinds that gate
access. Roughly 15–25 entries for RoK.

Layer 3 is `refdata/planner_standard.json`, keyed by era and edited by hand, sitting next to
`zone_eras.json`, `reuse_debuffs.json` and `split_mobs.json`. Same category of
thing, same rule already written down elsewhere in this repo: **game knowledge
is reference data, never inferred.**

Layer 3 is small enough to write in an evening and it is the layer that makes
the outline read as though somebody who played the expansion wrote it. The wiki
files `Sokokar Timeline` as a *peer* of `Kylong Plains Timeline`. Nothing in any
field anywhere says do it first.

---

## What comes from where

**The wiki is the reverse index Census does not have.** Census answers "what is
item 1546479523" exactly and cannot answer "every feet-slot item at level 80 a
necromancer can wear" — 212k items, no reverse index, which is why gear procs
are closed as wontfix in `docs/census-abilities.md`. `insource:` was re-tested on
2026-08-15 and CirrusSearch is still not enabled, so that stays closed on its own
terms.

The wiki's `EquipInformation` is a census dump in template form, with every stat
as a named field:

```
{{EquipInformation| icat = FABLED | slot = Feet | level = 80 |
  abmod = +98 | arspeed = 2.5 | potency = 3.7 | crit = 2.4 | critbonus = 0.8 |
  dtype = Cloth Armor | classes = {{AllSorcererCats|Equipment|yes}} |
  turquoiseslot = 1 | set = Focused Mind Set |
  itemlink = \aITEM 1546479523 -598653716 ...}}
{{Created_with_Census|id=1546479523|last_update=1653576113}}
```

**Both sides carry the Census item id, so the join is exact.** The wiki gives
searchability; Census gives the authoritative record; the log's `\aITEM` link
lands on the same key `items.py` already uses.

**Census intermittency is normal and is not an outage.** It comes and goes by
time of day. Every Census read here is cached and every Census-derived field has
a wiki fallback. Nothing on this page may block on a live Census call.

**Source attribution is built by INVERTING mob and quest pages, not by reading
items.** The `obtain` field on item pages is usually blank. `NamedInformation`
carries `drops` as a wikilink list, and `diff = epic x4` / `heroic` / `solo` is
the raid/group/solo split for free. Quest `==Rewards==` carries `{{Item|…}}`.

**The map corpus is client data, not wiki data.** `wikq2/data/eq2map/` holds
46,426 POIs extracted from the game's own map XML — **12,090 quest-update**,
2,105 quest-start, 11,618 mob, 2,339 npc, 1,982 zone-link — across 2,001 maps,
plus a zone travel graph of 3,759 nodes and 1,222 edges. Every quest step
location in the game, in absolute world coordinates, and every named mob's
position with it.

**`ERA_HIDDEN` and `zone_eras.json` already exist and are reused unchanged.** Era
is a hard filter here exactly as it is for the wiki ability ingest.

**Census's `all` is ABILITY MOD.** Already known and already handled in
`items.py`; it matters more here than anywhere else, since ability mod is the
stat most likely to sit at the top of a caster's priority list.

---

## What we take from wikq2, and the language boundary

wikq2 is a front-end to the same wiki that already resolves quest steps to
waypoints and follows cross-page links to do it. It is most of the extraction
engine, already written and already debugged.

Taken from `wikq2/lib/`:

| Symbol | File | What it does for us |
| --- | --- | --- |
| `collectQuestSteps` | `eq2.ts` | Quest steps as structured rows |
| `detectStepsAnyOrder` | `eq2.ts:1332` | **The "in any order" detector — layer 2, already built** |
| `extractCoordinates` | `coords.ts` | Waypoints out of step prose |
| `attachEq2MapMatches` | `eq2map.ts` | Wiki coordinate → client POI; the production matcher used by Phase 0 |
| `proximityDedup`, `dedupeIdenticalCoordinates` | `eq2.ts` | First cut at waypoint noise |
| `collectLocationListRows` | `eq2.ts` | Location tables |
| `findKnownAlias`, `pickDisambiguationEntry` | `eq2.ts` | Link resolution across pages |
| `wikiFetch`, `cached` | `wiki-fetch.ts`, `cache.ts` | Polite fetching and on-disk cache |
| `data/eq2map/*` | — | The 46k POI corpus, maps, map-links, travel graph |

**The extractors stay in TypeScript and run OFFLINE; eq2advanced ingests their
JSON.** wikq2 is Next.js/TS and this app is FastAPI/Python, and neither porting
2,663 lines of wiki parser nor running Node inside the API process is worth it.
The boundary is a versioned JSON artifact produced by a hand-run script — which
is precisely the `zone_eras.json` pattern already in the repo, and it means the
ingest inherits wikq2's fixes rather than forking them.

**The waypoint matcher has now been run across the whole RoK catalog.** The
2026-08-15 Phase 0 artifact covers 899 quests and 3,452 extracted coordinates.
After wikq2 v89 conservatively filled missing zones only when the structured
Starting Zone and zone Timeline agreed, 3,096 coordinates carried a zone label
and 2,584 matched: **74.86% of every extracted coordinate, 83.46% of zone-labeled
coordinates**. The five main RoK zones each match 90.0–94.5%; median confidence
is 0.98. The remaining 356 unzoned coordinates are predominantly cross-zone
epic/city-task work and stay unclaimed rather than inheriting a plausible but
wrong starting zone.

---

## Scoring — an order, not a model

**The priority list is an ORDER, not a set of numbers, and the UI never shows a
weight.** You say "ability mod, then reuse, then casting speed"; ranks map to a
decaying weight internally and that number is never surfaced.

**No cap or diminishing-returns math, and no set optimizer.** Both were
considered and rejected. Caps and soft caps only matter when the tool is trying
to *make the choice*; this one presents options and the reader chooses. A
weighted sum over a declared order is exactly the right fidelity for that, and
inventing precision the model does not have is worse than not having it.

The optimizer is additionally wrong on its own terms here — see the next
section. You cannot optimize a set whose most valuable component detaches and
moves to a different set.

**Sliders are rejected for the same reason.** A slider invites tuning and implies
the third decimal place means something. The priority control is three ordinary
dropdowns, numbered 1–3 and defaulting to Any.

### What can be prioritized, and what cannot

**POTENCY AND CRIT CHANCE ARE NOT PRIORITY OPTIONS.** They are on essentially
every EoF/RoK item — measured on the built catalog at **80% and 72% of 5,282
rows** — so ordering by them orders by nothing: every candidate has them, and
the ranking collapses back into "how expensive is this item". The stats that
separate two pieces of gear are the ones that are NOT on everything, and every
option below sits between 0.2% and 50%. They stay on the examine card and stay
available as table columns; they are simply not something to rank by.

**Crit Bonus is a third case and is absent for a different reason again**: TLE
does not have the stat at all, so it is dropped at parse time
(`ERA_HIDDEN_FIELDS`) and never reaches a card either.

The options are these thirteen, grouped the way a raider already thinks about
them (`wiki.STAT_GROUPS`). The first group reaches every class, because every
class casts abilities; the next two are what a melee and a tank are actually
shopping for. Max Health stands alone — wanted by more than one of them,
belonging to none.

| Group | Stats |
| --- | --- |
| **Abilities** | Ability Mod, Casting Speed, Reuse Speed |
| **Melee** | Haste, DPS, Multi Attack, Flurry, AE Autoattack |
| **Tanking** | Block Chance, Hate Gain, Mitigation, Strikethrough |
| **Also** | Max Health |

The page opens on the first group, because it is the one that applies whatever
you play. That is a starting point and not a recommendation — an empty order
scores nothing at all, and a table with no ranking cannot show what the page is
for.

`catalog.weights` honours **only** these keys, whatever a hand-built URL asks
for. That is not tidiness: a query string must not be a way around a rule that
exists because the answer would be meaningless.

### The four-stat floor

**EQ2 gear in these expansions is FOUR-STAT — potency and crit, which
everything has, plus two more.** So an item can carry at most about two of
whatever you listed, and naming three stats is not a request to see everything
carrying one of them.

Measured on the built catalog, that was the bulk of the list: **45% of the
5,282 rows carry no more than one priority stat**, and only 8% carry three or
more once mitigation (which every armour piece has) is set aside. On a 3-stat
order the floor takes RoK from 2,881 rows to 538.

So `search` applies a **match floor** — how many of the ranked stats a row must
actually carry — defaulting to `min(2, n)` (`FOUR_STAT_FLOOR`). It is counted
over the stats that RANK, so a hand-built order carrying potency cannot let a
row in on it, and it is **answered back in the response** so the page shows
"2 of 3" beside the table rather than dropping half the catalog silently. The
reader can loosen or tighten it.

**This is not `required`, and both apply.** `required` is per-stat and absolute
— "I will not look at anything without ability mod". The floor is about the
order as a whole and says nothing about which of them a row has.

### Hard filters

Separate from ranking and behaving as filters: class, **armour weight**, slot,
level range, **rarity**, era, and source kind (raid / group / solo / quest /
**world drop**, any combination).

**A RARITY IS ASKED FOR BY THE WORD A PLAYER USES, not by the word the wiki
stores** (`wiki.TIER_BUCKETS`, 2026-08-16). `icat` holds eleven distinct
spellings across the real catalog — `MASTERCRAFTED LEGENDARY`,
`MASTERCRAFTED FABLED`, `FABLED, GREATER RELIC`, `UNCOMMON`, `-` — and offering
all eleven asked the reader to know the wiki's vocabulary instead of the game's.
Five buckets, ascending: Handcrafted, Treasured, Legendary, Fabled, Mythical+.

**How a piece was MADE is not a rarity**: mastercrafted armour is Legendary
quality and a mastercrafted fabled piece is Fabled, so both fold into the tier
they actually are rather than becoming a sixth facet row. The top three fold
together because on a TLE server they are one answer — "past fabled" — and
splitting seven Mythical rows off would be three near-empty rows. Matched on
WORDS PRESENT, checked from the top down, so `MASTERCRAFTED LEGENDARY` reaches
Legendary and not Mastercrafted-something. **A value nothing recognizes stays
bucketless** and is simply unreachable by the filter: inventing a rarity for it
would be a claim about the item the wiki never made. `plan_items.tier` is
untouched — the card and the rarity colour still read the crawled string — and
the raw spelling is still accepted as a filter value so older links work.

**A two-hander says so in the slot: `Primary/2H`.** The wiki files a greatsword
and a dagger under the same `slot = Primary`, which invites comparing them as
though the other hand were still free — and **162 of the catalog's primaries
take both hands**. The fact is in `dtype` ("Two-Handed Crushing"), so
`wiki.slot_label` lifts it out the same way the armour weight is lifted, and
the naming decision lives on the server so anything else showing a slot says
the same thing. Two-handers still appear under a Primary filter, which is
right: they are what you would be putting there.

`dtype` also separates "Main Hand" from "One-Handed" — a real difference, since
a main-hand weapon cannot go in the off hand — and that claim is deliberately
NOT made yet. 33 items carry it.

**Armour weight is the first thing a player checks on a drop** — it is the one
property that rules an item out before any stat on it matters, since a plate
tank cannot wear leather however good the numbers are. The wiki keeps it in
`dtype` alongside weapon and shield types ("One-Handed Crushing", "Tower
Shield"), so `wiki.armor_of` lifts the four words out of that field rather than
storing them twice: no migration, no re-crawl. It is offered in weight order
(Cloth, Leather, Chain, Plate) rather than by how many items there are, because
it is a fixed scale a player already has in their head.

---

## The item is not the unit of value

In EoF and RoK the set bonus is no longer on the armor. It is on a **turquoise
adornment that ships in the item and can be removed and moved into any item of
the same level or higher.** The wiki says so in as many words on
`Mist Covered Set (Armor Set)`:

> *"When the set got released the set effect was on the armor, its on the
> adornments now."*

The structure follows: the armor set page is a stub that transcludes a separate
`(Adornment Set)` page carrying the tiers, and each turquoise is a first-class
Census item named `<set>: <slot>`.

**`items.py` already resolves this** — [`items.py:648`](../backend/items.py)
takes the wiki's `set` field, looks up the companion under `<set>: <slot>`, and
folds its bonus block into the gear card so the reader never chases a second
link. The Planner queries that join in bulk instead of one card at a time; it
does not reinvent it.

So an item is a bundle of three separable things, and all three are already
fields:

1. **Base stats** — ranked against your order.
2. **The turquoise it ships with** — a portable asset whose value is the set
   bonus, not the item. `set = …`.
3. **Adornment capacity** — the item as a *destination* for someone else's
   turquoise. `turquoiseslot = 1`.

**A turquoise is shortlisted separately from its host item.** "This Fabled has
mediocre stats but carries the 6-piece turquoise, and these five items can host
it" is a query, not a judgement call — and for a raider deciding what to bid on,
*harvest target* is a different answer from *upgrade*.

**A proc can beat the stat block, and effect text is layer 2.**
`effectlist`/`effectdesc` are free text; a batch pass classifies each into
(trigger, type, magnitude, rate) once, and a curator corrects the ones that
matter. Whether a spell-cast-triggered proc is worth anything is a class
question, and class questions come from the game.

**Building this catalog closes the gear-proc wontfix for the only era the server
has** — reverse lookup over a few thousand era-filtered rows instead of a search
across 212k. That is a dividend, not a reason.

---

## Concurrency — what can actually be done together

Two quests in the same timeline share waypoints and **cannot be worked at the
same time**. Any co-location feature that ignores this produces confident
nonsense.

**Quest level.** `prereq`/`next` gives the chain. Transitive closure over the DAG
is precomputed as a bitset; **two quests may pair only if neither can reach the
other.** A few thousand nodes, computed once at ingest.

**Step level.** Parallelism inside a quest is stated in the prose and
`detectStepsAnyOrder` already finds it. From the necromancer epic: *"You can do
the next 4 steps in any order."* Steps not covered by such a statement are
assumed sequential.

**Prerequisites are sometimes DISJUNCTIVE.** Sokokar requires adventure 65
**or** tradeskill 65, two separate quest lines reaching one unlock. The graph
needs OR-nodes from the start; retrofitting them once every consumer assumes a
flat prereq list is the kind of change that touches everything.

**Hard prerequisites and enablement are DIFFERENT EDGE TYPES.** A hard edge says
you cannot. An enablement edge says it gets much cheaper — travel, language,
faction, a key item. Sokokar is the canonical enablement edge and appears in no
field. So is this, from the RoK raid flagging timeline:

> *"Imzok's Revenge — Gives you The Greenmist Orb **which you need to
> successfully kill The Leviathan**"*

A kill→enables→kill dependency, in a sentence, in a paragraph. Layer 2.

**In multi-class mode the concurrency rule INVERTS.** Different people hold
different chains, so steps one person could never have active together are
routine for a group. The constraint becomes per-person, and the interesting
question is which steps *share*:

> *"Note that more than one update will drop if there are multiple necros in the
> group."*
> *"…can be harvested by multiple necros without having to wait."*

**Every group-relevant step is flagged ONE KILL SERVES ALL or EACH NEEDS THEIR
OWN**, because that flag is the difference between one Chardok trip and four,
and the wiki records it.

---

## Clusters, and why pairwise proximity is unusable

Naive pairwise waypoint distance reports thirty overlaps between two questlines
on the strength of a couple of incidental locations, and the reader sifts through
noise to find the two that matter. That is the failure mode to design against.

**Score CLUSTERS, never pairs.** Group waypoints spatially (grid-bucketed within
a map, using the same radius primitive the wikq2 roadmap designed for the
per-waypoint tooltip), then score each cluster by:

- how many **concurrency-eligible** distinct quests it touches, and
- what **fraction of each quest's steps** fall inside it.

**Materiality is a threshold, not a ranking tweak.** One stray step out of
fourteen is a coincidence; five of nine is a trip. A cluster below either
threshold is not shown at all — it does not appear greyed out or folded away,
because the whole point is that the reader never sees it.

Clusters overlap freely — they are sets over shared waypoints — so one tag
covering three quests, another covering two of those and a third covering one of
those plus three others is the expected shape, not a special case. Rank by total
work consolidated.

**Three resolutions, and "same zone" is the weakest of them.** Kunzar Jungle is
enormous and "both in Kunzar" is nearly useless. The useful answers are *within
80m of each other* and *one travel hop apart* — the latter from the `map-links`
graph, which is also how the outline knows that finishing sokokar just dropped
the cost of adding a zone to a run.

**A cluster is named as a PLACE, never as an id.** "Jarsath Wastes — the two
temples" is a trip. "Cluster 7" is a database row.

---

## UX

### The shape of the page

**ONE PRIMARY COLUMN WITH A CONTEXTUAL OUTLINE** (Lindsay, 2026-08-16). The first
build used `ZoneRun`'s rail-plus-main geometry, with a 292px left column
holding the title, the expansion chips, the class picker and the shortlist.
Lindsay's read of it: that block is obsolete. It was true — three controls and
a list were costing a fifth of the screen, and the page paid for it twice, with
an item table that scrolled sideways and a projected-stats panel that scrolled
vertically for room the rail was holding. Everything in it had a better home.
The Outline now uses otherwise idle space as a collapsible right column: it is
absent for an empty plan, opens when the first item, adornment, or target is
selected, and can be collapsed back to a compact button in the page head.

```
┌────────────────────────────────────────────────────────────────┐
│  Gear Planner                                  [Outline 3 ›]    │
│  EQUIPMENT & STATS / Bobby · Level 70 Necromancer   [lookup][▾]│
│  ┌ armour + weapons ┬ charms + jewelry ┬ projected stats ─────┐│
│  │ items + sockets + per-slot reset    │ (two columns, no     ││
│  │ worn set bonuses under the window   │  inner scrollbar)    ││
│  └─────────────────────────────────────┴──────────────────────┘│
│  search: item name                              [clear filters]│
│  ┌ STAT PRIORITY ──────────┬ FILTER ───────────────────────────┐
│  │ 1 [Any▾] 2 [Any▾] 3 [Any▾] │ Class▾ Slot▾ Armor▾ Tier▾ Lv–Lv│
│  │                            │ Source ☐raid ☐group … [chips]  │
│  └────────────────────────────┴───────────────────────────────┘│
│  ┌────────────────────────────────────────────────────────────┐│
│  │ item table (SortableTable, frozen)                         ││
│  └──────────────────────────────────────────┬ Outline ─────────┤
│                                             │ shortlist + path │
└────────────────────────────────────────────────────────────────┘
```

Where the rail's four things went, and why:

- **The title** is a clean page head; the old descriptive tagline is removed.
- **The expansions stay OUT of the filters**, in the item-search header. Lindsay
  offered them to the filter band; they govern more than the item table —
  which items exist, what a score is measured against, which sets are offered,
  which quests the Outline knows. A control all of those surfaces obey lives
  above the catalog rather than pretending to be one more result filter.
- **Class joined the facets**, which is what it is. Alone in the rail it read
  as a page-wide setting, and it is the single control most likely to be what
  emptied a table — `EmptyTable` names it by name for that reason.
- **The shortlist moved into the contextual Outline column**, which is the
  surface that consumes it. The column opens only once there is something to
  act on, so it does not tax the empty gear-search state.
  It still holds THREE kinds of thing separately: items, adornments, and
  targets (a mob or a quest you want for its own sake). A turquoise is not its
  host item and a raid target is not a slot.

Two more the same read caught:

- **A name search is the size of a name.** The box was `minmax(260px, 1fr)` in
  a grid, so it took every pixel the band had left — making the page's least
  used control its largest thing, when the facets under it do most of the
  narrowing.
- **Item level is ONE facet, not a band.** It had its own heading, its own two
  labelled boxes and the word "to" between them, for a thing that is read as
  "70–80". It is now a labelled pair of small number boxes in the filter row
  with a dash between, wearing the same selected state as every facet beside
  it.

### The equipment and stats workspace

The layout borrows the game's **mental model**, not another site's row design:
armour and weapons keep the game's left-side sequence, while charms and jewelry
keep its right-side sequence. There is deliberately no paper doll: it consumed
the page's most valuable vertical space without helping a gear decision. Rows
and icons are compact enough to keep search and results prominent, while the
stat ledger remains clean site UI.

Projected stats follow the in-game grouping more closely: unheaded Health and
Power rows lead directly into Attributes, followed by Defense, Offense, and
Autoattack. Every value uses the same aligned value-and-delta ledger row.
Casting Speed and Reuse Speed sit in Offense directly after Ability Mod, in that
order. A current equipped value is green; a planned
increase is cyan and a planned decrease is red. The signed delta repeats the
direction so color is never the only signal. DPS, Haste, and Multi Attack are
displayed as ratings without percent signs, matching the in-game stat window.
Item Primary Attributes are normalized before projection: the wiki's legacy
STR/AGI/WIS/INT field and Census's grouped `strength` storage both expand to
Strength, Agility, Wisdom, and Intelligence by the same amount. Stamina remains
its own stat. Catalog examine cards show that value once as Primary Attributes.

Clicking a slot filters the catalog to legal choices for that position. Adding
an item puts it in that concrete slot and activates it. Multiple candidates stay
in the slot and cycle with previous/next controls; **Current is always option
one**. Finger, Ear, Wrist and Charm keep their first/second identities. A
planned two-hander occupies Primary and removes Secondary from the projection.

The projection is explicit arithmetic over cached data:

`current Census total − current item stats + active planned item stats`

Under the planner's empirical TLE model, projected attribute deltas also flow
into the two vitals at every character level: each Stamina point adds 8 base
Health, while each archetype's primary stat adds 8 Power (Fighter Strength,
Priest Wisdom, Mage Intelligence, Scout Agility).
The current Census totals already contain the equipped attributes, so only the
difference produced by planned gear and active additive set bonuses is applied.

Max Health percentages add together and multiply the underlying Health pool,
not the already-modified displayed total. The estimated TLE pool is `2,476 +
STA × 8`. This is measured in game on Bobby: naked effective Stamina 25 produces a
2,676 pool; his 2% racial displays 2,729 Health; adding an otherwise-statless
2.2% Max Health item displays 2,788. With gear and buffs, removing that same
item moves 12,653 to 12,412, a 241-point difference and therefore an underlying
pool of about 10,955. The projection infers the character's existing combined
racial, buff, and equipped multiplier from the Census total, then applies gear's
signed Max Health difference to the projected pool. It follows the game's
one-decimal modifier precision and whole-Health floor, while anchoring the
resulting difference to Census's exact current total. Until another TLE
measurement contradicts it, the planner applies that empirical rule at every
level rather than suppressing vital projections away from level 70.

Only additive numbers enter it. Procs, named focus effects, cap behavior and
moved adornments are not guessed. The panel labels itself an estimate and says
what is excluded.

### Adornment sockets and set bonuses

Adornments are **socket icons on every equipment row**, beside the item they
belong to rather than in a detail strip below the window. White and orange
sockets show whether Census reports an installed id; clicking a turquoise
socket cycles through the shortlisted set adornments the host item's level can
accept. Hovering an installed adorn opens the same examine window with its
name, icon, type, level, stats and set thresholds when those records are cached.

Every change recomputes installed pieces and the set threshold ledger
immediately. Named effects remain prose and visibly activate at their threshold.
Simple additive bonuses (Ability Mod, Potency, Crit Chance, Casting Speed,
Flurry, attributes, Health and Power) are conservatively typed by the server and
also feed projected stats. Any sentence that is not exactly a known
number-plus-stat remains prose, and a comma list types only when EVERY segment
does — half a sentence read as arithmetic is worse than none of it.

**A SET TIER IS A BLOCK, NOT A LINE** (`wiki._BONUS_TIER`, corrected
2026-08-16). The page writes the proc on the `*(N)` line, its explanation in
`**` sub-bullets under it, and the tier's flat stats as **bare unbulleted lines
after those**, one per stat. The game draws those bare lines back onto the
tier's own line — "(6) 4 Potency, 100 Ability Mod, 5 Crit Chance" — which is
where they belong and where a single-line read could never find them. Reading
only the `(N)` line lost the Potency off every tier that also had a proc, kept
one stat of three where a tier had no proc, and **dropped the biggest tier
outright** whenever its own line was empty. The card now shows the stats as the
tier line and the proc plus its explanation as the bullets beneath, which is
the examine window's own arrangement.

### The priority control

The most novel control on the page, and the easiest to get wrong.

**Three dropdowns, numbered 1–3, each defaulting to Any** (Lindsay, 2026-08-16).
The list is still an ORDER and still shows no weight — what changed is only how
you say it. The first build was a draggable track carrying all thirteen
rankable stats, with a "Score top 1/2/3" control setting the boundary between
the ranked left edge and the rest. It made a reader arrange every stat in the
game in order to name two, and it needed a second control to say where the
ranking stopped. **Three boxes say the same thing and the number of boxes IS
the boundary**, so "Score top" is gone with the track that needed it.

Their options are grouped by `wiki.STAT_GROUPS` — Abilities, Melee, Tanking —
as headers in the panel, which is how a raider already sorts them. An empty box
is a hole that closes: naming a stat in box 3 while 1 and 2 say Any makes it the
first priority, because a gap in an ordering is not a thing.

**The rows carrying ALL your stats lead the table**, then the partial ones in
score order under them (`catalog.search`, and the `matched`-first sort key on
the Score column so a client re-sort agrees). Naming a third stat is asking for
the items that have all three; in four-stat expansions that is a handful or
none, and a two-stat item with large numbers outscores a three-stat item with
modest ones — so a pure score sort buried exactly the rows the third choice was
made to find. This is a TIER, not a filter: nothing is hidden for being one stat
short, and the four-stat floor below is unchanged and still applies. It is done
on the server because the sort decides which rows survive `limit`.

**`required` (a stat moved from ranking to filtering) is still a server
parameter and no longer has a control.** It lived in a modal hung off the drag
track; with the track gone it had no home, and the tiering covers most of what
it was for. `components/PriorityEditor.jsx` was deleted with it. Reopening it
means a "must have" toggle beside each of the three boxes, not the modal.

Search itself is one framed control window with four bands: name and item level;
**stat priority beside the facets** in one band, split by a rule — they are two
halves of one question, and a reader who has just said "ability mod first" is
about to say "chest only"; then the attached result count and scoring summary.
Selected facets use a strong gold state; inactive controls remain readable
instead of looking disabled.

**A facet's NAME is outside its box.** Folding it in ("Any armour") made the
control say what it was only while it was doing nothing — pick Chain and the
word "armour" left the screen, so a band of set facets read "Chest, Chain,
Fabled" with nothing saying which was which. The label is a standing part of the
row; the box holds the answer, which is "Any" until you give one.

**Source is checkboxes, not a dropdown.** "Group or raid" is a normal thing to
want and was two searches while it was single-choice; six short words fit on the
band unopened, which is the other half of the case. `kinds` was already a list
on the server.

**A separate "interested in procs" switch**, because that is a different axis
from any stat order and the reader said so in those words. On, the table's proc
badge column sorts to relevance; off, it is decoration.

Every dropdown is a `Picker` rendered into `document.body`. **Never
`<select>`** — house rule, and the backdrop-filter stacking trap applies here as
everywhere.

### The item table

One `SortableTable`, frozen, with the house column-preference behavior
(`eq2adv:cols:planner`). Columns: name, tier, level, score, the reader's
priority stats as their own columns, source, and two badge columns.

**CLICKING THE ROW PUTS THE ITEM IN THE WINDOW** (2026-08-16). The checkbox is
where the state lives and it still works, but nobody arrives at a table of gear
hunting for a checkbox — they click the thing they want, and "tick the box" was
not a gesture the page had taught anybody. The row shows it is in with a green
edge, since a click anywhere deserves a bigger answer than a 12px mark. **The
name cell stops the click**: it is a link to the wiki and still goes there.

The name, slot, armour, tier, source and **minimum/maximum item level** filters
are independent of scoring. The name hover uses the full examine shape: item
level, slot/type, additive stats, effects, socket colors, **class restriction**,
included set adornment and every known threshold—not merely the small table
columns.

**The card names who can wear it, and stays quiet when everyone can.** It is
the one property that rules an item out before any number on it matters, and
`catalog.card` sends nothing when the class list is the whole era-filtered
subclass set — a list of every class on the server is not a restriction, and the
game does not print one either.

**Loading a Census character does NOT set the class filter** (removed
2026-08-16). It did, and the result was a search that silently answered a
narrower question than the one on screen: the level-70 Head/Reuse search that
started all of this came back empty because exactly one such item exists in EoF
and it is illusionist-only, with nothing on the table saying a class had been
applied. The loadout panel is about one character; the item table is about the
expansion, and the reader narrows it when they mean to.

**AN EMPTY TABLE SAYS WHICH CONTROL EMPTIED IT** (`Planner.EmptyTable`).
"Nothing in this expansion matches" reads as "no such item exists", which is a
claim about EverQuest II that a crawl of somebody else's wiki is in no position
to make. `search` answers `before_priorities` — how many rows survived every
filter EXCEPT the stat controls — which separates "the stats found nothing among
rows that do exist" from "there were no rows", and the note under it says the
catalog is a crawl either way.

**The two badges are the point of the table.** *Carries a set turquoise* and
*has a proc* both say "this row's value is not in its stat columns." Clicking
either opens `ItemCard` — the existing EQ2i-replica examine card, which already
folds in the companion adornment.

**`ItemCard` is reused unchanged and still does not theme.** It is a replica of
EQ2's item box; it wears EQ2's colors, not the site's, exactly as it does on the
Loot tab.

**Rank coloring is NOT reused here.** On a parse, color is placement within a
role among peers who did the same thing. A table of items has no roles and no
peers, and borrowing the ramp would imply a comparison the data does not
support. Score is a number in a sortable column and nothing else.

**Adding to the shortlist is a checkbox in the name column**, pinned with the
name at `--fzleft`, matching every other checkable table in the app.

### The set-adornment view

A filter mode within Gear, not a third tab: **rank the set bonuses themselves**,
each row showing the tiers, which items carry which piece, and — separately —
which items in the reader's era can *host* it (`turquoiseslot ≥ 1`,
`level ≥ source level`). Shortlisting from here adds the adornment, never the
armor it came in.

### The Outline

A single ordered list. **It never reorders.**

Two sections:

**The prelude** — layer 3, visually distinct because it is not derived from your
shortlist. Every row carries its *because* on the same line:

> **Sokokar post network** — do this first; every other line on this list gets
> shorter. *Adventure 65 or tradeskill 65. Starts in Kylong Plains.*

**The body** — ordered by prerequisite, then level. Each row: what it is (quest
or target), level, zone, difficulty, what it gets you from your shortlist, and
in Phase 3, its tag chips.

**The outline answers "what order"; the tags answer "what trip." They are
different questions and they conflict.** Prerequisite order and travel
efficiency disagree constantly, and every route planner that tries to satisfy
both by reordering the list produces something nobody can follow. The list is a
stable spine and the tags are a lens over it. **Nothing moves when a tag is
selected.**

### Tags

**Selecting a tag HIGHLIGHTS, it never filters.** Filtering collapses the list
and loses the reader's place, which is the same failure as reordering it.

**Members take a left-edge bar and a background tint in the tag's color;
non-members are left alone.** Dimming ninety percent of a long list to emphasize
six rows is visually violent and makes the page unreadable at a glance.

**Only the strongest eight tags get a color; the rest are grey until selected.**
This is not a guess — the palette validator behind `lib/classes.js` already
established that four hues separate cleanly and twenty-six cannot, and the
8-color validated chart-series palette already exists in this app. Tags borrow
it; the Outline has no charts, so there is no collision.

**Tag color must NOT come from the class palette.** Class identity already owns
archetype color everywhere in this app, and multi-class mode puts class chips on
the same rows as tag chips. Two color systems on one row have to be visibly
different systems: classes stay chips that spell the class out, tags are edge
bars and legend swatches.

The legend is a strip above the list, ordered by consolidated work. Clicking a
chip in the legend or on a row does the same thing. Tags are multi-select;
selecting two shows both, and rows in both get both bars.

**Reduced motion is assumed on.** The highlight is a cross-fade, not a slide or
a pulse, and it is degraded rather than removed.

### Multi-class mode

A class multi-select where the single-class facet is now. With one class the
page is personal;
with several it becomes a group plan, and the framing shifts to epics — because
that is the case where different quests genuinely share zones and mobs, and
where coordinating is the actual pain.

Each outline row gains **class chips for who needs it**, using the existing
archetype colors and spelling the class out.

**Every shared step shows ONE KILL SERVES ALL or EACH NEEDS THEIR OWN.** This is
the single most useful thing on the multi-class view. Four necromancers on one
Queen Velazul Dizok kill is one trip; four people each needing their own drop is
four, and the estimate at the top of the outline is wrong by 4× if the page does
not say which.

**v1 assumes everyone starts at the beginning of their own chain.** Per-person
progress tracking is a later concern and should not hold up the joint route.

### States and edges

- **Empty shortlist** — the Outline column stays closed. The expansion prelude
  appears with the derived path once the reader has selected something to
  chase; until then the page gives its width to equipment search.
- **No clusters found** — the tag strip is absent, not empty. An empty legend
  reads as broken.
- **Unresolved item** — a name, no card. `GET /api/items/{id}/card` answers
  `null` for an unresolved id and must not trigger resolution; **`items.ensure`
  is network-bound and never runs in a request handler.**
- **Census dark** — the page works. Installed adorn IDs still render as
  equipped rather than empty even when their name records have not hydrated;
  the next successful character refresh fills names/icons/stats. The reader's
  last cached gear/stats remain available and signed-out readers keep the
  complete catalog. Intermittency is normal, not a fault.
- **Narrow viewport** — the head's three parts stack at the breakpoint the top
  nav wraps (900px), and the priority half of the choice band moves above the
  filter half at 1100px. The outline stays a single column at every width; the
  item table scrolls sideways inside its wrapper.

### Theming

Site tokens everywhere except `ItemCard`, which is inside EQ2's chrome and wears
EQ2's colors. Translucent fills are `rgba(var(--x-rgb), 0.NN)` and **never
`color-mix()`** — the tag bars in particular, since they are exactly the sort of
thing that invites it. Keep any new `-rgb` pairs in step across both themes.

---

## Schema

All planner tables are reference data; none touches a parse, an account or a
visibility predicate. Phase 2 is schema v42, guarded by table SHAPE like every
other migration in `db.py`. Later-phase rows below remain design, not schema.

| Table | Holds |
| --- | --- |
| `plan_items` | Catalog: census id, name, slot, level, tier, classes, armor type, stats, effect text, `set`, adornment slots, era |
| `plan_effects` *(planned)* | Classified proc/effect rows keyed to `plan_items`, with the source sentence |
| `plan_sets` | Adornment sets, their tiers, their pieces |
| `plan_sources` | item → source (mob / quest / merchant), with kind raid / group / solo / quest |
| `plan_quests` | Quest, timeline, level, zone, difficulty, journal category, era |
| `plan_quest_edges` | `prereq`/`next`, typed **hard** or **enable**, with OR-groups |
| `plan_steps` *(planned)* | Steps per quest, with an `any_order` group id |
| `plan_waypoints` *(planned)* | Step → coordinate → map/POI match, with match confidence |
| `plan_clusters`, `plan_cluster_members` *(planned)* | Computed tags and membership |
| `plan_nominations` *(planned)* | Layer-2 candidates awaiting a curator, with the quoted sentence |

`refdata/planner_standard.json` holds layer 3, keyed by era, and is **not** a
table — it is edited by hand and read like `zone_eras.json`. Adding a third
expansion is one more key, not another file.

Everything above is per-era reference data about the game. **One row serves every
account forever**, exactly as `items.py` puts it.

---

## Ingest

Offline, hand-run, never scheduled — the same rule as the wiki ability ingest,
for the same reason.

### What the crawl has to ask, and why the obvious question is the wrong one

Three separate things had to be added on 2026-08-16, after a level-70 Head
search for Reuse Speed came back empty while the broker was full of matches.
All three are the same mistake in different clothes: **asking the index the
wiki organises its own way instead of the one that answers our question.**

**1. The expansion category is not the expansion.** The wiki does not tag
mid-expansion content with the expansion. A monster added by a live update
carries `LU39`, `Tier 8` and its zone, and never `Echoes of Faydwer` —
`Kza'Bok` is exactly that, and he drops the level-70 treasured gear the search
was looking for. `Category:Echoes of Faydwer Named Monsters` holds 382 mobs
where the expansion really ran to 499, and the whole of Shard of Fear was
invisible. **So mobs are asked for BY ZONE**, because which expansion a zone
belongs to is already reference data here (`refdata/zone_eras.json`,
`zones.in_era`) and `tools/sync_zone_eras.py` already resolved the live-update
numbers against the launch dates. Strictly broader, no date arithmetic, no
second list. (`wiki.named_categories`.)

**2. Inverting mobs can never see a trash drop.** A named page LINKS what it
drops; nothing links what an unnamed mob drops, and that is most of what a
broker search returns. `Category:<zone> Dropped Items` is the only index of it
— 1,611 pages across EoF's zones and 1,345 across RoK's. Those enter as a
source kind of their own, **`zone`, shown as "World drop"**, and only for items
no named and no quest already accounts for: a drop that HAS a monster gets a
better answer from the monster. (`wiki.drop_categories`, `wiki.zone_source`.)

**3. A set piece is behind a CRATE, and the crate is what drops.** The Priest of
Fear drops `Faydwer Cloth Pattern: Head`; what you equip is one of three hoods
inside it, and only one of those carries Reuse Speed. A crate is an
`ItemInformation` page, so `parse_equip` rightly refuses it — and the armour
behind it was reachable from nothing at all. The crawl now follows a crate's
`contains` list the same way it follows a disambiguation, and the items inherit
the crate's source. Followed until nothing new appears (bounded by
`ingest._FOLLOW_ROUNDS`), because a crate can name a disambiguation.

*Legendary set pieces drop off group-instance and open-world nameds; fabled off
raid zones — game knowledge, from Lindsay. The turquoise moves between them,
which is why the adornment and the armour are separate rows (below).*

### Running it unattended (2026-08-16)

The crawl is now **monthly on cron** (`scripts/scheduled-sync.sh planner`),
which replaces "hand-run, never scheduled" for the *planner* crawl. The ability
ingest (`sync_wiki.py`) is unchanged and stays hand-run.

The rule existed for a real reason — `store` RECONCILES, and reconciling means
deleting what the wiki no longer says, which is safe exactly as long as a
person is watching. So the protection moved from the habit into the code:
**`ingest.CrawlCollapsed` refuses to write a crawl that came back under
`COLLAPSE_RATIO` (60%) of the last one**, and a crawl returning nothing where
there was something is always refused. A real itemization change never halves
an expansion; a rate limit or an hour of Fandom being unhappy always does. The
tool exits 2, the log says so, and the previous catalog keeps being served.
`--force` is the operator's override for a drop that is genuinely real.

**Census gets picked back up automatically.** `scripts/scheduled-sync.sh census`
runs every 30 minutes, probes with one real query (Census answers HTTP 200 with
an `error` body when it is unavailable, so a status code proves nothing), and
only when it answers runs the two backfills that were waiting on it — item
resolution and the roster. A down probe is a quiet no-op and never an alert,
because **Census intermittency is normal and is not an outage**.

### Loading a character without an account

`GET /api/plan/character?name=` is the **one route on this page that may reach
the network**, and it is the exception the app already makes for
`POST /characters/{id}/census/refresh`: it runs on a name a reader TYPED and
submitted, never on a page load. A Census character record is public, and
trying gear on your own toon should not be the one part of a signed-out page
that demands an account.

It is cache-first (`plan_characters`, v43, TTL 6h) and falls back to a stale
answer when Census is unreachable, so the panel keeps working through an
outage. **The cache is a cache of a public record, not a character**: no
`user_id`, no snapshots, no history, nothing anybody owns — `characters`
remains the only owned thing and this table could be dropped without losing
anything a person typed. The miss is cached too, so a typo is not re-asked.
`census.sync._summary_of` builds the answer for both this and an owned
character, because two builders would drift and the difference would only show
on the path nobody is signed in for.

### The steps

1. **Crawl** wiki categories for the era: quests, named monsters (per zone and
   per expansion), each zone's dropped items, timelines, equipment. RoK has
   500+ quests and 418 named monsters over 29 zones.
2. **Parse templates** into layer-1 fields. Batched at 50 titles per request;
   the whole equipment corpus is on the order of hundreds of requests.
3. **Extract steps and coordinates** with the wikq2 TS extractors, emitting JSON.
4. **Match waypoints to client POIs** with wikq2's production
   `attachEq2MapMatches`, keep the confidence on every row.
5. **Cluster** and score; write `plan_clusters`.
6. **Nominate layer-2 claims** with their sentences into `plan_nominations`.
7. **Curator confirms** in the admin console. Only confirmed claims reach the
   page.

Steps 1–6 are a script. Step 7 is a person, and that is the design.

---

## Phases

RoK is roughly four weeks out. Phase 1 is what has to exist on day one; 3 and 4
are explicitly after launch.

**Phase 0 — the decision gate. COMPLETE (2026-08-15).** The whole 899-quest RoK
catalog produced 3,452 coordinates; 2,584 matched a client POI. Coverage is
74.86% overall / 83.46% of zone-labeled coordinates, the five main RoK zones
are 90.0–94.5%, and median confidence is 0.98. Phase 3 may proceed
conservatively over matched rows. No cluster is invented for the 356 remaining
unzoned coordinates; improving those cross-zone pages is a wikq2 concern and
still constrains Phase 4's epic group view.

**Phase 1 — the catalog, search and loadout. COMPLETE (2026-08-15).**
`plan_items`, `plan_sources`, the priority control, item table, `ItemCard`
reuse, set-adornment view, concrete equipment slots, optional Census current
gear and candidate cycling, inline installed-adorn sockets and hover data,
additive stat projection, socket assignment, level-range search and live set
thresholds. It remains useful with no outline and with no account.

**The gear window's own rules** (Lindsay's read of the built page, 2026-08-16):

- **Every changed slot carries its own reset.** Cycling could already reach the
  equipped item, but only by walking past every candidate on the list — and
  undoing one change is a far commoner move than comparing five rings. The
  button appears ONLY on a slot that has been changed; a reset beside twenty
  untouched slots is twenty buttons that do nothing. It clears that slot's
  `active` entry and its installed set adornment, and leaves the shortlist
  alone: those are candidates you found, and finding them again is the work.
- **WORN SET BONUSES LIVE UNDER THE EQUIPPED GEAR** (`PlanLoadout.WornSets`,
  the way eq2lexicon does it), not in the projected-stats panel where they
  started. The count changes with every adornment click, so the fourth piece
  lighting up has to be visible in the same glance as the click that made it
  the fourth. It counts what is IN THE WINDOW from two sources — a set the
  reader installed (`set_slots`) and a turquoise the character already wears,
  whose tiers come from Census's own `setbonus_list` (`items._adornment`) —
  and the old panel could see only the first. **Same-named adornments are the
  same set**: that is what a set adornment is in EoF/RoK and it is the only
  join either source offers. Earned tiers are full-strength with a filled
  diamond, unreached ones dimmed. Their arithmetic contribution stays in
  `projection()`, because that is a stat like any other.
- **Changing who you are planning for empties the window.** A planned choice
  only means anything against one character's current equipment — a ring worth
  +40 Ability Mod on the fury is a downgrade on the guardian — and leaving the
  projection populated across a switch showed an "upgrade" measured against
  somebody else's gear. Both routes in (the account picker and the typed
  lookup) clear it. The shortlist survives.
- **THE CHARACTER IS THE HEADLINE OF THE GEAR CARD.** It was the other way
  round — "EQUIPMENT & STATS" in the site's gold display caps, with the toon's
  name in small muted type trailing after it — so the loudest words on the page
  named the panel and the one fact that changes, who this is, was set in the
  quietest type on it. Now the panel's name is the eyebrow label and the
  character's name is the `h2`. **That heading opts out of the site's
  small-caps `h2`**: `BOBBY` is not how anybody writes a name.
- **The lookup is a way IN, not the headline.** It sits to the LEFT of the
  picker and stays small: who you are planning for is what the row exists to
  say, and that is the picker plus the name in the heading. A 15rem search box
  beside a 220px gold picker read as the more important of the two and made the
  head the loudest thing on the page.

**Phase 2 — the outline.** Prelude from layer 3, body from the prerequisite DAG,
no tags. Ordered, readable, and honest about what it does not know.

**Phase 3 — clusters and tags.** The lens.

**Phase 4 — multi-class epics.** The group view and the shared-update flags.

---

## Risks and open questions

- **The waypoint match rate is measured, not universal.** Phase 0 found 74.86%
  overall / 83.46% of zone-labeled coordinates and 90.0–94.5% in the five main
  RoK zones. Phase 3 uses matched rows only; the remaining gaps do not become
  inferred spatial claims.
- **The old three-quest `pending` artifact from 2026-06-07 is superseded.** The
  reproducible hand-run audit is `backend/tools/planner_phase0.py`; its ignored
  runtime artifact is `data/planner-waypoints-rok.json` and carries schema
  version, source sync, every resolved quest, and the full distribution.
- **Layer-2 volume.** If the prose pass nominates thousands of claims, curation
  becomes the bottleneck and the console needs to be good. Cap the first run to
  the RoK timelines and the 24 epic questlines and see what the rate looks like.
- **TLE itemization has not been spot-checked.** The wiki mirrors a 2022 Census
  scrape. The stat vocabulary matches what the game shows today, which is
  reassuring but not proof. **Verify a half-dozen examine windows against the
  catalog on day one** before trusting it wholesale.
- **What "era" means for an item** is less crisp than for a zone. `zone_eras.json`
  is authoritative for zones; item era has to be inferred from the source, which
  is one more reason source attribution is built by inverting mob and quest pages
  rather than trusting a field.
- **The epic pages are the most valuable and the most drifted.** Trust their
  graph, discard their strategy commentary, expect to correct levels by hand.

---

## State of play — 2026-08-15 (Phases 0–2 complete)

**Phases 1 and 2 are BUILT and both expansions are synced; Phase 0 is now
MEASURED.** The gear workspace builds a slot-aware loadout and its contextual
Outline column consumes the same plan as a hand-kept prelude followed by the prerequisite DAG. Phase 3
cluster tags may now proceed over the matched coordinate corpus. Phase 4 remains
planned and must not pretend unresolved cross-zone epic coordinates are located.

### What exists

| Piece | Where |
| --- | --- |
| Template parsing (`EquipInformation`, `NamedInformation`, `QuestInformation`, `AdornmentSet`), prerequisite OR-groups, class-template expansion, era caps | `backend/planner/wiki.py` |
| The crawl: invert mobs and quests, follow disambiguations, reconcile quests and edges per era | `backend/planner/ingest.py` |
| The read side: era filter, priority scoring, typed additive set bonuses, the set view, the examine card adapter | `backend/planner/catalog.py` |
| The outline read side: layer-3 prelude, prerequisite walk, stable topological order | `backend/planner/outline.py`, `backend/refdata/planner_standard.json` |
| `GET /api/plan/meta` `/items` `/sets` `/outline` — no account, no POST | `backend/routers/planner_api.py` |
| `plan_items`, `plan_sources`, `plan_sets`, `plan_quests`, `plan_quest_edges`, `plan_syncs` (schema v42) | `backend/db.py` |
| The page: game-grouped concrete slots, candidate cycling, per-slot reset, projected stats, worn set bonuses under the window, three numbered priority dropdowns beside labelled facets, checkbox source filter, click-the-row-to-equip, inline socket icons, level/name filters, richer item/adorn hovers, item/set views and ordered outline | `frontend/src/pages/Planner.jsx`, `components/PlanLoadout.jsx`, `components/PlanOutline.jsx` |
| The hand-run sync | `backend/tools/sync_planner.py` |
| The resumable Phase 0 audit | `backend/tools/planner_phase0.py`, `backend/planner/waypoint_audit.py` |
| Planner tests including recorded wiki pages, set-bonus typing, isolated graph shapes, and audit resume/coverage; no network | `backend/tests/test_planner.py`, `backend/tests/test_planner_waypoint_audit.py` |

### What the latest full sync found (2026-08-15)

| | RoK | EoF |
| --- | --- | --- |
| Named monsters / quests crawled | 345 / 900 | 382 / 513 |
| Items | 2,881 | 2,487 |
| raid / group / solo / quest sources | 647 / 391 / 422 / 1,548 | 516 / 783 / 94 / 1,044 |
| Adornment sets | 20 | 49 |
| Prerequisite edges | 594 | 314 |
| Prerequisite links outside the catalog | 20 | 19 |
| Dropped above the era's level cap | 49 | 8 |
| Wiki pages read | 5,421 | 4,610 |

11 items across both eras have no Census id, so they list and score but cannot
open an examine card. 1,398 item icons are cached.

**THE LEVEL CAP IS THE ONE DEFENCE AGAINST LIVE-ERA DRIFT, and it was needed.**
The first crawl pulled `Archon's Boots` — level 100, 3,632 Ability Mod — out of
a RoK quest page whose rewards had been rewritten for a live revamp. One row
like that does not sit harmlessly in the catalog: it becomes the largest value
the scorer has ever seen, and every real RoK drop then scored about 2 out of
100 against an item nobody on this server can wear. `wiki.ERA_CAP` (EoF 70, RoK
80) drops the SOURCE, not the item, because the same item may still have an
honest source in the other era.

This is NOT the quest-level rule and must not be confused with it: a journal
level above the cap is normal and is a tag, because a yellow quest pays better.
An item's level is what you must BE to equip it, so above the cap it is not
hard, it is impossible.

### Corrections that landed after the first build

**Potency and crit were the opening default and should never have been.**
Lindsay: they are on every item, so they are not something to prioritize on.
The catalog agreed once it existed — 80% and 72%. They came out of the priority
options entirely (`wiki.PRIORITY_STATS`, enforced in `catalog.weights`). The
draggable track opens with Ability Mod, Casting Speed and Reuse Speed at its
numbered left edge, and the Requirements panel says in one line why potency and
crit are missing from the track.

**`hategain` was not being parsed at all.** A real `EquipInformation` field the
first pass missed; 11 items across both expansions carry it, and a tank wanting
it wants to know which 11. It is a percentage despite the template rendering no
sign — the values are 0.9 to 2.

**`aspeed` is labelled Haste**, which is the game's word and the template's own
short form.

**The table was full of items carrying one priority stat, or none.** Lindsay:
EQ2 items are four-stat right now, so prioritizing three needs to show items
with two of the three. That is the match floor above — the single biggest
improvement to what the table actually shows.

**Armour weight was missing entirely** — it was sitting unused inside `dtype`,
hidden behind a default-off "Type" column. It is now a visible column and a
facet.

**Two-handed weapons read as one-handed.** Same field, same fix: the slot cell
now says `Primary/2H`.

**The planner table's headers were unreadable.** Muted small caps at
`--fs-label` over a dozen columns of figures. Raised to `--text` at `--fs-sm`
with less letter-spacing, scoped to `.plantable`: the same argument applies to
`table.data` site-wide, and every parse view draws through that rule, so it was
not changed underneath them without asking.

### Decisions taken during the build

- **The era filter reads `plan_sources.era`, not `plan_items.era`.** An item
  introduced in EoF that also drops off a RoK named IS RoK content for somebody
  planning RoK. `plan_items.era` is the expansion it was introduced in — a
  different fact, displayed rather than filtered on.
- **A disambiguation resolves to EVERY version**, not the first. `Focused Mind
  Slippers` is two real items at two levels and the catalog wants both.
  `items.py` takes the first version instead, because there it is resolving one
  logged drop rather than building a catalog.
- **Scores are normalised against the whole selected-era catalog, not the
  filtered view.** Narrowing to one slot must not silently rescore every row —
  a score is only useful if it means the same thing after you press a filter.
  The cost is that a slot-limited view tops out below 100, which is honest:
  boots really do carry a third of a two-hander's ability mod.
- **The examine card is built server-side in `items.display`'s shape**
  (`catalog.card`), so `components/ItemCard.jsx` is reused unchanged. There are
  now three ways to meet an item and all three open the same window.
- **A re-sync RECONCILES its era** — upsert, then delete that era's sources the
  crawl did not produce, then delete items left with no source. A drop removed
  from a mob page has to be able to leave; the other era's rows must not.
- **`sync_planner.py` deliberately does not set `CENSUS_AUTO_REFRESH=0`** the
  way `sync_wiki.py` does. That switch is `items.network_allowed`, which gates
  the icon downloads too, and with it set the icon pass silently caches
  nothing. Nothing in this ingest calls Census.

### Still not built

- **Layer 2 and `plan_nominations`.** `effectlist` is stored as written and
  shown as a badge; nothing classifies a proc and no curator console exists.
- **Phases 3–4** — clusters, tags, multi-class. Phase 3 is now unblocked for
  matched coordinates. Phase 4 still needs better cross-zone epic coverage.

### What Phase 0 measured (2026-08-15)

| Measure | Result |
| --- | ---: |
| RoK quests resolved / errors | 899 / 0 |
| Extracted coordinates | 3,452 |
| Zone-labeled / still unzoned | 3,096 / 356 |
| POI matched | 2,584 |
| Match rate, all / zone-labeled | 74.86% / 83.46% |
| Median confidence / median 2D distance | 0.98 / 2.56m |
| Matches at confidence ≥ 0.95 | 2,213 |

The initial run exposed 1,168 coordinates without a zone. The fix landed in
wikq2 rather than here: parser v89 applies a page-wide zone only when the
quest's structured Starting Zone agrees with its declared zone Timeline. That
reduced the gap to 356 and raised matches from 1,874 to 2,584 without assigning
an epic's later steps to the city where its first step happens to begin.

### Verified on 2026-08-15 (re-verify before asserting any of it as current)

| Checked | Result |
| --- | --- |
| Census, all namespaces, real service id | `service_unavailable`. **Normal** — it comes and goes by time of day, per Lindsay. Not an outage, not a blocker, never blocked on. |
| `eq2.fandom.com` MediaWiki API | Up. Templates parse cleanly; 500+ RoK quests, 345 RoK named monsters, 9 RoK timelines catalogued. |
| CirrusSearch `insource:` | **Still not enabled.** Two forms tried, both empty. The gear-proc wontfix stands. |
| `u.eq2wire.com` | Up (200) — not used by this design, noted as a fallback. |
| Fandom S3 XML dump | 403. Bulk ingest is the API, batched — hundreds of requests, not tens of thousands. |

### Assets that already exist

- `/home/lindsay/wikq2/data/eq2map/` — 46,426 POIs, 2,001 maps, 1,222 map links,
  a 3,759-node travel graph. **Client-extracted, not wiki-scraped.** Generated
  2026-06-07; regenerate before trusting.
- `/home/lindsay/wikq2/lib/` — the extractor set in the table above, notably
  `detectStepsAnyOrder` (`eq2.ts:1332`).
- `/home/lindsay/wikq2/lib/eq2map.ts` — the production matcher used by exact
  quest lookup and the Phase 0 sweep.
- `backend/items.py:648` — set-adornment resolution, already working, reused
  as-is.
- `backend/refdata/zone_eras.json`, `ERA_HIDDEN` — era filtering, reused as-is.
- `components/ItemCard.jsx`, `SortableTable.jsx`, `Picker.jsx`, `Tabs.jsx` —
  reused as-is.

### The immediate next action

Two, and they are independent.

**Spot-check the catalog against the game (day one, before trusting it
wholesale).** The wiki mirrors a 2022 Census scrape and TLE itemization has
still never been checked against a real examine window. Open half a dozen RoK
drops in game and compare them to `/plan`. The level cap caught the gross
drift; this is the check for the quiet kind.

**Phase 3 ingest and cluster scoring.** Consume only the matched coordinates
from `data/planner-waypoints-rok.json`, retain confidence on every waypoint,
exclude prerequisite-related quest pairs, score clusters rather than pairs,
and expose tags as a highlighting lens over the stable outline. The Phase 0
gate now has a measured denominator; do not widen it by guessing locations for
the 356 unresolved coordinates.

### Settled — do not relitigate

These were each argued and decided in the design conversation; the reasoning is
in the sections above.

- **No set optimizer and no cap math.** Twice stated. The tool presents options;
  the reader chooses.
- **Stat priority is an order, not numbers.** No sliders. Three dropdowns
  numbered 1–3, defaulting to Any; the number of boxes IS the boundary, so
  there is no "score top" control.
- **Rows carrying ALL your stats lead the table, then the partial ones.** A
  tier, not a filter — the four-stat floor is unchanged and still applies.
- **The outline never reorders. Tags highlight, never filter.**
- **Tags do not use the class palette**, and only the top eight get a color.
- **The extractors stay in TypeScript and run offline.** The wiki parser is not
  ported to Python and Node does not run in the API process. (Phase 1 needed
  none of them — the catalog is templates, and templates parse in Python. The
  boundary matters from Phase 3, where the STEP extraction lives.)
- **A quest level above the era cap is a tag, not a drift signal** — and an
  ITEM above it is dropped. Different facts, opposite handling.
- **Which expansions count is the reader's**, EoF and/or RoK, and era is a
  column rather than a constant.
