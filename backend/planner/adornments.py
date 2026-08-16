"""White adornment choices for the EoF/RoK Planner loadout.

The source is the wiki's ``Adornments/Overview`` table, which is the one place
that keeps the names and legal host-slot matrix together.  This is
reference data, not a request-time fetch: the Planner must remain useful while
Census and the wiki are unavailable.  Most values below are the T6/T7/T8 columns
(Lambent, Scintillating, Smoldering), the only tiers reachable by the two eras
the Planner currently supports.

The overview is maintained for the live ruleset.  Do not expose a stat merely
because it has a row there: Crit Bonus does not exist in this TLE window, and
the overview's 1.7--4.2 Crit Chance numbers are also live-era scaling (observed
Wuoshi values top out around 0.6).  Crit Chance stays unavailable until its
TLE tier/quality values can be sourced rather than guessed.

Individual equipped adornments still come from Census/Lexicon.  These rows are
the alternatives offered when a reader clicks an ordinary white socket.
"""

from __future__ import annotations


TIERS = (
    # tier, displayed family prefix, minimum host-item level for that tier
    (6, "Lambent", 50),
    (7, "Scintillating", 60),
    (8, "Smoldering", 70),
)

# family, grade, display effect, T6/T7/T8 values, legal equipment slots
_ROWS = (
    ("Endurance", "Lesser", "Health", (65, 90, 122), "Head Hands Earring Wrist"),
    ("Endurance", "Greater", "Health", (94, 127, 171), "Head Hands Earring Wrist"),
    ("Endurance", "Superior", "Health", (122, 165, 221), "Head Hands Earring Wrist"),
    ("Energy", "Lesser", "Power", (65, 90, 122), "Head Hands Earring Wrist"),
    ("Energy", "Greater", "Power", (94, 127, 171), "Head Hands Earring Wrist"),
    ("Energy", "Superior", "Power", (122, 165, 221), "Head Hands Earring Wrist"),
    ("Agility", "Lesser", "Primary Attributes", (9, 12, 15), "Head Legs Neck Shoulders Forearms Waist Charm"),
    ("Agility", "Greater", "Primary Attributes", (None, None, 20), "Head Legs Neck Shoulders Forearms Waist Charm"),
    ("Strength", "Lesser", "Primary Attributes", (9, 12, 15), "Head Legs Neck Shoulders Forearms Waist Charm"),
    ("Strength", "Greater", "Primary Attributes", (None, None, 20), "Head Legs Neck Shoulders Forearms Waist Charm"),
    ("Intelligence", "Lesser", "Primary Attributes", (9, 12, 15), "Head Legs Neck Shoulders Forearms Waist Charm"),
    ("Intelligence", "Greater", "Primary Attributes", (None, None, 20), "Head Legs Neck Shoulders Forearms Waist Charm"),
    ("Wisdom", "Lesser", "Primary Attributes", (9, 12, 15), "Head Legs Neck Shoulders Forearms Waist Charm"),
    ("Wisdom", "Greater", "Primary Attributes", (None, None, 20), "Head Legs Neck Shoulders Forearms Waist Charm"),
    ("Parrying", "Lesser", "Parry Skill", (8, 11, 13), "Chest Ring"),
    ("Parrying", "Greater", "Parry Skill", (13, 15, 17), "Chest Ring"),
    ("Defense", "Lesser", "Defense Skill", (8, 11, 13), "Chest Ring"),
    ("Defense", "Greater", "Defense Skill", (13, 15, 17), "Chest Ring"),
    ("Weaponry", "Lesser", "Offensive Skill", (9, 11, 13), "Chest Hands Feet Neck Wrist"),
    ("Weaponry", "Greater", "Offensive Skill", (13, 15, 18), "Chest Hands Feet Neck Wrist"),
    ("Magical Skill", "Lesser", "Spell Skill", (9, 11, 13), "Chest Hands Feet Neck Wrist"),
    ("Magical Skill", "Greater", "Spell Skill", (13, 15, 18), "Chest Hands Feet Neck Wrist"),
    ("Blasting", "Greater", "Rune of Blasting", (370, 623, 864), "Primary Secondary Ranged"),
    ("Mending", "Greater", "Rune of Mending", (370, 623, 864), "Primary Secondary Ranged"),
    ("Mending", "Superior", "Rune of Mending", (None, None, 1080), "Primary Secondary Ranged"),
    ("Arcane Resilience", "Lesser", "Resistances", (330, 385, 440), "Earring Ring Wrist"),
    ("Arcane Resilience", "Superior", "Resistances", (None, 700, 800), "Earring Ring Wrist"),
    ("Noxious Resilience", "Lesser", "Resistances", (330, 385, 440), "Earring Ring Wrist"),
    ("Noxious Resilience", "Superior", "Resistances", (None, 700, 800), "Earring Ring Wrist"),
    ("Elemental Resilience", "Lesser", "Resistances", (330, 385, 440), "Earring Ring Wrist"),
    ("Elemental Resilience", "Superior", "Resistances", (None, 700, 800), "Earring Ring Wrist"),
    ("Critical Chance", "Lesser", "Crit Chance", (1.7, 2.1, 2.5), "Primary Secondary Ranged Chest Legs Head Shoulders Forearms Hands Feet Earring Neck Wrist Ring Charm Waist Cloak"),
    ("Critical Chance", "Greater", "Crit Chance", (2.5, 2.9, 3.4), "Primary Secondary Ranged Chest Legs Head Shoulders Forearms Hands Feet Earring Neck Wrist Ring Charm Waist Cloak"),
    ("Critical Chance", "Superior", "Crit Chance", (2.9, 3.8, 4.2), "Primary Secondary Ranged Chest Legs Head Shoulders Forearms Hands Feet Earring Neck Wrist Ring Charm Waist Cloak"),
    ("Swift Casting", "Lesser", "Casting Speed", (1.7, 2.1, 2.5), "Cloak Forearms Legs Wrist Charm"),
    ("Swift Casting", "Greater", "Casting Speed", (2.5, 2.9, 3.4), "Cloak Forearms Legs Wrist Charm"),
    ("Swift Casting", "Superior", "Casting Speed", (2.9, 3.8, 4.2), "Cloak Forearms Legs Wrist Charm"),
    ("Haste", "Lesser", "Attack Speed", (1.4, 1.9, 2.3), "Cloak Shoulders Feet Waist Charm"),
    ("Haste", "Greater", "Attack Speed", (1.9, 2.6, 3.2), "Cloak Shoulders Feet Waist Charm"),
    ("Haste", "Superior", "Attack Speed", (2.5, 3.3, 4.1), "Cloak Shoulders Feet Waist Charm"),
    ("Damaging", "Lesser", "DPS", (2.8, 3.5, 4.2), "Chest Neck Waist"),
    ("Damaging", "Superior", "DPS", (4.9, 6.3, 7), "Chest Neck Waist"),
    ("Reuse", "Greater", "Reuse Speed", (0.3, 0.6, 0.9), "Secondary Ranged Feet Waist Charm"),
    ("Reuse", "Superior", "Reuse Speed", (0.4, 0.7, 1.1), "Secondary Ranged Feet Waist Charm"),
    ("Extra Attacks", "Greater", "Multi Attack", (2.5, 2.9, 3.4), "Cloak Forearms Legs Charm Ranged"),
    ("Extra Attacks", "Superior", "Multi Attack", (2.9, 3.8, 4.2), "Cloak Forearms Legs Charm Ranged"),
    ("Aggressiveness", "Greater", "Hate Gain", (2.7, 3.2, 3.6), "Hands Wrist"),
    ("Aggressiveness", "Superior", "Hate Gain", (3.2, 4.1, 4.6), "Hands Wrist"),
    ("Fading", "Greater", "Hate Gain", (-2.7, -3.2, -3.6), "Hands Wrist"),
    ("Fading", "Superior", "Hate Gain", (-3.2, -4.1, -4.6), "Hands Wrist"),
    ("Heightened Power", "Greater", "Ability Mod", (22, 28, 34), "Chest Shoulders Earring Ring"),
    ("Heightened Power", "Superior", "Ability Mod", (None, None, 43), "Chest Shoulders Earring Ring"),
    ("Blocking", "Superior", "Block Chance", (1.1, 1.4, 1.7), "Chest Primary Secondary"),
    ("Avoidance", "Superior", "Extra Riposte Chance", (1, 2, 3), "Neck"),
    # Crit Bonus is intentionally omitted: this TLE ruleset does not have it.
    ("Raw Power", "Superior", "Potency", (0.5, 1, 1.5), "Primary Secondary Ranged"),
)

