"""Behavioral entity refinement. A single-token capitalized name ("Venekor")
is grammatically indistinguishable from a player, so `classify_entity_kind`
defaults it to player — which puts one-word bosses in the raider table and
mislabels their kills as trash. This pass reads the whole parsed event stream
and reclassifies such names as mobs when their BEHAVIOR proves it:

- they are the victim of a player-credited kill line, or
- they trade damage with the raid (hit >= 2 confirmed players AND are hit by
  >= 3 confirmed players) while never appearing on either side of a heal.

"Confirmed players" are names with player-only behavior: the logger, the names
`roster_prescan` PROVES are people, heal / ward / power / rez sources, and
swarm/named-pet owners. Anyone who damages an articled mob ("a bloodgorger") is
also player-like — that guard keeps a mind-controlled raider (a player-shaped
kill victim) from reclassifying.

OWNING A SWARM PET IS NOT PROOF OF PERSONHOOD. It reads like it — only players
summon dumbfires — but the log re-parents a charmed pet to whatever holds it,
so an encounter that steals the raid's pets prints `Enynti's protoflame` and
`Enynti's awaken grave` for a BOSS. On Lindsay's Mistmoore's Inner Sanctum
night that one grammar promoted Enynti to a confirmed player, which vetoed both
promotions below — and the raid's own kill target sat in the raider table with
872k damage, 24 people attacking it, having "cast" Ultraviolet Beam, Harm Touch
and Chromatic Shower (it was hit by them). So the pet-owner rule is applied
only to names the raid never KILLED: a thing you kill 28 times is not a raider,
and a real raider under mind control is held by `roster` and `hits_mobs`
instead, which are evidence about the person rather than about their grammar.

HEALS ONLY COUNT WHEN THEY ARE RAID-INTERNAL. Bosses heal: `Wuoshi's Nature's
Salve heals Wuoshi for 30,740 hit points.` fired twice on Lindsay's 2026-08-04
Emerald Halls night, and those two lines out of 248k events made Wuoshi
player-like, which vetoed the kill-victim promotion below. The whole
run was then titled after the biggest thing left that WAS a mob — "Ancient
Grovebeast", the adds — and the boss sat in the raider table at #17 damage
with 72M taken. So: a self-heal proves nothing (mobs and healers both do it),
and being healed only proves you are a player if a CONFIRMED player healed
you. Heal edges are therefore resolved after the main pass, once `confirmed`
is complete.

Pure function over ParsedEvents; runs before entity resolution and
segmentation.
"""

import re
from collections import defaultdict

from parser.events import ParsedEvent
from parser.subjects import _has_article

MIN_PLAYERS_HIT = 2
MIN_PLAYER_ATTACKERS = 3

# Lines only a PLAYER CHARACTER can produce. A summoned pet fights, takes group
# buffs and dies, but it never chats, never joins a raid, never loots and is
# never resurrected — and EQ2 writes a bare-named pet exactly like a raider
# ("Kober hits an ethereal veilrunner"), with no owner possessive anywhere in
# the file to give it away. On Lindsay's 2026-08-04 Emerald Halls night that
# put Kartik, Vaser, Leneker and Kober in the raider table with inferred
# classes; each acted in ONE of 21 encounters, while every real raider acted in
# 20 or more.
_ROSTER_PATTERNS = (
    re.compile(r"^(?:Guildmate: )?([A-Z][A-Za-z'`-]+) has (?:joined|left) the raid\b"),
    re.compile(r"^(?:Guildmate: )?([A-Z][A-Za-z'`-]+) has logged (?:in|out)\b"),
    # loot carries an \aITEM link and nothing else does. `receives` USED to be
    # here beside it and is not proof of anything: "Shotar receives a
    # transcendent injury!" is a debuff landing on a summoned pet, and it read
    # exactly like "Bobby receives a Dark Heart." — four dumbfires were
    # promoted to proven raiders by a combat message.
    re.compile(r"^([A-Z][A-Za-z'`-]+) (?:loots|has looted) \\aITEM\b"),
    re.compile(r"^([A-Z][A-Za-z'`-]+) (?:has been resurrected|is no longer linkdead)\b"),
    re.compile(r"^([A-Z][A-Za-z'`-]+) has joined the group\b"),
)
_RE_PC_TAG = re.compile(r"\\aPC -?\d+ ([A-Za-z'`-]+):")


