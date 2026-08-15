"""The Outline: what to actually DO, in an order you can follow.

The Gear tab answers "what should I be chasing"; this answers "what should I be
doing about it". The shortlist is the bridge — you fill it there and consume it
here — so everything on this page is derived from what the reader kept, plus
the expansion's standard work, which does not depend on the shortlist at all.

**A SINGLE ORDERED LIST, AND IT NEVER REORDERS.** Two sections: the prelude
(layer 3, hand-curated — see `refdata/planner_standard.json`) and the body,
ordered by prerequisite and then by level. Phase 3's cluster tags will be a
LENS over this list and will highlight rather than filter, for the same reason
this list is stable: prerequisite order and travel efficiency disagree
constantly, and a planner that satisfies both by reordering produces something
nobody can follow (docs/planner.md).

**TWO QUESTS IN ONE CHAIN CANNOT BE WORKED AT THE SAME TIME**, which is why
`plan_quest_edges` exists and why the sort is a topological one rather than a
sort by level: a level-72 quest that opens a level-70 one has to come first,
and no amount of sorting on the level column will say so.

Reads only. No network, no Census, no `items.ensure` — the same rule the rest
of `/plan` keeps, and here the whole answer is a few hundred rows and a heap.
"""

import heapq
import json
from pathlib import Path

from planner import wiki

STANDARD_FILE = Path(__file__).resolve().parents[1] / "refdata" / "planner_standard.json"

# How far back a prerequisite chain is walked from something you want. Kunark's
# longest are a dozen or so steps; the cap is a loop guard rather than an
# opinion, and a chain that hits it is reported as reaching further back rather
# than silently truncated.
MAX_DEPTH = 40
# The most rows the body will build. A shortlist is a handful of items and the
# answer is normally 20-60 rows; this stops a hand-built URL asking for the
# whole catalog's worth of chains.
MAX_ROWS = 400
# The most shortlist entries a request may carry, for the same reason.
MAX_INPUT = 120


def _standard() -> dict:
    """Layer 3, read from disk on every call.

    It is a few KB and it is edited by hand while somebody is looking at the
    page, so re-reading is the feature: `zone_eras.json` is cached because it
    is consulted per parsed line, and this is consulted once per page load."""
    try:
        return json.loads(STANDARD_FILE.read_text())
    except (OSError, ValueError):
        # A prelude that fails to parse must not take the outline down with
        # it. The body is the part derived from the catalog and still works.
        return {}


def prelude(conn, eras: list[str]) -> list[dict]:
    """The expansion's standard work — the same for everybody, every time.

    It does not depend on the shortlist, which is why an empty shortlist still
    has a useful Outline: what everybody does first in an expansion is not a
    function of what you are chasing.

    A `page` that names a quest the catalog knows is marked `known`, so the
    page can link it; one that does not is still shown. These are claims about
    the game, and the game does not stop being true because a crawl missed a
    page."""
    data = _standard()
    known = {r["page_title"] for r in conn.execute(
        "SELECT page_title FROM plan_quests")}
    out = []
    for era in eras:
        for entry in data.get(era, []):
            page = entry.get("page")
            out.append({
                "era": era, "title": entry.get("title") or "",
                "why": entry.get("why") or "", "detail": entry.get("detail"),
                "page": page, "zone": entry.get("zone"),
                "level": entry.get("level"), "kind": entry.get("kind") or "",
                "known": bool(page) and page in known,
                "wiki": _wiki_url(page) if page else None,
            })
    return out


def _wiki_url(page: str) -> str:
    return f"https://eq2.fandom.com/wiki/{page.replace(' ', '_')}"


