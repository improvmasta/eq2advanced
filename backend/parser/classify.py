"""Ordered line classifier. First match wins; order follows the verified spec.

`parse_lines()` is the single entry point for BOTH bulk file parsing and live
ingest batches — identical behavior by construction.
"""

import re
from collections.abc import Iterable, Iterator

from .events import (
    F_AOE,
    F_AUTOATTACK,
    F_BLEED,
    F_CRIT,
    F_FLURRY,
    F_MULTI,
    F_SELF_FOCUS,
    F_ZERO,
    ParsedEvent,
    Subject,
)
from . import buffs
from .flavor import resolve as resolve_flavor
from .prefix import split_prefix, to_int, unescape_items
from .subjects import decompose

DTYPES = "crushing|slashing|piercing|disease|poison|mental|cold|heat|divine|magic|focus"

RE_ZONE = re.compile(r"^You have entered\s+(.+)\.$")
# `/act end` typed in game. EQ2 has no such command, so the client writes the
# rejection into the log — `Unknown command: 'act end'`, exactly the format the
# fixture shows for a real typo (`Unknown command: 'lbtell'`, 9 of them). That
# rejection IS the channel: it is how ACT's own EQ2 plugin hears the command,
# and it is why the raider does not need this site to be listening to anything
# but the log. Only `end` means something here; `/act clear` operates on ACT's
# own window and has nothing to do on a server.
RE_ACT_END = re.compile(r"^Unknown command: '\s*act\s+end\s*'\.?$", re.IGNORECASE)
RE_KILL_YOU = re.compile(r"^You have killed (.+)\.$")
RE_KILL = re.compile(r"^(.+?) has killed (.+)\.$")
RE_ALAS = re.compile(r"^Alas, (.+) has died from pain and suffering\.$")
# Every healer archetype has its own rez flavor — clerics "petition the
# divinities of resurrection", druids "call forth primeval forces of
# resurrection", shamans "primal forces". Matching only the first family
# counted half the rezzes in a raid night and credited none to the druids, so
# the verb is open-ended and the trailing "…resurrection." is what identifies
# the line. The flavor text is kept so an unseen family shows up as data.
RE_REZ = re.compile(
    r"^(?P<subj>.+?) (?P<flavor>(?:petitions|calls forth|beseeches|invokes|"
    r"implores|summons)\b[^.]*\bresurrection)\.$")
