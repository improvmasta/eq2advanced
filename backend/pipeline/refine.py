"""Behavioral entity refinement. A single-token capitalized name ("Venekor")
is grammatically indistinguishable from a player, so `classify_entity_kind`
defaults it to player — which puts one-word bosses in the raider table and
mislabels their kills as trash. This pass reads the whole parsed event stream
and reclassifies such names as mobs when their BEHAVIOR proves it:

- they are the victim of a player-credited kill line, or
- they trade damage with the raid (hit >= 2 confirmed players AND are hit by
  >= 3 confirmed players) while never appearing on either side of a heal.

"Confirmed players" are names with player-only behavior: the logger, heal /
ward / power / rez sources, and swarm/named-pet owners. Anyone who damages an
articled mob ("a bloodgorger") is also player-like — that guard keeps a
mind-controlled raider (a player-shaped kill victim) from reclassifying.
Pure function over ParsedEvents; runs before entity resolution and
segmentation.
"""

from collections import defaultdict

from parser.events import ParsedEvent

MIN_PLAYERS_HIT = 2
MIN_PLAYER_ATTACKERS = 3


def _single_token_cap(name: str | None) -> bool:
    return (bool(name) and " " not in name and name[:1].isupper()
            and not name.lower().startswith(("a ", "an ", "the ")))


def refine_known_mobs(events: list[ParsedEvent], logger: str) -> frozenset[str]:
    confirmed: set[str] = {logger}
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
                confirmed.add(src.name)
            if ev.type in ("heal", "ward", "power", "rez") and _single_token_cap(src.name):
                confirmed.add(src.name)
                heal_involved.add(src.name)
        if ev.type == "heal" and ev.tgt and _single_token_cap(ev.tgt):
            heal_involved.add(ev.tgt)
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