def _wanted(conn, eras: list[str], items: list[str],
            sets: list[str]) -> dict[str, list[dict]]:
    """Shortlisted items (and set carriers) -> {source page: what it gets you}.

    A SET IS SHORTLISTED AS THE ADORNMENT, never as the armour it came in, so
    a set on the list resolves to every piece that carries one — you do not
    care which of them you get, you care where any of them drop. That is the
    same distinction the set view exists to make, carried through to the
    outline instead of quietly collapsing back into an item list."""
    keys = [e for e in eras if e in wiki.ERAS] or list(wiki.DEFAULT_ERAS)
    wanted: dict[str, list[dict]] = {}
    rows: list[tuple[dict, str | None]] = []

    for page in items[:MAX_INPUT]:
        row = conn.execute(
            "SELECT page_title, name, tier, slot, dtype, level, set_name "
            "FROM plan_items WHERE page_title=?", (page,)).fetchone()
        if row:
            rows.append((dict(row), None))
    for name in sets[:MAX_INPUT]:
        for row in conn.execute(
                "SELECT page_title, name, tier, slot, dtype, level, set_name "
                "FROM plan_items WHERE set_name=?", (name,)):
            rows.append((dict(row), name))

    for row, via_set in rows:
        got = {
            "page_title": row["page_title"], "name": row["name"],
            "tier": row["tier"], "level": row["level"],
            "slot": wiki.slot_label(row["slot"], row["dtype"]),
            # Which shortlist entry this row is answering. A piece reached
            # through a set is a step towards the ADORNMENT, and saying so is
            # the difference between "you wanted these boots" and "any of these
            # six carry the turquoise you wanted".
            "via_set": via_set,
        }
        for src in conn.execute(
                "SELECT source_page, source, kind, zone, level, detail, era "
                f"FROM plan_sources WHERE page_title=? AND era IN "
                f"({','.join('?' * len(keys))})", (row["page_title"], *keys)):
            entry = wanted.setdefault(src["source_page"], [])
            if not any(g["page_title"] == got["page_title"] and
                       g["via_set"] == via_set for g in entry):
                entry.append(got)
    return wanted


def _quests(conn, pages: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(pages), 400):
        chunk = pages[i:i + 400]
        for r in conn.execute(
                "SELECT * FROM plan_quests WHERE page_title IN "
                f"({','.join('?' * len(chunk))})", chunk):
            out[r["page_title"]] = dict(r)
    return out


def _ancestors(conn, seeds: set[str]) -> tuple[dict[str, set[str]], set[str]]:
    """Walk `prereq` edges back from every wanted quest.

    -> ({page: the wanted quests it is a prerequisite of}, the edges found)

    A quest you want is not one job, it is the chain that ends in it, and a
    plan that lists only the last step of a nine-step line is worse than no
    plan. The walk is breadth-first and bounded by `MAX_DEPTH`; an OR-group is
    followed on EVERY branch, because the outline's job is to show what the
    choice is rather than to make it."""
    need: dict[str, set[str]] = {}
    frontier = {s: {s} for s in seeds}
    seen = set(seeds)
    for _ in range(MAX_DEPTH):
        if not frontier:
            break
        pages = list(frontier)
        rows = []
        for i in range(0, len(pages), 400):
            chunk = pages[i:i + 400]
            rows += list(conn.execute(
                "SELECT from_page, to_page, or_group FROM plan_quest_edges "
                f"WHERE kind='hard' AND to_page IN ({','.join('?' * len(chunk))})",
                chunk))
        nxt: dict[str, set[str]] = {}
        for r in rows:
            for goal in frontier.get(r["to_page"], ()):
                need.setdefault(r["from_page"], set()).add(goal)
            if r["from_page"] not in seen:
                seen.add(r["from_page"])
                nxt.setdefault(r["from_page"], set()).update(
                    frontier.get(r["to_page"], ()))
        frontier = nxt
    return need, seen


def _order(rows: list[dict], edges: list[tuple[str, str]]) -> list[dict]:
    """Prerequisite first, then level. A topological sort, not a sort.

    Kahn's, with a heap on (level, name) so that among everything currently
    doable the lowest-level thing comes first — which is the "then level" half
    of the rule, and is also what interleaves the raid targets (which have no
    prerequisites at all) into the quest chains instead of stacking them at one
    end.

    A cycle cannot be ordered and is not dropped: the wiki has a handful of
    pages that name each other, and those rows come out at the end in level
    order rather than vanishing from a plan somebody is relying on."""
    by_key = {r["key"]: r for r in rows}
    indeg = {k: 0 for k in by_key}
    after: dict[str, list[str]] = {k: [] for k in by_key}
    for a, b in edges:
        if a in by_key and b in by_key and a != b:
            after[a].append(b)
            indeg[b] += 1

    def rank(key: str) -> tuple:
        row = by_key[key]
        return (row["level"] if row["level"] is not None else 999, row["name"])

    heap = [(rank(k), k) for k, n in indeg.items() if not n]
    heapq.heapify(heap)
    out: list[dict] = []
    while heap:
        _, key = heapq.heappop(heap)
        out.append(by_key[key])
        for nxt in after[key]:
            indeg[nxt] -= 1
            if not indeg[nxt]:
                heapq.heappush(heap, (rank(nxt), nxt))
    placed = {r["key"] for r in out}
    out += sorted((by_key[k] for k in by_key if k not in placed),
                  key=lambda r: rank(r["key"]))
    return out


