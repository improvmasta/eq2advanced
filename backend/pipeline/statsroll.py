"""Per-encounter rollups: actor stats (credited to the rollup/player entity)
and per-ability stats (kept at the raw source entity so pet rows stay visible).

Pure computation over resolved events; the writer persists the results.

Healer-quality numbers are ESTIMATES from HP-deficit reconstruction: each
player's deficit accrues from damage lines targeting them (full HP assumed at
encounter start; ward absorbs never touch HP) and drains by heals. A heal
beyond the current deficit is overheal; a heal landing while the deficit is
deep (>= SAVE_DEFICIT_FRACTION of that player's worst deficit this encounter)
is a save. The UI must carry the estimate caveat — the log has no max-HP line.
"""

from collections import defaultdict

from parser.events import F_AUTOATTACK, F_CRIT, F_SELF_FOCUS, F_ZERO

MELEE_ABILITY = "(melee)"
SAVE_DEFICIT_FRACTION = 0.6

ACTOR_INSERT = (
    "INSERT INTO encounter_actor_stats (encounter_id, entity_id, damage, dps, "
    "heals, overheal_est, save_count, wards_absorbed, ward_bleedthrough, "
    "power_fed, deaths, rez_casts, active_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)")


def actor_rows(enc_id: int, actor_stats: dict) -> list[tuple]:
    """Rows for ACTOR_INSERT — the one place the column order lives."""
    return [(enc_id, eid, a["damage"], a["dps"], a["heals"], a["overheal_est"],
             a["save_count"], a["wards_absorbed"], a["ward_bleedthrough"],
             a["power_fed"], a["deaths"], a["rez_casts"], a["active_s"])
            for eid, a in actor_stats.items()]


