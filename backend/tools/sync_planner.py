"""Build the Planner's catalog for one expansion (`backend/planner/`).

    .venv/bin/python backend/tools/sync_planner.py                 # RoK
    .venv/bin/python backend/tools/sync_planner.py --era eof
    .venv/bin/python backend/tools/sync_planner.py --era rok --era eof
    .venv/bin/python backend/tools/sync_planner.py --dry-run

Run it BY HAND. It is not on a schedule and should not be: an expansion's
itemization changes when the expansion changes, and a nightly crawl against
somebody else's wiki buys nothing and costs them bandwidth. The same rule
`sync_wiki.py` keeps, for the same reason.

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
from planner import ingest, wiki                  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--era", action="append", choices=sorted(wiki.ERAS),
                    help="which expansion (repeatable; default: rok)")
    ap.add_argument("--dry-run", action="store_true",
                    help="count the pages the crawl would fetch, and stop")
    ap.add_argument("--icons-only", action="store_true",
                    help="skip the crawl; only cache the item icons the "
                         "catalog already names")
    args = ap.parse_args()
    eras = args.era or list(wiki.DEFAULT_ERAS)

    init_db()
    conn = get_db()

    if args.dry_run:
        for era in eras:
            cats = wiki.CATEGORIES[era]
            mobs = len(gamewiki.category_members(cats["named"]))
            quests = len(gamewiki.category_members(cats["quests"]))
            print(f"{era} ({wiki.ERAS[era]}): {mobs} named monsters, "
                  f"{quests} quests — {(mobs + quests) // gamewiki.BATCH + 1} "
                  f"requests before the items they name")
        return 0

    def progress(label, done, total):
        print(f"\r  {label:9} {done}/{total} pages   ", end="", flush=True)

    if args.icons_only:
        print(f"\r  {ingest.fetch_icons(conn, progress)} icons cached")
        return 0

    for era in eras:
        print(f"{era} ({wiki.ERAS[era]})")
        report = ingest.sync(conn, era, progress=progress)
        print(f"\r  {report['mobs']} named monsters, {report['quests']} quests "
              f"-> {report['items']} items, {report['sources']} sources, "
              f"{report['sets']} adornment sets ({report['pages']} pages read)")
        if report["over_cap"]:
            # Live-era drift, counted rather than silent: a wiki page rewritten
            # for a revamp hands back an item nobody on this server can equip,
            # and one of those would become the top of the scoring scale.
            print(f"  {report['over_cap']} items dropped above the era's "
                  f"level cap ({wiki.ERA_CAP[era]})")

    got = ingest.fetch_icons(conn, progress)
    print(f"\r  {got} item icons cached                    ")

    print()
    for r in conn.execute(
            "SELECT s.era, COUNT(DISTINCT s.page_title) n, "
            "SUM(s.kind='raid') raid, SUM(s.kind='group') grp, "
            "SUM(s.kind='solo') solo, SUM(s.kind='quest') quest "
            "FROM plan_sources s GROUP BY s.era"):
        print(f"  {r['era']:4} {r['n']:5} items   raid {r['raid'] or 0:4}  "
              f"group {r['grp'] or 0:4}  solo {r['solo'] or 0:4}  "
              f"quest {r['quest'] or 0:4}")
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
