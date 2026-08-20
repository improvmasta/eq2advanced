"""The wiki's item, monster, quest and adornment-set templates as layer-1 fields.

Parsing only — nothing here opens a database and nothing here decides anything.
Every function takes a page title and its wikitext and returns named fields, so
the whole module is testable against recorded pages and never needs the network.
`gamewiki` owns the fetching, the batching and the polite pause; this owns what
the templates MEAN.

**The wiki is the reverse index Census does not have.** Census answers "what is
item 1546479523" exactly and cannot answer "every feet-slot item at level 80 a
necromancer can wear" (212k items, no reverse index — `docs/census-abilities.md`).
`EquipInformation` is a Census dump in template form with every stat as a named
field, and its `itemlink` carries the same signed item id the log writes, so the
two join exactly and this catalog closes the reverse-lookup gap for the only
eras the server has.

**An item's ERA comes from its SOURCE, never from the item page.** A drop page
says nothing about which expansion it belongs to; the monster that drops it says
`patch = Rise of Kunark` and the quest that rewards it says the same. That is
why the crawl inverts mobs and quests instead of reading item categories — and
it is also the only reason source attribution exists at all, since `obtain` is
blank on more than half of them.
"""

import re

import classtree
import gamewiki
import zones

# The expansions the Planner covers, RoK first because that is the one the
# server is getting. The key is what a URL and a stored row use; the name is
# `zones.EXPANSIONS`'s own spelling, so an era here and an era on a zone are
# the same string and can be compared without a translation table.
ERAS: dict[str, str] = {
    "rok": "Rise of Kunark",
    "eof": "Echoes of Faydwer",
}
ERA_KEY = {name: key for key, name in ERAS.items()}
DEFAULT_ERAS = ("rok",)

# The ADVENTURE LEVEL CAP each expansion shipped. Game knowledge, so it is
# written down rather than inferred — the same rule `zone_eras.json` keeps.
#
# THIS IS THE ONE DEFENCE AGAINST LIVE-ERA DRIFT, and it is needed: the wiki
# mirrors a 2022 scrape, and a RoK quest page whose rewards were rewritten for
# a live revamp hands back `Archon's Boots`, level 100, 3,632 Ability Mod. One
# of those in the catalog is not a stray row — it becomes the largest value the
# scorer has ever seen, and every real RoK drop then scores about 2 out of 100
# against an item nobody on this server can wear.
#
# An ITEM's level is not a quest's. A journal level above the cap is normal and
# is a tag rather than a warning (yellow quests pay better — docs/planner.md).
# An item's level is what you must BE to equip it, so above the cap it is not
# hard, it is impossible, and dropping it is a statement about the era rather
# than a guess about the page.
ERA_CAP = {"eof": 70, "rok": 80}

# The eras in RELEASE order, so "earlier" and "later" are answerable. Taken
# from `zones.EXPANSIONS` rather than written again here: the launch dates are
# already reference data and an era key already spells its expansion the same
# way.
ERA_ORDER = tuple(ERA_KEY[name] for name, _ in zones.EXPANSIONS if name in ERA_KEY)

# The LEVEL BAND an expansion's own gear and quests sit in — game knowledge,
# written down for the same reason `ERA_CAP` is. It is not derived from the
# neighbouring cap because the neighbour may not be configured: EoF is the
# earliest era here, and deriving its floor from "nothing before it" would
# claim the whole game up to 70 as EoF content and sweep tiers 1-8 for it.
#
# The band is what the TIER indexes are asked for (below). The cap is still the
# separate and stricter rule about what can be equipped at all.
ERA_BAND = {"eof": (60, 70), "rok": (71, 80)}


def era_of_level(level: int | None) -> str | None:
    """The EARLIEST era whose cap admits this level, or None.

    **A LEVEL IS AN ERA CLAIM, and for some content it is the only one.** The
    Artisan Epic reward is a level-80 earring behind a quest in Rivervale — a
    Shattered Lands zone — filed on the wiki under `LU42` and `Tier 9`. Neither
    its zone nor its category nor its patch says "Rise of Kunark", and yet the
    level says it plainly: nobody could equip it before the expansion that
    raised the cap to 80.

    Earliest rather than nearest, because an item stays equippable in every
    expansion after the one that admitted it. This is a FLOOR, so it never
    overrides an era a source actually declared — see `era_at_least`."""
    if not level:
        return None
    for key in ERA_ORDER:
        if key in ERA_CAP and level <= ERA_CAP[key]:
            return key
    return None


def era_at_least(era: str | None, level: int | None) -> str | None:
    """An era, moved forward if the level cannot be reached that early.

    A source's own claim wins whenever it is not impossible. `patch = LU42` on
    a quest whose reward is level 80 is not a lie about the update, it is just
    not the whole story: the update shipped before the cap moved, and the thing
    it rewards could not be worn until it did."""
    floor = era_of_level(level)
    if floor is None:
        return era
    if era is None:
        return floor
    order = {key: i for i, key in enumerate(ERA_ORDER)}
    return floor if order.get(floor, -1) > order.get(era, -1) else era

# The categories that enumerate one expansion's content. `Named Monsters` and
# `Quests` are the two INVERSIONS the catalog is built from: between them they
# name every item worth chasing, with its source and its era attached.
CATEGORIES = {
    key: {
        "named": f"Category:{name} Named Monsters",
        "quests": f"Category:{name} Quests",
    }
    for key, name in ERAS.items()
}
EPIC_WEAPONS_CATEGORY = "Category:Epic Weapons"
# The category omits four original Mythical reward pages. They are not
# speculative aliases: each is a real level-80 class weapon page, and keeping
# the exception beside the category makes the crawl complete without admitting
# later conversion weapons as the class progression suggestion.
EPIC_WEAPON_EXTRA_PAGES = (
    "Dream Scorcher (Mythical)",
    "Mirage Star (Mythical)",
    "Revitalized Vel'Arek",
    "Sedition, Sword of the Bloodmoon",
)
EPIC_WEAPON_EXTRA_QUESTS = (
    "A Bloodmoon Rising!",
    "Revitalizing Vel'Arek",
    "The Dream Scorcher, Part Two",
    "The Mirage Star",
)

