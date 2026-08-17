"""Recover rejected public chat from an original, unredacted EQ2 logfile.

Channel numbers in EQ2 logs are per-character slots, not global channel ids.
Before that was understood, `/chat` accepted only General (2), LFG (3), and
Auction (10), silently rejecting the same public channels in other slots.

This tool repairs a bounded window from the player's ORIGINAL logfile. Server
raw chunks cannot be used: public chat is deliberately redacted before those
chunks are stored.

Preview is the default and writes nothing::

    .venv/bin/python backend/tools/recover_chat.py \
      /path/to/eq2log_Ross.txt \
      --start 2026-08-17T00:00:00 --end 2026-08-17T09:00:00

After checking the counts, repeat with ``--apply``. Naive timestamps use the
timezone supplied by ``--timezone`` (America/New_York by default); ``--end`` is
exclusive. The recovery inserts only `chat_messages`. It deliberately does not
send historical Discord alerts or inject old messages into the live SSE tail.
"""

from __future__ import annotations

import argparse
import gzip
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_db  # noqa: E402
from parser.prefix import split_prefix  # noqa: E402
from pipeline import chatbus  # noqa: E402


_LOG_NAME_RE = re.compile(r"^eq2log_(?P<name>[A-Za-z]+)", re.I)
_CHANNEL_SHAPE_RE = re.compile(
    r'^(?:\\aPC -?\d+ [^\\]*\\/a tells|You tell) '
    r'(?P<name>[A-Za-z]+) \((?P<number>\d+)\), "')


def _when(value: str, timezone: str) -> int:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
        return int(parsed.timestamp())
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise argparse.ArgumentTypeError(f"invalid date/time {value!r}: {exc}") from exc


def _character(path: Path, override: str | None) -> str:
    if override:
        return override
    match = _LOG_NAME_RE.match(path.name)
    if match:
        return match.group("name")
    raise ValueError(
        f"cannot derive character from {path.name!r}; use --character")


def _lines(path: Path):
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="surrogateescape") as src:
        yield from src


def recover_paths(conn, paths: list[Path], start: int, end: int,
                  character: str | None = None, apply: bool = False) -> dict:
    """Preview or insert public chat in ``start <= ts < end``.

    Dedupe is deliberately identical to the live archive: channel, speaker and
    text inside ``DEDUPE_WINDOW_S`` is one message even when uploader clocks
    differ slightly. Preview tracks would-be rows in memory so its result is
    the same as apply without touching the database.
    """
    if end <= start:
        raise ValueError("--end must be later than --start")

    # Seed the public result keys so callers and the CLI get stable zeroes for
    # an empty window or a fully deduplicated re-run.
    stats = Counter({
        "files": len(paths),
        "lines_scanned": 0,
        "malformed": 0,
        "outside_window": 0,
        "public_candidates": 0,
        "duplicates": 0,
        "would_insert": 0,
        "channel_general": 0,
        "channel_lfg": 0,
        "channel_auction": 0,
    })
    slots = Counter()
    staged: dict[tuple[str, str, str], list[int]] = defaultdict(list)

    for path in paths:
        logger = _character(path, character)
        for raw in _lines(path):
            stats["lines_scanned"] += 1
            split = split_prefix(raw.rstrip("\r\n"))
            if split is None:
                stats["malformed"] += 1
                continue
            ts, body = split
            if not start <= ts < end:
                stats["outside_window"] += 1
                continue
            msg = chatbus.parse_chat(ts, body, logger)
            if msg is None:
                continue
            stats["public_candidates"] += 1
            shape = _CHANNEL_SHAPE_RE.match(body)
            if shape:
                slots[f"{shape.group('name')} ({shape.group('number')})"] += 1

            key = (msg["ch"], msg["who"], msg["text"])
            if any(abs(ts - other) <= chatbus.DEDUPE_WINDOW_S
                   for other in staged[key]):
                stats["duplicates"] += 1
                continue
            if conn.execute(
                    "SELECT 1 FROM chat_messages WHERE ch=? AND ts BETWEEN ? AND ? "
                    "AND who=? AND text=? LIMIT 1",
                    (msg["ch"], ts - chatbus.DEDUPE_WINDOW_S,
                     ts + chatbus.DEDUPE_WINDOW_S, msg["who"],
                     msg["text"])).fetchone():
                stats["duplicates"] += 1
                continue

            if apply:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO chat_messages (ts, ch, who, text) "
                    "VALUES (?,?,?,?)",
                    (ts, msg["ch"], msg["who"], msg["text"]))
                if not cur.rowcount:
                    stats["duplicates"] += 1
                    continue
            staged[key].append(ts)
            stats["would_insert"] += 1
            stats[f"channel_{msg['ch']}"] += 1

    return {**stats, "slots": dict(sorted(slots.items()))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", type=Path,
                    help="original eq2log_<Character>.txt files (plain or gzip)")
    ap.add_argument("--start", required=True,
                    help="inclusive ISO date/time")
    ap.add_argument("--end", required=True,
                    help="exclusive ISO date/time")
    ap.add_argument("--timezone", default="America/New_York",
                    help="timezone for timestamps without an offset")
    ap.add_argument("--character",
                    help="character name when it cannot be read from the filename")
    ap.add_argument("--apply", action="store_true",
                    help="insert rows; without this flag the command is a preview")
    args = ap.parse_args()

    missing = [str(path) for path in args.paths if not path.is_file()]
    if missing:
        ap.error("not a file: " + ", ".join(missing))
    try:
        start = _when(args.start, args.timezone)
        end = _when(args.end, args.timezone)
        conn = get_db()
        if args.apply:
            with conn:
                stats = recover_paths(conn, args.paths, start, end,
                                      args.character, apply=True)
        else:
            stats = recover_paths(conn, args.paths, start, end,
                                  args.character, apply=False)
    except (ValueError, OSError) as exc:
        ap.error(str(exc))

    action = "inserted" if args.apply else "would insert"
    print(f"scanned {stats.get('lines_scanned', 0):,} lines in "
          f"{stats.get('files', 0)} file(s)")
    print(f"found {stats.get('public_candidates', 0):,} public candidates; "
          f"{stats.get('duplicates', 0):,} already represented")
    print(f"{action} {stats.get('would_insert', 0):,}: "
          f"General {stats.get('channel_general', 0):,}, "
          f"LFG {stats.get('channel_lfg', 0):,}, "
          f"Auction {stats.get('channel_auction', 0):,}")
    if stats["slots"]:
        print("observed slots: " + ", ".join(
            f"{slot}={count:,}" for slot, count in stats["slots"].items()))
    if not args.apply:
        print("preview only — nothing was written; repeat with --apply")


if __name__ == "__main__":
    main()
