"""Read the chest loot out of logs that were parsed before loot existed.

    .venv/bin/python backend/tools/backfill_loot.py [--sessions 3,4] [--resolve]
    .venv/bin/python backend/tools/backfill_loot.py --resolve-only

Every session's raw is still on disk (uploads are content-addressed, live
chunks are per-session files), so the drops are already in the archive — 503 of
them across 18 nights at the time this was written — and nothing needs
re-uploading.

**This is deliberately NOT a PARSE_VERSION bump.** Loot is written beside the
parse and changes no stat, no segment, no roster and no rollup, so making the
startup sweep re-derive 114 sessions to pick up a column nothing else reads
would be a lot of work for a table that a single pass over the same bytes
fills. A session whose raw was dropped (`retain_raw=0`) simply has no loot, the
same way a parser improvement never reaches it.

`--resolve` then asks Census and the wiki about the items themselves — names,
rarity, icons, wiki pages (see backend/items.py). It needs a real
CENSUS_SERVICE_ID and it talks to the network, so it is opt-in and safe to
interrupt: every item resolved is cached, and a re-run starts from what is
left. `--resolve-only` skips the scan and just finishes the item side.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for line in (Path(__file__).resolve().parents[2] / ".env").read_text().splitlines() \
        if (Path(__file__).resolve().parents[2] / ".env").exists() else []:
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

import items                                                       # noqa: E402
from db import get_db                                              # noqa: E402
from pipeline import loot                                          # noqa: E402
from pipeline.ingest_writer import _iter_lines, session_raw_paths   # noqa: E402


def scan_sessions(conn, only: set[int] | None) -> int:
    rows = conn.execute(
        "SELECT s.id, c.name AS char_name FROM sessions s "
        "JOIN characters c ON c.id = s.character_id ORDER BY s.id").fetchall()
    total = 0
    for row in rows:
        if only and row["id"] not in only:
            continue
        paths = session_raw_paths(conn, row["id"])
        if not paths:
            continue
        with conn:
            n = loot.record(conn, row["id"], _iter_lines(paths), row["char_name"])
        if n:
            print(f"session {row['id']}: {n} chest drops")
        total += n
    return total


def refresh_census(conn) -> int:
    """Re-ask Census about every item already known, ignoring the cache.

    Not a repair job — it is how a change to the CARD reaches items that were
    resolved before it existed. `stat_block()` is built at resolve time so the
    hover card can be a read, which means widening the card is a re-resolve.
    Census only; the wiki half (page, icon) has not changed and is not asked
    again."""
    ids = [r[0] for r in conn.execute("SELECT item_id FROM items ORDER BY item_id")]
    print(f"re-asking Census about {len(ids)} items")
    for i in range(0, len(ids), items.CENSUS_CHUNK):
        items.fetch_census(conn, ids[i:i + items.CENSUS_CHUNK])
        print(f"  {min(i + items.CENSUS_CHUNK, len(ids))}/{len(ids)}")
    return len(ids)


def refresh_wiki(conn) -> int:
    """Re-ask the wiki about every item already known, ignoring the cache.

    The counterpart to `--refresh-census`, and for the same reason: the wiki
    half owns the page link, the icon and the item's EFFECT, so widening what
    is read off the page means reading the pages again. Slower than the Census
    pass — the wiki is asked politely, in batches, with a pause."""
    ids = [r[0] for r in conn.execute("SELECT item_id FROM items ORDER BY item_id")]
    print(f"re-asking the wiki about {len(ids)} items")
    for i in range(0, len(ids), items.CENSUS_CHUNK):
        items.fetch_wiki(conn, ids[i:i + items.CENSUS_CHUNK])
        print(f"  {min(i + items.CENSUS_CHUNK, len(ids))}/{len(ids)}")
    return len(ids)


def resolve_items(conn) -> int:
    ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT item_id FROM loot_drops")]
    todo = items.unresolved(conn, ids)
    if not todo:
        print(f"{len(ids)} items, all resolved")
        return 0
    print(f"{len(ids)} items, {len(todo)} to resolve")
    # In batches, so an interrupted run still leaves progress behind rather
    # than one enormous all-or-nothing pass.
    done = 0
    for i in range(0, len(todo), items.CENSUS_CHUNK):
        chunk = todo[i:i + items.CENSUS_CHUNK]
        items.fetch_census(conn, chunk)
        items.fetch_wiki(conn, chunk)
        done += len(chunk)
        print(f"  {done}/{len(todo)}")
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", help="comma-separated session ids (default: all)")
    ap.add_argument("--resolve", action="store_true",
                    help="also look the items up in Census and the wiki")
    ap.add_argument("--resolve-only", action="store_true",
                    help="skip the log scan, only finish the item lookups")
    ap.add_argument("--refresh-census", action="store_true",
                    help="re-ask Census about every known item (after a change "
                         "to the item card), then stop")
    ap.add_argument("--refresh-wiki", action="store_true",
                    help="re-ask the wiki about every known item (page, icon, "
                         "effect), then stop")
    args = ap.parse_args()

    conn = get_db()
    if args.refresh_census or args.refresh_wiki:
        if args.refresh_census:
            refresh_census(conn)
        if args.refresh_wiki:
            refresh_wiki(conn)
        return 0
    if not args.resolve_only:
        only = ({int(x) for x in args.sessions.split(",")}
                if args.sessions else None)
        scan_sessions(conn, only)
        # Lines scanned and rows stored differ on purpose: the log emits the
        # occasional exact duplicate, and the natural key collapses those.
        rows = conn.execute("SELECT COUNT(*) FROM loot_drops").fetchone()[0]
        print(f"\n{rows} drops in the table")
        for r in conn.execute(
                "SELECT attribution, COUNT(*) n FROM loot_drops "
                "GROUP BY 1 ORDER BY n DESC"):
            print(f"  {r['n']:6d} by {r['attribution']}")
    if args.resolve or args.resolve_only:
        resolve_items(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
