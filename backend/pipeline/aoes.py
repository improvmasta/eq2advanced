"""Raid-wide AoE detection: which enemy abilities hit the raid, how often
they really landed, and who was covered when they did.

The log never says "this was an AoE". What it says is that one ability from
one source landed on a lot of people at once, so that is the definition here:
a second in which the same ability touched at least MIN_TARGETS players is a
CAST. Everything that ability does for the next few seconds — DoT ticks, the
second wave on a second group — belongs to that same cast, which is what the
clustering threshold is for.

Two numbers per ability, and the point of the tab is the gap between them:

  reported  the timer in ACT's spell-timer list (`backend/refdata/
            act_spell_timers.json`), i.e. what the raid was told to expect
  observed  the shortest interval between two casts that ACTUALLY REPEATS

Observed is the shortest repeating gap rather than the mean or the median
because of how this measurement fails. An AoE that doesn't reach all four
groups is a cast we never see, and a cast we never see makes one gap look like
two — it can only ever make a gap LONGER. So the smallest gap that happens
more than once is the closest thing to the real timer, and one freak short gap
(an interrupt, a second mob wearing the same name) is not enough to claim one.

Trash caveat, stated in the payload rather than hidden: entities are keyed by
NAME, so six "a maven of wisdom" pulling the same AoE read as one mob casting
it six times as often. `instances_hint` flags the giveaway — an observed timer
that is a clean fraction of the reported one, from a source that isn't a named.
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from parser.events import F_ZERO

# a second that touches this many players at once is a raid AoE, not a cleave
MIN_TARGETS = 5
# ticks and second waves inside one cast, when nothing better is known
DEFAULT_CLUSTER_S = 6
# with a reported timer to go on, anything inside this fraction of it is the
# same cast — a 60s AoE does not land twice in 24 seconds
CLUSTER_FRACTION = 0.4
# two gaps this close to each other are the same interval
GAP_TOLERANCE = 0.15
MIN_CASTS = 2

# NOT backend/data/ — .gitignore swallows every `data/`, and a reference file
# that ships empty in the image is worse than no feature at all
_TIMERS_PATH = (Path(__file__).resolve().parent.parent
                / "refdata" / "act_spell_timers.json")


@lru_cache(maxsize=1)
def reported_timers() -> dict[str, dict]:
    """ACT's spell-timer list, keyed by ability name (the same name the log
    prints, which is why it can be joined at all)."""
    try:
        with open(_TIMERS_PATH, encoding="utf-8") as fh:
            return json.load(fh).get("spells", {}) or {}
    except (OSError, ValueError):
        return {}


def _cluster(stamps: list[int], threshold: float) -> list[list[int]]:
    """Consecutive seconds no further apart than `threshold` are one cast."""
    out: list[list[int]] = [[stamps[0]]]
    for ts in stamps[1:]:
        if ts - out[-1][-1] > threshold:
            out.append([ts])
        else:
            out[-1].append(ts)
    return out


def observed_period(gaps: list[int]) -> tuple[float | None, int]:
    """The shortest gap that repeats, averaged over the gaps that agree with
    it, plus how many did. Returns (None, 0) when nothing repeats."""
    for g in sorted(gaps):
        agree = [h for h in gaps if abs(h - g) <= max(1, GAP_TOLERANCE * g)]
        if len(agree) >= 2:
            return round(sum(agree) / len(agree), 1), len(agree)
    return None, 0


def _instances_hint(observed: float | None, reported: int | None, is_named: bool):
    """Several mobs sharing a name look like one mob casting faster. Only
    worth saying when the ratio is close to a whole number of mobs."""
    if not observed or not reported or is_named:
        return None
    ratio = reported / observed
    n = round(ratio)
    if n >= 2 and abs(ratio - n) <= 0.2:
        return n
    return None


def detect(events: list[dict], named_sources: set[str] | None = None) -> list[dict]:
    """events: rows with encounter_id, ts, type, src_name, src_kind, tgt_key,
    tgt_kind, ability, amount, flags — already scoped to one encounter
    selection and already authorized. Returns one row per (source, ability),
    biggest first, each carrying its own casts.
    """
    named_sources = named_sources or set()
    timers = reported_timers()

    # (source, ability) -> encounter -> second -> the players it touched, and how
    by_ability: dict[tuple, dict[int, dict[int, dict]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(
            lambda: {"hit": set(), "avoided": set(), "absorbed": set(), "damage": 0})))
    kinds: dict[tuple, str] = {}
    for ev in events:
        if ev["tgt_kind"] != "player" or not ev.get("ability"):
            continue
        if ev["type"] not in ("damage", "avoid"):
            continue
        if ev["src_kind"] not in ("mob", "other"):
            continue
        key = (ev["src_name"], ev["ability"])
        kinds[key] = ev["src_kind"]
        sec = by_ability[key][ev["encounter_id"]][ev["ts"]]
        who = ev["tgt_key"]
        if ev["type"] == "avoid":
            sec["avoided"].add(who)
        elif ev["flags"] & F_ZERO or not ev["amount"]:
            sec["absorbed"].add(who)
        else:
            sec["hit"].add(who)
            sec["damage"] += ev["amount"] or 0

    out = []
    for (src, ability), fights in by_ability.items():
        reported = (timers.get(ability) or {}).get("timer_s")
        threshold = max(DEFAULT_CLUSTER_S, reported * CLUSTER_FRACTION) if reported \
            else DEFAULT_CLUSTER_S
        casts, gaps = [], []
        for enc_id, seconds in fights.items():
            # only the seconds wide enough to be a raid AoE anchor a cast; the
            # rest ride along inside whichever cast they follow
            wide = sorted(ts for ts, s in seconds.items()
                          if len(s["hit"] | s["avoided"] | s["absorbed"]) >= MIN_TARGETS)
            if not wide:
                continue
            clusters = _cluster(wide, threshold)
            starts = [c[0] for c in clusters]
            # only intervals INSIDE one fight are a recast timer — the wait
            # between two pulls is a raid taking a break, not a cooldown
            gaps += [b - a for a, b in zip(starts, starts[1:])]
            for c in clusters:
                hit, avoided, absorbed, dmg = set(), set(), set(), 0
                for ts in range(c[0], c[-1] + int(threshold) + 1):
                    s = seconds.get(ts)
                    if not s:
                        continue
                    hit |= s["hit"]
                    avoided |= s["avoided"]
                    absorbed |= s["absorbed"]
                    dmg += s["damage"]
                # a player who ate it is not also a player who dodged it
                avoided -= hit
                absorbed -= hit
                casts.append({
                    "encounter_id": enc_id,
                    "ts": c[0],
                    "targets": len(hit | avoided | absorbed),
                    "hit": len(hit),
                    "avoided": len(avoided),
                    "absorbed": len(absorbed),
                    "damage": dmg,
                    "blocked_by": sorted(avoided | absorbed),
                })
        if len(casts) < MIN_CASTS:
            continue
        casts.sort(key=lambda c: c["ts"])
        period, agreed = observed_period(gaps)
        is_named = src in named_sources
        total_targets = sum(c["targets"] for c in casts)
        out.append({
            "source": src,
            "source_kind": kinds[(src, ability)],
            "ability": ability,
            "casts": len(casts),
            "reported_s": reported,
            "observed_s": period,
            "observed_agree": agreed,
            "missed_hint": sum(max(0, round(g / period) - 1)
                               for g in gaps) if period else 0,
            "instances_hint": _instances_hint(period, reported, is_named),
            "fights": len({c["encounter_id"] for c in casts}),
            "median_targets": sorted(c["targets"] for c in casts)[len(casts) // 2],
            "blocked": sum(c["avoided"] + c["absorbed"] for c in casts),
            "blocked_pct": round(
                100 * sum(c["avoided"] + c["absorbed"] for c in casts) / total_targets, 1)
            if total_targets else 0.0,
            "damage": sum(c["damage"] for c in casts),
            "cast_list": casts,
        })
    out.sort(key=lambda r: -r["damage"])
    return out
