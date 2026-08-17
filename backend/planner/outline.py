"""The Outline: selected-item sources, grouped into a compact route list.

The Gear tab answers "what should I be chasing"; this answers where those
selected items come from. It contains only mobs, reward quests, and the hard
prerequisites of those quests. No prelude and no separately-kept targets.

The result is prerequisite-ordered before the frontend groups it into zones.
Travel order and quest order can disagree, so the prerequisite relationship is
kept intact within the compact list.

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

from planner import catalog, wiki

_EPIC_WAYPOINT_FILE = (Path(__file__).resolve().parent.parent / "refdata" /
                       "planner_epic_waypoints.json")
EPIC_WAYPOINTS = json.loads(_EPIC_WAYPOINT_FILE.read_text())["waypoints"]

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


def _wiki_url(page: str) -> str:
    return f"https://eq2.fandom.com/wiki/{page.replace(' ', '_')}"


_PIECE_SLOT_ALIASES = {
    "ears": {"ear"}, "fingers": {"finger"}, "wrists": {"wrist"},
    "shoulder": {"shoulders"}, "shoulders": {"shoulders"},
}


def _piece_matches_item(piece: str, row: dict) -> bool:
    """Whether a carrier item contains this exact slot-specific turquoise."""
    suffix = piece.rsplit(":", 1)[-1].strip().lower()
    slots = {str(row.get("slot") or "").lower(),
             str(row.get("slot2") or "").lower()}
    if suffix == "one handed":
        return bool(slots & {"primary", "secondary"}) and not row.get("two_handed")
    wanted = _PIECE_SLOT_ALIASES.get(suffix, {suffix})
    return bool(slots & wanted)


def _wanted(conn, eras: list[str], items: list[str], sets: list[str],
            class_name: str | None = None) -> tuple[dict[str, list[dict]], set[str]]:
    """Shortlisted items (and exact set-adornment pieces) -> their sources.

    A SET PIECE IS THE ADORNMENT, never the armour it came in. An exact tracked
    `Set: Head` resolves only to Head carrier gear; the real carrier identity
    and examine card stay in the response. Legacy whole-set selections remain
    readable and resolve to every carrier until the reader replaces them."""
    keys = [e for e in eras if e in wiki.ERAS] or list(wiki.DEFAULT_ERAS)
    wanted: dict[str, list[dict]] = {}
    rejected: set[str] = set()
    rows: list[tuple[dict, str | None, str | None]] = []
    catalog_rows = catalog._rows(conn, keys)
    by_page = {row["page_title"]: row for row in catalog_rows}
    set_rows = {}
    set_bonuses = {}
    for row in conn.execute(
            "SELECT name, pieces_json, bonuses_json FROM plan_sets WHERE era IN "
            f"({','.join('?' * len(keys))})", keys):
        pieces = json.loads(row["pieces_json"] or "[]")
        set_rows[row["name"]] = pieces
        set_bonuses[row["name"]] = catalog.normalize_set_bonuses(
            json.loads(row["bonuses_json"] or "[]"))

    def eligible(row) -> bool:
        raw_classes = row.get("classes") or []
        classes = set(raw_classes if isinstance(raw_classes, list)
                      else (value for value in raw_classes.split(",") if value))
        # An empty class field is unknown, not proof that nobody can use it.
        return not class_name or not classes or class_name in classes

    for page in items[:MAX_INPUT]:
        row = by_page.get(page)
        if row and eligible(row):
            rows.append((row, None, None))
        elif row:
            rejected.add(page)
    for selection in sets[:MAX_INPUT]:
        set_name = next((name for name, pieces in set_rows.items()
                         if selection == name or selection in pieces), None)
        if not set_name and any(
                row.get("set_name") == selection for row in catalog_rows):
            # Legacy saved plans tracked the whole set name, and older test or
            # partial catalogs may have carrier rows before their plan_sets row.
            set_name = selection
        selected_piece = selection if set_name and selection != set_name else None
        found = False
        for row in (candidate for candidate in catalog_rows
                    if candidate.get("set_name") == set_name):
            if selected_piece and not _piece_matches_item(selected_piece, row):
                continue
            if eligible(row):
                rows.append((row, set_name, selected_piece))
                found = True
        if not found and set_name and any(
                row.get("set_name") == set_name for row in catalog_rows):
            rejected.add(selection)

    for row, via_set, via_set_piece in rows:
        row["_set_bonuses"] = set_bonuses.get(row.get("set_name"), [])
        got = {
            "page_title": row["page_title"], "name": row["name"],
            "tier": row["tier"], "level": row["level"],
            "slot": wiki.slot_label(row["slot"], row["dtype"]),
            "icon": row["icon"],
            "card": catalog.card(row),
            # Which shortlist entry this row is answering. A piece reached
            # through a set is a step towards the ADORNMENT, and saying so is
            # the difference between "you wanted these boots" and "any of these
            # six carry the turquoise you wanted".
            "via_set": via_set,
            "via_set_piece": via_set_piece,
        }
        for src in conn.execute(
                "SELECT source_page, source, kind, zone, level, detail, era "
                f"FROM plan_sources WHERE page_title=? AND era IN "
                f"({','.join('?' * len(keys))})", (row["page_title"], *keys)):
            entry = wanted.setdefault(src["source_page"], [])
            if not any(g["page_title"] == got["page_title"] and
                       g["via_set"] == via_set and
                       g["via_set_piece"] == via_set_piece for g in entry):
                entry.append(got)
    return wanted, rejected


def _quests(conn, pages: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(pages), 400):
        chunk = pages[i:i + 400]
        for r in conn.execute(
                "SELECT * FROM plan_quests WHERE page_title IN "
                f"({','.join('?' * len(chunk))})", chunk):
            out[r["page_title"]] = dict(r)
    return out


def _epic_plan(conn, seeds: set[str], seed_quests: dict[str, dict],
               class_name: str | None = None) -> tuple[dict[str, dict], list[dict], list[tuple[str, str]], set[str]]:
    """wikq2's canonical epic chain plus its timeline-level requirements."""
    extra_pages: set[str] = set()
    requirements: dict[str, dict] = {}
    canonical_edges: list[tuple[str, str]] = []
    epic_pages: set[str] = set()
    epic_meta: dict[str, dict] = {}
    for seed in seeds:
        quest = seed_quests.get(seed)
        timeline = (quest or {}).get("timeline") or ""
        if "epic weapon" not in timeline.lower():
            continue
        title = timeline if timeline.lower().endswith(" timeline") else f"{timeline} Timeline"
        record = conn.execute(
            "SELECT class_name, quests_json, requirements_json, source_url "
            "FROM plan_epic_timelines WHERE title=?", (title,)).fetchone()
        if not record:
            continue
        if class_name and record["class_name"] != class_name:
            continue
        chain = json.loads(record["quests_json"])
        chain_titles = [row["title"] for row in chain]
        known = _quests(conn, chain_titles)
        # The selected item's source quest is the acquisition boundary. This
        # is stronger than guessing the Fabled/Mythical split from difficulty:
        # some timelines continue with non-raid setup after the Fabled reward,
        # and a few canonical steps are absent from the general quest crawl.
        # Show the complete ordered prefix through what earns this target.
        try:
            included = chain_titles[:chain_titles.index(seed) + 1]
        except ValueError:
            include_raid = quest.get("kind") == "raid"
            included = [page for page in chain_titles
                        if include_raid or (known.get(page) or {}).get("kind") != "raid"]
        extra_pages.update(included)
        epic_pages.update(included)
        canonical_edges.extend(zip(included, included[1:]))
        for order, page in enumerate(included, 1):
            epic_meta[page] = {
                "epic": True, "epic_title": title,
                "epic_order": order,
            }
        first = included[0] if included else seed
        for index, requirement in enumerate(json.loads(record["requirements_json"])):
            linked = requirement.get("quests") or []
            if linked:
                keys = []
                for linked_quest in linked:
                    key = linked_quest["title"]
                    keys.append(key)
                    requirements.setdefault(key, {
                        "kind": "quest", "key": key, "name": key,
                        "level": None, "level_text": None, "zone": None,
                        "timeline": title, "jcat": None, "difficulty": "unknown",
                        "diff": requirement["text"], "era": "rok",
                        "wiki": linked_quest.get("url") or _wiki_url(key), "gets": [],
                        "why": "prerequisite", "opens": [seed],
                        "requirement": True, "requirement_text": requirement["text"],
                        "epic": True, "epic_title": title,
                        "epic_order": index + 1,
                    })
                canonical_edges.extend((key, first) for key in keys)
            else:
                key = f"requirement:{title}:{index}"
                requirements[key] = {
                    "kind": "requirement", "key": key,
                    "name": requirement["text"], "level": None,
                    "level_text": None, "zone": None, "timeline": title,
                    "jcat": None, "difficulty": "unknown", "diff": None,
                    "era": "rok", "wiki": record["source_url"], "gets": [],
                    "why": "prerequisite", "opens": [seed],
                    "requirement": True, "requirement_text": requirement["text"],
                    "epic": True, "epic_title": title,
                    "epic_order": index + 1,
                }
                canonical_edges.append((key, first))
    quests = _quests(conn, list(extra_pages))
    for page, meta in epic_meta.items():
        if page not in quests:
            # The epic export is authoritative for membership and order even
            # when the expansion category crawl never found the quest page.
            # Preserve that known step and stay explicitly silent on facts the
            # snapshot does not carry (zone, level and difficulty).
            quests[page] = {
                "page_title": page, "name": page, "level": None,
                "level_text": None, "zone": None,
                "timeline": meta["epic_title"].removesuffix(" Timeline"),
                "jcat": None, "kind": "unknown", "diff": None,
                "era": "rok",
            }
        quests[page].update(meta)
    return quests, list(requirements.values()), canonical_edges, epic_pages


