"""The reflect window: a state the raid is standing in, not a cast it is waiting for.

Two things these tests hold, and they are the two ways this feature goes wrong.

WHICH MOBS. It is an allowlist (`refdata/reflect_windows.json`), never a
detection. Nine mobs in the corpus reflect something and only one of them is
worth interrupting somebody's rotation over; a panel that announces the other
eight is a panel people stop reading. So an uncurated mob reflecting a hundred
casts must produce nothing at all.

WHERE THE WINDOW ENDS. The duration is the curated fact and the clustering rule
both, so a window can never be reported longer than the mechanic is (`aoes.
reflect_bursts`) — which is the failure a gap threshold has and this does not.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.events import ParsedEvent, Subject
from pipeline import livemeter
from pipeline.aoes import REFLECT_EDGE_S, reflect_bursts, reflect_windows

T0 = 1754500000
BOSS = "Treyloth D'Kulvith"
OTHER = "Vampire Lord Mayong Mistmoore"      # reflects, deliberately not curated
CASTERS = [f"Raider{i}" for i in range(1, 7)]


def denied(ts, caster, mob=BOSS, ability="Crystal Blast"):
    """`<caster> tries to zap <mob> with <ability>, but <mob> reflects.`"""
    return ParsedEvent(ts, "avoid", src=Subject(caster, "unknown"), tgt=mob,
                       ability=ability, extra={"how": "reflect", "avoider": mob})


def returned(ts, victim, amount=600, mob=BOSS, ability="Crystal Blast"):
    """The reflected spell coming back — re-attributed to the MOB, still
    wearing the player's spell name."""
    return ParsedEvent(ts, "damage", src=Subject(mob, "unknown"), tgt=victim,
                       ability=ability, amount=amount, dtype="magic")


def missed(ts, caster, mob=BOSS):
    """An ordinary avoid, so the reflect filter cannot just be matching `avoid`."""
    return ParsedEvent(ts, "avoid", src=Subject(caster, "unknown"), tgt=mob,
                       ability="Ice Comet", extra={"how": "parry", "avoider": mob})


def window(ts, mob=BOSS, casters=CASTERS, spread=6):
    """One window's worth of denies, spread over its first `spread` seconds."""
    return [denied(ts + (i % spread), c, mob) for i, c in enumerate(casters)]


def rows(events, now_ts):
    fight = livemeter.build_snapshot(events, "Bobby", "Freethinker Hideout",
                                     T0, None, livemeter.NO_KNOWLEDGE, now_ts)
    return [r for r in fight["aoes"] if r.get("kind") == "reflect"]


# --- which mobs get one ---------------------------------------------------

def test_the_curated_mob_gets_a_row():
    [row] = rows(window(T0 + 10), now_ts=T0 + 20)
    assert row["source"] == BOSS
    assert row["window_s"] == 30
    assert row["started_ts"] == T0 + 10
    assert row["ends_ts"] == T0 + 40


def test_an_uncurated_mob_that_reflects_just_as_hard_gets_nothing():
    """The whole point of the allowlist. Mayong reflects more casts per fight
    than Treyloth does and the raid does not call it, because it has never cost
    anybody more than a quarter of their health. Detection cannot tell those
    apart; a person can, and did."""
    assert OTHER not in reflect_windows()
    assert rows(window(T0 + 10, mob=OTHER) * 3, now_ts=T0 + 20) == []


def test_an_ordinary_avoid_is_not_a_reflect():
    assert rows([missed(T0 + 10, c) for c in CASTERS], now_ts=T0 + 20) == []


# --- where the window ends ------------------------------------------------

def test_the_countdown_runs_to_the_curated_duration():
    [row] = rows(window(T0 + 10), now_ts=T0 + 20)
    assert row["period_s"] == 30.0
    assert row["period_src"] == "curated"
    # `next_due_ts` is reused verbatim by the browser's drain and digits; on
    # this row it means the window ENDS, not that anything is next
    assert row["next_due_ts"] == row["ends_ts"]


def test_a_deny_a_second_past_the_duration_is_still_the_same_window():
    """A log stamps whole seconds, so a 30s state can print its last deny at
    +31. Taking the duration literally split one of 18 measured Treyloth
    windows into a real one plus a spurious one-cast fragment."""
    evs = window(T0 + 10) + [denied(T0 + 41, "Raider1")]
    [row] = rows(evs, now_ts=T0 + 42)
    assert row["started_ts"] == T0 + 10
    assert row["casts"] == len(CASTERS) + 1