# ---------- the expansion category is NOT the whole expansion ----------
#
# **THE WIKI DOES NOT TAG MID-EXPANSION CONTENT WITH THE EXPANSION.** A monster
# added by a live update is filed under `LU39 Named Monsters` and its tier, and
# under nothing else — `Kza'Bok` carries `LU39`, `Tier 8` and `Shard of Fear`
# and never `Echoes of Faydwer`. So `Category:Echoes of Faydwer Named Monsters`
# holds 382 mobs where the expansion actually ran to 499, and the whole of
# Shard of Fear — 14 nameds and 74 dropped items, level-70 treasured gear that
# is on the broker right now — was invisible to a crawl that asked only the
# expansion (measured 2026-08-16).
#
# The fix is to ask by ZONE, because which expansion a zone belongs to is
# already reference data here (`refdata/zone_eras.json`, `zones.in_era`) and
# `tools/sync_zone_eras.py` already resolved the live-update numbers against
# the expansion launch dates. Every named lives in a zone and every zone knows
# its era, so the zone categories are strictly broader than the expansion's own
# and need no date arithmetic and no second list to maintain.
#
# `<zone> Dropped Items` is the other half, and it is the only way to reach a
# TRASH drop at all: the mob inversion can only ever find what a NAMED page
# links, and most of what a level-70 broker search returns fell off something
# with no page of its own.
NAMED_SUFFIX = "Named Monsters"
DROPS_SUFFIX = "Dropped Items"

# What a world drop's SOURCE kind is. Not raid/group/solo even in a raid
# instance: nothing in particular drops it, which is a different claim about
# how you get it than "this named has it on its table", and the reader filtering
# the table is asking which. The zone's own difficulty rides along in `detail`.
ZONE_SOURCE_KIND = "zone"


def era_zones(era: str) -> list[dict]:
    """The zone rows reference data places in one expansion."""
    return zones.in_era(ERAS.get(era, ""))


# ---------- the THIRD index: by tier, for new content in an old zone ----------
#
# **A ZONE SWEEP CANNOT SEE NEW CONTENT IN AN OLD ZONE**, and there is a lot of
# it. The Artisan Epic runs out of Rivervale — a Shattered Lands zone — and
# rewards a level-80 earring; a holiday timeline, a city writ line and every
# revamped classic quest have the same shape. Asking `Category:Rivervale
# Quests` for RoK would mean claiming Rivervale for RoK, which is false and
# would drag in its level-20 content too.
#
# The wiki files both quests and monsters by TIER, and a tier is a level band,
# and a level band belongs to whichever expansion raised the cap to reach it.
# So the tier categories are the index that is blind to zone and to patch and
# still lands on the right era — `Category:Tier 9 Quests` holds `The Proof of
# the Pudding` (1,049 pages), and `Category:Tier 9 Equipment` holds the earring.
#
# Nothing is trusted from the category alone: everything it names is fetched
# and re-filed on its own level, which is what `era_at_least` is for.
TIER_QUESTS_SUFFIX = "Quests"
TIER_EQUIPMENT_SUFFIX = "Equipment"


def tier_of_level(level: int) -> int:
    """The wiki's tier number for a level. Tier 8 is 70-79, Tier 9 is 80-89."""
    return level // 10 + 1


def era_tiers(era: str) -> list[int]:
    """The tier numbers covering an expansion's own level band."""
    band = ERA_BAND.get(era)
    if not band:
        return []
    return list(range(tier_of_level(band[0]), tier_of_level(band[1]) + 1))


def tier_categories(era: str, suffix: str) -> list[str]:
    return [f"Category:Tier {n} {suffix}" for n in era_tiers(era)]


def named_categories(era: str) -> list[str]:
    """Every category that enumerates this expansion's named monsters.

    The expansion's own first — it is the one that carries the mobs whose zone
    page the wiki never made — then one per zone.

    NOT by tier, unlike quests and equipment: every named lives in a zone and
    every zone knows its era, so the zone index is already structurally
    complete here. A tier index would add only revamped classic content, and it
    could not be filtered honestly — a RoK raid mob is level 85, well past the
    band its loot belongs to, so the level test that files a quest cannot file
    a monster."""
    cats = [CATEGORIES[era]["named"]]
    cats += [f"Category:{z['page_title']} {NAMED_SUFFIX}" for z in era_zones(era)]
    return list(dict.fromkeys(cats))


def quest_categories(era: str) -> list[str]:
    """Every category that enumerates this expansion's quests.

    **THE EXPANSION CATEGORY IS THE SMALLEST OF THE THREE.** Measured 2026-08-19:
    `Category:Rise of Kunark Quests` holds 906 pages, its zones hold 241 more
    that it never named, and `Tier 8/9 Quests` reaches the ones filed in a zone
    from an older expansion. For EoF the zone sweep alone more than doubles it
    — 514 in the expansion category, 1,173 across its zones."""
    cats = [CATEGORIES[era]["quests"]]
    cats += [f"Category:{z['page_title']} {TIER_QUESTS_SUFFIX}" for z in era_zones(era)]
    cats += tier_categories(era, TIER_QUESTS_SUFFIX)
    return list(dict.fromkeys(cats))


# ---------- gear with no source page at all ----------
#
# **CRAFTED GEAR IS NOT DROPPED, NOT QUESTED, AND INDEXED FROM NOWHERE THE
# INVERSIONS LOOK.** A recipe makes it; no monster links it and no quest
# rewards it, so a source-first crawl is structurally incapable of finding one.
# Measured 2026-08-19: 1,107 mastercrafted pages sit in RoK's level band and
# the catalog held exactly one of them, and mastercrafted is real planning gear
# — it is what a raider wears in the slots the expansion has not dropped yet.
#
# The item side does have an index for these, and it is precise: the crafted
# categories intersected with the era's tier band. Both halves are cheap
# category listings, so the intersection costs two lookups and no page fetches,
# and only what survives it is ever read.
#
# Handcrafted is the treasured-tier version of the same corpus (990 more in the
# band) and is behind a flag: it is levelling gear, and the catalog is read by
# somebody deciding what to chase at cap.
CRAFTED_CATEGORIES = (
    "Category:Mastercrafted Equipment",
    "Category:Mastercrafted Legendary Equipment",
    "Category:Mastercrafted Fabled Equipment",
    "Category:Mastercrafted Mythical Equipment",
)
HANDCRAFTED_CATEGORIES = ("Category:Handcrafted Equipment",)

# What a crafted item's SOURCE kind is. Not solo/group/raid: you do not fight
# anything for it, and a reader filtering for what a group can get should not
# be handed a recipe. The tradeskill class rides along in `detail` when the
# page says which.
CRAFTED_SOURCE_KIND = "crafted"


def crafted_categories(era: str, handcrafted: bool = False) -> list[str]:
    cats = list(CRAFTED_CATEGORIES)
    if handcrafted:
        cats += list(HANDCRAFTED_CATEGORIES)
    return cats


def crafted_source(era: str, detail: str | None = None) -> dict:
    """The `plan_sources` fields a crafted item gets.

    No zone and no source page: a recipe is not a place and the item page is
    its own best reference. The era is the one its LEVEL admits, because that
    is the expansion whose tradeskill cap could make it."""
    return {"source_page": None, "source": "Crafted", "kind": CRAFTED_SOURCE_KIND,
            "zone": None, "level": None, "detail": detail, "era": era}


