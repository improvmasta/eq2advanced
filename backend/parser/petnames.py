"""Named-pet knowledge base. Grammar alone cannot split `Ellea's Lunar
Attendant` (a summoned pet) from `Aros' Soulrot` or `Banjeaux's Daro's Dull
Blade` (abilities with internal possessives) — bobby.txt is full of both. Only
names KNOWN to be pets are treated as pets:

- curated: summoned entities verified in real logs (they act with their own
  ability chains: `<Owner>'s <Pet>'s <Ability>`)
- observed: learned from hard evidence — `Alas, <Owner>'s <Capitalized> has
  died from pain and suffering.` where the owner is player-shaped. Persisted
  in the global `pet_names` table, so every later parse (and reparse of older
  sessions) starts with everything learned so far.

The kill-victim guard rejects named mob adds: `Garanel's Shade` dies to a
player kill line, a friendly pet never does.
"""

import re
import sqlite3

from .prefix import split_prefix

# Summoned entities seen acting via their own possessive chains in bobby.txt.
CURATED_PET_NAMES = (
    "Lunar Attendant",       # healer deity pet, casts Oracle's Blessing
    "Protecting Grove",      # fury summoned grove, casts Grove Healing
)

_RE_ALAS = re.compile(r"^Alas, (.+) has died from pain and suffering\.$")
_RE_KILL = re.compile(r"^(.+?) has killed (.+)\.$")
_RE_KILL_YOU = re.compile(r"^You have killed (.+)\.$")


def _possessive_split(text: str) -> tuple[str, str] | None:
    """("Ellea's Lunar Attendant") -> ("Ellea", "Lunar Attendant"); None if no
    possessive token before the end."""
    tokens = text.split(" ")
    for i, tok in enumerate(tokens[:-1]):
        if tok.endswith("'s"):
            return " ".join(tokens[: i + 1])[:-2], " ".join(tokens[i + 1:])
        if tok.endswith("'") and len(tok) > 1:
            return " ".join(tokens[: i + 1])[:-1], " ".join(tokens[i + 1:])
    return None


def _player_shaped(name: str) -> bool:
    return (bool(name) and " " not in name and name[:1].isupper()
            and not name.lower().startswith(("a ", "an ", "the ")))


def prescan(lines, logger: str) -> dict[str, str]:
    """Scan raw log lines for named-pet death evidence. Returns
    {pet_name: owner} for every `Alas, <player-shaped Owner>'s <Capitalized>`
    death whose full possessive form was never a player kill victim (a named
    add like `Garanel's Shade` is killed BY players; a friendly pet is not)."""
    candidates: dict[str, str] = {}
    killed: set[str] = set()
    for line in lines:
        parts = split_prefix(line)
        if parts is None:
            continue
        body = parts[1]
        if m := _RE_ALAS.match(body):
            split = _possessive_split(m.group(1))
            if split:
                owner, pet = split
                if _player_shaped(owner) and pet[:1].isupper():
                    candidates[pet] = owner
        elif m := _RE_KILL_YOU.match(body):
            killed.add(m.group(1))
        elif m := _RE_KILL.match(body):
            if _player_shaped(m.group(1)) or m.group(1) == logger:
                killed.add(m.group(2))
    return {
        pet: owner for pet, owner in candidates.items()
        if f"{owner}'s {pet}" not in killed and f"{owner}' {pet}" not in killed
    }


def seed_curated(conn: sqlite3.Connection) -> None:
    """Idempotent; called at startup beside catalog.seed_curated."""
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO pet_names (name, source) VALUES (?, 'curated')",
            [(n,) for n in CURATED_PET_NAMES])


def load(conn: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        {r[0] for r in conn.execute("SELECT name FROM pet_names")}
        | set(CURATED_PET_NAMES))


def learn(conn: sqlite3.Connection, observed: dict[str, str], session_id: int) -> None:
    """Persist prescan evidence. Runs inside the caller's transaction."""
    if observed:
        conn.executemany(
            "INSERT INTO pet_names (name, source, owner_hint, first_seen_session) "
            "VALUES (?, 'observed', ?, ?) ON CONFLICT(name) DO NOTHING",
            [(pet, owner, session_id) for pet, owner in observed.items()])
