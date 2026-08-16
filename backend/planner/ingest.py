"""Building the catalog: crawl one expansion's wiki, write `plan_*`.

**Offline, hand-run, never scheduled.** `tools/sync_planner.py` is the only
caller and a person types it — the same rule the wiki ability ingest keeps,
because a crawl that runs itself is a crawl nobody is watching and the wiki is
somebody else's server.

**The catalog is built by INVERTING mobs and quests, not by reading items.**
Item pages have no era and their `obtain` field is blank more often than not;
the monster that drops a thing says `patch = Rise of Kunark`, `diff = epic x4`
and `zone = The Protector's Realm`, and the quest that rewards it says the
same. So the crawl collects what those pages point at and fetches only those
item pages. Source, era and the raid/group/solo split all arrive with the
link — none of them could have been read off the item.

**WHICH mobs is asked BY ZONE, not by expansion**, because the wiki does not
tag mid-expansion content with the expansion — a live-update monster carries
`LU39` and its tier and nothing else. Asking `Category:<era> Named Monsters`
alone left whole zones out of the catalog. `wiki.named_categories` says why and
`zones.in_era` is what makes it answerable.

**And the two inversions can never see a TRASH drop**, since nothing links what
an unnamed mob carries — which is most of what a level-70 broker search
returns. The zone's `Dropped Items` category is the only index of those, and it
is read for the items no named and no quest already accounts for.

Five rounds of fetching, all batched at `gamewiki.BATCH` titles per request:

1. the era's named monsters, from the expansion's category and every zone's —
   drops, zone, difficulty
2. its quests — equipment rewards, level, timeline
3. each zone's dropped-item category, for what neither of those named
4. every item all three named — and every VERSION behind a disambiguation,
   because `Focused Mind Slippers` is two real items at two levels and the
   catalog wants both
5. the adornment sets those items belong to

RoK is roughly 350 monsters and 900 quests over 29 zones, so a full era is a
few hundred requests at a quarter-second apiece — minutes, not hours.
"""

import json
import time

import gamewiki
from planner import wiki

# A page whose title is one of these is a category or a file, not a thing.
_SKIP_PREFIXES = ("category:", "file:", "template:", "image:")

# How many times a page behind a link may itself be a pointer. Two hops is the
# real depth — a crate naming a disambiguation naming its versions — and the
# third is slack rather than a case anybody has seen. It is a bound and not a
# budget: the loop stops the moment a round finds nothing new.
_FOLLOW_ROUNDS = 3


def _titles(links) -> list[str]:
    return sorted({t.strip() for t in links
                   if t and not t.strip().lower().startswith(_SKIP_PREFIXES)})


def _fetch_all(titles: list[str], fetch, progress=None, label: str = "") -> dict[str, str]:
    """{title -> wikitext} for any number of titles, batched and paced."""
    out: dict[str, str] = {}
    ordered = sorted(set(titles))
    for i in range(0, len(ordered), gamewiki.BATCH):
        out.update(fetch(ordered[i:i + gamewiki.BATCH]))
        if progress:
            progress(label, min(i + gamewiki.BATCH, len(ordered)), len(ordered))
        if i + gamewiki.BATCH < len(ordered):
            time.sleep(gamewiki.PAUSE_S)
    return out


