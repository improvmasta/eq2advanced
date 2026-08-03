"""Encounter detail + multi-encounter aggregation: actor table (rolled up to
players; mobs and Unknown keep their own rows) and per-ability breakdown with
pet rows kept visible under their owner.

`GET /encounters/agg?ids=1,2,3` returns the same shape as single-encounter
detail with counters summed and DPS recomputed over the summed duration — it
powers the workspace tree's All / zone / collapsed-trash nodes."""

import json
import statistics

from fastapi import APIRouter, Depends, HTTPException, Query

from db import get_db, row_to_dict, rows_to_dicts
from routers.sessions_api import visible_session
from security import require_user

router = APIRouter(tags=["encounters"])

_SWING_COLS = ("misses", "parries", "ripostes", "dodges", "blocks", "resists")

_ABILITY_SELECT = (
    "SELECT s.encounter_id, s.entity_id, ent.name AS source_name, "
    "ent.kind AS source_kind, ent.rollup_to, ab.name AS ability, s.kind, "
    "s.casts, s.hits, s.crits, s.misses, s.resists, s.parries, s.ripostes, "
    "s.dodges, s.blocks, s.reflects, s.zero_hits, s.total, s.min, s.max, "
    "s.median, s.avg_delay_s, s.dtypes "
    "FROM encounter_ability_stats s "
    "JOIN entities ent ON ent.id = s.entity_id "
    "JOIN abilities ab ON ab.id = s.ability_id ")


def _pet_ability_names(conn) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT ability_name FROM ability_catalog WHERE unit='pet'")}


def _finish_ability_row(row: dict, pet_abilities: set[str]) -> dict:
    """Derived fields every consumer wants: swings, to-hit %, parsed dtypes,
    and the via_pet flag for pet abilities hiding under a player name (the
    conflated-pet case — damage credit stays with the owner, like ACT)."""
    swings = row["hits"] + sum(row[c] or 0 for c in _SWING_COLS)
    row["swings"] = swings
    row["to_hit_pct"] = round(100 * row["hits"] / swings, 2) if swings else None
    row["dtypes"] = json.loads(row["dtypes"]) if row["dtypes"] else None
    row["via_pet"] = (row["source_kind"] == "player"
                      and row["ability"] in pet_abilities)
    return row


