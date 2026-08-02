"""Flavor-text -> ability-name resolution for `You prepare ...` lines.

The prepare line is the ONLY cast-start record in an EQ2 log, and it prints
flavor text, not the ability name. Two generic grammars cover most of it:

- `You prepare the|a|an <Ability>.` — the ability name follows the article,
  capitalized ("the Bloodcloud" -> Bloodcloud). A lowercase continuation is
  prose, not a name ("to awaken the grave" must NOT strip to "grave").
- `You prepare to inflict <Ability> on <target>.` — targeted inflict form.

Everything else ("to rot a soul") needs a per-class curated map; unmapped
flavor stays unresolved (ability None) and still counts as a cast with
default cast times. Maps merge globally — flavor strings are distinctive
enough that cross-class collisions don't arise, and a collision would still
name the same ability line.
"""

import re

from .necromancer import FLAVOR_MAP as _NECROMANCER

_ARTICLE = re.compile(r"^(?:the|a|an) (?=[A-Z])")
_INFLICT = re.compile(r"^to inflict ([A-Z].*?) on .+$")

FLAVOR_MAP: dict[str, str] = {}
for _map in (_NECROMANCER,):
    FLAVOR_MAP.update(_map)


def resolve(flavor: str) -> str | None:
    """'to rot a soul' -> 'Soulrot'; 'the Bloodcloud' -> 'Bloodcloud';
    'to inflict Fear on Zylphax the Shredder' -> 'Fear'; unknown -> None."""
    hit = FLAVOR_MAP.get(flavor)
    if hit:
        return hit
    if m := _INFLICT.match(flavor):
        return m.group(1)
    stripped = _ARTICLE.sub("", flavor)
    if stripped != flavor:
        return stripped
    return None
