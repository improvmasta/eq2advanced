"""Keep the /plan by-name lookup cache current (`plan_characters`).

    .venv/bin/python backend/tools/refresh_plan_characters.py [--limit N]
                                                             [--older-than-hours H]

**A lookup cache nobody refreshes goes stale in one direction.** `/plan` caches
every character somebody searches by name, serves it while Census is down, and
re-asks Census only when a human types that name again. So a toon nobody
re-types keeps whatever gear they had the first time anyone looked — and a name
typed for the FIRST time during a Census outage answers nothing at all, because
there is no row to fall back to.

This is the other half, and it runs on the schedule that already probes Census
(`scripts/scheduled-sync.sh census`): refresh the stalest rows so the cache is
current for the next reader, signed in or not. Refreshing a row also caches
that character's item records, which is what makes the gear window and its
icons work for whoever looks next.

Bounded on purpose — `--limit` rows per run, oldest first, only rows past
`--older-than-hours`. A trickle that keeps up with a table people are adding
to, not a full re-read of somebody else's service every half hour. It stops at
the first Census error rather than hammering a service that is already unhappy;
the next probe finds it up again.

Needs a real CENSUS_SERVICE_ID (the same .env start.sh sources).
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for line in (Path(__file__).resolve().parents[2] / ".env").read_text().splitlines() \
        if (Path(__file__).resolve().parents[2] / ".env").exists() else []:
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from census import sync                                            # noqa: E402
from census.client import CensusClient                             # noqa: E402
from db import get_db                                              # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40,
                    help="how many cached names to re-ask about this run")
    ap.add_argument("--older-than-hours", type=float, default=12.0,
                    help="only refresh rows last fetched longer ago than this")
    ap.add_argument("--world", type=int, default=618)
    args = ap.parse_args()

    if os.environ.get("CENSUS_SERVICE_ID", "s:example") == "s:example":
        print("no CENSUS_SERVICE_ID — s:example throttles after ~6 requests",
              file=sys.stderr)
        return 2

    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM plan_characters").fetchone()[0]
    if not total:
        print("no cached lookups yet — nothing to refresh")
        return 0

    started = time.time()
    report = sync.refresh_cached_lookups(
        conn, CensusClient(), args.world, limit=args.limit,
        older_than_s=int(args.older_than_hours * 3600))
    print(f"{report['checked']} of {total} cached names re-asked "
          f"({report['found']} answered, {report['still_missing']} still unknown"
          f"{', census went away' if report['stopped'] else ''}) "
          f"in {round(time.time() - started, 1)}s; "
          f"{report['queued']} still stale")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