def crawl(era: str, fetch=gamewiki.fetch_wikitext,
          members=gamewiki.category_members, progress=None) -> dict:
    """One expansion -> everything the catalog needs, parsed but not stored.

    `fetch` and `members` are parameters so tests drive this from recorded
    fixtures and never touch the live wiki — the same rule `gamewiki.sync_aas`
    follows."""
    if era not in wiki.ERAS:
        raise ValueError(f"unknown era {era!r}; known: {sorted(wiki.ERAS)}")
    cats = wiki.CATEGORIES[era]
    pages_read = 0

    # --- 1 & 2: the two inversions -------------------------------------
    # ASKED BY ZONE AS WELL AS BY EXPANSION. The wiki files a mid-expansion
    # monster under its live update and never under the expansion, so the
    # expansion category alone misses whole zones — see `wiki.named_categories`.
    mob_titles = _titles(
        t for cat in wiki.named_categories(era) for t in members(cat))
    mob_pages = _fetch_all(mob_titles, fetch, progress, "monsters")
    pages_read += len(mob_pages)
    mobs = [m for title, text in mob_pages.items()
            if (m := wiki.parse_named(title, text))]

    quest_titles = _titles(members(cats["quests"]))
    quest_pages = _fetch_all(quest_titles, fetch, progress, "quests")
    pages_read += len(quest_pages)
    quests = [q for title, text in quest_pages.items()
              if (q := wiki.parse_quest(title, text))]

    # link as WRITTEN -> the sources that named it. Kept by link rather than by
    # resolved item because a disambiguation resolves to several items and all
    # of them come from that mob.
    wanted: dict[str, list[dict]] = {}
    for mob in mobs:
        for link in mob["drops"]:
            wanted.setdefault(link, []).append({
                "source_page": mob["page_title"], "source": mob["name"],
                "kind": mob["kind"], "zone": mob["zone"], "level": mob["level"],
                "detail": mob["diff"],
                "era": wiki.era_of_patch(mob["era"]) or era,
            })
    for quest in quests:
        for link in quest["rewards"]:
            wanted.setdefault(link, []).append({
                "source_page": quest["page_title"], "source": quest["name"],
                "kind": "quest", "zone": quest["zone"], "level": quest["level"],
                "detail": quest["timeline"],
                "era": wiki.era_of_patch(quest["era"]) or era,
            })

    # --- 2b: the world drops the two inversions cannot reach --------------
    #
    # A NAMED page links what it drops; nothing links what a trash mob drops,
    # and that is most of what a level-70 broker search returns. The zone's
    # `Dropped Items` category is the only index of it — 1,611 pages across
    # EoF's zones and 1,345 across RoK's (measured 2026-08-16).
    #
    # Only for items no named and no quest already accounts for. A drop that
    # HAS a monster gets a better answer from the monster, and adding "…and it
    # is also in this zone's category" beside it would be a second row saying
    # less. Counted, because the size of this set is the size of the gap the
    # expansion categories left.
    zone_drops = 0
    drop_cats = wiki.drop_categories(era)
    for i, (cat, zone_row) in enumerate(drop_cats, 1):
        src = wiki.zone_source(zone_row, era)
        for link in _titles(members(cat)):
            if link in wanted:
                continue
            wanted[link] = [dict(src)]
            zone_drops += 1
        if progress:
            progress("zones", i, len(drop_cats))

    # --- 3: the items, and the versions behind a disambiguation ---------
    item_pages = _fetch_all(_titles(wanted), fetch, progress, "items")
    pages_read += len(item_pages)
    # WHAT A LINK POINTS AT IS OFTEN NOT THE ITEM, in two different ways, and
    # both are followed the same way: the pages behind it are taken and all of
    # them inherit the source that named the pointer.
    #
    # A DISAMBIGUATION is the common case — `Focused Mind Slippers` points at
    # `(Level 78)` and `(Level 80)`, which are two real items. (`items.py`
    # picks the first version instead, because there it is resolving ONE logged
    # drop and here we are building a catalog.)
    #
    # A CRATE is the other, and it is where the set armour lives. The mob drops
    # `Faydwer Cloth Pattern: Head` and you unpack one of three hoods out of
    # it — of which exactly one carries Reuse Speed. The crate is not equipment
    # and is correctly refused by `parse_equip`, so before this the armour
    # behind it was reachable from nothing.
    #
    # Followed until nothing new appears rather than once, because a crate can
    # name a disambiguation and that is two hops from the mob.
    origin: dict[str, str] = {}            # a page -> the link that led to it
    frontier = dict(item_pages)
    for _ in range(_FOLLOW_ROUNDS):
        found: dict[str, str] = {}
        for title, text in frontier.items():
            came_from = origin.get(title, title)
            behind = (_titles(wiki.links(text)) if gamewiki.is_disambiguation(text)
                      else wiki.crate_contents(text))
            for link in behind:
                if link not in item_pages and link not in found:
                    found[link] = came_from
        if not found:
            break
        pages = _fetch_all(sorted(found), fetch, progress, "versions")
        pages_read += len(pages)
        item_pages.update(pages)
        origin.update(found)
        frontier = pages

    items: dict[str, dict] = {}
    sources: list[dict] = []
    over_cap = 0
    for title, text in item_pages.items():
        row = wiki.parse_equip(title, text)
        if not row:
            continue                       # a pattern, a recipe, a pointer page
        named_by = wanted.get(origin.get(title, title))
        if not named_by:
            continue                       # a version nothing actually pointed at
        # An item above an expansion's level cap cannot be equipped in it, so
        # that SOURCE is dropped rather than the item — a page rewritten for a
        # live revamp is the common cause, and the same item may still have an
        # honest source in another era. See `wiki.ERA_CAP`.
        keep = [src for src in named_by
                if not row["level"]
                or row["level"] <= wiki.ERA_CAP.get(src["era"], 999)]
        if not keep:
            over_cap += 1
            continue
        sources += [{**src, "page_title": title} for src in keep]
        items[title] = row

    # --- 4: the adornment sets --------------------------------------------
    set_names = sorted({r["set_name"] for r in items.values() if r["set_name"]})
    # The armour set page is a stub that transcludes a separate `(Adornment
    # Set)` page carrying the tiers, so that is the one asked for first — see
    # docs/planner.md. Both titles go in one batch; whichever parses answers.
    set_titles = [f"{n} (Adornment Set)" for n in set_names] + set_names
    set_pages = _fetch_all(set_titles, fetch, progress, "sets") if set_names else {}
    pages_read += len(set_pages)
    sets: dict[str, dict] = {}
    for title, text in set_pages.items():
        parsed = wiki.parse_adorn_set(title, text)
        if not parsed:
            continue
        # `(Adornment Set)` wins over the stub when both parse.
        prev = sets.get(parsed["name"])
        if prev and len(prev["bonuses"]) >= len(parsed["bonuses"]):
            continue
        sets[parsed["name"]] = parsed

    return {
        "era": era,
        "items": list(items.values()),
        "sources": sources,
        "sets": list(sets.values()),
        # THE OUTLINE'S SPINE, off pages the crawl already had to read. Every
        # quest is kept, not only the ones that reward gear: a quest with
        # nothing on it is still the step that unlocks the one that has
        # something. Both link directions come along, and which of them
        # survives is decided in `store` — an edge is only real once both ends
        # are quests this catalog knows.
        "quests": quests,
        "edges": _quest_edges(quests),
        "mobs": len(mobs),
        "quest_count": len(quests),
        "pages": pages_read,
        "over_cap": over_cap,
        # How many item pages entered on a ZONE's category alone. It is the
        # measure of what the two inversions cannot see, so it is reported
        # rather than folded into the item count.
        "zone_drops": zone_drops,
    }


