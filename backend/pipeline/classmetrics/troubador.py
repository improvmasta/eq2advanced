"""Troubador class metrics.

**Jester's Cap** is the one the raid actually argues about. Census: level 65,
single target, 30s duration on a 30s recast — 25s with the Enhance AA — and
+22.5% to +42% Reuse Speed depending on tier. Duration equals recast, so the
ceiling is one target held at ~100% for the whole fight and the number is a
measure of chain discipline, not of choosing the moment.

The log gives us both halves (`parser/buffs.py`): the cast line names the
caster, the landing line names the target, and both are written for everyone in
chat range — so this works off ANY raider's upload, not just the troubador's.

What it cannot give us is an END. No fade line exists for a beneficial buff, so
a window runs its full Census duration unless something in the log cuts it
short. Two things do, and both are applied here:

  - the fight ending (coverage is clipped to the pull);
  - the target DYING — a buff does not survive a death, and resurrection does
    not bring it back, so the window stops there and only a fresh landing
    starts a new one.

Everything else stays a floor: a cast out of chat range is not in the log at
all, and a landing inside the same second as a second troubador's cast keeps no
caster (see `_pair_buffs`).
"""

from collections import defaultdict

from pipeline.classstats import Column, register

ABILITY = "Jester's Cap"
FALLBACK_DURATION_S = 30.0   # Census says 30s for every tier; used only if the
                             # spell has never been fetched into census_spells


def _coverage(stamps: list[int], windows: list[tuple[int, int]],
              duration: float, deaths: list[int]) -> float:
    """Seconds of `windows` covered by a buff applied at each of `stamps`.

    Overlapping applications are a REFRESH, not two buffs, so the intervals are
    unioned rather than summed — a troubador recasting early must not read as
    140% uptime."""
    covered = 0.0
    for w_start, w_end in windows:
        cursor = w_start
        for ts in stamps:
            start, end = max(ts, w_start), min(ts + duration, w_end)
            if end <= cursor:
                continue                      # already covered by an earlier cast
            # a death ends the buff; the next landing has to start it again
            for d in deaths:
                if start <= d < end:
                    end = d
                    break
            start = max(start, cursor)
            if end > start:
                covered += end - start
                cursor = end
    return covered


def _applications(ctx):
    """Landings of the buff on players, and the deaths that cut them short.
    The lookback is one duration: a cap landed during the pull covers the
    opening of the fight, and that landing belongs to no encounter."""
    duration = ctx.census_duration_s(ABILITY) or FALLBACK_DURATION_S
    rows = ctx.events_around(("buff", "death"), int(duration) + 1)
    lands = defaultdict(list)        # target -> [ts]
    casters = defaultdict(set)       # target -> {caster}
    deaths = defaultdict(list)       # player -> [ts]
    unattributed = 0
    for r in rows:
        if r["type"] == "death":
            if r["tgt"]:
                deaths[r["tgt"]].append(r["ts"])
            continue
        if r["ability"] != ABILITY or r["tgt_kind"] != "player":
            continue
        lands[r["tgt"]].append(r["ts"])
        if r["src"]:
            casters[r["tgt"]].add(r["src"])
        else:
            unattributed += 1
    return duration, lands, casters, deaths, unattributed