def test_the_slack_is_membership_only_and_never_stretches_the_bar():
    evs = window(T0 + 10) + [denied(T0 + 30 + REFLECT_EDGE_S, "Raider1")]
    [row] = rows(evs, now_ts=T0 + 35)
    assert row["ends_ts"] == T0 + 40       # not T0 + 40 + REFLECT_EDGE_S
    assert row["period_s"] == 30.0


def test_a_burst_can_never_run_longer_than_the_mechanic():
    """The property a gap threshold cannot offer. Whatever the stamps do, no
    window spans more than the curated duration plus the second of slack."""
    dense = list(range(0, 400, 3))        # a deny every three seconds, forever
    for burst in reflect_bursts(dense, 30):
        assert burst[-1] - burst[0] <= 30 + REFLECT_EDGE_S


def test_a_window_that_has_not_started_yet_is_not_counted_down_to():
    """Live ingest cannot produce this — events arrive up to now and no
    further — but REPLAY can, and did: the whole fight's events exist while a
    cursor moves through them, so taking the last burst outright put the third
    window's countdown on screen from the pull timer, draining five minutes
    toward a mechanic that had not fired."""
    evs = window(T0 + 10) + window(T0 + 300)
    [row] = rows(evs, now_ts=T0 + 20)
    assert row["started_ts"] == T0 + 10


def test_a_later_window_is_its_own_window():
    evs = window(T0 + 10) + window(T0 + 120)
    [row] = rows(evs, now_ts=T0 + 130)
    # only the CURRENT one is reported — a window that closed 80s ago is
    # history, and this panel is about the next few seconds
    assert row["started_ts"] == T0 + 120
    assert row["casts"] == len(CASTERS)


# --- what the row says ----------------------------------------------------

def test_it_counts_the_casts_eaten_and_who_threw_them():
    evs = window(T0 + 10) + [denied(T0 + 12, "Raider1", ability="Ice Comet")]
    [row] = rows(evs, now_ts=T0 + 20)
    assert row["casts"] == len(CASTERS) + 1
    assert row["casters"] == len(CASTERS)


def test_the_damage_is_what_came_back_at_the_caster():
    """A reflected spell returns to whoever threw it — 113 of 115 pairings on
    the fight this was measured against. Pairing on (ability, caster) rather
    than counting the mob's damage during the window is what keeps the number
    honest: the boss is doing plenty of other things in those 30 seconds."""
    evs = [denied(T0 + 10, "Raider1"), returned(T0 + 10, "Raider1", 600),
           denied(T0 + 12, "Raider2"), returned(T0 + 13, "Raider2", 900),
           # the boss's own output in the same window, which is not this
           ParsedEvent(T0 + 14, "damage", src=Subject(BOSS, "unknown"),
                       tgt="Raider3", ability="Searing Rot", amount=50000)]
    [row] = rows(evs, now_ts=T0 + 20)
    assert row["damage"] == 1500


def test_a_return_that_lands_on_somebody_else_is_not_paired_to_the_caster():
    evs = [denied(T0 + 10, "Raider1"), returned(T0 + 10, "Raider2", 600)]
    [row] = rows(evs, now_ts=T0 + 20)
    assert row["damage"] == 0


def test_one_return_is_paired_once():
    """Two casters, one return line — the second cast does not get to claim
    the first one's damage a second time."""
    evs = [denied(T0 + 10, "Raider1"), denied(T0 + 10, "Raider1"),
           returned(T0 + 10, "Raider1", 600)]
    [row] = rows(evs, now_ts=T0 + 20)
    assert row["damage"] == 600


# --- when it leaves -------------------------------------------------------

def test_it_stays_briefly_after_it_ends_to_say_so():
    [row] = rows(window(T0 + 10), now_ts=T0 + 40 + livemeter.REFLECT_CLEAR_S)
    assert row["ends_ts"] == T0 + 40


def test_and_then_it_goes():
    assert rows(window(T0 + 10),
                now_ts=T0 + 41 + livemeter.REFLECT_CLEAR_S) == []


def test_it_leads_the_panel():
    """The one row here allowed to jump the queue. `MAX_AOES` and the panel's
    "rows do not move" ordering both protect rows somebody learns by position
    over a whole fight; this one lives for 30 seconds and is the only thing on
    screen that is true right now."""
    evs = window(T0 + 10) + [
        d for i in range(3)
        for d in (ParsedEvent(T0 + 10 + i, "damage", src=Subject(BOSS, "unknown"),
                              tgt=c, ability="Stench of Death", amount=900,
                              dtype="disease")
                  for c in CASTERS)]
    fight = livemeter.build_snapshot(evs, "Bobby", "Freethinker Hideout", T0,
                                     None, livemeter.NO_KNOWLEDGE, T0 + 20)
    assert fight["aoes"][0]["kind"] == "reflect"
