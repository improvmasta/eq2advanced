"""The "so what" layer: assemble currencies + fit + replay into a ranked,
plain-English coach report and persist it to coach_reports.

A report degrades gracefully: with no Census snapshot it still carries the
descriptive currencies and discipline findings, flagged with a `no_census`
finding instead of stat priorities. Tier-line fetching goes through
census.client.shared_client() but a Census outage only costs the tier-upgrade
section, never the report.
"""

import json
import logging
import time

from census import client as census_client
from census.sync import _snapshot_doc, ensure_spell_lines
from coach import descriptive, fit, replay
from coach.raidreport import build as build_raid_report
from db import json_dumps

ENGINE_VERSION = "coach-1"

IDLE_FINDING_PCT = 15.0
LATE_ENGAGE_S = 5.0
RESIST_FINDING_PCT = 8.0
CURE_SLOW_S = 3.0
OVERHEAL_FINDING_PCT = 40.0
WARD_BLEED_FINDING_PCT = 25.0
DEBUFF_BURN_FINDING_PCT = 60.0


def _findings(cur: dict, fits: list[dict], upgrades: list[dict],
              engage: dict | None, archetype: str) -> list[dict]:
    out = []
    if cur["deaths"]:
        cost = engage.get("death_dps_lost", 0) if engage else 0
        out.append({
            "code": "deaths", "severity": "warn",
            "title": f"Died {cur['deaths']}× ({cur['time_dead_s']}s dead)",
            "detail": (f"Estimated {cost:,} damage lost while dead. " if cost else "")
                      + "Staying alive is the cheapest DPS upgrade there is."})
    if cur.get("idle_pct") is not None and cur["idle_pct"] > IDLE_FINDING_PCT:
        out.append({
            "code": "idle_gcds", "severity": "opportunity",
            "title": f"~{cur['idle_pct']:.0f}% of combat time idle",
            "detail": "Estimated from cast starts vs cast+recovery times — more "
                      "casts per fight beats any stat upgrade at this level of idle."})
    if engage and engage.get("avg_engage_delay_s") is not None \
            and engage["avg_engage_delay_s"] > LATE_ENGAGE_S:
        out.append({
            "code": "late_engage", "severity": "opportunity",
            "title": f"Engaging ~{engage['avg_engage_delay_s']:.0f}s after the pull",
            "detail": "Across named fights. Earlier first casts are free damage — "
                      f"roughly your DPS × {engage['avg_engage_delay_s']:.0f}s per fight."})
    for f in fits:
        n = f["noncrit_n"] + f["crit_n"] + f.get("zero_n", 0)
        resists = f.get("resists") or 0
        if n + resists >= 10 and resists / max(n + resists, 1) * 100 > RESIST_FINDING_PCT:
            out.append({
                "code": "resists", "severity": "opportunity",
                "title": f"{f['ability']} resisted "
                         f"{100 * resists / (n + resists):.0f}% of the time",
                "detail": "Check debuffs for that damage school before the burn."})
    if archetype == "healer":
        if cur.get("cure_latency_self_s") is not None \
                and cur["cure_latency_self_s"] > CURE_SLOW_S:
            out.append({
                "code": "cure_latency", "severity": "opportunity",
                "title": f"Cures on you averaged {cur['cure_latency_self_s']:.1f}s",
                "detail": "Only detriments on the logger are measurable from one "
                          "log; group cure latency needs each healer's own upload."})
        if cur.get("overheal_pct") is not None \
                and cur["overheal_pct"] > OVERHEAL_FINDING_PCT:
            out.append({
                "code": "overheal", "severity": "opportunity",
                "title": f"~{cur['overheal_pct']:.0f}% of your healing was "
                         "overheal (estimate)",
                "detail": f"{cur['saves']} of your heals landed in a real "
                          "deep-deficit window. Estimated from HP-deficit "
                          "reconstruction — full HP assumed at each pull. "
                          "Volume on healthy targets is power spent on nothing; "
                          "consider holding group heals for actual damage."})
    wa, wb = cur.get("wards_absorbed") or 0, cur.get("ward_bleedthrough") or 0
    if wa + wb > 0 and 100 * wb / (wa + wb) > WARD_BLEED_FINDING_PCT:
        out.append({
            "code": "ward_bleedthrough", "severity": "opportunity",
            "title": f"{100 * wb / (wa + wb):.0f}% of warded damage punched "
                     "through your wards",
            "detail": "Incoming spikes exceed the ward size. Pre-warding before "
                      "scripted hits (or a higher-tier ward) covers the spike "
                      "instead of splitting it."})
    for d in cur.get("debuffs", []):
        if d["burn_uptime_pct"] is not None and d["casts"] >= 3 \
                and d["burn_uptime_pct"] < DEBUFF_BURN_FINDING_PCT:
            out.append({
                "code": "debuff_burn_coverage", "severity": "opportunity",
                "title": f"{d['ability']} up only {d['burn_uptime_pct']:.0f}% "
                         "of burn windows",
                "detail": f"Overall uptime {d['uptime_pct']:.0f}%. Debuffs pay "
                          "the most exactly when the raid is bursting — "
                          "re-apply going into burns, not after."})
    if upgrades:
        top = upgrades[0]
        out.append({
            "code": "tier_upgrade", "severity": "opportunity",
            "title": f"Best spell upgrade: {top['spell_name']} "
                     f"{top['from_tier']} → {top['to_tier']}",
            "detail": f"Worth about {top['dps_gain']:.1f} DPS at your actual cast "
                      "counts this session."})
    suspect = [f["ability"] for f in fits if f.get("suspect_join")]
    if suspect:
        out.append({
            "code": "suspect_join", "severity": "info",
            "title": f"Ignored {len(suspect)} implausible spell match"
                     f"{'' if len(suspect) == 1 else 'es'}",
            "detail": f"{', '.join(sorted(suspect))}: observed damage is too far "
                      "from any Census tier of the same-named spell — almost "
                      "certainly a different ability sharing the name. Excluded "
                      "from stat marginals and upgrades."})
    low = [f["ability"] for f in fits
           if f["coefficient"] is not None and f["confidence"] == "low"]
    if low:
        out.append({
            "code": "low_confidence", "severity": "info",
            "title": f"Thin samples for {len(low)} abilit"
                     f"{'y' if len(low) == 1 else 'ies'}",
            "detail": "Fits marked low-confidence used <5 non-crit hits. A dummy "
                      "calibration session firms them up."})
    return out