def drop_categories(era: str) -> list[tuple[str, dict]]:
    """`[(category, zone row)]` for this expansion's world drops.

    The zone row travels with the category because it IS the source: a trash
    drop has no monster to name, and the place is the whole of what can honestly
    be said about where it came from."""
    return [(f"Category:{z['page_title']} {DROPS_SUFFIX}", z)
            for z in era_zones(era)]


def zone_source(zone_row: dict, era: str) -> dict:
    """One zone -> the `plan_sources` fields a world drop gets.

    No level: a zone does not have one, and inventing the era's cap would be a
    claim the wiki never made."""
    detail = " ".join(w for w in (zone_row.get("instance"), zone_row.get("size"))
                      if w)
    return {
        "source_page": zone_row["page_title"], "source": zone_row["zone"],
        "kind": ZONE_SOURCE_KIND, "zone": zone_row["zone"], "level": None,
        "detail": detail or None, "era": era,
    }

# EQ2 gained Beastlord in 2011 and Channeler in 2014, so neither exists on a
# server running EoF or RoK. The wiki's own class templates carry `no_b=y` /
# `no_c=y` to exclude them from later-era items, which is the same correction
# made here once for every era this page serves: a class list is filtered to
# what the server HAS, not to what the template mirrored in 2022.
LATER_SUBCLASSES = frozenset({"beastlord", "channeler"})
SUBCLASSES = tuple(c for c in classtree.SUBCLASSES if c not in LATER_SUBCLASSES)

_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _clean(wikitext: str) -> str:
    """Comments first, always. `classes = |  <!-- the classes -->` otherwise
    reads its own documentation as the value — the same trap `gamewiki` names."""
    return _COMMENT.sub("", wikitext or "")


def _field(text: str, name: str) -> str:
    m = re.search(rf"^\s*\|?\s*{name}\s*=\s*(.*?)\s*\|?\s*$", text, re.I | re.M)
    return m.group(1).strip() if m else ""


def _block(text: str, name: str) -> str:
    """A multi-line template field — `drops`, `nextlist`, `effectdesc`. Runs to
    the next field or the end of the template, whichever comes first. Both
    boundaries are needed: without the `}}` stop the capture swallows the rest
    of the article."""
    m = re.search(rf"^\s*\|?\s*{name}\s*=\s*(.*?)(?=^\s*\|?\s*\w+\s*=|^\s*\|?\}}\}})",
                  text, re.I | re.M | re.S)
    return m.group(1).strip() if m else ""


_INT = re.compile(r"-?\d+")


def _int(value: str) -> int | None:
    m = _INT.search((value or "").replace(",", ""))
    return int(m.group(0)) if m else None


def _num(value: str) -> float | None:
    m = re.search(r"[-+]?\d*\.?\d+", (value or "").replace(",", ""))
    return float(m.group(0)) if m else None


# ---------- who can wear it ----------
#
# `classes` is a list of TEMPLATES, not a list of names: `{{AllShamanCats|...}}`
# is the shaman tier, `{{SubclassLink|Paladin|...}}` is one subclass, and
# `{{AllAdvCats|...}}` is everybody. The tier names are exactly `classtree`'s,
# which is the whole reason that module is the one consulted — a template that
# says "Predator" and a curator who types "predator" have to mean one thing.

_ALL_CATS = re.compile(r"\{\{\s*All(\w+?)Cats\b([^}]*)\}\}", re.I)
_SUBCLASS_LINK = re.compile(r"\{\{\s*(?:Subclass|Class)Link\s*\|\s*([^|}]+)", re.I)
_PLAIN_LINK = re.compile(r"\[\[([^\]|]+)")
# `no_b=y` drops beastlord from a scout list, `no_c=y` drops channeler from a
# priest one. Both are already excluded era-wide, so these are read only so the
# flags are not mistaken for class names.
_NO_FLAG = re.compile(r"no_[bc]\s*=", re.I)


def class_restriction(classes) -> list[str] | None:
    """A class list -> what an examine window should PRINT, or None.

    None when everything on this server can equip it. A list of every class is
    not a restriction and the game does not print one either, so the card says
    it by having no line rather than by having a line naming twenty-two
    classes. One definition because both cards ask — the Planner's and the
    loot/chat one — and they have to agree."""
    names = sorted({c for c in (classes or []) if c})
    if not names or len(names) >= len(SUBCLASSES):
        return None
    return [c.title() for c in names]


def classes_on_page(wikitext: str) -> list[str]:
    """An `EquipInformation` page -> who can equip it, era-filtered.

    The whole-page form of `classes_of`, so `items.py` can put the same line on
    a LOOT card without restating how the class templates expand. This module
    is the one that knows what `EquipInformation` means; the loot path knows
    Census, and Census's own class list is inside a `typeinfo` blob the items
    table does not keep."""
    return classes_of(_field(_clean(wikitext), "classes"))


def classes_of(value: str) -> list[str]:
    """The `classes` field -> the subclasses that can equip it, era-filtered.

    "Adv" is the template's word for every adventurer, and it is the single
    most common value — a charm or a ring that anybody can wear. It expands to
    the whole list rather than to a special token, so "which classes" has one
    kind of answer everywhere and a filter never needs a wildcard case."""
    found: set[str] = set()
    text = value or ""
    for m in _ALL_CATS.finditer(text):
        tier = m.group(1).strip().lower()
        if tier == "adv":
            found |= set(SUBCLASSES)
        else:
            found |= classtree.expand(tier)
    for m in _SUBCLASS_LINK.finditer(text):
        found |= classtree.expand(m.group(1))
    if not found:
        # A handful of pages list the classes as plain links instead.
        for m in _PLAIN_LINK.finditer(text):
            found |= classtree.expand(m.group(1))
    return sorted(found - LATER_SUBCLASSES)


