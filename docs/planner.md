# The Planner — gear targets and a leveling outline

A page that answers two questions for one character on one expansion:

- **What should I be chasing?** Given the stats you are pushing, which drops,
  quest rewards and set adornments in this era are worth your time — and where do
  they come from.
- **What should I be doing?** Given what you picked, an ordered outline of the
  quests and targets that get you there, with the expansion's standard work
  hoisted to the front and the trips that overlap painted on top.

**Which expansions count is the READER's choice** — a set of toggles at the top
of the rail, not a build-time constant. EoF and RoK are what exist today,
either or both; RoK is the priority and the default. A third expansion is a
re-sync and one entry in `wiki.ERAS`, never a migration.

**It is a route (`/plan`) with no nav entry**, like `/characters`. Adding the tab
is the whole publish step; nothing else changes.

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
| `attachEq2MapMatches` | `eq2map.ts` | Wiki coordinate → client POI |
| `proximityDedup`, `dedupeIdenticalCoordinates` | `eq2.ts` | First cut at waypoint noise |
| `collectLocationListRows` | `eq2.ts` | Location tables |
| `findKnownAlias`, `pickDisambiguationEntry` | `eq2.ts` | Link resolution across pages |
| `wikiFetch`, `cached` | `wiki-fetch.ts`, `cache.ts` | Polite fetching and on-disk cache |
| `data/eq2map/*` | — | The 46k POI corpus, maps, map-links, travel graph |
| `scripts/match-quest-waypoints-eq2map.js` | — | The matcher itself |

**The extractors stay in TypeScript and run OFFLINE; eq2advanced ingests their
JSON.** wikq2 is Next.js/TS and this app is FastAPI/Python, and neither porting
2,663 lines of wiki parser nor running Node inside the API process is worth it.
The boundary is a versioned JSON artifact produced by a hand-run script — which
is precisely the `zone_eras.json` pattern already in the repo, and it means the
ingest inherits wikq2's fixes rather than forking them.

**The waypoint matcher is proven on three quests and has never been run at
scale.** `quest-waypoint-poi-matches.pending.json` is a proof of concept from
2026-06-07. It works: *From Hands of Stone*, step 0 at `(-192, 33, 294)` in The
Living Tombs matched client POI "Gargoyle" (`quest-start`) at **6.32m 2D,
confidence 0.98**. Whether that holds across 500+ RoK quests is unknown and is
**Phase 0** below.

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
the third decimal place means something. The priority editor is a drag-to-reorder
list.

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
level range, tier (`icat`), era, and source kind (raid / group / solo / quest /
crafted).

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

Two regions, permanently: a **shortlist rail** on the left and a **tabbed main
area**. This is `ZoneRun`'s rail-plus-main geometry and reuses its layout CSS
and its `.workspace` row rules.

```
┌──────────────┬────────────────────────────────────────────────┐
│  SHORTLIST   │  [ Gear ]  [ Outline ]                          │
│              │                                                 │
│  Necromancer │  ── priority: abmod › reuse › cast ──  [edit]   │
│  RoK · 80    │                                                 │
│              │  filters: slot ▾  tier ▾  source ▾  class ▾     │
│  ▸ Feet      │                                                 │
│    Focused…  │  ┌───────────────────────────────────────────┐ │
│  ▸ Adorns    │  │ item table (SortableTable, frozen)        │ │
│    Mist Cov… │  │                                           │ │
│  ▸ Targets   │  └───────────────────────────────────────────┘ │
│    Chardok   │                                                 │
└──────────────┴────────────────────────────────────────────────┘
```

**The rail is the bridge between the tabs.** You fill it on Gear and consume it
on Outline. It is always visible so the Outline is never a thing you generated
and lost — it is a live view of what is in the rail.

**The rail holds THREE kinds of thing, and they are listed separately**: items,
adornments, and targets (a mob or a quest you want for its own sake). They are
separate because a turquoise is not its host item and a raid target is not a
slot.

### The priority editor

The most novel control on the page, and the easiest to get wrong.

**Drag-to-reorder list, no numbers, no sliders.** The header shows the current
order inline as `abmod › reuse › cast` and `[edit]` opens the list. Stats not in
the list are not scored — absence is a statement.

**A stat can be marked *required*, which moves it from ranking to filtering.**
One toggle per row, visually distinct from the ordering handle. This covers "I
will not look at anything without ability mod" without pretending a weight can
express a hard requirement.

**A separate "interested in procs" switch**, because that is a different axis
from any stat order and the reader said so in those words. On, the table's proc
badge column sorts to relevance; off, it is decoration.

The editor is a `Picker`-style panel rendered into `document.body`. **Never
`<select>`** — house rule, and the backdrop-filter stacking trap applies here as
everywhere.

### The item table

One `SortableTable`, frozen, with the house column-preference behavior
(`eq2adv:cols:planner`). Columns: name, tier, level, score, the reader's
priority stats as their own columns, source, and two badge columns.

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

A class multi-select in the rail header. With one class the page is personal;
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

- **Empty shortlist** — the Outline tab shows the prelude only, which is
  correct and useful on its own: the expansion's standard work does not depend
  on what you are chasing.
- **No clusters found** — the tag strip is absent, not empty. An empty legend
  reads as broken.
- **Unresolved item** — a name, no card. `GET /api/items/{id}/card` answers
  `null` for an unresolved id and must not trigger resolution; **`items.ensure`
  is network-bound and never runs in a request handler.**
