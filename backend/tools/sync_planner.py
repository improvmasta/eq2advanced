"""Build the Planner's catalog for one expansion (`backend/planner/`).

    .venv/bin/python backend/tools/sync_planner.py                 # RoK
    .venv/bin/python backend/tools/sync_planner.py --era eof
    .venv/bin/python backend/tools/sync_planner.py --era rok --era eof
    .venv/bin/python backend/tools/sync_planner.py --dry-run

Run it by hand or through the monthly `scripts/scheduled-sync.sh planner` job.
The low cadence fits expansion itemization, and the collapse guard refuses to
reconcile a suspiciously incomplete crawl. `sync_wiki.py` remains hand-run.

For RoK, the run first invokes wikq2's versioned 24-class epic export. This is
an offline sibling-repo boundary, not a request-time service dependency.

RoK is ~350 named monsters and ~900 quests, and what those two point at is
~1,500 item pages. Batched at 40 titles a request with a quarter-second pause,
a full era is minutes.

Content is CC-BY-SA; it is used here as reference data about the game.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gamewiki                                   # noqa: E402
from db import get_db, init_db                    # noqa: E402
from planner import epic_timelines, ingest, wiki  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--era", action="append", choices=sorted(wiki.ERAS),
                    help="which expansion (repeatable; default: rok)")
    ap.add_argument("--dry-run", action="store_true",
                    help="count the pages the crawl would fetch, and stop")
    ap.add_argument("--icons-only", action="store_true",
                    help="skip the crawl; only cache the item icons the "
                         "catalog already names")
    ap.add_argument("--force", action="store_true",
                    help="write the crawl even if it came back far smaller "
                         "than the last one (see ingest.COLLAPSE_RATIO)")
    ap.add_argument("--skip-wikq2", action="store_true",
                    help="leave the last structured epic timeline snapshot in place")
    args = ap.parse_args()
    eras = args.era or list(wiki.DEFAULT_ERAS)

    init_db()
    conn = get_db()

    epic_data = None
    if "rok" in eras and not args.skip_wikq2 and not args.dry_run and not args.icons_only:
        print("wikq2 (24 class epic timelines)")
        epic_data = epic_timelines.export()

    if args.dry_run:
        for era in eras:
            named = {t for cat in wiki.named_categories(era)
                     for t in gamewiki.category_members(cat)}
            quests = len(gamewiki.category_members(wiki.CATEGORIES[era]["quests"]))
            drops = sum(len(gamewiki.category_members(cat))
                        for cat, _ in wiki.drop_categories(era))
            print(f"{era} ({wiki.ERAS[era]}): {len(named)} named monsters over "
                  f"{len(wiki.era_zones(era))} zones, {quests} quests, "
                  f"{drops} zone drops — "
                  f"{(len(named) + quests + drops) // gamewiki.BATCH + 1} "
                  f"requests before the items they name")
        return 0

    def progress(label, done, total):
        print(f"\r  {label:9} {done}/{total} pages   ", end="", flush=True)

    if args.icons_only:
        print(f"\r  {ingest.fetch_icons(conn, progress)} icons cached")
        return 0

    crawls = []
    reports = []
    for era in eras:
        print(f"{era} ({wiki.ERAS[era]})")
        crawled = ingest.crawl(era, progress=progress)
        try:
            report = ingest.store(conn, crawled, force=args.force)
        except ingest.CrawlCollapsed as exc:
            # Loud and non-zero: this is the one failure mode that matters on a
            # schedule, because the alternative is a silently emptied catalog.
            print(f"\r  REFUSED: {exc}")
            return 2
        crawls.append(crawled)
        reports.append(report)

    # Resolve graphs once more with every requested era now present. A RoK
    # quest can depend on an EoF quest, and `--era rok --era eof` must not lose
    # that edge merely because RoK was stored first.
    if len(crawls) > 1:
        for crawled, report in zip(crawls, reports):
            report.update(ingest.reconcile_edges(
                conn, crawled["era"], crawled["edges"]))

    if epic_data is not None:
        print(f"  {epic_timelines.store(conn, epic_data)} structured epic timelines imported")

    for report in reports:
        print(f"\r{report['era']}  {report['mobs']} named monsters, "
              f"{report['quests']} quests "
              f"-> {report['items']} items, {report['sources']} sources, "
              f"{report['sets']} adornment sets ({report['pages']} pages read)")
        print(f"  {report['edges']} prerequisite edges between quests")
        if report["zone_drops"]:
            # What the mob and quest inversions cannot reach: gear that fell
            # off something with no page of its own. This number IS the gap the
            # expansion categories used to leave behind.
            print(f"  {report['zone_drops']} item pages came from a zone's "
                  f"drop list and no named or quest")
        if report["dangling"]:
            # A prerequisite naming a page this catalog does not have. A couple
            # of percent is normal — they point at another expansion's quest or
            # at a collection — and a number that starts climbing means the
            # crawl is missing pages rather than the wiki being loose.
            print(f"  {report['dangling']} prerequisite links point outside "
                  f"the catalog")
        if report["over_cap"]:
            # Live-era drift, counted rather than silent: a wiki page rewritten
            # for a revamp hands back an item nobody on this server can equip,
            # and one of those would become the top of the scoring scale.
            print(f"  {report['over_cap']} items dropped above the era's "
                  f"level cap ({wiki.ERA_CAP[report['era']]})")

    got = ingest.fetch_icons(conn, progress)
    print(f"\r  {got} item icons cached                    ")

    print()
    for r in conn.execute(
            "SELECT s.era, COUNT(DISTINCT s.page_title) n, "
            "SUM(s.kind='raid') raid, SUM(s.kind='group') grp, "
            "SUM(s.kind='solo') solo, SUM(s.kind='quest') quest, "
            "SUM(s.kind='zone') zone "
            "FROM plan_sources s GROUP BY s.era"):
        print(f"  {r['era']:4} {r['n']:5} items   raid {r['raid'] or 0:4}  "
              f"group {r['grp'] or 0:4}  solo {r['solo'] or 0:4}  "
              f"quest {r['quest'] or 0:4}  world {r['zone'] or 0:4}")
    unlinked = conn.execute(
        "SELECT COUNT(*) FROM plan_items WHERE census_id IS NULL").fetchone()[0]
    if unlinked:
        # No Census id means no examine card — the row still lists and still
        # scores, it just cannot open the item box. Worth printing because it
        # is the one gap a reader will notice.
        print(f"  {unlinked} items have no Census id (no examine card)")
    return 0


if __name__ == "__main__":
    # Deliberately NOT setting CENSUS_AUTO_REFRESH=0 the way `sync_wiki.py`
    # does. That switch is `items.network_allowed`, which gates the ICON
    # downloads as well as Census, and this tool's whole job is to reach the
    # network — with it set, the icon pass silently caches nothing. Nothing
    # here calls Census at all, so there is no refresh to suppress.
    raise SystemExit(main())
