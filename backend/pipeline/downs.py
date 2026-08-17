"""The logger's UNANNOUNCED deaths.

EQ2 tells you somebody died in one of two ways, and neither one covers the
logger dying with nobody to blame:

- `<Killer> has killed you.` needs a killer to credit. A necromancer's Lifeburn
  leaves them at 1 HP and their own choker proc finishes the job, so there is
  nothing to name.
- `Alas, <name> has died from pain and suffering.` is a broadcast ABOUT OTHER
  PEOPLE. Zero `Alas, Bobby` in Bobby's logs, zero `Alas, Oktavia` in
  Oktavia's — the game never uses that form for the logger themself.

`You lose consciousness!` is not the third case: it means INCAPACITATED, and a
heal that beats the timer undoes it (see `parser.classify` RE_KO). So a
killer-less death of the logger's leaves NO LINE AT ALL.

What it does leave is the shape of a corpse. Measured on session 301, the raid
of 2026-08-16: the logger stops acting on the last tick of a Lifeburn channel,
NOTHING lands on them for the whole hole — no damage, no heal, no ward, the
same signature as their thirteen killer-credited deaths that night — and the
hole ends with `You regain consciousness!`. Two of them, 27s on Mayong's
killing pull and 20s on Malkonis D'Morte, each recorded as zero deaths, zero
time dead, and a fight the raider was "active" for all but 3 seconds of.

So an unpaired revive IS a death, dated to the last moment the log can prove
they were alive. The rest is why the two obvious ways to get this wrong are not
in here:

- **The floor is `MIN_DOWN_S`, because a heal can beat the death timer.** An
  incapacitation that gets healed is a ONE-SECOND hole — both measured ones
  are (Bronir 14:13:28→29, Bobby 22:11:16→17) — and calling that a death is
  exactly the conflation this module exists to avoid.
- **A logged death is never re-counted.** A death the log DID announce claims
  the next revive, and `outstanding` is deliberately not cleared by the logger
  acting again: a DoT of theirs ticking on a corpse would otherwise unpair a
  real death and invent a second one on top of it. The cost is a missed
  inference when a logged death's revive never prints (they zoned out and
  self-revived instead), which is the direction to be wrong in.
"""

from parser.events import F_INFERRED, ParsedEvent
from pipeline.statsroll import ACTION_TYPES

# Shortest hole that is a death rather than a heal beating the timer.
MIN_DOWN_S = 5

# How far back a revive may date its own death. A hole longer than this is not
# a fight to attribute a death inside; it also bounds the search below.
MAX_DOWN_S = 600

# What lands ON somebody — proof they were still a target, so still alive.
_INCOMING_TYPES = frozenset(("damage", "heal", "ward"))


def _alive(ev, logger: str) -> bool:
    """Does this event prove the logger was up? Their own action, or anything
    landing on them. Their PETS are not proof — a swarm keeps swinging over the
    corpse, and a swarm tick is the logger's row in every other rollup."""
    return ((ev.type in ACTION_TYPES and ev.src is not None
             and ev.src.unit == "player" and ev.src.name == logger)
            or (ev.type in _INCOMING_TYPES and ev.tgt == "YOU"))


def _last_alive(events: list[ParsedEvent], before: int, logger: str,
                ts: int) -> tuple[int, int] | None:
    """Walk back from `before` for the last proof of life STRICTLY EARLIER than
    `ts`. Strictly, because the heal that brings somebody back shares its
    second with the revive line and the two can arrive in either order — read
    forwards, that heal would erase the hole it ends."""
    for j in range(before - 1, -1, -1):
        ev = events[j]
        if ts - ev.ts > MAX_DOWN_S:
            return None
        if ev.ts < ts and _alive(ev, logger):
            return j, ev.ts
    return None


def infer_logger_deaths(events: list[ParsedEvent], logger: str,
                        min_down_s: int = MIN_DOWN_S) -> list[ParsedEvent]:
    """Insert a flagged `death` for every unpaired "You regain consciousness!".

    Idempotent, because the death it inserts is itself the pairing evidence:
    the live path re-runs this over a `pending` list it has already seen on
    every flush and must not invent a second death for one revive.
    """
    inserts: list[tuple[int, ParsedEvent]] = []
    outstanding = False                    # a logged death, not yet revived

    for i, ev in enumerate(events):
        if ev.tgt != "YOU":
            continue
        if ev.type == "death":
            outstanding = True
        elif ev.type == "revive":
            if outstanding:
                outstanding = False
                continue
            found = _last_alive(events, i, logger, ev.ts)
            if found is not None and ev.ts - found[1] >= min_down_s:
                inserts.append((found[0] + 1, ParsedEvent(
                    ts=found[1], type="death", tgt="YOU", flags=F_INFERRED,
                    extra={"inferred": "revive"})))

    if not inserts:
        return events
    out = list(events)
    for at, ev in reversed(inserts):      # last first, so earlier indices hold
        out.insert(at, ev)
    return out