# ---------- what it gives you ----------
#
# The stat vocabulary is MEASURED, not guessed: these are the fields that
# actually appear on EoF and RoK equipment pages, and the labels are the ones
# `Template:EquipInformation` itself renders (it writes
# `{{EquipmentEffect|Ability Modifier||Ability Mod}}`, so "Ability Mod" is the
# wiki's own short form and not ours).
#
# (wiki field, key, label, is a percentage)
STAT_FIELDS: tuple[tuple[str, str, str, bool], ...] = (
    # the blue block — what a raider sorts on
    ("potency", "potency", "Potency", True),
    ("crit", "crit", "Crit Chance", True),
    ("abmod", "abmod", "Ability Mod", False),
    ("multi", "multi", "Multi Attack", True),
    ("dps", "dps", "DPS", True),
    # The game's word, and the template's own short form
    # (`{{EquipmentEffect|Attack Speed||Haste}}`). "Attack Speed" is what the
    # long label says and Haste is what anybody says out loud.
    ("aspeed", "aspeed", "Haste", True),
    ("acspeed", "acspeed", "Casting Speed", True),
    ("arspeed", "arspeed", "Reuse Speed", True),
    ("flurry", "flurry", "Flurry", True),
    ("aeauto", "aeauto", "AE Autoattack", True),
    # A percentage, though the template renders it without the sign — the
    # values in the catalog are 0.9 to 2, which is not a flat amount of
    # anything. Rare (11 items across both expansions) and asked for anyway,
    # because a tank wanting it wants to know which 11.
    ("hategain", "hategain", "Hate Gain", True),
    ("dblcast", "dblcast", "Doublecast", True),
    ("strike", "strike", "Strikethrough", True),
    ("bchance", "bchance", "Block Chance", True),
    ("maxhealth", "maxhealth", "Max Health", True),
    ("mitinc", "mitinc", "Mitigation Increase", True),
    ("hregen", "hregen", "Combat Health Regen", False),
    ("accuracy", "accuracy", "Accuracy", True),
    # The wiki preserves the legacy field that originally carried an item's
    # class-facing attribute. The TLE examine window groups any one of these as
    # Primary Attributes, granting the same amount to all four; normalization
    # immediately below turns the old storage shape into that game rule.
    ("str", "str", "Strength", False),
    ("sta", "sta", "Stamina", False),
    ("agi", "agi", "Agility", False),
    ("wis", "wis", "Wisdom", False),
    ("int", "int", "Intelligence", False),
    ("comskills", "comskills", "Combat Skills", False),
    ("mit", "mit", "Mitigation", False),
    ("prot", "prot", "Protection", False),
    ("vselemental", "vselemental", "Elemental Resist", False),
    ("vsarcane", "vsarcane", "Arcane Resist", False),
    ("vsnoxious", "vsnoxious", "Noxious Resist", False),
)
STAT_LABEL = {key: label for _, key, label, _ in STAT_FIELDS}
STAT_PCT = {key for _, key, _, pct in STAT_FIELDS if pct}
STAT_KEYS = tuple(key for _, key, _, _ in STAT_FIELDS)
PRIMARY_ATTRIBUTE_KEYS = ("str", "agi", "wis", "int")


def expand_primary_attributes(stats: dict[str, float]) -> dict[str, float]:
    """Apply EQ2's grouped Primary Attributes rule to an item's flat stats.

    EQ2i's template still exposes the legacy str/agi/wis/int fields, including
    a small number of old pages with more than one. The current examine window
    has one grouped rating: if any legacy primary is present, every primary
    attribute gets the largest stated rating. Taking the maximum preserves the
    one visible rating without multiplying equal legacy fields together.
    Stamina is deliberately outside this group.
    """
    out = dict(stats or {})
    values = [out[key] for key in PRIMARY_ATTRIBUTE_KEYS if out.get(key)]
    if values:
        primary = max(values)
        out.update({key: primary for key in PRIMARY_ATTRIBUTE_KEYS})
    return out

# WHAT YOU CAN ACTUALLY PRIORITIZE, and the order the editor offers it in.
# Lindsay's list, from the game — this is the whole set, and everything else a
# catalog row carries is data on the card rather than something to rank by.
#
# **POTENCY AND CRIT ARE DELIBERATELY ABSENT.** They are on essentially every
# EoF/RoK item — measured on the real catalog at 80% and 72% of 5,282 rows —
# so ordering by them orders by nothing: every candidate has them, and the
# ranking collapses back into "how expensive is this item". The stats that
# separate two pieces of gear are the ones that are NOT on everything, and
# every entry below sits between 1% and 31%. Crit Bonus is a third case again
# and is not here for a different reason: TLE does not have the stat at all
# (`ERA_HIDDEN_FIELDS`).
#
# The three groups are how a raider already thinks about them. The first
# reaches every class, because every class casts abilities; the other two are
# what a melee and a tank respectively are actually shopping for. Max Health
# stands alone on purpose — it is wanted by more than one of them and belongs
# to none.
STAT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Abilities", ("abmod", "acspeed", "arspeed")),
    ("Melee", ("aspeed", "dps", "multi", "flurry", "aeauto")),
    ("Tanking", ("bchance", "hategain", "mit", "strike")),
    ("Also", ("maxhealth",)),
)
PRIORITY_STATS: tuple[str, ...] = tuple(
    key for _, keys in STAT_GROUPS for key in keys)
# What the page opens on: the first group, because it is the one that applies
# whatever you play. Not a recommendation — an empty order scores nothing at
# all, and a table with no ranking cannot show what this page is for.
OPENING_ORDER = ("abmod", "acspeed", "arspeed")

# Live-era stats the wiki mirrors that a TLE server does not have. Reading them
# would invite comparing two items on a stat neither one grants, which is worse
# than showing nothing — the same call `items.ERA_HIDDEN` makes for Census
# records, made again here because this data has its own field names. Crit
# Bonus is on 90% of RoK item pages and belongs to none of them.
ERA_HIDDEN_FIELDS = frozenset({"critbonus", "cbovercap", "wdbonus", "abdblcast",
                               "flurrymulti", "critfail", "critsuccess",
                               "swaspeed", "swaeauto", "swmulti", "swdbonus"})

ADORN_COLORS = ("white", "orange", "turquoise", "red", "blue", "yellow",
                "green", "purple", "cyan", "grey", "black")

# THE ARMOUR TYPE IS THE FIRST THING A PLAYER CHECKS ON A DROP, because it is
# the one property that can rule an item out before any stat on it matters —
# a plate tank cannot wear leather however good the numbers are. The wiki keeps
# it in `dtype` alongside weapon and shield types ("One-Handed Crushing",
# "Tower Shield"), so the four armour words are lifted out of that field rather
# than given a column of their own: nothing is stored twice and no re-crawl is
# needed to start filtering on it.
#
# In armour weight order, light to heavy, which is the order a player names
# them in and the order the four archetypes fall in.
ARMOR_TYPES = ("Cloth", "Leather", "Chain", "Plate")


def armor_of(dtype: str | None) -> str | None:
    """`"Chain Armor"` -> `"Chain"`. A weapon, a shield or a symbol answers
    None — they have a `dtype` and they do not have an armour weight."""
    word = (dtype or "").strip().split()
    return word[0] if word and word[0] in ARMOR_TYPES else None


