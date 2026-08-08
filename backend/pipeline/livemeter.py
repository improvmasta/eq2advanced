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
is a cast. Only casts inside the CURRENT fight count toward an observed
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
                           MIN_TARGETS, observed_period, reported_timers)
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
    has ever heard of. What falls out is what an add's cleave and a boss's
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
                                     "absorbed": set()}))
    for ev in events:
        if ev.type not in ("damage", "avoid") or not ev.ability or not ev.tgt:
            continue
        src = ev.src
        # Only the parser's OWN knowledge excludes a source — `YOU`/`YOUR` and
        # the possessive pet forms. Nothing here filters on name grammar,
        # because that would drop exactly the bosses worth a countdown: live,
        # "Venekor" is indistinguishable from a raider by name alone. The
        # anchor rule below is the real evidence — a raider's green AE hits
        # mobs, so touching MIN_TARGETS RAIDERS in one second is a claim only
        # an enemy ability can make.
        if src is not None and (src.name == logger
                                or src.unit in ("player", "own_pet",
                                                "swarm_pet", "named_pet")):
            continue
        tgt, _kind = names.target(ev.tgt)
        if tgt not in players:
            continue
        # `X is hit by <Effect>` names no caster, and some of the biggest raid
        # AoEs arrive that way — the recorded tab pools them under Unknown and
        # so does this, or a 24-target hit with a 30s timer goes unwatched
        sec = by_ability[(src.name if src else UNKNOWN_SOURCE, ev.ability)][ev.ts]
        if ev.type == "avoid":
            sec["avoided"].add(tgt)
        elif ev.flags & F_ZERO or not ev.amount:
            sec["absorbed"].add(tgt)
        else:
            sec["hit"].add(tgt)

    timers = reported_timers()
    out = []
    for (src, ability), seconds in by_ability.items():
        wide = sorted(ts for ts, s in seconds.items()
                      if len(s["hit"] | s["avoided"] | s["absorbed"]) >= MIN_TARGETS)
        if not wide:
            continue
        reported = (timers.get(ability) or {}).get("timer_s")
        threshold = (max(DEFAULT_CLUSTER_S, reported * CLUSTER_FRACTION)
                     if reported else DEFAULT_CLUSTER_S)
        clusters = [[wide[0]]]
        for ts in wide[1:]:
            if ts - clusters[-1][-1] > threshold:
                clusters.append([ts])
            else:
                clusters[-1].append(ts)
        starts = [c[0] for c in clusters]
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        period, _agree = observed_period(gaps) if gaps else (None, 0)
        # reported wins: it is what the raid was told to expect, and one gap
        # inside one pull is a weak measurement
        period_s, period_src = ((reported, "reported") if reported
                                else (period, "observed") if period else (None, None))
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
        hit, avoided, absorbed = reach[-1]
        # a player who ate it is not also a player who dodged it
        avoided -= hit
        absorbed -= hit
        out.append({
            "source": src,
            "ability": ability,
            "casts": len(starts),
            "last_cast_ts": starts[-1],
            "since_s": max(0, now_ts - starts[-1]),
            "period_s": period_s,
            "period_src": period_src,
            "next_due_ts": starts[-1] + period_s if period_s else None,
            "last_hit": len(hit),
            "last_blocked": len(avoided | absorbed),
            "last_targets": len(hit | avoided | absorbed),
        })
    # soonest first, then the ones with no timer at all
    out.sort(key=lambda r: (r["next_due_ts"] is None, r["next_due_ts"] or 0))
    return out[:MAX_AOES]


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
        "aoes": _live_aoes(events, names, players, log_ts),
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
