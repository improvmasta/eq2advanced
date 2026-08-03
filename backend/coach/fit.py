"""Observed-vs-Census per-ability fit.

Census effect numbers are a PRIOR — TLE tuning can differ from the live-data
export — so we reconcile the character's actual non-crit hits at their snapshot
stats into a per-ability coefficient (observed / expected). The what-if math in
replay.py always scales through that coefficient, so advice tracks the real
server even where Census drifts. Calibration sessions (dummy parses flagged on
the Calibration page) override per-ability coefficients as ground truth.

Damage model (EoF era: no fervor, no crit bonus):

    expected_noncrit = base_mid * (1 + basemodifier/100) + min(abilitymod, base_mid/2)

Ability mod contributes flat damage capped at half the ability's base (the
community rule for this era). The crit multiplier is fitted empirically per
ability (crit mean / non-crit mean) and defaults to 1.3 when the sample is thin.
"""

import json
import statistics

from census.sync import typed_fields
from parser.events import F_CRIT, F_SELF_FOCUS, F_ZERO

DEFAULT_CRIT_MULT = 1.3
ABMOD_CAP_FRACTION = 0.5
# cast/reuse/recovery speed all cap at 50% in this era
SPEED_CAP = 0.5
# observed/expected outside these bounds means the name join is wrong, not
# that TLE tuning drifted that far (real raid fits run ~2-4.6; Master's
# Strike misjoined at 54.6) — suspect fits are excluded from all marginals
K_SANE_MIN = 0.2
K_SANE_MAX = 12.0


def snapshot_stats(doc: dict) -> dict:
    """The coaching stat vector from a trimmed census snapshot doc."""
    stats = doc.get("stats") or {}
    combat = stats.get("combat") or {}
    ability = stats.get("ability") or {}
    return {
        "abilitymod": combat.get("abilitymod") or 0.0,
        "basemodifier": combat.get("basemodifier") or 0.0,
        "critchance": combat.get("critchance") or 0.0,
        "castpct": ability.get("spelltimecastpct") or 0.0,
        "reusepct": ability.get("spelltimereusepct") or 0.0,
        "recoverypct": ability.get("spelltimerecoverypct") or 0.0,
    }


def expected_noncrit(base_mid: float, stats: dict) -> float:
    return (base_mid * (1 + stats["basemodifier"] / 100)
            + min(stats["abilitymod"], base_mid * ABMOD_CAP_FRACTION))


def effective_cast_s(cast_s: float, stats: dict) -> float:
    return cast_s * max(SPEED_CAP, 1 - stats["castpct"] / 100)


def effective_recast_s(recast_s: float, stats: dict) -> float:
    return recast_s * max(SPEED_CAP, 1 - stats["reusepct"] / 100)


def effective_recovery_s(recovery_s: float, stats: dict) -> float:
    return recovery_s * max(SPEED_CAP, 1 - stats["recoverypct"] / 100)


def spellbook(conn, char, doc: dict) -> dict:
    """log-ability-name -> the character's best scribed spell for that line.

    Keyed by base_name (damage lines drop the numeral: 'YOUR Soulrot hits')
    AND by the full versioned name (buff lines keep it: "...'s Clarion VI").
    Effects come from parsed_effects with spell_overrides applied on top.
    """
    ids = doc.get("spell_list") or []
    if not ids:
        return {}
    rows = conn.execute(
        f"SELECT * FROM census_spells WHERE spell_id IN ({','.join('?' * len(ids))})",
        ids).fetchall()
    cls = (char["class"] or "").lower()
    best: dict[str, dict] = {}
    for r in rows:
        if cls and cls not in (r["class"] or ""):
            continue
        # a scribed version above the character's level isn't castable — the
        # log's base name maps to the highest version they can actually use
        if r["level"] and char["level"] and r["level"] > char["level"]:
            continue
        cur = best.get(r["base_name"])
        if cur is None or ((r["level"] or 0), (r["tier"] or 0)) > (
                (cur["level"] or 0), (cur["tier"] or 0)):
            best[r["base_name"]] = dict(r)
    # a log name the catalog knows as a pet-kit ability must never join the
    # player fit, even when a scribed spell shares the base name
    pet_names = {r[0] for r in conn.execute(
        "SELECT ability_name FROM ability_catalog WHERE unit='pet'")}
    book: dict[str, dict] = {}
    for b in best.values():
        if b["base_name"] in pet_names:
            continue
        ov = conn.execute("SELECT parsed_effects FROM spell_overrides WHERE spell_id=?",
                          (b["spell_id"],)).fetchone()
        b["effects"] = json.loads(ov[0] if ov else (b["parsed_effects"] or "[]"))
        b["overridden"] = ov is not None
        # timing from the shared extractor (owns the recovery-hundredths
        # gotcha) so the fit and the typed columns can never disagree; effects
        # here include any override, so damage still comes from b["effects"]
        rec = json.loads(b["json"]) if b["json"] else {}
        t = typed_fields(rec, b["effects"])
        b["cast_s"], b["recast_s"] = t["cast_s"], t["recast_s"]
        b["recovery_s"], b["duration_s"] = t["recovery_s"], t["duration_s"]
        book[b["base_name"]] = b
        book.setdefault(b["name"], b)
    return book