- **Census dark** — the page works. Anything Census-only degrades to the wiki
  value with no error surface; intermittency is normal, not a fault.
- **Narrow viewport** — the rail collapses to a summary bar above the main area
  at the same breakpoint the top nav wraps (900px). The outline stays a single
  column at every width; the item table scrolls sideways inside its wrapper.

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

1. **Crawl** wiki categories for the era: quests, named monsters, timelines,
   equipment. RoK has 500+ quests and 345 named monsters catalogued.
2. **Parse templates** into layer-1 fields. Batched at 50 titles per request;
   the whole equipment corpus is on the order of hundreds of requests.
3. **Extract steps and coordinates** with the wikq2 TS extractors, emitting JSON.
4. **Match waypoints to client POIs** (`match-quest-waypoints-eq2map.js`), keep
   the confidence on every row.
5. **Cluster** and score; write `plan_clusters`.
6. **Nominate layer-2 claims** with their sentences into `plan_nominations`.
7. **Curator confirms** in the admin console. Only confirmed claims reach the
   page.

Steps 1–6 are a script. Step 7 is a person, and that is the design.

---

## Phases

RoK is roughly four weeks out. Phase 1 is what has to exist on day one; 3 and 4
are explicitly after launch.

**Phase 0 — the decision gate.** Run the waypoint matcher across the RoK quest
set and look at the match rate and confidence distribution. Everything spatial
depends on this number and it is currently unknown. If it holds near the 0.98 of
the three-quest sample, clusters are nearly free. If it does not, the outline
falls back to zone-level grouping, which is still useful — but we should know
before designing around it.

**Phase 1 — the catalog and the search.** `plan_items`, `plan_sources`, the
priority editor, the item table, `ItemCard` reuse, the set-adornment view, the
shortlist rail. **This is the launch-critical piece** and it is useful with no
outline at all.

**Phase 2 — the outline.** Prelude from layer 3, body from the prerequisite DAG,
no tags. Ordered, readable, and honest about what it does not know.

**Phase 3 — clusters and tags.** The lens.

**Phase 4 — multi-class epics.** The group view and the shared-update flags.

---

## Risks and open questions

- **The waypoint match rate is unknown.** Phase 0. Everything spatial rests on
  it, and the existing evidence is three quests.
- **The `pending` map artifacts are from 2026-06-07** and predate whatever has
  changed in wikq2 since. Regenerate before trusting them.
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

## State of play — 2026-08-15 (Phase 2 built)

**Phases 1 and 2 are BUILT and both expansions are synced.** The Gear tab builds
the shortlist; the Outline tab consumes it as a hand-kept prelude followed by
the prerequisite DAG. Phase 0 is still unrun, so Phases 3–4 — cluster tags and
the multi-class epic view — remain planned.

### What exists

| Piece | Where |
| --- | --- |
| Template parsing (`EquipInformation`, `NamedInformation`, `QuestInformation`, `AdornmentSet`), prerequisite OR-groups, class-template expansion, era caps | `backend/planner/wiki.py` |
| The crawl: invert mobs and quests, follow disambiguations, reconcile quests and edges per era | `backend/planner/ingest.py` |
| The read side: era filter, priority scoring, the set view, the examine card adapter | `backend/planner/catalog.py` |
| The outline read side: layer-3 prelude, prerequisite walk, stable topological order | `backend/planner/outline.py`, `backend/refdata/planner_standard.json` |
| `GET /api/plan/meta` `/items` `/sets` `/outline` — no account, no POST | `backend/routers/planner_api.py` |
| `plan_items`, `plan_sources`, `plan_sets`, `plan_quests`, `plan_quest_edges`, `plan_syncs` (schema v42) | `backend/db.py` |
| The page: Gear/Outline tabs, filters, item/set views, persistent three-kind shortlist, ordered outline | `frontend/src/pages/Planner.jsx`, `components/PlanOutline.jsx`, `components/PriorityEditor.jsx` |
| The hand-run sync | `backend/tools/sync_planner.py` |
| 39 planner tests, including recorded wiki pages and isolated graph shapes; no network | `backend/tests/test_planner.py` |

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
options entirely (`wiki.PRIORITY_STATS`, enforced in `catalog.weights`), the
page now opens on Ability Mod › Casting Speed › Reuse Speed, and the editor
says in one line why they are missing.

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

- **Phase 0.** The waypoint matcher has not been run at scale, so no spatial
  claim is made by the current outline.
- **Layer 2 and `plan_nominations`.** `effectlist` is stored as written and
  shown as a badge; nothing classifies a proc and no curator console exists.
- **Phases 3–4** — clusters, tags, multi-class — unchanged and still gated on
  Phase 0.

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
- `/home/lindsay/wikq2/scripts/match-quest-waypoints-eq2map.js` — the matcher.
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

**Phase 0**, unchanged and still unrun. Run the waypoint matcher across the RoK
quest set; report match rate and confidence distribution. The only evidence
today is a three-quest proof from 2026-06-07 whose best sample matched at 6.32m
2D / 0.98 confidence. Phases 3 and 4 are designed against a number nobody has
measured. Nothing in Phase 1 or 2 depends on it.

### Settled — do not relitigate

These were each argued and decided in the design conversation; the reasoning is
in the sections above.

- **No set optimizer and no cap math.** Twice stated. The tool presents options;
  the reader chooses.
- **Stat priority is an order, not numbers.** No sliders.
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