@router.get("/encounters/agg")
def encounters_agg(ids: str = Query(...), user=Depends(require_user)):
    """Aggregate N encounters of one session into a single detail payload."""
    try:
        enc_ids = sorted({int(x) for x in ids.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(422, "ids must be a comma-separated list of encounter ids")
    if not enc_ids:
        raise HTTPException(422, "ids is empty")

    conn = get_db()
    ph = ",".join("?" * len(enc_ids))
    encs = conn.execute(
        f"SELECT * FROM encounters WHERE id IN ({ph}) ORDER BY started_ts",
        enc_ids).fetchall()
    if len(encs) != len(enc_ids):
        raise HTTPException(404, "no such encounter")
    sessions = {e["session_id"] for e in encs}
    if len(sessions) != 1:
        raise HTTPException(400, "encounters span multiple sessions")
    sess = visible_session(conn, user, sessions.pop())

    if len(enc_ids) == 1:
        return _detail(conn, encs[0])

    duration = sum(max(e["duration_s"], 1) for e in encs)

    # ---- actors: sum counters, recompute DPS over the summed clock ----
    actor_sum: dict[int, dict] = {}
    for r in conn.execute(
            f"SELECT a.*, e.name, e.kind FROM encounter_actor_stats a "
            f"JOIN entities e ON e.id = a.entity_id "
            f"WHERE a.encounter_id IN ({ph})", enc_ids):
        a = actor_sum.get(r["entity_id"])
        if a is None:
            a = actor_sum[r["entity_id"]] = dict(r)
            a.pop("encounter_id", None)
            continue
        for k in ("damage", "heals", "overheal_est", "save_count", "wards_absorbed",
                  "ward_bleedthrough", "power_fed", "power_drain", "damage_taken",
                  "deaths", "rez_casts", "cure_count", "active_s"):
            a[k] = (a[k] or 0) + (r[k] or 0)
    for a in actor_sum.values():
        a["dps"] = round((a["damage"] or 0) / duration, 1)
    actors = sorted(actor_sum.values(), key=lambda a: -(a["damage"] or 0))

    # ---- abilities: keyed by (source entity, ability, kind) ----
    pet_abilities = _pet_ability_names(conn)
    abil_sum: dict[tuple, dict] = {}
    weighted_delay: dict[tuple, list] = {}
    for r in conn.execute(_ABILITY_SELECT + f"WHERE s.encounter_id IN ({ph})", enc_ids):
        key = (r["entity_id"], r["ability"], r["kind"])
        row = abil_sum.get(key)
        if row is None:
            row = abil_sum[key] = dict(r)
            row.pop("encounter_id", None)
            row["dtypes"] = json.loads(row["dtypes"]) if row["dtypes"] else None
            row["median"] = None            # recomputed from events below
            weighted_delay[key] = [(r["avg_delay_s"], r["hits"])] if r["avg_delay_s"] else []
            continue
        for k in ("casts", "hits", "crits", "misses", "resists", "parries",
                  "ripostes", "dodges", "blocks", "reflects", "zero_hits", "total"):
            row[k] = (row[k] or 0) + (r[k] or 0)
        row["min"] = min(x for x in (row["min"], r["min"]) if x is not None) \
            if (row["min"] is not None or r["min"] is not None) else None
        row["max"] = max(x for x in (row["max"], r["max"]) if x is not None) \
            if (row["max"] is not None or r["max"] is not None) else None
        if r["dtypes"]:
            merged = row["dtypes"] or {}
            for dt, amt in json.loads(r["dtypes"]).items():
                merged[dt] = merged.get(dt, 0) + amt
            row["dtypes"] = merged
        if r["avg_delay_s"]:
            weighted_delay[key].append((r["avg_delay_s"], r["hits"]))

    # true medians need the raw amounts; cheap via the encounter index unless
    # the session is pruned (events deleted) — then medians stay null
    if not sess["pruned"]:
        _KIND_TYPE = {"damage": "damage", "self": "damage", "heal": "heal",
                      "ward": "ward", "power": "power"}
        from parser.events import F_AUTOATTACK
        from pipeline.statsroll import _melee_bucket
        amounts: dict[tuple, list] = {}
        for r in conn.execute(
                f"SELECT e.src_entity, ab.name AS ability, e.type, e.amount, e.flags "
                f"FROM events e LEFT JOIN abilities ab ON ab.id = e.ability_id "
                f"WHERE e.encounter_id IN ({ph}) AND e.amount IS NOT NULL "
                f"AND e.amount != 0 AND e.type IN ('damage','heal','ward','power')",
                enc_ids):
            name = r["ability"] or (
                _melee_bucket(r["flags"]) if r["flags"] & F_AUTOATTACK else None)
            if name:
                amounts.setdefault((r["src_entity"], name, r["type"]), []).append(r["amount"])
        for key, row in abil_sum.items():
            etype = _KIND_TYPE.get(key[2])
            vals = amounts.get((key[0], key[1], etype)) if etype else None
            if vals:
                row["median"] = round(statistics.median(vals), 1)

    for key, row in abil_sum.items():
        pairs = weighted_delay.get(key) or []
        n = sum(h for _, h in pairs)
        row["avg_delay_s"] = round(sum(d * h for d, h in pairs) / n, 2) if n else None
        swings = row["hits"] + sum(row[c] or 0 for c in _SWING_COLS)
        row["swings"] = swings
        row["to_hit_pct"] = round(100 * row["hits"] / swings, 2) if swings else None
        row["via_pet"] = (row["source_kind"] == "player"
                          and row["ability"] in pet_abilities)
    abilities = sorted(abil_sum.values(), key=lambda r: -(r["total"] or 0))

    return {
        "encounter": {
            "id": None, "session_id": sess["id"],
            "zone": encs[0]["zone"] if len({e["zone"] for e in encs}) == 1 else None,
            "name": None, "is_named": 0,
            "started_ts": encs[0]["started_ts"], "ended_ts": encs[-1]["ended_ts"],
            "duration_s": duration, "success": None,
        },
        "encounter_ids": enc_ids,
        "actors": actors,
        "abilities": abilities,
    }


def _detail(conn, enc) -> dict:
    actors = conn.execute(
        "SELECT a.*, e.name, e.kind FROM encounter_actor_stats a "
        "JOIN entities e ON e.id = a.entity_id "
        "WHERE a.encounter_id=? ORDER BY a.damage DESC",
        (enc["id"],),
    ).fetchall()
    pet_abilities = _pet_ability_names(conn)
    abilities = [
        _finish_ability_row(dict(r), pet_abilities)
        for r in conn.execute(
            _ABILITY_SELECT + "WHERE s.encounter_id=? ORDER BY s.total DESC",
            (enc["id"],))
    ]
    return {
        "encounter": row_to_dict(enc),
        "encounter_ids": [enc["id"]],
        "actors": rows_to_dicts(actors),
        "abilities": abilities,
    }


@router.get("/encounters/{encounter_id}")
def encounter_detail(encounter_id: int, user=Depends(require_user)):
    conn = get_db()
    enc = conn.execute("SELECT * FROM encounters WHERE id=?", (encounter_id,)).fetchone()
    if enc is None:
        raise HTTPException(404, "no such encounter")
    visible_session(conn, user, enc["session_id"])
    return _detail(conn, enc)