def _exclude_other_class_epics(conn, wanted: dict[str, list[dict]],
                               quests: dict[str, dict], class_name: str | None,
                               rejected: set[str]) -> None:
    """Drop a seed when the epic snapshot itself proves another class owns it.

    Item class metadata normally catches this first. The timeline is a second,
    independent authority for old/incomplete item rows and prevents falling
    back to the contradictory generic quest-edge walk for somebody else's epic.
    """
    if not class_name:
        return
    candidates: set[str] = set()
    for page, quest in list(quests.items()):
        timeline = quest.get("timeline") or ""
        if "epic weapon" not in timeline.lower():
            continue
        title = timeline if timeline.lower().endswith(" timeline") else f"{timeline} Timeline"
        record = conn.execute(
            "SELECT class_name FROM plan_epic_timelines WHERE title=?", (title,)
        ).fetchone()
        if not record or record["class_name"] == class_name:
            continue
        for got in wanted.pop(page, []):
            candidates.add(got.get("via_set_piece") or got.get("via_set")
                           or got["page_title"])
        quests.pop(page, None)
    remaining = {got.get("via_set_piece") or got.get("via_set") or got["page_title"]
                 for gets in wanted.values() for got in gets}
    rejected.update(candidates - remaining)


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
    of the rule and keeps independent quest chains naturally interleaved.

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


