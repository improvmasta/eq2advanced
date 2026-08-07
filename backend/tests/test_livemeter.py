"""The in-flight view: what the dashboard shows about a fight still running.

Two things these tests exist to hold. First, the numbers mean the same as the
recorded ones — a self-hit is not damage dealt on either path, or the meter
would disagree with the parse it turns into thirty seconds later. Second, the
view stays a view: no rows, no snapshot for a backfill, and nothing computed
for a session nobody has open.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from parser.events import F_SELF_FOCUS, ParsedEvent, Subject
from pipeline import livemeter

T0 = 1754500000
RAIDERS = [f"Raider{i}" for i in range(1, 9)]


def dmg(ts, src, tgt, amount=1000, ability="Fireball", unit="unknown", flags=0):
    return ParsedEvent(ts, "damage", src=Subject(src, unit), tgt=tgt,
                       ability=ability, amount=amount, flags=flags)


def heal(ts, src, tgt, amount=500, unit="unknown"):
    return ParsedEvent(ts, "heal", src=Subject(src, unit), tgt=tgt,
                       ability="Healing Grove", amount=amount)


def snap(events, logger="Bobby", zone="The Estate of Unrest", start_ts=T0, roster=None):
    return livemeter.build_snapshot(events, logger, zone, start_ts, roster)


def actor(fight, name):
    return next(a for a in fight["actors"] if a["name"] == name)


# --- what the numbers mean ------------------------------------------------

def test_damage_and_dps_over_the_fight_so_far():
    """DPS divides by the fight's elapsed time, not by the actor's active
    window — the same denominator `roll_encounter` uses, so the live figure
    and the recorded one are the same statistic."""
    fight = snap([dmg(T0, "Bobby", "a training dummy", 1000, unit="player"),
                  dmg(T0 + 9, "Bobby", "a training dummy", 1000, unit="player")])
    bobby = actor(fight, "Bobby")
    assert bobby["damage"] == 2000
    assert bobby["dps"] == pytest.approx(2000 / 9, abs=0.1)
    assert fight["elapsed_s"] == 9
    assert fight["raid"]["damage"] == 2000


def test_self_damage_is_not_damage_dealt():
    """Vampiric Requiem and friends: ACT excludes self-inflicted damage from
    both Damage and DamageTaken, `roll_encounter` excludes it, and so does
    this. A live meter that counted it would fall as the fight closed."""
    fight = snap([dmg(T0, "Bobby", "a training dummy", 500, unit="player"),
                  dmg(T0 + 1, "Bobby", "Bobby", 300, unit="player",
                      flags=F_SELF_FOCUS),
                  dmg(T0 + 2, "Bobby", "Bobby", 200, unit="player")])
    bobby = actor(fight, "Bobby")
    assert bobby["damage"] == 500
    assert bobby["damage_taken"] == 0


def test_a_pet_credits_its_owner():
    """Credit is by NAME here — no EntityResolver — so a possessive pet has to
    roll up through the owner its subject already carries."""
    fight = snap([dmg(T0, "Tragedy", "a training dummy", 400, unit="player"),
                  dmg(T0 + 1, "Tragedy", "a training dummy", 600,
                      unit="swarm_pet")])
    assert actor(fight, "Tragedy")["damage"] == 1000
    assert [a["name"] for a in fight["actors"] if a["kind"] == "player"] == ["Tragedy"]


def test_mobs_get_rows_of_their_own_and_are_not_raid_damage():
    fight = snap([dmg(T0, "Bobby", "a training dummy", 1000, unit="player"),
                  dmg(T0 + 1, "a training dummy", "Bobby", 700)])
    assert actor(fight, "a training dummy")["kind"] == "mob"
    assert actor(fight, "Bobby")["damage_taken"] == 700
    # the headline is the RAID's damage; the boss's own row is not part of it
    assert fight["raid"]["damage"] == 1000


def test_heals_overheal_and_deaths():
    """Overheal is the same HP-deficit reconstruction `roll_encounter` does:
    a heal beyond what the target has lost is overheal."""
    fight = snap([dmg(T0, "a training dummy", "Raider1", 800),
                  heal(T0 + 1, "Mendya", "Raider1", 500),
                  heal(T0 + 2, "Mendya", "Raider1", 900),
                  ParsedEvent(T0 + 3, "death", tgt="Raider1")])
    mendya = actor(fight, "Mendya")
    assert mendya["heals"] == 1400
    assert mendya["overheal"] == 600      # 800 lost, 1400 healed
    assert actor(fight, "Raider1")["deaths"] == 1
    assert fight["raid"]["deaths"] == 1
    assert fight["raid"]["heals"] == 1400


def test_timeline_has_one_bucket_per_second_of_the_fight():
    fight = snap([dmg(T0, "Bobby", "a training dummy", 100, unit="player"),
                  dmg(T0 + 2, "Bobby", "a training dummy", 300, unit="player"),
                  heal(T0 + 2, "Mendya", "Bobby", 50)])
    assert fight["timeline"]["t0"] == T0
    assert fight["timeline"]["dmg"] == [100, 0, 300]
    assert fight["timeline"]["heal"] == [0, 0, 50]


def test_timeline_is_windowed_on_a_long_fight():
    """The chart scrolls, so an hour-long chain pull does not ship an hour of
    buckets on every batch."""
    span = livemeter.MAX_TIMELINE_S + 500
    fight = snap([dmg(T0, "Bobby", "a mob", 100, unit="player"),
                  dmg(T0 + span, "Bobby", "a mob", 100, unit="player")])
    assert len(fight["timeline"]["dmg"]) == livemeter.MAX_TIMELINE_S + 1


def test_actor_list_is_capped_and_ordered_by_damage():
    events = [dmg(T0, f"Raider{i}", "a mob", i * 10, unit="player")
              for i in range(1, livemeter.MAX_ACTORS + 10)]
    fight = snap(events)
    assert len(fight["actors"]) == livemeter.MAX_ACTORS
    assert fight["actors"][0]["damage"] > fight["actors"][-1]["damage"]


def test_class_comes_from_the_roster():
    fight = snap([dmg(T0, "Zylphax", "a mob", 100, unit="player")],
                 roster={"zylphax": "warlock"})
    assert actor(fight, "Zylphax")["class"] == "warlock"


# --- naming the fight before it ends --------------------------------------

def test_provisional_name_is_the_mob_taking_the_most_damage():
    """`encounter_label`'s rule, on names alone — the enemy the raid is
    fighting, not the one that happens to be dying."""
    fight = snap([dmg(T0, "Bobby", "a knotted guardian", 5000, unit="player"),
                  dmg(T0 + 1, "Bobby", "Treah Greenroot", 100, unit="player")])
    assert fight["provisional_name"] == "a knotted guardian"
    assert fight["provisional_is_named"] is False


def test_a_wipe_with_no_damage_dealt_is_named_after_what_is_killing_you():
    """The first seconds of a pull the raid loses: nothing has been dealt yet,
    and the thing hitting them is still the answer."""
    fight = snap([dmg(T0, "Wuoshi the Green", "Raider1", 9000),
                  dmg(T0 + 1, "Wuoshi the Green", "Raider2", 9000)])
    assert fight["provisional_name"] == "Wuoshi the Green"
    assert fight["provisional_is_named"] is True


# --- AoE timers -----------------------------------------------------------

def aoe_cast(ts, ability="Ruinous Slam", src="The Corsolander", targets=RAIDERS):
    return [dmg(ts, src, p, 3000, ability=ability) for p in targets]


def test_one_second_touching_the_raid_is_a_cast():
    events = aoe_cast(T0) + aoe_cast(T0 + 45)
    fight = snap(events)
    row = fight["aoes"][0]
    assert (row["source"], row["ability"]) == ("The Corsolander", "Ruinous Slam")
    assert row["casts"] == 2
    assert row["last_cast_ts"] == T0 + 45


def test_a_cleave_on_four_people_is_not_a_raid_aoe():
    fight = snap(aoe_cast(T0, targets=RAIDERS[:4]))
    assert fight["aoes"] == []


def test_ticks_inside_one_cast_do_not_count_as_recasts():
    """Second waves and DoT ticks ride along in the cast they follow —
    `pipeline/aoes.py`'s clustering rule, imported rather than restated."""
    events = aoe_cast(T0) + aoe_cast(T0 + 2) + aoe_cast(T0 + 60)
    fight = snap(events)
    assert fight["aoes"][0]["casts"] == 2


