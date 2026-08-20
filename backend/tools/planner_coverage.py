"""What the Planner's catalog is missing, measured against the wiki's indexes.

    .venv/bin/python backend/tools/planner_coverage.py                # RoK
    .venv/bin/python backend/tools/planner_coverage.py --era eof --era rok
    .venv/bin/python backend/tools/planner_coverage.py --list-missing

Category listings only — no page fetches and no parsing — so it is cheap to run
before a crawl and again after. See `planner/coverage.py` for why the crawl
cannot answer this question about itself.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_db, init_db          # noqa: E402
from planner import coverage, wiki      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--era", action="append", choices=sorted(wiki.ERAS),
                    help="which expansion (repeatable; default: rok)")
    ap.add_argument("--handcrafted", action="store_true",
                    help="count handcrafted gear as in scope too")
    ap.add_argument("--list-missing", action="store_true",
                    help="print every crafted page the catalog has no row for")
    args = ap.parse_args()

    init_db()
    conn = get_db()
    for era in (args.era or list(wiki.DEFAULT_ERAS)):
        report = coverage.audit(conn, era, handcrafted=args.handcrafted)
        print("\n".join(coverage.lines(report)))
        if args.list_missing:
            for title in report["equipment"]["crafted_missing"]:
                print(f"      {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