# THE RARITY LADDER A PLAYER ACTUALLY HAS IN THEIR HEAD, which is not the set
# of strings the wiki's `icat` field holds. That field carries eleven distinct
# values across the catalog — `MASTERCRAFTED LEGENDARY`, `MASTERCRAFTED FABLED`
# and `FABLED, GREATER RELIC` among them — and offering all eleven as a filter
# asks the reader to know the wiki's vocabulary rather than the game's.
#
# So the filter is five BUCKETS, in ascending rarity (Lindsay's list, from the
# game). How a piece was made is not a rarity: mastercrafted armour is
# Legendary quality and a mastercrafted fabled piece is Fabled, so both fold
# into the tier they actually are. The three top rarities are one bucket
# because on a TLE server they are the same answer — "past fabled" — and
# splitting seven Mythical rows off would be three empty facet rows.
#
# Matched on WORDS PRESENT, not on the whole string, and checked from the top
# down: `MASTERCRAFTED LEGENDARY` contains `LEGENDARY`, and `FABLED, GREATER
# RELIC` contains `FABLED`, so the first bucket that recognizes a word wins.
# A value none of them recognizes (`UNCOMMON`, `-`, blank) is bucketless and
# is simply not reachable by this filter — inventing a rarity for it would be
# a claim about the item that the wiki did not make.
TIER_BUCKETS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("mythical", "Mythical+", ("MYTHICAL", "ETHEREAL", "CELESTIAL")),
    ("fabled", "Fabled", ("FABLED",)),
    ("legendary", "Legendary", ("LEGENDARY", "MASTERCRAFTED")),
    ("treasured", "Treasured", ("TREASURED",)),
    ("handcrafted", "Handcrafted", ("HANDCRAFTED",)),
)
TIER_BUCKET_LABEL = {key: label for key, label, _ in TIER_BUCKETS}
# Ascending, which is the order the facet offers them in — the ladder reads
# upward the way a player says it, and `TIER_BUCKETS` is written top-down only
# because the word test has to be.
TIER_ORDER = tuple(key for key, _, _ in reversed(TIER_BUCKETS))


def tier_bucket(tier: str | None) -> str | None:
    """`"MASTERCRAFTED FABLED"` -> `"fabled"`. None when nothing recognizes it."""
    text = (tier or "").upper()
    for key, _, words in TIER_BUCKETS:
        if any(word in text for word in words):
            return key
    return None


def is_two_handed(dtype: str | None) -> bool:
    """A two-hander takes BOTH hands and the wiki does not say so in `slot`.

    `slot = Primary` is what a greatsword and a dagger both carry, which reads
    as though you could put something in the other hand — and 162 of the
    catalog's primaries are two-handed. The fact is in `dtype`
    ("Two-Handed Crushing"), so it is lifted out of there the way the armour
    weight is: nothing stored twice, and no re-crawl to start saying it.

    Only two-handedness is claimed here. `dtype` also distinguishes "Main Hand"
    from "One-Handed" — a real difference, since a main-hand weapon cannot go
    in the off hand — but that is a separate statement and is not made by this
    one."""
    return (dtype or "").strip().lower().startswith("two-handed")


def slot_label(slot: str | None, dtype: str | None) -> str | None:
    """What the slot column says. `Primary/2H` for a two-hander, because the
    thing a reader needs to know before they compare it to a one-hander is
    that it costs them the other hand."""
    if slot and is_two_handed(dtype):
        return f"{slot}/2H"
    return slot

# `\aITEM 2117508092 -1324740137 0 0 0:Mist Covered Boots\/a` — the first id is
# the Census item id written signed, exactly as a raid log writes it, which is
# what makes the join to `items` (and so to the examine card) exact.
_ITEMLINK = re.compile(r"\\aITEM\s+(-?\d+)")


def _unsign(item_id: int) -> int:
    return item_id + 2**32 if item_id < 0 else item_id


# ---------- what the item page says about itself ----------
#
# **`obtain` IS STRUCTURED, and it was being thrown away.** The module header
# is right that it is blank on more than half of item pages and so cannot be
# the SPINE of the crawl — but "not the spine" got read as "not worth reading",
# and on the pages that do fill it in, it is the most precise source statement
# the wiki holds:
#
#     {{CraftedItem|Weaponsmith|88|Advanced Weaponsmith Volume 88}}
#     {{QuestReward|The Proof of the Pudding}}
#     {{DroppedItem|Quarry Cobble Rock||The Outer Vault|Ornate}}
#     {{VendorItem||a mysterious Quellthulian|...}}
#     {{FromCrate|Box of Old Boots (Level 85)|Box of Old Boots}}
#
# `CraftedItem`'s second parameter is the RECIPE level, and it is a better era
# signal than the item's own: `Blessed Brellium Great Spear` equips at 80 and
# is made at Weaponsmith 88, which is past every tradeskill cap this Planner
# serves. Reading it is the difference between admitting RoK's crafted gear and
# admitting all of TSO's alongside it.
_OBTAIN_TEMPLATES = ("CraftedItem", "QuestReward", "DroppedItem", "VendorItem",
                     "FromCrate")


def _templates(text: str, names) -> list[list[str]]:
    """Every `{{Name|a|b}}` in the text as `[name, a, b]`, nesting-aware.

    A `VendorItem` carries a `{{Loc}}` inside it, so splitting the whole field
    on `|` would tear one claim into three. Depth is tracked instead."""
    wanted = {n.lower() for n in names}
    out: list[list[str]] = []
    i = 0
    while (i := text.find("{{", i)) != -1:
        depth, j, parts, cur = 0, i, [], []
        while j < len(text):
            if text.startswith("{{", j):
                depth += 1
                if depth > 1:
                    cur.append("{{")
                j += 2
            elif text.startswith("}}", j):
                depth -= 1
                if depth == 0:
                    parts.append("".join(cur))
                    break
                cur.append("}}")
                j += 2
            elif text[j] == "|" and depth == 1:
                parts.append("".join(cur))
                cur = []
                j += 1
            else:
                cur.append(text[j])
                j += 1
        else:
            break                              # an unclosed template ends it
        if parts and parts[0].strip().lower() in wanted:
            out.append([p.strip() for p in parts])
        i = j + 2
    return out


def parse_obtain(wikitext: str) -> dict:
    """The `obtain` field -> the claims it makes, by kind.

    Kept as claims rather than resolved sources: what a title MEANS is the
    crawl's job — a quest named here has to be fetched before its era is known,
    and a recipe level has to be compared against a cap this module does not
    apply."""
    block = _block(_clean(wikitext), "obtain")
    out: dict[str, list] = {"crafted": [], "quests": [], "dropped": [],
                            "vendors": [], "crates": []}
    if not block:
        return out
    for parts in _templates(block, _OBTAIN_TEMPLATES):
        name = parts[0].lower()
        args = parts[1:]

        def arg(n):
            return args[n].strip() if len(args) > n and args[n].strip() else None

        if name == "crafteditem":
            out["crafted"].append({"ts_class": arg(0), "level": _int(arg(1) or ""),
                                   "book": arg(2)})
        elif name == "questreward" and arg(0):
            out["quests"].append(_PIPE_ESCAPE.split(arg(0))[0].strip())
        elif name == "droppeditem":
            out["dropped"].append({"mob": arg(0), "zone": arg(2), "chest": arg(3)})
        elif name == "vendoritem":
            out["vendors"].append({"zone": arg(0), "npc": arg(1)})
        elif name == "fromcrate" and arg(0):
            out["crates"].append(arg(0))
    return out