def test_a_sourceless_effect_still_counts():
    """`X is hit by <Effect>` names no caster. The recorded AoE tab pools those
    under Unknown and they include real raid AoEs — bobby.txt's Stench of
    Death hits 17 people on a 30s timer — so dropping them live would hide the
    biggest thing on the screen."""
    events = [ParsedEvent(T0, "damage", src=None, tgt=p, ability="Stench of Death",
                          amount=4000) for p in RAIDERS]
    events += [ParsedEvent(T0 + 30, "damage", src=None, tgt=p,
                           ability="Stench of Death", amount=4000) for p in RAIDERS]
    # a player has to exist for the targets to read as raiders
    events.insert(0, dmg(T0, "Bobby", "a mob", 10, unit="player"))
    row = snap(events)["aoes"][0]
    assert row["source"] == livemeter.UNKNOWN_SOURCE
    assert row["casts"] == 2


def test_a_single_cast_with_no_timer_is_not_shown():
    """It can only say "that happened". A first cast whose timer ACT knows is
    a different matter — that one counts down."""
    assert snap(aoe_cast(T0))["aoes"] == []


def test_observed_period_needs_a_gap_that_repeats():
    """One gap inside one pull is not a timer; two that agree are."""
    once = snap(aoe_cast(T0) + aoe_cast(T0 + 40))["aoes"][0]
    assert once["period_s"] is None and once["next_due_ts"] is None
    twice = snap(aoe_cast(T0) + aoe_cast(T0 + 40) + aoe_cast(T0 + 80))["aoes"][0]
    assert twice["period_src"] == "observed"
    assert twice["period_s"] == pytest.approx(40, abs=1)
    assert twice["next_due_ts"] == T0 + 80 + twice["period_s"]