def _quest_edges(quests: list[dict]) -> list[dict]:
    """Every `prereq`/`next` claim on these pages as (from, to) pairs.

    Both directions are read because the wiki fills them independently: a chain
    is often written forward on one page and backward on the next, and the pair
    that agrees simply lands on the same row twice. Nothing is resolved here —
    a title that names no quest we know is dropped in `store`, where the whole
    era is on hand to say so."""
    out: list[dict] = []
    for q in quests:
        page = q["page_title"]
        for group in q["prereq"]:
            out.append({"to": page, "titles": group})
        for group in q["next"]:
            # A forward pointer names ONE quest; a group of alternatives here
            # would mean "this opens either of two", which is not a thing the
            # field says. Each becomes its own edge into that quest.
            out += [{"to": title, "titles": [page]} for title in group]
    return out


ITEM_UPSERT = (
    "INSERT INTO plan_items (page_title, name, census_id, era, slot, slot2, "
    "level, tier, dtype, wtype, classes, flags, adorns_json, set_name, "
    "stats_json, effects, effect_desc, icon, fetched_ts) VALUES "
    "(:page_title,:name,:census_id,:era,:slot,:slot2,:level,:tier,:dtype,"
    ":wtype,:classes,:flags,:adorns_json,:set_name,:stats_json,:effects,"
    ":effect_desc,:icon,:fetched_ts) "
    "ON CONFLICT(page_title) DO UPDATE SET name=excluded.name, "
    "census_id=excluded.census_id, era=excluded.era, slot=excluded.slot, "
    "slot2=excluded.slot2, level=excluded.level, tier=excluded.tier, "
    "dtype=excluded.dtype, wtype=excluded.wtype, classes=excluded.classes, "
    "flags=excluded.flags, adorns_json=excluded.adorns_json, "
    "set_name=excluded.set_name, stats_json=excluded.stats_json, "
    "effects=excluded.effects, effect_desc=excluded.effect_desc, "
    "icon=excluded.icon, fetched_ts=excluded.fetched_ts")