def logger_entities(conn, session_id: int, logger_name: str):
    """-> (player_entity_id | None, ids credited to the logger incl. pets)."""
    player = conn.execute(
        "SELECT id FROM entities WHERE session_id=? AND kind='player' AND name=?",
        (session_id, logger_name)).fetchone()
    if player is None:
        return None, set()
    pid = player["id"]
    ids = {pid} | {r["id"] for r in conn.execute(
        "SELECT id FROM entities WHERE session_id=? AND rollup_to=?",
        (session_id, pid))}
    return pid, ids


def damage_samples(conn, session_id: int, player_entity_id: int) -> dict:
    """Per-ability hit samples for the PLAYER entity only (scribed spells are
    player casts; pet kits are not in the character's spell_list).
    -> {ability: {noncrit: [amt], crit: [amt], zero: int,
                  ts: {encounter_id: [ts,...]}}}"""
    out: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT e.encounter_id, e.ts, e.amount, e.flags, a.name AS ability "
            "FROM events e JOIN abilities a ON a.id = e.ability_id "
            "WHERE e.session_id=? AND e.type='damage' AND e.src_entity=? "
            "AND (e.tgt_entity IS NULL OR e.tgt_entity != e.src_entity) "
            "ORDER BY e.ts, e.seq",
            (session_id, player_entity_id)):
        if r["flags"] & F_SELF_FOCUS:
            continue
        s = out.setdefault(r["ability"], {"noncrit": [], "crit": [], "zero": 0, "ts": {}})
        s["ts"].setdefault(r["encounter_id"], []).append(r["ts"])
        if r["flags"] & F_ZERO:
            s["zero"] += 1
        elif r["flags"] & F_CRIT:
            s["crit"].append(r["amount"] or 0)
        else:
            s["noncrit"].append(r["amount"] or 0)
    return out


def _confidence(n: int) -> str:
    if n >= 20:
        return "high"
    if n >= 5:
        return "medium"
    return "low"