def outline(conn, *, eras: list[str], items: list[str] | None = None,
            sets: list[str] | None = None,
            targets: list[str] | None = None) -> dict:
    """The whole page: the prelude, then the body, then what it could not say.

    `targets` are things wanted for their own sake — a raid mob you are going
    for whatever it drops, or a quest you just want done. They are the rail's
    third kind and they enter the body exactly as a shortlisted item's source
    does, minus the "gets you" line."""
    keys = [e for e in eras if e in wiki.ERAS] or list(wiki.DEFAULT_ERAS)
    wanted = _wanted(conn, keys, items or [], sets or [])
    picked = [t for t in (targets or [])[:MAX_INPUT]]

    # A source page is a quest page or a monster page and the catalog knows
    # which — the quest table is the authority, and anything not in it is a mob
    # that was crawled as a named monster.
    quests = _quests(conn, list(wanted) + picked)
    seeds = {p for p in list(wanted) + picked if p in quests}
    need, _ = _ancestors(conn, seeds)
    chain = _quests(conn, [p for p in need if p not in quests])
    quests.update(chain)

    edges: list[tuple[str, str]] = []
    pages = list(quests)
    for i in range(0, len(pages), 400):
        chunk = pages[i:i + 400]
        edges += [(r["from_page"], r["to_page"]) for r in conn.execute(
            "SELECT from_page, to_page FROM plan_quest_edges WHERE kind='hard' "
            f"AND to_page IN ({','.join('?' * len(chunk))})", chunk)]
    edges = [(a, b) for a, b in edges if a in quests]

    rows: list[dict] = []
    for page, q in quests.items():
        gets = wanted.get(page, [])
        rows.append({
            "kind": "quest", "key": page, "name": q["name"],
            "level": q["level"], "level_text": q["level_text"],
            "zone": q["zone"], "timeline": q["timeline"], "jcat": q["jcat"],
            "difficulty": q["kind"], "diff": q["diff"], "era": q["era"],
            "wiki": _wiki_url(page), "gets": gets,
            # WHY THIS ROW IS ON THE LIST, which is not always "it rewards
            # something you want": most of a chain is steps that reward nothing
            # and exist only to open the one that does.
            "why": ("reward" if gets else
                    "target" if page in picked else "prerequisite"),
            "opens": sorted(quests[g]["name"] for g in need.get(page, ())
                            if g in quests and g != page),
        })

    # Named targets must survive on their own after the item that first exposed
    # them leaves the shortlist. Walk the union, not only `wanted`: a raid mob
    # kept for its own sake has no `gets` entry by definition.
    for page in dict.fromkeys([*wanted, *picked]):
        if page in quests:
            continue
        gets = wanted.get(page, [])
        src = conn.execute(
            "SELECT source, kind, zone, level, detail, era FROM plan_sources "
            f"WHERE source_page=? AND era IN ({','.join('?' * len(keys))}) "
            "LIMIT 1", (page, *keys)).fetchone()
        if not src:
            continue
        rows.append({
            "kind": "target", "key": page, "name": src["source"],
            "level": src["level"], "level_text": None, "zone": src["zone"],
            "timeline": None, "jcat": None, "difficulty": src["kind"],
            "diff": src["detail"], "era": src["era"],
            "wiki": _wiki_url(page), "gets": gets,
            "why": "target" if page in picked else "drop", "opens": [],
        })

    ordered = _order(rows, edges)[:MAX_ROWS]
    return {
        "prelude": prelude(conn, keys),
        "rows": ordered,
        "total": len(rows),
        "eras": keys,
        # What the reader put in that the catalog could not place. A shortlist
        # entry with no source in the selected expansions is not an error and
        # is not silently dropped: it is usually a reader who narrowed the era
        # after shortlisting, and saying so is how they find that out.
        "unplaced": sorted(
            (set(items or []) -
             {g["page_title"] for r in rows for g in r["gets"]}) |
            (set(sets or []) -
             {g["via_set"] for r in rows for g in r["gets"] if g["via_set"]}) |
            (set(targets or []) - {r["key"] for r in rows})
        ),
        "counts": {
            "quests": sum(1 for r in ordered if r["kind"] == "quest"),
            "targets": sum(1 for r in ordered if r["kind"] == "target"),
            "chain": sum(1 for r in ordered if r["why"] == "prerequisite"),
        },
    }
