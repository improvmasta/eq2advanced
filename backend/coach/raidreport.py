"""Raid Report: per-encounter and per-night contribution-in-context over every
raider in the uploader's log. Computed on demand from stored events + rollups —
no schema changes, so it works on any already-parsed session.

Engagement timing (the proc caveat, verified on the Zylphax pull): pre-pull
buffs fire log lines the player didn't act for — ward absorbs and buff procs
flood in ~1s after the real opener. Classifier rules, in order:
- ward absorbs are NEVER engagement (the cast predates the pull, and the
  absorb line is the mob acting, not the warder);
- the logger's `You prepare` lines are ground truth — a cast start is always
  a deliberate, high-confidence anchor;
- an ability the catalog flags as a proc never anchors, at any time;
- inside the opening window, ability damage from a player who was hit by a
  mob within the last second is a reactive proc (damage shield / "when
  damaged" buffs correlate with incoming swings) and does not anchor;
- any other ability first-action inside the opening window is flagged
  low-confidence; autoattack and pet swings are always deliberate.

What counts as acting (the anchor kinds):
- offensive — landed damage, an ATTEMPTED swing the mob avoided (a miss is
  still an action: it is the swing that counts, not the roll), positive threat;
- support — a heal, a cure, or a rez. A healer never rolls a hostile action in
  the opening seconds, and scoring them as "never engaged" was reading a
  templar's whole pull as absence. Heals inside the opening window carry the
  same low-confidence flag as abilities do: a HoT cast before the pull ticks
  the moment it starts, and nothing in the line says which it was.
`engage_anchor` names which kind fired, so an 8s is readable as "first swing"
or "first heal" rather than a bare number.

Cross-player numbers from one log are coarser than self-coaching — no
cast-start lines exist for other players, so a 4s cast reads as engagement at
the moment it LANDS. The UI carries that caveat.
"""

from collections import defaultdict

from parser.events import F_AUTOATTACK, F_SELF_FOCUS

PROC_SUSPECT_WINDOW_S = 2      # ability first-action this early may be a buff proc
REACTIVE_WINDOW_S = 1          # ability firing this soon after being hit = reactive
REZ_WINDOW_S = 120


def _entities(conn, session_id: int) -> dict[int, dict]:
    return {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, name, kind, rollup_to FROM entities WHERE session_id=?",
        (session_id,))}


def _rollup(ents: dict, eid: int | None) -> int | None:
    """Credit entity: players credit themselves, pets their owner."""
    if eid is None:
        return None
    e = ents.get(eid)
    if e is None:
        return None
    if e["kind"] == "player":
        return eid
    return e["rollup_to"]


