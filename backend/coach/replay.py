"""What-if stat replay: re-price the session's actual casts under perturbed
stats and report the marginal DPS of each stat. All damage-side marginals are
differences of `predicted_damage`, which is monotone in every stat by
construction — a bigger stat can never predict less damage.

Marginals are per conventional step: +100 ability mod, +1 base modifier,
+1% crit chance, +1% reuse speed, +1% cast speed.
"""

import json
from statistics import median

from coach.fit import (DEFAULT_CRIT_MULT, effective_cast_s, effective_recast_s,
                       expected_noncrit)

# a rotation this idle gains nothing from faster casts
IDLE_FULL_CREDIT = 5.0    # idle % under which cast-speed time converts fully
IDLE_NO_CREDIT = 15.0
COOLDOWN_LOCK_RATIO = 1.25  # median gap within this multiple of recast = locked
MAX_UPGRADE_TIER = 9        # Master — Ancient/Celestial don't exist on TLE, and
                            # Grandmaster is a class choice, not an obtainable scroll


def predicted_damage(fits: list[dict], stats: dict) -> float:
    """Model total for the fitted abilities at the given stats, holding the
    session's observed hit counts and per-ability crit rates fixed (crit chance
    perturbations shift each ability's own observed rate)."""
    total = 0.0
    base_crit = stats.get("_base_critchance", stats["critchance"])
    crit_shift = (stats["critchance"] - base_crit) / 100
    for f in fits:
        if f["coefficient"] is None or f.get("suspect_join"):
            continue
        n = f["noncrit_n"] + f["crit_n"]
        if n == 0:
            continue
        c = min(1.0, max(0.0, f["crit_n"] / n + crit_shift))
        exp = expected_noncrit(f["base_mid"], stats) * f["coefficient"]
        mult = f["crit_mult"] or DEFAULT_CRIT_MULT
        total += n * exp * ((1 - c) + c * mult)
    return total


def _damage_marginal(fits, stats, **delta) -> float:
    perturbed = {**stats, "_base_critchance": stats["critchance"]}
    for k, v in delta.items():
        perturbed[k] = perturbed[k] + v
    return max(0.0, predicted_damage(fits, perturbed) - predicted_damage(fits, stats))


def cooldown_locked(usage: dict, spell: dict | None, stats: dict) -> bool:
    """An ability is cooldown-locked when the observed inter-cast gap tracks its
    effective recast — recasting the moment it comes back up."""
    if not spell or not spell["recast_s"] or usage["casts"] < 3 or not usage["gaps"]:
        return False
    r_eff = effective_recast_s(spell["recast_s"], stats)
    return r_eff > 0 and median(usage["gaps"]) <= r_eff * COOLDOWN_LOCK_RATIO


def reuse_marginal(usage_by_ability: dict, book: dict, stats: dict) -> tuple[float, list[str]]:
    """Damage gained from +1% reuse: cooldown-locked abilities fit extra casts
    into the same window; abilities cast slower than their recast gain nothing."""
    gain = 0.0
    locked = []
    r2 = {**stats, "reusepct": stats["reusepct"] + 1}
    for name, u in usage_by_ability.items():
        spell = book.get(name)
        if not cooldown_locked(u, spell, stats):
            continue
        r_eff = effective_recast_s(spell["recast_s"], stats)
        r_eff2 = effective_recast_s(spell["recast_s"], r2)
        if r_eff2 <= 0 or r_eff2 >= r_eff:
            continue
        extra_casts = u["casts"] * (r_eff / r_eff2 - 1)
        gain += extra_casts * (u["damage"] / max(u["casts"], 1))
        locked.append(name)
    return gain, locked


def cast_speed_marginal(usage_by_ability: dict, book: dict, stats: dict,
                        dps: float, idle_pct: float | None) -> tuple[float, str | None]:
    """Time freed by +1% cast speed, converted to damage only if the rotation is
    actually time-starved (little idle)."""
    c2 = {**stats, "castpct": stats["castpct"] + 1}
    freed = 0.0
    for name, u in usage_by_ability.items():
        spell = book.get(name)
        if not spell or not spell["cast_s"]:
            continue
        freed += u["casts"] * (effective_cast_s(spell["cast_s"], stats)
                               - effective_cast_s(spell["cast_s"], c2))
    if freed <= 0:
        return 0.0, None
    if idle_pct is None or idle_pct <= IDLE_FULL_CREDIT:
        return freed * dps, None
    if idle_pct <= IDLE_NO_CREDIT:
        return freed * dps * 0.5, "rotation has some idle time — half credit"
    return 0.0, f"rotation is {idle_pct:.0f}% idle — faster casts add idle, not damage"


