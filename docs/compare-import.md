# eq2advanced — Compare and screenshot import

Part of the architecture reference. Index: `ARCHITECTURE.md`.

## The Compare page — any parses, side by side

`/compare` (`frontend/src/pages/Compare.jsx`, in the nav, signed-out too) puts
N parses side by side: a column is `(zone run, fight selection, subject)`
where the subject is the whole raid or one player. Same player on two nights, two players on one boss,
raid against raid — one surface. It absorbed the old `RaidParseCompare` modal
(raid columns only, unshareable); `ComparePanel` on the raid page stays,
because "these raiders, this run" is already loaded there and needs no picker
— but it renders the same way this page does.

**A column is the ACTUAL parse, not a metric rollup.** How people compare in
practice is a screenshot of their ACT window lined up against somebody
else's, so a player column is their ability breakdown and a raid column is
the zone page's parse list (same columns, same rank coloring). A first
version rendered metrics as rows (DPS, Crit %, … with a ▲ on the leader) and
was replaced — nobody compares "Crit % rows", they compare parses. The
breakdown itself is one component, `BreakdownTable.jsx`, extracted from
`ActorPanel` and shared by the drilldown, the raid page's `ComparePanel` and
this page, so a parse looks identical everywhere it appears; comparison
surfaces pass `defaultHidden` (Share, ToHit, Median, MinHit) and the
`SortableTable` Columns menu brings those back — see "A default-hidden column
is a baseline" below for why that survives the reader touching the menu.
Tables sharing a `prefsKey`
sync layout changes live (an in-module listener set — localStorage's own
event only fires cross-tab), which is what keeps side-by-side columns lined
up while you rearrange them.

**The parses SCROLL TOGETHER, and each one is a frozen table.** Move one
column's horizontal scrollbar and every parse beside it moves with it
(`SortableTable`'s `syncScroll` group — `compare` on this page, `cmppanel` in
the raid page's `ComparePanel`, and `compareraid` for raid columns, whose
columns are a different set and would line up with nothing). A comparison is
read ACROSS: the whole job is having the same stat under the same stat, and
doing that by dragging two scrollbars to the same place is the work this page
exists to remove. A parse added mid-comparison lands where the others already
are. The ability name and the header row stay pinned inside each column on
top of that — see "A parse table is FROZEN" in `docs/zoneruns.md`.

**A column carries its own kind tabs**, the same `KIND_FILTERS` set the
drilldown offers (Damage / Heals / Power / Threat / Cures / Self) and only the
ones that parse has rows for (`availKinds`) — a fury's column has no Threat tab
to click. A raid column offers the two the parse list is written for, Damage and
Heals. This replaced a page-wide Damage|Healing tab pair above the columns,
which existed on the argument that comparing one column's damage to another's
heals is not a comparison: true, and still the reader's call rather than the
page's. The tab lives in component state, NOT in `?c` — the token says what the
comparison is OF, a tab is how you are looking at it — and removing a column
takes its tab with it (they are held by position, so a survivor must not
inherit it). A screenshot column has no tabs at all: an image is of one view.
Beyond the tabs, a column is built like the drilldown on purpose — name, class
chip, ✕, controls, tabs, then the composition strip (`CompositionStrip`, shared
with `ActorPanel`) or a raid column's `ParseStrip`, one compact line of headline
numbers standing in for ACT's title bar.

