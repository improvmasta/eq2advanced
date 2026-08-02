"""Ordered line classifier. First match wins; order follows the verified spec.

`parse_lines()` is the single entry point for BOTH bulk file parsing and live
ingest batches — identical behavior by construction.
"""

import re
from collections.abc import Iterable, Iterator

from .events import (
    F_AUTOATTACK,
    F_BLEED,
    F_CRIT,
    F_MULTI,
    F_SELF_FOCUS,
    F_ZERO,
    ParsedEvent,
    Subject,
)
from .flavor import resolve as resolve_flavor
from .prefix import split_prefix, to_int, unescape_items
from .subjects import decompose

DTYPES = "crushing|slashing|piercing|disease|poison|mental|cold|heat|divine|magic|focus"

RE_ZONE = re.compile(r"^You have entered\s+(.+)\.$")
RE_KILL_YOU = re.compile(r"^You have killed (.+)\.$")
RE_KILL = re.compile(r"^(.+?) has killed (.+)\.$")
RE_ALAS = re.compile(r"^Alas, (.+) has died from pain and suffering\.$")
RE_REZ = re.compile(r"^(.+?) petitions the divinities of resurrection\.$")
RE_REVIVE = re.compile(r"^You regain consciousness!$")
RE_DAMAGE = re.compile(
    rf"^(?P<subj>.+?) (?P<verb>hits|hit|multi attacks|multi attack|aoe attacks|aoe attack|flurries|flurry) (?P<tgt>.+?) "
    rf"for (?P<crit>a critical of )?(?P<amt>[\d,.]+K?) (?P<dtype>{DTYPES})"
    rf"(?: and (?P<amt2>[\d,.]+K?) (?P<dtype2>{DTYPES}))? damage\.$"
)
RE_HIT_PASSIVE = re.compile(
    rf"^(?P<tgt>.+?) is hit for (?P<crit>a critical of )?(?P<amt>[\d,.]+K?) (?P<dtype>{DTYPES}) damage\.$"
)
RE_ZERO_DMG = re.compile(
    r"^(?P<subj>.+?) (?:hits|hit|multi attacks|multi attack|aoe attacks|aoe attack|flurries|flurry) "
    r"(?P<tgt>.+?) but fails to inflict any damage\.$"
)
RE_AVOID = re.compile(
    r"^(?P<subj>.+?) tr(?:y|ies) to (?P<verb>\w+) (?P<tgt>.+?)(?: with (?P<ability>.+?))?"
    r", but (?:(?P<avoider>.+?) )?(?P<how>miss(?:es)?|parries|blocks|ripostes|"
    r"dodges(?: the multi attack)?|resists?|reflects)\.$"
)
RE_HEAL = re.compile(
    r"^(?P<subj>.+?) heals (?P<tgt>.+?) for (?P<crit>a critical of )?(?P<amt>[\d,.]+K?) hit points\.$"
)
RE_WARD = re.compile(
    r"^(?P<subj>.+?) absorbs (?P<amt>[\d,.]+K?) points of damage from being done to (?P<tgt>.+?)"
    r"(?: with (?P<bleed>[\d,]+) points of damage bleeding through)?\. \((?P<remain>[\d,]+) points remaining\)$"
)
RE_WARD_REGEN = re.compile(r"^(?P<subj>.+?) regenerates (?P<amt>[\d,]+) points of absorption\.$")
RE_POWER = re.compile(
    r"^(?P<subj>.+?) refreshes (?P<tgt>.+?) for (?P<crit>a critical of )?(?P<amt>[\d,.]+K?) mana points\.$"
)
RE_DRAIN = re.compile(r"^(?P<subj>.+?) confounds (?P<tgt>.+?) draining (?P<amt>[\d,]+) points of power\.$")
RE_THREAT = re.compile(
    r"^(?P<subj>.+?) (?P<dir>reduces|increases) (?:YOUR|THEIR) hate with (?P<mob>.+?) "
    r"for (?P<crit>a critical of )?(?P<amt>[\d,.]+K?) threat\.$"
)
RE_DISPEL = re.compile(r"^(?P<subj>.+?) dispels (?P<effect>.+?) from (?P<tgt>.+?)\.$")
RE_AFFLICT = re.compile(r"^(?P<subj>.+) afflicts you\.$")
RE_EXPIRE_EFFECTS = re.compile(r"^(?P<effect>.+?) no longer effects (?P<tgt>.+)\.$")  # sic
RE_EXPIRE_FADES = re.compile(r"^(?P<effect>.+?) fades away\.$")
RE_EXPIRE_OVER = re.compile(r"^(?P<effect>.+?) is over\.$")
RE_INTERRUPT = re.compile(r"^(?P<tgt>.+?) was interrupted!$")
RE_ANON_HEAL = re.compile(r"^A healing spell is cast on (?P<tgt>.+)\.$")
RE_PREPARE = re.compile(r"^You prepare (?P<what>.+?)\.?$")

