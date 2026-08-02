"""Descriptive session currencies for the logger — the per-archetype facts the
advisor reasons over. Everything here is computed from stored events + rollups;
nothing needs Census except the cast/recovery times used for the idle-GCD
estimate (unmatched abilities fall back to a 1.0s cast + 0.5s recovery guess).

Honesty rules baked in:
- casts are estimated from damage-line initiations; for periodic spells a new
  cast only starts after a gap > 2 ticks, so DoT ticks don't count as casts.
- cure latency is only measurable for detriments on the LOGGER (other players'
  afflictions are not in your log) — reported as `cure_latency_self`.
- autoattack share and crit rate cover the player's own hits (pets excluded).
"""

from collections import defaultdict

from coach.fit import (effective_cast_s, effective_recovery_s, logger_entities)
from parser.events import F_AUTOATTACK, F_CRIT, F_SELF_FOCUS

DEFAULT_CAST_S = 1.0
DEFAULT_RECOVERY_S = 0.5
CURE_WINDOW_S = 30
REZ_WINDOW_S = 120

ARCHETYPES = {
    "dps": ("assassin", "ranger", "swashbuckler", "brigand", "wizard", "warlock",
            "necromancer", "conjuror", "beastlord", "bruiser", "monk"),
    "healer": ("templar", "inquisitor", "warden", "fury", "mystic", "defiler",
               "channeler"),
    "utility": ("dirge", "troubador", "coercer", "illusionist"),
    "tank": ("guardian", "berserker", "paladin", "shadowknight"),
}


def archetype_for(cls: str | None) -> str:
    c = (cls or "").lower()
    for arch, classes in ARCHETYPES.items():
        if c in classes:
            return arch
    return "dps"


def _cast_initiations(ts_list: list[int], period_s: float | None) -> list[int]:
    """Collapse a sorted per-encounter hit-timestamp list into cast starts.
    Periodic spells tick on their own; only a gap longer than ~2 ticks means a
    recast. Instant spells: each distinct second is (at most) one cast — AE
    lines share a timestamp."""
    threshold = (period_s or 0) * 2 + 1.5
    casts = []
    last = None
    for ts in ts_list:
        if last is None or ts - last > threshold:
            casts.append(ts)
        last = ts
    return casts


