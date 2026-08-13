"""What the site has LEARNED about a mob's AoE timers, from every raid on it.

Two questions, one table of evidence (`aoe_cycles`):

  base timer   what the gap between two casts really is, measured off cycles
               nobody was slowing. ACT's list (`refdata/act_spell_timers.json`)
               is where a timer starts, not where it ends: on 8 uploaded Mayong
               kills `Soul Paralysis` runs 43.6s against the list's 37, with 27
               agreeing intervals behind it. A countdown that keeps insisting on
               37 is wrong on purpose.
  swipe        whether a reuse debuff moves this particular ability, and by how
               much. NOT assumed from the tooltip — `Traumatic Swipe` says -50%
               reuse speed, measures ~x1.31 against clean cycles of the same
               ability in the same fight, and does not move `Whirling
               Bladestorm` at all. So it is measured per (mob, ability), and an
               ability with no measurement gets no adjustment.

POOLED SITE-WIDE, and that is a deliberate reading of the sharing rules rather
than an exception to them. A mob's recast is a fact about the GAME — the same
kind of thing as `zone_eras.json` or an item's stats, true for everyone whether
or not they were there — so these rows carry no raider, no roster, no parse and
no run. Nothing here can be traced to whose log it came from, and nothing here
is gated, because there is nothing in it to gate.

WHY IT IS SAFE TO ADOPT A MEASURED NUMBER NOW. Before the debuff was accounted
for, "observed disagrees with reported" had two explanations and no way to pick
one, so the number could only ever be OFFERED (`aoes.suggest_period`, still how
a config edit is proposed). A clean cycle has no such ambiguity left: the mob
was not being slowed, several fights agree, and the two remaining ways to be
wrong are handled where each of them lives — a fight too short to repeat by the
thresholds below, and SEVERAL MOBS SHARING ONE NAME by `aoes.several_bodies`,
which is the one failure more evidence makes worse instead of better.
"""

from __future__ import annotations

# Adoption. A learned base timer REPLACES ACT's number in the countdown, so it
# has to be better evidence than one lucky fight. Two gates, both about the
# ways a measurement is wrong rather than noisy:
#
#   - agreeing intervals, because `observed_period` already survives a MISSED
#     cast (a merged gap is longer, never shorter) and what it cannot survive
#     is having only one of them. Six is comfortably past the three
#     `suggest_period` asks for to merely PRINT a disagreement.
#   - distinct fights, because everything that makes one pull unrepresentative
#     — a mob stunned through a cast, an add wearing the boss's name, a raid
#     that wiped at 40% — is a property of that pull. Two fights is what makes
#     it a timer instead of an anecdote.
MIN_AGREE = 6
MIN_FIGHTS = 2

# TWO RAIDERS' LOGS OF ONE PULL ARE ONE FIGHT, and counting them as two defeats
# `MIN_FIGHTS` precisely where it is supposed to bite: one raid night, uploaded
# by two people, satisfying "two fights is what makes it a timer instead of an
# anecdote" on its own.
#
# `encounters.dup_of` cannot answer this and should not try. It is one
# character's overlapping FILES, and two raiders' parses are not duplicates —
# each is that player's own observation, and merging them would break the one
# thing a zone run is (see `zoneruns._dedupe`). So the notion of "one real pull"
# lives here, where it is needed, and is derived rather than stored.
#
# IDENTITY IS OVERLAP, NOT START TIME. Measured over the corpus: 247 pairs of
# same-named encounters from different characters overlap by more than half the
# shorter fight, and 1,424 overlap by less — the two populations are the same
# pull and adjacent pulls of the same trash, and nothing in between. A start-
# time rule was tried first and gets 92% of them at 15s, but the 19 pairs it
# misses have 100% overlap: a raider who engaged late has a shorter encounter
# sitting entirely inside somebody else's, which is exactly the case a start
# delta is blind to and overlap gets right for free.
PULL_OVERLAP = 0.5

# Whether a swipe moved this ability. The measured factors sit in two clumps
# with nothing between them: x1.29-x1.34 for the abilities that respond (Soul
# Paralysis, Blanket of Eternal Night, Mayong's Touch, measured against clean
# cycles of the same ability in the same fight) against x1.04-x1.13 for the ones
# that do not (Whirling Bladestorm across nine cycles at 98% uptime). So the
# band between them is where "we do not know yet" lives, and a row sits there
# rather than being forced to a verdict — which is exactly the state the panel
# draws as `swiped?` and resolves by watching one more cast.
AFFECTED_AT = 1.15
IMMUNE_UNDER = 1.10
# Both sides of a ratio need enough intervals to be a measurement. Lower than
# the base-timer gate on purpose: this is a comparison of two populations from
# the SAME mob and usually the same fight, so it is not exposed to the
# between-fights variance MIN_FIGHTS exists to cover.
MIN_SIDE = 3