def _questlines(ordered: list[dict], seeds: set[str],
                need: dict[str, set[str]]) -> list[dict]:
    """Connected prerequisite closures as acquisition units.

    One reward quest with fifty-two prerequisites is one thing the reader
    added, not fifty-three peers. Goals whose closures overlap are merged so a
    shared prerequisite appears once and the unit can name every selected item
    the work advances.
    """
    by_key = {row["key"]: row for row in ordered}
    goal_pages: dict[str, set[str]] = {}
    for goal in seeds:
        row = by_key.get(goal)
        if not row or row.get("epic"):
            continue
        pages = {goal}
        pages.update(page for page, goals in need.items() if goal in goals)
        pages &= set(by_key)
        if len(pages) > 1:
            goal_pages[goal] = pages

    components: list[dict[str, set[str]]] = []
    for goal, pages in goal_pages.items():
        touching = [part for part in components if part["pages"] & pages]
        if not touching:
            components.append({"goals": {goal}, "pages": set(pages)})
            continue
        merged = {"goals": {goal}, "pages": set(pages)}
        for part in touching:
            merged["goals"].update(part["goals"])
            merged["pages"].update(part["pages"])
            components.remove(part)
        components.append(merged)

    out = []
    for part in components:
        pages = [row["key"] for row in ordered if row["key"] in part["pages"]]
        goals = [by_key[key] for key in pages if key in part["goals"]]
        targets = []
        seen = set()
        for row in goals:
            for item in row.get("gets") or []:
                identity = item.get("via_set_piece") or item.get("via_set") \
                    or item["page_title"]
                if identity in seen:
                    continue
                seen.add(identity)
                targets.append(item)
        timelines = {by_key[key].get("timeline") for key in pages
                     if by_key[key].get("timeline")}
        out.append({
            "key": f"questline:{'|'.join(sorted(part['goals']))}",
            "pages": pages,
            "goals": sorted(part["goals"]),
            "targets": targets,
            "timeline": next(iter(timelines)) if len(timelines) == 1 else None,
            "count": len(pages),
        })
    return out