def test_the_reported_timer_wins_when_act_knows_the_ability():
    """What the raid was told to expect beats one gap measured inside one
    pull — and it is the only countdown available on a boss's first cast."""
    known = next(iter(livemeter.reported_timers().items()), None)
    if known is None:
        pytest.skip("no reported timers shipped")
    ability, meta = known
    fight = snap(aoe_cast(T0, ability=ability))
    row = fight["aoes"][0]
    assert row["period_src"] == "reported"
    assert row["period_s"] == meta["timer_s"]
    assert row["next_due_ts"] == T0 + meta["timer_s"]


def test_a_players_own_aoe_is_never_an_incoming_cast():
    """`YOU`/`YOUR` and the possessive pet forms are the parser's own
    knowledge, and they are what excludes a source here — a raider's green AE
    lands on mobs, so it cannot anchor a cast anyway."""
    events = [dmg(T0, "Bobby", p, 500, ability="Rays of Disintegration",
                  unit="player") for p in RAIDERS]
    assert snap(events, logger="Bobby")["aoes"] == []


def test_a_one_word_boss_still_gets_a_countdown():
    """The reason nothing filters on name grammar: live, "Venekor" reads as a
    raider by name and only behaviour says otherwise."""
    events = aoe_cast(T0, src="Venekor") + aoe_cast(T0 + 30, src="Venekor") \
        + aoe_cast(T0 + 60, src="Venekor")
    row = snap(events)["aoes"][0]
    assert row["source"] == "Venekor"
    assert row["period_s"] == pytest.approx(30, abs=1)


# --- the payload wrapper --------------------------------------------------

def test_between_pulls_the_payload_carries_no_fight():
    """The stream keeps ticking so the dashboard can dim the last fight
    instead of guessing whether it is still connected."""
    assert livemeter.snapshot_payload([], "Bobby", None, None)["fight"] is None
    payload = livemeter.snapshot_payload(
        [dmg(T0, "Bobby", "a mob", 100, unit="player")], "Bobby", "Zone", T0)
    assert payload["fight"]["raid"]["damage"] == 100
    assert payload["computed_ts"] <= int(time.time())
