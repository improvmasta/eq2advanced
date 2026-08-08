"""The doorbell: a stream is woken when a snapshot exists, instead of asking.

The dashboard and the stream overlay are SSE loops that want one thing — the
newest `partial` for a session. They used to get it by waking on a timer and
looking (`STREAM_POLL_S`, 1.5s), which put an average of ~0.75s of pure waiting
between a batch landing and the screen showing it, on top of the plugin's own
send cadence. That was the largest term left in the chain after the plugin was
retuned, and it was the one costing nothing to remove: the snapshot is built in
this process, by `pipeline/live.py`, so it can just say so.

Two properties matter and both are about NOT missing an edge:

* **Subscribe, then read.** A subscriber holds its event across the read, so a
  snapshot published while it is reading leaves the event set and the next wait
  returns at once. Subscribing after the read would drop exactly the update
  that arrived during it — the race that makes a push system feel worse than a
  poll, because it only shows up under load.
* **The timeout stays.** `wait` is a wake-up, never a guarantee: a caller that
  only ever woke on a publish would stop refreshing `mark_watched`, would never
  notice a fight card or a session going `ready`, and would hang forever if a
  publish were ever lost. So every loop still has its own fallback tick — the
  bus makes it FAST, it does not make it correct.

Thread-crossing is the whole implementation. Snapshots are published from the
ingest request thread (`live.process_batch` runs in a threadpool) and the
waiters live in the event loop, so each waiter records its loop and a publisher
rings it with `call_soon_threadsafe`. Process-local, like `live.py`'s own state
and `replaybus.py`: one uvicorn process serves this app.
"""

from __future__ import annotations

import asyncio
import threading

# session_id -> the events waiting on it, each with the loop it belongs to.
_waiters: dict[int, set["_Waiter"]] = {}
_lock = threading.Lock()


class _Waiter:
    __slots__ = ("event", "loop")

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.loop = asyncio.get_running_loop()

    def ring(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.event.set)
        except RuntimeError:
            # the loop this stream lived in is gone; its unsubscribe is racing
            # us and the stream is over either way
            pass


class Subscription:
    """One stream's doorbell. Use it as a context manager, around the whole
    read-and-yield body — see the note about subscribing before reading.

    A key of `None` is a subscription to NOTHING, and it is deliberate rather
    than a guard the callers should be making: the overlay re-resolves which
    session it is watching on every pass and often the answer is "none yet" or
    "a replay, which has no bell". Those passes still have to wait, and they
    should read like every other pass rather than forking the loop."""

    def __init__(self, key: int | None) -> None:
        self._key = key
        self._waiter: _Waiter | None = None

    def __enter__(self) -> "Subscription":
        if self._key is None:
            return self
        self._waiter = _Waiter()
        with _lock:
            _waiters.setdefault(self._key, set()).add(self._waiter)
        return self

    def __exit__(self, *exc) -> None:
        if self._waiter is None:
            return None
        with _lock:
            group = _waiters.get(self._key)
            if group is not None:
                group.discard(self._waiter)
                if not group:
                    _waiters.pop(self._key, None)
        self._waiter = None
        return None

    async def wait(self, timeout: float) -> bool:
        """Sleep until something is published for this key, or `timeout`.

        Returns True if it was rung. The event is cleared BEFORE the caller
        goes on to read, so a publish that lands while it reads is kept and
        answered by the next wait rather than being swallowed.
        """
        waiter = self._waiter
        if waiter is None:                      # not subscribed: behave as a sleep
            await asyncio.sleep(timeout)
            return False
        try:
            await asyncio.wait_for(waiter.event.wait(), timeout)
            rung = True
        except asyncio.TimeoutError:
            rung = False
        waiter.event.clear()
        return rung


def subscribe(key: int | None) -> Subscription:
    return Subscription(key)


def publish(key: int) -> None:
    """A new snapshot exists for this session. Safe from any thread; costs
    nothing when nobody is watching, which is the normal case."""
    with _lock:
        group = list(_waiters.get(key, ()))
    for waiter in group:
        waiter.ring()


def waiting(key: int) -> int:
    """How many streams are parked on this session — for tests."""
    with _lock:
        return len(_waiters.get(key, ()))
