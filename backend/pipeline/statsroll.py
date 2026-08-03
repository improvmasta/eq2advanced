"""Per-encounter rollups: actor stats (credited to the rollup/player entity;
mobs, mob-owned pets, and the pooled Unknown get their own rows) and
per-ability stats (kept at the raw source entity so pet rows stay visible).

Pure computation over resolved events; the writer persists the results.

ACT-parity notes:
- hits INCLUDE fully-absorbed zero hits (stored separately in zero_hits);
  min/max/median are over the non-zero amounts.
- swings = hits + every avoid kind (miss/parry/riposte/dodge/block/resist);
  reflects tracked but not part of swings.
- autoattack splits into (melee)/(multi attack)/(aoe attack)/(flurry) rows.

Healer-quality numbers are ESTIMATES from HP-deficit reconstruction: each
player's deficit accrues from damage lines targeting them (full HP assumed at
encounter start; ward absorbs never touch HP) and drains by heals. A heal
beyond the current deficit is overheal; a heal landing while the deficit is
deep (>= SAVE_DEFICIT_FRACTION of that player's worst deficit this encounter)
is a save. The UI must carry the estimate caveat — the log has no max-HP line.
"""

import statistics
from collections import defaultdict

from db import json_dumps
from parser.events import (
    F_AOE,
    F_AUTOATTACK,
    F_CRIT,
    F_FLURRY,
    F_MULTI,
    F_SELF_FOCUS,
    F_ZERO,
)

MELEE_ABILITY = "(melee)"
SAVE_DEFICIT_FRACTION = 0.6

_AVOID_COL = {"miss": "misses", "parry": "parries", "riposte": "ripostes",
              "dodge": "dodges", "block": "blocks", "reflect": "reflects",
              "resist": "resists"}

ACTOR_INSERT = (
    "INSERT INTO encounter_actor_stats (encounter_id, entity_id, damage, dps, "
    "heals, overheal_est, save_count, wards_absorbed, ward_bleedthrough, "
    "power_fed, power_drain, damage_taken, deaths, rez_casts, cure_count, "
    "active_s, atk_swings, atk_span_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")

