#!/usr/bin/env python3
"""Replay a log file against the live ingest contract — the reference client
the ACT uploader DLL will mirror. stdlib only.

    python backend/tools/simulate_live.py /home/lindsay/bobby.txt \
        --host http://10.1.1.15:8450 --token <device-token> \
        --character Bobby --cadence 2 --window 2 [--restamp] [--done]

Batches are cut on log-time boundaries (--window seconds of log per batch) so a
second's lines never split across batches; --cadence is the real-time delay
between sends (0 = as fast as possible). --done closes the session at EOF.

--restamp replays the file as if the raid were happening right now, which is
what the live dashboard needs: its in-flight snapshots are deliberately gated
on log time being near the clock, so an old log replayed verbatim produces
fight cards but no live meter.
"""

import argparse
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PREFIX_RE = re.compile(r"^\((\d{10})\)")


def request(host: str, path: str, token: str, payload=None):
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        data = gzip.compress(json.dumps(payload).encode())
        headers.update({"Content-Type": "application/json", "Content-Encoding": "gzip"})
    req = urllib.request.Request(host + path, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", "2")))
                continue
            sys.exit(f"{path} -> HTTP {e.code}: {e.read().decode()[:300]}")
        except urllib.error.URLError as e:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    sys.exit(f"{path}: gave up after retries")


def first_ts(path: str) -> int | None:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = PREFIX_RE.match(line)
            if m:
                return int(m.group(1))
    return None


def batches(path: str, window: int, shift: int = 0):
    """Yield lists of verbatim lines, cut when log time crosses a window edge.

    `shift` moves every stamp by a constant (--restamp), which is the only way
    to replay an old log as a raid happening NOW. The live dashboard's
    snapshots are gated on log time being close to the clock, so a raid from
    March must not read as a pull in progress — see pipeline/live.py."""
    batch, edge = [], None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = PREFIX_RE.match(line)
            ts = int(m.group(1)) + shift if m else None
            if ts is not None:
                if shift:
                    line = f"({ts})" + line[m.end():]
                if edge is None:
                    edge = ts - (ts % window) + window
                elif ts >= edge:
                    if batch:
                        yield batch
                    batch = []
                    edge = ts - (ts % window) + window
            batch.append(line)
    if batch:
        yield batch


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file")
    ap.add_argument("--host", default="http://127.0.0.1:8450")
    ap.add_argument("--token", required=True)
    ap.add_argument("--cadence", type=float, default=2.0,
                    help="real seconds between batches (0 = flat out)")
    ap.add_argument("--window", type=int, default=2,
                    help="log seconds per batch")
    ap.add_argument("--mode", choices=("live", "backfill"), default="live")
    ap.add_argument("--character",
                    help="whose log this is; the plugin reads it off the file "
                         "name (eq2log_<Name>.txt) and so does this")
    ap.add_argument("--restamp", action="store_true",
                    help="shift every stamp so the log starts NOW — what the "
                         "live dashboard needs to treat a replay as a raid")
    ap.add_argument("--done", action="store_true",
                    help="POST /ingest/backfill/done after the last batch")
    args = ap.parse_args()

    character = args.character
    if not character:
        m = re.match(r"eq2log_([A-Za-z]+)", Path(args.file).name)
        character = m.group(1).capitalize() if m else Path(args.file).stem.capitalize()

    hello = request(args.host, "/api/ingest/hello", args.token)
    print(f"hello: account={hello['account']} character={character} "
          f"session={hello['session']}")

    shift = 0
    if args.restamp:
        base = first_ts(args.file)
        if base is None:
            sys.exit("--restamp: no timestamped lines in that file")
        shift = int(time.time()) - base
        print(f"restamp: shifting log time by {shift}s")

    sent = accepted = duplicates = 0
    for batch in batches(args.file, args.window, shift):
        resp = request(args.host, "/api/ingest/batch", args.token, {
            "batch_id": str(uuid.uuid4()),
            "mode": args.mode,
            "character": character,
            "lines": batch,
        })
        sent += 1
        accepted += resp["accepted"]
        duplicates += resp["duplicates"]
        print(f"\rbatch {sent}: session={resp['session_id']} "
              f"accepted={accepted} duplicates={duplicates}", end="", flush=True)
        if args.cadence:
            time.sleep(args.cadence)
    print()

    if args.done:
        resp = request(args.host, "/api/ingest/backfill/done", args.token, {})
        print(f"done: finalized session {resp['session_id']}")


if __name__ == "__main__":
    main()