def fit_ability(ability: str, sample: dict, spell: dict | None, stats: dict) -> dict:
    noncrit, crit = sample["noncrit"], sample["crit"]
    row = {
        "ability": ability,
        "spell_id": spell["spell_id"] if spell else None,
        "spell_name": spell["name"] if spell else None,
        "tier": spell["tier"] if spell else None,
        "tier_name": spell["tier_name"] if spell else None,
        "crc": spell["crc"] if spell else None,
        "overridden": bool(spell and spell.get("overridden")),
        "noncrit_n": len(noncrit), "crit_n": len(crit), "zero_n": sample["zero"],
        "observed_mean": round(statistics.fmean(noncrit), 1) if noncrit else None,
        "observed_min": min(noncrit) if noncrit else None,
        "observed_max": max(noncrit) if noncrit else None,
        "crit_mult": None, "crit_mult_fitted": False,
        "census_min": None, "census_max": None, "base_mid": None,
        "expected": None, "coefficient": None, "mixed": False, "periodic": False,
        "confidence": "none",
    }
    if noncrit and crit and len(noncrit) >= 5 and len(crit) >= 5:
        nc_mean = statistics.fmean(noncrit)
        if nc_mean > 0:
            row["crit_mult"] = round(statistics.fmean(crit) / nc_mean, 3)
            row["crit_mult_fitted"] = True
    if row["crit_mult"] is None:
        row["crit_mult"] = DEFAULT_CRIT_MULT

    effs = [e for e in (spell["effects"] if spell else [])
            if e.get("kind") == "damage" and e.get("min") is not None]
    if not effs or not noncrit:
        return row

    # A spell can carry several damage effects (initial hit + tick). Assign each
    # observed hit to the effect whose expected value it is closest to (ratio
    # distance), then fit the biggest cluster — we'd rather under-use a spell
    # than misread it.
    expects = [(e, expected_noncrit((e["min"] + e["max"]) / 2, stats)) for e in effs]
    clusters: list[list[int]] = [[] for _ in expects]
    for amt in noncrit:
        best_i = min(range(len(expects)),
                     key=lambda i: abs(amt - expects[i][1]) / max(expects[i][1], 1e-9))
        clusters[best_i].append(amt)
    main_i = max(range(len(expects)), key=lambda i: len(clusters[i]))
    eff, exp = expects[main_i]
    main = clusters[main_i]
    row.update({
        "census_min": eff["min"], "census_max": eff["max"],
        "base_mid": (eff["min"] + eff["max"]) / 2,
        "expected": round(exp, 1),
        "coefficient": round(statistics.fmean(main) / exp, 4) if exp > 0 else None,
        "mixed": sum(1 for c in clusters if c) > 1,
        "periodic": bool(eff.get("period_s")),
        "noncrit_n": len(main),
        "observed_mean": round(statistics.fmean(main), 1),
        "confidence": _confidence(len(main)),
    })
    row["dtype"] = eff.get("dtype")
    k = row["coefficient"]
    if k is not None and not (K_SANE_MIN <= k <= K_SANE_MAX):
        row["suspect_join"] = True
        row["confidence"] = "suspect"
    return row


def fit_session(conn, char, session_id: int, stats: dict, book: dict) -> list[dict]:
    """Fit every player-cast damage ability in the session against Census."""
    player_id, _ = logger_entities(conn, session_id, char["name"])
    if player_id is None:
        return []
    samples = damage_samples(conn, session_id, player_id)
    return [fit_ability(name, s, book.get(name), stats)
            for name, s in sorted(samples.items())]


ABMOD_TWO_POINT_MIN_DELTA = 100.0   # abmod separation needed for a cap solve
TWO_POINT_TOLERANCE = 0.10


def _solve_two_point(points: list[dict]) -> tuple[float | None, str | None]:
    """Fit the TRUE base (and so the real abmod cap) from dummy parses at two+
    ability-mod values. One stat point cannot separate base drift from abmod
    contribution; two can, piecewise:

        obs = B*(1 + bm/100) + min(abmod, B/2)

    Try the three cap hypotheses (both points under the cap, both over,
    mixed), keep the consistent one with the smallest residual. Returns
    (base_true, hypothesis) or (None, None) when the points can't decide."""
    pts = sorted(points, key=lambda p: p["abmod"])
    lo, hi = pts[0], pts[-1]
    if hi["abmod"] - lo["abmod"] < ABMOD_TWO_POINT_MIN_DELTA:
        return None, None

    def bmf(p):
        return 1 + p["basemodifier"] / 100

    cands = []
    # both uncapped: obs = B*bm + abmod
    b1 = (lo["observed_mean"] - lo["abmod"]) / bmf(lo)
    b2 = (hi["observed_mean"] - hi["abmod"]) / bmf(hi)
    if b1 > 0 and b2 > 0:
        b = (b1 + b2) / 2
        resid = abs(b1 - b2) / b
        if resid <= TWO_POINT_TOLERANCE and \
                hi["abmod"] <= b / 2 * (1 + TWO_POINT_TOLERANCE):
            cands.append((resid, b, "uncapped"))
    # both capped: obs = B*bm + B/2
    b1 = lo["observed_mean"] / (bmf(lo) + ABMOD_CAP_FRACTION)
    b2 = hi["observed_mean"] / (bmf(hi) + ABMOD_CAP_FRACTION)
    if b1 > 0 and b2 > 0:
        b = (b1 + b2) / 2
        resid = abs(b1 - b2) / b
        if resid <= TWO_POINT_TOLERANCE and \
                lo["abmod"] >= b / 2 * (1 - TWO_POINT_TOLERANCE):
            cands.append((resid, b, "capped"))
    # mixed: the low point under the cap pins B; the high point must then land
    # on the capped branch
    b = (lo["observed_mean"] - lo["abmod"]) / bmf(lo)
    if b > 0 and hi["observed_mean"] > 0:
        pred_hi = b * bmf(hi) + b * ABMOD_CAP_FRACTION
        resid = abs(pred_hi - hi["observed_mean"]) / hi["observed_mean"]
        if resid <= TWO_POINT_TOLERANCE and \
                lo["abmod"] <= b / 2 * (1 + TWO_POINT_TOLERANCE) <= hi["abmod"]:
            cands.append((resid, b, "mixed"))
    if not cands:
        return None, None
    _, b, hyp = min(cands)
    return b, hyp


