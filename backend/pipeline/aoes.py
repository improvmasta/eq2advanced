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
it six times as often. `several_bodies` is the whole answer to that and
`instances_hint` is one third of it — see them for what a measurement off
several bodies is and is not allowed to do.
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
_DEBUFFS_PATH = (Path(__file__).resolve().parent.parent
                 / "refdata" / "reuse_debuffs.json")
_SPLITS_PATH = (Path(__file__).resolve().parent.parent
                / "refdata" / "split_mobs.json")
_REFLECT_PATH = (Path(__file__).resolve().parent.parent
                 / "refdata" / "reflect_windows.json")


@lru_cache(maxsize=1)
def reported_timers() -> dict[str, dict]:
    """ACT's spell-timer list, keyed by ability name (the same name the log
    prints, which is why it can be joined at all)."""
    try:
        with open(_TIMERS_PATH, encoding="utf-8") as fh:
            return json.load(fh).get("spells", {}) or {}
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def reuse_debuffs() -> dict[str, dict]:
    """Player abilities that slow an enemy's recast, keyed by ability name."""
    try:
        with open(_DEBUFFS_PATH, encoding="utf-8") as fh:
            shipped = json.load(fh).get("debuffs", {}) or {}
    except (OSError, ValueError):
        shipped = {}
    try:
        from db import get_db
        for row in get_db().execute(
                "SELECT name,config_json FROM timer_mechanics WHERE kind='reuse_debuff'"):
            shipped[row["name"]] = json.loads(row["config_json"])
    except Exception:  # database may not be initialized during tooling imports
        pass
    return shipped


def reuse_debuff_names() -> frozenset[str]:
    """Just the names, for the hot filter on the live path."""
    return frozenset(reuse_debuffs())


@lru_cache(maxsize=1)
def split_mobs() -> dict[str, dict]:
    """Mobs KNOWN to wear one name on several bodies at once — the splitters,
    keyed by the name the log prints (`refdata/split_mobs.json`)."""
    try:
        with open(_SPLITS_PATH, encoding="utf-8") as fh:
            return json.load(fh).get("mobs", {}) or {}
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def reflect_windows() -> dict[str, dict]:
    """Mobs whose damage reflect is worth telling the raid about, keyed by the
    name the log prints (`refdata/reflect_windows.json`).

    AN ALLOWLIST, AND DELIBERATELY NOT A DETECTOR. Nine mobs in the corpus
    reflect something and the measured severity spans two orders of importance:
    Treyloth's costs the caster 79% of their health at p90, Mayong's has never
    passed 26% and the raid does not call it. Detecting "this mob reflects"
    would draw a row for both, and a panel that announces things nobody acts on
    is a panel people stop reading — the same argument that keeps the AoE rows
    on `RAID_FRACTION` rather than on everything `aoes.detect` can see.

    So the ladder here is the one `docs/census-abilities.md` sets for a pet or
    proc label: measurement NOMINATES, a human RULES, and an unlisted mob gets
    no row rather than a guessed one."""
    try:
        with open(_REFLECT_PATH, encoding="utf-8") as fh:
            shipped = json.load(fh).get("mobs", {}) or {}
    except (OSError, ValueError):
        shipped = {}
    try:
        from db import get_db
        for row in get_db().execute(
                "SELECT name,config_json FROM timer_mechanics WHERE kind='reflect_window'"):
            shipped[row["name"]] = json.loads(row["config_json"])
    except Exception:
        pass
    return shipped