def _encounter_report(conn, enc, ents: dict, proc_names: set[str]) -> dict:
    stats = {r["entity_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM encounter_actor_stats WHERE encounter_id=?", (enc["id"],))
        if ents.get(r["entity_id"], {}).get("kind") == "player"}

    first: dict[int, dict] = {}          # player -> first-action record
    deaths: dict[int, list[int]] = defaultdict(list)
    actions: dict[int, list[int]] = defaultdict(list)  # player -> sourced-event ts
    cures: dict[int, int] = defaultdict(int)
    rez_delays: dict[int, list[int]] = defaultdict(list)
    all_death_ts: list[int] = []
    last_incoming: dict[int, int] = {}   # player -> last ts a non-player hit them

    rows = conn.execute(
        "SELECT e.ts, e.type, e.src_entity, e.tgt_entity, e.amount, e.flags, "
        "a.name AS ability FROM events e LEFT JOIN abilities a ON a.id = e.ability_id "
        "WHERE e.encounter_id=? ORDER BY e.ts, e.seq", (enc["id"],))
    for r in rows:
        src_roll = _rollup(ents, r["src_entity"])
        tgt = ents.get(r["tgt_entity"]) if r["tgt_entity"] else None
        src = ents.get(r["src_entity"]) if r["src_entity"] else None
        if (r["type"] == "damage" and tgt is not None and tgt["kind"] == "player"
                and (src is None or src["kind"] not in ("player", "own_pet", "swarm_pet"))):
            tgt_roll = _rollup(ents, r["tgt_entity"])
            if tgt_roll is not None:
                last_incoming[tgt_roll] = r["ts"]
        if src_roll is not None and src_roll in stats:
            if r["type"] in ("damage", "heal", "power", "threat", "dispel", "ward",
                             "cast_flavor"):
                actions[src_roll].append(r["ts"])
            if r["type"] == "cast_flavor" and src_roll not in first:
                # the logger's own cast start — deliberate by definition
                first[src_roll] = {
                    "delay_s": r["ts"] - enc["started_ts"], "anchor": "cast",
                    "confidence": "high",
                }
            hostile_tgt = (tgt is None
                           or tgt["kind"] not in ("player", "own_pet", "swarm_pet"))
            offensive = (
                (r["type"] == "damage" and not (r["flags"] & F_SELF_FOCUS)
                 and hostile_tgt)
                # a swing the mob dodged is still a swing
                or (r["type"] == "avoid" and hostile_tgt)
                or (r["type"] == "threat" and (r["amount"] or 0) > 0))
            # a healer's opener is a heal; treating only hostile actions as
            # engagement scored every priest as absent for the first 10s
            support = r["type"] in ("heal", "dispel", "rez")
            if (offensive or support) and src_roll not in first:
                src_kind = ents[r["src_entity"]]["kind"]
                anchor = ("pet" if src_kind in ("own_pet", "swarm_pet")
                          else "heal" if r["type"] == "heal"
                          else "cure" if r["type"] == "dispel"
                          else "rez" if r["type"] == "rez"
                          else "autoattack" if r["flags"] & F_AUTOATTACK
                          else "ability")
                # a proc can fire an ability or a heal; both are gear acting
                suspect = anchor in ("ability", "heal", "cure")
                delay = r["ts"] - enc["started_ts"]
                if suspect and r["ability"] in proc_names:
                    pass    # a known buff/item proc is never an action
                elif (anchor == "ability" and delay <= PROC_SUSPECT_WINDOW_S
                        and r["ts"] - last_incoming.get(src_roll, -REZ_WINDOW_S)
                        <= REACTIVE_WINDOW_S):
                    pass    # fired while being hit — reactive proc, not an action
                else:
                    first[src_roll] = {
                        "delay_s": delay, "anchor": anchor,
                        "confidence": ("low" if suspect
                                       and delay <= PROC_SUSPECT_WINDOW_S else "high"),
                    }
            if r["type"] == "dispel":
                # relieves + dispels both count, any target (ACT Cures parity)
                cures[src_roll] += 1
            if r["type"] == "rez":
                recent = [d for d in all_death_ts if 0 <= r["ts"] - d <= REZ_WINDOW_S]
                if recent:
                    rez_delays[src_roll].append(r["ts"] - max(recent))
        if r["type"] == "death" or (
                r["type"] == "kill" and tgt is not None and tgt["kind"] == "player"):
            tgt_roll = _rollup(ents, r["tgt_entity"])
            if tgt_roll is not None:
                deaths[tgt_roll].append(r["ts"])
                all_death_ts.append(r["ts"])

    duration = max(enc["duration_s"], 1)
    players = []
    for pid, st in stats.items():
        time_dead = 0
        for d in deaths.get(pid, []):
            nxt = next((t for t in actions.get(pid, []) if t > d), enc["ended_ts"])
            time_dead += max(min(nxt, enc["ended_ts"]) - d, 0)
        alive_s = max(duration - time_dead, 1)
        dps_alive = st["damage"] / alive_s
        f = first.get(pid)
        rd = rez_delays.get(pid)
        players.append({
            "entity_id": pid, "name": ents[pid]["name"],
            "damage": st["damage"], "dps": st["dps"],
            "heals": st["heals"], "overheal_est": st["overheal_est"] or 0,
            "saves": st["save_count"], "wards_absorbed": st["wards_absorbed"],
            "ward_bleedthrough": st["ward_bleedthrough"],
            "power_fed": st["power_fed"], "rez_casts": st["rez_casts"],
            "deaths": len(deaths.get(pid, [])),
            "time_dead_s": time_dead,
            "death_dps_lost": round(dps_alive * time_dead),
            "cures": cures.get(pid, 0),
            "rez_delay_s": round(sum(rd) / len(rd), 1) if rd else None,
            "engage_delay_s": f["delay_s"] if f else None,
            "engage_anchor": f["anchor"] if f else None,
            "engage_confidence": f["confidence"] if f else None,
        })
    players.sort(key=lambda p: -p["damage"])
    raid_damage = sum(p["damage"] for p in players)
    for p in players:
        p["damage_share_pct"] = round(100 * p["damage"] / raid_damage, 1) if raid_damage else 0
    return {
        "encounter": dict(enc),
        "raid_damage": raid_damage,
        "players": players,
    }


def _frozen_encounter_report(conn, cache: dict, session_id: int, enc) -> dict | None:
    """A pruned session's per-encounter rows come from its frozen report —
    events are gone, but encounter ids are stable (pruned sessions never
    reparse). None when no frozen report exists or the id is missing."""
    import json

    if session_id not in cache:
        row = conn.execute("SELECT json FROM raid_reports WHERE session_id=?",
                           (session_id,)).fetchone()
        reports = json.loads(row["json"])["encounters"] if row else []
        cache[session_id] = {r["encounter"]["id"]: r for r in reports}
    return cache[session_id].get(enc["id"])