RE_REZ_ANON = re.compile(r"^A resurrection spell is cast on (?P<tgt>.+)\.$")
# the landing side, printed for everyone in range ("Sorengail is resurrected!",
# "Aros is revived!"); the logger gets their own "You regain consciousness!"
RE_REVIVED = re.compile(r"^(?P<tgt>.+?) (?:is|are) (?:revived|resurrected)!$")
RE_REVIVE = re.compile(r"^You regain consciousness!$")
RE_KO = re.compile(r"^You lose consciousness!$")
RE_DAMAGE = re.compile(
    rf"^(?P<subj>.+?) (?P<verb>hits|hit|multi attacks|multi attack|aoe attacks|aoe attack|flurries|flurry) (?P<tgt>.+?) "
    rf"for (?P<crit>a critical of )?(?P<amt>[\d,.]+K?) (?P<dtype>{DTYPES})"
    rf"(?: and (?P<amt2>[\d,.]+K?) (?P<dtype2>{DTYPES}))? damage\.$"
)
RE_HIT_PASSIVE = re.compile(
    rf"^(?P<tgt>.+?) is hit for (?P<crit>a critical of )?(?P<amt>[\d,.]+K?) (?P<dtype>{DTYPES}) damage\.$"
)
RE_HIT_BY = re.compile(
    rf"^(?P<tgt>.+?) (?:is|are) hit by (?P<effect>.+?) "
    rf"for (?P<crit>a critical of )?(?P<amt>[\d,.]+K?) (?P<dtype>{DTYPES})"
    rf"(?: and (?P<amt2>[\d,.]+K?) (?P<dtype2>{DTYPES}))? damage\.$"
)
RE_ZERO_DMG = re.compile(
    r"^(?P<subj>.+?) (?P<verb>hits|hit|multi attacks|multi attack|aoe attacks|aoe attack|flurries|flurry) "
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
RE_DRAIN = re.compile(
    r"^(?P<subj>.+?) (?:confounds|zaps|smites|diseases|freezes|slashes|poisons|"
    r"burns|crushes|pierces|stabs|rends|shocks) (?P<tgt>.+?) "
    r"draining (?P<amt>[\d,]+) points? of power\.$")
# the drain verb tracks the spell's damage school (smites/zaps/diseases/…);
# unknown verbs still land here — the trailing clause is unambiguous, only the
# ability name keeps the verb+target garbage (power_drain never makes ability rows)
RE_DRAIN_ANY = re.compile(r"^(?P<subj>.+?) draining (?P<amt>[\d,]+) points? of power\.$")
RE_DRAINED_BY = re.compile(
    r"^(?P<tgt>.+?) (?:is|are) drained by (?P<eff>.+?) of (?P<amt>[\d,]+) points? of power\.$")
RE_THREAT = re.compile(
    r"^(?P<subj>.+?) (?P<dir>reduces|increases) (?:YOUR|THEIR) hate with (?P<mob>.+?) "
    r"for (?P<crit>a critical of )?(?P<amt>[\d,.]+K?) threat\.$"
)
RE_DISPEL = re.compile(r"^(?P<subj>.+?) dispels (?P<effect>.+?) from (?P<tgt>.+?)\.$")
RE_RELIEVE = re.compile(r"^(?P<subj>.+?) relieves (?P<effect>.+?) from (?P<tgt>.+?)\.$")
RE_AFFLICT = re.compile(r"^(?P<subj>.+) afflicts you\.$")
RE_EXPIRE_EFFECTS = re.compile(r"^(?P<effect>.+?) no longer effects (?P<tgt>.+)\.$")  # sic
RE_EXPIRE_FADES = re.compile(r"^(?P<effect>.+?) fades away\.$")
RE_EXPIRE_OVER = re.compile(r"^(?P<effect>.+?) is over\.$")
RE_INTERRUPT = re.compile(r"^(?P<tgt>.+?) was interrupted!$")
# "Bobby intercepted some of the damage intended for you!" — a fighter (or a
# pet with the same job) eating a hit aimed at someone else. The log never says
# how much, and the victim is only ever named from the logger's seat, so an
# intercept is a COUNT, not an amount. The two variants are the same event seen
# twice (see _dedupe_repeats).
RE_INTERCEPT = re.compile(
    r"^(?P<subj>.+?) intercepted some of the damage intended for "
    r"(?P<who>you|your target)!$")
RE_ANON_HEAL = re.compile(r"^A healing spell is cast on (?P<tgt>.+)\.$")
RE_PREPARE = re.compile(r"^You prepare (?P<what>.+?)\.?$")

# Chat, which classify_body discards. pipeline/redact.py imports these two so the
# set it is allowed to strip from a stored log stays exactly the set the parser
# ignores — a drift between them is how redaction would start eating real events.
# `\b` not ' ': `You say, "…"` (local /say) is chat too, and matching on a space
# missed it. Output is unchanged either way — it classified to None regardless.
CHAT_PREFIXES = ("\\aPC ", "\\aNPC ")
CHAT_RE = re.compile(r'^You (?:say|tell)\b')


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


def classify_body(ts: int, body: str, logger: str,
                  pet_names: frozenset[str] = frozenset()) -> ParsedEvent | None:
    """Classify one prefix-stripped body. Returns None for chat/unknown lines."""
    if body.startswith(CHAT_PREFIXES) or CHAT_RE.match(body):
        return None
    if "\\aITEM" in body:
        body = unescape_items(body)

    def dec(subj: str) -> tuple[Subject, str | None]:
        return decompose(subj, logger, pet_names)

    if RE_ACT_END.match(body):
        return ParsedEvent(ts, "encounter_end")

    if m := RE_ZONE.match(body):
        zone = m.group(1).strip()
        # real zone names are capitalized; "You have entered a house." /
        # "...an area where you may not summon a mount." are not zone changes
        # and must not hard-cut an encounter
        if not zone[:1].isupper():
            return None
        return ParsedEvent(ts, "zone", extra={"zone": zone})

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
        subj, ability = dec(m.group("subj"))
        return ParsedEvent(ts, "rez", src=subj, ability=ability,
                           extra={"flavor": m.group("flavor")})
    if m := RE_REZ_ANON.match(body):
        # no caster named — the rez still happened to somebody
        return ParsedEvent(ts, "rez", tgt=m.group("tgt"), extra={"anon": True})
    if RE_REVIVE.match(body):
        return ParsedEvent(ts, "revive", tgt="YOU")
    if RE_KO.match(body):
        # INCAPACITATED, not dead. EQ2 drops you unconscious at 0 HP and a heal
        # that beats the timer brings you back with nothing lost — Bronir's
        # 2026-08-16 log goes KO 14:13:28 -> "You regain consciousness!"
        # 14:13:29, and 4s later dies for real with its killer named. Counting
        # this line as a death recorded 2 deaths for that one death. The
        # logger's unannounced deaths are recovered from the shape of the hole
        # instead (pipeline/downs.py).
        return ParsedEvent(ts, "ko", tgt="YOU")
    if m := RE_REVIVED.match(body):
        tgt = m.group("tgt")
        return ParsedEvent(ts, "revive", tgt="YOU" if tgt == "You" else tgt)

    if m := RE_HIT_BY.match(body):
        # named sourceless effect ("Moklok is hit by Stench of Death for …") —
        # must precede RE_DAMAGE, which would mis-split it into garbage
        # entities. Attribution unknown; ACT pools these under "Unknown".
        flags = F_CRIT if m.group("crit") else 0
        amount = to_int(m.group("amt"))
        extra = {}
        if m.group("amt2"):
            amt2 = to_int(m.group("amt2"))
            extra["components"] = [[amount, m.group("dtype")], [amt2, m.group("dtype2")]]
            amount += amt2
        return ParsedEvent(
            ts, "damage", src=None, tgt=m.group("tgt"),
            ability=m.group("effect"), amount=amount, dtype=m.group("dtype"),
            flags=flags, extra=extra,
        )

    if m := RE_DAMAGE.match(body):
        subj, ability = dec(m.group("subj"))
        flags = 0
        if m.group("crit"):
            flags |= F_CRIT
        if ability is None:
            flags |= F_AUTOATTACK
        verb = m.group("verb")
        if verb.startswith("multi"):
            flags |= F_MULTI
        elif verb.startswith("aoe"):
            flags |= F_AOE
        elif verb.startswith("flurr"):
            flags |= F_FLURRY
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
        subj, ability = dec(m.group("subj"))
        flags = F_ZERO | (F_AUTOATTACK if ability is None else 0)
        verb = m.group("verb")
        if verb.startswith("multi"):
            flags |= F_MULTI
        elif verb.startswith("aoe"):
            flags |= F_AOE
        elif verb.startswith("flurr"):
            flags |= F_FLURRY
        return ParsedEvent(
            ts, "damage", src=subj, tgt=m.group("tgt"),
            ability=ability, amount=0, flags=flags,
        )

    if m := RE_AVOID.match(body):
        subj, _ = dec(m.group("subj"))
        ability = m.group("ability")
        how = m.group("how")
        kind = (
            "miss" if how.startswith("miss")
            else "dodge" if how.startswith("dodges")
            else "resist" if how.startswith("resist")
            else {"parries": "parry", "blocks": "block", "ripostes": "riposte",
                  "reflects": "reflect"}.get(how, how)
        )
        flags = 0 if ability else F_AUTOATTACK
        if how.endswith("multi attack"):
            flags |= F_MULTI
        return ParsedEvent(
            ts, "avoid", src=subj, tgt=m.group("tgt"),
            ability=ability, flags=flags,
            extra={"how": kind, "avoider": m.group("avoider") or None},
        )

    if m := RE_HEAL.match(body):
        subj, ability = dec(m.group("subj"))
        flags = F_CRIT if m.group("crit") else 0
        return ParsedEvent(
            ts, "heal", src=subj, tgt=m.group("tgt"),
            ability=ability, amount=to_int(m.group("amt")), flags=flags,
        )

    if m := RE_WARD.match(body):
        subj, ability = dec(m.group("subj"))
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
        subj, ability = dec(m.group("subj"))
        return ParsedEvent(ts, "ward_regen", src=subj, ability=ability, amount=to_int(m.group("amt")))

    if m := RE_POWER.match(body):
        subj, ability = dec(m.group("subj"))
        flags = F_CRIT if m.group("crit") else 0
        return ParsedEvent(
            ts, "power", src=subj, tgt=m.group("tgt"),
            ability=ability, amount=to_int(m.group("amt")), flags=flags,
        )
    if m := RE_DRAIN.match(body):
        subj, ability = dec(m.group("subj"))
        return ParsedEvent(
            ts, "power_drain", src=subj, tgt=m.group("tgt"),
            ability=ability, amount=to_int(m.group("amt")),
        )
    if m := RE_DRAIN_ANY.match(body):
        subj, ability = dec(m.group("subj"))
        return ParsedEvent(
            ts, "power_drain", src=subj,
            ability=ability, amount=to_int(m.group("amt")),
        )
    if m := RE_DRAINED_BY.match(body):
        # sourceless mob-effect drain ("X is drained by Revived Sickness of
        # 1,000 points of power.") — no drainer to credit
        return ParsedEvent(
            ts, "power_drain", tgt=m.group("tgt"),
            amount=to_int(m.group("amt")), extra={"effect": m.group("eff")},
        )

    if m := RE_THREAT.match(body):
        subj, ability = dec(m.group("subj"))
        flags = F_CRIT if m.group("crit") else 0
        return ParsedEvent(
            ts, "threat", src=subj, tgt=m.group("mob"), ability=ability,
            amount=to_int(m.group("amt")) * (1 if m.group("dir") == "increases" else -1),
            flags=flags,
        )

    if m := RE_DISPEL.match(body):
        subj, ability = dec(m.group("subj"))
        return ParsedEvent(
            ts, "dispel", src=subj, tgt=m.group("tgt"),
            ability=ability, extra={"effect": m.group("effect")},
        )
    if m := RE_RELIEVE.match(body):
        # "<Curer>'s <Ability> relieves <Effect> from <Target>." — the cure
        # grammar (dispels = stripping buffs; relieves = curing detriments).
        # ACT counts both in its Cures column, credited to the subject.
        subj, ability = dec(m.group("subj"))
        return ParsedEvent(
            ts, "dispel", src=subj, tgt=m.group("tgt"),
            ability=ability, extra={"effect": m.group("effect"), "cure": True},
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

    if m := RE_INTERCEPT.match(body):
        subj, ability = dec(m.group("subj"))
        who = m.group("who")
        return ParsedEvent(
            ts, "intercept", src=subj, ability=ability,
            tgt="YOU" if who == "you" else None, extra={"for": who},
        )

    if m := RE_ANON_HEAL.match(body):
        # no caster, no amount — never counts toward HPS
        return ParsedEvent(ts, "anon_heal", tgt=m.group("tgt"))

    if hit := buffs.match(body):
        # the only place another player's cast is visible at all — see buffs.py
        kind, ability, who = hit
        if kind == "cast":
            src = Subject(logger, "player") if who is None else Subject(who, "unknown")
            return ParsedEvent(ts, "buff_cast", src=src, ability=ability)
        # the landing names the TARGET and never the caster; attribution is
        # paired on afterwards (`_pair_buffs`), and stays unclaimed when two
        # casters are inside the same window
        return ParsedEvent(ts, "buff", tgt="YOU" if who is None else who,
                           ability=ability)

    if m := RE_PREPARE.match(body):
        # flavor text, NOT an ability name ("to rot a soul" -> Soulrot)
        what = m.group("what")
        return ParsedEvent(ts, "cast_flavor", src=Subject(logger, "player"),
                           ability=resolve_flavor(what), extra={"flavor": what})

    return None


WARD_PAIR_WINDOW_S = 2   # an absorb line precedes its hit line, same/next second


def _pair_wards(events: Iterator[ParsedEvent]) -> Iterator[ParsedEvent]:
    """Fold ward absorbs into the hit they mitigated, the way ACT does.

    The log prints `<Ward> absorbs N points of damage from being done to <T>`
    immediately BEFORE the corresponding hit line, and the hit line shows only
    the bleed-through (or "fails to inflict any damage" when fully absorbed).
    ACT reconstructs the pre-ward hit: the absorbed amount counts as the
    attacker's damage and the target's damage taken; the warder separately
    keeps the full absorb as healing. Pairing key = the raw target string
    (wards and hits both say YOU for the logger)."""
    pending: dict[str, list[ParsedEvent]] = {}

    def key(tgt: str) -> str:
        # absorbs say "being done to YOU", the mitigated self-hit says
        # "hits YOURSELF" — same combatant, one pairing key
        return "YOU" if tgt == "YOURSELF" else tgt

    for ev in events:
        if ev.type == "ward" and ev.tgt is not None:
            pending.setdefault(key(ev.tgt), []).append(ev)
            # a target nobody hits again would grow forever on a long night
            if len(pending) > 512:
                for k in [k for k, ws in pending.items()
                          if ws[-1].ts < ev.ts - WARD_PAIR_WINDOW_S]:
                    del pending[k]
        elif ev.type == "damage" and ev.tgt is not None and key(ev.tgt) in pending:
            wards = [w for w in pending.pop(key(ev.tgt))
                     if ev.ts - w.ts <= WARD_PAIR_WINDOW_S]
            absorbed = sum(w.amount or 0 for w in wards)
            if absorbed:
                ev.extra = dict(ev.extra or {})
                ev.extra["warded"] = absorbed
                ev.amount = (ev.amount or 0) + absorbed
                if ev.flags & F_ZERO:
                    ev.flags &= ~F_ZERO   # fully-absorbed hit is a real hit in ACT
                for w in wards:
                    # mutated after the ward was yielded — safe because every
                    # consumer (bulk parse, live batches) materializes the full
                    # event list before rolling stats. An UNpaired absorb means
                    # the mitigated hit printed no line at all (fully-absorbed
                    # DoT tick); its amount still counts as the target's damage
                    # taken, which is what ACT does.
                    w.extra = dict(w.extra or {})
                    w.extra["paired"] = True
        yield ev


BUFF_PAIR_WINDOW_S = 2   # cast line, then the landing line 0-2s later


def _pair_buffs(events: Iterator[ParsedEvent]) -> Iterator[ParsedEvent]:
    """Give a buff landing the caster that produced it.

    The two lines are written independently — the cast names the caster and no
    target, the landing names the target and no caster — so the only link is
    time. Census puts Jester's Cap at a 1s cast, and the observed delay is 0s
    or 1s in 816 of 820 landings.

    With several casters of the same buff in a raid this is still decisive
    almost always: pairing across a 3-troubador log left 590 of 596 landings
    with exactly ONE candidate caster and none ambiguous. When two casters DO
    fall in one window the landing keeps no source at all — the log cannot say
    whose it was, and picking one would invent attribution that reads as
    measured."""
    recent: dict[str, list[tuple[int, Subject]]] = {}

    for ev in events:
        if ev.type == "buff_cast" and ev.src is not None and ev.ability:
            casts = recent.setdefault(ev.ability, [])
            casts.append((ev.ts, ev.src))
            if len(casts) > 8:
                recent[ev.ability] = [c for c in casts
                                      if ev.ts - c[0] <= BUFF_PAIR_WINDOW_S]
        elif ev.type == "buff" and ev.src is None and ev.ability:
            near = [s for ts, s in recent.get(ev.ability, ())
                    if 0 <= ev.ts - ts <= BUFF_PAIR_WINDOW_S]
            if near and len({s.name for s in near}) == 1:
                ev.src = near[0]
        yield ev


_REPEATABLE = ("revive", "intercept", "death", "ko")


def _dedupe_repeats(events: Iterator[ParsedEvent]) -> Iterator[ParsedEvent]:
    """Collapse the several lines EQ2 prints for one event, SAME SECOND only.

    - a rez lands as both "You regain consciousness!" and "You are revived!";
    - an intercept prints "…intended for you!" AND "…intended for your target!"
      (1270 of the 1442 intercept seconds in the raid logs carry both);
    - a `ko` ("You lose consciousness!") can share its second with the kill
      line for the death it turned into, and is its own type either way.
    The window is the second, not a span: a tank intercepts again 2s later and
    that is a second intercept, not an echo. Two intercepts inside one second
    are indistinguishable in the log, so one is the honest floor."""
    seen: set[tuple] = set()
    seen_ts: int | None = None

    for ev in events:
        if ev.ts != seen_ts:
            seen.clear()
            seen_ts = ev.ts
        if ev.type in _REPEATABLE:
            who = (ev.src.name if ev.type == "intercept" and ev.src else ev.tgt)
            key = (ev.type, who)
            if key in seen:
                continue
            seen.add(key)
        yield ev


def parse_lines(lines: Iterable[str], logger: str,
                pet_names: frozenset[str] = frozenset()) -> Iterator[ParsedEvent]:
    """Parse raw log lines (with prefixes) into events. Shared by bulk uploads
    and live ingest batches."""
    def raw() -> Iterator[ParsedEvent]:
        # the client sometimes logs a prepare line twice in the same second
        # (exact duplicate, per-spell — 234 of 918 in bobby.txt); a real
        # same-second re-prepare of the same spell can't happen, so collapse
        last_flavor: tuple[int, str] | None = None
        # buff lines duplicate the same way. The key carries who and which
        # ability, so two troubadors casting in one second stay two casts and
        # one buff landing on two people stays two landings — the only thing
        # collapsed is the client printing one event twice.
        seen_buffs: tuple[int, set] = (-1, set())
        for line in lines:
            parts = split_prefix(line)
            if parts is None:
                continue
            ts, body = parts
            ev = classify_body(ts, body, logger, pet_names)
            if ev is None:
                continue
            if ev.type == "cast_flavor":
                key = (ts, ev.extra["flavor"])
                if key == last_flavor:
                    continue
                last_flavor = key
            elif ev.type in ("buff", "buff_cast"):
                if seen_buffs[0] != ts:
                    seen_buffs = (ts, set())
                key = (ev.type, ev.ability,
                       ev.src.name if ev.src else None, ev.tgt)
                if key in seen_buffs[1]:
                    continue
                seen_buffs[1].add(key)
            yield ev

    yield from _dedupe_repeats(_pair_buffs(_pair_wards(raw())))
