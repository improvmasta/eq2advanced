"""The logger's unannounced deaths (`pipeline/downs.py`).

Every case here is a shape taken from a real log — session 301 (Bobby,
2026-08-16) for the deaths EQ2 never printed, session 309 (Bronir, same night)
for the incapacitation that must not become one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser import parse_lines
from parser.events import F_INFERRED
from pipeline.downs import MIN_DOWN_S, infer_logger_deaths


# `parser.prefix` wants a real unix stamp, so the made-up shapes below count
# seconds from one — the same night the measured ones came from.
T = 1786931000


def stamp(ts: int, body: str) -> str:
    return f"({ts})[Sun Aug 16 00:00:00 2026] {body}\r\n"


def events(lines, logger="Bobby"):
    return infer_logger_deaths(list(parse_lines(lines, logger)), logger)


def deaths(evs):
    return [e for e in evs if e.type == "death"]


# The Malkonis D'Morte death, cut down: the last tick of a Lifeburn channel,
# then twenty seconds in which nothing at all touches the logger, then the rez.
MALKONIS = [
    stamp(1786931488, "YOUR Lifeburn hits Malkonis D'Morte for a critical of 25,857 disease damage."),
    stamp(1786931490, "YOUR Lifeburn hits Malkonis D'Morte for a critical of 25,857 disease damage."),
    stamp(1786931495, "Shalazad hits Malkonis D'Morte for a critical of 1,022 slashing damage."),
    stamp(1786931505, "Beaux hits Malkonis D'Morte for 256 disease damage."),
    stamp(1786931510, "You regain consciousness!"),
    stamp(1786931510, "Lotus's Lunar Attendant's Oracle's Blessing heals YOU for 284 hit points."),
]


def test_an_unpaired_revive_is_a_death_dated_to_the_last_thing_they_did():
    got = deaths(events(MALKONIS))
    assert len(got) == 1
    assert got[0].ts == 1786931490      # the last Lifeburn tick, not the rez
    assert got[0].tgt == "YOU"
    assert got[0].flags & F_INFERRED


def test_the_death_sits_before_the_revive_that_proved_it():
    """statsroll reads the stream in order: a death after its own revive would
    leave the dead clock running to the end of the fight."""
    evs = events(MALKONIS)
    types = [e.type for e in evs]
    assert types.index("death") < types.index("revive")
    assert all(a.ts <= b.ts for a, b in zip(evs, evs[1:])), "still time-ordered"


def test_running_twice_does_not_invent_a_second_death():
    """The live path re-runs this over its unflushed tail on every flush."""
    once = events(MALKONIS)
    twice = infer_logger_deaths(list(once), "Bobby")
    assert len(deaths(twice)) == 1
    assert [e.type for e in twice] == [e.type for e in once]


def test_a_heal_beating_the_death_timer_is_not_a_death():
    """Bronir, 14:13:28 -> 14:13:29. Incapacitated and healed back up."""
    lines = [
        stamp(1786907607, "Bronir hits Anguis for 500 crushing damage."),
        stamp(1786907608, "You lose consciousness!"),
        stamp(1786907609, "You regain consciousness!"),
    ]
    assert deaths(events(lines, "Bronir")) == []


def test_a_death_the_log_announced_is_not_counted_twice():
    lines = [
        stamp(1786928490, "YOU hit Mayong Mistmoore for 800 crushing damage."),
        stamp(1786928494, "Mayong Mistmoore has killed you."),
        stamp(1786928508, "You regain consciousness!"),
    ]
    got = deaths(events(lines))
    assert len(got) == 1
    assert not got[0].flags & F_INFERRED


def test_the_hole_is_measured_to_the_last_hit_taken_not_the_last_action():
    """Damage landing on somebody proves they were still a target. A death
    dated before it would bill the raid for time the player was up."""
    lines = [
        stamp(T, "YOU hit a bloodgorger for 800 crushing damage."),
        stamp(T + 4, "a bloodgorger hits YOU for 3,000 slashing damage."),
        stamp(T + 30, "You regain consciousness!"),
    ]
    got = deaths(events(lines))
    assert len(got) == 1 and got[0].ts == T + 4


def test_the_heal_that_ends_the_hole_cannot_erase_it():
    """The reviving heal shares its second with the revive line and the two
    arrive in either order — read forwards, that heal looks like proof of life
    at the moment the hole ends."""
    lines = [
        stamp(T, "YOU hit a bloodgorger for 800 crushing damage."),
        stamp(T + 30, "Lotus's Oracle's Blessing heals YOU for 284 hit points."),
        stamp(T + 30, "You regain consciousness!"),
    ]
    got = deaths(events(lines))
    assert len(got) == 1 and got[0].ts == T


def test_a_pet_swinging_over_the_corpse_is_not_proof_of_life():
    lines = [
        stamp(T, "YOU hit a bloodgorger for 800 crushing damage."),
        stamp(T + 10, "Bobby's blighted horde hits a bloodgorger for 900 crushing damage."),
        stamp(T + 30, "You regain consciousness!"),
    ]
    got = deaths(events(lines))
    assert len(got) == 1 and got[0].ts == T


def test_a_hole_under_the_floor_is_left_alone():
    lines = [
        stamp(T, "YOU hit a bloodgorger for 800 crushing damage."),
        stamp(T + MIN_DOWN_S - 1, "You regain consciousness!"),
    ]
    assert deaths(events(lines)) == []


def test_another_raiders_revive_is_none_of_this_pass_business():
    """Everyone else's killer-less death IS logged — the raid gets the "Alas"
    broadcast for it — so there is nothing to recover and no hole to read."""
    lines = [
        stamp(T, "YOU hit a bloodgorger for 800 crushing damage."),
        stamp(T + 30, "Beaux is revived!"),
    ]
    assert deaths(events(lines)) == []