def stat_marginals(fits: list[dict], usage: dict, book: dict, stats: dict,
                   combat_s: int, currencies: dict) -> list[dict]:
    """Ranked stat-priority rows: damage/DPS gained per conventional step."""
    dps = currencies["dps"]
    out = []

    gain = _damage_marginal(fits, stats, abilitymod=100)
    out.append({
        "stat": "abilitymod", "label": "Ability Mod", "step": "+100",
        "damage_gain": round(gain), "dps_gain": round(gain / combat_s, 2),
        "why": "flat damage on every hit, capped at half each spell's base — "
               "spells still under the cap gain the full amount"})

    gain = _damage_marginal(fits, stats, basemodifier=1)
    out.append({
        "stat": "basemodifier", "label": "Base Modifier", "step": "+1",
        "damage_gain": round(gain), "dps_gain": round(gain / combat_s, 2),
        "why": "multiplies every spell's base damage before ability mod"})

    gain = _damage_marginal(fits, stats, critchance=1)
    out.append({
        "stat": "critchance", "label": "Crit Chance", "step": "+1%",
        "damage_gain": round(gain), "dps_gain": round(gain / combat_s, 2),
        "why": "converts non-crits to crits at the fitted per-ability "
               "crit multiplier"})

    gain, locked = reuse_marginal(usage, book, stats)
    out.append({
        "stat": "reusepct", "label": "Reuse Speed", "step": "+1%",
        "damage_gain": round(gain), "dps_gain": round(gain / combat_s, 2),
        "why": (f"extra casts of your cooldown-locked abilities "
                f"({', '.join(sorted(locked)[:4])}…)" if len(locked) > 4 else
                f"extra casts of your cooldown-locked abilities "
                f"({', '.join(sorted(locked))})") if locked else
               "no ability was recast on cooldown this session — reuse would "
               "not have added casts"})

    gain, note = cast_speed_marginal(usage, book, stats, dps,
                                     currencies.get("idle_pct"))
    out.append({
        "stat": "castpct", "label": "Cast Speed", "step": "+1%",
        "damage_gain": round(gain), "dps_gain": round(gain / combat_s, 2),
        "why": note or "shorter casts free time for more casts"})

    out.sort(key=lambda r: -r["dps_gain"])
    return out


def tier_upgrades(conn, fits: list[dict], stats: dict, combat_s: int) -> list[dict]:
    """Value of taking each used spell line from its scribed tier to the best
    tier Census knows, priced through the fitted coefficient at the session's
    actual hit counts. Needs ensure_spell_lines() to have cached the lines."""
    out = []
    for f in fits:
        if f["coefficient"] is None or not f["crc"] or f.get("suspect_join"):
            continue
        best = conn.execute(
            "SELECT spell_id, name, tier, tier_name, parsed_effects "
            "FROM census_spells WHERE crc=? AND tier <= ? ORDER BY tier DESC LIMIT 1",
            (f["crc"], MAX_UPGRADE_TIER)).fetchone()
        if best is None or (best["tier"] or 0) <= (f["tier"] or 0):
            continue
        effs = [e for e in json.loads(best["parsed_effects"] or "[]")
                if e.get("kind") == "damage" and e.get("min") is not None]
        if not effs:
            continue
        # compare the same slot: the effect closest in base to the fitted one
        eff = min(effs, key=lambda e: abs((e["min"] + e["max"]) / 2 - f["base_mid"]))
        new_mid = (eff["min"] + eff["max"]) / 2
        if new_mid <= f["base_mid"]:
            continue
        per_hit = (expected_noncrit(new_mid, stats)
                   - expected_noncrit(f["base_mid"], stats)) * f["coefficient"]
        n = f["noncrit_n"] + f["crit_n"]
        c = f["crit_n"] / n if n else 0
        mult = f["crit_mult"] or DEFAULT_CRIT_MULT
        gain = n * per_hit * ((1 - c) + c * mult)
        out.append({
            "ability": f["ability"], "spell_name": f["spell_name"],
            "from_tier": f["tier_name"], "to_tier": best["tier_name"],
            "damage_gain": round(gain), "dps_gain": round(gain / combat_s, 2),
        })
    out.sort(key=lambda r: -r["dps_gain"])
    return out
