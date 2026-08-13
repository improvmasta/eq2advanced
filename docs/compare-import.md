# Compare and screenshot import

Index: `ARCHITECTURE.md`.

## The Compare page (`/compare`, `pages/Compare.jsx`)

N parses side by side, in the nav and available signed out. A column is
`(zone run, fight selection, subject)` where the subject is the whole raid or one
player — same player on two nights, two players on one boss, raid against raid.
It absorbed the old `RaidParseCompare` modal (raid columns only, unshareable);
`ComparePanel` on the raid page stays, because "these raiders, this run" is
already loaded there and needs no picker, but it renders the same way.

**A column is the ACTUAL parse, not a metric rollup.** People compare by lining
up screenshots of their ACT windows, so a player column is their ability
breakdown and a raid column is the zone page's parse list — same columns, same
rank coloring. A first version rendered metrics as rows and was replaced; nobody
compares "Crit % rows", they compare parses. The breakdown is one component,
`BreakdownTable.jsx`, shared by the drilldown, `ComparePanel` and this page, so a
parse looks identical everywhere. Comparison surfaces pass `defaultHidden`, and
the `SortableTable` Columns menu brings those back. Tables sharing a `prefsKey`
sync layout changes live via an in-module listener set (localStorage's own event
only fires cross-tab), which is what keeps columns lined up while you rearrange
them.

**The parses SCROLL TOGETHER, and each is a frozen table.** `SortableTable`'s
`syncScroll` groups — `compare` here, `cmppanel` in the raid page's
`ComparePanel`, `compareraid` for raid columns (a different column set, which
would line up with nothing). A comparison is read ACROSS: the whole job is having
the same stat under the same stat. A parse added mid-comparison lands where the
others already are. See "A parse table is FROZEN" in `docs/zoneruns.md`.

**A column carries its own kind tabs** — the same `KIND_FILTERS` the drilldown
offers (Damage / Heals / Power / Threat / Cures / Self), and only the ones that
parse has rows for (`availKinds`). A raid column offers Damage and Heals. This
replaced a page-wide tab pair, on the argument that comparing one column's damage
to another's heals is the reader's call, not the page's. The tab lives in
component state, NOT in `?c` — the token says what the comparison is OF, a tab is
how you are looking at it — and removing a column takes its tab with it (they are
held by position). Beyond the tabs a column is built like the drilldown: name,
class chip, ✕, controls, tabs, then `CompositionStrip` (shared with `ActorPanel`)
or a raid column's `ParseStrip`.

**The URL is the comparison.** One query param `c`, a CSV of
`<runId>:<sel>:<subject>` tokens, where `sel` is `all` or fight ids joined by `.`
(not `+`, which `URLSearchParams` reads as a space) and `subject` is `raid` or a
player name. EQ2 names are single-word alphanumeric, so the delimiters cannot
collide. Malformed tokens are dropped, never crashed on. Every action rewrites
`c`, so a pasted link reproduces the whole comparison.

**Every number comes from `/encounters/agg`** — per-encounter authorized,
memoized, client-cached — and never from the run report, whose rows are frozen
whole-run and would silently mismatch a per-fight selection. Cross-parse identity
is BY NAME (entity ids are session-scoped). A raid column sums its raiders, which
is right for the rates too since every raider's rate runs over the same fight
clock. **Columns fetch and fail INDEPENDENTLY**: a run the viewer cannot see
renders "not visible to you" in that column and the rest of the comparison
stands, which is what makes the links safe to share.

Getting there is one click from any parse: a Compare chip in the raid page's
title block (carrying the current fight selection) and one in the player
drilldown header (`ActorPanel compareTo`, players only). Both land with one
column loaded.

### The picker is one faceted live search, computed in the browser

A search box over Zone / Named mob / Date / Guild / Player dropdowns. Zones,
guilds and mob names match anywhere in the string (people type them from the
middle) while roster names match from the front. It replaced a flat `<select>`
over every visible run plus a separate two-step player search — two controls that
could not narrow each other.

It is client-side because the page **already fetches the whole visible list**, one
row per NIGHT with the same yours-then-primary rule as the raid list. `?roster=1`
(`list_zone_runs`) adds each night's names and named mobs with the encounter ids
that are each fight, parsed server-side so the client never learns the storage
format; it is smaller than one `/encounters/agg` answer the page will fetch
anyway. That buys zero debounce, zero new endpoints, and instant cross-narrowing:
**each dropdown's options are computed from the nights matching every OTHER
facet**, so no combination can strand you on an empty list. The Guild dropdown is
not rendered at all when nothing visible carries a tag. Named mobs follow the
fight rail's hiding rule (`_named_for_runs`).

