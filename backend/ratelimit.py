"""In-process failure counter for the credential routes.

Two things here are guessable by brute force and nothing else protects them:
a username/password pair (no email, no 2FA) and a group's 6-digit join code —
a 1,000,000-code space that a script walks in minutes. So both are counted.

Only FAILURES are counted; a success clears the bucket. Buckets are keyed
independently (per identity AND per client address) and the caller checks both,
so one attacker can't spend someone else's budget and lock them out — the
address bucket bites first.

State is a plain dict in this process: it resets on restart and does not survive
multiple workers. The app runs one uvicorn worker (see start.sh), and a restart
resetting the counters is an acceptable ceiling for a self-hosted parse site —
it is not a substitute for fail2ban at the edge.
"""

import threading
import time

WINDOW_S = 900          # 15 minutes
MAX_FAILURES = 5        # then the bucket is locked for the rest of its window

_lock = threading.Lock()
_buckets: dict[tuple[str, str], list] = {}   # (scope, key) -> [count, window_start]


def _bucket(scope: str, key: str, now: float) -> list:
    b = _buckets.get((scope, key))
    if b is None or now - b[1] > WINDOW_S:
        b = [0, now]
        _buckets[(scope, key)] = b
    return b


def retry_after(scope: str, key: str) -> int:
    """Seconds the caller must wait, or 0 if they may try now."""
    if not key:
        return 0
    now = time.time()
    with _lock:
        _sweep(now)
        b = _bucket(scope, key, now)
        if b[0] < MAX_FAILURES:
            return 0
        return max(1, int(WINDOW_S - (now - b[1])))


def fail(scope: str, key: str) -> None:
    if not key:
        return
    now = time.time()
    with _lock:
        _bucket(scope, key, now)[0] += 1


def clear(scope: str, key: str) -> None:
    """A success wipes the record — an honest user who mistypes twice is not
    then locked out for fifteen minutes."""
    with _lock:
        _buckets.pop((scope, key), None)


def _sweep(now: float) -> None:
    if len(_buckets) < 512:
        return
    for k, b in list(_buckets.items()):
        if now - b[1] > WINDOW_S:
            del _buckets[k]


def reset_all() -> None:
    """Tests only."""
    with _lock:
        _buckets.clear()
