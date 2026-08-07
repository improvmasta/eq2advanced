"""Apply redaction to logs that were stored before redaction existed.

    .venv/bin/python backend/tools/redact_existing.py --dry-run
    .venv/bin/python backend/tools/redact_existing.py

New uploads and live batches are filtered as they stream, so this is a ONE-TIME
pass over what is already on disk. Until it has run, the Import page's promise is
true of new logs and false of old ones — which is the wrong way round for a
promise, so run it before saying anything publicly.

It rewrites each stored file in place through the same `keep_line` the ingest
path uses (atomic temp + rename, so an interrupted run leaves whole files), then
applies `trim_to_fights` per session. Safe to re-run: a redacted file redacts to
itself. Safe to run against a live app for the same reason, though a session
being written at that moment is better left until it closes.

It does NOT touch derived data. Chat produces no events, so nothing downstream
changes and no reparse is needed.
"""

import argparse
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_db                                        # noqa: E402
from pipeline.ingest_writer import session_raw_paths         # noqa: E402
from pipeline.redact import keep_line, trim_to_fights        # noqa: E402


def redact_file(path: Path, dry_run: bool) -> tuple[int, int]:
    """Returns (lines dropped, bytes reclaimed)."""
    dropped = 0
    before = path.stat().st_size
    tmp = path.with_suffix(path.suffix + ".redacting")
    try:
        with gzip.open(path, "rb") as src, gzip.open(tmp, "wb") as out:
            for raw in src:
                if keep_line(raw.decode("utf-8", "surrogateescape")):
                    out.write(raw)
                else:
                    dropped += 1
        if dropped and not dry_run:
            after = tmp.stat().st_size
            tmp.replace(path)
            return dropped, before - after
        after = tmp.stat().st_size if dropped else before
    finally:
        tmp.unlink(missing_ok=True)
    return dropped, before - after


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would go, change nothing")
    args = ap.parse_args()

    conn = get_db()
    sessions = conn.execute(
        "SELECT id FROM sessions WHERE raw_deleted_ts IS NULL ORDER BY id").fetchall()

    seen: set[Path] = set()
    total_dropped = total_bytes = 0
    for row in sessions:
        paths = [p for p in session_raw_paths(conn, row["id"]) if p not in seen]
        seen.update(paths)
        dropped = 0
        for path in paths:
            d, b = redact_file(path, args.dry_run)
            dropped += d
            total_bytes += b
        if not args.dry_run:
            trimmed = trim_to_fights(conn, row["id"])
            dropped += trimmed
            if dropped:
                with conn:
                    conn.execute(
                        "UPDATE sessions SET redacted_lines = redacted_lines + ? "
                        "WHERE id=?", (dropped, row["id"]))
        if dropped:
            print(f"session {row['id']:>5}: {dropped:>6} private lines removed")
        total_dropped += dropped

    verb = "would remove" if args.dry_run else "removed"
    print(f"\n{verb} {total_dropped} private lines across {len(seen)} files "
          f"({total_bytes / (1 << 20):.1f} MB reclaimed)")
    if args.dry_run:
        print("dry run — nothing was written")


if __name__ == "__main__":
    main()