**The URL is the comparison.** One query param `c`, a CSV of
`<runId>:<sel>:<subject>` tokens, where `sel` is `all` or fight ids joined by
`.` — not `+`, which `URLSearchParams` reads as a space — and `subject` is
`raid` or a player name (EQ2 names are single-word alphanumeric, so the
delimiters can't collide). Malformed tokens are dropped, never crashed on.
Everything on the page — add, remove, flip a fight or a subject — rewrites `c`,
so a pasted link reproduces the whole comparison.

**Every number comes from `/encounters/agg`** — per-encounter authorized,
memoized, client-cached — and never from the run report, whose rows are frozen
whole-run and would silently mismatch a per-fight selection. Cross-parse
identity is BY NAME (entity ids are session-scoped). A raid column sums its
raiders — right for the rates too, every raider's rate runs over the same
fight clock — and crit/auto/proc/casts aggregate by summing the per-player
`damageDerived` rollups. Columns fetch and fail INDEPENDENTLY: a run the
viewer can't see renders "not visible to you" in that column, and the rest of
the comparison stands, which is what makes the links safe to share.

**Getting there is one click from any parse**: a Compare chip in the raid
page's title block — the fight rail's head (carrying the page's current fight
selection) — and one in the
player drilldown header (`ActorPanel compareTo`, players only — comparing a
mob across nights isn't a thing). Both land with one column loaded, and the
placeholder slot beside it says where the next one goes.

**The picker is one faceted live search, computed in the browser.** The first
version was a flat `<select>` over every visible run plus a separate two-step
player search — two controls that could not narrow each other, and a dropdown
that grows to three hundred options is not a picker. It is now a search box over
Zone / Named mob / Date / Guild / Player dropdowns: typing `freeth` surfaces
*Freethinker Hideout* nights AND Freethinkers-guild nights, because zones,
guilds and mob names match anywhere in the string (people type them from the
middle) while roster names match from the front (people type those from the
start).

It is client-side because the page **already fetches the whole visible list**,
one row per NIGHT with the same yours-then-primary rule as the raid list.
`?roster=1` (`list_zone_runs`) adds each night's names AND its named mobs with
the encounter ids that are each fight, parsed server-side so the client never
learns the storage format; ~300 nights × ~24 names is about 100 KB, smaller than
one `/encounters/agg` answer the page will fetch anyway. That buys zero debounce,
zero new endpoints, and instant cross-narrowing: **each dropdown's options are
computed from the nights matching every OTHER facet**, so no combination of
choices can strand you on an empty list. The Guild dropdown is not rendered at
all when nothing visible carries a tag — a fresh backfill degrades by the
control not existing yet, not by an empty select. Named mobs follow the fight
rail's hiding rule (`_named_for_runs`): a hidden pull is still its owner's and
is not a boss anyone else can search for.

**The search is a BAND across the top, and one click on a result IS the add.**
The picker used to be a 300px card holding the left edge with the parses stacked
to its right — better than trailing them, which walked the control further right
with every raid added, but still a third of every row spent on something you
have already used. It is now a card across the top of the page — the facets on
one line, the full width underneath for the parses — with a search field
carrying its own magnifier, then Zone / Named / Date / Guild / Player. Each
dropdown is named for what it holds rather than for the rows it would leave
alone ("Zone", not "Any zone": a facet is off when it reads its own name), and
Guild and Player put YOUR guild and YOUR characters at the top marked `(You)`,
read off the `mine` flag the list already carries — hunting for your own name
among three hundred alphabetical ones is the picker failing at its one job.

A result click lands the column already scoped to what the search was about —
the named mob's fights if one is picked (all of them: a raid pulls a boss twice
often enough that both belong), and the person if the Player facet or the anchor
column names one, spelled the way the roster spells them. The old two-step
(select a night, then pick a subject in a confirm strip, then press Add) is
gone: the column's own two dropdowns fix whatever the click got wrong, which is
the same control in the place you are already looking.

**Every dropdown on the page is `Picker`, not `<select>`.** A native select
costs three things this page cannot spend. Its popup is OS chrome —
`color-scheme` gets it dark and that is the end of what this stylesheet may say
about it, so the surface a reader opens most often is the one surface that
looks like nothing else here. An `<option>` is a string, so a raider can be a
name or a class but not both. And a closed select is as wide as its widest
option, which is how one 24-name roster came to set the width of a control
reading `Bobby`, and how a fight label (mob name plus a clock) pushed the
subject picker to the far side of a 380px column. `Picker.jsx` splits those
apart: the BUTTON is sized by the row it sits in and truncates, the PANEL is
sized by its content. Rows carry an icon and a muted hint, so a player row is a
class dot, a name and the class spelled out; sections are optgroups; past ten
rows the panel grows a filter, because a roster is a list you search.

The open panel is rendered into `document.body` and positioned from the
button's rect. That is not a preference — **every `.card` here carries
`backdrop-filter`, which makes it a stacking context AND a containing block for
`position: fixed`**, so a menu written inside a card is sealed into that card's
box and painted under every later sibling however high its z-index goes. The
search band is a card and the parse columns are cards after it, so facet menus
dropped down *behind* the parses. The same trap put the screenshot viewer under
the next column. Leaving the card is the fix; a bigger z-index cannot be one.

**A row's own parts are targets too.** A night found by a mob name is not really
an answer of "this raid" — searching `saw` and getting *The Emerald Halls* means
the pull, not the night — so the matching named mobs sit under the row as chips
and go straight to that fight, as does any raider whose name the query matched.
A chip carries a MARK, not just a tint: a skull for a pull, a head for a person.
They are the one place on the page where the two kinds of target sit in one
strip, and telling them apart by color alone is the mistake the class chips are
careful not to make. The chips hang off a vertical rule descending from their
row, and each result is ruled off from the next — a dozen results in a
three-column grid, each two or three lines with a strip of chips under it, ran
together into a block where one row's chips read as the next row's subtitle.
With no question about mobs asked, the night's named mobs are offered anyway
(capped): going straight to a boss is the common move. Raiders are not offered
that way — twenty-four names under every row is a roster, not a shortlist — and
the chips compose with the facets rather than replacing them, so a mob chip
keeps the player the search is about and a person chip keeps the pull.

**Results appear only once something has been asked, and the empty slot is the
drop box.** Twelve recent raids sitting there on arrival read as the page's
content, when the content is the parses underneath — so what speaks in the
meantime is the last column: a `ShotDrop` styled as a parse column (`.dropslot`,
a + inside a heavy dashed border) captioned *Search or add a screenshot to
compare…*. It says where the next parse lands AND takes one, which is why there
is no second placeholder and no drop target up in the search band: those were
two objects making one statement. It stays for good, walking right as parses
fill in from the left. Removing a column is an explicit ✕ at the end of its
title line (the drilldown's), not a click on the title itself — a heading that
deletes what you are reading is not a heading.

`GET /api/players?q=` / `GET /api/players/{name}/runs` remain in `zoneruns_api.py`
— a `json_each` scan of `zone_runs.roster_json` behind `VISIBLE_RUN_IDS`, the
same predicate as the list — but the picker no longer calls them. `?roster=1`
runs behind that identical predicate, so it reveals nothing a viewer could not
already read fight by fight.

## Importing a parse from a screenshot (`pipeline/actshot.py`, schema v27)

Half of every comparison people actually make lives in Discord as an image.
Somebody posts their ACT window, somebody else wants to know how they measure
up, and there is no log on either side of that exchange — only a JPEG. This
reads one back into numbers.

**It is not a second ground truth.** An ACT XML export is what the parser is
validated against, and that has not changed. A screenshot is a CLAIM about
somebody else's night, read off pixels, and the whole design follows from
taking that seriously rather than from trying to make it look authoritative.

### It is kept out of the parse world entirely

An import writes one row in `imported_parses` and touches nothing else: no
session, no character, no encounter, no zone run, no entity. That is the
containment, and it is structural rather than a matter of remembering to
filter. Nothing that rolls up, ranks, votes on a guild tag or clusters a raid
can reach a shot, because none of them look in that table. The rows live as
JSON in it for the same reason — the moment they share a table with parsed
numbers, something eventually averages the two together.

Visibility is equally deliberate: a shot is private to whoever imported it,
full stop. `groups.py` owns the one visibility predicate for real parses, and
the rule about that predicate is that it does not acquire weaker siblings. A
shot needs no branch in it, so it gets none; if shots ever want sharing they go
through the existing predicate rather than beside it. Ids are sequential, so a
stranger's `GET` answers 404 exactly as a missing one does.

The picture is kept, but never the original file. A re-encoded WebP copy and a
thumbnail go to `PARSESHOTS_DIR`; the uploaded bytes do not. This reverses a
first decision to drop the image entirely, and the reason it reversed is the
table further down: four columns cannot be checked by any arithmetic, so the
screenshot is the only other evidence those numbers have, and a parse you
cannot put beside its source is one you have to take on faith. Re-encoding is
what makes that safe to keep — the file on disk is an image this app wrote, at
a size it chose, carrying nothing the original file carried besides pixels.

The copies are exactly as private as the row. Served by an owner-checked
endpoint rather than a static mount, because a static directory makes the
filename the permission; named with a random token so a stray path is not one
either; and `Cache-Control: private` so no shared cache holds somebody's
screenshot. They are written only AFTER the table reads — a picture of
something that is not an ACT window has no reason to be on this disk — and
deleting the parse deletes them.

The kept copy is deliberately NOT shrunk to a convenient web size. Its purpose
is reading a number off it, and small antialiased digits scaled to fit a
viewport are precisely what cannot be checked; it is bounded at 2200px only so
a 4K capture doesn't sit at full size.

The VIEWER is a separate question from the file, and it answers differently:
it opens **fit to the screen** and zooms on request. Opening at the stored
pitch followed that same reading-a-number argument and got it wrong by one
step — a 2200px capture dropped onto a laptop shows you one corner of a table
with no way to tell which corner. So the first paint is the whole window and
`Full size` scrolls it at its stored pitch, which is the mode for checking a
cell. Two jobs, two modes. It also renders into `document.body`: opened from
inside a compare column it was trapped in that column's card (`backdrop-filter`
again — see the Picker note above) and painted under the column to its right.

**An imported column is NAMED, not labelled with whatever ACT's title bar
said.** That title bar names the VIEW, so a whole-night screenshot comes back
called `All` — true, and no answer at all to "which parse is this" when two
imported columns sit side by side. `shotTitle` joins who, where and which
fight: *Bobby — Halls of Fate — All*, dropping any part the shot doesn't carry.
The screenshot itself sits in the column HEAD, right of that title block rather
than under it: both are about three short lines tall, so side by side they cost
one band of the column where stacked they cost two, and vertical space above
the table is spent by BOTH parses before their rows line up. The ✕ goes past
the picture, in the card's corner — everywhere else it ends the title line,
which is the same statement (the far end of the head).

### Nothing about the table is assumed

ACT's columns are the reader's, and the two committed fixtures prove it: one
has `AvgDelay` and the other has no such column. So the geometry is measured
per image.

*Rows.* Horizontal rules give the ladder, but they are FITTED rather than
walked. Rescaling by Discord makes the pitch fractional — 17.46px, so gaps
alternate 17/18 — and a fixed pitch drifts off the ladder within twenty rows.
Every (pitch, offset) is scored by how many rules land on it and the winner is
refit by least squares. That also absorbs the two things that break a greedy
chain: a highlighted row swallows its own rule, and the pie chart under the
table contributes rules of its own. Getting this wrong is not subtle — the
pie's legend entries are ability names, so a ladder that runs past the table's
bottom edge reads the legend as parse rows.

*Columns.* Only the header band carries separator ticks. A separator is told
apart from header LETTERING by variance down the band, not by darkness: on a
rescaled shot the two are equally dark, and a mean-only test reads half the
header as columns. The heading is then OCR'd and fuzzy-matched to a field,
because the heading is the only thing that says what a column is.

*The selected row.* ACT draws it white-on-blue. Greyscaling loses it entirely,
and inverting leaves dark text on grey inside an otherwise white column strip,
which `psm 6` drops. It is binarized to black-on-white and re-read three ways —
tight crop, wide crop, whole row — because no single crop is right: tight
clips a right-aligned leading digit (`824` → `24`), wide bleeds the
neighbour's digits in (`1,017.33` → `64,017.33`), and whole-row loses column
identity where a word box straddles a boundary. The three disagree in
different places, so their agreement is the answer, grouped by DIGITS so that
`2.57` and `257` count as one reading — they differ only in whether the
decimal mark survived.

### The locale is arithmetic, not a setting

`5.612.947` is five million to a German client and 5.612 to an American one,
and at this font size `.` and `,` are a couple of pixels apart. Nothing asks
the user and nothing guesses: `Damage / EncDPS` is the same number on every
row, so the mark that makes the most rows agree on it wins. That is usually a
tie — reading `9.241,15` under the wrong mark still recovers every digit and
merely shifts the ratio by a factor of 100, the same factor on every row, so
the cluster is exactly as tight — and the tiebreak is the shape of the
two-decimal columns (`98,60` against `100.00`), which is the evidence a human
uses. The Discord fixture is German and is detected as such.

### The fight length comes from the table, not the title bar

The title's `[mm:ss]` is the duration of ONE encounter. On ACT's `All` line it
is not the fight length at all, and a shot of `All` printing `[00:12]` over 654
seconds of parse was read as a 12-second fight: `_repair` recomputed every
EncDPS as damage/12, so the `All` row published 378,596 DPS against ACT's own
6,946.73 and every ability row was wrong by the same factor of 54. Only the
DPS column was wrong, which is the column people import a screenshot to read.

So the duration is fitted from the table (`_duration_from_table`): the mode of
`Damage / EncDPS` across the rows, which is forty readings of one number
against the title's one. It survives the rows whose EncDPS lost a decimal mark,
and it is right about `All` as well as a single pull. The title is used only
when fewer than four rows agree, and where the two disagree the shot carries a
note saying so. On both single-encounter fixtures they agree, and the title's
value is kept untouched.

The same reversal applies to the two-decimal columns. ACT prints `EncDPS`,
`Average`, `ToHit` and `AvgDelay` with two decimals ALWAYS, so a reading
carrying no separator at all lost the mark rather than the digits — AvgDelay
`461` is 4.61. Losing a mark cannot shorten the digit string, which is what
makes that safe to apply without a second reading.

### What can be checked, and what is simply reported

ACT's table is redundant, and that redundancy is the entire warrant for showing
an OCR'd parse at all:

| column | how it is known |
|---|---|
| `Damage` | the `All` row is the sum of the rest |
| `EncDPS` | `Damage / duration` — recomputed, and it is also what FITS the duration |
| `Average` | `Damage / Hits` — recomputed, so a dropped decimal repairs itself |
| `ToHit` | `Hits / Swings` — recomputed while `Hits <= Swings` holds |
| `Hits`, `Swings` | `Hits <= Swings` is an invariant; a Swings cell that breaks it lost a digit (`73` → `7`) and is rebuilt from ToHit, which is a third reading of the same fact. Publishing the pair instead printed a ToHit of 1042.86% |
| `Median`, `MinHit`, `MaxHit` | **unverifiable**, beyond `Min <= Median <= Max` |
| `Crit%` | **unverifiable**, beyond being a percentage — which is enough to drop the selection artifact's leading digit on the highlighted row (`167%` → 67%) |

Unverifiable cells are reported as read. A cell that FAILS a check it was
subject to is blanked rather than published: a number we have positive
evidence is wrong is worse than no number. `Resist` snaps to the closed
vocabulary of damage types, so `cisease` never reaches a reader.

There is deliberately **no review step**. One was designed and dropped on
Lindsay's call, and the reasoning holds: a confirm grid cannot make an
unverifiable number true, and the cells it would have caught are exactly the
ones nobody can check against anything anyway. What survives instead is the
labelling — an imported column says `imported` wherever it appears. (Naming a
shot afterwards is a different thing and is allowed — see "On the page": it
touches the metadata around the table, never a figure in it.)

### On the page

`ShotDrop` IS Compare's empty column rather than a page of its own, because a
screenshot is another way of NAMING a parse, not a separate activity — and
because the slot that says "another parse goes here" and the box that takes one
are the same statement. Drop it, paste it (the way an image leaves Discord is
right-click → Copy image, so paste is first-class, not a nicety) or click to
browse; it becomes a column, and the slot slides one place right, still ready
for the next one, exactly as the search is after a hit.

Behind it, dimmed almost to a texture, is a real ACT window
(`frontend/src/assets/act-window.webp` — a 640px crop of the TABLE, not the pie
chart, 33 KB): the box shows what goes in it instead of only saying so. It
lifts on hover and again while a file is over the box, so the slot answers the
pointer. On the parchment theme it is stronger and multiplied, because a pale
screenshot on a pale card is otherwise invisible; either way it stays faint
enough that the caption is the only thing you read. Reading takes seconds rather than milliseconds, so the
endpoint is a plain `def` — FastAPI runs it in the threadpool and one import
does not stall the event loop.

**On Import, the shots are a COLUMN and the same slot heads it** (`.importcols`
— logs two thirds, shots one third, stacking under 900px). They were a
full-width card UNDER the log table, where a fifty-file backfill put them below
the fold, and they were a seven-column table, which a third of a page cannot
draw. So the shots are a list of entries — thumbnail, fight, who and where,
kind/length/when — and the slot at the top of the column is Compare's,
picture and all, because dropping a screenshot is one gesture and it should not
wear two shapes. The log drop box beside it took the same dress (2px dash, the
same +): it had grown its own arrow glyph, sitting a few inches from the box
everybody else's + is on.

**A shot can be NAMED after the fact** (`PATCH /api/parseshots/{id}`,
`ShotEdit`). A screenshot cropped to the table carries no title bar, so the
character, the zone, the fight and the date arrive empty and the import stays
`Unnamed fight` forever — while the person who dropped it knows exactly whose
parse it is. Character, zone, fight, when and damage-vs-healing are editable;
they are CLAIMS, which is what the row already was.

This is not the review step. **Not one figure out of the table is editable** —
those are checked against each other at import, and a typed cell would be the
only number on the page with no evidence behind it. The LENGTH is the one
number and only where there isn't one: it is fitted from the table (the mode of
`Damage / EncDPS`, forty readings against the title's one), so a shot that has
one refuses a replacement with a 409 and the field is disabled. A shot with
none has nothing to overrule — without it the column declines to show
per-second numbers at all, and the reader's own clock beats that refusal.

The token grammar keeps three fields, `shot:<id>:parse`, so the CSV, the
ordering and the remove logic never learn which kind a column is; only the
fetch and the table differ. `shot` cannot collide with a run id, which is
always a number. An imported column renders the SAME `BreakdownTable` as a
real parse — that is the point of importing one — and every column it draws is
either carried by the shot or derived the way it is for a real parse. `Crit %`
is the one worth naming: a shot carries the percentage, so the crit COUNT is
reconstructed from it, which the table's own `All` row then re-weights.

**The picture travels with the parse.** An imported column carries the
screenshot as a thumbnail beside its headline numbers (`ShotViewer.jsx`, shared
with the Import page's table), and clicking it opens the stored image at full
size — scrolling rather than scaled down, because the reason to open it is to
read a figure off it. Clicking ANYWHERE closes it: backdrop, picture, caption,
Escape, the Close chip; only the head bar is exempt. Some of the columns cannot
be checked by arithmetic, so the picture is the only other evidence there is —
leaving it behind on the Import page put the claim and its evidence on two
different screens.

A screenshot is of ONE view, so its column has no kind tabs — a Healing shot is
a Healing column, headed as one, next to whatever the columns beside it are
showing. (While a page-wide tab ruled every column, that same fact had to be an
apology: a Healing shot on the Damage tab said to switch tabs instead of drawing
heals under a DPS heading.) And it refuses rather than invents where it must: a
title bar with no `[mm:ss]` means there is no clock, so the column says
per-second numbers cannot be worked out rather than dividing by a guess.