def currencies(conn, char, session_id: int, stats: dict, book: dict):
    """-> (currencies dict, per-ability usage dict for replay)."""
    encounters = conn.execute(
        "SELECT id, name, is_named, started_ts, ended_ts, duration_s "
        "FROM encounters WHERE session_id=? ORDER BY started_ts",
        (session_id,)).fetchall()
    enc_ids = [e["id"] for e in encounters]
    combat_s = sum(e["duration_s"] for e in encounters) or 1
    player_id, roll_ids = logger_entities(conn, session_id, char["name"])

    cur = {
        "combat_s": combat_s,
        "encounter_count": len(encounters),
        "named_count": sum(1 for e in encounters if e["is_named"]),
        "damage": 0, "dps": 0.0, "heals": 0, "hps": 0.0, "wards_absorbed": 0,
        "power_fed": 0, "deaths": 0, "rez_casts": 0,
        "crit_pct": None, "autoattack_pct": None,
        "casts": 0, "cpm": None, "cast_source": None, "casts_estimated": None,
        "idle_pct": None, "time_dead_s": 0,
        "cures_delivered": 0, "cure_latency_self_s": None,
        "rez_delay_s": None,
        "overheal_est": 0, "overheal_pct": None, "saves": 0,
        "ward_bleedthrough": 0, "debuffs": [],
    }
    usage: dict[str, dict] = {}
    if player_id is None or not enc_ids:
        return cur, usage

    ph = ",".join("?" * len(enc_ids))
    tot = conn.execute(
        f"SELECT SUM(damage) d, SUM(heals) h, SUM(overheal_est) oh, "
        f"SUM(save_count) sv, SUM(wards_absorbed) w, SUM(ward_bleedthrough) wb, "
        f"SUM(power_fed) p, SUM(deaths) dt, SUM(rez_casts) r "
        f"FROM encounter_actor_stats WHERE entity_id=? AND encounter_id IN ({ph})",
        [player_id, *enc_ids]).fetchone()
    cur.update({
        "damage": tot["d"] or 0, "heals": tot["h"] or 0,
        "overheal_est": tot["oh"] or 0, "saves": tot["sv"] or 0,
        "wards_absorbed": tot["w"] or 0, "ward_bleedthrough": tot["wb"] or 0,
        "power_fed": tot["p"] or 0,
        "deaths": tot["dt"] or 0, "rez_casts": tot["r"] or 0,
    })
    cur["dps"] = round(cur["damage"] / combat_s, 1)
    cur["hps"] = round(cur["heals"] / combat_s, 1)
    healed = cur["heals"] + cur["overheal_est"]
    if healed:
        cur["overheal_pct"] = round(100 * cur["overheal_est"] / healed, 1)

    kinds = {r["id"]: r["kind"] for r in conn.execute(
        "SELECT id, kind FROM entities WHERE session_id=?", (session_id,))}

    # one pass over the logger-relevant events
    rows = conn.execute(
        "SELECT e.encounter_id, e.ts, e.seq, e.type, e.src_entity, e.tgt_entity, "
        "e.amount, e.flags, a.name AS ability "
        "FROM events e LEFT JOIN abilities a ON a.id = e.ability_id "
        "WHERE e.session_id=? AND e.encounter_id IS NOT NULL AND "
        "(e.src_entity=? OR e.tgt_entity=? OR e.type IN ('death','kill','rez')) "
        "ORDER BY e.ts, e.seq",
        (session_id, player_id, player_id)).fetchall()

    crit_hits = hits = 0
    auto_dmg = 0
    ability_ts: dict[tuple, list[int]] = defaultdict(list)   # (ability, enc) -> [ts]
    flavor_ts: dict[tuple, list[int]] = defaultdict(list)    # (ability|None, enc) -> [ts]
    afflictions: list[int] = []
    cure_latencies: list[float] = []
    death_ts: list[int] = []           # any player death (for rez responsiveness)
    my_death_ts: list[int] = []
    my_action_ts: list[int] = []
    rez_delays: list[float] = []

    for r in rows:
        mine = r["src_entity"] == player_id
        if mine and r["type"] in ("damage", "heal", "power", "threat", "dispel", "ward"):
            my_action_ts.append(r["ts"])
        if mine and r["type"] == "cast_flavor":
            my_action_ts.append(r["ts"])   # preparing a cast is acting (alive)
            flavor_ts[(r["ability"], r["encounter_id"])].append(r["ts"])
            continue
        if r["type"] == "damage" and mine and not (r["flags"] & F_SELF_FOCUS) \
                and r["tgt_entity"] != player_id:
            hits += 1
            if r["flags"] & F_CRIT:
                crit_hits += 1
            if r["flags"] & F_AUTOATTACK:
                auto_dmg += r["amount"] or 0
            if r["ability"]:
                key = (r["ability"], r["encounter_id"])
                ability_ts[key].append(r["ts"])
                u = usage.setdefault(r["ability"], {
                    "damage": 0, "hits": 0, "noncrit_n": 0, "crit_n": 0,
                    "casts": 0, "gaps": []})
                u["damage"] += r["amount"] or 0
                u["hits"] += 1
                u["crit_n" if r["flags"] & F_CRIT else "noncrit_n"] += 1
        elif r["type"] == "affliction" and r["tgt_entity"] == player_id:
            afflictions.append(r["ts"])
        elif r["type"] == "dispel":
            if r["tgt_entity"] == player_id and afflictions:
                # first cure after the most recent uncured affliction on the logger
                dt = r["ts"] - afflictions[0]
                if 0 <= dt <= CURE_WINDOW_S:
                    cure_latencies.append(dt)
                afflictions.clear()
            if mine and kinds.get(r["tgt_entity"]) == "player":
                cur["cures_delivered"] += 1
        elif r["type"] == "death" or (
                r["type"] == "kill" and kinds.get(r["tgt_entity"]) == "player"):
            death_ts.append(r["ts"])
            if r["tgt_entity"] == player_id:
                my_death_ts.append(r["ts"])
        elif r["type"] == "rez" and mine:
            recent = [d for d in death_ts if 0 <= r["ts"] - d <= REZ_WINDOW_S]
            if recent:
                rez_delays.append(r["ts"] - max(recent))

    if hits:
        cur["crit_pct"] = round(100 * crit_hits / hits, 1)
    if cur["damage"]:
        cur["autoattack_pct"] = round(100 * auto_dmg / cur["damage"], 1)
    if cure_latencies:
        cur["cure_latency_self_s"] = round(sum(cure_latencies) / len(cure_latencies), 1)
    if rez_delays:
        cur["rez_delay_s"] = round(sum(rez_delays) / len(rez_delays), 1)

    # time dead: from each of the logger's deaths to their next action (or the
    # end of that encounter)
    enc_of_ts = sorted((e["started_ts"], e["ended_ts"], e["id"]) for e in encounters)
    for d in my_death_ts:
        nxt = next((t for t in my_action_ts if t > d), None)
        end = next((en for st, en, _ in enc_of_ts if st <= d <= en), None)
        until = min(x for x in (nxt, end) if x is not None) if (nxt or end) else d
        cur["time_dead_s"] += max(until - d, 0)

    # casts + inter-cast gaps + busy time (idle-GCD estimate)
    busy = 0.0
    if flavor_ts:
        # Prepare lines are the logger's REAL cast starts — but only spells
        # with a cast bar print them. Instant casts (Lifetap: 344 hits, zero
        # prepares in the fixture) never prepare, so book membership is the
        # discriminator: prepared -> real count; in the spellbook but never
        # prepared -> real instant spell, estimate initiations; in neither ->
        # buff/item proc (Lich's Siphoning), zero casts. DoT ticks can't
        # inflate counts either way, so idle% stops saturating at 0.
        cur["cast_source"] = "log"
        unmapped = 0
        for (ability, _enc_id), ts_list in flavor_ts.items():
            spell = book.get(ability) if ability else None
            cast_s = effective_cast_s(spell["cast_s"], stats) if spell else DEFAULT_CAST_S
            recov = (effective_recovery_s(spell["recovery_s"], stats)
                     if spell else DEFAULT_RECOVERY_S)
            busy += len(ts_list) * (cast_s + recov)
            if ability is None:
                unmapped += len(ts_list)   # unmapped flavor still is a cast
                continue
            u = usage.setdefault(ability, {
                "damage": 0, "hits": 0, "noncrit_n": 0, "crit_n": 0,
                "casts": 0, "gaps": []})
            u["casts"] += len(ts_list)
            u["gaps"] += [b - a for a, b in zip(ts_list, ts_list[1:])]
        prepared = {a for a, _enc in flavor_ts}
        est = 0
        for (ability, enc_id), ts_list in ability_ts.items():
            spell = book.get(ability)
            if ability in prepared or not spell:
                continue
            eff = next((e for e in spell["effects"]
                        if e.get("kind") == "damage" and e.get("period_s")), None)
            casts = _cast_initiations(ts_list, eff["period_s"] if eff else None)
            u = usage[ability]
            u["casts"] += len(casts)
            u["gaps"] += [b - a for a, b in zip(casts, casts[1:])]
            busy += len(casts) * (effective_cast_s(spell["cast_s"], stats)
                                  + effective_recovery_s(spell["recovery_s"], stats))
            est += len(casts)
            # instant debuffs (Vampire Bats) never prepare — their initiation
            # estimates feed debuff-uptime alongside the prepared casts
            flavor_ts[(ability, enc_id)].extend(casts)
        cur["casts_estimated"] = est   # instant-cast estimates inside "log" mode
        cur["casts"] = unmapped + sum(u["casts"] for u in usage.values())
    else:
        cur["cast_source"] = "estimated"
        for (ability, enc_id), ts_list in ability_ts.items():
            spell = book.get(ability)
            period = None
            if spell:
                eff = next((e for e in spell["effects"]
                            if e.get("kind") == "damage" and e.get("period_s")), None)
                period = eff["period_s"] if eff else None
            casts = _cast_initiations(ts_list, period)
            u = usage[ability]
            u["casts"] += len(casts)
            u["gaps"] += [b - a for a, b in zip(casts, casts[1:])]
            cast_s = effective_cast_s(spell["cast_s"], stats) if spell else DEFAULT_CAST_S
            recov = (effective_recovery_s(spell["recovery_s"], stats)
                     if spell else DEFAULT_RECOVERY_S)
            busy += len(casts) * (cast_s + recov)
        cur["casts"] = sum(u["casts"] for u in usage.values())
    if cur["casts"]:
        cur["cpm"] = round(60 * cur["casts"] / combat_s, 1)
    alive_s = max(combat_s - cur["time_dead_s"], 1)
    if cur["casts"]:
        cur["idle_pct"] = round(max(0.0, 100 * (1 - busy / alive_s)), 1)

    if flavor_ts:
        cur["debuffs"] = _debuff_uptime(conn, session_id, encounters,
                                        flavor_ts, book, combat_s)
    return cur, usage