**The search is a BAND across the top, and one click on a result IS the add.** It
was a 300px card holding the left edge, which spent a third of every row on a
control you have already used. Each dropdown is named for what it holds rather
than for the rows it would leave alone ("Zone", not "Any zone": a facet is off
when it reads its own name), and Guild and Player put YOUR guild and YOUR
characters at the top marked `(You)`, read off the `mine` flag the list already
carries.

A result click lands the column already scoped to what the search was about — the
named mob's fights if one is picked (all of them), and the person if the Player
facet or the anchor column names one. The old two-step (select a night, pick a
subject, press Add) is gone; the column's own two dropdowns fix whatever the click
got wrong.

**Every dropdown on the page is `Picker`, not `<select>`.** A native select costs
three things this page cannot spend: its popup is OS chrome, so the surface a
reader opens most often is the one that looks like nothing else here; an
`<option>` is a string, so a raider can be a name or a class but not both; and a
closed select is as wide as its widest option, so one long roster name sets the
width of a control reading a short one. `Picker.jsx` splits those apart — the
BUTTON is sized by the row it sits in and truncates, the PANEL is sized by its
content. Rows carry an icon and a muted hint; sections are optgroups; past ten
rows the panel grows a filter.

**The open panel renders into `document.body`** and is positioned from the
button's rect. That is not a preference: **every `.card` here carries
`backdrop-filter`, which makes it a stacking context AND a containing block for
`position: fixed`**, so a menu written inside a card is sealed into that card's
box and painted under every later sibling however high its z-index goes. The same
trap put the screenshot viewer under the next column. Leaving the card is the fix;
a bigger z-index cannot be.

**A row's own parts are targets too.** A night found by a mob name is not really
an answer of "this raid", so the matching named mobs sit under the row as chips
and go straight to that fight, as does any raider whose name the query matched. A
chip carries a MARK, not just a tint (a skull for a pull, a head for a person) —
they are the one place two kinds of target sit in one strip. The chips hang off a
vertical rule descending from their row, and each result is ruled off from the
next, or one row's chips read as the next row's subtitle. With no question about
mobs asked, the night's named mobs are offered anyway (capped); raiders are not,
because two dozen names under every row is a roster, not a shortlist. Chips
compose with the facets rather than replacing them.

**Results appear only once something has been asked, and the empty slot is the
drop box.** A dozen recent raids sitting there on arrival read as the page's
content, when the content is the parses underneath. So what speaks in the meantime
is the last column: a `ShotDrop` styled as a parse column (`.dropslot`) captioned
*Search or add a screenshot to compare…*. It says where the next parse lands AND
takes one, which is why there is no second placeholder. It stays for good, walking
right as parses fill in from the left. Removing a column is an explicit ✕ at the
end of its title line, not a click on the title.

`GET /api/players?q=` / `GET /api/players/{name}/runs` remain in
`zoneruns_api.py` behind `VISIBLE_RUN_IDS`, but the picker no longer calls them.
`?roster=1` runs behind that identical predicate, so it reveals nothing a viewer
could not already read fight by fight.

## Importing a parse from a screenshot (`pipeline/actshot.py`, schema v27)

Half the comparisons people actually make live in Discord as an image, with no log
on either side of the exchange. This reads one back into numbers.

**It is not a second ground truth.** An ACT XML export is what the parser is
validated against. A screenshot is a CLAIM about somebody else's night, read off
pixels, and the whole design follows from taking that seriously.

### It is kept out of the parse world entirely

An import writes one row in `imported_parses` and touches nothing else: no
session, no character, no encounter, no zone run, no entity. That containment is
structural rather than a matter of remembering to filter — nothing that rolls up,
ranks, votes on a guild tag or clusters a raid looks in that table. The rows live
as JSON for the same reason: the moment they share a table with parsed numbers,
something eventually averages the two together.

Visibility is equally deliberate: a shot is private to whoever imported it. A shot
needs no branch in `groups.py`'s one predicate, so it gets none; if shots ever
want sharing they go through the existing predicate rather than beside it. Ids are
sequential, so a stranger's `GET` answers 404 exactly as a missing one does.

