"""Subject decomposition — the crux of EQ2 log attribution.

Verified model (bobby.txt spec):
- `YOU` / `YOUR <Ability>`        -> the logging player (their own actions ALWAYS
                                     use this form)
- bare `<logger name>`            -> the logger's own pet (pets can share the
- `<logger name>'s <Ability>`        owner's exact name)
- `<Name>` / `<Name>'s <Ability>` -> another actor; single-token capitalized names
                                     are players (their pet conflates — community
                                     convention), articled/multi-word names are mobs
- `<Owner>'s <lowercase pet>['s <Ability>]` -> swarm/dumbfire pet chain
- Possessive hazard: ability possessive on s-ending names is a bare apostrophe
  (`Aros' Soulrot`) but pet-owner possessive is `'s` (`Aros's blighted horde`);
  names and abilities may contain internal apostrophes (`D'Kulvith`, `Autumn's
  Kiss`) — never split inside a capitalized remainder.
"""

from .events import Subject

_ARTICLES = ("a ", "an ", "the ")


def _strip_possessive(token: str) -> str | None:
    """Return the token without its possessive marker, or None if not possessive."""
    if token.endswith("'s"):
        return token[:-2]
    if token.endswith("'") and len(token) > 1:
        return token[:-1]
    return None


def _first_possessive(tokens: list[str]) -> int | None:
    for i, tok in enumerate(tokens):
        if _strip_possessive(tok) is not None:
            return i
    return None


def _leads_lowercase(text: str) -> bool:
    return bool(text) and text[0].islower()


def _has_article(name: str) -> bool:
    return name.lower().startswith(_ARTICLES)


def decompose(subj: str, logger: str,
              pet_names: frozenset[str] = frozenset()) -> tuple[Subject, str | None]:
    """Split a source expression into (Subject, ability-or-None). `pet_names`
    is the named-pet knowledge base (parser.petnames) — capitalized possessive
    remainders are abilities UNLESS known to be pets."""
    if subj == "YOU":
        return Subject(logger, "player"), None
    if subj.startswith("YOUR "):
        return Subject(logger, "player"), subj[5:]

    tokens = subj.split(" ")
    pi = _first_possessive(tokens)
    if pi is None:
        # bare name: logger's pet, or another actor's conflated player/pet
        if subj == logger:
            return Subject(logger, "own_pet"), None
        return Subject(subj, "unknown"), None

    owner = " ".join(tokens[: pi + 1])
    owner = owner[: len(owner) - (len(tokens[pi]) - len(_strip_possessive(tokens[pi])))]
    remainder = " ".join(tokens[pi + 1 :])
    if not remainder:
        return Subject(owner, "unknown"), None

    if _has_article(owner):
        # mob possessive: remainder is the mob's ability, whatever its case
        # ("A shard of Garanel's grave sacrament")
        return Subject(owner, "unknown"), remainder

    if _leads_lowercase(remainder):
        # swarm-pet chain: `<Owner>'s <lowercase pet>['s <Ability>]`
        rtokens = remainder.split(" ")
        rpi = _first_possessive(rtokens)
        if rpi is None:
            return Subject(owner, "swarm_pet", pet=remainder), None
        pet = " ".join(rtokens[: rpi + 1])
        pet = pet[: len(pet) - (len(rtokens[rpi]) - len(_strip_possessive(rtokens[rpi])))]
        ability = " ".join(rtokens[rpi + 1 :]) or None
        return Subject(owner, "swarm_pet", pet=pet), ability

    # capitalized remainder: a known named pet (whole, or its own chain head)
    # beats the ability reading — "Ellea's Lunar Attendant['s Oracle's
    # Blessing]" is the pet acting, not an Ellea ability
    if pet_names:
        if remainder in pet_names:
            return Subject(owner, "named_pet", pet=remainder), None
        rtokens = remainder.split(" ")
        rpi = _first_possessive(rtokens)
        if rpi is not None and rpi < len(rtokens) - 1:
            pet = " ".join(rtokens[: rpi + 1])
            pet = pet[: len(pet) - (len(rtokens[rpi]) - len(_strip_possessive(rtokens[rpi])))]
            if pet in pet_names:
                return Subject(owner, "named_pet", pet=pet), " ".join(rtokens[rpi + 1:])

    # otherwise it's an ability; keep internal possessives intact
    # ("Autumn's Kiss", "Daro's Dull Blade")
    unit = "own_pet" if owner == logger else "unknown"
    return Subject(owner, unit), remainder


def resolve_target(tgt: str, logger: str) -> str:
    if tgt in ("YOU", "YOURSELF"):
        return logger
    return tgt


def classify_entity_kind(name: str, unit: str, logger: str,
                         known_mobs: frozenset[str] = frozenset()) -> str:
    """Best-effort kind for an entity row. `known_mobs` carries the behavioral
    refinement pass (pipeline.refine) — single-token capitalized names that
    provably fought the raid ("Venekor") override the player default."""
    if unit == "player":
        return "player"
    if unit in ("own_pet", "swarm_pet", "named_pet"):
        return unit
    if _has_article(name):
        return "mob"
    if name in known_mobs:
        return "mob"
    if name == logger:
        return "own_pet"
    if " " in name:
        return "mob"          # multi-word capitalized => named mob
    return "player"           # single-token capitalized => another player (pet conflated)
