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
from pipeline.encounters import GAP_S

T0 = 1754500000
RAIDERS = [f"Raider{i}" for i in range(1, 9)]


def dmg(ts, src, tgt, amount=1000, ability="Fireball", unit="unknown", flags=0):
    return ParsedEvent(ts, "damage", src=Subject(src, unit), tgt=tgt,
                       ability=ability, amount=amount, flags=flags)


def heal(ts, src, tgt, amount=500, unit="unknown"):
    return ParsedEvent(ts, "heal", src=Subject(src, unit), tgt=tgt,
                       ability="Healing Grove", amount=amount)


def snap(events, logger="Bobby", zone="The Estate of Unrest", start_ts=T0,
         roster=None, know=livemeter.NO_KNOWLEDGE, now_ts=None):
    return livemeter.build_snapshot(events, logger, zone, start_ts, roster, know,
                                    now_ts)


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


def test_max_hit_is_the_biggest_single_line_either_way():
    """The one number a rate cannot say: 3M in one nuke and 3M of ticks are the
    same DPS. It is per SOURCE, so a pet's crit lands on its owner's row the
    way its damage does, and a self-hit is not a hit."""
    fight = snap([dmg(T0, "Bobby", "a training dummy", 1000, unit="player"),
                  dmg(T0 + 1, "Bobby", "a training dummy", 4200, unit="player"),
                  dmg(T0 + 2, "Bobby", "a training dummy", 900, unit="player"),
                  dmg(T0 + 3, "Bobby", "Bobby", 9999, unit="player",
                      flags=F_SELF_FOCUS),
                  heal(T0 + 4, "Mendya", "Raider1", 700),
                  heal(T0 + 5, "Mendya", "Raider1", 2500)])
    assert actor(fight, "Bobby")["max_hit"] == 4200
    assert actor(fight, "Mendya")["max_heal"] == 2500
    assert actor(fight, "Mendya")["max_hit"] == 0


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


# --- how long the fight is, and when it is over ----------------------------

def test_elapsed_ends_at_the_last_damage_not_the_last_event():
    """The fight card this becomes measures damage to damage
    (`encounters.Segment.end_ts` only advances on a damage line), so the meter
    has to as well. Heals and deaths inside the idle window are part of the
    pull and are counted — they just do not make it longer, or the live DPS
    would read low against the parse it turns into."""
    fight = snap([dmg(T0, "Bobby", "a training dummy", 1000, unit="player"),
                  dmg(T0 + 9, "Bobby", "a training dummy", 1000, unit="player"),
                  heal(T0 + 13, "Mendya", "Bobby", 500),
                  ParsedEvent(T0 + 15, "death", tgt="Raider1")])
    assert fight["elapsed_s"] == 9
    assert fight["last_combat_ts"] == T0 + 9
    assert fight["last_ts"] == T0 + 15          # the timeline still runs to here
    assert actor(fight, "Mendya")["heals"] == 500
    assert fight["raid"]["deaths"] == 1
    assert actor(fight, "Bobby")["dps"] == pytest.approx(2000 / 9, abs=0.1)


def test_a_pull_whose_damage_stopped_is_over():
    """ACT calls a fight at its idle timeout; the writer cannot close the
    segment for another ten seconds in case a late kill line joins it. `ended`
    is the difference — the screen stops counting where ACT stops."""
    events = [dmg(T0, "Bobby", "a training dummy", 1000, unit="player"),
              dmg(T0 + 9, "Bobby", "a training dummy", 1000, unit="player")]
    assert snap(events, now_ts=T0 + 9)["ended"] is False
    assert snap(events, now_ts=T0 + 9 + GAP_S - 1)["ended"] is False
    over = snap(events, now_ts=T0 + 9 + GAP_S)
    assert over["ended"] is True
    assert over["quiet_s"] == GAP_S
    assert over["elapsed_s"] == 9              # and it stays where it stopped


def test_without_a_log_clock_nothing_has_ended():
    """No `now_ts` means the caller has nothing to judge quiet by — the old
    behaviour, and the only honest answer."""
    fight = snap([dmg(T0, "Bobby", "a training dummy", 1000, unit="player")])
    assert fight["ended"] is False
    assert fight["quiet_s"] == 0


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


# --- who these names are (livemeter.Names) --------------------------------