def _period(gaps: list[int]) -> tuple[float | None, int]:
    """The shortest gap that repeats, averaged over the gaps that agree with
    it. `aoes.observed_period`'s rule, and the same reasoning: a missed cast
    can only make a gap longer, so the smallest repeating one is the closest
    thing to the truth."""
    from pipeline.aoes import observed_period
    return observed_period(gaps)


def verdict(factor: float | None, clean_n: int, swiped_n: int) -> str | None:
    """'affected' | 'immune' | None (not enough to say)."""
    if factor is None or clean_n < MIN_SIDE or swiped_n < MIN_SIDE:
        return None
    if factor >= AFFECTED_AT:
        return "affected"
    if factor <= IMMUNE_UNDER:
        return "immune"
    return None


def pull_keys(conn, names: set[str] | None = None) -> dict[int, int]:
    """{encounter id -> the id of the earliest encounter that is the same real
    pull}, over every character on the site.

    Same mob name, and time windows overlapping by more than `PULL_OVERLAP` of
    the shorter one. Transitive by construction — each encounter joins the
    first open group it overlaps — which is what makes three raiders' logs of
    one pull collapse to one key rather than to two.

    `names` narrows it to the mobs the caller is actually deriving, for the
    same reason `learn` takes `sources` — the AoE tab asks about one fight's
    mobs and should not pay for the bestiary. Grouping is per NAME, so a subset
    of names gives exactly the same answer for those names as the full read.

    Cheap enough to do in Python: it is one indexed read per derive, and the
    full-site derive is already cached against the cycle table's row count
    (`learned`)."""
    sql = ("SELECT id, name, started_ts, ended_ts FROM encounters "
           "WHERE name IS NOT NULL AND deleted_ts IS NULL")
    args: list = []
    if names:
        sql += " AND name IN (%s)" % ",".join("?" * len(names))
        args = sorted(names)
    rows = conn.execute(sql + " ORDER BY name, started_ts", args).fetchall()
    key: dict[int, int] = {}
    open_groups: list[tuple] = []       # (anchor id, name, start, end)
    for r in rows:
        span = max(1, r["ended_ts"] - r["started_ts"])
        hit = None
        for i, (anchor, name, start, end) in enumerate(open_groups):
            if name != r["name"]:
                continue
            overlap = min(end, r["ended_ts"]) - max(start, r["started_ts"])
            if overlap > PULL_OVERLAP * min(span, max(1, end - start)):
                hit = i
                break
        if hit is None:
            open_groups.append((r["id"], r["name"], r["started_ts"], r["ended_ts"]))
            key[r["id"]] = r["id"]
        else:
            anchor, name, start, end = open_groups[hit]
            # the group grows to cover what it has absorbed, so a third log
            # that overlaps only the late joiner still lands in one group
            open_groups[hit] = (anchor, name, min(start, r["started_ts"]),
                                max(end, r["ended_ts"]))
            key[r["id"]] = anchor
        # rows are name-then-time ordered, so anything ending before this one
        # started can never match again
        open_groups = [g for g in open_groups
                       if g[1] == r["name"] and g[3] >= r["started_ts"]]
    return key


