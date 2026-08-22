"""Canonical public-character identity for Planner-owned browser/account data.

The key files a reader's plans; it is never evidence that the reader owns the
EQ2 character.  Census ids and provider-specific display labels are deliberately
not part of it, so a Lexicon fallback and a later Census answer land in the same
folder.
"""

from __future__ import annotations

import re
import unicodedata

DEFAULT_WORLD = "Wuoshi"
MAX_KEY = 160
MAX_LOOKUP_NAME = 40

_DISPLAY_WORLD = re.compile(r"\s*\(([^()]*)\)\s*$")


def lookup_name(value: str | None, world: str | None = None) -> str:
    """Return the round-trippable character name, not a display label."""
    clean = " ".join(str(value or "").split()).strip()
    match = _DISPLAY_WORLD.search(clean)
    if match and (not world or match.group(1).casefold() == str(world).casefold()):
        clean = clean[:match.start()].rstrip()
    return clean[:MAX_LOOKUP_NAME]


def _key_part(value: str | None) -> str:
    clean = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(clean.split()).strip().casefold()


def planner_key(world: str | None, name: str | None) -> str:
    clean_world = _key_part(world or DEFAULT_WORLD)
    clean_name = _key_part(lookup_name(name, world or DEFAULT_WORLD))
    if not clean_world or not clean_name:
        raise ValueError("Planner character world and lookup name are required")
    key = f"{clean_world}:{clean_name}"
    if len(key) > MAX_KEY or any(ord(char) < 32 for char in key):
        raise ValueError("Planner character key is invalid")
    return key


def validate_planner_key(value: str | None) -> str:
    clean = _key_part(value)
    if ":" not in clean:
        raise ValueError("Planner character key must be world:name")
    world, name = clean.split(":", 1)
    expected = planner_key(world, name)
    if clean != expected:
        raise ValueError("Planner character key is not canonical")
    return clean


def character_fields(doc: dict, character: dict) -> dict:
    """The three separate identity facts every Planner summary exposes."""
    world = str(character.get("world") or DEFAULT_WORLD)
    doc_name = (doc.get("name") or {}).get("first")
    lookup = lookup_name(doc_name or character.get("lookup_name")
                         or character.get("name"), world)
    display = " ".join(str(doc.get("displayname")
                               or character.get("display_name")
                               or f"{lookup} ({world})").split()).strip()
    return {
        "planner_key": planner_key(world, lookup),
        "lookup_name": lookup,
        "display_name": display,
    }