def parse_equip(page_title: str, wikitext: str) -> dict | None:
    """An `EquipInformation` page -> one catalog row, or None if the page is
    not a piece of equipment.

    Most of what a drop link points at is NOT one: a disambiguation, an armour
    pattern (`ItemInformation`), a recipe book. Returning None for those is
    what keeps the catalog to things you can wear."""
    text = _clean(wikitext)
    if not re.search(r"\{\{\s*EquipInformation\b", text, re.I):
        return None
    stats: dict[str, float] = {}
    for field, key, _, _ in STAT_FIELDS:
        value = _num(_field(text, field))
        if value:
            stats[key] = value
    stats = expand_primary_attributes(stats)
    adorns = {c: n for c in ADORN_COLORS
              if (n := _int(_field(text, f"{c}slot")))}
    census_id = None
    m = _ITEMLINK.search(text)
    if m:
        census_id = _unsign(int(m.group(1)))
    # `effectlist` is the proc's NAME and `effectdesc` is what it does. Kept as
    # written: whether a proc is worth anything is a class question, and a class
    # question is answered from the game and by a person (docs/planner.md,
    # layer 2), never by this parser.
    effects = _field(text, "effectlist")
    effect_desc = _block(text, "effectdesc")
    return {
        "page_title": page_title,
        "name": _field(text, "altname") or gamewiki.log_name(page_title),
        "census_id": census_id,
        "slot": _field(text, "slot") or None,
        "slot2": _field(text, "slot2") or None,
        "level": _int(_field(text, "level")),
        "tier": (_field(text, "icat") or "").upper() or None,
        "dtype": _field(text, "dtype") or None,
        "wtype": _field(text, "wtype") or None,
        "classes": classes_of(_field(text, "classes")),
        "flags": _field(text, "flags") or None,
        "adorns": adorns,
        "set_name": _field(text, "set") or None,
        "stats": stats,
        "effects": re.sub(r"<br\s*/?>", ", ", effects).strip() or None,
        "effect_desc": _strip_markup(effect_desc) or None,
        "icon": _int(_field(text, "iconnum")),
        # Not a stored column — the crawl reads it to decide where the item
        # came from when nothing pointed at it. See `parse_obtain`.
        "obtain": parse_obtain(wikitext),
    }


# ---------- the box the drop actually is ----------
#
# **A SET PIECE IS BEHIND A CRATE, AND THE CRATE IS WHAT THE MOB DROPS.** The
# Priest of Fear drops `Faydwer Cloth Pattern: Head`; what you equip is one of
# the three hoods inside it, and only one of those has Reuse Speed on it. The
# crate is an `ItemInformation` page, so `parse_equip` correctly refuses it —
# and the armour it stands in front of was then reachable from nothing at all.
#
# `contains` is a `CItemList` of numbered fields, and a piped display name is
# escaped `{{!}}` because a bare pipe would end the template parameter. The
# page title is the half before it.
_CITEM = re.compile(r"^\s*\|?\s*item\d+\s*=\s*(.+?)\s*\|?\s*$", re.M)
_PIPE_ESCAPE = re.compile(r"\{\{\s*!\s*\}\}")


def crate_contents(wikitext: str) -> list[str]:
    """A crate page -> the item pages it hands out, or nothing.

    Nothing for an ordinary item page: this is only asked of pages the equipment
    parser already rejected, and the numbered-field shape is the crate's own."""
    text = _clean(wikitext)
    if not re.search(r"\{\{\s*CItemList\b", text, re.I):
        return []
    out = []
    for raw in _CITEM.findall(text):
        title = _PIPE_ESCAPE.split(raw)[0].strip().strip("[]").strip()
        if title and not _NOT_A_TITLE.search(title):
            out.append(title)
    return out


_MARKUP = re.compile(r"'''''|'''|''|\[\[([^\]|]*\|)?|\]\]|\{\{[^}]*\}\}")


def _strip_markup(text: str) -> str:
    return re.sub(r"[ \t]+", " ", _MARKUP.sub("", text or "")).strip()


# ---------- where it comes from ----------

# `diff = epic x4` / `Heroic` / `Solo` on a monster, and the same word on a
# quest. The KIND is what a reader filters on — a raider and somebody levelling
# alone are asking different questions of the same catalog — so the wiki's
# free-typed capitalisation is collapsed here rather than at every read.
_DIFF_KIND = (
    (re.compile(r"epic", re.I), "raid"),
    (re.compile(r"heroic|group|x2", re.I), "group"),
    (re.compile(r"solo", re.I), "solo"),
)


def source_kind(diff: str | None) -> str:
    for pattern, kind in _DIFF_KIND:
        if pattern.search(diff or ""):
            return kind
    return "unknown"


_LINKS = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
_EQUIP_TPL = re.compile(r"\{\{\s*Equip\s*\|\s*([^|}]+)", re.I)

# **A REWARD IS THE FIRST THING A BULLET NAMES, WHATEVER TEMPLATE NAMES IT.**
# Reading only `{{Equip}}` lost whole quests: measured over 200 RoK pages, 16
# wrote every reward as `{{Item}}` and 4 more hid some behind it. `The Proof of
# the Pudding` is one of the 16 — the Artisan Epic earring is written
# `{{Item|Earring of the Solstice||}}`, so the quest looked like it rewarded
# nothing at all.
#
# One link per bullet, not every link on the page, and this is the rule wikq2
# already arrived at against the RENDERED page (`collectQuestRewards` in
# `lib/eq2.ts` takes the first usable anchor of each `<li>` under the Rewards
# heading). It is what separates a reward from the prose around it: the bullet
# leads with what you get and then explains it, and an explanation links zones,
# NPCs and factions that are emphatically not rewards.
#
# `{{Item}}` is not equipment-specific — the same list hands out
# `{{Item|mahogany lumber||}}` — and that costs one page fetch and nothing
# else, because `parse_equip` refuses any page without an `EquipInformation`
# block.
# `#` as well as `*`: a choice of rewards is written as a NUMBERED list —
# "One of the following:" then `#{{Equip|Gruedheim Beater}}` — and reading only
# `*` silently dropped every quest that offers a choice.
_REWARD_BULLET = re.compile(r"^\s*[*#]+\s*(.+)$", re.M)
_REWARD_LINK = re.compile(
    r"\{\{\s*Equip\s*\|\s*([^|}]+)"
    r"|\{\{\s*Item\s*\|\s*([^|}]+)"
    r"|\[\[([^\]|#]+)", re.I)


