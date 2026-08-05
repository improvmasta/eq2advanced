"""Look up every raider's class in Census and cache it (`roster_classes`).

    .venv/bin/python backend/tools/sync_roster.py [--all] [--budget N]
    .venv/bin/python backend/tools/sync_roster.py --guilds

By default it asks about the names that are actually in the database as player
entities — the people in your raids — and only the ones whose cache entry is
missing or stale. `--all` adds every name in a run roster, which is the same
set plus anyone whose only appearance was in a merged parse.

`--guilds` is the "do it now" version of the hourly background pass: re-read
every row cached before guilds existed, then retag every raid. It replaces
waiting a day for the trickle, and it is safe to interrupt — the queue is a
column, so the next run picks up where this one stopped.

Needs a real CENSUS_SERVICE_ID (the same .env start.sh sources). `s:example`
throttles after about six requests, which is not enough for one raid, let alone
a season of them.
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

from census import guilds, roster                                  # noqa: E402
from census.client import CensusClient                             # noqa: E402
from db import get_db                                              # noqa: E402


def sync_guilds(conn, world: int) -> int:
    client = CensusClient()
    while True:
        report = guilds.backfill_stale_guilds(conn, client, world_id=world)
        print(report)
        if not report["asked"] or not report["remaining"]:
            break
    with conn:
        changed = guilds.retag_runs(conn)
    tagged = conn.execute(
        "SELECT COUNT(*) FROM zone_runs WHERE guild IS NOT NULL").fetchone()[0]
    print(f"{changed} runs changed; {tagged} raids now carry a guild")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="include every name in a zone-run roster")
    ap.add_argument("--guilds", action="store_true",
                    help="backfill guilds for already-cached names, then retag raids")
    ap.add_argument("--budget", type=int, default=None,
                    help="stop after this many Census requests")
    ap.add_argument("--world", type=int, default=roster.DEFAULT_WORLD)
    args = ap.parse_args()

    if os.environ.get("CENSUS_SERVICE_ID", "s:example") == "s:example":
        print("no CENSUS_SERVICE_ID — s:example throttles after ~6 requests, "
              "which will not get through one raid", file=sys.stderr)
        return 2

    conn = get_db()
    if args.guilds:
        return sync_guilds(conn, args.world)
    names = {r[0] for r in conn.execute(
        "SELECT DISTINCT name FROM entities WHERE kind='player'")}
    if args.all:
        import json
        for (rj,) in conn.execute(
                "SELECT roster_json FROM zone_runs WHERE roster_json IS NOT NULL"):
            names |= set(json.loads(rj))

    todo = roster.stale_names(conn, names, args.world)
    print(f"{len(names)} names, {len(todo)} to ask Census about")
    t = time.time()
    report = roster.resolve(conn, CensusClient(), names, args.world, args.budget)
    print(f"{report} in {round(time.time() - t, 1)}s")
    known = roster.known_classes(conn, args.world)
    covered = {n for n in names if n.lower() in known}
    print(f"{len(covered)}/{len(names)} names now have a class from Census "
          f"({round(100 * len(covered) / max(len(names), 1))}%)")
    # those answers carried guilds too, so the raids they belong to can be
    # revoted right now instead of at the next write
    with conn:
        print(f"{guilds.retag_runs(conn)} runs retagged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