@register(
    key="jesters_cap_uptime", cls="troubador",
    label="Jester's Cap uptime",
    blurb="Share of combat time with the cap up. A window ends at 30s, a "
          "death, or the fight.",
    columns=[
        Column("target", "Target"),
        Column("uptime", "Uptime", "pct", "Of combat time"),
        Column("covered", "Covered", "secs"),
        Column("applications", "Applications", "num", "Refreshes included"),
        Column("casters", "Kept by"),
    ],
    needs_events=True,
)
def jesters_cap_uptime(ctx):
    duration, lands, casters, deaths, unattributed = _applications(ctx)
    windows = ctx.windows
    total = float(sum(end - start for start, end in windows)) or 1.0

    rows = []
    for target, stamps in lands.items():
        covered = _coverage(sorted(stamps), windows, duration, sorted(deaths.get(target, ())))
        if not covered:
            continue          # every landing fell between pulls
        rows.append({
            "target": target,
            "uptime": 100.0 * covered / total,
            "covered": covered,
            "applications": len(stamps),
            "casters": ", ".join(sorted(casters.get(target, ()))) or "—",
        })
    rows.sort(key=lambda r: -r["uptime"])

    notes = []
    if not ctx.census_duration_s(ABILITY):
        notes.append(f"No Census row; assumed {FALLBACK_DURATION_S:.0f}s.")
    if unattributed:
        notes.append(f"{unattributed} uncredited — two troubadors, one second.")
    return {"rows": rows, "note": " ".join(notes) or None}


@register(
    key="jesters_cap_casts", cls="troubador",
    label="Jester's Cap casts",
    blurb="Casts seen in chat range — a floor, not a count.",
    columns=[
        Column("actor", "Troubador"),
        Column("casts", "Casts", "num"),
        Column("landed", "Landed", "num", "Landings matched to them"),
        Column("targets", "Targets", "num", "Distinct people capped"),
    ],
    needs_events=True,
)
def jesters_cap_casts(ctx):
    casts = defaultdict(int)
    landed = defaultdict(int)
    targets = defaultdict(set)
    for r in ctx.events(("buff_cast", "buff")):
        if r["ability"] != ABILITY or not r["src"]:
            continue
        if r["type"] == "buff_cast":
            casts[r["src"]] += 1
        else:
            landed[r["src"]] += 1
            if r["tgt"]:
                targets[r["src"]].add(r["tgt"])
    rows = [{"actor": name, "casts": n, "landed": landed.get(name, 0),
             "targets": len(targets.get(name, ()))}
            for name, n in casts.items()]
    # a troubador whose casts were all out of range but whose landings were
    # seen still belongs in the table
    for name, n in landed.items():
        if name not in casts:
            rows.append({"actor": name, "casts": 0, "landed": n,
                         "targets": len(targets.get(name, ()))})
    rows.sort(key=lambda r: -r["casts"])
    return rows


# --------------------------------------------- Perfection of the Maestro ---
#
# PotM prints NOTHING. No cast line, no landing line, no fade — the log was
# checked for all three: the only "augmentation song" casts in a raid night are
# the concentration buffs (77 of them across a five-week log, none within 35s
# of a PotM window), and the landing line names mobs buffing themselves.
#
# What it does leave is its PROC. Census: "On a hostile spell cast this spell
# will cast Precise Note on target of spell" — and exactly one spell in the
# game casts Precise Note, this one. A Precise Note in the log is therefore
# proof that its caster had PotM at that second, and it is the only proof
# there is.
#
# Three consequences, all of them load-bearing:
#
#   - Every number here is a FLOOR. The proc needs the buffed player to cast a
#     hostile spell of a triggering type (poison/mental/magic/heat/divine/
#     disease/cold), so what is proven is where casting and buff overlap. A
#     melee raider with PotM up never procs and cannot appear in the table at
#     all.
#   - The window is MEASURED, not assumed. Census says 20s; Enhance: PotM adds
#     10; and the longest proven run in the reference raids is 31s. 30s it is.
#   - The join tolerance is measured too, and it is the whole ballgame for the
#     overlap column — see JOIN_GAP_S. Read generously and the metric starts
#     inventing coverage longer than the buff can last.
#
# The overlap column is the RoK question asked early. PotM is group-scoped in
# EoF, so a raider covered twice at once is a rarity (26 wasted seconds in a
# ten-fight Vyemm run, and nothing at all in most); when the buff goes
# raid-wide and every troubador casts it at the whole raid, a covered stretch
# longer than one duration IS the second troubador's cast landing on someone
# already buffed. The arithmetic does not change with the expansion, which is
# why there is no era switch here — today the column is quiet because the
# waste is not happening yet.

