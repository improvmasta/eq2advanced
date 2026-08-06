"""Pull EQ2 wiki ability reference data into `wiki_abilities`.

Census stays authoritative for spells; this fills what it was never asked for
— AAs above all, which is where the log's own evidence runs out (see
`backend/gamewiki.py`).

    .venv/bin/python backend/tools/sync_wiki.py              # EoF AAs (this server)
    .venv/bin/python backend/tools/sync_wiki.py --era rok    # when RoK lands
    .venv/bin/python backend/tools/sync_wiki.py --dry-run

Run it by hand. It is not on a schedule and should not be: the game's AA trees
change once an expansion, and a nightly job against somebody else's wiki buys
nothing and costs them bandwidth.

Content is CC-BY-SA; it is used here as internal reference data to label
abilities, and anything surfaced to a reader should carry attribution.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gamewiki                                   # noqa: E402
from db import get_db, init_db                    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--era", default="eof", choices=sorted(gamewiki.AA_TREES),
                    help="which expansion's AA trees (default: eof, this server)")
    ap.add_argument("--what", default="all", choices=("all", "aa", "deity"),
                    help="aa = the AA trees; deity = blessings and miracles "
                         "(always EoF — deities arrived with it)")
    ap.add_argument("--dry-run", action="store_true",
                    help="count the pages and stop, without fetching them")
    args = ap.parse_args()

    init_db()
    conn = get_db()

    if args.dry_run:
        if args.what in ("all", "aa"):
            titles = gamewiki.collect_aa_titles(args.era)
            tiers = sorted({t for ts in titles.values() for t in ts})
            print(f"{args.era} AAs: {len(titles)} pages across {len(tiers)} class tiers")
            print(f"  tiers: {', '.join(tiers)}")
        if args.what in ("all", "deity"):
            print(f"deity: {len(gamewiki.collect_deity_titles())} blessings + miracles")
        return 0

    def progress(done, total):
        print(f"\r  {done}/{total} pages", end="", flush=True)

    total = 0
    if args.what in ("all", "aa"):
        n = gamewiki.sync_aas(conn, era=args.era, progress=progress)
        print(f"\n{n} AAs written (era={args.era})")
        total += n
    if args.what in ("all", "deity"):
        n = gamewiki.sync_deities(conn, progress=progress)
        print(f"\n{n} deity abilities written (disambiguation pages skipped)")
        total += n

    for kind, act, tot in conn.execute(
            "SELECT kind, SUM(activated), COUNT(*) FROM wiki_abilities GROUP BY kind"):
        print(f"  {kind:6} {tot:5}  activated (pressed): {act}  passive: {tot - act}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("CENSUS_AUTO_REFRESH", "0")
    raise SystemExit(main())
