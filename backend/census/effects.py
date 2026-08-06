"""Parse Census effect_list description text into structured effects.

The damage/heal numbers a spell "should" do exist ONLY in this machine-generated
grammar ("Inflicts 33 - 45 disease damage on target instantly and every second.")
— the typed spell fields cover cast/recast/duration but not amounts. The grammar
is regular; anything we don't recognize is kept verbatim as kind="other" so the
coach engine (phase 5) can only ever under-use a spell, never misread it.
"""

import re

DTYPES = ("crushing|slashing|piercing|disease|poison|mental|cold|heat|divine|"
          "magic|focus")

_NUM = r"(\d[\d,]*(?:\.\d+)?)"


def _f(s: str) -> float:
    return float(s.replace(",", ""))


_PERIOD = re.compile(r"instantly and every (?:(\d+(?:\.\d+)?) )?seconds?")

_DAMAGE = re.compile(
    rf"^Inflicts {_NUM}(?: - {_NUM})? ({DTYPES}) damage on (.+?)(?: instantly.*)?\.?$")
_DAMAGE_PCT = re.compile(
    rf"^Inflicts {_NUM}% of max health in ({DTYPES}) damage on (.+?)(?: instantly.*)?\.?$")
_HEAL = re.compile(rf"^Heals (.+?) for {_NUM}(?: - {_NUM})?(?: instantly.*)?\.?$")
_HEAL_PCT = re.compile(rf"^Heals (.+?) for {_NUM}(?: - {_NUM})?% of max health.*\.?$")
_WARD = re.compile(rf"^Wards (.+?) against {_NUM} points of (.+?) damage\.?$")
_POWER = re.compile(
    rf"^(Increases|Decreases) power of (.+?) by {_NUM}(?: instantly.*)?\.?$")
_STAT = re.compile(
    rf"^(Increases|Decreases) (.+?) of (.+?) by {_NUM}%?( instantly.*)?\.?$")
# The trigger grammar, whole. It reads "On a melee hit this spell may cast
# Cripple on target of attack." — a CONDITION, a chance, the ability it fires,
# and who it lands on.
#
# There are exactly three ways EQ2 writes the middle of that, and getting the
# set wrong silently loses whole categories of proc:
#
#     this spell will cast X on ...                    2236
#     this spell may cast X on ...                     1138
#     this spell has a 12% chance to cast X on ...      1074   <-- missed twice
#
# The original `may cast (.+?) on ` took only the second, dropping every
# guaranteed proc (`Shout`, `Thorns`, `Grisly Feedback`, `Prismatic Shock`).
# Widening it to `may|will` still missed the percentage form, which is a
# QUARTER of all triggers — and that is the form that hides the ones we most
# needed: `Crypt's Revenge` is the dirge group buff Dead Calm at 12% on a
# combat hit, and with no source found it read as "gear, an AA or a deity,
# and the ingest that would say which does not exist yet". It was in Census
# the whole time.
#
# `may not be cast` (24 effects) must NOT match — it is a restriction, not a
# trigger — which the required `cast <name> on <target>` tail takes care of.
#
# Keeping `trigger`, `mode` and the chance is what makes an effect reviewable
# rather than just flagged: "on a melee hit, 12% of the time" is a proc, "on a
# kill" is a consequence of one, and the two read identically once the clause
# is thrown away.
_PROC = re.compile(
    r"(?P<trigger>\b(?:on|when|whenever)\b[^.]*?)\bthis spell "
    r"(?:(?P<mode>may|will) cast|has a (?P<pct>[\d.]+)% chance to cast) "
    r"(?P<casts>.+?) on (?P<on>[^.]+?)\s*\.", re.IGNORECASE)
# "Casts Tranquil Healing for each successful Dispel." — the same idea with the
# condition trailing instead of leading (63 effects, all dispel-driven).
_PROC_FOR_EACH = re.compile(
    r"^Casts (?P<casts>.+?) for each (?P<trigger>[^.]+?)\s*\.", re.IGNORECASE)
_TRIGGERS_PER_MIN = re.compile(
    r"Triggers about ([\d.]+) times? per minute", re.IGNORECASE)


def _period(text: str) -> float | None:
    m = _PERIOD.search(text)
    if not m:
        return None
    return _f(m.group(1)) if m.group(1) else 1.0


def parse_effect(text: str) -> dict:
    """One effect_list description -> structured dict. Always includes raw+kind."""
    out = {"raw": text}
    if m := _DAMAGE.match(text):
        out.update(kind="damage", min=_f(m.group(1)),
                   max=_f(m.group(2)) if m.group(2) else _f(m.group(1)),
                   dtype=m.group(3), target=m.group(4), period_s=_period(text))
    elif m := _DAMAGE_PCT.match(text):
        out.update(kind="damage", pct_max_health=_f(m.group(1)), dtype=m.group(2),
                   target=m.group(3), period_s=_period(text))
    elif m := _HEAL_PCT.match(text):
        out.update(kind="heal", target=m.group(1), pct_max_health=_f(m.group(2)))
    elif m := _HEAL.match(text):
        out.update(kind="heal", target=m.group(1), min=_f(m.group(2)),
                   max=_f(m.group(3)) if m.group(3) else _f(m.group(2)),
                   period_s=_period(text))
    elif m := _WARD.match(text):
        out.update(kind="ward", target=m.group(1), amount=_f(m.group(2)),
                   dtype=m.group(3))
    elif m := _POWER.match(text):
        out.update(kind="power", direction=m.group(1).lower(), target=m.group(2),
                   amount=_f(m.group(3)), period_s=_period(text))
    elif (m := _PROC.search(text)) or (m := _PROC_FOR_EACH.search(text)):
        g = m.groupdict()
        rate = _TRIGGERS_PER_MIN.search(text)
        pct = g.get("pct")
        out.update(kind="proc", casts=g["casts"].strip(),
                   trigger=" ".join(g["trigger"].split()),
                   # a stated percentage IS the chance word — "12% chance to
                   # cast" is a `may`, spelled out
                   mode=(g.get("mode") or ("may" if pct else "will")).lower(),
                   chance_pct=_f(pct) if pct else None,
                   on=(g.get("on") or "").strip() or None,
                   per_min=_f(rate.group(1)) if rate else None)
    elif m := _STAT.match(text):
        out.update(kind="stat", direction=m.group(1).lower(), stat=m.group(2),
                   target=m.group(3), amount=_f(m.group(4)))
    elif text == "This effect cannot be critically applied.":
        out.update(kind="note", no_crit=True)
    else:
        out["kind"] = "other"
    return out


def parse_effects(effect_list: list[dict]) -> list[dict]:
    out = []
    for e in effect_list or []:
        desc = (e.get("description") or "").strip()
        if not desc:
            continue
        parsed = parse_effect(desc)
        parsed["indentation"] = e.get("indentation", 0)
        out.append(parsed)
    return out