_STAT_KEYS = {
    "Health": "health", "Power": "power", "Crit Chance": "crit",
    "Casting Speed": "acspeed", "Attack Speed": "aspeed", "DPS": "dps",
    "Reuse Speed": "arspeed", "Multi Attack": "multi",
    "Hate Gain": "hategain", "Ability Mod": "abmod",
    "Block Chance": "bchance", "Potency": "potency",
}
_ATTRIBUTE_KEYS = {
    "Agility": "agi", "Strength": "str", "Intelligence": "int", "Wisdom": "wis",
}
_RESIST_KEYS = {
    "Arcane Resilience": "vsarcane", "Noxious Resilience": "vsnoxious",
    "Elemental Resilience": "vselemental",
}
_PERCENT = frozenset({
    "Crit Chance", "Casting Speed", "Attack Speed", "Reuse Speed",
    "Multi Attack", "Hate Gain", "Block Chance", "Potency",
})


def white_catalog() -> list[dict]:
    """All known T6-T8 ordinary white adornment alternatives."""
    out = []
    for family, grade, effect, values, slot_text in _ROWS:
        # The live overview's Crit Chance scaling is not valid on Wuoshi.  A
        # wrong arithmetic choice is worse than an omitted one, especially
        # because selected whites immediately affect projected stats.
        if effect in {"Crit Chance", "Crit Bonus"}:
            continue
        for (tier, prefix, level), value in zip(TIERS, values):
            if value is None:
                continue
            name = f"{prefix} Adornment of {family} ({grade})"
            key = (_ATTRIBUTE_KEYS.get(family) or _RESIST_KEYS.get(family)
                   or _STAT_KEYS.get(effect))
            stats = {key: value} if key else {}
            display_effect = (family if family in _ATTRIBUTE_KEYS else
                              family.replace("Resilience", "Resistance")
                              if family in _RESIST_KEYS else effect)
            sign = "+" if value > 0 else ""
            amount = f"{sign}{value:g}{'%' if effect in _PERCENT else ''} {display_effect}"
            out.append({
                "key": f"white:{tier}:{family}:{grade}",
                "name": name, "color": "white", "tier": tier, "level": level,
                "prefix": prefix, "family": family, "grade": grade,
                "effect": display_effect, "value": value,
                "pct": effect in _PERCENT, "summary": amount,
                "slots": slot_text.split(), "stats": stats,
                # These legacy whites do not carry an expansion predicate on
                # their item pages. Compatibility is therefore host slot +
                # item level, with the two-tier window enforced by the UI.
                "predicate": None,
            })
    return out
