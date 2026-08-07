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

from parser.events import F_SELF_FOCUS
from pipeline.aoes import (DEFAULT_CLUSTER_S, CLUSTER_FRACTION, MIN_CASTS,
                           MIN_TARGETS, observed_period, reported_timers)

MAX_ACTORS = 40          # a raid is 24; the tail is mobs and strays
MAX_TIMELINE_S = 900     # the chart scrolls, so older seconds are not sent
MAX_AOES = 12
UNKNOWN_SOURCE = "Unknown"   # the same pool the recorded AoE tab uses

_ARTICLES = ("a ", "an ", "the ")


def _is_mob_name(name: str) -> bool:
    """The grammar half of `subjects.classify_entity_kind`, minus the pet
    branches: this asks what the CREDITED actor is, and a pet's credit goes to
    its owner. Articled or multi-word is a mob; one capitalized token is a
    person (a one-word boss reads as a raider until the rebuild's behavioural
    pass corrects it — see pipeline/refine.py)."""
    low = name.lower()
    return low.startswith(_ARTICLES) or " " in name


def _actor_kind(name: str, unit: str, logger: str) -> str:
    if unit == "player" or name == logger:
        return "player"
    return "mob" if _is_mob_name(name) else "player"


def _provisional(dmg_to: dict[str, int], dmg_from: dict[str, int]) -> tuple[str | None, bool]:
    """What to call a fight that has not ended yet.

    `encounter_label` names a fight after the enemy that took the most damage,
    but it needs resolved entities and a finished segment. This is the same
    idea on names alone, with the fallback that matters early in a pull: for
    the first seconds of a wipe the raid has dealt nothing, and the thing
    hitting them is still the answer."""
    pool = dmg_to or dmg_from
    if not pool:
        return None, False
    name = max(pool, key=lambda n: (pool[n], n))
    low = name.lower()
    # same rule as encounters._is_named_mob, without known_mobs (a behavioural
    # pass the live path never runs)
    named = not low.startswith(("a ", "an ")) and _is_mob_name(name)
    return name, named


def _live_aoes(events, logger: str, players: set[str], now_ts: int) -> list[dict]:
    """Enemy AoEs seen in this fight, with when the next one is due.

    The anchor rule is `pipeline/aoes.py`'s, and the constants are imported
    from it so the two definitions of "a cast" cannot drift apart. What differs
    is the scope: one open segment, no encounter dimension, and no cast list —
    the dashboard wants a countdown, not an audit."""
    # (source, ability) -> second -> the players it touched
    by_ability: dict[tuple, dict[int, set[str]]] = defaultdict(
        lambda: defaultdict(set))
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
        if ev.tgt not in players:
            continue
        # `X is hit by <Effect>` names no caster, and some of the biggest raid
        # AoEs arrive that way — the recorded tab pools them under Unknown and
        # so does this, or a 24-target hit with a 30s timer goes unwatched
        by_ability[(src.name if src else UNKNOWN_SOURCE, ev.ability)][ev.ts].add(ev.tgt)

    timers = reported_timers()
    out = []
    for (src, ability), seconds in by_ability.items():
        wide = sorted(ts for ts, who in seconds.items() if len(who) >= MIN_TARGETS)
        if not wide:
            continue
        reported = (timers.get(ability) or {}).get("timer_s")
        threshold = (max(DEFAULT_CLUSTER_S, reported * CLUSTER_FRACTION)
                     if reported else DEFAULT_CLUSTER_S)
        starts = [wide[0]]
        for ts in wide[1:]:
            if ts - starts[-1] > threshold:
                starts.append(ts)
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
        out.append({
            "source": src,
            "ability": ability,
            "casts": len(starts),
            "last_cast_ts": starts[-1],
            "since_s": max(0, now_ts - starts[-1]),
            "period_s": period_s,
            "period_src": period_src,
            "next_due_ts": starts[-1] + period_s if period_s else None,
        })
    # soonest first, then the ones with no timer at all
    out.sort(key=lambda r: (r["next_due_ts"] is None, r["next_due_ts"] or 0))
    return out[:MAX_AOES]


def build_snapshot(events, logger: str, zone: str | None, start_ts: int,
                   roster: dict[str, str] | None = None) -> dict:
    """One pass over the open segment -> the `partial` payload's `fight`.

    `events` are `ParsedEvent`s (unresolved, straight off the parser);
    `roster` maps a lowercased name to a class, so the bars can be colored by
    the same Census knowledge the raid page uses.
    """
    roster = roster or {}
    actors: dict[str, dict] = defaultdict(lambda: {
        "damage": 0, "heals": 0, "wards": 0, "overheal": 0,
        "damage_taken": 0, "deaths": 0, "kind": "player",
    })
    dmg_to_mob: dict[str, int] = defaultdict(int)     # what the raid is fighting
    dmg_from_mob: dict[str, int] = defaultdict(int)   # what is fighting the raid
    per_sec_dmg: dict[int, int] = defaultdict(int)
    per_sec_heal: dict[int, int] = defaultdict(int)
    deficit: dict[str, int] = defaultdict(int)        # reconstructed HP lost, as statsroll does it
    last_ts = start_ts

    for ev in events:
        last_ts = max(last_ts, ev.ts)
        src = ev.src
        src_name = src.name if src else None
        src_kind = _actor_kind(src_name, src.unit, logger) if src_name else None
        tgt_name = ev.tgt
        tgt_kind = _actor_kind(tgt_name, "unknown", logger) if tgt_name else None
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

        elif ev.type in ("death", "kill"):
            # "X has killed YOU" parses as a death; everything else that kills
            # a player is a kill line with them as the target
            if tgt_name and tgt_kind == "player":
                actors[tgt_name]["kind"] = "player"
                actors[tgt_name]["deaths"] += 1

    duration = max(last_ts - start_ts, 1)
    players = {n for n, a in actors.items() if a["kind"] == "player"}
    rows = []
    for name, a in actors.items():
        rows.append({
            "name": name,
            "kind": a["kind"],
            "class": roster.get(name.lower()) if a["kind"] == "player" else None,
            "damage": a["damage"],
            "dps": round(a["damage"] / duration, 1),
            "heals": a["heals"],
            "hps": round(a["heals"] / duration, 1),
            "wards": a["wards"],
            "overheal": a["overheal"],
            "damage_taken": a["damage_taken"],
            "deaths": a["deaths"],
        })
    rows.sort(key=lambda r: (-r["damage"], -r["heals"], r["name"]))
    del rows[MAX_ACTORS:]

    t0 = max(start_ts, last_ts - MAX_TIMELINE_S)
    span = range(t0, last_ts + 1)
    raid_dmg = sum(r["damage"] for r in rows if r["kind"] == "player")
    raid_heals = sum(r["heals"] for r in rows if r["kind"] == "player")
    name, is_named = _provisional(dmg_to_mob, dmg_from_mob)

    return {
        "zone": zone,
        "started_ts": start_ts,
        "last_ts": last_ts,
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
        "aoes": _live_aoes(events, logger, players, last_ts),
    }


def snapshot_payload(events, logger: str, zone: str | None,
                     start_ts: int | None, roster: dict[str, str] | None = None) -> dict:
    """The whole `partial` event. `start_ts` None (no open segment) means the
    raid is between pulls — the stream still ticks so the dashboard can dim the
    last fight rather than guess whether it is still connected."""
    return {
        "computed_ts": int(time.time()),
        "fight": (build_snapshot(events, logger, zone, start_ts, roster)
                  if events and start_ts is not None else None),
    }
