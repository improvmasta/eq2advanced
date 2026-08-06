"""EQ2's class tree — archetype > class > subclass — and what a grant at any
level of it means.

The log and Census both speak only the bottom tier: `ranger`, `assassin`,
`troubador`. But the GAME grants at every level, and AAs especially do — the
Predator tree belongs to rangers and assassins together, a Scout AA to all
seven scouts. So "who gets this" cannot be a single class name, and it cannot
be a free-text field either: a curator writing "predator" has to mean the same
thing the code means, every time.

`expand()` is that translation, and it is the only one. A grant recorded
against `predator` groups under BOTH ranger and assassin on the Abilities page
and compares against both when deciding self-vs-granted, without the ruling
having to be written twice or kept in sync when it changes.

Census only ever writes subclass names (it expands groups itself before we see
them), so this table exists for the side Census does not cover: what a person
types when they rule on an AA.

Note this is EQ2's OWN tree, not the role grouping in `coach/descriptive.py`.
The two answer different questions and must not be merged — every one of the
six Fighter subclasses is a TANK, which is a fact about role; that Paladin and
Shadowknight are both Crusaders is a fact about the tree, and it is the one
that says who an AA reaches.
"""

# archetype -> class -> subclasses. A class with a single subclass is one EQ2
# added later without a pair; it is still a real tier with its own NAME, and
# that name is not the subclass's — Beastlord's tier-2 is Animalist, Channeler's
# is Shaper. Verified against the wiki's own category tree
# (Category:Animalist Spells sits under Category:Scout Spells, Shaper under
# Priest), which is also where a curator will have read the AA line's name.
#
# The whole table matches that tree exactly, which is worth knowing: the wiki
# files spells at every tier the game grants at, so "Predator Spells" and
# "Crusader Spells" are real categories there too.
TREE: dict[str, dict[str, tuple[str, ...]]] = {
    "fighter": {
        "warrior": ("guardian", "berserker"),
        "crusader": ("paladin", "shadowknight"),
        "brawler": ("monk", "bruiser"),
    },
    "priest": {
        "cleric": ("templar", "inquisitor"),
        "druid": ("warden", "fury"),
        "shaman": ("mystic", "defiler"),
        "shaper": ("channeler",),
    },
    "mage": {
        "sorcerer": ("wizard", "warlock"),
        "enchanter": ("illusionist", "coercer"),
        "summoner": ("conjuror", "necromancer"),
    },
    "scout": {
        "bard": ("troubador", "dirge"),
        "predator": ("ranger", "assassin"),
        "rogue": ("swashbuckler", "brigand"),
        "animalist": ("beastlord",),
    },
}

SUBCLASSES: tuple[str, ...] = tuple(sorted(
    {s for classes in TREE.values() for subs in classes.values() for s in subs}))

# Every name a grant may be recorded against, widest first — the order the
# picker shows them in, because "is this a Predator AA or a Ranger one" is the
# question, and burying the group under twenty-six subclasses does not ask it.
ARCHETYPES: tuple[str, ...] = tuple(TREE)
CLASSES: tuple[str, ...] = tuple(
    c for classes in TREE.values() for c in classes)

_MEMBERS: dict[str, frozenset[str]] = {}
for _arch, _classes in TREE.items():
    _MEMBERS[_arch] = frozenset(s for subs in _classes.values() for s in subs)
    for _cls, _subs in _classes.items():
        _MEMBERS.setdefault(_cls, frozenset(_subs))
for _s in SUBCLASSES:
    _MEMBERS[_s] = frozenset({_s})

GRANT_TARGETS: tuple[str, ...] = ARCHETYPES + CLASSES + SUBCLASSES


def is_target(name: str) -> bool:
    return (name or "").strip().lower() in _MEMBERS


def expand(name: str | None) -> frozenset[str]:
    """One grant target -> every SUBCLASS it reaches.

    `predator` -> {ranger, assassin}; `scout` -> all seven scouts; `ranger` ->
    just ranger. Anything unrecognized expands to nothing rather than to
    itself — a typo must not quietly become a class nobody can find."""
    return _MEMBERS.get((name or "").strip().lower(), frozenset())


def expand_all(names: str | None) -> frozenset[str]:
    """A comma-separated list of targets -> every subclass they reach. This is
    the form both `ability_rulings.grant_class` and Census's `class` column
    take, so one function reads both."""
    out: set[str] = set()
    for part in (names or "").split(","):
        out |= expand(part)
    return frozenset(out)


def normalize(names: str | None) -> str:
    """Clean a typed grant target list: lowercased, de-duplicated, in the
    order the tree lists them. Unrecognized names are DROPPED, so a stored
    ruling only ever contains targets `expand` can honour."""
    seen = {p.strip().lower() for p in (names or "").split(",")}
    return ",".join(t for t in GRANT_TARGETS if t in seen)


def label(name: str) -> str:
    """"predator (ranger, assassin)" — a group has to say who it covers, or a
    curator is picking a word and hoping."""
    subs = expand(name)
    if len(subs) <= 1:
        return name
    return f"{name} ({', '.join(sorted(subs))})"