def roster_prescan(lines, logger: str) -> frozenset[str]:
    """Names this log PROVES are player characters.

    Deliberately over-inclusive — server-wide chat counts, so a stranger who
    said hello in General lands in here. That is the safe direction: this set
    is only ever used to withhold a claim about an actor we have no player
    evidence for, so a name wrongly included merely keeps the status quo, while
    a name wrongly excluded would strip a real raider's class.
    """
    from parser.prefix import split_prefix
    names = {logger}
    for line in lines:
        parts = split_prefix(line)
        body = parts[1] if parts else line
        tag = _RE_PC_TAG.search(body)
        if tag:
            names.add(tag.group(1))
        for pat in _ROSTER_PATTERNS:
            m = pat.match(body)
            if m:
                names.add(m.group(1))
                break
    return frozenset(names)


MIN_PET_ABILITIES = 2


def refine_bare_pets(events: list[ParsedEvent], logger: str, roster: frozenset[str],
                     pet_abilities: frozenset[str],
                     known_mobs: frozenset[str] = frozenset(),
                     census_missing: frozenset[str] = frozenset()) -> frozenset[str]:
    """Bare-named summoned pets, by the one thing they cannot hide: their KIT.

    EQ2 writes a dumbfire with no owner possessive anywhere in the file, so
    `petnames` can never reach it and the grammar makes it a raider — which is
    how `Viber`, `Knyi`, `Geker`, `Holmes` and `Reaper` ended up in the raid
    table with no class. But `Viber` cast Grisly Feedback (a necromancer Grim
    Sorcerer's), `Knyi` cast Confusion and Headache (an illusionist pet's) and
    `Geker` cast Graven Vanquishing (a conjuror pet's) — `ability_catalog`
    already knows every one of those is `unit='pet'`, because real pets under
    real owners taught it.

    So: a single capitalized name the file never proves is a person, casting
    MIN_PET_ABILITIES abilities out of a pet's kit, is a pet. The roster is the
    veto — a raider who somehow shares an ability name with a pet kit still
    chatted, looted or joined the raid, and a pet never does.

    `census_missing` is the second, independent way in: names Census was ASKED
    about and does not have (`census/roster.py`, `found=0`). `Holmes` only ever
    melees, so no kit gives it away, but no character by that name exists on the
    server and the file never proves a person either. Two independent negatives
    are enough — a real raider since deleted or renamed would still have left a
    personhood line across a whole raid night, and `roster` vetoes on it. An
    empty `census_missing` (nothing looked up yet) simply costs nothing.

    BOTH ways in require the name to fight the raid's ENEMIES, because that is
    what separates a hireling from a hostile. Without it, "Census has never
    heard of this name" swept up every mob the pass below had not promoted —
    `Bristlecone`, `Ishka-Urz`, `Mai'sith` — and the `Unknown` damage bucket
    with them. A pet or a mercenary swings at articled mobs all night; a boss
    swings at the raid. `known_mobs` is excluded outright on top of that: mobs
    cast pet kits too (`Enynti` cast Grave Decay), and that pass is the stronger,
    behavior-proven finding."""
    cast: dict[str, set[str]] = defaultdict(set)
    fights_our_enemies: set[str] = set()
    for ev in events:
        src = ev.src
        if src is None or src.unit != "unknown" or not _single_token_cap(src.name):
            continue
        if src.name in roster or src.name in known_mobs:
            continue
        if ev.type == "damage" and ev.tgt and _has_article(ev.tgt):
            fights_our_enemies.add(src.name)
        if ev.ability and ev.ability in pet_abilities:
            cast[src.name].add(ev.ability)
    return frozenset(
        n for n in fights_our_enemies
        if len(cast.get(n, ())) >= MIN_PET_ABILITIES or n in census_missing)


def _single_token_cap(name: str | None) -> bool:
    return (bool(name) and " " not in name and name[:1].isupper()
            and not name.lower().startswith(("a ", "an ", "the ")))