def roll_encounter(events: list[dict], duration_s: int) -> tuple[dict, dict]:
    """events: resolved event dicts with entity ids + rollup ids.
    Returns (actor_stats, ability_stats):
      actor_stats[rollup_id] -> dict of encounter_actor_stats columns
      ability_stats[(src_id, ability_name, kind)] -> dict of columns
    """
    actors: dict[int, dict] = defaultdict(lambda: {
        "damage": 0, "heals": 0, "overheal_est": 0, "save_count": 0,
        "wards_absorbed": 0, "ward_bleedthrough": 0, "power_fed": 0,
        "deaths": 0, "rez_casts": 0, "first_ts": None, "last_ts": None,
    })
    deficit: dict[int, int] = defaultdict(int)      # player -> reconstructed HP lost
    max_deficit: dict[int, int] = defaultdict(int)
    heal_records: list[tuple] = []                  # (src_roll, tgt_roll, amt, before)
    abilities: dict[tuple, dict] = defaultdict(lambda: {
        "casts": 0, "hits": 0, "crits": 0, "misses": 0, "resists": 0,
        "total": 0, "min": None, "max": None,
    })

    def touch(actor: dict, ts: int):
        if actor["first_ts"] is None:
            actor["first_ts"] = ts
        actor["last_ts"] = ts

    for ev in events:
        etype = ev["type"]
        src_roll = ev.get("src_rollup")
        src_id = ev.get("src_entity")
        ability = ev.get("ability") or (MELEE_ABILITY if ev["flags"] & F_AUTOATTACK else None)

        if etype == "damage":
            amt = ev["amount"] or 0
            # any self-hit (focus dtype OR ability costs/bleeds landing on
            # yourself) is not enemy damage — ACT-parity verified vs Aug 1
            self_hit = (ev["flags"] & F_SELF_FOCUS) or (
                src_roll is not None and ev.get("tgt_rollup") == src_roll)
            if src_roll is not None:
                a = actors[src_roll]
                touch(a, ev["ts"])
                if not self_hit:
                    a["damage"] += amt
            tgt_roll = ev.get("tgt_rollup")
            if ev.get("tgt_kind") == "player" and tgt_roll is not None:
                deficit[tgt_roll] += amt
                max_deficit[tgt_roll] = max(max_deficit[tgt_roll], deficit[tgt_roll])
            if src_id is not None and ability:
                # self-inflicted damage gets its own kind so it never reads as
                # enemy damage in the breakdown
                akind = "self" if self_hit else "damage"
                key = (src_id, ability, akind)
                st = abilities[key]
                st["hits"] += 1
                st["total"] += amt
                if ev["flags"] & F_CRIT:
                    st["crits"] += 1
                if not ev["flags"] & F_ZERO:
                    st["min"] = amt if st["min"] is None else min(st["min"], amt)
                    st["max"] = amt if st["max"] is None else max(st["max"], amt)

        elif etype == "avoid":
            if src_id is not None and ability:
                st = abilities[(src_id, ability, "damage")]
                if ev.get("extra", {}).get("how") == "resist":
                    st["resists"] += 1
                else:
                    st["misses"] += 1

        elif etype == "heal":
            amt = ev["amount"] or 0
            if src_roll is not None:
                a = actors[src_roll]
                touch(a, ev["ts"])
                a["heals"] += amt
            tgt_roll = ev.get("tgt_rollup")
            if ev.get("tgt_kind") == "player" and tgt_roll is not None:
                before = deficit[tgt_roll]
                if src_roll is not None:
                    actors[src_roll]["overheal_est"] += max(0, amt - before)
                    heal_records.append((src_roll, tgt_roll, amt, before))
                deficit[tgt_roll] = max(0, before - amt)
            if src_id is not None and ability:
                st = abilities[(src_id, ability, "heal")]
                st["hits"] += 1
                st["total"] += amt
                if ev["flags"] & F_CRIT:
                    st["crits"] += 1
                st["min"] = amt if st["min"] is None else min(st["min"], amt)
                st["max"] = amt if st["max"] is None else max(st["max"], amt)

        elif etype == "ward":
            amt = ev["amount"] or 0
            if src_roll is not None:
                a = actors[src_roll]
                touch(a, ev["ts"])
                a["wards_absorbed"] += amt
                a["ward_bleedthrough"] += (ev.get("extra") or {}).get("bleed", 0)
            if src_id is not None and ability:
                st = abilities[(src_id, ability, "ward")]
                st["hits"] += 1
                st["total"] += amt

        elif etype == "power":
            amt = ev["amount"] or 0
            if src_roll is not None and ev.get("src_entity") != ev.get("tgt_entity"):
                actors[src_roll]["power_fed"] += amt
            if src_id is not None and ability:
                st = abilities[(src_id, ability, "power")]
                st["hits"] += 1
                st["total"] += amt

        elif etype == "cast_flavor":
            # real cast starts (logger only — prepare lines exist for nobody
            # else); credited to the damage-kind row so casts sit beside hits
            if src_id is not None and ability:
                abilities[(src_id, ability, "damage")]["casts"] += 1

        elif etype == "threat":
            if src_id is not None and ability:
                st = abilities[(src_id, ability, "threat")]
                st["hits"] += 1
                st["total"] += abs(ev["amount"] or 0)

        elif etype == "death":
            tgt_roll = ev.get("tgt_rollup")
            if tgt_roll is not None:
                actors[tgt_roll]["deaths"] += 1

        elif etype == "kill":
            # a player victim (mind control) is a death for that player
            tgt_roll = ev.get("tgt_rollup")
            tgt_kind = ev.get("tgt_kind")
            if tgt_roll is not None and tgt_kind == "player":
                actors[tgt_roll]["deaths"] += 1

        elif etype == "rez":
            if src_roll is not None:
                actors[src_roll]["rez_casts"] += 1

    # saves need each target's WORST deficit, known only after the pass
    for src_roll, tgt_roll, amt, before in heal_records:
        worst = max_deficit.get(tgt_roll, 0)
        if worst > 0 and before >= worst * SAVE_DEFICIT_FRACTION:
            actors[src_roll]["save_count"] += 1

    duration = max(duration_s, 1)
    actor_stats = {}
    for eid, a in actors.items():
        active = 0
        if a["first_ts"] is not None:
            active = max(a["last_ts"] - a["first_ts"], 1)
        actor_stats[eid] = {
            "damage": a["damage"],
            "dps": round(a["damage"] / duration, 1),
            "heals": a["heals"],
            "overheal_est": a["overheal_est"],
            "save_count": a["save_count"],
            "wards_absorbed": a["wards_absorbed"],
            "ward_bleedthrough": a["ward_bleedthrough"],
            "power_fed": a["power_fed"],
            "deaths": a["deaths"],
            "rez_casts": a["rez_casts"],
            "active_s": active,
        }
    return actor_stats, dict(abilities)