def _reward_links(block: str) -> list[str]:
    """The Rewards block -> one title per list item, in order.

    Falls back to every reward TEMPLATE in the block when the section is not a
    list at all. One-per-item is the precise rule and it depends on the page
    being written as a list; where it is not, the old behaviour is still
    strictly better than returning nothing, and a template is a reward claim
    wherever it appears."""
    out = []
    for line in _REWARD_BULLET.findall(block):
        m = _REWARD_LINK.search(line)
        if not m:
            continue
        title = next(g for g in m.groups() if g)
        title = _PIPE_ESCAPE.split(title)[0].strip()
        if title:
            out.append(title)
    if out:
        return out
    return [_PIPE_ESCAPE.split(g)[0].strip()
            for m in _REWARD_LINK.finditer(block)
            for g in [next((x for x in m.groups()[:2] if x), None)] if g]


def links(wikitext: str) -> list[str]:
    """Every `[[page]]` a chunk of wikitext points at, in order."""
    return [t.strip() for t in _LINKS.findall(wikitext or "") if t.strip()]


def parse_named(page_title: str, wikitext: str) -> dict | None:
    """A `NamedInformation` page -> the mob, and the items it drops.

    `diff` gives the raid/group/solo split for free, which is the answer no
    item page carries, and `zone` is where you go to get it."""
    text = _clean(wikitext)
    if not re.search(r"\{\{\s*NamedInformation\b", text, re.I):
        return None
    drops = [d.strip() for d in _LINKS.findall(_block(text, "drops"))]
    diff = _field(text, "diff")
    return {
        "page_title": page_title,
        "name": gamewiki.log_name(page_title),
        "zone": _field(text, "zone") or None,
        "era": _field(text, "patch") or None,
        "level": _int(_field(text, "level")),
        "diff": diff or None,
        "kind": source_kind(diff),
        "drops": [d for d in drops if d and not d.lower().startswith(("category:", "file:"))],
    }


_REWARDS = re.compile(r"^==+\s*Rewards?\s*==+(.*?)(?=^==[^=]|\Z)", re.M | re.S)

# ---------- what comes before a quest, and what comes after ----------
#
# MEASURED ON 400 REAL RoK QUEST PAGES (2026-08-15), because the shape of these
# fields is the whole outline: `prereq` is filled on 242 of them and `next` on
# 223, and both are a SINGLE page title written as plain text — 98% of them
# resolve to another page in the same category. `prelist` and `nextlist` are
# the multi-valued forms (8 and 55 of the 400) and are written as wikilinks or
# `{{Quest|…}}` templates.
#
# **A COMMA IN A PREREQ IS PART OF THE TITLE AND IS NEVER A SEPARATOR.**
# `prereq = Warm Skins, Fat Bellies`, `prereq = One Fish, Two Fish` and
# `next = Mischief, Mayhem, Clockwork` are ONE quest each. Splitting on the
# comma would invent five quests that do not exist and lose the three that do
# — which is why the plain fields are read whole and only the LIST fields are
# split, and those are split on their links rather than on their punctuation.
_QUEST_REF = re.compile(
    r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]|"
    r"\{\{\s*Quest\s*\|\s*([^|}\n]+)(?:\|[^}]*)?\}\}", re.I)
# An empty field followed by template markup hands `_field` back `}}`, `| =`
# or `| >` — measured on the same 400 pages. A page title contains none of
# these characters, so anything carrying one is punctuation rather than a name.
_NOT_A_TITLE = re.compile(r"[\[\]{}|=<>#]")
# `{{Quest|A}} / {{Quest|B}}` and `[[A]] or [[B]]` are ALTERNATIVES; a line
# break or a comma between links is a list of things you need all of.
_ALTERNATIVE = re.compile(r"/|\bor\b", re.I)


def _plain_ref(value: str) -> list[list[str]]:
    """A single-valued `prereq`/`next` -> one OR-group, or nothing.

    Read WHOLE. The value is a page title and titles contain commas."""
    title = (value or "").strip().strip(".")
    if not title or _NOT_A_TITLE.search(title) or len(title) > 120:
        return []
    return [[title]]


def _list_refs(block: str) -> list[list[str]]:
    """A `prelist`/`nextlist` block -> OR-groups of page titles.

    Links only — a list field is written as links, and reading its prose would
    turn "See Previous Quests Below" into a quest. Segments separated by a line
    break or a comma are things you need ALL of; links separated by `/` or the
    word "or" inside one segment are ALTERNATIVES and share a group.

    **OR-groups exist from the start on purpose.** Kunark's prerequisites
    really are disjunctive — the sokokar network wants adventure 65 *or*
    tradeskill 65, two separate lines reaching one unlock — and retrofitting
    them once every consumer assumes a flat prereq list touches everything
    (docs/planner.md)."""
    text = block or ""
    matches = list(_QUEST_REF.finditer(text))
    groups: list[list[str]] = []
    previous = None
    for match in matches:
        title = (match.group(1) or match.group(2) or "").strip()
        if not title or _NOT_A_TITLE.search(title):
            previous = match
            continue
        # Read delimiters BETWEEN complete references. Splitting the raw block
        # on commas would split `[[Warm Skins, Fat Bellies]]` in half — the
        # exact title-corruption this parser exists to prevent.
        between = text[previous.end():match.start()] if previous else ""
        if groups and _ALTERNATIVE.search(between):
            groups[-1].append(title)
        else:
            groups.append([title])
        previous = match
    return groups


def _edges(text: str, one: str, many: str) -> list[list[str]]:
    """The single field and the list field for one direction, together."""
    return _plain_ref(_field(text, one)) + _list_refs(_block(text, many))


def parse_quest(page_title: str, wikitext: str) -> dict | None:
    """A `QuestInformation` page -> the quest, what it rewards, and its chain.

    A gear reward is written `{{Equip|Name}}` — the same template the adornment
    set pages use for their pieces — with `{{Item|…}}` reserved for things you
    cannot wear. Only the first is read, for the same reason the item crawl
    drops armour patterns: this catalog is things you equip.

    **A journal level above the era's cap is normal and is not read as drift.**
    A yellow or red quest usually pays better, which is exactly the kind of
    thing worth surfacing; comparing `level` to a cap here would flag the good
    ones (docs/planner.md). It is also why `level` can be missing entirely —
    `level = Scales` is a real value on three of every four hundred pages, and
    a quest that scales has no number to sort on rather than a number of 0.

    `kind` is the SOURCE kind for the catalog and is always `quest`; `diff_kind`
    is the quest's own solo/group/raid difficulty, which is a different
    question and the one the outline shows."""
    text = _clean(wikitext)
    if not re.search(r"\{\{\s*QuestInformation\b", text, re.I):
        return None
    rewards: list[str] = []
    timeline = _field(text, "timeline")
    m = _REWARDS.search(text)
    if m:
        block = m.group(1)
        # `{{Equip}}`, `{{Item}}` and a plain link are all read the same way
        # now — one per bullet — which subsumes the old class-epic exception.
        # That exception existed because the epic pages write their reward as
        # `[[Bite of the Wolf (Fabled)]]` and reading only the template
        # silently removed the defining rewards of Rise of Kunark from the
        # catalog; the general rule covers it and 20 more reward titles per 200
        # quest pages besides. Order-preserving dedupe: a page that writes the
        # same reward twice rewards it once.
        rewards = list(dict.fromkeys(_reward_links(block)))
    diff = _field(text, "diff")
    level_text = _field(text, "level")
    return {
        "page_title": page_title,
        "name": _field(text, "altname") or gamewiki.log_name(page_title),
        "era": _field(text, "patch") or None,
        "level": _int(level_text),
        "level_text": level_text or None,
        "zone": _field(text, "szone") or None,
        "timeline": timeline or None,
        "jcat": _field(text, "jcat") or None,
        "diff": diff or None,
        "kind": "quest",
        "diff_kind": source_kind(diff),
        # What must be done first, and what this opens. Both directions are
        # read because the wiki fills them independently: a chain is often
        # written forward on one page and backward on the next, and the two
        # together close gaps neither one has on its own.
        "prereq": _edges(text, "prereq", "prelist"),
        "next": _edges(text, "next", "nextlist"),
        "rewards": [r for r in rewards if r],
    }


