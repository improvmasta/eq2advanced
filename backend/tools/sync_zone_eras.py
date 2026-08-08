"""Pull the EQ2 wiki's zone facts into `backend/refdata/zone_eras.json`.

Which expansion a zone arrived with, whether it is a raid, and how big — the
three things a log line never says and the notes outline is grouped by.

    .venv/bin/python backend/tools/sync_zone_eras.py            # ~1400 pages
    .venv/bin/python backend/tools/sync_zone_eras.py --dry-run  # just count them

Run it by hand, like `sync_wiki.py` and for the same reason: zones arrive once
an expansion, and a schedule against somebody else's wiki buys nothing. The
JSON it writes is committed — the app never fetches at runtime, so a wiki
outage is not an outage here.

Content is CC-BY-SA; it is used as internal reference data to group notes.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gamewiki                                   # noqa: E402
import zones                                      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="count the zone pages and stop, without fetching them")
    ap.add_argument("--out", default=str(zones.ZONE_FILE))
    args = ap.parse_args()

    titles = gamewiki.collect_zone_titles()
    print(f"{len(titles)} zone pages")
    if args.dry_run:
        return 0

    rows: list[dict] = []
    for i in range(0, len(titles), gamewiki.BATCH):
        batch = titles[i:i + gamewiki.BATCH]
        for title, text in gamewiki.fetch_wikitext(batch).items():
            row = gamewiki.parse_zone(title, text)
            if row:
                rows.append(row)
        print(f"\r  {min(i + gamewiki.BATCH, len(titles))}/{len(titles)} pages, "
              f"{len(rows)} with an expansion", end="", flush=True)
        time.sleep(gamewiki.PAUSE_S)
    print()

    # A zone the wiki files under a live update belongs to whatever expansion
    # was live that day — `LU22` is Kingdom of Sky, and its own patch notes say
    # so. Resolved HERE rather than at read time: the answer is a fact about a
    # date in 2006 and will not change again.
    lus = {r["era"] for r in rows if gamewiki.live_update_number(r["era"])}
    if lus:
        dates = gamewiki.live_update_dates(
            gamewiki.live_update_number(e) for e in lus)
        print(f"{len(dates)}/{len(lus)} live updates dated")
        for row in rows:
            date = dates.get(row["era"])
            era = zones.expansion_on(date) if date else None
            if era:
                row["update"] = row["era"]          # provenance, not identity
                row["era"] = era

    # One entry per zone NAME, because that is all a log line gives us. Where
    # two pages disambiguate to the same name (`Nektulos Forest (Original)`),
    # the raid one wins — this file exists for raid notes.
    by_name: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r["page_title"]):
        prev = by_name.get(row["zone"])
        if prev is None or (row["instance"] == "Raid" and prev["instance"] != "Raid"):
            by_name[row["zone"]] = row

    eras = sorted({r["era"] for r in by_name.values()})
    unknown = [e for e in eras if e not in zones.ERA_ORDER]
    if unknown:
        # Not fatal: an era the display order has never heard of still groups,
        # it just sorts to the end. Say so, because the fix is one list.
        print(f"note: {len(unknown)} expansion(s) not in zones.ERA_ORDER: "
              f"{', '.join(unknown)}")

    out = Path(args.out)
    out.write_text(json.dumps(
        {"zones": [by_name[k] for k in sorted(by_name)]}, indent=1) + "\n")
    raids = sum(1 for r in by_name.values() if r["instance"] == "Raid")
    print(f"{len(by_name)} zones written to {out} ({raids} raid zones, "
          f"{len(eras)} expansions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
