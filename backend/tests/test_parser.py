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
    assert to_int("1.5") == 1      # malformed decimal must not abort a parse


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
    from parser.events import F_AOE
    e = ev("Beaux aoe attacks a bloodgorger for a critical of 660 disease damage.")
    assert e.flags & F_AOE and not e.flags & F_MULTI and e.amount == 660


def test_flurry():
    from parser.events import F_FLURRY
    e = ev("Moklok flurries Zylphax the Shredder for a critical of 11,198 slashing damage.")
    assert e.flags & F_FLURRY and not e.flags & F_MULTI and e.amount == 11198


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
    # without pet knowledge the owner still gets credit (composite ability)
    e = ev("Ellea's Lunar Attendant's Oracle's Blessing heals YOU for 550 hit points.")
    assert e.src.name == "Ellea"


# ---- named-pet knowledge base ----

PETS = frozenset({"Lunar Attendant"})


def pev(body):
    return classify_body(0, body, LOGGER, PETS)


def test_named_pet_chain_decomposes():
    e = pev("Ellea's Lunar Attendant's Oracle's Blessing heals YOU for 550 hit points.")
    assert e.src.name == "Ellea" and e.src.unit == "named_pet"
    assert e.src.pet == "Lunar Attendant" and e.ability == "Oracle's Blessing"


def test_named_pet_bare_is_autoattack():
    e = pev("Ellea's Lunar Attendant hits Zylphax the Shredder for 500 disease damage.")
    assert e.src.unit == "named_pet" and e.src.pet == "Lunar Attendant"
    assert e.ability is None and e.flags & F_AUTOATTACK


def test_named_pet_avoid_keeps_pet_subject():
    e = pev("Ellea's Lunar Attendant tries to slash Zylphax the Shredder, but Zylphax the Shredder parries.")
    assert e.type == "avoid" and e.src.unit == "named_pet" and e.extra["how"] == "parry"


def test_unknown_capitalized_remainder_still_ability():
    e = pev("Banjeaux's Daro's Dull Blade hits Zylphax the Shredder for 100 mental damage.")
    assert e.src.name == "Banjeaux" and e.src.unit == "unknown"
    assert e.ability == "Daro's Dull Blade"


def test_s_ending_owner_possessive_still_ability():
    e = pev("Aros' Soulrot hits Zylphax the Shredder for 871 disease damage.")
    assert e.src.name == "Aros" and e.ability == "Soulrot"


def test_logger_named_pet():
    e = classify_body(0, "Bobby's Kibibi hits Zylphax the Shredder for 90 piercing damage.",
                      LOGGER, frozenset({"Kibibi"}))
    assert e.src.name == "Bobby" and e.src.unit == "named_pet" and e.src.pet == "Kibibi"


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


def test_zone_lowercase_is_not_a_zone():
    # these must not hard-cut an in-progress encounter
    assert ev("You have entered a house.") is None
    assert ev("You have entered an area where you may not summon a mount.") is None


def test_avoid_parry_kind():
    e = ev("Treyloth D'Kulvith tries to crush Sorengail, but Sorengail parries.")
    assert e.extra["how"] == "parry"    # regression: rstrip bug produced "parrie"


def test_avoid_riposte_and_reflect_kinds():
    e = ev("Treyloth D'Kulvith tries to crush Sorengail, but Sorengail ripostes.")
    assert e.extra["how"] == "riposte"
    e = ev("Treyloth D'Kulvith tries to crush Sorengail, but Sorengail reflects.")
    assert e.extra["how"] == "reflect"


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


# ---- rezzes, revives, intercepts (every line verbatim from the raid logs) ----

def test_rez_families_all_count():
    """Three healer archetypes, three flavors, one event type. Matching only
    the cleric line credited no druid or shaman with a rez."""
    for body, who in (
        ("Kashale petitions the divinities of resurrection.", "Kashale"),
        ("Ramms calls forth primeval forces of resurrection.", "Ramms"),
        ("Ariya calls forth primal forces of resurrection.", "Ariya"),
    ):
        e = ev(body)
        assert e.type == "rez" and e.src.name == who


def test_rez_anonymous_names_the_target_not_the_caster():
    e = ev("A resurrection spell is cast on Zooey.")
    assert e.type == "rez" and e.src is None and e.tgt == "Zooey"


def test_revive_grammar():
    assert ev("Aros is revived!").tgt == "Aros"
    assert ev("Sorengail is resurrected!").tgt == "Sorengail"
    assert ev("You regain consciousness!").tgt == "YOU"
    assert ev("You are revived!").tgt == "YOU"
    assert all(ev(b).type == "revive" for b in (
        "Aros is revived!", "Sorengail is resurrected!",
        "You regain consciousness!", "You are revived!"))


def test_revive_pair_in_one_second_is_one_revive():
    from parser import parse_lines
    lines = [
        "(1785632908)[Sat Aug  1 21:08:28 2026] You regain consciousness!\r\n",
        "(1785632908)[Sat Aug  1 21:08:28 2026] You are revived!\r\n",
        # a later rez of the same player is a second revive, not an echo
        "(1785632930)[Sat Aug  1 21:08:50 2026] You regain consciousness!\r\n",
    ]
    evs = [e for e in parse_lines(lines, "Bobby") if e.type == "revive"]
    assert [e.ts for e in evs] == [1785632908, 1785632930]


def test_intercept_is_credited_to_the_interceptor():
    e = ev("Buls intercepted some of the damage intended for you!")
    assert e.type == "intercept" and e.src.name == "Buls" and e.tgt == "YOU"
    # the logger's bare name is their pet, the same rule as everywhere else
    e = ev("Bobby intercepted some of the damage intended for you!")
    assert e.src.unit == "own_pet"