**The picture is kept, but never the original file.** A re-encoded WebP copy and a
thumbnail go to `PARSESHOTS_DIR`; the uploaded bytes do not. This reversed a first
decision to drop the image, because four of the table's columns cannot be checked
by any arithmetic — the screenshot is the only other evidence those numbers have.
Re-encoding is what makes keeping it safe: the file on disk is an image this app
wrote, at a size it chose, carrying nothing besides pixels.

The copies are exactly as private as the row: served by an owner-checked endpoint
rather than a static mount (a static directory makes the filename the
permission), named with a random token, and `Cache-Control: private`. They are
written only AFTER the table reads, and deleting the parse deletes them.

The kept copy is deliberately NOT shrunk to a convenient web size — its purpose is
reading a number off it — and is bounded at 2200px only so a 4K capture does not
sit at full size. **The VIEWER answers differently**: it opens fit to the screen
and zooms on request, because a 2200px capture dropped onto a laptop shows one
corner of a table with no way to tell which corner. `Full size` scrolls it at its
stored pitch. It also renders into `document.body` (`backdrop-filter` again).

**An imported column is NAMED, not labelled with whatever ACT's title bar said.**
That title bar names the VIEW, so a whole-night screenshot comes back called
`All` — true, and no answer to "which parse is this" when two imported columns
sit side by side. `shotTitle` joins who, where and which fight, dropping any part
the shot does not carry. The screenshot sits in the column HEAD, right of the
title block rather than under it: both are about three short lines tall, so side
by side they cost one band where stacked they cost two.

### Nothing about the table is assumed

ACT's columns are the reader's — one committed fixture has `AvgDelay` and the
other has no such column — so the geometry is measured per image.

*Rows.* Horizontal rules give the ladder, but they are FITTED rather than walked.
Rescaling by Discord makes the pitch fractional, and a fixed pitch drifts off the
ladder within twenty rows. Every (pitch, offset) is scored by how many rules land
on it and the winner is refit by least squares. That also absorbs the two things
that break a greedy chain: a highlighted row swallows its own rule, and the pie
chart under the table contributes rules of its own. Getting this wrong is not
subtle — the pie's legend entries are ability names, so a ladder running past the
table's bottom edge reads the legend as parse rows.

*Columns.* Only the header band carries separator ticks. A separator is told apart
from header LETTERING by variance down the band, not by darkness: on a rescaled
shot the two are equally dark. The heading is then OCR'd and fuzzy-matched to a
field.

*The selected row.* ACT draws it white-on-blue. Greyscaling loses it entirely and
inverting leaves dark text on grey, which `psm 6` drops. It is binarized to
black-on-white and re-read three ways — tight crop, wide crop, whole row —
because no single crop is right: tight clips a right-aligned leading digit, wide
bleeds the neighbour's digits in, and whole-row loses column identity where a word
box straddles a boundary. Their agreement is the answer, grouped by DIGITS so
readings differing only in whether the decimal mark survived count as one.

### The locale is arithmetic, not a setting

`.` and `,` are a couple of pixels apart at this font size, and the same string
means two different numbers depending on the client's locale. Nothing asks the
user and nothing guesses: `Damage / EncDPS` is the same number on every row, so
the mark that makes the most rows agree wins. That is usually a tie — reading
under the wrong mark still recovers every digit and shifts the ratio by the same
factor on every row — and the tiebreak is the shape of the two-decimal columns,
which is the evidence a human uses.

### The fight length comes from the table, not the title bar

The title's `[mm:ss]` is the duration of ONE encounter, and on ACT's `All` line it
is not the fight length at all. Taking it literally made `_repair` recompute every
EncDPS against a duration off by a factor of fifty — and DPS is the column people
import a screenshot to read.

So the duration is fitted from the table (`_duration_from_table`): the mode of
`Damage / EncDPS` across the rows, which is forty readings of one number against
the title's one. It survives rows whose EncDPS lost a decimal mark. The title is
used only when fewer than four rows agree, and where the two disagree the shot
carries a note.

The same reversal applies to the two-decimal columns: ACT prints `EncDPS`,
`Average`, `ToHit` and `AvgDelay` with two decimals ALWAYS, so a reading carrying
no separator lost the mark rather than the digits. Losing a mark cannot shorten
the digit string, which is what makes that safe without a second reading.