def test_a_one_word_boss_is_the_enemy_and_not_a_raider():
    """`Wuoshi` is grammatically a raider, so without help the boss sits in the
    bar list, inflates the raider count, and — not being an enemy — cannot name
    the fight, which then gets titled after whichever add IS multi-word. The
    seed is what an earlier parse's behavioural pass already worked out."""
    events = [dmg(T0, "Bobby", "Wuoshi", 9000, unit="player"),
              dmg(T0 + 1, "Bobby", "an ancient grovebeast", 500, unit="player")]
    know = livemeter.Knowledge(mobs=frozenset({"Wuoshi"}))
    fight = snap(events, know=know)
    assert fight["provisional_name"] == "Wuoshi"
    assert fight["provisional_is_named"] is True
    assert actor(fight, "Wuoshi")["kind"] == "mob"
    assert fight["raid"]["raiders"] == 1


def test_behaviour_finds_a_one_word_boss_the_seed_has_never_seen():
    """Nothing seeded, but the thing trading damage with five raiders is not
    one of them — `refine_known_mobs`, the recorded path's own rule, run over
    the open segment."""
    events = [dmg(T0 + i, "Venekor", f"Raider{i}", 900) for i in range(1, 6)]
    events += [dmg(T0 + 6 + i, f"Raider{i}", "Venekor", 900, unit="player")
               for i in range(1, 6)]
    fight = snap(events)
    assert actor(fight, "Venekor")["kind"] == "mob"
    assert fight["provisional_name"] == "Venekor"


def test_a_raider_the_boss_killed_is_still_a_raider():
    """One segment of a wipe is the boss killing people and the raid landing
    nothing, and refine's kill-victim rule reads that as eight mobs. A name
    that has been a raider before is a raider — otherwise the meter empties out
    at exactly the moment the raid wants to look at it."""
    events = [dmg(T0, "Wuoshi", "Spades", 90000),
              ParsedEvent(T0 + 1, "kill", src=Subject("Wuoshi", "unknown"),
                          tgt="Spades")]
    know = livemeter.Knowledge(mobs=frozenset({"Wuoshi"}),
                               players=frozenset({"Spades"}))
    fight = snap(events, know=know)
    assert actor(fight, "Spades")["kind"] == "player"
    assert actor(fight, "Spades")["deaths"] == 1
    assert fight["raid"]["raiders"] == 1


def test_a_possessive_pet_target_is_neither_a_raider_nor_an_enemy():
    """`Tragedy's unswerving hammer` is multi-word, so damage into it used to
    read as damage into an enemy — enough, early in a pull, to name the fight
    after somebody's dumbfire."""
    events = [dmg(T0, "Wuoshi", "Tragedy's unswerving hammer", 40000),
              dmg(T0 + 1, "Bobby", "Wuoshi", 900, unit="player")]
    fight = snap(events, know=livemeter.Knowledge(mobs=frozenset({"Wuoshi"})))
    assert actor(fight, "Tragedy's unswerving hammer")["kind"] == "pet"
    assert fight["provisional_name"] == "Wuoshi"
    assert fight["raid"]["raiders"] == 1


def test_a_bare_named_dumbfire_is_not_a_raider():
    """EQ2 writes a dumbfire exactly like a raider and never prints an owner
    for it, so only what an earlier parse concluded can say what `Knyi` is."""
    events = [dmg(T0, "Bobby", "a mob", 100, unit="player"),
              dmg(T0 + 1, "Knyi", "a mob", 900)]
    fight = snap(events, know=livemeter.Knowledge(pets=frozenset({"Knyi"})))
    assert actor(fight, "Knyi")["kind"] == "pet"
    assert fight["raid"]["raiders"] == 1
    assert fight["raid"]["damage"] == 100


def test_the_logger_is_one_raider_under_both_spellings():
    """Their own lines say YOU; everyone else's say their name."""
    events = [dmg(T0, "a mob", "YOU", 300),
              dmg(T0 + 1, "a mob", "Bobby", 200),
              heal(T0 + 2, "Mendya", "YOURSELF", 100)]
    fight = snap(events)
    assert actor(fight, "Bobby")["damage_taken"] == 500
    assert fight["raid"]["raiders"] == 2      # Bobby and Mendya


def test_the_unknown_pool_is_not_a_raider():
    """Sourceless `X is hit by <Effect>` pools under Unknown, which is one
    capitalized token and so read as a person called Unknown."""
    fight = snap([ParsedEvent(T0, "damage", src=None, tgt="Unknown",
                              ability="Stench of Death", amount=500),
                  dmg(T0 + 1, "Bobby", "a mob", 100, unit="player")])
    assert actor(fight, "Unknown")["kind"] == "other"
    assert fight["raid"]["raiders"] == 1