ABILITY_INSERT = (
    "INSERT INTO encounter_ability_stats (encounter_id, entity_id, ability_id, "
    "kind, casts, hits, crits, misses, resists, parries, ripostes, dodges, "
    "blocks, reflects, zero_hits, total, min, max, median, avg_delay_s, dtypes) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def actor_rows(enc_id: int, actor_stats: dict) -> list[tuple]:
    """Rows for ACTOR_INSERT — the one place the column order lives."""
    return [(enc_id, eid, a["damage"], a["dps"], a["heals"], a["overheal_est"],
             a["save_count"], a["wards_absorbed"], a["ward_bleedthrough"],
             a["power_fed"], a["power_drain"], a["damage_taken"], a["deaths"],
             a["rez_casts"], a["cure_count"], a["active_s"], a["atk_swings"],
             a["atk_span_s"])
            for eid, a in actor_stats.items()]


def ability_rows(enc_id: int, ability_stats: dict, ability_id) -> list[tuple]:
    """Rows for ABILITY_INSERT; `ability_id` maps name -> abilities.id."""
    return [(enc_id, src, ability_id(name), kind, st["casts"], st["hits"],
             st["crits"], st["misses"], st["resists"], st["parries"],
             st["ripostes"], st["dodges"], st["blocks"], st["reflects"],
             st["zero_hits"], st["total"], st["min"], st["max"], st["median"],
             st["avg_delay_s"], st["dtypes"])
            for (src, name, kind), st in ability_stats.items()]


def _melee_bucket(flags: int) -> str:
    if flags & F_MULTI:
        return "(multi attack)"
    if flags & F_AOE:
        return "(aoe attack)"
    if flags & F_FLURRY:
        return "(flurry)"
    return MELEE_ABILITY


def roll_encounter(events: list[dict], duration_s: int) -> tuple[dict, dict]:
    """events: resolved event dicts with entity ids + rollup ids.
    Returns (actor_stats, ability_stats):
      actor_stats[actor_id] -> dict of encounter_actor_stats columns, where
        actor_id is the rollup entity for players/pets and the entity itself
        for mobs/other (incl. the pooled Unknown source)
      ability_stats[(src_id, ability_name, kind)] -> dict of columns
    """
    actors: dict[int, dict] = defaultdict(lambda: {
        "damage": 0, "heals": 0, "overheal_est": 0, "save_count": 0,
        "wards_absorbed": 0, "ward_bleedthrough": 0, "power_fed": 0,
        "power_drain": 0, "damage_taken": 0, "deaths": 0, "rez_casts": 0,
        "cure_count": 0, "first_ts": None, "last_ts": None,
        "atk_swings": 0, "atk_first": None, "atk_last": None,
    })
    deficit: dict[int, int] = defaultdict(int)      # player -> reconstructed HP lost
    max_deficit: dict[int, int] = defaultdict(int)
    heal_records: list[tuple] = []                  # (src_roll, tgt_roll, amt, before)
    abilities: dict[tuple, dict] = defaultdict(lambda: {
        "casts": 0, "hits": 0, "crits": 0, "misses": 0, "resists": 0,
        "parries": 0, "ripostes": 0, "dodges": 0, "blocks": 0, "reflects": 0,
        "zero_hits": 0, "total": 0, "min": None, "max": None,
        "_amounts": [], "_ts": [], "_dtypes": defaultdict(int),
    })
    cast_counts: dict[tuple, int] = defaultdict(int)

    def touch(actor: dict, ts: int):
        if actor["first_ts"] is None:
            actor["first_ts"] = ts
        actor["last_ts"] = ts

    def swing(actor: dict, ts: int):
        # ACT's per-combatant Avg Delay: offensive swings (landed, absorbed,
        # or avoided) spaced over first->last swing time
        actor["atk_swings"] += 1
        if actor["atk_first"] is None:
            actor["atk_first"] = ts
        actor["atk_last"] = ts

    def actor_key(ev: dict, side: str) -> int | None:
        """Credit key: the rollup for players/owned pets, the entity itself for
        mobs/other (and the Unknown pool)."""
        roll = ev.get(f"{side}_rollup")
        if roll is not None:
            return roll
        kind = ev.get(f"{side}_kind")
        if kind in ("mob", "other", "swarm_pet", "named_pet"):
            return ev.get(f"{side}_entity")
        return None

    def taken_key(ev: dict) -> int | None:
        """Damage-taken credit: ACT keeps possessive pets ("Tragedy's
        unswerving hammer") as their own combatants on the taken side; only
        the logger's bare-name pet merges into the player."""
        if ev.get("tgt_kind") in ("swarm_pet", "named_pet"):
            return ev.get("tgt_entity")
        return actor_key(ev, "tgt")

    for ev in events:
        etype = ev["type"]
        src_roll = actor_key(ev, "src")
        src_id = ev.get("src_entity")
        flags = ev["flags"]
        ability = ev.get("ability") or (_melee_bucket(flags) if flags & F_AUTOATTACK else None)

        if etype == "damage":
            amt = ev["amount"] or 0
            # any self-hit (focus dtype OR ability costs/bleeds landing on
            # yourself) is not enemy damage — ACT-parity verified vs Aug 1
            self_hit = (flags & F_SELF_FOCUS) or (
                src_roll is not None and actor_key(ev, "tgt") == src_roll)
            if src_roll is not None:
                a = actors[src_roll]
                touch(a, ev["ts"])
                if not self_hit:
                    a["damage"] += amt
                    swing(a, ev["ts"])
            tgt_key = taken_key(ev)
            tgt_roll = ev.get("tgt_rollup")
            if tgt_key is not None and not self_hit:
                # ACT excludes self-inflicted damage from DamageTaken exactly
                # like it does from Damage (Vampiric Requiem et al.)
                actors[tgt_key]["damage_taken"] += amt
            if ev.get("tgt_kind") == "player" and tgt_roll is not None:
                deficit[tgt_roll] += amt
                max_deficit[tgt_roll] = max(max_deficit[tgt_roll], deficit[tgt_roll])
            if src_id is not None and ability:
                # self-inflicted damage gets its own kind so it never reads as
                # enemy damage in the breakdown
                akind = "self" if self_hit else "damage"
                st = abilities[(src_id, ability, akind)]
                st["hits"] += 1
                st["total"] += amt
                st["_ts"].append(ev["ts"])
                if flags & F_CRIT:
                    st["crits"] += 1
                if flags & F_ZERO:
                    st["zero_hits"] += 1
                else:
                    st["_amounts"].append(amt)
                    st["min"] = amt if st["min"] is None else min(st["min"], amt)
                    st["max"] = amt if st["max"] is None else max(st["max"], amt)
                components = (ev.get("extra") or {}).get("components")
                if components:
                    for camt, cdtype in components:
                        st["_dtypes"][cdtype] += camt
                elif ev.get("dtype"):
                    st["_dtypes"][ev["dtype"]] += amt

        elif etype == "avoid":
            if src_roll is not None:
                swing(actors[src_roll], ev["ts"])
            if src_id is not None and ability:
                st = abilities[(src_id, ability, "damage")]
                st[_AVOID_COL.get(ev.get("extra", {}).get("how"), "misses")] += 1

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
                st["_ts"].append(ev["ts"])
                st["_amounts"].append(amt)
                if flags & F_CRIT:
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
            if not (ev.get("extra") or {}).get("paired"):
                # absorb whose mitigated hit printed no line (fully-absorbed
                # DoT tick): the absorbed amount is still damage taken by the
                # warded target — ACT counts it the same way
                tgt_key = taken_key(ev)
                if tgt_key is not None:
                    actors[tgt_key]["damage_taken"] += amt
            if src_id is not None and ability:
                st = abilities[(src_id, ability, "ward")]
                st["hits"] += 1
                st["total"] += amt
                st["_ts"].append(ev["ts"])
                st["_amounts"].append(amt)
                if flags & F_CRIT:
                    st["crits"] += 1

        elif etype == "power":
            # self power gains (Lich, Savant's Intelligence, mana regen) count:
            # ACT's PowerReplenish is every refresh line credited to the caster
            amt = ev["amount"] or 0
            if src_roll is not None:
                actors[src_roll]["power_fed"] += amt
            if src_id is not None and ability:
                st = abilities[(src_id, ability, "power")]
                st["hits"] += 1
                st["total"] += amt
                st["_ts"].append(ev["ts"])
                st["_amounts"].append(amt)
                if flags & F_CRIT:
                    st["crits"] += 1

        elif etype == "power_drain":
            amt = ev["amount"] or 0
            if src_roll is not None:
                actors[src_roll]["power_drain"] += amt

        elif etype == "cast_flavor":
            # real cast starts (logger only — prepare lines exist for nobody
            # else); attached to the ability's busiest row after the pass so a
            # heal spell doesn't grow a phantom damage row
            if src_id is not None and ability:
                cast_counts[(src_id, ability)] += 1

        elif etype == "threat":
            amt = ev["amount"] or 0
            if src_id is not None and ability:
                kind = "threat" if amt >= 0 else "detaunt"
                st = abilities[(src_id, ability, kind)]
                st["hits"] += 1
                st["total"] += abs(amt)
                st["_ts"].append(ev["ts"])
                st["_amounts"].append(abs(amt))
                if flags & F_CRIT:
                    st["crits"] += 1

        elif etype == "dispel":
            # cures: `relieves <Effect> from <T>` (curing detriments) and
            # `dispels <Effect> from <T>` (stripping buffs). ACT counts both
            # per line, credited to the caster, regardless of target kind —
            # verified against the Emerald Halls Cures column (Tragedy 186,
            # Treah Greenroot 417).
            if src_roll is not None:
                actors[src_roll]["cure_count"] += 1
            if src_id is not None and ability:
                st = abilities[(src_id, ability, "cure")]
                st["hits"] += 1
                st["_ts"].append(ev["ts"])

        elif etype == "death":
            tgt_roll = ev.get("tgt_rollup")
            if tgt_roll is not None:
                actors[tgt_roll]["deaths"] += 1

        elif etype == "kill":
            # a player victim (mind control) is a death for that player; the
            # logger's bare-name pet ("… has killed Bobby") counts as the
            # player too — ACT can't tell them apart, so its Deaths column
            # includes the same-name pet, and parity means we merge it as well
            tgt_roll = ev.get("tgt_rollup")
            tgt_kind = ev.get("tgt_kind")
            if tgt_roll is not None and tgt_kind in ("player", "own_pet"):
                actors[tgt_roll]["deaths"] += 1

        elif etype == "rez":
            if src_roll is not None:
                actors[src_roll]["rez_casts"] += 1

    # casts land on the busiest existing row for that ability; a cast with no
    # landed row at all (whiffed/fizzled every time) becomes a bare damage row
    for (src, name), n in cast_counts.items():
        candidates = [k for k in abilities if k[0] == src and k[1] == name]
        key = (max(candidates, key=lambda k: abilities[k]["total"])
               if candidates else (src, name, "damage"))
        abilities[key]["casts"] += n

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
            "power_drain": a["power_drain"],
            "damage_taken": a["damage_taken"],
            "deaths": a["deaths"],
            "rez_casts": a["rez_casts"],
            "cure_count": a["cure_count"],
            "active_s": active,
            "atk_swings": a["atk_swings"],
            "atk_span_s": (a["atk_last"] - a["atk_first"]
                           if a["atk_swings"] >= 2 else 0),
        }

    ability_stats = {}
    for key, st in abilities.items():
        amounts, ts, dtypes = st.pop("_amounts"), st.pop("_ts"), st.pop("_dtypes")
        st["median"] = round(statistics.median(amounts), 1) if amounts else None
        st["avg_delay_s"] = (round((ts[-1] - ts[0]) / (len(ts) - 1), 2)
                             if len(ts) >= 2 else None)
        st["dtypes"] = json_dumps(dict(dtypes)) if dtypes else None
        ability_stats[key] = st
    return actor_stats, ability_stats
