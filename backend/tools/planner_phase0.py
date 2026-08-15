#!/usr/bin/env python3
"""Run the Planner's Phase 0 waypoint-matcher decision gate.

    .venv/bin/python backend/tools/planner_phase0.py
    .venv/bin/python backend/tools/planner_phase0.py --limit 10

The script reads the already-synced quest catalog, asks a local wikq2 instance
for each exact quest page, and writes a resumable JSON artifact under ``data/``.
It is deliberately hand-run and never imported by the web application.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_db, init_db                    # noqa: E402
from planner import waypoint_audit               # noqa: E402
from planner.wiki import ERAS                     # noqa: E402


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--era", default="rok", choices=sorted(ERAS))
    parser.add_argument(
        "--lookup-url",
        default=os.environ.get("WIKQ2_LOOKUP_URL", "http://localhost:3001/api/lookup"),
        help="wikq2 exact lookup endpoint (default: local dev server)",
    )
    parser.add_argument("--output", type=Path,
                        help="JSON artifact (default: data/planner-waypoints-ERA.json)")
    parser.add_argument("--workers", type=int, default=2,
                        help="concurrent exact lookups (default: 2)")
    parser.add_argument("--limit", type=int,
                        help="audit only the first N titles (smoke testing)")
    parser.add_argument("--fresh", action="store_true",
                        help="resolve every title again instead of resuming successes")
    args = parser.parse_args()
    output = args.output or ROOT / "data" / f"planner-waypoints-{args.era}.json"

    init_db()
    conn = get_db()

    def progress(done: int, total: int, title: str) -> None:
        print(f"\r  {done:4}/{total}  {title[:72]:72}", end="", flush=True)

    report = waypoint_audit.audit(
        conn, args.era, args.lookup_url.rstrip("?"), output,
        workers=args.workers, limit=args.limit, progress=progress,
        resume=not args.fresh,
    )
    print()
    summary = report["summary"]
    compact = {key: value for key, value in summary.items()
               if key not in ("byZone", "errors")}
    compact["largestZones"] = summary["byZone"][:12]
    compact["errors"] = summary["errors"]
    print(json.dumps(compact, indent=2))
    print(f"\nArtifact: {output}")
    return 0 if report["summary"]["quests"]["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
