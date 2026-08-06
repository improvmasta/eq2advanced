"""Curated buff lines — the few abilities whose APPLY the log actually prints.

A beneficial buff is nearly invisible in an EQ2 log. It deals no damage, heals
nothing, and never prints its own name; there is no "X gains Jester's Cap" and
no fade line. What a handful of abilities DO print is flavor text, twice:

    (…) Vestigial begins to play the song of the Jester.     <- the cast
    (…) The Jester inspires Rorschach.                       <- where it landed

Both lines are written for everyone in chat range, not just the logger — which
is the whole reason this file can exist. It is the only place in the parser
where another player's cast is visible at all (`You prepare …` is the logger's
own, and nothing else). That makes buff uptime computable from ANY raider's
upload, not only the buffer's own.

CURATED, deliberately. There is a generic third-person grammar — `<Name>
begins <flavor>.` covers 822 `Tasrin begins a phantasmal enchantment.` lines in
one raid — but the flavor is shared across a whole ability line ("an
augmentation song" is every troubador group buff), and the first person form
is not even always a spell: `You begin to breathe normally.`, `You begin to
move faster!`, `You begin to choke!`. A line only earns an entry here when its
flavor identifies ONE ability and, ideally, its landing line names the target.

What the log still will not say, and the metrics have to state:
  - no fade line exists, so a buff's END is inferred from Census duration;
  - a cast outside chat range is not logged at all, so counts are floors.

Adding one is four regexes and a Census name. Keep the `token` — it is a
substring gate so the regexes never run on the millions of lines that cannot
match.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BuffLine:
    ability: str        # the Census spell name, so a metric can join durations
    cls: str            # who casts it
    token: str          # cheap substring gate, checked before any regex
    cast_self: re.Pattern       # the logger casting it
    cast_other: re.Pattern      # somebody else casting it -> group 'who'
    land_self: re.Pattern       # it landing on the logger
    land_other: re.Pattern      # it landing on somebody else -> group 'tgt'


BUFFS: tuple[BuffLine, ...] = (
    # Jester's Cap (troubador, L65): +Reuse Speed on ONE target, 30s duration
    # on a 30s recast (25s with the Enhance AA), so its uptime is a measure of
    # chain discipline rather than of choosing when to press it. Line counts
    # from the 2026-08-03 raid: 782 self casts, 48 by the raid's other
    # troubador, 820 landings across 12 different targets.
    BuffLine(
        ability="Jester's Cap", cls="troubador", token="Jester",
        cast_self=re.compile(r"^You begin to play the song of the Jester\.$"),
        cast_other=re.compile(r"^(?P<who>.+?) begins to play the song of the Jester\.$"),
        land_self=re.compile(r"^You feel inspired by the Jester\.$"),
        land_other=re.compile(r"^The Jester inspires (?P<tgt>.+?)\.$"),
    ),
)


def match(body: str) -> tuple[str, str, str | None] | None:
    """Classify one prefix-stripped body as a curated buff line.

    -> (kind, ability, who) where kind is 'cast' or 'land' and `who` is the
    caster/target, or None when the line is about the logger ("You begin…",
    "You feel…"). None when the line is not a buff line at all.
    """
    for buff in BUFFS:
        if buff.token not in body:
            continue
        if buff.cast_self.match(body):
            return "cast", buff.ability, None
        if m := buff.cast_other.match(body):
            return "cast", buff.ability, m.group("who")
        if buff.land_self.match(body):
            return "land", buff.ability, None
        if m := buff.land_other.match(body):
            return "land", buff.ability, m.group("tgt")
    return None
