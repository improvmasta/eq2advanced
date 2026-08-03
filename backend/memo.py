"""In-process memo for the two expensive read paths: the run raid report and
the multi-encounter aggregate.

Both are pure functions of rows that only change when something is written, and
both are slow enough to feel: the raid report for a 60-fight night replays every
stored event once (~1.5s on the Emerald Halls run), and the zone page asks for
it again on every visit. Clicking a zone should not re-earn that.

Correctness rests on one rule: **every write bumps the epoch and empties the
map**. `rebuild_zone_runs` is the funnel — uploads, live sessions, reparses,
deletes and hand edits all end there — plus `prune_once`, which deletes events
without touching run membership. A cached payload therefore cannot outlive the
data it was built from, and a build racing a write is discarded rather than
stored under the new epoch.

Entries are whole response payloads, so the cap is small and deliberate.
Authorization happens before the memo is consulted (the callers resolve
visibility first), and nothing user-specific is keyed here.
"""

import threading
from collections import OrderedDict

MAX_ENTRIES = 12

_lock = threading.Lock()
_epoch = 0
_cache: OrderedDict = OrderedDict()


def invalidate() -> None:
    """Called from every write path. Cheap, and never wrong."""
    global _epoch
    with _lock:
        _epoch += 1
        _cache.clear()


def get_or_build(key, build):
    """Cached `build()` for `key` (any hashable). The result is shared, so
    callers must treat it as read-only — copy before adding fields to it."""
    with _lock:
        epoch = _epoch
        hit = _cache.get((epoch, key))
        if hit is not None:
            _cache.move_to_end((epoch, key))
            return hit
    value = build()
    with _lock:
        if epoch == _epoch:      # a write landed mid-build: drop this answer
            _cache[(epoch, key)] = value
            while len(_cache) > MAX_ENTRIES:
                _cache.popitem(last=False)
    return value


def stats() -> dict:
    with _lock:
        return {"epoch": _epoch, "entries": len(_cache)}
