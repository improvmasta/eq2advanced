"""Building the catalog: crawl one expansion's wiki, write `plan_*`.

**Offline, hand-run, never scheduled.** `tools/sync_planner.py` is the only
caller and a person types it — the same rule the wiki ability ingest keeps,
because a crawl that runs itself is a crawl nobody is watching and the wiki is
somebody else's server.

**The catalog is built by INVERTING mobs and quests, not by reading items.**
Item pages have no era and their `obtain` field is blank more often than not;
the monster that drops a thing says `patch = Rise of Kunark`, `diff = epic x4`
and `zone = The Protector's Realm`, and the quest that rewards it says the
same. So the crawl walks `Category:<era> Named Monsters` and
`Category:<era> Quests`, collects what they point at, and fetches only those
item pages. Source, era and the raid/group/solo split all arrive with the
link — none of them could have been read off the item.

Four rounds of fetching, all batched at `gamewiki.BATCH` titles per request:

1. the named monsters of the era — drops, zone, difficulty
2. its quests — equipment rewards, level, timeline
3. every item those two named — and every VERSION behind a disambiguation,
   because `Focused Mind Slippers` is two real items at two levels and the
   catalog wants both
4. the adornment sets those items belong to

RoK is roughly 350 monsters and 900 quests, so a full era is a few hundred
requests at a quarter-second apiece — minutes, not hours.
"""

import json
import time

import gamewiki
from planner import wiki

# A page whose title is one of these is a category or a file, not a thing.
_SKIP_PREFIXES = ("category:", "file:", "template:", "image:")


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
    mob_titles = _titles(members(cats["named"]))
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

    # --- 3: the items, and the versions behind a disambiguation ---------
    item_pages = _fetch_all(_titles(wanted), fetch, progress, "items")
    pages_read += len(item_pages)
    # A disambiguation is the COMMON case here — `Focused Mind Slippers` points
    # at `(Level 78)` and `(Level 80)`, which are two real items — so both are
    # taken and both inherit the source that named the pointer. items.py picks
    # the first version instead, because there it is resolving ONE logged drop
    # and here we are building a catalog.
    versions: dict[str, str] = {}          # version page -> the link that led here
    for title, text in item_pages.items():
        if gamewiki.is_disambiguation(text):
            for link in _titles(wiki.links(text)):
                versions[link] = title
    if versions:
        version_pages = _fetch_all(sorted(versions), fetch, progress, "versions")
        pages_read += len(version_pages)
        item_pages.update(version_pages)

    items: dict[str, dict] = {}
    sources: list[dict] = []
    over_cap = 0
    for title, text in item_pages.items():
        row = wiki.parse_equip(title, text)
        if not row:
            continue                       # a pattern, a recipe, a pointer page
        origin = versions.get(title, title)
        named_by = wanted.get(origin)
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
        "mobs": len(mobs),
        "quests": len(quests),
        "pages": pages_read,
        "over_cap": over_cap,
    }


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

SET_UPSERT = (
    "INSERT INTO plan_sets (name, page_title, era, level, pieces_json, "
    "bonuses_json, fetched_ts) VALUES "
    "(:name,:page_title,:era,:level,:pieces_json,:bonuses_json,:fetched_ts) "
    "ON CONFLICT(name) DO UPDATE SET page_title=excluded.page_title, "
    "era=excluded.era, level=excluded.level, pieces_json=excluded.pieces_json, "
    "bonuses_json=excluded.bonuses_json, fetched_ts=excluded.fetched_ts")


def store(conn, crawled: dict) -> dict:
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
    known = {r["page_title"] for r in items}
    sources = [s for s in crawled["sources"] if s["page_title"] in known]
    sets = [{
        "name": s["name"], "page_title": s["page_title"], "era": era,
        "level": s["level"],
        "pieces_json": json.dumps(s["pieces"], separators=(",", ":")),
        "bonuses_json": json.dumps(s["bonuses"], separators=(",", ":")),
        "fetched_ts": now,
    } for s in crawled["sets"]]
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
        conn.execute(
            "INSERT INTO plan_syncs (era, items, sources, sets, pages, synced_ts) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(era) DO UPDATE SET items=excluded.items, "
            "sources=excluded.sources, sets=excluded.sets, pages=excluded.pages, "
            "synced_ts=excluded.synced_ts",
            (era, len(items), len(sources), len(sets), crawled["pages"], now))
    return {"era": era, "items": len(items), "sources": len(sources),
            "sets": len(sets), "pages": crawled["pages"],
            "mobs": crawled["mobs"], "quests": crawled["quests"],
            "over_cap": crawled["over_cap"]}


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


def sync(conn, era: str, **kw) -> dict:
    return store(conn, crawl(era, **kw))
