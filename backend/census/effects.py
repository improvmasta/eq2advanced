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
_PROC = re.compile(r"may cast (.+?) on ", re.IGNORECASE)


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
    elif _PROC.search(text):
        out.update(kind="proc", casts=_PROC.search(text).group(1))
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