def test_cures_are_counted_per_healer():
    """One per line, credited to the caster whatever the target was — ACT's
    Cures column, the way `roll_encounter` counts it."""
    fight = snap([ParsedEvent(T0, "dispel", src=Subject("Mendya", "player"),
                              tgt="Raider1", ability="Cure Curse"),
                  ParsedEvent(T0 + 1, "dispel", src=Subject("Mendya", "player"),
                              tgt="a mob", ability="Cure Curse")])
    assert actor(fight, "Mendya")["cures"] == 2


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


def test_the_logger_counts_toward_the_anchor_under_their_own_spelling():
    """A raid-wide AoE reaches the person whose log this is as `YOU`. Counting
    that as a different name misses the ≥5 anchor by exactly one — on the one
    log that can see the cast at all."""
    four = RAIDERS[:4]
    events = (aoe_cast(T0, targets=four + ["YOU"])
              + aoe_cast(T0 + 45, targets=four + ["YOU"]))
    fight = snap(events)
    assert [a["ability"] for a in fight["aoes"]] == ["Ruinous Slam"]
    assert fight["aoes"][0]["last_targets"] == 5


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


def test_the_last_casts_outcome_says_who_ate_it_and_who_was_covered():
    """The dashboard's mid-fight question is "is the raid handling this AoE",
    so each row carries the freshest cast's split: hit vs blocked (avoided or
    absorbed), the same three outcomes aoes.detect reports afterwards."""
    from parser.events import F_ZERO
    first = aoe_cast(T0)
    second = [dmg(T0 + 45, "The Corsolander", p, 3000, ability="Ruinous Slam")
              for p in RAIDERS[:5]]
    second.append(ParsedEvent(T0 + 45, "avoid", src=Subject("The Corsolander", "unknown"),
                              tgt=RAIDERS[5], ability="Ruinous Slam"))
    second.append(dmg(T0 + 45, "The Corsolander", RAIDERS[6], 0,
                      ability="Ruinous Slam", flags=F_ZERO))
    row = snap(first + second)["aoes"][0]
    assert row["casts"] == 2
    assert row["last_hit"] == 5
    assert row["last_blocked"] == 2        # one avoided + one absorbed
    assert row["last_targets"] == 7


def test_a_player_who_ate_the_cast_is_not_also_covered_by_it():
    """A resist line and a damage line for the same player in one cast is one
    person who got hit, not one hit and one block."""
    events = aoe_cast(T0) + aoe_cast(T0 + 45)
    events.append(ParsedEvent(T0 + 45, "avoid", src=Subject("The Corsolander", "unknown"),
                              tgt=RAIDERS[0], ability="Ruinous Slam"))
    row = snap(events)["aoes"][0]
    assert row["last_hit"] == len(RAIDERS)
    assert row["last_blocked"] == 0


RAID = [f"Raider{i}" for i in range(1, 25)]


def full_raid(ts):
    """Enough raiders in the actor list for a fraction of them to mean
    something — `RAIDERS` is eight, and five of eight is most of the raid."""
    return [dmg(ts, p, "a mob", 100, unit="player") for p in RAID]


REPORTED = "War Stomp"        # in the shipped ACT list at 45s


def test_a_reported_ability_counts_down_however_few_it_reached():
    """The panel's other half. `RAID_FRACTION` decides which abilities EARN a
    row; this decides what a CAST is once one has, and reach stops deciding.
    Mayong's Soul Paralysis reached one group on a 16-minute kill, so a panel
    that re-armed only on the raid-wide landings counted 37s from the third of
    eleven casts and read overdue for most of the fight."""
    events = full_raid(T0) + [
        dmg(T0 + 10, "The Corsolander", RAID[0], 4000, ability=REPORTED),
        dmg(T0 + 55, "The Corsolander", RAID[0], 4000, ability=REPORTED),
    ]
    row = snap(events, now_ts=T0 + 60)["aoes"][0]
    assert row["ability"] == REPORTED
    assert row["casts"] == 2
    assert row["last_cast_ts"] == T0 + 55
    assert row["next_due_ts"] == T0 + 55 + 45


def test_a_countdown_that_has_been_wrong_for_a_minute_leaves():
    """Overdue is information right up until it is not telling anybody when
    anything is due — and the panel is a shortlist (OVERDUE_DROP_S)."""
    events = full_raid(T0) + [dmg(T0 + 10, "The Corsolander", p, 4000,
                                  ability=REPORTED) for p in RAID]
    due = T0 + 10 + 45
    assert snap(events, now_ts=due + livemeter.OVERDUE_DROP_S)["aoes"]
    assert snap(events, now_ts=due + livemeter.OVERDUE_DROP_S + 1)["aoes"] == []


