"""The in-flight parse: what the fight on screen looks like RIGHT NOW.

Everything else in this app reports fights that are over. `pipeline/live.py`
deliberately publishes nothing about an open segment — a fight is written only
once it can no longer change (CLOSE_S of log time), because the incremental
rows have to stay byte-identical to what uploading the same file would produce.

A raid dashboard needs the other thing: the pull that is happening. So this
module builds a SNAPSHOT — a plain dict, computed from the open segment's
parsed events, handed to the SSE stream and thrown away. It writes nothing, it
resolves no entities, and no other code path reads it. That is what keeps
`test_golden_equivalence` true: the record is still the record, and this is a
picture of it taken mid-fight.

Two consequences of being a view, both deliberate:

- **Names are keys.** There is no `EntityResolver` here (resolution is the
  expensive half of a flush), so a player is their name and a pet credits its
  owner by name. Two mobs sharing a name pool, exactly as they do on the AoE
  tab. The close-time rebuild is what makes attribution exact.
- **The numbers are provisional.** DPS over a fight still running rises and
  falls; the fight's own name is a guess until it ends (see `_provisional`).
  The payload says so — the UI must not present these as the parse.

Live AoE detection reuses `pipeline/aoes.py`'s definition rather than
restating it: a second in which one enemy ability touched MIN_TARGETS players
is a cast — or touched anyone at all, if the reported-timer list knows the
ability by name (`aoes.anchors`). Only casts inside the CURRENT fight count
toward an observed
period — the wait between two pulls is a raid taking a break, not a cooldown —
so a boss's first pull of the night shows a countdown only when ACT's
reported-timer list knows the ability.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import NamedTuple

from parser.events import F_SELF_FOCUS, F_ZERO
from parser.subjects import classify_entity_kind, decompose
from pipeline.aoes import (DEFAULT_CLUSTER_S, CLUSTER_FRACTION, MIN_CASTS,
                           PET_KINDS, SUSTAINED_RUN, _cluster, anchors,
                           collect_windows, debuffed_at, observed_period,
                           REFLECT_EDGE_S, reflect_bursts, reflect_windows,
                           reported_timers, reuse_debuff_names, several_bodies,
                           split_cycles, suggest_period)
from pipeline import aoelearn
from pipeline.encounters import GAP_S, _is_named_mob
from pipeline.refine import refine_known_mobs

MAX_ACTORS = 40          # a raid is 24; the tail is mobs and strays
MAX_TIMELINE_S = 900     # the chart scrolls, so older seconds are not sent
MAX_AOES = 12
UNKNOWN_SOURCE = "Unknown"   # the same pool the recorded AoE tab uses
# What "raid-wide" has to mean on a screen that shows only the abilities worth
# watching for. `MIN_TARGETS` is five people in one second, which is an EQ2
# GROUP — the right anchor for an audit that lists everything, and far too
# loose for a panel of countdowns. Measured over real pulls the two
# populations do not overlap: a boss's raid AoE reaches 72-100% of the raid,
# and every add cleave and one-off that cluttered the panel reached 15-43%.
RAID_FRACTION = 0.6
# When a countdown stops being a countdown. Past due is information — "3s
# late" reads as a stunned mob and "40s late" reads as a wrong timer — but an
# hour into a raid a row that has been late for a minute is telling nobody
# anything, and the panel is a shortlist. It leaves, and it comes back on its
# own the moment the ability lands again, because every snapshot is rebuilt
# from the fight's events rather than accumulated.
#
# This is the line for a row with NOTHING TO BE LATE FOR — no period, measured
# from its last cast. An avatar's irregular raid-wides (`Stealth Assault`) have
# no next cast to miss, so the only honest thing they say is "this happened
# recently", and a minute is how long that stays true.
OVERDUE_DROP_S = 60
# WHEN A CAST THAT HAD A TIME IS ADMITTED TO HAVE BEEN MISSED, which is a much
# shorter fuse than the one above and a different question.
#
# A row WITH a period is a claim about the next few seconds, and once it is
# badly past due that claim is simply wrong: either the mob was stunned, or the
# cast landed and nothing detected it (a raid-wide that every single person
# blocked or absorbed prints no damage, so there is nothing to anchor on), or
# the timer is off. Whichever it was, the ability did not fire when it said, and
# a countdown reading `+0:47` is not telling anybody when anything is due.
#
# The COST of getting this wrong is not symmetric, which is why it is 15s and
# not 60. Vampire Lord Mayong Mistmoore's `Soul Paralysis` gets skipped a minute
# or two into the fight; at 60s the row sat there overdue for a full minute AND
# — because the burn window belongs to the SOONEST jousted cast, and a cast in
# the past is soonest by a mile — it held the burn window with it. So the one
# number a raid acts on said "you are 47 seconds into an AoE you already left
# for", all the way through a window they could have been burning in.
MISSED_S = 15
# HOW LONG A FINISHED REFLECT WINDOW STAYS ON SCREEN, saying it is over.
#
# Every other row here counts toward something. A reflect row counts toward the
# moment it stops mattering, and that moment is the only thing anybody is
# waiting for — so vanishing silently at 0:00 throws away the one announcement
# the row exists to make. It holds the slot briefly, says CLEAR, and goes.
#
# Short, because it is a statement about the present tense and stops being true
# almost immediately; the AoE rows' `OVERDUE_DROP_S` minute would leave a stale
# all-clear on screen most of the way to the next window.
REFLECT_CLEAR_S = 5
# Pairing a reflected cast to the damage that came back. The log prints the
# deny line and the return as separate lines in the same second or the next
# one; two seconds is slack for a tick that lands late, and is tight enough
# that the mob's own use of a same-named ability cannot be swept in.
REFLECT_RETURN_S = 2


class Knowledge(NamedTuple):
    """What the session already knows about who these names are.

    None of it is derivable from the open segment, all of it comes from parses
    that already finished, and it is what makes the FIRST second of a pull as
    accurate as the fortieth. `pipeline/live.snapshot_context` builds it.
    """
    mobs: frozenset[str] = frozenset()       # one-word names that are enemies
    players: frozenset[str] = frozenset()    # names that have been raiders
    pets: frozenset[str] = frozenset()       # bare-named dumbfires
    pet_names: frozenset[str] = frozenset()  # named pets, for `decompose`
    # (mob, ability) -> what every raid on the site measured about its recast,
    # and about what a reuse debuff does to it (`pipeline/aoelearn.py`). The
    # same argument as the rest of this tuple, applied to timers: it is the
    # output of parses that already finished, it cannot be derived from one
    # open segment, and having it is what makes the first cast of a pull count
    # down against the right number instead of the third.
    timers: dict = {}


NO_KNOWLEDGE = Knowledge()


class Names:
    """Who each side of an event is, in the vocabulary the meter reports.

    Grammar alone gets four things wrong, and all four were visible on one
    Wuoshi pull:

    - **A one-word boss reads as a raider.** `Wuoshi` sat in the bar list, in
      the raider count, and — being a "player" — out of the pool the fight is
      named from, so the pull was titled after the adds ("Ancient Grovebeast").
      `refine_known_mobs` is the recorded path's answer to exactly this and it
      is a pure function over parsed events, so the live path runs it too, over
      `Knowledge.players` as its roster.
    - **A pet reads as a mob.** `Tragedy's unswerving hammer` is multi-word, so
      damage into it counted as damage into an enemy — and early in a pull that
      is enough to name the fight after somebody's dumbfire. Targets decompose
      exactly as `EntityResolver.resolve_target` decomposes them.
    - **A bare dumbfire reads as a raider.** `Knyi` has no owner possessive
      anywhere in the log, so only `Knowledge.pets` can say what it is.
    - **`YOU` is not a name.** The logger's own lines say YOU/YOURSELF, so the
      logger counted twice (once under each spelling) and their own AoE hits
      never reached the ≥5-raiders anchor.
    """

    def __init__(self, logger: str, events=(), know: Knowledge = NO_KNOWLEDGE):
        self.logger = logger
        self.know = know
        # a seeded mob is a finding from a whole finished night and outranks
        # the roster, which is only the veto on what this ONE segment can
        # infer (see live.snapshot_context)
        self.mobs = (know.mobs | refine_known_mobs(
            list(events), logger, know.players)) - {logger}
        self._tgt: dict[str, tuple[str, str]] = {}

    def _kind(self, name: str) -> str:
        kind = classify_entity_kind(name, "unknown", self.logger,
                                    self.mobs, self.know.pets)
        return {"mob": "mob", "swarm_pet": "pet"}.get(kind, "player")

    def source(self, src) -> tuple[str, str]:
        """-> (credited name, kind) for an event source. A pet's Subject
        already carries its OWNER's name, which is the whole reason the live
        path can credit pets without resolving entities."""
        if src.unit == "player" or src.unit in ("own_pet", "swarm_pet", "named_pet"):
            return src.name, "player"
        if src.name == UNKNOWN_SOURCE:
            return src.name, "other"
        return src.name, self._kind(src.name)

    def target(self, name: str) -> tuple[str, str]:
        """-> (credited name, kind) for an event target. Memoized: a raid fight
        is tens of thousands of events over a few dozen distinct names."""
        hit = self._tgt.get(name)
        if hit is None:
            hit = self._tgt[name] = self._target(name)
        return hit

    def _target(self, name: str) -> tuple[str, str]:
        if name == UNKNOWN_SOURCE:
            # the sourceless-damage pool, not a one-word raider called Unknown
            return name, "other"
        if name in ("YOU", "YOURSELF") or name == self.logger:
            # bare logger-name is their own pet, and it merges into them the
            # way `resolve_target` merges it
            return self.logger, "player"
        subj, remainder = decompose(name, self.logger, self.know.pet_names)
        if remainder is None and subj.unit in ("swarm_pet", "named_pet"):
            # ACT keeps a possessive pet as its own combatant on the TAKEN side
            # (`statsroll.taken_key`), so it is neither a raider nor an enemy
            return name, "pet"
        return name, self._kind(name)


def _provisional(dmg_to: dict[str, int], dmg_from: dict[str, int],
                 names: Names) -> tuple[str | None, bool]:
    """What to call a fight that has not ended yet.

    `encounter_label` names a fight after the enemy that took the most damage,
    but it needs resolved entities and a finished segment. This is the same
    rule on names alone, with the fallback that matters early in a pull: for
    the first seconds of a wipe the raid has dealt nothing, and the thing
    hitting them is still the answer.

    Both pools are already filtered to enemies by `Names`, which is where the
    accuracy actually comes from — a pool that mistook the boss for a raider
    and a dumbfire for an enemy could only ever name the fight after an add."""
    pool = dmg_to or dmg_from
    if not pool:
        return None, False
    name = max(pool, key=lambda n: (pool[n], n))
    return name, _is_named_mob(name, names.logger, names.mobs)


def _live_aoes(events, names: Names, players: set[str], now_ts: int) -> list[dict]:
    """Enemy AoEs seen in this fight, with when the next one is due.

    The anchor rule is `pipeline/aoes.py`'s, and the constants are imported
    from it so the two definitions of "a cast" cannot drift apart. What differs
    is the scope: one open segment, no encounter dimension, and no cast list —
    the dashboard wants a countdown, not an audit.

    And an audit's threshold is not a panel's. `aoes.detect` lists every
    ability that touched five people at once because a tab you scroll should
    miss nothing; a panel you glance at during a pull has the opposite job, and
    on a real Mayong kill that threshold drew ten rows for the three abilities
    the raid actually calls out. So a row earns its place two ways: ACT's
    spell-timer list knows the ability (the raid was TOLD to expect it, whoever
    it lands on — `Soul Paralysis` reaches one group in a long fight and
    everyone in a short one), or the cast reached `RAID_FRACTION` of the raid,
    which is what keeps a sourceless 24-target `Overnuke` that no timer list
    has ever heard of.

    The list decides the other thing too, and this is the half that was wrong:
    for an ability it knows, reach stops deciding what a CAST is. A row that
    earns its place by name and then re-arms only on the casts that happened
    to reach five people is a countdown anchored to the wrong second — Mayong's
    `Soul Paralysis` landed 11 times on the kill that turned this up, three of
    them raid-wide, and the panel counted 37s from the third. What falls out is what an add's cleave and a boss's
    one-off spell have in common: a group's worth of targets and nothing to
    count down to.

    Targets go through `Names` for the same reason the meter does: the logger
    is `YOU` on their own lines, and a raid-wide AoE that misses the one person
    whose log this is misses the anchor by exactly one."""
    logger = names.logger
    # (source, ability) -> second -> who it touched and HOW — the same three
    # outcomes the recorded tab reports (aoes.detect), so "blocked" means the
    # same thing mid-fight as it does in the audit afterwards
    by_ability: dict[tuple, dict[int, dict]] = defaultdict(
        lambda: defaultdict(lambda: {"hit": set(), "avoided": set(),
                                     "absorbed": set(), "touched": set()}))
    schools: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[tuple, int] = defaultdict(int)
    # When a reuse debuff was on each mob, off the SAME events. A swipe is a
    # damage line a raider put on a boss, so the live path sees it exactly as
    # the audit does and needs no cast line — which matters, because a cast
    # line from somebody who is not the logger is the one thing this parser
    # deliberately drops (`parser/buffs.py`).
    swipes = []
    debuff_names = reuse_debuff_names()
    for ev in events:
        if ev.type != "damage" or ev.ability not in debuff_names or not ev.tgt:
            continue
        # WHAT IT LANDED ON is the whole test, and the source deliberately is
        # not part of it. A registry entry is a player ability by definition —
        # no mob casts `Traumatic Swipe` — and the source side cannot carry the
        # test anyway: another raider's ability line gives `Subject('Tezen',
        # 'unknown')`, because a bare possessive name is exactly what the
        # parser cannot classify without the roster. Requiring `player` there
        # matched the LOGGER and nobody else, which is the one person who
        # usually is not the rogue pressing it.
        tgt, kind = names.target(ev.tgt)
        if kind in ("player", "pet"):
            continue          # a raider debuffing a raider is not this
        swipes.append((tgt, ev.ability, ev.ts))
    windows = collect_windows(swipes)

    for ev in events:
        if ev.type not in ("damage", "avoid") or not ev.ability or not ev.tgt:
            continue
        src = ev.src
        # Only the parser's OWN knowledge excludes a source — `YOU`/`YOUR` and
        # the possessive pet forms. Nothing here filters on name grammar,
        # because that would drop exactly the bosses worth a countdown: live,
        # "Venekor" is indistinguishable from a raider by name alone. The
        # anchor rule below is the real evidence — a raider's green AE hits
        # mobs, so reaching a GROUP of raiders in one second is a claim only an
        # enemy ability can make (`aoes.anchors`; an ability on the reported
        # list is taken on its name, and no raider spell is on that list).
        if src is not None and (src.name == logger
                                or src.unit in ("player", "own_pet",
                                                "swarm_pet", "named_pet")):
            continue
        tgt, kind = names.target(ev.tgt)
        raider = tgt in players
        # A pet is not part of what the raid covered and never counts toward
        # reach, but it is proof the ability went off (aoes.PET_KINDS) — which
        # is the whole evidence a reported-timer row needs.
        if not raider and kind not in PET_KINDS:
            continue
        # `X is hit by <Effect>` names no caster, and some of the biggest raid
        # AoEs arrive that way — the recorded tab pools them under Unknown and
        # so does this, or a 24-target hit with a 30s timer goes unwatched
        key = (src.name if src else UNKNOWN_SOURCE, ev.ability)
        sec = by_ability[key][ev.ts]
        sec["touched"].add(tgt)
        if not raider:
            continue
        if ev.type == "avoid":
            sec["avoided"].add(tgt)
        elif ev.flags & F_ZERO or not ev.amount:
            sec["absorbed"].add(tgt)
        else:
            sec["hit"].add(tgt)
        # shares, not a reconciliation — see the same accumulation in aoes.py
        if ev.type == "damage" and ev.dtype:
            schools[key][ev.dtype] += ev.amount or 0
        # and the total, which is not the sum of those: a school is only named
        # on a line that dealt damage, and this is what RANKS the abilities for
        # a panel that can only draw three of them
        if ev.type == "damage":
            totals[key] += ev.amount or 0

    timers = reported_timers()
    timers_known = names.know.timers or {}
    typical_factor = aoelearn.typical_factor(timers_known)
    out = []
    for (src, ability), seconds in by_ability.items():
        reported = (timers.get(ability) or {}).get("timer_s")
        # Reach, or the reported list — `aoes.anchors` owns which, so the panel
        # and the audit cannot disagree about what a cast is
        wide = anchors(seconds, reported)
        if not wide:
            continue
        threshold = (max(DEFAULT_CLUSTER_S, reported * CLUSTER_FRACTION)
                     if reported else DEFAULT_CLUSTER_S)
        # the audit's clustering, called rather than restated — this used to
        # be a copy of the loop, and a copy is how the panel and the tab come
        # to disagree about how many times a boss cast something
        clusters = _cluster(wide, threshold)
        starts = [c[0] for c in clusters]
        # A CAST IS A MOMENT; A DAMAGE SHIELD IS A CONDITION (aoes.
        # SUSTAINED_RUN). A shield reaches the raid exactly the way a cast
        # does, so neither the anchor nor RAID_FRACTION can tell them apart —
        # what separates them is that a shield keeps meeting the anchor second
        # after second, because it fires every time somebody swings. Mayong's
        # `Caress Feedback` was drawing a countdown on this panel: 9 raid-wide
        # seconds per burst against 1 for every real AoE in the same fights.
        #
        # It has to be caught HERE and not left to the clustering, which
        # actively hides it: 6-second gaps turn a shield that never stops into
        # tidy "casts" 19 seconds apart, and a shield that pauses when the mob
        # is untargetable into a plausible 55-second timer. The one shield that
        # the old code did drop was an accident — 149 unbroken seconds became a
        # single cluster and fell under MIN_CASTS.
        runs = sorted(len(c) for c in clusters)
        if not reported and runs[len(runs) // 2] >= SUSTAINED_RUN:
            continue
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        clean_gaps, swiped_gaps, _flags = split_cycles(starts, windows, src)
        # the SUGGESTION still comes off this pull's clean cycles alone, which
        # is what stops a fight somebody swiped end to end from proposing a
        # config edit measured under somebody else's debuff
        period, agree = observed_period(clean_gaps) if clean_gaps else (None, 0)

        # IS THIS NAME ONE MOB? (`aoes.several_bodies`). Only the reference
        # file's answer reaches the countdown, and the two INFERRED reasons
        # deliberately do not, for a reason particular to this panel: they are
        # computed off this pull's own measurement, so a row would gain and
        # lose its countdown as the number moved, and a countdown that comes
        # and goes mid-fight is worse than either answer. The list is known
        # before the pull starts and never changes during it.
        bodies = several_bodies(src, _is_named_mob(src, logger, names.mobs),
                                reported, period)

        # What to count with. Order of authority: what the site has MEASURED
        # over several clean fights, then ACT's list, then this pull's own
        # clean cycles. The learned number wins because it is the same
        # measurement as the last one with more of it behind it — 8 uploaded
        # Mayong kills put `Soul Paralysis` at 43.6s against the list's 37, and
        # a countdown that keeps insisting on 37 is not being cautious, it is
        # being wrong six seconds at a time (pipeline/aoelearn.py).
        #
        # A SPLITTER GETS NO COUNTDOWN AT ALL, from any of the three. Not
        # because the numbers are unknown but because none of them answers the
        # question the countdown asks: two halves of The Emerald Halls rumbler
        # are each on their own 50s recast, so the next `Rumbling of Earth`
        # lands in something between 0 and 50 seconds and a bar draining to 50
        # would be wrong on nearly every cast — while looking exactly like the
        # bars either side of it that are right. The row stays, says how many
        # times it fired and still flashes on the landing, which is the same
        # treatment a damage shield gets and for the same reason: what it has
        # to say is that this just happened.
        known = timers_known.get((src, ability))
        if bodies == "splits":
            base_s, period_src = None, None
        elif known and known["base_s"]:
            base_s, period_src = known["base_s"], "learned"
        elif reported:
            base_s, period_src = float(reported), "reported"
        else:
            base_s, period_src = period, ("observed" if period else None)

        # THE RECAST BELONGS TO THE STATE AT THE CAST THAT STARTED IT
        # (`aoes.split_cycles`), so the question is not whether a debuff is on
        # the mob right now — it is whether one was on it when it last cast.
        swiped = bool(starts) and debuffed_at(windows, src, starts[-1])
        verdict = (known or {}).get("swipe_verdict")
        factor = (known or {}).get("swipe_factor")
        # ONE SPAN, DECIDED BEFORE THE COUNTDOWN STARTS. A swiped row counts
        # the stretched number from the first second and marks where the
        # un-slowed timer would have fired; it never changes length partway
        # through. The first build did change — an unconfirmed row planned the
        # normal timer and grew past it — and a bar that resizes mid-drain is
        # exactly the thing this panel cannot afford: somebody is reading it
        # while fighting, and a length that means one thing at 0:30 and another
        # at 0:10 costs them their place. The mark carries what the growth was
        # trying to say, and it holds still.
        period_s = base_s
        normal_s = None
        if swiped and base_s and verdict != "immune":
            # This ability's OWN ratio when it has one, the median of the
            # confirmed rows when it does not. Weaker evidence than the verdict
            # asks for, deliberately: the verdict decides what we CLAIM, and
            # this only decides where the bar ends — with the normal timer
            # marked on it, so both numbers are on screen either way and a cast
            # landing on the tick says "immune" as loudly as one landing at the
            # end says "affected".
            period_s = round(base_s * (factor or typical_factor), 1)
            normal_s = base_s
        # one cast, no timer, nothing to count down to: a row that can only
        # say "that happened" is noise on a screen meant to be read at a glance
        if len(starts) < MIN_CASTS and not period_s:
            continue
        # Each cast's reach, aggregated over its ticks the way aoes.detect does
        # it. The last one is the outcome the row reports — who ate it and who
        # was covered — and the widest is the evidence that this is raid-wide
        # at all.
        reach = []
        for c in clusters:
            hit, avoided, absorbed = set(), set(), set()
            for ts in range(c[0], c[-1] + int(threshold) + 1):
                s = seconds.get(ts)
                if not s:
                    continue
                hit |= s["hit"]
                avoided |= s["avoided"]
                absorbed |= s["absorbed"]
            reach.append((hit, avoided, absorbed))
        widest = max(len(h | a | b) for h, a, b in reach)
        if not reported and widest < len(players) * RAID_FRACTION:
            continue
        next_due_ts = starts[-1] + period_s if period_s else None
        # Overdue is a state and the row reports it (OVERDUE_DROP_S), right up
        # until it has been late long enough that it is no longer telling
        # anybody when anything is due. The next cast puts it straight back.
        #
        # A row with NO period gets the same line, measured from its last cast,
        # and it was the half that was missing: nothing here expired a row that
        # had nothing to be late for, so it held its slot until the pull ended.
        # An avatar throws several raid-wide abilities that do not repeat on a
        # clock (`Stealth Assault`, `Mischievous Bombardment` — two casts, no
        # agreeing gap, no entry in ACT's list), and each one took a permanent
        # place at the bottom of a panel the meter is drawn UNDER: five rows on
        # screen, two of them saying only "2×", and the raiders pushed off the
        # scene. What a row with no timer has to say is that this just
        # happened, so it says it for as long as that is true.
        # Two lines, because the two kinds of row are late about different
        # things: a timed row has MISSED a cast it named a second for, an
        # untimed one has only stopped being recent. See both constants.
        stale_ts = next_due_ts if next_due_ts is not None else starts[-1]
        if now_ts - stale_ts > (MISSED_S if next_due_ts is not None
                                else OVERDUE_DROP_S):
            continue
        hit, avoided, absorbed = reach[-1]
        # a player who ate it is not also a player who dodged it
        avoided -= hit
        absorbed -= hit
        by_damage = dict(sorted(schools[(src, ability)].items(),
                                key=lambda kv: (-kv[1], kv[0])))
        out.append({
            # What KIND of row this is, so the browser branches on one field
            # rather than on which fields happen to be present. Everything
            # built here counts toward the next cast; `reflect` rows count
            # toward the end of a state (`_live_reflect`).
            "kind": "aoe",
            "source": src,
            "ability": ability,
            "casts": len(starts),
            # what fixes this row's place in the list, for the whole fight
            "first_cast_ts": starts[0],
            "last_cast_ts": starts[-1],
            # what decides whether the compact panel has room for it
            "damage": totals[(src, ability)],
            "since_s": max(0, now_ts - starts[-1]),
            "dtype": next(iter(by_damage), None),
            "dtypes": by_damage,
            # a timer the raid could go and fix, measured off this pull. Live
            # it needs the same three agreeing gaps the audit asks for, so it
            # appears late in a long fight or not at all — which is the point.
            "suggested_s": suggest_period(reported, period, agree, bodies),
            # why this name's gaps may not be one mob's recast, or None. The
            # panel puts it on the row's title and never in a word beside the
            # name: it explains a countdown that is missing or a number that
            # is not being believed, and neither changes what anybody does in
            # the next few seconds.
            "several_bodies": bodies,
            # WHETHER ACT'S LIST KNOWS THIS ABILITY, which the countdown may or
            # may not be using — a learned number outranks it and a splitter
            # refuses both. It is sent regardless because the browser needs it
            # for something else entirely: it is what the JOUST and MINI marks
            # default to (`lib/marks.js: actListed`), on the reasoning that the
            # raid's own callout list is the best available guess at which AoEs
            # somebody leaves for and which are worth a slot beside the game.
            "reported_s": reported,
            "period_s": period_s,
            "period_src": period_src,
            "next_due_ts": next_due_ts,
            # Was a reuse debuff on the mob when it last cast — the state this
            # recast is running under, not the state right now.
            "swiped": swiped,
            "swipe_verdict": verdict,
            "swipe_factor": factor,
            # the timer it WOULD have had, when we stretched it: the mark
            "normal_period_s": normal_s,
            "last_hit": len(hit),
            "last_blocked": len(avoided | absorbed),
            "last_targets": len(hit | avoided | absorbed),
        })
    # soonest first, then the ones with no timer at all
    # ROWS DO NOT MOVE. Soonest-due first is the obvious order and it was the
    # wrong one: every re-arm reshuffles the list, so the thing somebody is
    # tracking is somewhere else each time they glance back — and they are
    # glancing while fighting. Ordered by FIRST CAST instead, which is a fact
    # about the fight that cannot change once it has happened: a row appears at
    # the bottom when its ability first goes off and holds that slot until it
    # expires. The countdown moves; nothing else does.
    #
    # The cost is real and is accepted: the next cast due is no longer the top
    # row, so the panel is read by position rather than by rank. That is the
    # trade a raid wants — a position can be learned once, a rank has to be
    # re-read every glance.
    out.sort(key=lambda r: (r["first_cast_ts"], r["source"], r["ability"]))
    return out[:MAX_AOES]


def _live_reflect(events, names: Names, now_ts: int) -> list[dict]:
    """The reflect window the raid is standing in, if it is standing in one.

    A DURATION, NOT A PERIOD, and that is the whole reason this is a separate
    builder rather than another branch of `_live_aoes`. Every row up there is
    anchored on a cast and counts toward the next one; this is anchored on
    entering a state and counts toward LEAVING it. The two look alike on screen
    and share the drain bar, but nothing about how they are derived is shared:
    there is no ability, no reach, no period to measure, and no cast line —

    THE MECHANIC ANNOUNCES ITSELF NOWHERE. Checked against every non-damage
    line at all three window starts on six Treyloth kills: no emote, no buff,
    no `X begins to cast`. The only evidence a window has opened is a raider
    getting denied — `<caster> tries to <verb> <mob> with <spell>, but <mob>
    reflects` — so the row cannot exist until somebody has already paid for it.
    That cost is bounded and small: of 1,073 casts eaten across those 18
    windows, 55 (5%) landed in the trigger second itself. The other 95% is what
    this row is for.

    WHICH MOBS GET ONE IS A HUMAN'S CALL (`aoes.reflect_windows`), not a
    detection. Nine mobs reflect; on most of them nobody cares.

    Only the CURRENT window is returned. A window that closed two minutes ago
    is a fact for the audit and clutter on a countdown panel."""
    curated = reflect_windows()
    if not curated:
        return []

    denies: dict[str, list[tuple[int, str, str | None]]] = defaultdict(list)
    # (mob, ability, victim) -> the mob's own damage lines, which is where a
    # reflected spell comes back from: the log re-attributes it to the MOB and
    # keeps the player's spell name on it.
    returns: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for ev in events:
        if (ev.type == "avoid" and ev.src is not None and ev.tgt
                and ev.extra.get("how") == "reflect"):
            mob, _kind = names.target(ev.tgt)
            if mob in curated:
                denies[mob].append((ev.ts, ev.src.name, ev.ability))
            continue
        if (ev.type == "damage" and ev.src is not None and ev.tgt
                and ev.ability and ev.amount and ev.src.name in curated):
            victim, _kind = names.target(ev.tgt)
            returns[(ev.src.name, ev.ability, victim)].append((ev.ts, ev.amount))

    out = []
    for mob, hits in denies.items():
        window_s = int(curated[mob].get("window_s") or 0)
        if window_s <= 0:
            continue
        # THE LATEST WINDOW THAT HAS ACTUALLY STARTED, which under live ingest
        # is simply the latest — events arrive up to now and no further. It is
        # spelled out because REPLAY does not work that way (`replaybus`, and
        # `simulate_live.py` without `--restamp`): there the whole fight's
        # events exist and the cursor moves through them, so taking the last
        # burst outright showed the third window's countdown from the pull
        # timer, counting down five minutes to a mechanic that had not fired.
        bursts = [b for b in reflect_bursts([ts for ts, _c, _a in hits],
                                            window_s) if b[0] <= now_ts]
        if not bursts:
            continue
        start = bursts[-1][0]
        ends_ts = start + window_s
        if now_ts > ends_ts + REFLECT_CLEAR_S:
            continue        # over, and said so for long enough
        # MEMBERSHIP IS THE BURST'S RULE, NOT THE BAR'S. `reflect_bursts` lets
        # a deny land a second or two past the duration (REFLECT_EDGE_S — a log
        # stamps whole seconds), and filtering the tally on `ends_ts` instead
        # would silently drop exactly those casts from the count while
        # `reflect_bursts` still called them part of this window. Two rules for
        # one boundary is how a tally and a countdown come to disagree.
        current = [h for h in hits
                   if start <= h[0] <= start + window_s + REFLECT_EDGE_S]

        # WHAT IT HAS COST, paired cast by cast. A reflected spell returns to
        # the CASTER — 113 of 115 pairings on the fight this was measured on —
        # so the pairing is (this ability, this caster) and not merely "damage
        # the mob did during the window", which would sweep in everything else
        # the boss was doing and read as a far scarier number than the truth.
        taken = 0
        used: dict[tuple, int] = defaultdict(int)
        for ts, caster, ability in current:
            key = (mob, ability, caster)
            back = returns.get(key) or []
            for i in range(used[key], len(back)):
                rts, amount = back[i]
                if rts < ts:
                    continue
                if rts - ts > REFLECT_RETURN_S:
                    break
                taken += amount
                used[key] = i + 1
                break

        out.append({
            "kind": "reflect",
            "source": mob,
            # There is no ability to name — the mob is not casting anything,
            # it is refusing to be cast at — so this names the MECHANIC. It is
            # also the row's identity in the browser (`source|ability`) and in
            # the hand marks, both of which are keyed by name.
            "ability": "Damage reflect",
            "window_s": window_s,
            "started_ts": start,
            "ends_ts": ends_ts,
            "casts": len(current),
            "casters": len({c for _t, c, _a in current}),
            "damage": taken,
            # `next_due_ts` + `period_s` are what the browser's countdown, its
            # drain bar and its ticker all read, and they are reused verbatim
            # so a reflect row needs no second implementation of any of them.
            # The MEANING is the one thing that differs: this is when the
            # window ENDS, not when the next one starts. Nothing predicts the
            # next one — see `docs/live.md`.
            "period_s": float(window_s),
            "period_src": "curated",
            "next_due_ts": ends_ts,
            "first_cast_ts": start,
            "last_cast_ts": start,
            "since_s": max(0, now_ts - start),
            # Fields the AoE rows carry that have no meaning here. Sent as
            # None rather than omitted so one row shape reaches the browser and
            # `kind` is the only thing anybody has to branch on.
            "dtype": None,
            "dtypes": {},
            "reported_s": None,
            "suggested_s": None,
            "several_bodies": None,
            "swiped": False,
            "swipe_verdict": None,
            "swipe_factor": None,
            "normal_period_s": None,
            "last_hit": 0,
            "last_blocked": 0,
            "last_targets": 0,
        })
    out.sort(key=lambda r: (r["started_ts"], r["source"]))
    return out


def build_snapshot(events, logger: str, zone: str | None, start_ts: int,
                   roster: dict[str, str] | None = None,
                   know: Knowledge = NO_KNOWLEDGE,
                   now_ts: int | None = None) -> dict:
    """One pass over the open segment -> the `partial` payload's `fight`.

    `events` are `ParsedEvent`s (unresolved, straight off the parser);
    `roster` maps a lowercased name to a class, so the bars can be colored by
    the same knowledge the raid page uses; `know` is who these names are (see
    `Names` and `pipeline/live.snapshot_context`).

    `now_ts` is the LOG clock — the newest line the uploader has sent, or the
    replay's cursor. It is what lets the payload say a fight is over: the
    writer cannot close the segment for `CLOSE_S` (17s) because a late kill
    line may still join it, but combat itself stopped at `GAP_S`, and a screen
    that keeps a clock running through the difference is telling the raid the
    pull is still going after ACT has called it. Absent, nothing is over — the
    caller has no clock to judge it by.
    """
    roster = roster or {}
    names = Names(logger, events, know)
    actors: dict[str, dict] = defaultdict(lambda: {
        "damage": 0, "heals": 0, "wards": 0, "overheal": 0, "cures": 0,
        "damage_taken": 0, "deaths": 0, "kind": "player",
        # the biggest single line either way — ACT's Max Hit, which is the one
        # number a rate cannot say (a 3M nuke and a steady 3M of DoT ticks are
        # the same DPS) and the reason the raid asks "what did that crit for"
        "max_hit": 0, "max_heal": 0,
    })
    dmg_to_mob: dict[str, int] = defaultdict(int)     # what the raid is fighting
    dmg_from_mob: dict[str, int] = defaultdict(int)   # what is fighting the raid
    per_sec_dmg: dict[int, int] = defaultdict(int)
    per_sec_heal: dict[int, int] = defaultdict(int)
    deficit: dict[str, int] = defaultdict(int)        # reconstructed HP lost, as statsroll does it
    last_ts = start_ts
    # The fight's own end, which is not the last event in it. A segment holds
    # the heals, cures and deaths that land inside the idle window (they are
    # part of the pull and `roll_encounter` counts them), but its LENGTH is
    # damage to damage — `encounters.Segment.end_ts` only advances on a
    # damage/avoid line, so this is the same denominator the fight card gets
    # when the writer commits it a few seconds later.
    last_combat_ts = start_ts

    for ev in events:
        last_ts = max(last_ts, ev.ts)
        if ev.type in ("damage", "avoid"):
            last_combat_ts = max(last_combat_ts, ev.ts)
        src = ev.src
        src_name, src_kind = names.source(src) if src else (None, None)
        tgt_name, tgt_kind = names.target(ev.tgt) if ev.tgt else (None, None)
        amt = ev.amount or 0

        if ev.type == "damage":
            # a cost or a bleed landing on yourself is not damage dealt — the
            # same exclusion statsroll makes, so the live number and the
            # recorded one mean the same thing
            self_hit = bool(ev.flags & F_SELF_FOCUS) or (
                src_name is not None and src_name == tgt_name)
            if src_name and not self_hit:
                a = actors[src_name]
                a["kind"] = src_kind
                a["damage"] += amt
                if amt > a["max_hit"]:
                    a["max_hit"] = amt
                if src_kind == "player":
                    per_sec_dmg[ev.ts] += amt
            if tgt_name and not self_hit:
                t = actors[tgt_name]
                t["kind"] = tgt_kind
                t["damage_taken"] += amt
                if tgt_kind == "player":
                    deficit[tgt_name] += amt
            if not self_hit:
                if src_kind == "player" and tgt_kind == "mob":
                    dmg_to_mob[tgt_name] += amt
                elif src_kind == "mob" and tgt_kind == "player":
                    dmg_from_mob[src_name] += amt

        elif ev.type == "heal":
            if src_name:
                a = actors[src_name]
                a["kind"] = src_kind
                a["heals"] += amt
                if amt > a["max_heal"]:
                    a["max_heal"] = amt
                if src_kind == "player":
                    per_sec_heal[ev.ts] += amt
            if tgt_name and tgt_kind == "player":
                before = deficit[tgt_name]
                if src_name:
                    actors[src_name]["overheal"] += max(0, amt - before)
                deficit[tgt_name] = max(0, before - amt)

        elif ev.type == "ward":
            if src_name:
                a = actors[src_name]
                a["kind"] = src_kind
                a["wards"] += amt

        elif ev.type == "dispel":
            # cures and dispels, one per line, credited to the caster whatever
            # the target was — `statsroll` counts ACT's Cures column this way
            if src_name:
                a = actors[src_name]
                a["kind"] = src_kind
                a["cures"] += 1

        elif ev.type in ("death", "kill"):
            # "X has killed YOU" parses as a death; everything else that kills
            # a player is a kill line with them as the target
            if tgt_name and tgt_kind == "player":
                actors[tgt_name]["kind"] = "player"
                actors[tgt_name]["deaths"] += 1

    duration = max(last_combat_ts - start_ts, 1)
    log_ts = now_ts if now_ts is not None else last_ts
    quiet_s = max(0, log_ts - last_combat_ts)
    players = {n for n, a in actors.items() if a["kind"] == "player"}
    rows = []
    for name, a in actors.items():
        rows.append({
            "name": name,
            "kind": a["kind"],
            "class": roster.get(name.lower()) if a["kind"] == "player" else None,
            "damage": a["damage"],
            "dps": round(a["damage"] / duration, 1),
            "max_hit": a["max_hit"],
            "heals": a["heals"],
            "hps": round(a["heals"] / duration, 1),
            "max_heal": a["max_heal"],
            "wards": a["wards"],
            "overheal": a["overheal"],
            "cures": a["cures"],
            "damage_taken": a["damage_taken"],
            "dtps": round(a["damage_taken"] / duration, 1),
            "deaths": a["deaths"],
        })
    rows.sort(key=lambda r: (-r["damage"], -r["heals"], r["name"]))
    del rows[MAX_ACTORS:]

    t0 = max(start_ts, last_ts - MAX_TIMELINE_S)
    span = range(t0, last_ts + 1)
    raid_dmg = sum(r["damage"] for r in rows if r["kind"] == "player")
    raid_heals = sum(r["heals"] for r in rows if r["kind"] == "player")
    name, is_named = _provisional(dmg_to_mob, dmg_from_mob, names)

    return {
        "zone": zone,
        "started_ts": start_ts,
        "last_ts": last_ts,
        "last_combat_ts": last_combat_ts,
        # the log clock the countdowns and the ended test are read against
        "log_ts": log_ts,
        "quiet_s": quiet_s,
        "ended": quiet_s >= GAP_S,
        "elapsed_s": duration,
        "provisional_name": name,
        "provisional_is_named": is_named,
        "raid": {
            "damage": raid_dmg,
            "dps": round(raid_dmg / duration, 1),
            "heals": raid_heals,
            "hps": round(raid_heals / duration, 1),
            "deaths": sum(r["deaths"] for r in rows if r["kind"] == "player"),
            "raiders": len(players),
        },
        "actors": rows,
        "timeline": {
            "t0": t0,
            "dmg": [per_sec_dmg.get(ts, 0) for ts in span],
            "heal": [per_sec_heal.get(ts, 0) for ts in span],
        },
        # against the LOG clock, not the last cast: a countdown has to keep
        # counting while the raid stands still between pulls
        # A REFLECT ROW GOES FIRST AND IS EXEMPT FROM `MAX_AOES`, which is the
        # one place this breaks the panel's own "rows do not move" rule — and
        # it does not really break it, because that rule protects rows somebody
        # LEARNS BY POSITION over a whole fight. A reflect row exists for 30
        # seconds and then is gone; there is no position to learn, and while it
        # is up it is the only thing on the panel that is true right now.
        # Capping it away behind twelve AoEs would be capping away the row most
        # likely to change what somebody does in the next second.
        "aoes": (_live_reflect(events, names, log_ts)
                 + _live_aoes(events, names, players, log_ts)),
        # the browser drains its own bars between partials, so it needs the
        # same drop rule the payload was built with or a row it is still
        # drawing outlives the one the server has already dropped. BOTH
        # numbers, because the rule has two sides (see the constants) and the
        # browser clock runs ahead of the payload on both of them.
        "aoe_drop_s": OVERDUE_DROP_S,
        "aoe_missed_s": MISSED_S,
    }


def snapshot_payload(events, logger: str, zone: str | None,
                     start_ts: int | None, roster: dict[str, str] | None = None,
                     know: Knowledge = NO_KNOWLEDGE,
                     now_ts: int | None = None) -> dict:
    """The whole `partial` event. `start_ts` None (no open segment) means the
    raid is between pulls — the stream still ticks so the dashboard can dim the
    last fight rather than guess whether it is still connected."""
    return {
        "computed_ts": int(time.time()),
        "fight": (build_snapshot(events, logger, zone, start_ts, roster, know,
                                 now_ts)
                  if events and start_ts is not None else None),
    }