SOURCE_UPSERT = (
    "INSERT INTO plan_sources (page_title, source_page, source, kind, era, "
    "zone, level, detail) VALUES "
    "(:page_title,:source_page,:source,:kind,:era,:zone,:level,:detail) "
    "ON CONFLICT(page_title, source_page) DO UPDATE SET source=excluded.source, "
    "kind=excluded.kind, era=excluded.era, zone=excluded.zone, "
    "level=excluded.level, detail=excluded.detail")

QUEST_UPSERT = (
    "INSERT INTO plan_quests (page_title, name, era, level, level_text, zone, "
    "timeline, jcat, diff, kind, fetched_ts) VALUES "
    "(:page_title,:name,:era,:level,:level_text,:zone,:timeline,:jcat,:diff,"
    ":kind,:fetched_ts) "
    "ON CONFLICT(page_title) DO UPDATE SET name=excluded.name, era=excluded.era, "
    "level=excluded.level, level_text=excluded.level_text, zone=excluded.zone, "
    "timeline=excluded.timeline, jcat=excluded.jcat, diff=excluded.diff, "
    "kind=excluded.kind, fetched_ts=excluded.fetched_ts")

EDGE_UPSERT = (
    "INSERT INTO plan_quest_edges (from_page, to_page, era, kind, or_group) "
    "VALUES (:from_page,:to_page,:era,:kind,:or_group) "
    "ON CONFLICT(from_page, to_page, kind) DO UPDATE SET era=excluded.era, "
    "or_group=excluded.or_group")

SET_UPSERT = (
    "INSERT INTO plan_sets (name, page_title, era, level, pieces_json, "
    "bonuses_json, fetched_ts) VALUES "
    "(:name,:page_title,:era,:level,:pieces_json,:bonuses_json,:fetched_ts) "
    "ON CONFLICT(name) DO UPDATE SET page_title=excluded.page_title, "
    "era=excluded.era, level=excluded.level, pieces_json=excluded.pieces_json, "
    "bonuses_json=excluded.bonuses_json, fetched_ts=excluded.fetched_ts")


# A CRAWL THAT CAME BACK EMPTY MUST NOT EMPTY THE CATALOG.
#
# `store` reconciles, which means it DELETES — that is what lets a correction
# land, and it is safe exactly as long as a person is watching the run. Once
# this is on a schedule nobody is, and a rate limit, a redirect loop or an hour
# of Fandom being unhappy comes back as "the wiki no longer says any of this"
# and takes the catalog with it. So a crawl that collapses is refused rather
# than written, and the operator is told. A real itemization change never halves
# an expansion; a broken fetch always does.
COLLAPSE_RATIO = 0.6


class CrawlCollapsed(RuntimeError):
    """A crawl returned so much less than the last one that it is not credible."""