def test_a_row_with_no_timer_leaves_on_the_same_line():
    """The other half of the drop rule, and the half that was missing: a row
    with no period has nothing to be LATE for, so nothing expired it and it
    held a slot until the pull ended. An avatar throws several raid-wide
    abilities that do not repeat on a clock (`Stealth Assault`), and on a panel
    the meter is drawn under, a row that can only say "2x" forever is a raider
    off the bottom of the scene. Measured from the last cast, same 60s."""
    cast = aoe_cast(T0) + aoe_cast(T0 + 40)      # two casts, no agreeing gap
    row = snap(cast, now_ts=T0 + 40)["aoes"][0]
    assert row["period_s"] is None and row["next_due_ts"] is None
    assert snap(cast, now_ts=T0 + 40 + livemeter.OVERDUE_DROP_S)["aoes"]
    assert snap(cast, now_ts=T0 + 40 + livemeter.OVERDUE_DROP_S + 1)["aoes"] == []


def test_and_comes_back_the_moment_it_lands_again():
    """Nothing is remembered between snapshots — each one is rebuilt from the
    fight's events — so a dropped row needs no un-dropping."""
    late = T0 + 400
    events = full_raid(T0) + [dmg(T0 + 10, "The Corsolander", p, 4000,
                                  ability=REPORTED) for p in RAID]
    events += [dmg(late, "The Corsolander", p, 4000, ability=REPORTED)
               for p in RAID]
    row = snap(events, now_ts=late + 1)["aoes"][0]
    assert row["last_cast_ts"] == late


def test_the_row_says_what_it_lands_as():
    events = full_raid(T0) + [
        ParsedEvent(T0 + 10, "damage", src=Subject("The Corsolander", "unknown"),
                    tgt=p, ability=REPORTED, amount=4000, dtype="cold")
        for p in RAID]
    assert snap(events)["aoes"][0]["dtype"] == "cold"


def test_a_group_sized_hit_with_no_timer_is_not_a_spell_timer():
    """The audit's threshold is not the panel's. Five people in one second is
    an EQ2 GROUP, and on a real Mayong kill that let seven add cleaves and
    one-off boss spells onto a screen showing three abilities worth calling
    out. With no reported timer, a row has to have reached the RAID."""
    events = (full_raid(T0)
              + aoe_cast(T0 + 10, ability="Rampaging Blow", targets=RAID[:6])
              + aoe_cast(T0 + 70, ability="Rampaging Blow", targets=RAID[:6]))
    assert snap(events)["aoes"] == []


def test_a_raid_wide_hit_earns_its_row_without_any_timer():
    """The other half of the same rule, and why it is a fraction rather than a
    ban on timerless rows: bobby.txt's sourceless `Overnuke` reaches the whole
    raid and no timer list has ever heard of it."""
    events = (full_raid(T0)
              + aoe_cast(T0 + 10, ability="Overnuke", targets=RAID)
              + aoe_cast(T0 + 70, ability="Overnuke", targets=RAID))
    assert [a["ability"] for a in snap(events)["aoes"]] == ["Overnuke"]


def test_a_reported_timer_keeps_its_row_however_few_it_reached():
    """`Soul Paralysis` lands on one group in a long fight and on seventeen
    people in a short one. ACT's list saying the raid was told to expect it is
    the evidence, not tonight's reach."""
    known = next((a for a, m in livemeter.reported_timers().items()
                  if m.get("timer_s")), None)
    if known is None:
        pytest.skip("no reported timers shipped")
    events = full_raid(T0) + aoe_cast(T0 + 10, ability=known, targets=RAID[:5])
    assert [a["ability"] for a in snap(events)["aoes"]] == [known]


# --- the payload wrapper --------------------------------------------------

def test_between_pulls_the_payload_carries_no_fight():
    """The stream keeps ticking so the dashboard can dim the last fight
    instead of guessing whether it is still connected."""
    assert livemeter.snapshot_payload([], "Bobby", None, None)["fight"] is None
    payload = livemeter.snapshot_payload(
        [dmg(T0, "Bobby", "a mob", 100, unit="player")], "Bobby", "Zone", T0)
    assert payload["fight"]["raid"]["damage"] == 100
    assert payload["computed_ts"] <= int(time.time())
