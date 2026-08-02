"""Parsed-event dataclass emitted by the classifier. Pure data, no DB."""

from dataclasses import dataclass, field


# flags bitmask
F_CRIT = 1
F_AUTOATTACK = 2
F_MULTI = 4
F_SELF_FOCUS = 8      # `focus` dtype: self-inflicted (e.g. Vampiric Requiem) — excluded from DPS
F_BLEED = 16          # ward absorb with bleedthrough
F_ZERO = 32           # "hits X but fails to inflict any damage"


@dataclass(slots=True)
class Subject:
    """A resolved actor reference.

    name:  credited actor ("Bobby", "a marrow ripper", "Aros")
    unit:  'player' | 'own_pet' | 'swarm_pet' | 'unknown'
    pet:   swarm/named pet label when unit is a pet with its own name
    """
    name: str
    unit: str = "unknown"
    pet: str | None = None


@dataclass(slots=True)
class ParsedEvent:
    ts: int
    type: str                       # damage|heal|ward|power|threat|dispel|affliction|expiry|
                                    # kill|death|pet_death|rez|interrupt|cast_flavor|zone|other
    src: Subject | None = None
    tgt: str | None = None          # raw target name (YOU/YOURSELF already mapped by subjects)
    ability: str | None = None
    amount: int | None = None
    dtype: str | None = None
    flags: int = 0
    extra: dict = field(default_factory=dict)
