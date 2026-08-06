"""CSV of every ability and its pet/proc evidence — the Abilities admin page's
data, offline.

The page (`/admin/abilities`) is where decisions are made; this is for reading
the whole set at once, which a scrolling queue is bad at. Same module behind
both, so they cannot disagree: `census/abilityreview.py`.

    .venv/bin/python backend/tools/ability_review.py            # -> data/ability-review.csv
    .venv/bin/python backend/tools/ability_review.py --open-only --out /tmp/x.csv
"""

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from census.abilityreview import gather      # noqa: E402
from db import DATA_DIR, get_db, init_db     # noqa: E402

FIELDS = ["ability", "suggest", "confidence", "why", "classes",
          "scribed_by", "grant_kind", "grant_name", "grant_class", "trigger",
          "curated_pet", "curated_proc",
          "pet_definite", "pet_own", "pet_guess", "pet_sessions",
          "prepare_lines", "logger_hits", "player_casts", "distinct_players",
          "mob_casts", "total_damage", "player_classes", "ruled"]

OPEN = ("medium", "low")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(DATA_DIR / "ability-review.csv"))
    ap.add_argument("--open-only", action="store_true",
                    help="just the queue: unruled and under full confidence")
    args = ap.parse_args()

    init_db()
    rows = list(gather(get_db()).values())
    if args.open_only:
        rows = [d for d in rows if not d["ruling"] and d["confidence"] in OPEN]
    rank = {"ruled": 0, "curated": 1, "high": 2, "medium": 3, "low": 4}
    rows.sort(key=lambda d: (rank[d["confidence"]], -d["total_damage"], d["ability"]))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for d in rows:
            w.writerow({**d,
                        "classes": ",".join(d["classes"]),
                        "player_classes": ",".join(d["player_classes"]),
                        "ruled": 1 if d["ruling"] else 0})

    print(f"{len(rows)} abilities -> {out}")
    for conf in ("ruled", "curated", "high", "medium", "low"):
        n = sum(1 for d in rows if d["confidence"] == conf)
        if n:
            print(f"  {conf:8} {n:5}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("CENSUS_AUTO_REFRESH", "0")
    raise SystemExit(main())
