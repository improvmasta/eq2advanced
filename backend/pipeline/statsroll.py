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

Two derived stats need explaining because the log does not carry them:

- **Time dead** pairs a player's death with their next revive line ("X is
  revived!" / "You regain consciousness!"), clamped to the fight. No revive
  before the fight ends means dead for the rest of it.
- **Presses** (the "adjusted delay" the UI shows) counts ACTIVATIONS, not
  hits. ACT's Avg Delay divides the swing span by swings, so a DoT ticking six
  times and an AoE hitting five mobs read as eleven presses when the player
  pressed two buttons. An activation is the first hit of a chain on a given
  target, with same-second hits collapsed (AoE) and periodic follow-ups
  dropped (DoT ticks) — see `_activations`.

Healer-quality numbers are ESTIMATES from HP-deficit reconstruction: each
player's deficit accrues from damage lines targeting them (full HP assumed at
encounter start; ward absorbs never touch HP) and drains by heals. A heal
beyond the current deficit is overheal; a heal landing while the deficit is
deep (>= SAVE_DEFICIT_FRACTION of that player's worst deficit this encounter)
is a save. The UI must carry the estimate caveat — the log has no max-HP line.
"""

import statistics
from collections import Counter, defaultdict

from db import json_dumps
from parser.events import (
    F_AOE,
    F_AUTOATTACK,
    F_CRIT,
    F_FLURRY,
    F_INFERRED,
    F_MULTI,
    F_SELF_FOCUS,
    F_ZERO,
)

MELEE_ABILITY = "(melee)"
SAVE_DEFICIT_FRACTION = 0.6

# --- activation ("button press") detection -------------------------------
# Log timestamps are whole seconds, so a 3s tick prints as a 2-4s gap.
TICK_SLACK_S = 1.0      # tolerance around a known/inferred tick period
TICK_MAX_PERIOD_S = 6.0  # nothing in EQ2 ticks slower than this
TICK_MIN_CHAIN = 4      # gaps needed before regularity is evidence of a DoT
TICK_MODE_SHARE = 0.5   # share of gaps the modal one must carry

MELEE_BUCKETS = frozenset(
    ("(melee)", "(multi attack)", "(aoe attack)", "(flurry)"))

# what counts as "this player is up again" — the same list the raid report uses,
# and the same one `pipeline/downs.py` reads a hole in the logger's activity by
ACTION_TYPES = frozenset((
    "damage", "heal", "power", "threat", "dispel", "ward", "cast_flavor"))

_AVOID_COL = {"miss": "misses", "parry": "parries", "riposte": "ripostes",
              "dodge": "dodges", "block": "blocks", "reflect": "reflects",
              "resist": "resists"}

ACTOR_INSERT = (
    "INSERT INTO encounter_actor_stats (encounter_id, entity_id, damage, dps, "
    "heals, overheal_est, save_count, wards_absorbed, ward_bleedthrough, "
    "power_fed, power_drain, damage_taken, deaths, deaths_inferred, "
    "time_dead_s, rez_casts, "
    "intercepts, cure_count, active_s, atk_swings, atk_span_s, presses, "
    "press_span_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")

ABILITY_INSERT = (
    "INSERT INTO encounter_ability_stats (encounter_id, entity_id, ability_id, "
    "kind, casts, hits, crits, misses, resists, parries, ripostes, dodges, "
    "blocks, reflects, zero_hits, total, min, max, median, avg_delay_s, "
    "presses, press_delay_s, dtypes) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def actor_rows(enc_id: int, actor_stats: dict) -> list[tuple]:
    """Rows for ACTOR_INSERT — the one place the column order lives."""
    return [(enc_id, eid, a["damage"], a["dps"], a["heals"], a["overheal_est"],
             a["save_count"], a["wards_absorbed"], a["ward_bleedthrough"],
             a["power_fed"], a["power_drain"], a["damage_taken"], a["deaths"],
             a["deaths_inferred"],
             a["time_dead_s"], a["rez_casts"], a["intercepts"], a["cure_count"],
             a["active_s"], a["atk_swings"], a["atk_span_s"], a["presses"],
             a["press_span_s"])
            for eid, a in actor_stats.items()]


def ability_rows(enc_id: int, ability_stats: dict, ability_id) -> list[tuple]:
    """Rows for ABILITY_INSERT; `ability_id` maps name -> abilities.id."""
    return [(enc_id, src, ability_id(name), kind, st["casts"], st["hits"],
             st["crits"], st["misses"], st["resists"], st["parries"],
             st["ripostes"], st["dodges"], st["blocks"], st["reflects"],
             st["zero_hits"], st["total"], st["min"], st["max"], st["median"],
             st["avg_delay_s"], st["presses"], st["press_delay_s"], st["dtypes"])
            for (src, name, kind), st in ability_stats.items()]


def _melee_bucket(flags: int) -> str:
    if flags & F_MULTI:
        return "(multi attack)"
    if flags & F_AOE:
        return "(aoe attack)"
    if flags & F_FLURRY:
        return "(flurry)"
    return MELEE_ABILITY


def _tick_period(hits: list[tuple[int, int | None]]) -> float | None:
    """Infer the tick period of a periodic effect from its own hits, or None.

    What separates a tick chain from a rotation in the real logs is not how
    regular the average gap is — it is how DOMINANT one gap is. Measured over
    a raid night: Bloodcoil's same-target gaps are 3s 75% of the time and
    Grave Decay's are 1s 86% of the time (EQ2 ticks these every second),
    while Lifetap, a nuke, spreads across 8-14s with no gap over 15%, and
    Dynamism's most common gap is 17% of its gaps. So the modal gap has to
    carry at least half the chain, and appear at least TICK_MIN_CHAIN times,
    before anything is folded away."""
    gaps: list[int] = []
    last: dict[int | None, int] = {}
    for ts, tgt in hits:
        if tgt in last:
            gaps.append(ts - last[tgt])
        last[tgt] = ts
    gaps = [g for g in gaps if g > 0]
    if len(gaps) < TICK_MIN_CHAIN:
        return None
    # ties go to the shorter gap: it is the one that can be a tick
    period, n = min(Counter(gaps).most_common(), key=lambda kv: (-kv[1], kv[0]))
    if period > TICK_MAX_PERIOD_S or n < TICK_MIN_CHAIN:
        return None
    return period if n / len(gaps) >= TICK_MODE_SHARE else None


def _activations(hits: list[tuple[int, int | None]],
                 period: float | None) -> list[int]:
    """Hits -> the seconds the ability was actually ACTIVATED.

    `period` is the known tick period (Census `dmg_period_s`) when we have it;
    otherwise regularity is inferred from the hits themselves. A hit that lands
    within one period of the previous hit ON THE SAME TARGET continues a chain
    (a DoT tick, a multi-hit); anything else is a fresh press. Presses that
    share a second are one press — that is an AoE, not a flurry of casts."""
    if not hits:
        return []
    hits = sorted(hits)
    if period is None:
        period = _tick_period(hits)
    starts: set[int] = set()
    last: dict[int | None, int] = {}
    for ts, tgt in hits:
        prev = last.get(tgt)
        if prev is None or period is None or ts - prev > period + TICK_SLACK_S:
            starts.add(ts)
        last[tgt] = ts
    return sorted(starts)


def roll_encounter(events: list[dict], duration_s: int,
                   periods: dict[str, float] | None = None,
                   proc_names: frozenset[str] = frozenset()) -> tuple[dict, dict]:
    """events: resolved event dicts with entity ids + rollup ids.
    Returns (actor_stats, ability_stats):
      actor_stats[actor_id] -> dict of encounter_actor_stats columns, where
        actor_id is the rollup entity for players/pets and the entity itself
        for mobs/other (incl. the pooled Unknown source)
      ability_stats[(src_id, ability_name, kind)] -> dict of columns

    `periods` maps an ability name to its known tick period (Census
    `dmg_period_s`) and `proc_names` names the abilities that fire themselves —
    both only feed the press count, never damage or healing.
    """
    periods = periods or {}
    actors: dict[int, dict] = defaultdict(lambda: {
        "damage": 0, "heals": 0, "overheal_est": 0, "save_count": 0,
        "wards_absorbed": 0, "ward_bleedthrough": 0, "power_fed": 0,
        "power_drain": 0, "damage_taken": 0, "deaths": 0, "deaths_inferred": 0,
        "rez_casts": 0,
        "intercepts": 0, "cure_count": 0, "first_ts": None, "last_ts": None,
        "atk_swings": 0, "atk_first": None, "atk_last": None,
    })
    # death -> revive pairing per player, and the events that feed press counts
    dead_since: dict[int, int] = {}
    time_dead: dict[int, int] = defaultdict(int)
    # the fight's end, not the last line in it: trailing kill/death lines sit
    # outside the encounter clock, and the raid report clamps to the same edge
    end_ts = events[0]["ts"] + duration_s if events else 0
    deficit: dict[int, int] = defaultdict(int)      # player -> reconstructed HP lost
    max_deficit: dict[int, int] = defaultdict(int)
    heal_records: list[tuple] = []                  # (src_roll, tgt_roll, amt, before)
    abilities: dict[tuple, dict] = defaultdict(lambda: {
        "casts": 0, "hits": 0, "crits": 0, "misses": 0, "resists": 0,
        "parries": 0, "ripostes": 0, "dodges": 0, "blocks": 0, "reflects": 0,
        "zero_hits": 0, "total": 0, "min": None, "max": None,
        "_amounts": [], "_ts": [], "_hits": [], "_dtypes": defaultdict(int),
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

    # ability rows stay at the raw source (pet rows keep their own), so the
    # per-actor press count needs the source -> credited-actor map back
    press_rollup: dict[int, int] = {}

    for ev in events:
        etype = ev["type"]
        src_roll = actor_key(ev, "src")
        src_id = ev.get("src_entity")
        if src_id is not None and src_roll is not None:
            press_rollup[src_id] = src_roll
        # acting again ends the dead clock, exactly as the raid report reads it
        # — a revive line is better evidence, but not every rez prints inside
        # the fight it happened in. THEIR OWN action, though: a swarm keeps
        # swinging over its owner's corpse and every one of those ticks rolls
        # up to them, which stopped the clock the second they died. Measured on
        # session 301 (2026-08-16): Bobby's 27s dead on Mayong's killing pull
        # read as 0s with his pets up, while the 20s on Malkonis read true only
        # because the same swarm had died with him.
        if (src_roll in dead_since and etype in ACTION_TYPES
                and ev.get("src_kind") == "player"):
            time_dead[src_roll] += max(0, ev["ts"] - dead_since.pop(src_roll))
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
                st["_hits"].append((ev["ts"], ev.get("tgt_entity")))
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
                # a parried cast was still a button press (it never lands, so
                # it stays out of _ts and ACT's avg delay)
                st["_hits"].append((ev["ts"], ev.get("tgt_entity")))

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
                st["_hits"].append((ev["ts"], ev.get("tgt_entity")))
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
                st["_hits"].append((ev["ts"], ev.get("tgt_entity")))
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
                st["_hits"].append((ev["ts"], ev.get("tgt_entity")))
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
                st["_hits"].append((ev["ts"], ev.get("tgt_entity")))
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
                st["_hits"].append((ev["ts"], ev.get("tgt_entity")))

        elif etype == "death":
            tgt_roll = ev.get("tgt_rollup")
            if tgt_roll is not None:
                actors[tgt_roll]["deaths"] += 1
                # a death the log never printed, recovered from the hole it
                # left (pipeline/downs.py). Counted with the rest and kept
                # separately so the column can say which ones they were
                if ev["flags"] & F_INFERRED:
                    actors[tgt_roll]["deaths_inferred"] += 1
                dead_since.setdefault(tgt_roll, ev["ts"])

        elif etype == "revive":
            # the landing side of a rez: "X is revived!" / "You regain
            # consciousness!". Ends the dead clock; a revive with nobody dead
            # (rezzed after the fight it died in) is not an error, just noise
            tgt_roll = ev.get("tgt_rollup")
            since = dead_since.pop(tgt_roll, None) if tgt_roll is not None else None
            if since is not None:
                time_dead[tgt_roll] += max(0, ev["ts"] - since)

        elif etype == "intercept":
            # a count, never an amount — the log does not say how much was
            # taken, only that somebody stepped in front of it
            if src_roll is not None:
                a = actors[src_roll]
                touch(a, ev["ts"])
                a["intercepts"] += 1

        elif etype == "kill":
            # a player victim (mind control) is a death for that player; the
            # logger's bare-name pet ("… has killed Bobby") counts as the
            # player too — ACT can't tell them apart, so its Deaths column
            # includes the same-name pet, and parity means we merge it as well
            tgt_roll = ev.get("tgt_rollup")
            tgt_kind = ev.get("tgt_kind")
            if tgt_roll is not None and tgt_kind in ("player", "own_pet"):
                actors[tgt_roll]["deaths"] += 1
                if tgt_kind == "player":
                    dead_since.setdefault(tgt_roll, ev["ts"])

        elif etype == "rez":
            # every healer archetype's rez flavor counts, and an anonymous one
            # ("A resurrection spell is cast on X") has no caster to credit
            if src_roll is not None:
                a = actors[src_roll]
                touch(a, ev["ts"])
                a["rez_casts"] += 1

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

    # still down when the fight ended: dead for the rest of it
    for roll, since in dead_since.items():
        time_dead[roll] += max(0, end_ts - since)

    # presses per ability, then per actor. Autoattack is not a button and a
    # proc fires itself, so neither is a press; both keep their own rows.
    presses: dict[tuple, list[int]] = {}
    actor_presses: dict[int, set[int]] = defaultdict(set)
    for key, st in abilities.items():
        src, name, kind = key
        if kind == "self" or name in MELEE_BUCKETS:
            continue        # a cost (Vampiric Requiem) and autoattack: not buttons
        acts = _activations(st["_hits"], periods.get(name))
        presses[key] = acts
        if name not in proc_names:
            roll = press_rollup.get(src)
            if roll is not None:
                # a set, not a list: two abilities landing in the same second
                # is one moment of activity, and one press per second is the
                # most a whole-second log can honestly claim
                actor_presses[roll].update(acts)

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
            "deaths_inferred": a["deaths_inferred"],
            "time_dead_s": min(time_dead.get(eid, 0), duration),
            "rez_casts": a["rez_casts"],
            "intercepts": a["intercepts"],
            "cure_count": a["cure_count"],
            "active_s": active,
            "atk_swings": a["atk_swings"],
            "atk_span_s": (a["atk_last"] - a["atk_first"]
                           if a["atk_swings"] >= 2 else 0),
            "presses": len(actor_presses.get(eid, ())),
            "press_span_s": (max(actor_presses[eid]) - min(actor_presses[eid])
                             if len(actor_presses.get(eid, ())) >= 2 else 0),
        }

    ability_stats = {}
    for key, st in abilities.items():
        amounts, ts, dtypes = st.pop("_amounts"), st.pop("_ts"), st.pop("_dtypes")
        st.pop("_hits")
        acts = presses.get(key, [])
        st["median"] = round(statistics.median(amounts), 1) if amounts else None
        st["avg_delay_s"] = (round((ts[-1] - ts[0]) / (len(ts) - 1), 2)
                             if len(ts) >= 2 else None)
        st["presses"] = len(acts)
        st["press_delay_s"] = (round((acts[-1] - acts[0]) / (len(acts) - 1), 2)
                               if len(acts) >= 2 else None)
        st["dtypes"] = json_dumps(dict(dtypes)) if dtypes else None
        ability_stats[key] = st
    return actor_stats, ability_stats
