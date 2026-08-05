"""Behavioral entity refinement: promoting one-word bosses to mobs, and
deciding which bare names this log can prove were people.

Both passes exist because EQ2's grammar is ambiguous in the same place — a
single capitalized token is a player, a boss, or a summoned pet, and only
behavior tells them apart."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser import parse_lines
from pipeline.refine import (refine_bare_pets, refine_known_mobs,
                             roster_prescan)

LOGGER = "Bobby"


def lines(*bodies):
    return [f"(1785890000)[Tue Aug  4 20:33:20 2026] {b}" for b in bodies]


def known(*bodies):
    return refine_known_mobs(list(parse_lines(lines(*bodies), LOGGER)), LOGGER)


# ---- one-word bosses ----
# Every line here is verbatim from Lindsay's 2026-08-04 Emerald Halls log.

KILLED_BY_A_PLAYER = "Laveau has killed Wuoshi."
SELF_HEAL = "Wuoshi's Nature's Salve heals Wuoshi for 30,740 hit points."


def test_a_one_word_boss_killed_by_a_player_is_a_mob():
    assert "Wuoshi" in known(KILLED_BY_A_PLAYER)


def test_a_bosss_self_heal_does_not_make_it_a_player():
    """Two self-heal lines out of 248k events kept the Emerald Halls boss in
    the raider table at #17 damage and handed every fight's title to the adds
    ("Ancient Grovebeast"). Mobs heal; a heal is only evidence about a name
    when it crossed BETWEEN that name and a known player."""
    assert "Wuoshi" in known(SELF_HEAL, KILLED_BY_A_PLAYER)


def test_a_raider_healed_by_a_real_healer_is_still_a_player():
    """The guard must not swing the other way. A mind-controlled raider dies to
    a player kill line exactly like a boss does; being topped up by a healer
    the log has already confirmed is what still tells them apart."""
    assert "Tragedy" not in known(
        "Ellea's Ritual Healing heals YOU for a critical of 2,271 hit points.",
        "Ellea's Ritual Healing heals Tragedy for 2,271 hit points.",
        "Laveau has killed Tragedy.",
    )


# ---- who this log proves is a person ----

def test_roster_takes_chat_raid_and_loot_lines():
    roster = roster_prescan(lines(
        "Guildmate: Emericant has logged in.",
        "Spades has joined the raid.",
        "Tragedy loots \\aITEM -1 -1:a Nayad eye\\/a.",
        "Ellea has been resurrected.",
        "\\aPC -1 Squigs:Squigs\\/a says to the guild, \"yo\"",
    ), LOGGER)
    assert {"Emericant", "Spades", "Tragedy", "Ellea", "Squigs", LOGGER} <= roster


def test_receiving_a_debuff_is_not_proof_of_personhood():
    """`Shotar receives a transcendent injury!` reads exactly like
    `Bobby receives a Dark Heart.`, and the loot pattern used to take both —
    which promoted four summoned pets to proven raiders and put them in the
    raid table. Loot carries an item link; a debuff does not."""
    roster = roster_prescan(lines(
        "Shotar receives a transcendent injury!",
        "Bobby loots \\aITEM -1 -1:basilisk spine\\/a.",
    ), LOGGER)
    assert "Shotar" not in roster


def test_a_bare_named_pet_leaves_no_player_evidence():
    """`Kober` fought a whole pull with Berserker abilities and was never once
    named in a line only a person can produce — no chat, no roster, no loot,
    no rez. EQ2 writes no owner possessive for it either, which is why the
    named-pet knowledge base cannot reach it."""
    roster = roster_prescan(lines(
        "Spades has joined the raid.",
        "Kober hits an ethereal veilrunner for 511 mental damage.",
        "Kober's Rupture hits an ethereal veilrunner for 2,396 mental damage.",
        "Kober's group is enveloped by a protective umbral ward!",
        "Ellea's Runic Armor absorbs 291 points of damage from being done to Kober.",
    ), LOGGER)
    assert "Kober" not in roster
    assert "Spades" in roster


# ---- a boss that holds the raid's pets ----
# Verbatim from Lindsay's 2026-08-03 Mistmoore's Inner Sanctum log.

HOLDS_A_PET = "Enynti's protoflame tries to burn Faildozer, but Faildozer blocks."
KILLED = "Laveau has killed Enynti."


def test_owning_a_swarm_pet_does_not_make_a_boss_a_player():
    """One possessive did it: the encounter holds the raid's dumbfires, so the
    log prints `Enynti's protoflame` for a MOB, that promoted Enynti to a
    confirmed player, and being player-like vetoed its own kill-victim
    reclassing. It sat in the raider table with 872k damage while 24 people
    attacked it."""
    assert "Enynti" in known(HOLDS_A_PET, KILLED)


def test_a_real_pet_owner_the_raid_never_killed_is_still_a_player():
    """The guard must not swing the other way — a conjuror's protoflame is the
    ordinary reading of that grammar, and nothing here says the raid fought
    Stymie."""
    assert "Stymie" not in known(
        "Stymie's protoflame hits an ethereal veilrunner for 511 heat damage.",
        "Laveau has killed Wuoshi.",
    )


def test_the_roster_outranks_every_inference():
    """A raider under mind control produces a player-credited kill line on
    their own name. `roster_prescan` is the one signal a mob cannot
    manufacture, so it is the one that decides."""
    events = list(parse_lines(lines("Laveau has killed Tragedy."), LOGGER))
    assert "Tragedy" in refine_known_mobs(events, LOGGER)
    assert "Tragedy" not in refine_known_mobs(events, LOGGER, frozenset({"Tragedy"}))


# ---- bare-named summoned pets ----

def bare_pets(*bodies, roster=frozenset(), kit=frozenset(), mobs=frozenset(),
              missing=frozenset()):
    events = list(parse_lines(lines(*bodies), LOGGER))
    return refine_bare_pets(events, LOGGER, roster, kit, mobs, missing)


def test_a_bare_name_casting_a_pet_kit_is_a_pet():
    """`Viber` and `Knyi` fought whole raids in the raider table with no class,
    because EQ2 writes a dumbfire with no owner possessive anywhere in the file.
    Their KIT gives them away: `ability_catalog` already knows Grisly Feedback
    and Confusion are `unit='pet'`, taught by real pets under real owners."""
    got = bare_pets(
        "Viber's Grisly Feedback hits a cucuy for 47 poison damage.",
        "Viber's Grim Wave hits a cucuy for 512 poison damage.",
        kit=frozenset({"Grisly Feedback", "Grim Wave"}))
    assert "Viber" in got


def test_one_pet_ability_is_not_enough():
    got = bare_pets("Viber's Grisly Feedback hits a cucuy for 47 poison damage.",
                    kit=frozenset({"Grisly Feedback", "Grim Wave"}))
    assert "Viber" not in got


def test_a_proven_person_is_never_demoted_by_their_abilities():
    got = bare_pets(
        "Bobby's Grisly Feedback hits a cucuy for 47 poison damage.",
        "Bobby's Grim Wave hits a cucuy for 512 poison damage.",
        roster=frozenset({"Bobby"}), kit=frozenset({"Grisly Feedback", "Grim Wave"}))
    assert "Bobby" not in got


def test_a_mob_is_not_reclassified_as_somebodys_pet():
    """Mobs cast pet kits too — Enynti cast Grave Decay. `known_mobs` is the
    stronger finding and wins."""
    got = bare_pets(
        "Enynti's Grave Decay hits a cucuy for 1,102 disease damage.",
        "Enynti's Grim Wave hits a cucuy for 512 poison damage.",
        kit=frozenset({"Grave Decay", "Grim Wave"}), mobs=frozenset({"Enynti"}))
    assert "Enynti" not in got


def test_a_name_that_only_ever_hits_players_is_not_a_pet():
    """The Census-missing signal alone swept up every mob the known-mob pass had
    not promoted. A hireling swings at articled mobs; a boss swings at the
    raid."""
    got = bare_pets("Bristlecone's Thorncoat hits Bobby for 2,100 piercing damage.",
                    missing=frozenset({"Bristlecone"}))
    assert "Bristlecone" not in got


def test_census_never_heard_of_it_and_neither_did_the_log():
    """`Holmes` only ever melees, so no kit gives it away — but Census has no
    such character and the file never proves one either. Two independent
    negatives are enough to keep it out of the raid table."""
    body = "Holmes hits a young dragon for 1,468 piercing damage."
    assert "Holmes" not in bare_pets(body)
    assert "Holmes" in bare_pets(body, missing=frozenset({"Holmes"}))
    assert "Holmes" not in bare_pets(body, roster=frozenset({"Holmes"}),
                                     missing=frozenset({"Holmes"}))