# MEMBERSHIP GETS A SECOND OR TWO THAT THE COUNTDOWN DOES NOT.
#
# A log stamps whole seconds, so a 30s state entered at t=0.4 and denied at
# t=30.9 prints its last deny at +31 having lasted 30.5s — the same
# quantization `SUGGEST_MIN_DELTA_S` exists for. Measured across 18 Treyloth
# windows the last deny lands at +28 to +31, and taking `window_s` literally
# split one of the 18 into a real window plus a spurious one-cast fragment.
#
# So the edge is slack on WHICH WINDOW A DENY BELONGS TO and is deliberately
# not slack on the drain: the bar still runs to the documented duration,
# because that is the number the raid is being told. Two seconds cannot weld
# real windows together — Treyloth's are 70-124s apart, and any mob whose
# reflect recurs faster than its own duration has no window to speak of.
REFLECT_EDGE_S = 2


def reflect_bursts(stamps, window_s: int) -> list[list[int]]:
    """Reflect seconds -> one list per window, earliest first.

    THE DURATION IS THE CLUSTERING RULE, which is what makes this different
    from `_cluster` and simpler than it. Everywhere else in this file the gap
    between casts is the unknown being measured, so ticks have to be merged by
    a threshold that is a guess. Here the window length is the CURATED fact
    (`reflect_windows`) and the gap between windows is the accident — so a
    stamp belongs to the open window if it falls within `window_s` of that
    window's START, and the first one that does not opens the next.

    That has a property a gap threshold does not: it cannot invent a window
    that is longer than the mechanic. A merge gap tuned to 8s split Treyloth's
    real windows into spurious one-second fragments on the sparse-casting mobs,
    and one tuned to 12s risked welding two windows together on a mob that
    reflects more often. Anchoring on the start makes both failures impossible
    — a window is exactly as long as it is documented to be, and anything
    outside one is honestly a new window rather than a longer old one."""
    out: list[list[int]] = []
    for ts in sorted(stamps):
        if not out or ts - out[-1][0] > window_s + REFLECT_EDGE_S:
            out.append([ts])
        else:
            out[-1].append(ts)
    return out


def collect_windows(hits) -> dict[str, list[tuple[int, int]]]:
    """`hits`: (mob_name, ability, ts) for player attacks that LANDED on a mob.
    Returns, per mob, when a reuse debuff was on it.

    A landed hit is the whole evidence available, and it is one-sided: the log
    prints a damage line when the debuff arrives and prints NOTHING when it
    goes, so the window is the hit plus the ability's stated duration and there
    is no second line to confirm it. An attack that MISSED never opens one —
    `Traumatic Swipe` that was parried debuffs nothing — which is why this
    takes landed hits rather than casts.

    What it cannot see is a mob that resisted the debuff while eating the hit;
    EQ2 says nothing about that either way. So a window is "the debuff was
    applied", never "the debuff was working", and the difference is exactly
    what `aoelearn` is measuring when it asks whether an ability moved."""
    reg = reuse_debuffs()
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for mob, ability, ts in hits:
        d = reg.get(ability)
        if d and mob:
            out[mob].append((ts, ts + int(d.get("duration_s") or 0)))
    for mob in out:
        out[mob].sort()
    return dict(out)


def debuffed_at(windows: dict, mob: str, ts: int) -> bool:
    return any(a <= ts < b for a, b in windows.get(mob, ()))