# ---------- the set bonus, which is not on the armour ----------

_ADORN_SET = re.compile(r"\{\{\s*AdornmentSet\s*\|\s*([^|}\n]+)", re.I)
# `*(3) Applies '''''Focus: Magi's Shielding.'''''` — the tier count, then a
# BLOCK that runs to the next tier or the end of the template.
#
# **A TIER IS NOT ONE LINE, AND READING IT AS ONE LOSES THE STATS.** The page
# writes the proc on the `*(N)` line, its explanation in `**` sub-bullets under
# it, and the tier's flat stats as BARE LINES after those — one per stat:
#
#     *(2) Applies '''''Focus: Lifetap IV.'''''
#     **Reduces power cost of Lifetap IV by 200.
#     3 Potency
#     *(6)
#     4 Potency
#     100 Ability Modifier
#     5 Crit Chance
#
# The game draws those bare lines ON the tier's own line ("(6) 4 Potency, 100
# Ability Mod, 5 Crit Chance"), which is where they belong and where the
# previous single-line read could never find them: `(2)` and `(4)` lost their
# Potency outright, `(6)` kept the first of three, and a tier whose own line is
# empty was dropped altogether.
#
# Bounded by the next tier, by the template's next named field and by its
# closing braces — the same three stops `_block` uses. Without the last two the
# final tier reads `level =70` and `}}` as two more of its stats.
_BONUS_TIER = re.compile(
    r"^\*\s*\((\d+)\)[ \t]*(.*?)"
    r"(?=^\*\s*\(\d+\)|^\s*\|?\s*\w+\s*=|^\s*\|?\}\}|\Z)", re.M | re.S)
# Inside a tier: a bulleted line is prose about the proc, anything else is a
# stat line. The depth is the number of asterisks and is not kept — the card
# shows one flat list, the way the examine window does.
_BULLET = re.compile(r"^\*+\s*")


def parse_adorn_set(page_title: str, wikitext: str) -> dict | None:
    """An `(Adornment Set)` page -> its pieces and its tiered bonuses.

    **In EoF and RoK the set bonus is not on the armour.** It is on a turquoise
    adornment that ships inside the item and can be pulled out and moved into
    anything of the same level or higher, which is why a set is a first-class
    row here and not a column on an item: the most valuable part of a drop
    detaches (docs/planner.md)."""
    text = _clean(wikitext)
    m = _ADORN_SET.search(text)
    if not m:
        return None
    pieces = [p.strip() for p in _EQUIP_TPL.findall(text)]
    bonuses = []
    for count, block in _BONUS_TIER.findall(text):
        head, detail, stats = "", [], []
        for i, raw in enumerate(block.splitlines()):
            line = _strip_markup(raw.strip().strip("|")).strip()
            if not line:
                continue
            if i == 0:
                head = line               # the `(N)` line's own text, if any
            elif _BULLET.match(raw.strip()):
                detail.append(_BULLET.sub("", line).strip() or line)
            else:
                stats.append(line)
        if head or detail or stats:
            # `text` stays the tier's headline so nothing reading the old shape
            # breaks. `stat_lines` and `detail` are what it was silently
            # losing — and `stat_lines` is deliberately not called `stats`,
            # because `catalog.sets` puts the TYPED version under that name and
            # two different things wearing one key is how this got lost once
            # already.
            bonuses.append({"pieces": int(count), "text": head,
                            "stat_lines": stats, "detail": detail})
    return {
        "page_title": page_title,
        "name": m.group(1).strip(),
        "level": _int(_field(text, "level")),
        "pieces": pieces,
        "bonuses": bonuses,
    }


def era_name(key: str) -> str | None:
    return ERAS.get((key or "").strip().lower())


EXPANSION_RANK = {name: i for i, (name, _) in enumerate(zones.EXPANSIONS)}


def expansion_of_patch(patch: str | None, lu_eras: dict | None = None) -> str | None:
    """A `patch = …` value -> the EXPANSION it shipped in, ours or not.

    Every expansion, not only the two the Planner serves, because the question
    this answers is "did this page come from LATER than the era being crawled"
    and the answer has to be able to be "yes, The Shadow Odyssey" for a crawl
    that has never heard of it.

    `lu_eras` is `{"LU42": "Echoes of Faydwer"}` — resolved once per crawl by
    `ingest`, since dating a live update is a network read and this module does
    not do those."""
    name = (patch or "").strip()
    if name in EXPANSION_RANK:
        return name
    return (lu_eras or {}).get(name)


def era_of_patch(patch: str | None, lu_eras: dict | None = None) -> str | None:
    """A `patch = …` value -> the era key this Planner knows, or None.

    The field is free text on the wiki and carries live-update names as well as
    expansion names, so anything not one of ours is simply not ours — an item
    is filed under an era we serve or it is not filed at all."""
    return ERA_KEY.get(expansion_of_patch(patch, lu_eras) or "")


def declared_after(patch: str | None, era: str, lu_eras: dict | None = None) -> bool:
    """Does the page's own patch put it in a LATER expansion than this crawl?

    The one guard the tier sweep needs. `Category:Tier 9 Quests` is level 80,
    and level 80 is Rise of Kunark AND The Shadow Odyssey — the cap did not
    move between them — so the level alone cannot separate the two and the
    page's own patch has to be asked. Unknown is not later: a page that says
    nothing is decided on its level, which is the whole point of the sweep."""
    declared = expansion_of_patch(patch, lu_eras)
    if declared is None:
        return False
    mine = ERAS.get(era)
    return EXPANSION_RANK.get(declared, -1) > EXPANSION_RANK.get(mine, -1)


ERA_LABEL = {key: zones.ERA_SHORT.get(name, name) for key, name in ERAS.items()}