BURN_WINDOW_S = 10
BURN_THRESHOLD = 1.5     # rolling raid DPS this far over the encounter mean


def _is_debuff(spell: dict) -> bool:
    # census writes "Decreases Defense of target by 15.9" — the target string
    # is literally "target" (or "target enemy"/"... encounter"), never "caster"
    for e in spell["effects"]:
        tgt = (e.get("target") or "").lower()
        if e.get("kind") in ("stat", "power") \
                and (e.get("direction") or "").startswith("decrease") \
                and ("target" in tgt or "enemy" in tgt or "encounter" in tgt):
            return True
    return False


def _debuff_uptime(conn, session_id: int, encounters, flavor_ts: dict,
                   book: dict, combat_s: int) -> list[dict]:
    """Per-debuff uptime vs burn windows, from the logger's real cast starts +
    Census durations (expiry lines lack targets — this is application-side, so
    an early target death overstates uptime slightly; surfaced as estimate).
    Burn windows are the raid's hot seconds: rolling damage over BURN_WINDOW_S
    at >= BURN_THRESHOLD x the encounter's own mean."""
    debuffs = {}
    for (ability, _enc), _ts in flavor_ts.items():
        spell = book.get(ability) if ability else None
        if ability and ability not in debuffs and spell \
                and spell.get("duration_s") and _is_debuff(spell):
            debuffs[ability] = spell["duration_s"]
    if not debuffs:
        return []

    enc_win = {e["id"]: (e["started_ts"], e["ended_ts"]) for e in encounters}
    # burn seconds per encounter from raid-wide per-second damage
    per_sec: dict[int, dict[int, int]] = defaultdict(dict)
    for r in conn.execute(
            "SELECT encounter_id, ts, SUM(amount) amt FROM events "
            "WHERE session_id=? AND type='damage' AND encounter_id IS NOT NULL "
            "GROUP BY encounter_id, ts", (session_id,)):
        per_sec[r["encounter_id"]][r["ts"]] = r["amt"] or 0
    burn: set[int] = set()
    for enc_id, (start, end) in enc_win.items():
        secs = per_sec.get(enc_id)
        dur = max(end - start, 1)
        if not secs or dur < BURN_WINDOW_S:
            continue
        threshold = sum(secs.values()) / dur * BURN_WINDOW_S * BURN_THRESHOLD
        for w0 in range(start, end - BURN_WINDOW_S + 2):
            if sum(secs.get(t, 0) for t in range(w0, w0 + BURN_WINDOW_S)) >= threshold:
                burn.update(range(w0, w0 + BURN_WINDOW_S))

    out = []
    for ability, duration_s in sorted(debuffs.items()):
        covered: set[int] = set()
        casts = 0
        for (a, enc_id), ts_list in flavor_ts.items():
            if a != ability or enc_id not in enc_win:
                continue
            start, end = enc_win[enc_id]
            casts += len(ts_list)
            for ts in ts_list:
                covered.update(range(ts, min(int(ts + duration_s), end) + 1))
        out.append({
            "ability": ability, "casts": casts,
            "duration_s": duration_s,
            "uptime_pct": round(100 * len(covered) / combat_s, 1),
            "burn_uptime_pct": (round(100 * len(burn & covered) / len(burn), 1)
                                if burn else None),
        })
    return out