def outline(conn, *, eras: list[str], items: list[str] | None = None,
            sets: list[str] | None = None, class_name: str | None = None) -> dict:
    """Selected items -> source mobs and reward quests + quest prerequisites."""
    keys = [e for e in eras if e in wiki.ERAS] or list(wiki.DEFAULT_ERAS)
    wanted_class = (class_name or "").strip().lower()
    if wanted_class not in wiki.SUBCLASSES:
        wanted_class = None
    wanted, ineligible = _wanted(
        conn, keys, items or [], sets or [], wanted_class)
    # A source page is a quest page or a monster page and the catalog knows
    # which — the quest table is the authority, and anything not in it is a mob
    # that was crawled as a named monster.
    quests = _quests(conn, list(wanted))
    _exclude_other_class_epics(conn, wanted, quests, wanted_class, ineligible)
    seeds = {p for p in wanted if p in quests}
    epic_quests, epic_requirements, epic_edges, epic_pages = _epic_plan(
        conn, seeds, quests, wanted_class)
    quests.update(epic_quests)
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
    edges = [(a, b) for a, b in edges
             if not (a in epic_pages and b in epic_pages)]
    edges.extend(epic_edges)

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
                    "prerequisite"),
            "requirement": False,
            "epic": bool(q.get("epic")),
            "epic_title": q.get("epic_title"),
            "epic_order": q.get("epic_order"),
            "start_waypoint": EPIC_WAYPOINTS.get(page) if q.get("epic") else None,
            "opens": sorted(quests[g]["name"] for g in need.get(page, ())
                            if g in quests and g != page),
        })

    rows.extend(epic_requirements)

    for page in wanted:
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
            "why": "drop", "opens": [],
            "requirement": False,
        })

    ordered = _order(rows, edges)[:MAX_ROWS]
    questlines = _questlines(ordered, seeds, need)
    return {
        "rows": ordered,
        "questlines": questlines,
        "total": len(rows),
        "eras": keys,
        # What the reader put in that the catalog could not place. A shortlist
        # entry with no source in the selected expansions is not an error and
        # is not silently dropped: it is usually a reader who narrowed the era
        # after shortlisting, and saying so is how they find that out.
        "unplaced": sorted((
            (set(items or []) -
             {g["page_title"] for r in rows for g in r["gets"]}) |
            (set(sets or []) -
             {g.get("via_set_piece") or g["via_set"]
              for r in rows for g in r["gets"] if g["via_set"]})
        ) - ineligible),
        # Separate from unplaced: these entries exist, but their authoritative
        # item class list rules them out for the character named by the plan.
        "ineligible": sorted(ineligible),
        "counts": {
            "quests": sum(1 for r in ordered if r["kind"] == "quest"),
            "mobs": sum(1 for r in ordered if r["kind"] != "quest"),
            "chain": sum(1 for r in ordered if r["why"] == "prerequisite"),
        },
    }
