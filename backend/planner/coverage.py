"""What the wiki indexes for an era, against what the catalog actually holds.

**A CRAWL CANNOT REPORT ITS OWN COMPLETENESS.** `sync_planner` prints what it
found, and what it found is exactly what its indexes reach — the one number it
can never produce is the size of what they do not. That gap is not theoretical:
the catalog held 1 of the 1,107 mastercrafted pages in RoK's level band and
every count the crawl printed looked healthy.

So the denominator has to come from somewhere the crawl does not look. This
asks the wiki's own indexes for the era's universe and subtracts the catalog
from it, and it does the whole thing with CATEGORY LISTINGS — no page fetches,
no parsing — so it is cheap enough to run before every crawl and again after.

The residual it prints is the honest answer to "what else is there": equipment
the wiki files in this era's level band that the catalog holds no row for,
split by whether an index we already run should have caught it. A number that
does not go down after a crawl is a missing index, and the list says which.

    .venv/bin/python backend/tools/planner_coverage.py --era rok
"""

import gamewiki
from planner import wiki


def _members(members, categories) -> set[str]:
    out: set[str] = set()
    for cat in categories:
        out |= {t.strip() for t in members(cat) if t.strip()}
    return out


def audit(conn, era: str, members=gamewiki.category_members,
          handcrafted: bool = False) -> dict:
    """One era: the wiki's universe per index, and the catalog against it."""
    if era not in wiki.ERAS:
        raise ValueError(f"unknown era {era!r}; known: {sorted(wiki.ERAS)}")

    # --- quests: the three indexes, separately, so a gap names its index ---
    expansion = _members(members, [wiki.CATEGORIES[era]["quests"]])
    by_zone = _members(members, [f"Category:{z['page_title']} Quests"
                                 for z in wiki.era_zones(era)])
    by_tier = _members(members, wiki.tier_categories(era, wiki.TIER_QUESTS_SUFFIX))
    held_quests = {r[0] for r in conn.execute(
        "SELECT page_title FROM plan_quests WHERE era = ?", (era,))}

    # --- equipment: the era's level band is the universe ---
    band = _members(members, wiki.tier_categories(era, wiki.TIER_EQUIPMENT_SUFFIX))
    crafted = _members(members, wiki.crafted_categories(era, handcrafted)) & band
    held = {r[0] for r in conn.execute(
        "SELECT DISTINCT page_title FROM plan_sources WHERE era = ?", (era,))}

    # The band spans every expansion that did not move the cap — RoK and TSO
    # share Tier 9 entirely — so `band - held` is NOT a to-do list and saying
    # so would be dishonest. What it is good for is the SPLIT below: the
    # crafted slice is era-decidable from the recipe level alone, and the rest
    # is only reachable through a source page, which is the crawl's own job.
    return {
        "era": era,
        "quests": {
            "expansion": len(expansion),
            "zone_only": len(by_zone - expansion),
            "tier_only": len(by_tier - expansion - by_zone),
            "universe": len(expansion | by_zone | by_tier),
            "held": len(held_quests),
        },
        "equipment": {
            "band": len(band),
            "held_in_band": len(held & band),
            "crafted_in_band": len(crafted),
            "crafted_held": len(crafted & held),
            "crafted_missing": sorted(crafted - held),
            "sourced_outside_band": len(held - band),
        },
    }


def lines(report: dict) -> list[str]:
    """The audit as the operator reads it."""
    era = report["era"]
    q, e = report["quests"], report["equipment"]
    tiers = ", ".join(f"Tier {n}" for n in wiki.era_tiers(era))
    band = wiki.ERA_BAND[era]
    out = [
        f"{era} ({wiki.ERAS[era]}) — levels {band[0]}-{band[1]}, {tiers}",
        "  quests the wiki indexes:",
        f"    {q['expansion']:6d}  in the expansion category",
        f"    {q['zone_only']:6d}  more in its zones and nowhere else",
        f"    {q['tier_only']:6d}  more by tier alone (new content in an old zone)",
        f"    {q['universe']:6d}  union   ->  catalog holds {q['held']}",
        "  equipment in the era's level band:",
        f"    {e['band']:6d}  pages the wiki files at these tiers",
        f"    {e['held_in_band']:6d}  the catalog sources in this era",
        f"    {e['crafted_in_band']:6d}  of them crafted  ->  catalog holds "
        f"{e['crafted_held']}, missing {len(e['crafted_missing'])}",
    ]
    if e["sourced_outside_band"]:
        # Sourced here, filed at another tier: an item below the band that a
        # named in an era zone still drops. Not an error — the band is about
        # what the expansion ADDED, and a source is about where you get it.
        out.append(f"    {e['sourced_outside_band']:6d}  sourced in this era "
                   f"from outside the band")
    return out