def refine_known_mobs(events: list[ParsedEvent], logger: str,
                      roster: frozenset[str] = frozenset()) -> frozenset[str]:
    """`roster` is `roster_prescan`'s output — the names this file PROVES are
    player characters (chat, loot, raid join, login, resurrection). It is the
    only player evidence here that a mob cannot manufacture, so it seeds
    `confirmed` and it is the one signal that can never be overridden."""
    # the logger is "YOU"/"YOURSELF" on their own lines; both ends of a heal
    # edge have to be the same vocabulary before they can be compared
    def _me(name: str | None) -> str | None:
        return logger if name in ("YOU", "YOURSELF") else name

    confirmed: set[str] = {logger} | set(roster)
    pet_owners: set[str] = set()
    heal_edges: list[tuple[str | None, str | None]] = []   # (healer, healed)
    heal_involved: set[str] = set()
    kill_victims: set[str] = set()
    hit_players: dict[str, set[str]] = defaultdict(set)     # name -> players it damaged
    hit_by: dict[str, set[str]] = defaultdict(set)          # name -> sources that damaged it
    hits_mobs: set[str] = set()                             # names that damaged an articled mob

    for ev in events:
        src = ev.src
        if src is not None:
            if src.unit == "player":
                confirmed.add(src.name)
            elif src.unit in ("swarm_pet", "named_pet") and _single_token_cap(src.name):
                # held back until the kill victims are known — see the module
                # docstring on `Enynti's protoflame`
                pet_owners.add(src.name)
            if ev.type in ("heal", "ward", "power", "rez") and _single_token_cap(src.name):
                heal_edges.append((src.name, ev.tgt))
        if ev.type == "heal" and ev.tgt and _single_token_cap(ev.tgt):
            heal_edges.append((src.name if src else None, ev.tgt))
        if ev.type == "kill" and ev.tgt and _single_token_cap(ev.tgt):
            killer = src.name if src else None
            if killer == logger or _single_token_cap(killer):
                kill_victims.add(ev.tgt)
        if ev.type == "damage" and src is not None and ev.tgt:
            if _single_token_cap(src.name) and src.unit == "unknown":
                hit_players[src.name].add(logger if ev.tgt in ("YOU", "YOURSELF") else ev.tgt)
                if ev.tgt.lower().startswith(("a ", "an ", "the ")):
                    hits_mobs.add(src.name)
            if _single_token_cap(ev.tgt):
                hit_by[ev.tgt].add(src.name)

    # A summoned pet belongs to a player — unless the raid killed the owner,
    # in which case the log was describing a charmed pet and the "owner" is
    # what the raid was fighting.
    confirmed |= {n for n in pet_owners if n not in kill_victims}

    # Resolve heals now that `confirmed` is complete. A heal is evidence about
    # the two names on it only if it crossed BETWEEN them and one end is
    # already known to be a player — `Wuoshi heals Wuoshi` is neither. Run to a
    # fixpoint over the DEDUPED edges (roster-sized, not line-sized) so a chain
    # of raid heals resolves whatever order the lines arrived in.
    edges = {(_me(h), _me(t)) for h, t in heal_edges}
    edges = {(h, t) for h, t in edges if h and t and h != t}
    while True:
        grew = False
        for healer, healed in edges:
            if _single_token_cap(healer) and healed in confirmed and healer not in confirmed:
                confirmed.add(healer)
                heal_involved.add(healer)
                grew = True
            if _single_token_cap(healed) and healer in confirmed and healed not in heal_involved:
                heal_involved.add(healed)
                grew = True
        if not grew:
            break

    # `roster` rides inside `confirmed`, so a name this file proved is a person
    # is player-like here no matter what the rest of the evidence says
    def player_like(name: str) -> bool:
        return name in confirmed or name in heal_involved or name in hits_mobs

    mobs = {v for v in kill_victims if not player_like(v)}
    for name, targets in hit_players.items():
        if player_like(name) or name in mobs:
            continue
        if (len(targets & confirmed) >= MIN_PLAYERS_HIT
                and len(hit_by.get(name, set()) & confirmed) >= MIN_PLAYER_ATTACKERS):
            mobs.add(name)
    return frozenset(mobs)