def store(conn, crawled: dict, force: bool = False) -> dict:
    """Write one crawl, and RECONCILE that era against it.

    Upsert, then delete this era's source rows the crawl did not produce, then
    delete items left with no source at all. Reconciling rather than wiping is
    what lets two eras share the table: a re-sync of RoK must not take an EoF
    item's rows with it. Reconciling rather than only inserting is what lets a
    correction land — a drop removed from a mob page, or an item that the new
    level-cap rule says was never in this era, has to be able to LEAVE.

    An item's own `era` is the expansion it was INTRODUCED in — the earliest
    one that has it — and the era a reader FILTERS on lives on the source row.
    So a re-sync of RoK never rewrites an EoF item's introduction, and an EoF
    item that also drops in RoK is visible to both readers."""
    now = int(time.time())
    era = crawled["era"]
    rank = _era_rank(era)
    items = []
    for row in crawled["items"]:
        prev = conn.execute("SELECT era FROM plan_items WHERE page_title=?",
                            (row["page_title"],)).fetchone()
        introduced = era
        if prev and _era_rank(prev["era"]) < rank:
            introduced = prev["era"]
        items.append({
            "page_title": row["page_title"], "name": row["name"],
            "census_id": row["census_id"], "era": introduced,
            "slot": row["slot"], "slot2": row["slot2"], "level": row["level"],
            "tier": row["tier"], "dtype": row["dtype"], "wtype": row["wtype"],
            "classes": ",".join(row["classes"]) or None, "flags": row["flags"],
            "adorns_json": json.dumps(row["adorns"], separators=(",", ":")),
            "set_name": row["set_name"],
            "stats_json": json.dumps(row["stats"], separators=(",", ":")),
            "effects": row["effects"], "effect_desc": row["effect_desc"],
            "icon": row["icon"], "fetched_ts": now,
        })
    prev = conn.execute("SELECT items FROM plan_syncs WHERE era=?",
                        (era,)).fetchone()
    # `max(1, …)` because a crawl that returns NOTHING where there was
    # something is always the network and never the wiki, however small the
    # catalog was.
    had = prev["items"] if prev else 0
    floor = max(1, int(had * COLLAPSE_RATIO)) if had else 0
    if not force and len(items) < floor:
        raise CrawlCollapsed(
            f"{era}: crawl returned {len(items)} items against "
            f"{prev['items']} last time — refusing to reconcile. Re-run when "
            f"the wiki is answering, or pass force=True if the drop is real.")
    known = {r["page_title"] for r in items}
    sources = [s for s in crawled["sources"] if s["page_title"] in known]
    sets = [{
        "name": s["name"], "page_title": s["page_title"], "era": era,
        "level": s["level"],
        "pieces_json": json.dumps(s["pieces"], separators=(",", ":")),
        "bonuses_json": json.dumps(s["bonuses"], separators=(",", ":")),
        "fetched_ts": now,
    } for s in crawled["sets"]]
    quests = [{
        "page_title": q["page_title"], "name": q["name"], "era": era,
        "level": q["level"], "level_text": q["level_text"], "zone": q["zone"],
        "timeline": q["timeline"], "jcat": q["jcat"], "diff": q["diff"],
        "kind": q["diff_kind"], "fetched_ts": now,
    } for q in crawled["quests"]]
    seen = {(s["page_title"], s["source_page"]) for s in sources}
    with conn:
        conn.executemany(ITEM_UPSERT, items)
        conn.executemany(SOURCE_UPSERT, sources)
        stale = [(p, sp) for p, sp in conn.execute(
            "SELECT page_title, source_page FROM plan_sources WHERE era=?", (era,))
            if (p, sp) not in seen]
        conn.executemany(
            "DELETE FROM plan_sources WHERE page_title=? AND source_page=?", stale)
        # An item with no source left is not reachable in any era — it is not
        # hidden, it is gone from the game's own record. The catalog is a cache
        # of a crawl, so it says what the last crawl said.
        conn.execute("DELETE FROM plan_items WHERE page_title NOT IN "
                     "(SELECT page_title FROM plan_sources)")
        conn.execute("DELETE FROM plan_sets WHERE era=? AND name NOT IN "
                     "(SELECT set_name FROM plan_items WHERE set_name IS NOT NULL)",
                     (era,))
        if sets:
            conn.executemany(SET_UPSERT, sets)
        conn.executemany(QUEST_UPSERT, quests)
        crawled_quests = {q["page_title"] for q in quests}
        conn.executemany(
            "DELETE FROM plan_quests WHERE page_title=?",
            [(r["page_title"],) for r in conn.execute(
                "SELECT page_title FROM plan_quests WHERE era=?", (era,))
             if r["page_title"] not in crawled_quests])
        edge_report = _reconcile_edges(conn, era, crawled["edges"])
        conn.execute(
            "INSERT INTO plan_syncs (era, items, sources, sets, quests, edges, "
            "pages, synced_ts) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(era) DO UPDATE SET items=excluded.items, "
            "sources=excluded.sources, sets=excluded.sets, "
            "quests=excluded.quests, edges=excluded.edges, pages=excluded.pages, "
            "synced_ts=excluded.synced_ts",
            (era, len(items), len(sources), len(sets), len(quests),
             edge_report["edges"],
             crawled["pages"], now))
    return {"era": era, "items": len(items), "sources": len(sources),
            "sets": len(sets), "pages": crawled["pages"],
            "mobs": crawled["mobs"], "quests": len(quests),
            "edges": edge_report["edges"],
            "dangling": edge_report["dangling"],
            "over_cap": crawled["over_cap"],
            "zone_drops": crawled["zone_drops"]}


