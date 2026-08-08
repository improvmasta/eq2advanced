"""`live.in_combat` — the nav's Idle / In Combat light, and the dashboard's
answer to which of several live sessions is the one being played.

It answers from the in-memory tail alone: no DB, no snapshot building, and it
must not claim a fight is running when the plugin stopped sending mid-pull
(only a later batch can close an open segment, so a dead uploader would
otherwise leave the light on forever)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import live
from pipeline.encounters import GAP_S


def _state(session_id, open_start_ts=None, last_line_ts=None, open_end_ts=None):
    st = live.LiveState(session_id, "Bobby")
    st.open_start_ts = open_start_ts
    st.open_end_ts = open_end_ts
    st.last_line_ts = last_line_ts
    live._states[session_id] = st
    return st


def teardown_function():
    live._states.clear()


def test_no_state_is_not_in_combat():
    assert live.in_combat(424242) is False


def test_an_open_segment_with_a_fresh_tail_is_in_combat():
    now = int(time.time())
    _state(1, open_start_ts=now - 30, last_line_ts=now - 2)
    assert live.in_combat(1) is True


def test_no_open_segment_is_between_pulls():
    now = int(time.time())
    _state(2, open_start_ts=None, last_line_ts=now)
    assert live.in_combat(2) is False


def test_an_open_segment_whose_damage_stopped_is_not_in_combat():
    """The writer keeps the segment open for CLOSE_S in case a late kill line
    joins it. Combat itself stopped at GAP_S, and this light — which is also
    how the dashboard decides which client is being played — has to mean the
    fight is happening, not that its paperwork is still open."""
    now = int(time.time())
    st = _state(4, open_start_ts=now - 60, last_line_ts=now, open_end_ts=now - 2)
    assert live.in_combat(4) is True
    st.open_end_ts = now - GAP_S
    assert live.in_combat(4) is False


def test_an_abandoned_open_segment_goes_dark():
    """The plugin died mid-fight: log time stops advancing, and after
    LIVE_LAG_S the light must go out even though the segment never closed."""
    now = int(time.time())
    _state(3, open_start_ts=now - 600, last_line_ts=now - live.LIVE_LAG_S - 5)
    assert live.in_combat(3) is False