def test_intercept_both_variants_in_one_second_count_once():
    from parser import parse_lines
    lines = [
        "(1785630967)[Sat Aug  1 20:36:07 2026] Bobby intercepted some of the damage intended for you!\r\n",
        "(1785630967)[Sat Aug  1 20:36:07 2026] Bobby intercepted some of the damage intended for your target!\r\n",
        # two seconds later the tank steps in front of something else
        "(1785630969)[Sat Aug  1 20:36:09 2026] Bobby intercepted some of the damage intended for you!\r\n",
    ]
    evs = [e for e in parse_lines(lines, "Bobby") if e.type == "intercept"]
    assert [e.ts for e in evs] == [1785630967, 1785630969]


def test_lose_consciousness_is_a_death_and_dedupes_with_the_kill_line():
    from parser import parse_lines
    assert ev("You lose consciousness!").type == "death"
    lines = [
        "(1784856094)[Thu Jul 23 21:21:34 2026] Nizari'ishi vindicae has killed you.\r\n",
        "(1784856094)[Thu Jul 23 21:21:34 2026] You lose consciousness!\r\n",
    ]
    evs = [e for e in parse_lines(lines, "Bobby") if e.type in ("death", "kill")]
    assert len(evs) == 1


# ---------------------------------------------------------------- buff lines ---
# The curated buff grammar (parser/buffs.py) — the ONLY place another player's
# cast is visible at all, which is what makes a buff uptime computable from any
# raider's upload rather than only the buffer's own.

def test_the_buff_cast_line_names_the_caster():
    e = ev("You begin to play the song of the Jester.")
    assert (e.type, e.ability, e.src.name) == ("buff_cast", "Jester's Cap", "Bobby")
    e = ev("Vestigial begins to play the song of the Jester.")
    assert (e.type, e.ability, e.src.name) == ("buff_cast", "Jester's Cap", "Vestigial")


def test_the_landing_line_names_the_target():
    e = ev("The Jester inspires Rorschach.")
    assert (e.type, e.ability, e.tgt) == ("buff", "Jester's Cap", "Rorschach")
    # the logger's own copy of the same event
    assert ev("You feel inspired by the Jester.").tgt == "YOU"


def test_a_landing_is_credited_to_the_cast_that_produced_it():
    """The two lines are written independently — the cast names nobody's
    target, the landing names no caster — so the only link is time."""
    from parser import parse_lines
    lines = [
        "(1785025822)[Sat Jul 25 20:30:22 2026] Vestigial begins to play the song of the Jester.\r\n",
        "(1785025823)[Sat Jul 25 20:30:23 2026] The Jester inspires Adam.\r\n",
    ]
    [land] = [e for e in parse_lines(lines, "Bobby") if e.type == "buff"]
    assert (land.src.name, land.tgt) == ("Vestigial", "Adam")


def test_two_casters_in_one_window_leave_the_landing_uncredited():
    """A guess would read as measured. 590 of 596 landings in a three-troubador
    log had exactly one candidate; these are the other six."""
    from parser import parse_lines
    lines = [
        "(1785025822)[Sat Jul 25 20:30:22 2026] Vestigial begins to play the song of the Jester.\r\n",
        "(1785025822)[Sat Jul 25 20:30:22 2026] Cobbletone begins to play the song of the Jester.\r\n",
        "(1785025823)[Sat Jul 25 20:30:23 2026] The Jester inspires Adam.\r\n",
    ]
    evs = list(parse_lines(lines, "Bobby"))
    assert len([e for e in evs if e.type == "buff_cast"]) == 2
    assert [e.src for e in evs if e.type == "buff"] == [None]


def test_a_stale_cast_does_not_claim_a_later_landing():
    from parser import parse_lines
    lines = [
        "(1785025822)[Sat Jul 25 20:30:22 2026] Vestigial begins to play the song of the Jester.\r\n",
        "(1785025830)[Sat Jul 25 20:30:30 2026] The Jester inspires Adam.\r\n",
    ]
    [land] = [e for e in parse_lines(lines, "Bobby") if e.type == "buff"]
    assert land.src is None


def test_the_client_printing_a_buff_line_twice_is_one_event():
    """Same second, same caster, same target — the client echo. Two different
    casters, or two different targets, stay two events."""
    from parser import parse_lines
    lines = [
        "(1785025822)[Sat Jul 25 20:30:22 2026] Vestigial begins to play the song of the Jester.\r\n",
        "(1785025822)[Sat Jul 25 20:30:22 2026] Vestigial begins to play the song of the Jester.\r\n",
        "(1785025823)[Sat Jul 25 20:30:23 2026] The Jester inspires Adam.\r\n",
        "(1785025823)[Sat Jul 25 20:30:23 2026] The Jester inspires Adam.\r\n",
        "(1785025823)[Sat Jul 25 20:30:23 2026] The Jester inspires Bobby.\r\n",
    ]
    evs = list(parse_lines(lines, "Klebb"))
    assert len([e for e in evs if e.type == "buff_cast"]) == 1
    assert [e.tgt for e in evs if e.type == "buff"] == ["Adam", "Bobby"]


def test_a_line_that_only_looks_like_a_cast_is_not_one():
    """`You begin ...` is not a spell grammar — which is why buffs.py is a
    curated list and not the generic third-person form."""
    for body in ("You begin to breathe normally.", "You begin to move faster!",
                 "You begin to choke!", "You begin to play an augmentation song."):
        e = ev(body)
        assert e is None or e.type != "buff_cast", body
