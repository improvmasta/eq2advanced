"""The last frame a replay produced, so a second reader can watch it too.

A replay (`routers/replay_api.py`) is a per-request generator: it parses a
recorded fight off disk and walks a cursor through it, and what it produces
goes down the one SSE connection that asked for it. That is exactly right for
the dashboard, and it made the stream overlay untestable — the overlay reads
the LIVE snapshot, so the only way to see what a viewer sees was to raid.

So a replay also drops its latest frame here, keyed by the account running it,
and the overlay picks it up. Three properties are what make that safe to do:

* **It is keyed by USER, and the overlay only reads its own owner's key.** An
  overlay token belongs to an account; a replay is started by an account that
  already passed `require_curator` and `visible_encounters`. Nothing crosses.
* **It expires.** A frame older than `MAX_AGE_S` is not answered, so a replay
  that ended (or a browser tab that was closed mid-fight) stops feeding the
  overlay on its own — there is no "stop" message to lose.
* **It is a view of a view.** Same promise `livemeter` makes: nothing here is
  written, read back as a record, or allowed to reach a parse page.

Process-local, like `pipeline/live.py`'s own state — one dict, last write wins.
"""

from __future__ import annotations

import time

# user_id -> (monotonic seconds, payload). One slot per account: a person
# replaying two fights at once is watching neither.
_LAST: dict[int, tuple[float, dict]] = {}

# A replay ticks every `replay_api.TICK_S` (2s). This is generous enough that a
# slow frame does not blink the overlay back to "between pulls", and short
# enough that a finished replay releases the screen within a few seconds.
MAX_AGE_S = 8.0


def publish(user_id: int, payload: dict) -> None:
    """Hand the overlay the frame the dashboard is looking at."""
    _LAST[user_id] = (time.monotonic(), payload)


def latest(user_id: int) -> dict | None:
    """That account's current replay frame, or None if there isn't a fresh one."""
    hit = _LAST.get(user_id)
    if hit is None:
        return None
    at, payload = hit
    if time.monotonic() - at > MAX_AGE_S:
        _LAST.pop(user_id, None)
        return None
    return payload


def clear(user_id: int) -> None:
    _LAST.pop(user_id, None)
