"""Raid-wide AoE detection: which enemy abilities hit the raid, how often
they really landed, and who was covered when they did.

The log never says "this was an AoE". What it says is that one ability from
one source landed on a lot of people at once, so that is the definition here:
a second in which the same ability touched at least MIN_TARGETS players is a
CAST. Everything that ability does for the next few seconds — DoT ticks, the
second wave on a second group — belongs to that same cast, which is what the
clustering threshold is for.

Reach is how an ability EARNS that definition; it is not the definition. When
the reported-timer list already knows the ability, the raid was told to expect
it BY NAME and there is nothing left for reach to prove: one target is a cast,
and so is a cast that found nobody but a pet. That is not a loosened
threshold, it is a different kind of evidence — and it matters most for
exactly the abilities the raid calls out, because those are the ones that do
not always reach the raid. Measured on a 16-minute Mayong kill, `Soul
Paralysis` landed 11 times and touched five people three times: a reach test
saw three casts, put 598 seconds between two of them, and left a 37-second
countdown reading overdue for most of the fight.

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

# a second that touches this many players at once is a raid AoE, not a cleave.
# The test for an ability the reported-timer list has never heard of; see
# `anchors` for the one it has.
MIN_TARGETS = 5
# Targets that prove the ability went off without being part of what the raid
# covered: a pet eating a cast is evidence the cast happened, and is not a
# raider who took it. So they anchor and they are never in `hit`.
PET_KINDS = ("own_pet", "swarm_pet", "named_pet")
# ticks and second waves inside one cast, when nothing better is known
DEFAULT_CLUSTER_S = 6
# with a reported timer to go on, anything inside this fraction of it is the
# same cast — a 60s AoE does not land twice in 24 seconds
CLUSTER_FRACTION = 0.4
# two gaps this close to each other are the same interval
GAP_TOLERANCE = 0.15
MIN_CASTS = 2
# When this log is confident enough to say the reported timer is wrong.
# Two agreeing gaps is a guess — the tab already marks that "?" — and a
# SUGGESTION is an invitation to go and edit an ACT config, so it asks for
# three. The disagreement has to clear the 15% the tab already highlights AND
# a few seconds flat, because a log stamps seconds and a 20s timer measured
# off second-resolution stamps is allowed to read 21.
SUGGEST_MIN_AGREE = 3
SUGGEST_OFF = 0.15
SUGGEST_MIN_DELTA_S = 3
# A CAST IS A MOMENT; A DAMAGE SHIELD IS A CONDITION, and that is the whole
# test. A shield, an aura or an on-hit proc reaches the raid the same way a
# cast does — five people in one second — so reach cannot tell them apart. How
# LONG it goes on can: an ability that keeps meeting the raid-wide anchor
# second after second is not being cast at the raid, it is a state the raid is
# standing in.
#
# Measured over 60 named fights, 288 minutes of combat, as the median number of
# raid-wide seconds in one burst:
#
#   Caress Feedback (Mayong's shield)     9   longest burst  29s
#   Caress Feedback (D'Lizta)            36   longest burst 149s
#   Royal Decree (Lenya Thex)         33-38   longest burst 232s
#   ---------------------------------------------------------------
#   Stench of Death (widest real AoE)     3
#   Vortex of Darkness, Rumbling of Earth 2
#   Blanket of Eternal Night, Soul Paralysis, Dark Visage, Ydalian Bolt,
#   Regal Backlash, Enthralling Flames    1
#
# The populations do not overlap and there is no borderline case, so the
# threshold sits in the empty middle. A REPORTED timer is exempt: the raid's
# own spell-timer list outranks any shape argument, the same way it outranks an
# observed period everywhere else in this file.
SUSTAINED_RUN = 6

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
    """Consecutive seconds no further apart than `threshold` are one cast.

    Hop by hop, deliberately, and DON'T bound how far a cluster may run from
    its start. The bound is the obvious repair for the one flaw here — a DoT
    tail can walk from one cast into the next and merge them, which is real
    (`Stench of Death` ticks every 3s for ~15s on a 23s cycle and loses about
    a third of its casts that way) — and it trades that flaw for a worse one.
    Measured on nine Mayong kills: `Blanket of Eternal Night` ticks every 6s
    for 76 SECONDS, which is longer than its own ~60s cycle, so any span short
    enough to split Stench chops Blanket's tail into casts that never happened
    — 65 casts became 72 and the measured period fell from 59.8s to 40.3s,
    inventing a "your 60s timer should be 40s" suggestion out of a tail.

    Merging is the failure to prefer. A merged cast makes a gap LONGER, and
    `observed_period` is built to survive exactly that; a split one makes a
    gap SHORTER, and nothing downstream can tell that from a real timer."""
    out: list[list[int]] = [[stamps[0]]]
    for ts in stamps[1:]:
        if ts - out[-1][-1] > threshold:
            out.append([ts])
        else:
            out[-1].append(ts)
    return out


def anchors(seconds: dict[int, dict], reported: int | None) -> list[int]:
    """The seconds that prove a cast happened, newest last.

    Two tests, because there are two kinds of evidence and only one of them is
    about reach. With a reported timer the ability is already on the raid's
    callout list by name, so anything it touched that second will do. Without
    one, reach is the only argument that this was an AoE rather than a cleave,
    a proc or a one-off, and MIN_TARGETS is that argument."""
    if reported:
        return sorted(ts for ts, s in seconds.items() if s["touched"])
    return sorted(ts for ts, s in seconds.items()
                  if len(s["hit"] | s["avoided"] | s["absorbed"]) >= MIN_TARGETS)


def suggest_period(reported: int | None, observed: float | None, agree: int,
                   instances_hint: int | None) -> float | None:
    """The timer this log would put in the ACT config instead of the reported
    one, or None when the reported one is fine or the disagreement has a
    better explanation than a wrong timer.

    `instances_hint` is that better explanation: several mobs sharing a name
    cast on their own timers and read as one mob casting faster, so editing
    the config to match would be wrong twice over."""
    if not reported or not observed or agree < SUGGEST_MIN_AGREE or instances_hint:
        return None
    if abs(observed - reported) < max(SUGGEST_MIN_DELTA_S, SUGGEST_OFF * reported):
        return None
    return observed


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
            lambda: {"hit": set(), "avoided": set(), "absorbed": set(),
                     "touched": set(), "damage": 0})))
    # (source, ability) -> school -> damage. What the raid needs off the row at
    # a glance is not only when it lands but what it lands AS, because that is
    # what decides who can be asked to cover it.
    schools: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    kinds: dict[tuple, str] = {}
    for ev in events:
        if not ev.get("ability"):
            continue
        if ev["type"] not in ("damage", "avoid"):
            continue
        if ev["src_kind"] not in ("mob", "other"):
            continue
        if ev["tgt_kind"] != "player" and ev["tgt_kind"] not in PET_KINDS:
            continue
        key = (ev["src_name"], ev["ability"])
        kinds[key] = ev["src_kind"]
        sec = by_ability[key][ev["encounter_id"]][ev["ts"]]
        who = ev["tgt_key"]
        # evidence the ability went off, whoever it found (see PET_KINDS)
        sec["touched"].add(who)
        if ev["tgt_kind"] != "player":
            continue
        if ev["type"] == "avoid":
            sec["avoided"].add(who)
        elif ev["flags"] & F_ZERO or not ev["amount"]:
            sec["absorbed"].add(who)
        else:
            sec["hit"].add(who)
            sec["damage"] += ev["amount"] or 0
        # Every damage line, absorbed ones included: a cast the raid warded
        # through still names its school, and a zero contributes zero, so this
        # is a breakdown OF `damage` and not a second total beside it. It can
        # still come to LESS — `_pair_wards` folds an absorb into the hit it
        # belongs to, and a line that was fully absorbed named no school to
        # fold — so read it as shares, never as a reconciliation.
        if ev["type"] == "damage" and ev.get("dtype"):
            schools[key][ev["dtype"]] += ev["amount"] or 0

    out = []
    for (src, ability), fights in by_ability.items():
        reported = (timers.get(ability) or {}).get("timer_s")
        threshold = max(DEFAULT_CLUSTER_S, reported * CLUSTER_FRACTION) if reported \
            else DEFAULT_CLUSTER_S
        casts, gaps, runs = [], [], []
        for enc_id, seconds in fights.items():
            # only the seconds that anchor a cast start one; the rest ride
            # along inside whichever cast they follow
            wide = anchors(seconds, reported)
            if not wide:
                continue
            clusters = _cluster(wide, threshold)
            starts = [c[0] for c in clusters]
            # how many anchor seconds each burst is made of — see
            # SUSTAINED_RUN. Not the span: a cast whose ticks straddle a
            # 6-second window is still one moment. Only the unreported branch
            # reads this, which is also the only branch where an anchor second
            # means a RAID-WIDE second (`anchors`), so the shield measurement
            # is still counting what it was calibrated on.
            runs += [len(c) for c in clusters]
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
        # No anchor in any fight is no row at all, and it has to be tested
        # before the reported-timer exemption below rather than left to it:
        # everything past this point indexes into `casts`.
        if not casts:
            continue
        # One cast can only say "that happened" — unless the ability is one the
        # raid was told to expect, in which case one cast plus the list it came
        # from is a countdown, and a first pull is exactly when that is worth
        # the most.
        if len(casts) < MIN_CASTS and not reported:
            continue
        casts.sort(key=lambda c: c["ts"])
        period, agreed = observed_period(gaps)
        is_named = src in named_sources
        instances_hint = _instances_hint(period, reported, is_named)
        total_targets = sum(c["targets"] for c in casts)
        runs.sort()
        run_s = runs[len(runs) // 2] if runs else 1
        by_damage = dict(sorted(schools[(src, ability)].items(),
                                key=lambda kv: (-kv[1], kv[0])))
        out.append({
            "source": src,
            "source_kind": kinds[(src, ability)],
            "ability": ability,
            "casts": len(casts),
            # the shape, and the verdict it carries. The row stays on the
            # audit tab either way — a thing that reached the raid is evidence
            # whatever it turned out to be — but a CONDITION has nothing to
            # count down to and never reaches the countdown panel.
            "run_s": run_s,
            "sustained": bool(not reported and run_s >= SUSTAINED_RUN),
            # what it lands AS: the schools it dealt damage in, biggest first,
            # and the head of that is the pill the panels print
            "dtypes": by_damage,
            "dtype": next(iter(by_damage), None),
            "reported_s": reported,
            "observed_s": period,
            "observed_agree": agreed,
            # the reported timer this log disagrees with strongly enough to
            # say so — a config edit, not a countdown (see `suggest_period`)
            "suggested_s": suggest_period(reported, period, agreed, instances_hint),
            "missed_hint": sum(max(0, round(g / period) - 1)
                               for g in gaps) if period else 0,
            "instances_hint": instances_hint,
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