def split_cycles(starts: list[int], windows: dict, mob: str):
    """(clean_gaps, swiped_gaps, per-cast flags) for one ability's casts.

    A CYCLE BELONGS TO THE STATE AT THE CAST THAT STARTED IT, and that is a
    measurement, not a modelling preference. The obvious alternative — how much
    of the gap the debuff covered — was tried first and does not separate the
    populations: on a 20-minute Mayong kill with the debuff up about three
    quarters of the time, `Blanket of Eternal Night` ran 60s and 77s cycles
    side by side and the covered FRACTION of the 60s ones (0.62, 0.80) sat
    inside the range of the 77s ones (0.60–0.97), so no threshold on coverage
    could tell them apart. Classified at the cast instead, the same fight gives
    57/60/60/58 against ~77, which is the split the eye already sees in the
    gaps. The mob evidently takes its recast from what is on it when it casts,
    and a debuff landing halfway through a recast does not retune it."""
    flags = [debuffed_at(windows, mob, t) for t in starts]
    clean, swiped = [], []
    for (a, b), f in zip(zip(starts, starts[1:]), flags):
        (swiped if f else clean).append(b - a)
    return clean, swiped, flags


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
                   bodies) -> float | None:
    """The timer this log would put in the ACT config instead of the reported
    one, or None when the reported one is fine or the disagreement has a
    better explanation than a wrong timer.

    `bodies` is one such better explanation, and it is `several_bodies`'
    verdict: several mobs sharing a name cast on their own timers and read as
    one mob casting faster, so editing the config to match would be wrong twice
    over. Any reason at all is disqualifying — this is an errand somebody is
    being sent on, and the bar for sending it is that the number is right.

    A REUSE DEBUFF IS THE OTHER ONE, and it is handled by what callers pass
    rather than by a flag: `observed` must be the CLEAN period, measured off
    cycles nobody was slowing. A fight somebody swiped through has no clean
    period at all, so it proposes nothing, which is the right answer — the
    number it would otherwise have offered is not this mob's timer, it is this
    mob's timer under somebody else's debuff. Today's avatar kill is the case:
    six agreeing gaps at 72.3s against ACT's 45, every one of them cast under a
    swipe that two brigands held for 98% of the fight."""
    if not reported or not observed or agree < SUGGEST_MIN_AGREE or bodies:
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


def several_bodies(source: str, is_named: bool, reported: int | None,
                   observed: float | None) -> str | None:
    """Why this name's gaps may not be ONE MOB'S RECAST, or None if they are.

    THE ASYMMETRY BEHIND THE QUESTION. Everything here keys an enemy by name,
    and the two ways that goes wrong pull in opposite directions: a cast we
    never saw makes a gap LONGER (which `observed_period` is built to survive),
    and a second body wearing the same name makes it SHORTER. So a measurement
    that comes out longer than the configured timer has one explanation, and a
    measurement that comes out shorter has two — the timer is wrong, or that is
    not one mob.

    HOW MANY BODIES A NAME HAS IS GAME KNOWLEDGE, AND IS NOT INFERRED FROM THE
    SHAPE OF A PARSE. That was tried and it is why this docstring is long. The
    rule was "a measurement well under the reported timer, from a source that is
    not the fight's named, is suspect" — and it takes `Ancient Grovebeast`'s
    `Tremerous Stomp` (33.6s against ACT's 40) with it, which is wrong: only one
    grovebeast is ever up. The test was never really about how many mobs there
    are. `is_named` is set from the ENCOUNTER's headline name, so every add and
    every second boss in a room fails it however singular it is, and a wrong ACT
    entry reads exactly like two mobs anyway. A shape argument cannot answer a
    question about the game.

    So two reasons, and the first one is a file:

      splits     `refdata/split_mobs.json` says so — reference data, the same
                 kind of thing as `zone_eras.json`, written down by somebody who
                 knows the encounter. The only reason that holds for an ability
                 no timer list has ever heard of, which is what it is for: the
                 Emerald Halls rumbler splits into two halves and then into six
                 thirds, and `Engulfing Maw` is on nobody's list.
      instances  `_instances_hint`: the measurement is a clean whole fraction of
                 the reported timer. Kept because it is a SIGNATURE and not a
                 direction — half or a third of the configured number, to inside
                 20%, is what N mobs on one timer look like and is not what a
                 mis-typed config entry looks like — and because it can say
                 which N. It predates this function and nothing here loosened
                 it.

    A NAMED IS EXEMPT from `instances` and not from the file: one boss is one
    body by construction, which is why `Soul Paralysis` at 43.6s against ACT's
    37 is adopted — but a splitter can perfectly well be the mob a fight is
    named after.
    """
    if source in split_mobs():
        return "splits"
    if _instances_hint(observed, reported, is_named):
        return "instances"
    return None