def calibration_data(conn, char, current_stats: dict, book: dict,
                     exclude_session: int | None = None) -> dict:
    """Per-ability calibration evidence from the character's dummy-parse
    sessions. Each session fits at the stats captured WHEN it was flagged
    (old flags without a capture fall back to current stats and are marked
    stale). -> {ability: {k_dummy, n, points, base_true, cap, stale_stats}}"""
    q = ("SELECT id, calib_stats_json FROM sessions WHERE character_id=? "
         "AND calibration=1 AND status='ready'")
    params: list = [char["id"]]
    if exclude_session is not None:
        q += " AND id != ?"
        params.append(exclude_session)
    out: dict[str, dict] = {}
    for row in conn.execute(q, params).fetchall():
        stats_i = (json.loads(row["calib_stats_json"])
                   if row["calib_stats_json"] else current_stats)
        for f in fit_session(conn, char, row["id"], stats_i, book):
            if f["coefficient"] is None or f.get("suspect_join"):
                continue
            d = out.setdefault(f["ability"], {
                "points": [], "k_dummy": None, "n": 0,
                "base_true": None, "cap": None, "hypothesis": None,
                "stale_stats": False})
            d["points"].append({
                "session_id": row["id"], "abmod": stats_i["abilitymod"],
                "basemodifier": stats_i["basemodifier"],
                "observed_mean": f["observed_mean"], "n": f["noncrit_n"],
                "base_mid": f["base_mid"], "coefficient": f["coefficient"]})
            if row["calib_stats_json"] is None:
                d["stale_stats"] = True
    for d in out.values():
        best = max(d["points"], key=lambda p: p["n"])
        d["k_dummy"] = best["coefficient"]
        d["n"] = best["n"]
        base, hyp = _solve_two_point(d["points"])
        if base:
            d["base_true"] = round(base, 1)
            d["cap"] = round(base * ABMOD_CAP_FRACTION, 1)
            d["hypothesis"] = hyp
    return out


def apply_calibration(fits: list[dict], calib: dict, stats: dict) -> None:
    """Fold calibration evidence into a session's fit rows, in place.

    The raid fit is NEVER overwritten by a healthy dummy fit — the spread
    between them is a measurement (raid debuffs on that damage school), so
    both are kept and the ratio is reported as `debuff_uplift`. Dummy k only
    substitutes when the session's own sample is too thin to fit.

    With a two-point solve, the TRUE base replaces the Census base: the abmod
    cap in every marginal becomes real, and the refit coefficient measures
    uplift over truth instead of Census drift."""
    for f in fits:
        c = calib.get(f["ability"])
        if c is None or f.get("suspect_join"):
            continue
        f["k_dummy"] = c["k_dummy"]
        if c["base_true"]:
            best = max(c["points"], key=lambda p: p["n"])
            f["base_source"] = "calibrated2"
            f["base_mid"] = c["base_true"]
            f["abmod_cap"] = c["cap"]
            exp = expected_noncrit(c["base_true"], stats)
            f["expected"] = round(exp, 1)
            if f["observed_mean"] and exp > 0:
                f["coefficient"] = round(f["observed_mean"] / exp, 4)
            f["confidence"] = "calibrated"
            dummy_exp = expected_noncrit(c["base_true"], {
                **stats, "abilitymod": best["abmod"],
                "basemodifier": best["basemodifier"]})
            if dummy_exp > 0 and best["observed_mean"] and f["coefficient"]:
                k_dummy_true = best["observed_mean"] / dummy_exp
                if k_dummy_true > 0:
                    f["debuff_uplift"] = round(f["coefficient"] / k_dummy_true, 3)
        elif f["coefficient"] is None or f["confidence"] in ("low", "none"):
            f["coefficient"] = c["k_dummy"]
            f["confidence"] = "calibrated"
        elif c["k_dummy"]:
            f["debuff_uplift"] = round(f["coefficient"] / c["k_dummy"], 3)
