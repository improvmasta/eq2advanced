"""Parser unit tests — every fixture line is verbatim from bobby.txt (the
golden raid night) unless noted. If one of these breaks, the parser no longer
matches the real log grammar."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.classify import classify_body
from parser.events import F_AUTOATTACK, F_CRIT, F_MULTI, F_SELF_FOCUS, F_ZERO
from parser.prefix import split_prefix, to_int
from parser.subjects import decompose

LOGGER = "Bobby"


def ev(body):
    return classify_body(0, body, LOGGER)


# ---- prefix ----

def test_prefix_crlf_and_epoch():
    line = "(1785630651)[Sat Aug  1 20:30:51 2026] You prepare the Teachings of the Underworld.\r\n"
    ts, body = split_prefix(line)
    assert ts == 1785630651
    assert body == "You prepare the Teachings of the Underworld."


def test_amounts():
    assert to_int("1,485") == 1485
    assert to_int("296.1K") == 296100
    assert to_int("42") == 42


# ---- subject model ----

def test_you_is_player_autoattack():
    e = ev("YOU hit Malkonis D'Morte for a critical of 1,873 crushing damage.")
    assert e.src.name == "Bobby" and e.src.unit == "player"
    assert e.ability is None and e.flags & F_AUTOATTACK and e.flags & F_CRIT
    assert e.amount == 1873 and e.dtype == "crushing"


def test_your_ability_keeps_internal_possessive():
    e = ev("YOUR Lich's Siphoning heals YOU for 264 hit points.")
    assert e.src.unit == "player" and e.ability == "Lich's Siphoning"
    assert e.tgt == "YOU"


def test_bare_logger_name_is_the_pet():
    e = ev("Bobby hits Zylphax the Shredder for a critical of 1,283 piercing damage.")
    assert e.src.name == "Bobby" and e.src.unit == "own_pet"
    assert e.flags & F_AUTOATTACK


def test_logger_possessive_is_pet_ability():
    e = ev("Bobby's Throat Gash hits Treyloth D'Kulvith for a critical of 2,989 poison damage.")
    assert e.src.unit == "own_pet" and e.ability == "Throat Gash"


def test_apostrophe_only_possessive_is_ability():
    e = ev("Aros' Soulrot hits Zylphax the Shredder for 871 disease damage.")
    assert e.src.name == "Aros" and e.ability == "Soulrot"


def test_apostrophe_s_lowercase_is_swarm_pet():
    e = ev("Aros's blighted horde hits a fragment of Garanel for 612 crushing damage.")
    assert e.src.name == "Aros" and e.src.unit == "swarm_pet" and e.src.pet == "blighted horde"
    assert e.ability is None


def test_three_level_swarm_chain():
    e = ev("Bobby's blighted horde's Grave Decay hits Treyloth D'Kulvith for 1,206 cold damage.")
    assert e.src.name == "Bobby" and e.src.unit == "swarm_pet"
    assert e.src.pet == "blighted horde" and e.ability == "Grave Decay"


def test_ability_with_internal_possessive_not_split():
    subj, ability = decompose("Ramms' Autumn's Kiss", LOGGER)
    assert subj.name == "Ramms" and ability == "Autumn's Kiss"


def test_mob_possessive_lowercase_ability():
    e = ev("A shard of Garanel's grave sacrament hits YOU for 42 slashing damage.")
    assert e.src.name == "A shard of Garanel" and e.ability == "grave sacrament"


def test_mob_internal_apostrophe_name():
    e = ev("Treyloth D'Kulvith's Bloodcoil hits YOU for 12 disease damage.")
    assert e.src.name == "Treyloth D'Kulvith" and e.ability == "Bloodcoil"


# ---- damage variants ----

def test_zero_damage_hit():
    e = ev("a fragment of Garanel hits YOU but fails to inflict any damage.")
    assert e.type == "damage" and e.amount == 0 and e.flags & F_ZERO


def test_focus_damage_flagged_self():
    e = ev("YOUR Vampiric Requiem hits YOURSELF for 1,602 focus damage.")
    assert e.flags & F_SELF_FOCUS and e.tgt == "YOURSELF"


def test_multi_attack():
    e = ev("Beaux multi attacks Malkonis D'Morte for a critical of 586 disease damage.")
    assert e.flags & F_MULTI and e.ability is None


def test_aoe_attack():
    e = ev("Beaux aoe attacks a bloodgorger for a critical of 660 disease damage.")
    assert e.flags & F_MULTI and e.amount == 660


def test_flurry():
    e = ev("Moklok flurries Zylphax the Shredder for a critical of 11,198 slashing damage.")
    assert e.flags & F_MULTI and e.amount == 11198


def test_dual_type_hit_sums_components():
    e = ev("Malkonis D'Morte hits Sorengail for 7,896 crushing and 0 disease damage.")
    assert e.amount == 7896
    assert e.extra["components"] == [[7896, "crushing"], [0, "disease"]]


def test_big_hit_k_notation():
    e = ev("Spades' Assassinate hits Treyloth D'Kulvith for a critical of 296.1K disease damage.")
    assert e.amount == 296100


def test_passive_sourceless_hit():
    e = ev("Xalithra is hit for 0 crushing damage.")
    assert e.type == "damage" and e.src is None and e.tgt == "Xalithra"


# ---- avoidance ----

def test_miss_second_person():
    e = ev("YOU try to crush Othysis Muravian, but miss.")
    assert e.type == "avoid" and e.extra["how"] == "miss" and e.flags & F_AUTOATTACK


def test_blocker_differs_from_target():
    e = ev("Treyloth D'Kulvith tries to crush Sorengail, but Birch blocks.")
    assert e.tgt == "Sorengail" and e.extra["avoider"] == "Birch" and e.extra["how"] == "block"


def test_resist_with_ability():
    e = ev("YOU try to disease Malkonis D'Morte with Mortality Mark, but Malkonis D'Morte resists.")
    assert e.ability == "Mortality Mark" and e.extra["how"] == "resist"
    assert not e.flags & F_AUTOATTACK


# ---- heals / wards / power / threat ----

def test_heal_to_bare_logger_name_targets_pet():
    e = ev("YOUR Consume heals Bobby for a critical of 332 hit points.")
    assert e.type == "heal" and e.tgt == "Bobby" and e.flags & F_CRIT


def test_three_level_heal_source():
    e = ev("Ellea's Lunar Attendant's Oracle's Blessing heals YOU for 550 hit points.")
    assert e.src.name == "Ellea"


def test_ward_with_bleedthrough():
    e = ev("Zooey's Runic Armor absorbs 294 points of damage from being done to YOU "
           "with 7 points of damage bleeding through. (0 points remaining)")
    assert e.type == "ward" and e.amount == 294
    assert e.extra["bleed"] == 7 and e.extra["remaining"] == 0


def test_power_drain():
    e = ev("Continuum's Manatap confounds Zylphax the Shredder draining 301 points of power.")
    assert e.type == "power_drain" and e.amount == 301


def test_threat_reduce_negative():
    e = ev("YOUR Dynamism reduces YOUR hate with Othysis Muravian for 1,805 threat.")
    assert e.type == "threat" and e.amount == -1805


# ---- deaths / kills ----

def test_kill_line():
    e = ev("Shaly has killed Zylphax the Shredder.")
    assert e.type == "kill" and e.src.name == "Shaly" and e.tgt == "Zylphax the Shredder"


def test_killed_you_is_logger_death():
    e = ev("a shard of Garanel has killed you.")
    assert e.type == "death" and e.tgt == "YOU"


def test_pet_death():
    e = ev("Alas, Bobby's blighted horde has died from pain and suffering.")
    assert e.type == "pet_death" and e.src.name == "Bobby" and e.tgt == "blighted horde"


def test_player_death():
    e = ev("Alas, Beaux has died from pain and suffering.")
    assert e.type == "death" and e.tgt == "Beaux"


# ---- misc ----

def test_chat_filtered():
    assert ev('\\aPC -1 Ellea:Ellea\\/a says to the raid party, "sounds good"') is None
    assert ev('You say to the group, "hi"') is None


def test_item_markup_unescaped_in_chat_free_line():
    # loot lines are currently unclassified, but must not crash on ITEM markup
    assert ev("You loot \\aITEM -909490762 1847732481:impure chunk of arcane stone\\/a "
              "from the corpse of a mangled mass of corpses.") is None


def test_zone_double_space():
    e = ev("You have entered  The Estate of Unrest.")
    assert e.type == "zone" and e.extra["zone"] == "The Estate of Unrest"


def test_prepare_is_flavor():
    e = ev("You prepare to rot a soul.")
    assert e.type == "cast_flavor" and e.extra["flavor"] == "to rot a soul"
    assert e.ability == "Soulrot"      # necromancer prose map


def test_prepare_flavor_resolution():
    # article form resolves generically; lowercase continuation is prose
    assert ev("You prepare the Bloodcloud.").ability == "Bloodcloud"
    assert ev("You prepare an Unholy Covenant.").ability == "Unholy Covenant"
    assert ev("You prepare to awaken the grave.").ability == "Awaken Grave"
    # targeted inflict form
    e = ev("You prepare to inflict Fear on Zylphax the Shredder.")
    assert e.ability == "Fear"
    # unknown prose stays unresolved but keeps the flavor text
    e = ev("You prepare to do something inscrutable.")
    assert e.ability is None and e.extra["flavor"] == "to do something inscrutable"


def test_prepare_same_second_duplicate_collapses():
    from parser import parse_lines
    lines = [
        "(1785630651)[Sat Aug  1 20:30:51 2026] You prepare the Bloodcloud.\r\n",
        "(1785630651)[Sat Aug  1 20:30:51 2026] Something else happens here.\r\n",
        "(1785630651)[Sat Aug  1 20:30:51 2026] You prepare the Bloodcloud.\r\n",
        "(1785630652)[Sat Aug  1 20:30:52 2026] You prepare the Bloodcloud.\r\n",
    ]
    evs = [e for e in parse_lines(lines, "Bobby") if e.type == "cast_flavor"]
    # same-second duplicate collapses; the next-second recast does not
    assert len(evs) == 2 and [e.ts for e in evs] == [1785630651, 1785630652]


def test_anonymous_heal_ignored_kind():
    e = ev("A healing spell is cast on Bobby.")
    assert e.type == "anon_heal"


def test_dispel():
    e = ev("a shrouded horror's Soul Strip dispels Transcendence from YOU.")
    assert e.type == "dispel" and e.ability == "Soul Strip" and e.extra["effect"] == "Transcendence"
