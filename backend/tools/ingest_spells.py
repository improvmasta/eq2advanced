#!/usr/bin/env python3
"""Bulk-ingest the base spell book from Census into census_spells.

    .venv/bin/python -m tools.ingest_spells --all --max-level 70
    .venv/bin/python -m tools.ingest_spells --classes wizard,warlock --max-level 70

Run from backend/ (or with --app-dir backend on PYTHONPATH). Fetches every
tier of every spell scribable by the given classes at or below --max-level,
then backfills the typed columns on any rows cached before the columns
existed. Safe to re-run: inserts are INSERT OR REPLACE.
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

# same .env start.sh sources — CENSUS_SERVICE_ID lives there (s:example gets
# burst-throttled hard on bulk pulls; a registered id does not)
_env = Path(__file__).resolve().parent.parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from census.client import CensusError, shared_client
from census.sync import ALL_CLASSES, backfill_typed_columns, ingest_class_spells
from db import get_db, init_db


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--classes", help="comma-separated lowercase class names")
    ap.add_argument("--all", action="store_true",
                    help="all 24 adventure classes")
    ap.add_argument("--max-level", type=int, default=70,
                    help="scribe-level ceiling (EoF cap = 70)")
    args = ap.parse_args()
    if args.all:
        classes = ALL_CLASSES
    elif args.classes:
        classes = [c.strip().lower() for c in args.classes.split(",") if c.strip()]
    else:
        ap.error("need --all or --classes")
    unknown = [c for c in classes if c not in ALL_CLASSES]
    if unknown:
        ap.error(f"unknown class name(s): {', '.join(unknown)}")

    init_db()
    conn = get_db()
    client = shared_client()
    # s:example burst-throttles bulk pulls; a registered id doesn't need pacing
    casual = os.environ.get("CENSUS_SERVICE_ID", "s:example") == "s:example"
    page_sleep = 30 if casual else 2
    if casual:
        print("no CENSUS_SERVICE_ID — pacing for the s:example burst limit "
              "(30s/page; a registered id makes this ~50x faster)", flush=True)
    total = 0
    failed = []

    def one(cls) -> bool:
        nonlocal total
        try:
            res = ingest_class_spells(conn, client, cls, args.max_level,
                                      page_sleep_s=page_sleep)
        except CensusError as e:
            # pages already fetched are persisted and the offset saved — a
            # re-run resumes mid-class, so a failure here loses nothing
            print(f"{cls:>14}: FAILED, will resume ({e})", flush=True)
            return False
        total += res["spells"]
        print(f"{cls:>14}: {res['spells']:4d} spell records "
              f"({res['fetched']} new this run), {res['lines']:3d} lines",
              flush=True)
        if res["spells"] == 0:
            print(f"{cls:>14}: WARNING — zero records; class name wrong or "
                  "Census hiccup, re-run for this class", flush=True)
        return True

    for i, cls in enumerate(classes):
        if i:
            time.sleep(5)  # the s:example burst limit trips fast — stay slow
        if not one(cls):
            failed.append(cls)
    if failed:
        print(f"cooling down 120s, then retrying: {', '.join(failed)}", flush=True)
        time.sleep(120)
        failed = [cls for cls in failed if not (time.sleep(5) or one(cls))]
    backfilled = backfill_typed_columns(conn)
    rows = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT crc) FROM census_spells").fetchone()
    print(f"fetched {total} records; backfilled typed columns on {backfilled} "
          f"older rows; cache now {rows[0]} spells / {rows[1]} lines")
    if failed:
        print(f"INCOMPLETE — re-run with --classes {','.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