def learn(conn, sources: set[str] | None = None) -> dict[tuple[str, str], dict]:
    """{(mob, ability) -> what we know}, over every cycle on the site.

    `sources` narrows the read to the mobs a caller actually needs — the live
    meter wants the one boss in front of it, not the bestiary.
    """
    sql = ("SELECT source_name, ability, gap_s, swiped, is_named, encounter_id "
           "FROM aoe_cycles")
    args: list = []
    if sources:
        sql += " WHERE source_name IN (%s)" % ",".join("?" * len(sources))
        args = sorted(sources)

    # A FIGHT IS A PULL, NOT AN ENCOUNTER ROW. Two raiders who both logged the
    # same pull produce two encounters, and counting those as two fights let
    # ONE raid night satisfy `MIN_FIGHTS` by itself — the exact anecdote the
    # gate exists to refuse. Falls back to the encounter's own id for anything
    # `pull_keys` did not see (an unnamed fight), which is the identity this
    # used to have throughout.
    pulls = pull_keys(conn, sources)

    acc: dict[tuple[str, str], dict] = {}
    for r in conn.execute(sql, args):
        key = (r["source_name"], r["ability"])
        d = acc.setdefault(key, {"clean": [], "swiped": [], "fights": set(),
                                 "clean_fights": set(), "is_named": 0})
        (d["swiped"] if r["swiped"] else d["clean"]).append(r["gap_s"])
        pull = pulls.get(r["encounter_id"], r["encounter_id"])
        d["fights"].add(pull)
        if not r["swiped"]:
            d["clean_fights"].add(pull)
        d["is_named"] = max(d["is_named"], r["is_named"])

    out = {}
    for key, d in acc.items():
        clean_s, clean_agree = _period(d["clean"])
        swiped_s, swiped_agree = _period(d["swiped"])
        factor = (round(swiped_s / clean_s, 3)
                  if clean_s and swiped_s else None)
        # A base timer is adopted only from CLEAN cycles across several fights,
        # and never when SEVERAL MOBS SHARING ONE NAME is as good an explanation
        # for it. That last guard is `aoes.several_bodies`' and is called rather
        # than restated: six "a maven of wisdom" pulling the same AoE read as
        # one mob casting it six times as often, and more fights cannot fix that
        # — every one of them counts the same six mobs, so the evidence piles up
        # behind the wrong number and MIN_AGREE makes it worse rather than
        # better. Two halves of The Emerald Halls rumbler had 21 agreeing
        # intervals across 4 fights saying `Rumbling of Earth` is 28.7s against
        # ACT's 50, and this site believed them.
        #
        # It is read HERE, at derive time, and not stored on the cycle rows —
        # the whole reason `aoe_cycles` keeps observations instead of
        # conclusions. Naming a new splitter in the reference file re-decides
        # every fight the site already holds, with no reparse.
        from pipeline.aoes import reported_timers, several_bodies
        rep = (reported_timers().get(key[1]) or {}).get("timer_s")
        bodies = several_bodies(key[0], bool(d["is_named"]), rep, clean_s)
        adopt = bool(clean_s and clean_agree >= MIN_AGREE
                     and len(d["clean_fights"]) >= MIN_FIGHTS
                     and not bodies)
        out[key] = {
            "source": key[0],
            "ability": key[1],
            "base_s": clean_s if adopt else None,
            "base_agree": clean_agree,
            "base_fights": len(d["clean_fights"]),
            "clean_s": clean_s,
            "swiped_s": swiped_s,
            "swipe_factor": factor,
            "swipe_verdict": verdict(factor, clean_agree, swiped_agree),
            "clean_n": len(d["clean"]),
            "swiped_n": len(d["swiped"]),
            "fights": len(d["fights"]),
            "is_named": bool(d["is_named"]),
            # why `clean_s` was measured and still not adopted, when that is
            # the reason — the tab prints it, because "we measured 28.7s and
            # are counting 50" is only honest with the because on the end
            "several_bodies": bodies,
        }
    return out


"""The last full read, and the row count it was taken at.

The cycle table changes only when a session is parsed — rare, and already the
expensive thing — while a live payload asks for this every couple of seconds.
Keyed on the row COUNT rather than a timestamp because that is what a reparse
changes and what `clear_derived` changes back, and because it costs one count
against a table this process is the only writer of."""
_CACHE: dict = {"rows": None, "n": -1}


def learned(conn) -> dict[tuple[str, str], dict]:
    """`learn` over everything, cached until the cycle table changes."""
    n = conn.execute("SELECT COUNT(*) FROM aoe_cycles").fetchone()[0]
    if _CACHE["n"] != n or _CACHE["rows"] is None:
        _CACHE["rows"], _CACHE["n"] = learn(conn), n
    return _CACHE["rows"]


# Where to look when an ability is swiped and nobody knows yet whether it
# responds. Not a countdown and never treated as one — it is the far end of the
# bar an unconfirmed row keeps draining toward once it passes its normal timer,
# and the next cast replaces it with a measurement.
FALLBACK_FACTOR = 1.3


def typical_factor(learned_rows: dict) -> float:
    """The stretch the abilities that DO respond actually show, median.

    Taken from this site's own measurements rather than from the tooltip: the
    ability says -50% reuse speed, and the abilities confirmed to move sit
    between x1.2 and x1.35. Median over the confirmed rows, so one freak
    ratio in a short fight cannot drag it, and `FALLBACK_FACTOR` until there
    are any."""
    fs = sorted(r["swipe_factor"] for r in learned_rows.values()
                if r["swipe_verdict"] == "affected" and r["swipe_factor"])
    return round(fs[len(fs) // 2], 3) if fs else FALLBACK_FACTOR


def timer_for(learned_rows: dict, source: str, ability: str,
              reported: int | None) -> tuple[float | None, str]:
    """The number to count with, and where it came from.

    Order of authority, and it is the point of the whole module: what this site
    MEASURED over several clean fights beats what the uploaded ACT list says,
    and the ACT list beats nothing. `reported` stays the fallback rather than
    being retired, because a mob nobody has parsed enough times yet is exactly
    when the raid's configured number is the best thing available."""
    row = learned_rows.get((source, ability))
    if row and row["base_s"]:
        return row["base_s"], "learned"
    if reported:
        return float(reported), "reported"
    return None, "none"