PROC = "Precise Note"
WINDOW_S = 30.0     # Census 20s + Enhance: PotM (+10s); see above
# How far apart two procs can be and still be read as one covered stretch.
# CALIBRATED, not guessed: across 35,339 stored procs, 95% of consecutive gaps
# inside a window are <= 3s, and 3s is the largest join that does not start
# inventing coverage — runs longer than the buff can possibly last are 0.5% of
# runs at a 3s join and 6.8% at 8s. A bigger tolerance does not find more
# coverage, it manufactures the overlap below.
JOIN_GAP_S = 3
# Only an excess bigger than the join tolerance is real: one bridged gap must
# not be able to produce a double-cover on its own.
OVERLAP_MIN_S = WINDOW_S + JOIN_GAP_S


def _runs(stamps: list[int], gap: int = JOIN_GAP_S) -> list[tuple[int, int]]:
    """Proc times -> the spans they prove, merging anything closer than `gap`.

    A lone proc proves one second, not zero — the log's clock is whole
    seconds, and a buff that fired is a buff that was up."""
    out: list[list[int]] = []
    for t in sorted(set(stamps)):
        if out and t - out[-1][1] <= gap:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(s, e + 1) for s, e in out]


def _window_count(stamps: list[int]) -> int:
    """How many PotM windows touched this player, at least.

    One cast covers at most `WINDOW_S`, so a proc more than that after the
    window's first proc cannot belong to it and proves another cast. Counting
    the covered STRETCHES instead would count how choppy their casting was: a
    caster who pauses twice inside one window has three stretches and one
    buff."""
    count = 0
    opened = None
    for t in sorted(set(stamps)):
        if opened is None or t - opened >= WINDOW_S:
            count += 1
            opened = t
    return count


def _double_covered(runs: list[tuple[int, int]]) -> float:
    """Seconds paid for twice. A run longer than one window cannot come from
    one cast — PotM's 90s recast means a single troubador cannot chain it —
    so the excess is a second cast landing on somebody already covered."""
    wasted = 0.0
    for start, end in runs:
        length = end - start
        if length <= OVERLAP_MIN_S:
            continue
        casts = -(-length // WINDOW_S)          # ceil: how many it must have taken
        wasted += casts * WINDOW_S - length
    return wasted


@register(
    key="potm_coverage", cls="troubador",
    label="Perfection of the Maestro",
    blurb="Proven from Precise Note procs, so it counts only while the buffed "
          "player was casting — a floor, and melee never shows.",
    columns=[
        Column("player", "Player"),
        Column("coverage", "Coverage", "pct", "Of combat time, proven"),
        Column("covered", "Covered", "secs"),
        Column("windows", "Windows", "num", "At least this many casts reached them"),
        Column("longest", "Longest", "secs", "The longest proven run"),
        Column("wasted", "Double-covered", "secs",
               "Covered twice at once — a cast thrown away"),
    ],
    needs_events=True,
)
def potm_coverage(ctx):
    total = float(sum(end - start for start, end in ctx.windows)) or 1.0
    by_player = defaultdict(list)
    for r in ctx.events(("damage",), ability=PROC):
        if r["src_kind"] == "player" and r["src"]:
            by_player[r["src"]].append(r["ts"])

    rows = []
    for name, stamps in by_player.items():
        runs = _runs(stamps)
        covered = float(sum(e - s for s, e in runs))
        wasted = _double_covered(runs)
        rows.append({
            "player": name,
            "coverage": min(100.0, 100.0 * covered / total),
            "covered": covered,
            "windows": _window_count(stamps),
            "longest": max(e - s for s, e in runs),
            "wasted": wasted or None,
        })
    rows.sort(key=lambda r: -r["coverage"])
    return rows
