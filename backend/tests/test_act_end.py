"""`/act end` typed in game ends the fight, exactly as it does in ACT.

EQ2 has no `/act` command; the client rejects it into the log
(`Unknown command: 'act end'` — the same shape as the 9 real `'lbtell'`
rejections in the golden fixture) and that rejection is the whole channel. ACT's
own EQ2 plugin reads it off the log line, so a raider who ends a pull with a
macro ends it on this site too, with nothing else configured.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser import parse_lines
from pipeline.encounters import segment_events

T0 = 1722556800
LOGGER = "Bobby"


def line(t, body):
    return f"({T0 + t})[Thu Aug  1 21:00:00 2026] {body}\r\n"


HIT = "YOU hit a training dummy for 100 crushing damage."


def segs(lines):
    events = list(parse_lines(iter(lines), LOGGER))
    return events, segment_events(events, LOGGER)


def test_a_continuous_fight_is_one_segment():
    """The control: nothing here is 7s apart, so silence alone never cuts it."""
    _, s = segs([line(t, HIT) for t in (0, 2, 4, 6, 8, 10)])
    assert len(s) == 1
    assert (s[0].start_ts, s[0].end_ts) == (T0, T0 + 10)


def test_act_end_splits_a_fight_the_silence_would_not():
    _, s = segs([line(t, HIT) for t in (0, 2, 4)]
                + [line(5, "Unknown command: 'act end'")]
                + [line(t, HIT) for t in (6, 8, 10)])
    assert len(s) == 2
    assert (s[0].start_ts, s[0].end_ts) == (T0, T0 + 4)
    assert (s[1].start_ts, s[1].end_ts) == (T0 + 6, T0 + 10)
    assert s[0].ended_by_cmd and not s[1].ended_by_cmd


def test_the_marker_belongs_to_no_segment():
    """Like a zone line: it cuts, it is not part of what it cuts."""
    events, s = segs([line(0, HIT), line(1, "Unknown command: 'act end'"),
                      line(2, HIT)])
    marker = next(i for i, ev in enumerate(events) if ev.type == "encounter_end")
    assert all(marker not in seg.event_indices for seg in s)


def test_nothing_trails_into_a_fight_the_raid_ended():
    """A kill line inside the grace window joins a segment that timed out. Once
    the raid has ended the pull there is nothing left to join: the kill belongs
    to whatever comes next."""
    events, s = segs([line(0, HIT), line(2, HIT),
                      line(3, "Unknown command: 'act end'"),
                      line(4, "You have killed a training dummy.")])
    assert len(s) == 1
    kill = next(i for i, ev in enumerate(events) if ev.type == "kill")
    assert kill not in s[0].event_indices


def test_act_end_between_pulls_does_nothing():
    _, s = segs([line(0, "Unknown command: 'act end'"), line(1, HIT),
                 line(3, HIT)])
    assert len(s) == 1
    assert (s[0].start_ts, s[0].end_ts) == (T0 + 1, T0 + 3)
    assert not s[0].ended_by_cmd