_CHAT_PREFIXES = ("\\aPC ", "\\aNPC ")
_CHAT_RE = re.compile(r'^You (?:say|tell) ')


def _split_possessive_head(text: str) -> tuple[str, str] | None:
    """Split at the FIRST possessive token: "Malkonis D'Morte's exposed noxious"
    -> ("Malkonis D'Morte", "exposed noxious"). None if no possessive."""
    tokens = text.split(" ")
    for i, tok in enumerate(tokens):
        if tok.endswith("'s"):
            head = " ".join(tokens[: i + 1])[:-2]
        elif tok.endswith("'") and len(tok) > 1:
            head = " ".join(tokens[: i + 1])[:-1]
        else:
            continue
        rest = " ".join(tokens[i + 1 :])
        if rest:
            return head, rest
        return None
    return None


def classify_body(ts: int, body: str, logger: str) -> ParsedEvent | None:
    """Classify one prefix-stripped body. Returns None for chat/unknown lines."""
    if body.startswith(_CHAT_PREFIXES) or _CHAT_RE.match(body):
        return None
    if "\\aITEM" in body:
        body = unescape_items(body)

    if m := RE_ZONE.match(body):
        return ParsedEvent(ts, "zone", extra={"zone": m.group(1).strip()})

    if m := RE_KILL_YOU.match(body):
        return ParsedEvent(ts, "kill", src=Subject(logger, "player"), tgt=m.group(1))
    if m := RE_KILL.match(body):
        if m.group(2) == "you":
            # "<Killer> has killed you." — the logger's own death IS logged
            return ParsedEvent(ts, "death", src=Subject(m.group(1), "unknown"), tgt="YOU")
        return ParsedEvent(ts, "kill", src=Subject(m.group(1), "unknown"), tgt=m.group(2))

    if m := RE_ALAS.match(body):
        who = m.group(1)
        split = _split_possessive_head(who)
        if split:
            owner, pet = split
            return ParsedEvent(ts, "pet_death", src=Subject(owner, "unknown"), tgt=pet)
        return ParsedEvent(ts, "death", tgt=who)
    if m := RE_REZ.match(body):
        return ParsedEvent(ts, "rez", src=Subject(m.group(1), "unknown"))
    if RE_REVIVE.match(body):
        return ParsedEvent(ts, "revive", tgt="YOU")

    if m := RE_DAMAGE.match(body):
        subj, ability = decompose(m.group("subj"), logger)
        flags = 0
        if m.group("crit"):
            flags |= F_CRIT
        if ability is None:
            flags |= F_AUTOATTACK
        if m.group("verb").startswith(("multi", "aoe", "flurr")):
            flags |= F_MULTI
        dtype = m.group("dtype")
        if dtype == "focus":
            flags |= F_SELF_FOCUS
        amount = to_int(m.group("amt"))
        extra = {}
        if m.group("amt2"):
            # dual-type hit ("7,896 crushing and 556 disease") — weapon + proc in
            # one line; total credited, components kept
            amt2 = to_int(m.group("amt2"))
            extra["components"] = [[amount, dtype], [amt2, m.group("dtype2")]]
            amount += amt2
        return ParsedEvent(
            ts, "damage", src=subj, tgt=m.group("tgt"),
            ability=ability, amount=amount, dtype=dtype, flags=flags, extra=extra,
        )
    if m := RE_HIT_PASSIVE.match(body):
        # sourceless passive hit ("X is hit for N ... damage") — damage shields,
        # traps, environment; attribution unknown
        flags = F_CRIT if m.group("crit") else 0
        return ParsedEvent(
            ts, "damage", src=None, tgt=m.group("tgt"),
            amount=to_int(m.group("amt")), dtype=m.group("dtype"), flags=flags,
        )
    if m := RE_ZERO_DMG.match(body):
        subj, ability = decompose(m.group("subj"), logger)
        flags = F_ZERO | (F_AUTOATTACK if ability is None else 0)
        return ParsedEvent(
            ts, "damage", src=subj, tgt=m.group("tgt"),
            ability=ability, amount=0, flags=flags,
        )

    if m := RE_AVOID.match(body):
        subj, _ = decompose(m.group("subj"), logger)
        ability = m.group("ability")
        how = m.group("how")
        kind = (
            "miss" if how.startswith("miss")
            else "dodge" if how.startswith("dodges")
            else "resist" if how.startswith("resist")
            else how.rstrip("s") if how in ("parries",)
            else {"parries": "parry", "blocks": "block", "ripostes": "riposte", "reflects": "reflect"}.get(how, how)
        )
        return ParsedEvent(
            ts, "avoid", src=subj, tgt=m.group("tgt"),
            ability=ability,
            flags=0 if ability else F_AUTOATTACK,
            extra={"how": kind, "avoider": m.group("avoider") or None},
        )

    if m := RE_HEAL.match(body):
        subj, ability = decompose(m.group("subj"), logger)
        flags = F_CRIT if m.group("crit") else 0
        return ParsedEvent(
            ts, "heal", src=subj, tgt=m.group("tgt"),
            ability=ability, amount=to_int(m.group("amt")), flags=flags,
        )

    if m := RE_WARD.match(body):
        subj, ability = decompose(m.group("subj"), logger)
        flags = F_BLEED if m.group("bleed") else 0
        return ParsedEvent(
            ts, "ward", src=subj, tgt=m.group("tgt"),
            ability=ability, amount=to_int(m.group("amt")), flags=flags,
            extra={
                "remaining": to_int(m.group("remain")),
                **({"bleed": to_int(m.group("bleed"))} if m.group("bleed") else {}),
            },
        )
    if m := RE_WARD_REGEN.match(body):
        subj, ability = decompose(m.group("subj"), logger)
        return ParsedEvent(ts, "ward_regen", src=subj, ability=ability, amount=to_int(m.group("amt")))

    if m := RE_POWER.match(body):
        subj, ability = decompose(m.group("subj"), logger)
        flags = F_CRIT if m.group("crit") else 0
        return ParsedEvent(
            ts, "power", src=subj, tgt=m.group("tgt"),
            ability=ability, amount=to_int(m.group("amt")), flags=flags,
        )
    if m := RE_DRAIN.match(body):
        subj, ability = decompose(m.group("subj"), logger)
        return ParsedEvent(
            ts, "power_drain", src=subj, tgt=m.group("tgt"),
            ability=ability, amount=to_int(m.group("amt")),
        )

    if m := RE_THREAT.match(body):
        subj, ability = decompose(m.group("subj"), logger)
        flags = F_CRIT if m.group("crit") else 0
        return ParsedEvent(
            ts, "threat", src=subj, tgt=m.group("mob"), ability=ability,
            amount=to_int(m.group("amt")) * (1 if m.group("dir") == "increases" else -1),
            flags=flags,
        )

    if m := RE_DISPEL.match(body):
        subj, ability = decompose(m.group("subj"), logger)
        return ParsedEvent(
            ts, "dispel", src=subj, tgt=m.group("tgt"),
            ability=ability, extra={"effect": m.group("effect")},
        )

    if m := RE_AFFLICT.match(body):
        split = _split_possessive_head(m.group("subj"))
        if split:
            owner, effect = split
            return ParsedEvent(ts, "affliction", src=Subject(owner, "unknown"),
                               tgt=logger, extra={"effect": effect})
        return ParsedEvent(ts, "affliction", tgt="YOU", extra={"effect": m.group("subj")})

    if m := RE_EXPIRE_EFFECTS.match(body):
        return ParsedEvent(ts, "expiry", tgt=m.group("tgt"), extra={"effect": m.group("effect")})
    if m := RE_EXPIRE_FADES.match(body):
        return ParsedEvent(ts, "expiry", extra={"effect": m.group("effect")})
    if m := RE_EXPIRE_OVER.match(body):
        return ParsedEvent(ts, "expiry", extra={"effect": m.group("effect")})

    if m := RE_INTERRUPT.match(body):
        return ParsedEvent(ts, "interrupt", tgt=m.group("tgt"))

    if m := RE_ANON_HEAL.match(body):
        # no caster, no amount — never counts toward HPS
        return ParsedEvent(ts, "anon_heal", tgt=m.group("tgt"))

    if m := RE_PREPARE.match(body):
        # flavor text, NOT an ability name ("to rot a soul" -> Soulrot)
        what = m.group("what")
        return ParsedEvent(ts, "cast_flavor", src=Subject(logger, "player"),
                           ability=resolve_flavor(what), extra={"flavor": what})

    return None


def parse_lines(lines: Iterable[str], logger: str) -> Iterator[ParsedEvent]:
    """Parse raw log lines (with prefixes) into events. Shared by bulk uploads
    and live ingest batches."""
    # the client sometimes logs a prepare line twice in the same second (exact
    # duplicate, per-spell — 234 of 918 in bobby.txt); a real same-second
    # re-prepare of the same spell can't happen, so collapse to one cast
    last_flavor: tuple[int, str] | None = None
    for line in lines:
        parts = split_prefix(line)
        if parts is None:
            continue
        ts, body = parts
        ev = classify_body(ts, body, logger)
        if ev is None:
            continue
        if ev.type == "cast_flavor":
            key = (ts, ev.extra["flavor"])
            if key == last_flavor:
                continue
            last_flavor = key
        yield ev