### What can be checked, and what is simply reported

ACT's table is redundant, and that redundancy is the entire warrant for showing an
OCR'd parse at all:

| column | how it is known |
|---|---|
| `Damage` | the `All` row is the sum of the rest |
| `EncDPS` | `Damage / duration` — recomputed, and it is what FITS the duration |
| `Average` | `Damage / Hits` — recomputed, so a dropped decimal repairs itself |
| `ToHit` | `Hits / Swings` — recomputed while `Hits <= Swings` holds |
| `Hits`, `Swings` | `Hits <= Swings` is an invariant; a Swings cell that breaks it lost a digit and is rebuilt from ToHit, a third reading of the same fact |
| `Median`, `MinHit`, `MaxHit` | **unverifiable**, beyond `Min <= Median <= Max` |
| `Crit%` | **unverifiable**, beyond being a percentage — which is enough to drop the selection artifact's leading digit |

Unverifiable cells are reported as read. **A cell that FAILS a check it was
subject to is blanked rather than published** — a number we have positive
evidence is wrong is worse than no number. `Resist` snaps to the closed vocabulary
of damage types.

There is deliberately **no review step**: a confirm grid cannot make an
unverifiable number true, and the cells it would catch are exactly the ones nobody
can check against anything. What survives instead is the labelling — an imported
column says `imported` wherever it appears.

### On the page

`ShotDrop` IS Compare's empty column rather than a page of its own, because a
screenshot is another way of NAMING a parse, and because the slot that says
"another parse goes here" and the box that takes one are the same statement. Drop
it, paste it (right-click → Copy image is how an image leaves Discord, so paste is
first-class) or click to browse; it becomes a column and the slot slides one place
right.

Behind it, dimmed almost to a texture, is a real ACT window
(`assets/act-window.webp`, a crop of the TABLE): the box shows what goes in it
instead of only saying so. It lifts on hover and again while a file is over it. On
the parchment theme it is stronger and multiplied. Reading takes seconds rather
than milliseconds, so the endpoint is a plain `def` — FastAPI runs it in the
threadpool and one import does not stall the event loop.

**On Import the shots are a COLUMN and the same slot heads it** (`.importcols` —
logs two thirds, shots one third, stacking under 900px). They were a full-width
card under the log table, below the fold after a large backfill, and a
seven-column table that a third of a page cannot draw. So they are a list of
entries — thumbnail, fight, who and where, kind/length/when — and the slot at the
top is Compare's, picture and all. The log drop box beside it took the same dress.

**A shot can be NAMED after the fact** (`PATCH /api/parseshots/{id}`, `ShotEdit`).
A screenshot cropped to the table carries no title bar, so character, zone, fight
and date arrive empty while the person who dropped it knows exactly whose parse it
is. Those, and damage-vs-healing, are editable; they are CLAIMS, which is what the
row already was.

**This is not the review step. Not one figure out of the table is editable** —
those are checked against each other at import, and a typed cell would be the only
number on the page with no evidence behind it. The LENGTH is the one exception and
only where there is not one: a shot that has a fitted length refuses a replacement
with a 409 and the field is disabled. A shot with none has nothing to overrule,
and without it the column declines to show per-second numbers at all.

The token grammar keeps three fields, `shot:<id>:parse`, so the CSV, the ordering
and the remove logic never learn which kind a column is; only the fetch and the
table differ. `shot` cannot collide with a run id, which is always a number. An
imported column renders the SAME `BreakdownTable` as a real parse, and every
column it draws is either carried by the shot or derived the way it is for a real
parse (a shot carries the crit percentage, so the crit count is reconstructed from
it and the `All` row re-weights it).

**The picture travels with the parse.** An imported column carries the screenshot
as a thumbnail beside its headline numbers (`ShotViewer.jsx`, shared with the
Import page's table), and clicking it opens the stored image at full size,
scrolling rather than scaled down. Clicking anywhere closes it. Leaving the
picture behind on the Import page put the claim and its evidence on two different
screens.

A screenshot is of ONE view, so its column has no kind tabs — a Healing shot is a
Healing column, headed as one. And it refuses rather than invents where it must: a
title bar with no `[mm:ss]` means there is no clock, so the column says per-second
numbers cannot be worked out rather than dividing by a guess.