def build_for_encounters(conn, encounters) -> dict:
    """Report over an arbitrary encounter set — possibly spanning sessions
    (zone runs). Rollup rows are keyed by player NAME, the only cross-session
    identity; entities are resolved per encounter's own session."""
    proc_names = {r[0] for r in conn.execute(
        "SELECT ability_name FROM ability_catalog WHERE proc=1")}
    session_ids = sorted({e["session_id"] for e in encounters})
    pruned = {sid: bool(row[0]) for sid in session_ids
              for row in conn.execute(
                  "SELECT pruned FROM sessions WHERE id=?", (sid,))}
    ents_cache: dict[int, dict] = {}
    frozen_cache: dict[int, dict] = {}
    partial = False

    enc_reports = []
    for e in encounters:
        sid = e["session_id"]
        if pruned.get(sid):
            rep = _frozen_encounter_report(conn, frozen_cache, sid, e)
            if rep is None:
                partial = True
                continue
            enc_reports.append(rep)
        else:
            ents = ents_cache.setdefault(sid, _entities(conn, sid))
            enc_reports.append(_encounter_report(conn, e, ents, proc_names))

    night: dict[str, dict] = {}
    combat_s = sum(max(e["duration_s"], 1) for e in encounters) or 1
    for rep in enc_reports:
        named = rep["encounter"]["is_named"]
        for p in rep["players"]:
            n = night.setdefault(p["name"], {
                "entity_id": p["entity_id"], "name": p["name"],
                "damage": 0, "heals": 0, "overheal_est": 0, "saves": 0,
                "wards_absorbed": 0, "ward_bleedthrough": 0, "power_fed": 0,
                "deaths": 0, "time_dead_s": 0, "death_dps_lost": 0,
                "cures": 0, "rez_casts": 0, "encounters": 0,
                "_engage": [], "_engage_low": 0, "_rez": [], "_anchors": {}})
            for k in ("damage", "heals", "overheal_est", "saves", "wards_absorbed",
                      "ward_bleedthrough", "power_fed", "deaths", "time_dead_s",
                      "death_dps_lost", "cures", "rez_casts"):
                n[k] += p[k]
            n["encounters"] += 1
            if named and p["engage_delay_s"] is not None:
                n["_engage"].append(p["engage_delay_s"])
                if p["engage_confidence"] == "low":
                    n["_engage_low"] += 1
                a = p["engage_anchor"]
                n["_anchors"][a] = n["_anchors"].get(a, 0) + 1
            if p["rez_delay_s"] is not None:
                n["_rez"].append(p["rez_delay_s"])

    raid_damage = sum(n["damage"] for n in night.values())
    rows = []
    for n in night.values():
        eng = n.pop("_engage")
        low = n.pop("_engage_low")
        rez = n.pop("_rez")
        n["dps"] = round(n["damage"] / combat_s, 1)
        healed = n["heals"] + n["overheal_est"]
        n["overheal_pct"] = round(100 * n["overheal_est"] / healed, 1) if healed else None
        n["damage_share_pct"] = round(100 * n["damage"] / raid_damage, 1) if raid_damage else 0
        n["avg_engage_delay_s"] = round(sum(eng) / len(eng), 1) if eng else None
        n["engage_samples"] = len(eng)
        n["engage_low_confidence"] = low
        n["engage_anchors"] = n.pop("_anchors")
        n["avg_rez_delay_s"] = round(sum(rez) / len(rez), 1) if rez else None
        rows.append(n)
    rows.sort(key=lambda n: -n["damage"])

    report = {
        "combat_s": combat_s,
        "raid_damage": raid_damage,
        "night": rows,
        "encounters": enc_reports,
        "caveats": [
            "Cross-player numbers from one log are coarser than self-coaching: "
            "other players' pets conflate into them and their cast starts are "
            "not logged.",
            "Engage is the gap between the pull and a raider's first action — "
            "a hit, an attempted swing, threat, a heal, a cure or a rez. Known "
            "buff/item procs, ward absorbs, and hits that correlate with being "
            "struck never count; anything inside the opening 2s may still be a "
            "pre-pull proc or HoT tick and is flagged low confidence. Only the "
            "uploader's cast STARTS are logged, so for everyone else a slow "
            "cast is dated when it lands.",
            "Overheal and saves come from HP-deficit reconstruction (the log "
            "has no max-HP line): full health is assumed at each pull, ward "
            "absorbs never touch HP. Treat them as estimates.",
        ],
    }
    if partial:
        report["partial"] = True
        report["caveats"].append(
            "Some encounters were pruned without a frozen report and are "
            "missing from these numbers.")
    return report


def build(conn, session_id: int) -> dict:
    """Full raid report for one session: every encounter + the night rollup."""
    encounters = conn.execute(
        "SELECT id, session_id, zone, name, is_named, started_ts, ended_ts, "
        "duration_s, success FROM encounters WHERE session_id=? ORDER BY started_ts",
        (session_id,)).fetchall()
    return {"session_id": session_id, **build_for_encounters(conn, encounters)}
