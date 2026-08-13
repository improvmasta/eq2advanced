# SkillIssueToolkit + eq2advanced

A proposal for combining the two projects.

## Where we each are now

**SkillIssueToolkit** is an ACT plugin plus a separate overlay process. It reads ACT's
combat data and draws transparent always-on-top windows over the game: a DPS meter,
regex notification alerts, and timer bars mirrored from ACT's native Spell Timers.

**eq2advanced** is an ACT plugin plus a website. The plugin tails the EQ2 log file and
uploads the raw lines; everything else happens server-side. The site parses the night,
stores it, and serves per-fight and per-night stats, AoE analysis, and coaching. It also
has a live path: while you raid, the in-flight fight streams back out as a browser
overlay (an OBS source, or EQ2's own in-game browser window).

Almost nothing overlaps except the DPS meter. You have solved the Windows overlay problem
(always-on-top, click-through, lock-to-game, per-window config), which eq2advanced has
not. eq2advanced has years of parsed raids and measures things off them that a single
session cannot know.

## What we would do

### 1. Add the uploader to your plugin

Your plugin gains an eq2advanced settings group with a box to paste a pairing code.
Once it is paired it uploads in the background, and the user's raids show up on
eq2advanced.com as they play.

The upload contract is small (`/api/ingest/hello`, `/api/ingest/batch`,
`/api/ingest/backfill/done`, bearer token) but it has edges that only show up under raid
load: batches have to be cut on log-second boundaries or the line dedupe splits, one
batch in flight per token, and idempotency is keyed on `(token, batch_id)`. Rather than
have you implement against a spec, we'd give you the working client as a component to
embed. It is about 1,100 lines across `Ingest/LogTail.cs`, `Ingest/Uploader.cs`,
`Net/ApiClient.cs` and `Core/Settings.cs`, and it depends on ACT for nothing but its
settings tab. It reads the log file directly, so it does not interact with your parsing
path at all.

Worth saying up front: both plugins already load in ACT side by side today, so this part
is about a single install and a single settings tab rather than a new capability.

### 2. Your timer bars draw from eq2advanced instead of from ACT's list

This is the part with something new in it.

ACT's spell timers are regexes with a duration typed in by hand. Nobody re-measures them,
and the number cannot change once the bar starts. eq2advanced measures the real recast off
every raid uploaded to the site. On 8 Mayong kills, `Soul Paralysis` runs 43.6s against
the list's 37, with 27 agreeing intervals behind it. A bar counting to 37 is wrong by six
seconds on every cast.

The bigger piece is reuse debuffs. `Traumatic Swipe` advertises -50% reuse speed. Measured
against clean cycles of the same ability in the same fight it comes out around x1.31 on
`Soul Paralysis`, and it does not move `Whirling Bladestorm` at all. So the magnitude is
learned per (mob, ability) rather than taken from the tooltip, and the site tracks whether
a swipe window was open at the moment of each cast. The recast belongs to the state at the
cast that started it: a debuff landing halfway through a recast does not retune it. On a
20-minute Mayong kill that separates `Blanket of Eternal Night` into 57/60/60/58 clean
cycles against ~77 swiped, which is the split you can already see by eye in the gaps.

The result is a countdown that re-targets itself when a rogue presses Traumatic Swipe.
ACT's timers have no mechanism for that.

Two other things fall out of measuring rather than configuring:

- Abilities nobody wrote a trigger for still get a bar. A cast is detected structurally
  (one enemy ability touching five or more people in one second), so the site finds AoEs
  that are not in anyone's ACT config.
- Some abilities get no countdown on purpose. A damage shield reaches the raid the same
  way an AoE does, and clustering turns it into a plausible but invented timer. Mobs that
  share a name (two halves of a splitter, six trash mobs pulling the same AoE) produce a
  number that is worse than nothing. Both are detected and suppressed.

**How it would work.** All of the above already runs server-side and is already streamed:
`GET /api/overlay/{token}/stream` emits the in-flight fight including the AoE section, and
`GET /api/overlay/{token}` serves the config alongside per-account marks for which AoEs
the raid actually jousts and which one owns the burn window. Your overlay would consume
that stream. Nothing about a cast, a period, or a swipe would be decided in the plugin,
which means your overlay, the in-game window, an OBS source and the website cannot
disagree with each other.

The one thing this asks of your side: `AlarmTimerBridge` currently mirrors ACT's own
remaining-time, which cannot re-target mid-cycle. The bars would need to run their own
countdown, seeded by a start event. That is a change in what `timers.html` is, and it is
worth deciding on before anything else gets built.

Two display details that matter for this to read as trustworthy:

- When a bar re-targets because of a swipe it should say so on the bar. A bar that
  silently stretches looks broken.
- Where the measurement is not yet conclusive (the site keeps a deliberate band between
  "definitely affected" and "definitely immune"), the bar should show uncertainty rather
  than pick a number.

### 3. Connectivity

Timers are the one thing that must not stop when the network does.

eq2advanced would publish the learned timer table as a plain cached endpoint
(`GET /api/timers`): base period, whether it is learned or reported, the swipe factor and
verdict, and the suppressions. The plugin fetches it at zone-in and keeps it. If the
connection drops, the bars keep running off that table, marked as degraded, instead of
going away. The swipe adjustment can survive too if it is worth the code on your end:
detecting a landed Traumatic Swipe and holding a 30-second window is a few dozen lines.

Because the cached path only runs when the live path is gone, the two are never active at
once, so there is no question of them disagreeing.

### 4. The DPS meter stays yours

The meter is faster computed locally, it is already correct, and it works with no network
at all. eq2advanced's live meter runs about a second behind by design (a log line cannot
be sent until the second it belongs to is complete). There is no reason to replace it.
What eq2advanced would add on that side is after the fact: a link to the fight on the
site, the night's history, coaching.

## What each side gets

**SkillIssueToolkit users get:**

- timers that are measured instead of typed, and that adjust for reuse debuffs
- timers for abilities nobody wrote a trigger for
- no bogus bars from damage shields or same-named mobs
- their raids on eq2advanced.com without installing a second plugin
- jousts and burn windows marked once and shown in the overlay

**eq2advanced gets:**

- a real overlay, which it currently does not have. Its in-game answer today is EQ2's
  built-in browser window, which is worse in every way than an always-on-top window with
  click-through and lock-to-game.
- more uploaded raids, which is directly what makes the learned timers better. The
  adoption gates are 6 agreeing intervals across 2+ separate fights per (mob, ability),
  so coverage grows with the number of people uploading.

## Things to settle before code

- Licensing. SkillIssueToolkit is MIT. eq2advanced has no LICENSE file yet; that needs
  fixing before source moves either direction.
- Who owns the DLL and its updates. Your plugin auto-updates its notification rules from
  GitHub; eq2advanced serves its plugin as a ZIP from `/api/plugin`. Two update mechanisms
  in one process needs one answer.
- Build. Your csproj points a HintPath at a local ACT install. eq2advanced's CI downloads
  ACT and builds against the strong-named assembly. Merging the build is likely more
  annoying than merging the code.
- Repos. The suggestion is that neither project absorbs the other: two repos, two release
  cadences, joined by the uploader component and the two HTTP endpoints. Nobody ends up
  blocked on the other's release, and if it stops being fun nothing has to be untangled.

## Suggested first step

No code. Mint an overlay token on eq2advanced, point a page at the stream, and watch a
Mayong pull: `Soul Paralysis` counting to 43.6 instead of 37, and stretching when someone
presses Traumatic Swipe. If that looks worth having in `timers.html`, the rest follows
from it.