def detect(events: list[dict], named_sources: set[str] | None = None) -> list[dict]:
    """events: rows with encounter_id, ts, type, src_name, src_kind, tgt_key,
    tgt_kind, ability, amount, flags — already scoped to one encounter
    selection and already authorized. Returns one row per (source, ability),
    biggest first, each carrying its own casts.
    """
    named_sources = named_sources or set()
    timers = reported_timers()
    # When a reuse debuff was on each mob. Built from the SAME event list —
    # a swipe is a damage line from a player onto a mob, so it is already here
    # and needs no second query and no cast line, which matters because a cast
    # line by somebody else is exactly what this parser drops.
    windows = collect_windows(
        (ev.get("tgt_name"), ev["ability"], ev["ts"]) for ev in events
        if ev.get("ability") and ev["type"] == "damage"
        and ev["src_kind"] == "player" and ev["tgt_kind"] == "mob")

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
        # the same gaps, told apart by whether a reuse debuff was on the mob
        # when the cast that started each one went off (see `split_cycles`)
        clean_gaps, swiped_gaps = [], []
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
            cl, sw, swiped_flags = split_cycles(starts, windows, src)
            clean_gaps += cl
            swiped_gaps += sw
            for c, was_swiped in zip(clusters, swiped_flags):
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
                    # whether this cast started its recast under a debuff —
                    # per cast, so the tab can show which ones are being
                    # compared rather than only the two averages
                    "swiped": bool(was_swiped),
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
        # Two measurements now, and which one is "the" observed period is
        # decided by which cycles exist rather than by preference. The clean
        # one is the mob's own timer and is the only one anything is allowed to
        # act on; the swiped one is the mob's timer under somebody's debuff.
        # A fight that was swiped end to end has only the second — the panel
        # still needs a number to count with, so it gets it, flagged, because a
        # countdown that says "72s, measured while swiped" is useful and one
        # that says nothing is not.
        clean_s, clean_agree = observed_period(clean_gaps)
        swiped_s, swiped_agree = observed_period(swiped_gaps)
        period, agreed = ((clean_s, clean_agree) if clean_s
                          else (swiped_s, swiped_agree) if swiped_s
                          else observed_period(gaps))
        is_named = src in named_sources
        # measured off the clean number, which is the only one it is a ratio TO
        factor = (round(swiped_s / clean_s, 3)
                  if clean_s and swiped_s and clean_agree >= 2 and swiped_agree >= 2
                  else None)
        instances_hint = _instances_hint(period, reported, is_named)
        # …and the wider question that one is a third of: is this name one mob
        # at all? Decided off the CLEAN period, which is the only one that is
        # this mob's own timer to be suspicious of.
        bodies = several_bodies(src, is_named, reported, clean_s)
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
            # the two populations behind that one number, so a reader can see
            # WHY it is what it is instead of taking it
            "clean_s": clean_s,
            "clean_agree": clean_agree,
            "swiped_s": swiped_s,
            "swiped_agree": swiped_agree,
            "swiped_casts": sum(1 for c in casts if c["swiped"]),
            # how far the debuff moved THIS ability, measured, not assumed
            "swipe_factor": factor,
            # true when the number above came from debuffed cycles because
            # there were no clean ones to prefer
            "observed_swiped": bool(not clean_s and swiped_s),
            # the reported timer this log disagrees with strongly enough to
            # say so — a config edit, not a countdown (see `suggest_period`).
            # CLEAN cycles only: a swiped fight proposes nothing.
            "suggested_s": suggest_period(reported, clean_s, clean_agree,
                                          bodies),
            "missed_hint": sum(max(0, round(g / period) - 1)
                               for g in gaps) if period else 0,
            "instances_hint": instances_hint,
            # why this name's gaps may not be one mob's recast (or None) —
            # 'splits' | 'instances', see `several_bodies`
            "several_bodies": bodies,
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