def _reconcile_edges(conn, era: str, claims: list[dict]) -> dict:
    edges, dangling = _resolve_edges(conn, era, claims)
    conn.executemany(EDGE_UPSERT, edges)
    live = {(e["from_page"], e["to_page"]) for e in edges}
    conn.executemany(
        "DELETE FROM plan_quest_edges WHERE from_page=? AND to_page=?",
        [(f, t) for f, t in conn.execute(
            "SELECT from_page, to_page FROM plan_quest_edges WHERE era=?", (era,))
         if (f, t) not in live])
    return {"edges": len(edges), "dangling": dangling}


def reconcile_edges(conn, era: str, claims: list[dict]) -> dict:
    """Resolve one crawl's graph again after all requested eras are stored.

    A RoK quest can name an EoF prerequisite. When both eras are synced in one
    command, the dependent era may be stored first, while the older quest is
    not in the database yet. The ordinary store still resolves what it can;
    this second cheap pass runs after every crawl and closes those cross-era
    edges without fetching a single page again.
    """
    with conn:
        report = _reconcile_edges(conn, era, claims)
        conn.execute("UPDATE plan_syncs SET edges=? WHERE era=?",
                     (report["edges"], era))
    return report


def _resolve_edges(conn, era: str,
                   claims: list[dict]) -> tuple[list[dict], int]:
    """Link claims -> (edge rows, how many named a page we do not have).

    Only edges whose BOTH ends are quests this catalog knows are kept.
    Resolution happens here rather than in the crawl because only the database
    has the whole picture: a RoK quest whose prerequisite is an EoF quest is a
    real edge, and a crawl of RoK alone cannot tell that from a typo.

    An OR-GROUP is a set of alternatives — any one of them satisfies the
    requirement — and it is numbered per dependent quest so a reader of the
    table can tell "either of these two" from "both of these"."""
    known = {r["page_title"] for r in conn.execute(
        "SELECT page_title FROM plan_quests")}
    rows: dict[tuple[str, str], dict] = {}
    counters: dict[str, int] = {}
    dangling = 0
    for claim in claims:
        to = claim["to"]
        titles = [t for t in claim["titles"] if t in known and t != to]
        dangling += len(claim["titles"]) - len(titles)
        if not titles or to not in known:
            continue
        group = 0
        if len(titles) > 1:
            counters[to] = counters.get(to, 0) + 1
            group = counters[to]
        for title in titles:
            key = (title, to)
            prev = rows.get(key)
            # The same edge claimed twice — forward on one page, backward on
            # the other — is one row. A claim that is part of an alternative
            # group keeps that fact: an unconditional claim would otherwise
            # erase the choice.
            if prev and prev["or_group"]:
                continue
            rows[key] = {"from_page": title, "to_page": to, "era": era,
                         "kind": "hard", "or_group": group}
    return list(rows.values()), dangling


def fetch_icons(conn, progress=None) -> int:
    """Cache the pictures the catalog's items use. -> how many were downloaded.

    Part of the hand-run ingest and nowhere else. `items.py` already keys icons
    by Census's ICON id rather than by item, so one 42x42 file serves every
    item that uses it — 5,000 catalog rows come to about 1,200 pictures, and
    most of them are already on disk from loot resolution. Without this pass an
    examine card opened from the Planner would be a card with a hole in it,
    because nothing on a page load may fetch anything (`docs/sharing.md`)."""
    import items
    if not items.network_allowed():
        return 0
    need = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT icon FROM plan_items WHERE icon IS NOT NULL")
        if r[0] and not items.icon_path(r[0]).exists()})
    got = 0
    for i in range(0, len(need), items.WIKI_BATCH):
        chunk = need[i:i + items.WIKI_BATCH]
        urls = items._icon_urls(chunk)
        got += sum(1 for n in chunk
                   if urls.get(n) and items._download_icon(n, urls[n]))
        if progress:
            progress("icons", min(i + items.WIKI_BATCH, len(need)), len(need))
        if i + items.WIKI_BATCH < len(need):
            time.sleep(gamewiki.PAUSE_S)
    return got


def _era_rank(era: str) -> int:
    """Release order, so "introduced in" can be decided between two eras. Read
    from `zones.ERA_ORDER` rather than restated, because there is already one
    list of what came after what."""
    import zones
    return zones.era_rank(wiki.ERAS.get(era, era))


def sync(conn, era: str, force: bool = False, **kw) -> dict:
    return store(conn, crawl(era, **kw), force=force)