def _calibration_summary(calib: dict) -> dict:
    """What the Calibration page needs to guide the two-parse flow."""
    abmods = sorted({round(p["abmod"]) for d in calib.values() for p in d["points"]})
    return {
        "abmod_points": abmods,
        "two_point": sorted(a for a, d in calib.items() if d["base_true"]),
        "single_point": sorted(a for a, d in calib.items() if not d["base_true"]),
        "stale_stats": sorted(a for a, d in calib.items() if d["stale_stats"]),
    }


def _debuff_summary(fits: list[dict]) -> list[dict]:
    """Median dummy-vs-raid coefficient spread per damage school — the raid
    debuff uplift measurement."""
    import statistics
    by_dtype: dict[str, list[float]] = {}
    for f in fits:
        u = f.get("debuff_uplift")
        if u and f.get("dtype"):
            by_dtype.setdefault(f["dtype"], []).append(u)
    return [{"dtype": d, "uplift": round(statistics.median(v), 2), "abilities": len(v)}
            for d, v in sorted(by_dtype.items())]


def generate(conn, char, session_id: int) -> dict:
    """Build the full coach report for (character, session). Pure computation +
    census cache reads; the only network touch is tier-line caching, which is
    optional."""
    _, doc = _snapshot_doc(conn, char["id"])
    stats = fit.snapshot_stats(doc) if doc else None
    book = fit.spellbook(conn, char, doc) if doc else {}
    archetype = descriptive.archetype_for(char["class"])

    cur, usage = descriptive.currencies(conn, char, session_id, stats or {
        "abilitymod": 0, "basemodifier": 0, "critchance": 0,
        "castpct": 0, "reusepct": 0, "recoverypct": 0}, book)

    report = {
        "engine_version": ENGINE_VERSION,
        "generated_ts": int(time.time()),
        "character": {"id": char["id"], "name": char["name"],
                      "class": char["class"], "level": char["level"]},
        "session_id": session_id,
        "archetype": archetype,
        "stats": stats,
        "currencies": cur,
        "stat_priorities": [], "tier_upgrades": [], "fit": [],
        "calibration": None, "debuff_uplift": [],
        "findings": [], "caveats": [],
    }

    # the logger's own rows from the raid report feed death-cost + engagement
    engage = None
    raid = build_raid_report(conn, session_id)
    for n in raid["night"]:
        if n["name"] == char["name"]:
            engage = n
            break

    if doc is None:
        report["findings"] = [{
            "code": "no_census", "severity": "warn",
            "title": "No Census snapshot for this character",
            "detail": "Refresh Census on the Character page, then regenerate — "
                      "stat priorities and expected-damage fits need your real "
                      "stats and scribed tiers."}] + _findings(cur, [], [], engage,
                                                               archetype)
        return report

    fits = fit.fit_session(conn, char, session_id, stats, book)
    # resist/miss counts join from the rollup table for the findings
    player_id, _ids = fit.logger_entities(conn, session_id, char["name"])
    if player_id is not None:
        res_rows = {r["name"]: r for r in conn.execute(
            "SELECT ab.name, SUM(s.resists) AS resists, SUM(s.misses) AS misses "
            "FROM encounter_ability_stats s JOIN abilities ab ON ab.id = s.ability_id "
            "JOIN encounters e ON e.id = s.encounter_id "
            "WHERE e.session_id=? AND s.entity_id=? AND s.kind='damage' "
            "GROUP BY ab.name", (session_id, player_id))}
        for f in fits:
            r = res_rows.get(f["ability"])
            f["resists"] = r["resists"] if r else 0
            f["misses"] = r["misses"] if r else 0

    calib = fit.calibration_data(conn, char, stats, book,
                                 exclude_session=session_id)
    fit.apply_calibration(fits, calib, stats)
    report["calibration"] = _calibration_summary(calib)
    report["debuff_uplift"] = _debuff_summary(fits)

    try:
        ensure_spell_lines(conn, census_client.shared_client(),
                           [f["crc"] for f in fits if f["crc"]])
    except Exception:
        logging.getLogger("coach").exception("tier-line fetch failed; skipping upgrades")
        report["caveats"].append("Census was unreachable — tier-upgrade advice "
                                 "skipped this run.")

    report["fit"] = fits
    report["stat_priorities"] = replay.stat_marginals(
        fits, usage, book, stats, cur["combat_s"], cur)
    report["tier_upgrades"] = replay.tier_upgrades(conn, fits, stats,
                                                   cur["combat_s"])
    report["findings"] = _findings(cur, fits, report["tier_upgrades"], engage,
                                   archetype)

    sess = conn.execute("SELECT calibration FROM sessions WHERE id=?",
                        (session_id,)).fetchone()
    is_calib_session = bool(sess and sess["calibration"])
    for d in report["debuff_uplift"]:
        if d["uplift"] >= 1.15 and not is_calib_session:
            report["findings"].append({
                "code": "debuff_uplift", "severity": "info",
                "title": f"Raid debuffs multiplied your {d['dtype']} damage "
                         f"~{d['uplift']:.1f}×",
                "detail": "Measured as this session's fit over your dummy-parse "
                          "baseline. Big spreads mean your raid's debuffs on "
                          "that school are working (or your dummy parse is "
                          "stale)."})
    if calib and not report["calibration"]["two_point"] and not is_calib_session:
        report["findings"].append({
            "code": "calibration_second_point", "severity": "opportunity",
            "title": "Add a dummy parse at a different Ability Mod",
            "detail": "All calibration parses share one Ability Mod value, so "
                      "the abmod cap can't be separated from base drift — the "
                      "Ability Mod marginal is still an estimate. Swap abmod "
                      "gear, parse a dummy, and flag it on the Calibration "
                      "page."})
    if report["calibration"]["stale_stats"]:
        report["caveats"].append(
            "Some calibration parses predate stat capture and were fitted at "
            "your CURRENT stats — reflag them after gear changes.")
    return report


def persist(conn, report: dict) -> int:
    with conn:
        return conn.execute(
            "INSERT INTO coach_reports (character_id, session_id, generated_ts, "
            "engine_version, json) VALUES (?,?,?,?,?)",
            (report["character"]["id"], report["session_id"],
             report["generated_ts"], report["engine_version"],
             json_dumps(report))).lastrowid


def latest(conn, session_id: int) -> dict | None:
    row = conn.execute(
        "SELECT json FROM coach_reports WHERE session_id=? "
        "ORDER BY generated_ts DESC, id DESC LIMIT 1", (session_id,)).fetchone()
    return json.loads(row["json"]) if row else None
