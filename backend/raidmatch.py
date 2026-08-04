"""The same raid, uploaded by several people.

Everyone in a raid runs their own ACT, so one night can arrive as five parses.
Within ONE character's uploads those collapse by content (`pipeline/zoneruns`
dedupes byte-identical encounters), but two people's logs are not the same
bytes: different subjects, different vantage points, different fights heard.
Both parses are real and neither is a copy, so nothing here merges or deletes
anything — it says WHICH RUNS ARE THE SAME NIGHT and which one to open first.

Three rules decide it, applied to the runs a given viewer can already see:

  zone     — the same zone name, matched NULL-safe. A run with no zone line
             ("Unknown zone") can still match another one, but only on the
             roster, because the place is exactly what it cannot state.
  time     — the windows overlap, give or take CLOCK_SKEW_S. The log's epoch
             prefix is authoritative (`parser/prefix.py`) and comes off the
             raider's own machine, so two clocks in the same raid agree to
             within seconds; two minutes is slack, not a matching rule.
  roster   — enough of the same people (`zone_runs.roster_json`, written by
             `pipeline.zoneruns._roster`). This is the rule that says no: two
             guilds in the same instance zone at the same hour overlap on the
             first two and share nobody.

Precedence, which is the whole point of the feature:

  * YOUR OWN parse wins, always. You uploaded it, it is the one whose numbers
    you can check against what you remember, and it is the only one that keeps
    working if the person who shared theirs leaves the group. That decision is
    the viewer's, so it is applied in the browser (`Home.jsx`) — it cannot be
    baked into the payload, which is cached and shared between viewers.
  * Otherwise the site picks one FOR EVERYONE (`primary`), so two people
    discussing a raid are looking at the same numbers unless one of them says
    otherwise. `_score` is the pick: the parse that covers the most of the
    night, tie-broken toward the oldest upload so the answer is stable across
    reparses and refreshes.

Nothing here is stored. Visibility is decided at read time (`groups.py`), so a
cluster computed at write time would be a fact about somebody else's account —
and it would go stale the moment a share was revoked.
"""

import json

# Slack on either side of the time comparison. Not a fuzzy-match knob: the
# epochs come from different machines, and a raider whose clock is a minute out
# still fought the same boss at the same time.
CLOCK_SKEW_S = 120

# How much of the smaller roster the two have to share. A raid keeps its people
# all night, so real pairs sit near 1.0 — this only has to be above the overlap
# a passing group produces (they turn up in a fight or two, and the roster's
# presence rule has already dropped most of them).
ROSTER_AGREEMENT = 0.34
MIN_SHARED_RAIDERS = 2


def roster_of(run) -> set[str] | None:
    """-> the run's roster, or None when it was never computed (a row written
    before schema v18, until the next startup relink)."""
    raw = run["roster_json"] if not isinstance(run, dict) else run.get("roster_json")
    if not raw:
        return None
    try:
        names = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return {n for n in names if n} if isinstance(names, list) else None


def _get(run, key):
    return run[key] if not isinstance(run, dict) else run.get(key)


def rosters_agree(a: set[str] | None, b: set[str] | None) -> bool | None:
    """-> True / False, or None when one side has no roster to compare. A None
    is not a match on its own: the caller decides what the missing evidence
    costs, and it costs a zone-less run everything."""
    if a is None or b is None or not a or not b:
        return None
    shared = len(a & b)
    need = max(MIN_SHARED_RAIDERS, round(ROSTER_AGREEMENT * min(len(a), len(b))))
    return shared >= need


def same_raid(x, y) -> bool:
    """Are these two runs the same night, logged by different people?"""
    zx, zy = _get(x, "zone"), _get(y, "zone")
    if (zx or None) != (zy or None):
        return False
    start = max(_get(x, "started_ts"), _get(y, "started_ts"))
    end = min(_get(x, "ended_ts"), _get(y, "ended_ts"))
    if end - start < -CLOCK_SKEW_S:
        return False
    agree = rosters_agree(roster_of(x), roster_of(y))
    if agree is None:
        # No roster on one side. A named zone plus an overlapping window is
        # already strong evidence, so the pair stands; an Unknown-zone run has
        # nothing left to stand on and does not.
        return bool(zx)
    return agree


def _score(run) -> tuple:
    """How much of the night a parse holds. Someone who zoned in for the last
    two pulls has a real parse of two pulls; the person who logged all of it is
    what "the raid" means, and is what a stranger should land on."""
    return (_get(run, "encounter_count") or 0,
            _get(run, "combat_s") or 0,
            _get(run, "raider_count") or 0,
            -(_get(run, "id") or 0))


def annotate(runs: list[dict]) -> None:
    """Stamp `raid_key` and `primary` on every run, in place.

    `raid_key` is the cluster's lowest run id — a handle for "these rows are one
    night", stable within a payload and meaningless outside it (it is derived
    from what THIS viewer can see). A run that matches nothing is its own key,
    so the caller has one rule and no special case for the common row.
    """
    parent: dict[int, int] = {r["id"]: r["id"] for r in runs}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # Runs are listed newest first; comparing each against the ones that start
    # near it is enough, because a night that does not overlap in time can never
    # match — no need to compare every pair in a 500-run list.
    order = sorted(runs, key=lambda r: r["started_ts"])
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if b["started_ts"] - a["ended_ts"] > CLOCK_SKEW_S:
                break
            if same_raid(a, b):
                union(a["id"], b["id"])

    clusters: dict[int, list[dict]] = {}
    for r in runs:
        clusters.setdefault(find(r["id"]), []).append(r)
    for key, members in clusters.items():
        best = max(members, key=_score)
        for r in members:
            r["raid_key"] = key
            r["parses"] = len(members)
            r["primary"] = r["id"] == best["id"]


def alternates(conn, visible_sql: str, uid: int | None, run) -> list[dict]:
    """The other people's parses of this run's night, best first — what the raid
    page offers as a switch. Only runs the viewer can already see: this reveals
    no raid, it re-sorts ones they were always allowed to open."""
    rows = conn.execute(
        f"SELECT z.id, z.zone, z.started_ts, z.ended_ts, z.encounter_count, "
        f"z.combat_s, z.raider_count, z.roster_json, z.character_id, "
        f"c.name AS character_name, c.user_id AS owner_id, u.username AS owner_username "
        f"FROM zone_runs z JOIN characters c ON c.id = z.character_id "
        f"JOIN users u ON u.id = c.user_id "
        f"WHERE z.id IN ({visible_sql}) AND z.id <> :rid AND z.zone IS :zone "
        f"AND z.started_ts <= :end AND z.ended_ts >= :start",
        {"uid": uid, "rid": run["id"], "zone": run["zone"],
         "end": run["ended_ts"] + CLOCK_SKEW_S,
         "start": run["started_ts"] - CLOCK_SKEW_S}).fetchall()
    out = [dict(r) for r in rows if same_raid(run, r)]
    out.sort(key=lambda r: (r["owner_id"] != uid, tuple(-v for v in _score(r))))
    for r in out:
        r["mine"] = uid is not None and r["owner_id"] == uid
        r.pop("roster_json", None)
        r.pop("owner_id", None)
    return out
