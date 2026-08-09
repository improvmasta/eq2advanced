"""Encounter detail + multi-encounter aggregation: actor table (rolled up to
players; mobs and Unknown keep their own rows) and per-ability breakdown with
pet rows kept visible under their owner.

`GET /encounters/agg?ids=1,2,3` returns the same shape as single-encounter
detail with counters summed and DPS recomputed over the summed duration — it
powers the workspace tree's All / zone / collapsed-trash nodes.

`GET /encounters/timeline?ids=…`, `GET /encounters/deaths?ids=…` and
`GET /encounters/aoes?ids=…` read the stored EVENTS for the same selection (a
pruned session contributes nothing — its events are gone — and says so).

`GET /encounters/class-stats?ids=…` is the Class tab: the same selection split
by class, each class holding whatever metrics `pipeline/classstats.py` has
registered for it.

`GET /encounters/report?ids=…` is the raid report over the same selection —
what `/zone-runs/{id}/report` gives the raid page, for callers that have
fights rather than a run (the dashboard, mid-night).

Everything here takes the same `ids` list and the same visibility rule: every
session touched must be visible to the caller, unknown ids 404, junk 422."""

import json
import math
import statistics
from bisect import bisect_left

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

import items
import memo
from census import catalog
from census.roster import DEFAULT_WORLD
from coach.descriptive import archetype_for
from db import get_db, row_to_dict
from parser.events import F_AUTOATTACK, F_CRIT, F_SELF_FOCUS
from pipeline import aoes, classstats, loot
from pipeline import classmetrics  # noqa: F401 — importing registers the metrics
from pipeline.classguess import (backfill_session, parse_class_guess, resolve_class,
                                 strong_classes_here)
from pipeline.statsroll import _melee_bucket
from security import optional_user, visible_encounters

router = APIRouter(tags=["encounters"])

_SWING_COLS = ("misses", "parries", "ripostes", "dodges", "blocks", "resists")

# statsroll.actor_key: these keep their own actor row instead of rolling up
_OWN_ROW_KINDS = ("mob", "other", "swarm_pet", "named_pet")

BUCKET_CHOICES = (1, 2, 5, 10, 15, 30, 60)
MAX_BUCKETS = 240
MAX_BUCKET_S = 300
DEATH_WINDOW_S = 12
DEATH_WINDOW_MIN_S = 3
DEATH_WINDOW_MAX_S = 60
DEATH_MAX_ENTRIES = 40

_ABILITY_SELECT = (
    "SELECT s.encounter_id, s.entity_id, ent.name AS source_name, "
    "ent.kind AS source_kind, ent.rollup_to, ab.name AS ability, s.kind, "
    "s.casts, s.hits, s.crits, s.misses, s.resists, s.parries, s.ripostes, "
    "s.dodges, s.blocks, s.reflects, s.zero_hits, s.total, s.min, s.max, "
    "s.median, s.avg_delay_s, s.presses, s.press_delay_s, s.dtypes "
    "FROM encounter_ability_stats s "
    "JOIN entities ent ON ent.id = s.entity_id "
    "JOIN abilities ab ON ab.id = s.ability_id ")


def _pet_ability_names(conn) -> set[str]:
    """Names a badge may call a pet's — a hand-written ruling, else the curated
    seed. The observed sightings that used to land here are candidates now,
    reviewed on the Abilities admin page; see census/catalog.py for what
    believing them cost."""
    return catalog.pet_ability_names(conn)


def _proc_ability_names(conn) -> set[str]:
    """Names that fire on their own. Same ladder, same reason: Census's "may
    cast X" grammar flagged `Berserk`, `Dragon Stance` and `Baffle`, which are
    the class's own buttons."""
    return catalog.proc_ability_names(conn)


def _proc_evidence(conn) -> dict[str, str]:
    """ability name -> what fires it, in words, for the badge's tooltip.

    A ruling knows the actual source — "Fae Fire (fury spell)" — so the badge
    can say it rather than asserting a bare "proc" the reader has to trust."""
    out = {r["ability_name"]: "seen firing with no cast, all night"
           for r in conn.execute(
               "SELECT ability_name FROM ability_catalog WHERE proc=1")}
    for r in conn.execute(
            "SELECT ability_name, grant_kind, grant_name, grant_class "
            "FROM ability_rulings WHERE fires='proc'"):
        who = " ".join(x for x in (r["grant_class"], r["grant_kind"]) if x)
        out[r["ability_name"]] = (
            f"{r['grant_name']} ({who})" if r["grant_name"] and who
            else r["grant_name"] or who or "set by hand")
    return out


def _catalog_classes(conn) -> dict[str, str]:
    """ability name -> catalog class string (Census stores every class that can
    scribe the spell, comma-joined — passed through verbatim)."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT ability_name, class FROM ability_catalog WHERE class IS NOT NULL")}


def _class_fields(guess: dict | None, ts: int | None = None,
                  strong_here: dict | None = None) -> dict:
    """The four class columns every actor row carries, from a parsed
    `entities.class_guess`. Archetype is NULL when the class is unknown —
    `archetype_for` defaults to "dps", which would read as a claim we never
    made.

    `ts` is when the fight happened, and it matters for the handful of names
    that changed class: the row stores every era, and the raid gets the one
    that was true on the night. Without it a six-week log would label its
    Mistmoore's Inner Sanctum run with whichever class Zooey cast more of
    across the whole file. `strong_here` is what THIS selection's abilities
    say, which beats the clock when it is decisive — see `resolve_class`."""
    guess = resolve_class(guess, ts, strong_here)
    if guess is None:
        return {"class": None, "class_confidence": None, "class_source": None,
                "archetype": None}
    cls = guess.get("class")
    return {"class": cls, "class_confidence": guess.get("confidence"),
            "class_source": guess.get("source"),
            "archetype": archetype_for(cls) if cls else None}


def _census_facts(conn, session_ids, names) -> dict:
    """lowercase player name -> the rest of what Census already told us about
    them: `level` and `guild`.

    `census/roster.py` pays for a whole character doc to answer "what class",
    and until now the class and the guild vote were the only things read back
    out of it. The level is in the same cached row, so a raider in the
    drilldown can read as a person — level 70 Paladin, Grit and Gelt — for one
    indexed lookup and no request. Absent for anyone Census never resolved,
    which is the honest answer rather than a zero.

    The world comes from the sessions in the selection, so a name is answered
    by the server the raid was on (`roster_classes` is keyed by both)."""
    if not names:
        return {}
    sph = ",".join("?" * len(session_ids))
    worlds = [r[0] for r in conn.execute(
        f"SELECT DISTINCT COALESCE(c.world_id, {DEFAULT_WORLD}) FROM sessions s "
        f"LEFT JOIN characters c ON c.id = s.character_id WHERE s.id IN ({sph})",
        list(session_ids))] or [DEFAULT_WORLD]
    lowered = [n.lower() for n in names]
    ph = ",".join("?" * len(lowered))
    wph = ",".join("?" * len(worlds))
    return {r["name_lower"]: {"level": r["level"], "guild": r["guild_name"]}
            for r in conn.execute(
                f"SELECT name_lower, level, guild_name FROM roster_classes "
                f"WHERE found=1 AND world_id IN ({wph}) AND name_lower IN ({ph})",
                worlds + lowered)}


def _add_census_facts(conn, actors, session_ids) -> None:
    """Hang `_census_facts` on the player rows, in place."""
    players = [a["name"] for a in actors if a["kind"] == "player"]
    facts = _census_facts(conn, session_ids, players)
    for a in actors:
        if a["kind"] != "player":
            continue
        f = facts.get(a["name"].lower()) or {}
        a["level"] = f.get("level")
        a["guild"] = f.get("guild")


_STRONG_HERE_SQL = (
    "SELECT DISTINCT e.name AS name, e.kind AS kind, ab.name AS ability "
    "FROM encounter_ability_stats s "
    "JOIN entities e ON e.id = s.entity_id "
    "JOIN abilities ab ON ab.id = s.ability_id "
    "WHERE e.kind='player' AND s.encounter_id IN ({ph})")


def _strong_here(conn, enc_ids) -> dict:
    """What the fights on screen prove about each raider's class, keyed the
    same way the actor rows are. One extra indexed read per selection; it is
    the difference between labelling a raid and labelling a career."""
    from pipeline.classguess import _catalog
    muted, cls_of = _catalog(conn)
    ph = ",".join("?" * len(enc_ids))
    rows = [(_ent_key(r["name"], r["kind"]), r["ability"])
            for r in conn.execute(_STRONG_HERE_SQL.format(ph=ph), enc_ids)]
    return strong_classes_here(rows, muted, cls_of)


def _avg_delay(a: dict) -> float | None:
    """ACT-style per-combatant Avg Delay: swing span over swing gaps. Sums
    exactly across encounters (sum of spans / sum of gaps)."""
    swings, span = a.get("atk_swings") or 0, a.get("atk_span_s") or 0
    return round(span / (swings - 1), 2) if swings >= 2 and span else None


def _avg_delay_adj(a: dict) -> float | None:
    """The same shape over PRESSES instead of swings — a DoT's ticks and an
    AoE's extra targets are one activation each, so this reads as "how often
    did they press something" rather than "how often did something land".
    See pipeline/statsroll for how an activation is decided."""
    presses, span = a.get("presses") or 0, a.get("press_span_s") or 0
    return round(span / (presses - 1), 2) if presses >= 2 and span else None


def _ent_key(name: str, kind: str) -> str:
    """Cross-session actor identity: entity ids are session-scoped, so merged
    payloads key actors by name+kind instead."""
    return f"{name}|{kind}"


def _entity_keys(conn, session_ids: list[int]) -> dict[int, str]:
    """entity id -> merge key, for every entity in the given sessions."""
    ph = ",".join("?" * len(session_ids))
    return {r["id"]: _ent_key(r["name"], r["kind"]) for r in conn.execute(
        f"SELECT id, name, kind FROM entities WHERE session_id IN ({ph})",
        session_ids)}


def _scribed_classes(conn) -> dict[str, str]:
    """ability name -> classes that scribe it (catalog rows built from a spell
    record). A proc-only row's `class` names whoever's buff fires it, which is
    a different question — see census/catalog.py."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT ability_name, class FROM ability_catalog "
        "WHERE scribed=1 AND class IS NOT NULL AND class != ''")}


def _proc_flag(row: dict, proc_abilities: set[str], scribed_classes: dict,
               actor_class: str | None) -> bool:
    """Did this row fire on its own?

    The catalog's proc flag is a claim about a NAME — "some item or buff can
    cast this" — and Census hands out that claim generously: a spell that any
    proc effect references gets flagged for everyone, including the classes
    that scribe it and press it on purpose. Believing the name alone is what
    marks a bard's own combat art as gear. So the name only stands when this
    actor gives no evidence they cast it:

    - `casts` counts prepare lines, which procs never print. A cast is proof.
    - an ability in the actor's own class spellbook is theirs to press. Only a
      SCRIBED catalog row answers that; curated procs carry no class list at
      all, so they stay flagged for everyone.
    """
    if row["ability"] not in proc_abilities:
        return False
    if row.get("casts"):
        return False
    classes = scribed_classes.get(row["ability"])
    if actor_class and classes:
        if actor_class in {c.strip() for c in classes.split(",")}:
            return False
    return True


def _finish_ability_row(row: dict, pet_abilities: set[str],
                        proc_abilities: set[str], ability_classes: dict,
                        actor_class: str | None = None,
                        scribed_classes: dict | None = None,
                        proc_evidence: dict | None = None) -> dict:
    """Derived fields every consumer wants: swings, to-hit %, parsed dtypes,
    the via_pet flag for pet abilities hiding under a player name (the
    conflated-pet case — damage credit stays with the owner, like ACT), and the
    catalog's proc flag + class."""
    swings = row["hits"] + sum(row[c] or 0 for c in _SWING_COLS)
    row["swings"] = swings
    row["to_hit_pct"] = round(100 * row["hits"] / swings, 2) if swings else None
    row["dtypes"] = json.loads(row["dtypes"]) if row["dtypes"] else None
    row["via_pet"] = (row["source_kind"] == "player"
                      and row["ability"] in pet_abilities)
    row["proc"] = _proc_flag(row, proc_abilities, scribed_classes or {}, actor_class)
    row["proc_why"] = (proc_evidence or {}).get(row["ability"]) if row["proc"] else None
    row["ability_class"] = ability_classes.get(row["ability"])
    return row


def _selection(conn, user, ids: str):
    """Shared front door for every ?ids= endpoint: parse, load, authorize.
    -> (enc_ids, encounter rows in started_ts order, session_ids, sess_of).

    Authorization is per ENCOUNTER, not per session (`security.visible_encounters`):
    a raid shared with a group must expose that raid's fights and nothing else
    out of the same uploaded file."""
    try:
        enc_ids = sorted({int(x) for x in ids.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(422, "ids must be a comma-separated list of encounter ids")
    if not enc_ids:
        raise HTTPException(422, "ids is empty")
    ph = ",".join("?" * len(enc_ids))
    encs = conn.execute(
        f"SELECT * FROM encounters WHERE id IN ({ph}) ORDER BY started_ts, id",
        enc_ids).fetchall()
    if len(encs) != len(enc_ids):
        raise HTTPException(404, "no such encounter")
    sess_of = visible_encounters(conn, user, encs)
    session_ids = sorted(sess_of)
    for sid, sess in sess_of.items():
        # existing parses light up without a reparse; one indexed lookup when
        # there is nothing to do, and nothing at all once a session is done
        backfill_session(conn, sid, cache=(sess["status"] == "ready"))
    return enc_ids, encs, session_ids, sess_of


@router.get("/encounters/agg")
def encounters_agg(ids: str = Query(...), user=Depends(optional_user)):
    """Aggregate N encounters into a single detail payload. Encounters may span
    sessions (zone runs are cross-file): actors merge by name+kind — entity ids
    are session-scoped — and every session must be visible to the user.

    Authorization happens here, on every request; only the computed payload is
    memoized (memo.py), keyed by the encounter set and dropped by any write."""
    conn = get_db()
    enc_ids, encs, session_ids, sess_of = _selection(conn, user, ids)
    return memo.get_or_build(
        ("agg", tuple(enc_ids)),
        lambda: _agg(conn, enc_ids, encs, session_ids, sess_of))


def _agg(conn, enc_ids, encs, session_ids, sess_of):
    ph = ",".join("?" * len(enc_ids))

    if len(enc_ids) == 1:
        return _detail(conn, encs[0])

    duration = sum(max(e["duration_s"], 1) for e in encs)
    # one clock for the whole selection's class lookup — a run is contiguous,
    # so its first fight dates every row in it
    when = min(e["started_ts"] for e in encs)
    strong_here = _strong_here(conn, enc_ids)
    ent_key_of = _entity_keys(conn, session_ids)

    # ---- actors: sum counters by name+kind, recompute DPS over the summed clock ----
    actor_sum: dict[str, dict] = {}
    best_guess: dict[str, dict] = {}
    for r in conn.execute(
            f"SELECT a.*, e.name, e.kind, e.class_guess FROM encounter_actor_stats a "
            f"JOIN entities e ON e.id = a.entity_id "
            f"WHERE a.encounter_id IN ({ph})", enc_ids):
        key = _ent_key(r["name"], r["kind"])
        # sessions can disagree (one has Census ground truth, another only a
        # thin inference) — the most confident guess wins the merged row
        guess = parse_class_guess(r["class_guess"])
        if guess and (guess.get("confidence") or 0) > (
                (best_guess.get(key) or {}).get("confidence") or -1):
            best_guess[key] = guess
        a = actor_sum.get(key)
        if a is None:
            a = actor_sum[key] = dict(r)
            a.pop("encounter_id", None)
            a.pop("class_guess", None)
            a["key"] = key
            a["entity_ids"] = [r["entity_id"]]
            continue
        if r["entity_id"] not in a["entity_ids"]:
            a["entity_ids"].append(r["entity_id"])
        for k in ("damage", "heals", "overheal_est", "save_count", "wards_absorbed",
                  "ward_bleedthrough", "power_fed", "power_drain", "damage_taken",
                  "deaths", "time_dead_s", "rez_casts", "intercepts",
                  "cure_count", "active_s", "atk_swings", "atk_span_s",
                  "presses", "press_span_s"):
            a[k] = (a[k] or 0) + (r[k] or 0)
    for key, a in actor_sum.items():
        a["dps"] = round((a["damage"] or 0) / duration, 1)
        a["avg_delay_s"] = _avg_delay(a)
        a["avg_delay_adj_s"] = _avg_delay_adj(a)
        a.update(_class_fields(best_guess.get(key), when, strong_here.get(key)))
    actors = sorted(actor_sum.values(), key=lambda a: -(a["damage"] or 0))
    _add_census_facts(conn, actors, session_ids)
    # the proc flag is answered per actor: an ability in their own spellbook
    # is theirs to press, whatever the catalog says about the name
    class_of = {a["key"]: a["class"] for a in actors}

    # ---- abilities: keyed by (source name+kind, ability, kind) ----
    pet_abilities = _pet_ability_names(conn)
    proc_abilities = _proc_ability_names(conn)
    ability_classes = _catalog_classes(conn)
    scribed = _scribed_classes(conn)
    proc_why = _proc_evidence(conn)
    abil_sum: dict[tuple, dict] = {}
    weighted_delay: dict[tuple, list] = {}
    weighted_press: dict[tuple, list] = {}
    for r in conn.execute(_ABILITY_SELECT + f"WHERE s.encounter_id IN ({ph})", enc_ids):
        src_key = _ent_key(r["source_name"], r["source_kind"])
        key = (src_key, r["ability"], r["kind"])
        row = abil_sum.get(key)
        if row is None:
            row = abil_sum[key] = dict(r)
            row.pop("encounter_id", None)
            row["source_key"] = src_key
            # players credit themselves — their DB rollup_to is NULL
            row["rollup_key"] = ent_key_of.get(r["rollup_to"]) or (
                src_key if r["source_kind"] == "player" else None)
            row["dtypes"] = json.loads(row["dtypes"]) if row["dtypes"] else None
            row["median"] = None            # recomputed from events below
            weighted_delay[key] = [(r["avg_delay_s"], r["hits"])] if r["avg_delay_s"] else []
            weighted_press[key] = (
                [(r["press_delay_s"], r["presses"])] if r["press_delay_s"] else [])
            continue
        for k in ("casts", "hits", "crits", "misses", "resists", "parries",
                  "ripostes", "dodges", "blocks", "reflects", "zero_hits",
                  "total", "presses"):
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
        if r["press_delay_s"]:
            weighted_press[key].append((r["press_delay_s"], r["presses"]))

    # true medians need the raw amounts; cheap via the encounter index unless
    # a session is pruned (events deleted) — those encounters' amounts are gone
    live_enc_ids = [e["id"] for e in encs if not sess_of[e["session_id"]]["pruned"]]
    if live_enc_ids:
        _KIND_TYPE = {"damage": "damage", "self": "damage", "heal": "heal",
                      "ward": "ward", "power": "power"}
        lph = ",".join("?" * len(live_enc_ids))
        amounts: dict[tuple, list] = {}
        for r in conn.execute(
                f"SELECT e.src_entity, ab.name AS ability, e.type, e.amount, e.flags "
                f"FROM events e LEFT JOIN abilities ab ON ab.id = e.ability_id "
                f"WHERE e.encounter_id IN ({lph}) AND e.amount IS NOT NULL "
                f"AND e.amount != 0 AND e.type IN ('damage','heal','ward','power')",
                live_enc_ids):
            name = r["ability"] or (
                _melee_bucket(r["flags"]) if r["flags"] & F_AUTOATTACK else None)
            if name:
                amounts.setdefault(
                    (ent_key_of.get(r["src_entity"]), name, r["type"]), []
                ).append(r["amount"])
        for key, row in abil_sum.items():
            etype = _KIND_TYPE.get(key[2])
            vals = amounts.get((key[0], key[1], etype)) if etype else None
            if vals:
                row["median"] = round(statistics.median(vals), 1)

    for key, row in abil_sum.items():
        pairs = weighted_delay.get(key) or []
        n = sum(h for _, h in pairs)
        row["avg_delay_s"] = round(sum(d * h for d, h in pairs) / n, 2) if n else None
        ppairs = weighted_press.get(key) or []
        pn = sum(p for _, p in ppairs)
        row["press_delay_s"] = (
            round(sum(d * p for d, p in ppairs) / pn, 2) if pn else None)
        swings = row["hits"] + sum(row[c] or 0 for c in _SWING_COLS)
        row["swings"] = swings
        row["to_hit_pct"] = round(100 * row["hits"] / swings, 2) if swings else None
        row["via_pet"] = (row["source_kind"] == "player"
                          and row["ability"] in pet_abilities)
        row["proc"] = _proc_flag(row, proc_abilities, scribed,
                                 class_of.get(row["source_key"]))
        row["proc_why"] = proc_why.get(row["ability"]) if row["proc"] else None
        row["ability_class"] = ability_classes.get(row["ability"])
    abilities = sorted(abil_sum.values(), key=lambda r: -(r["total"] or 0))

    return {
        "encounter": {
            "id": None,
            "session_id": session_ids[0] if len(session_ids) == 1 else None,
            "zone": encs[0]["zone"] if len({e["zone"] for e in encs}) == 1 else None,
            "name": None, "is_named": 0,
            "started_ts": encs[0]["started_ts"], "ended_ts": encs[-1]["ended_ts"],
            "duration_s": duration, "success": None,
        },
        "encounter_ids": enc_ids,
        "session_ids": session_ids,
        "actors": actors,
        "abilities": abilities,
    }


# ---------------------------------------------------------------- timeline ---

def _entity_meta(conn, session_ids: list[int]) -> dict:
    ph = ",".join("?" * len(session_ids))
    return {r["id"]: r for r in conn.execute(
        f"SELECT id, name, kind, rollup_to FROM entities "
        f"WHERE session_id IN ({ph})", session_ids)}


def _credit_maps(meta: dict) -> tuple[dict, dict, dict]:
    """Re-derive statsroll's three credit rules from the stored entity rows
    (the rollup ids the roller saw live: players self-credit, so their DB
    `rollup_to` is NULL).

    -> (credit, rollup, taken)
      credit  = statsroll.actor_key — outgoing credit; mobs/Unknown/pets that
                belong to nobody keep their own row
      rollup  = the player behind an entity (pets fold into their owner), NULL
                for anything that isn't a player or a player's pet
      taken   = statsroll.taken_key — possessive pets take their own damage
    """
    credit, rollup, taken = {}, {}, {}
    for eid, r in meta.items():
        roll, kind = r["rollup_to"], r["kind"]
        if roll is not None:
            credit[eid] = roll
        elif kind == "player":
            credit[eid] = eid
        elif kind in _OWN_ROW_KINDS:
            credit[eid] = eid
        else:
            credit[eid] = None
        rollup[eid] = roll if roll is not None else (eid if kind == "player" else None)
        taken[eid] = eid if kind in ("swarm_pet", "named_pet") else credit[eid]
    return credit, rollup, taken


def _pick_bucket(bucket: str, duration_s: int) -> int:
    """`auto` = the finest resolution that stays under MAX_BUCKETS columns; an
    explicit value is clamped to 1..MAX_BUCKET_S."""
    if bucket in (None, "", "auto"):
        for b in BUCKET_CHOICES:
            if math.ceil(duration_s / b) <= MAX_BUCKETS:
                return b
        return BUCKET_CHOICES[-1]
    try:
        return max(1, min(MAX_BUCKET_S, int(bucket)))
    except ValueError:
        raise HTTPException(422, "bucket must be an integer or 'auto'")


@router.get("/encounters/timeline")
def encounters_timeline(ids: str = Query(...), bucket: str = Query("auto"),
                        user=Depends(optional_user)):
    """Per-actor damage/heal/taken series over a CONCATENATED clock: the
    selected encounters are laid end to end in `started_ts` order with the
    between-fight gaps removed, so a multi-fight selection reads as continuous
    combat and the total equals the summed `duration_s` used by /agg."""
    conn = get_db()
    enc_ids, encs, session_ids, sess_of = _selection(conn, user, ids)

    duration = sum(max(e["duration_s"], 1) for e in encs)
    bucket_s = _pick_bucket(bucket, duration)
    bucket_count = max(1, math.ceil(duration / bucket_s))

    segments, offset_of, span_of = [], {}, {}
    offset = 0
    live_enc_ids, pruned_encounters = [], 0
    for e in encs:
        dur = max(e["duration_s"], 1)
        offset_of[e["id"]] = offset
        span_of[e["id"]] = (e["started_ts"], dur)
        segments.append({
            "encounter_id": e["id"], "name": e["name"], "is_named": e["is_named"],
            "start_bucket": min(offset // bucket_s, bucket_count - 1),
            "end_bucket": min((offset + dur - 1) // bucket_s, bucket_count - 1),
            "duration_s": dur,
        })
        offset += dur
        if sess_of[e["session_id"]]["pruned"]:
            pruned_encounters += 1
        else:
            live_enc_ids.append(e["id"])

    meta = _entity_meta(conn, session_ids)
    credit, rollup, taken_key = _credit_maps(meta)
    series: dict[str, dict] = {}
    markers: list[dict] = []

    def track(eid: int | None) -> dict | None:
        if eid is None or eid not in meta:
            return None
        key = _ent_key(meta[eid]["name"], meta[eid]["kind"])
        s = series.get(key)
        if s is None:
            s = series[key] = {
                "key": key, "name": meta[eid]["name"], "kind": meta[eid]["kind"],
                "damage": [0] * bucket_count, "heals": [0] * bucket_count,
                "taken": [0] * bucket_count,
            }
        return s

    if live_enc_ids:
        lph = ",".join("?" * len(live_enc_ids))
        for r in conn.execute(
                f"SELECT encounter_id, ts, type, src_entity, tgt_entity, amount, "
                f"flags, extra FROM events WHERE encounter_id IN ({lph}) "
                f"AND type IN ('damage','heal','ward','death','kill') "
                f"ORDER BY ts, seq", live_enc_ids):
            started, dur = span_of[r["encounter_id"]]
            rel = min(max(r["ts"] - started, 0), dur - 1)
            b = min((offset_of[r["encounter_id"]] + rel) // bucket_s, bucket_count - 1)
            etype, amt = r["type"], r["amount"] or 0
            src_roll = credit.get(r["src_entity"])

            if etype == "damage":
                # self-inflicted damage is neither damage nor damage taken (ACT)
                self_hit = (r["flags"] & F_SELF_FOCUS) or (
                    src_roll is not None and credit.get(r["tgt_entity"]) == src_roll)
                if self_hit:
                    continue
                s = track(src_roll)
                if s is not None:
                    s["damage"][b] += amt
                s = track(taken_key.get(r["tgt_entity"]))
                if s is not None:
                    s["taken"][b] += amt
            elif etype == "heal":
                s = track(src_roll)
                if s is not None:
                    s["heals"][b] += amt
            elif etype == "ward":
                s = track(src_roll)
                if s is not None:
                    s["heals"][b] += amt
                if not (json.loads(r["extra"]) if r["extra"] else {}).get("paired"):
                    # a fully-absorbed tick prints no hit line; the absorbed
                    # amount is still damage the target took
                    s = track(taken_key.get(r["tgt_entity"]))
                    if s is not None:
                        s["taken"][b] += amt
            else:
                # death, or a kill whose victim rolls up to a player (incl. the
                # logger's bare-name pet) — same rule statsroll counts Deaths by
                tgt_roll = rollup.get(r["tgt_entity"])
                if tgt_roll is None or (
                        etype == "kill"
                        and meta[r["tgt_entity"]]["kind"] not in ("player", "own_pet")):
                    continue
                s = track(tgt_roll)
                if s is not None:
                    markers.append({
                        "bucket": b, "type": "death", "key": s["key"],
                        "name": s["name"], "encounter_id": r["encounter_id"]})

    rows = [s for s in series.values()
            if any(s["damage"]) or any(s["heals"]) or any(s["taken"])]
    rows.sort(key=lambda s: -sum(s["damage"]))
    return {
        "bucket_s": bucket_s,
        "bucket_count": bucket_count,
        "duration_s": duration,
        "segments": segments,
        "series": rows,
        "markers": markers,
        "pruned": pruned_encounters == len(enc_ids),
        "pruned_encounters": pruned_encounters,
    }


# ------------------------------------------------------------------ deaths ---

def _ability_name(row) -> str | None:
    return row["ability"] or (
        _melee_bucket(row["flags"]) if row["flags"] & F_AUTOATTACK else None)


def _window_slice(entries: list[dict], stamps: list[int], death_ts: int,
                  window: int) -> tuple[list[dict], int, bool]:
    """The entries in [death_ts - window, death_ts] — both edges inclusive, so
    a window reaches events landing exactly `window` seconds out — their summed
    amount (over everything in range, not just what survives the cap), and
    whether the list was truncated to the most recent DEATH_MAX_ENTRIES."""
    lo = bisect_left(stamps, death_ts - window)
    hi = bisect_left(stamps, death_ts + 1)
    chunk = entries[lo:hi]
    total = sum(e["amount"] for e in chunk)
    truncated = len(chunk) > DEATH_MAX_ENTRIES
    if truncated:
        chunk = chunk[-DEATH_MAX_ENTRIES:]
    out = []
    for e in chunk:
        row = {k: v for k, v in e.items() if k != "ts"}
        out.append({"t": float(e["ts"] - death_ts), **row})
    return out, total, truncated


@router.get("/encounters/deaths")
def encounters_deaths(ids: str = Query(...), window: int = Query(DEATH_WINDOW_S),
                      user=Depends(optional_user)):
    """Death recap: what hit each player, and who was healing them, in the
    `window` seconds before they died."""
    conn = get_db()
    enc_ids, encs, session_ids, sess_of = _selection(conn, user, ids)
    window = max(DEATH_WINDOW_MIN_S, min(DEATH_WINDOW_MAX_S, window))

    live, pruned_encounters = [], 0
    name_of_enc = {}
    for e in encs:
        name_of_enc[e["id"]] = e["name"]
        if sess_of[e["session_id"]]["pruned"]:
            pruned_encounters += 1
        else:
            live.append(e["id"])
    if not live:
        return {"window_s": window, "deaths": [],
                "pruned_encounters": pruned_encounters}

    meta = _entity_meta(conn, session_ids)
    credit, rollup, taken_key = _credit_maps(meta)
    lph = ",".join("?" * len(live))

    deaths = []
    for r in conn.execute(
            f"SELECT encounter_id, ts, type, tgt_entity FROM events "
            f"WHERE encounter_id IN ({lph}) AND type IN ('death','kill') "
            f"ORDER BY ts, seq", live):
        tgt_roll = rollup.get(r["tgt_entity"])
        if tgt_roll is None or (
                r["type"] == "kill"
                and meta[r["tgt_entity"]]["kind"] not in ("player", "own_pet")):
            continue
        deaths.append({"ts": r["ts"], "encounter_id": r["encounter_id"],
                       "encounter_name": name_of_enc.get(r["encounter_id"]),
                       "key": _ent_key(meta[tgt_roll]["name"], meta[tgt_roll]["kind"]),
                       "name": meta[tgt_roll]["name"]})
    if not deaths:
        return {"window_s": window, "deaths": [],
                "pruned_encounters": pruned_encounters}

    # one pass over the events that could fall in ANY death window, indexed by
    # the key that took / received them
    wanted = {d["key"] for d in deaths}
    since = min(d["ts"] for d in deaths) - window
    until = max(d["ts"] for d in deaths)
    incoming: dict[str, list] = {k: [] for k in wanted}
    healing: dict[str, list] = {k: [] for k in wanted}
    for r in conn.execute(
            f"SELECT e.encounter_id, e.ts, e.type, e.src_entity, e.tgt_entity, "
            f"e.amount, e.dtype, e.flags, ab.name AS ability FROM events e "
            f"LEFT JOIN abilities ab ON ab.id = e.ability_id "
            f"WHERE e.encounter_id IN ({lph}) AND e.type IN ('damage','heal','ward') "
            f"AND e.ts >= ? AND e.ts <= ? ORDER BY e.ts, e.seq",
            [*live, since, until]):
        src = meta.get(r["src_entity"])
        amt = r["amount"] or 0
        if r["type"] == "damage":
            src_roll = credit.get(r["src_entity"])
            if (r["flags"] & F_SELF_FOCUS) or (
                    src_roll is not None and credit.get(r["tgt_entity"]) == src_roll):
                continue
            key = taken_key.get(r["tgt_entity"])
            bucket = incoming
            entry = {"ts": r["ts"], "source": src["name"] if src else None,
                     "ability": _ability_name(r), "amount": amt,
                     "dtype": r["dtype"], "crit": bool(r["flags"] & F_CRIT)}
        else:
            key = rollup.get(r["tgt_entity"])
            bucket = healing
            entry = {"ts": r["ts"], "source": src["name"] if src else None,
                     "ability": _ability_name(r), "amount": amt,
                     "kind": r["type"]}
        if key is None:
            continue
        ent = meta.get(key)
        k = _ent_key(ent["name"], ent["kind"]) if ent else None
        if k in bucket:
            bucket[k].append(entry)

    stamps = {"incoming": {k: [e["ts"] for e in v] for k, v in incoming.items()},
              "healing": {k: [e["ts"] for e in v] for k, v in healing.items()}}
    for d in deaths:
        rows, total, truncated = _window_slice(
            incoming[d["key"]], stamps["incoming"][d["key"]], d["ts"], window)
        d["incoming"], d["incoming_total"] = rows, total
        d["incoming_truncated"] = truncated
        rows, total, truncated = _window_slice(
            healing[d["key"]], stamps["healing"][d["key"]], d["ts"], window)
        d["healing"], d["healing_total"] = rows, total
        d["healing_truncated"] = truncated
    deaths.sort(key=lambda d: d["ts"])
    return {"window_s": window, "deaths": deaths,
            "pruned_encounters": pruned_encounters}


# -------------------------------------------------------------------- aoes ---

@router.get("/encounters/aoes")
def encounters_aoes(ids: str = Query(...), user=Depends(optional_user)):
    """Enemy AoEs in the selection: how often each one really landed, next to
    the timer ACT reports for it, and how much of the raid was covered when it
    did. Reads raw events, so a pruned session contributes nothing."""
    conn = get_db()
    enc_ids, encs, session_ids, sess_of = _selection(conn, user, ids)

    live, pruned_encounters = [], 0
    named_sources = set()
    for e in encs:
        if sess_of[e["session_id"]]["pruned"]:
            pruned_encounters += 1
        else:
            live.append(e["id"])
        if e["is_named"] and e["name"]:
            named_sources.add(e["name"])
    if not live:
        return {"aoes": [], "min_targets": aoes.MIN_TARGETS,
                "pruned_encounters": pruned_encounters,
                "pruned": pruned_encounters == len(enc_ids)}

    meta = _entity_meta(conn, session_ids)
    lph = ",".join("?" * len(live))
    rows = []
    for r in conn.execute(
            f"SELECT e.encounter_id, e.ts, e.type, e.src_entity, e.tgt_entity, "
            f"e.amount, e.flags, ab.name AS ability FROM events e "
            f"LEFT JOIN abilities ab ON ab.id = e.ability_id "
            f"WHERE e.encounter_id IN ({lph}) AND e.type IN ('damage','avoid') "
            f"ORDER BY e.ts, e.seq", live):
        src, tgt = meta.get(r["src_entity"]), meta.get(r["tgt_entity"])
        if src is None or tgt is None:
            continue
        rows.append({
            "encounter_id": r["encounter_id"],
            "ts": r["ts"], "type": r["type"], "ability": r["ability"],
            "src_name": src["name"], "src_kind": src["kind"],
            "tgt_key": _ent_key(tgt["name"], tgt["kind"]), "tgt_kind": tgt["kind"],
            "amount": r["amount"], "flags": r["flags"],
        })
    return {
        "aoes": aoes.detect(rows, named_sources),
        "min_targets": aoes.MIN_TARGETS,
        "pruned_encounters": pruned_encounters,
        "pruned": False,
    }


# --------------------------------------------------------------------- loot ---

@router.get("/encounters/loot")
def encounters_loot(ids: str = Query(...), user=Depends(optional_user)):
    """What the chests in this selection gave, and who took it.

    Chest loot only — body drops are shards and vendor coin and the parser
    never records them (`pipeline/loot.py`). Each row is one item off one
    chest, carrying the fight it belonged to, the mob whose chest it was, and
    the raider who won or took it.

    **Drops with no fight are included, bounded by the selection's CLOCK.** A
    chest is looted after the pull and sometimes the mob it names is not the
    one the fight was named for, so `attribution='none'` is a real outcome
    (~12% of the archive, mostly sessions whose events have been pruned). Those
    still belong to the raid, so they are returned when they fall inside the
    span of the fights being looked at — which is bounded by what the caller
    was already authorized to see, never by a whole session.

    The item CARD (icon, rarity, wiki page) is whatever has already been
    resolved; see backend/items.py. Nothing is fetched on a page load, so an
    unresolved item renders as the name the log wrote and nothing is missing
    from the answer except its picture."""
    conn = get_db()
    enc_ids, encs, session_ids, sess_of = _selection(conn, user, ids)

    eph = ",".join("?" * len(enc_ids))
    sph = ",".join("?" * len(session_ids))
    # The window an unattributed chest may sit in: the selection's own span,
    # plus the tail pipeline/loot.py allows between a fight and its chest.
    lo = min(e["started_ts"] for e in encs)
    hi = max(e["ended_ts"] for e in encs) + loot.NEAREST_S
    rows = conn.execute(
        f"SELECT * FROM loot_drops "
        f"WHERE encounter_id IN ({eph}) "
        f"   OR (encounter_id IS NULL AND session_id IN ({sph}) "
        f"       AND ts BETWEEN ? AND ?) "
        f"ORDER BY ts, id",
        (*enc_ids, *session_ids, lo, hi)).fetchall()
    if not rows:
        return {"loot": [], "unresolved": 0}

    cards = items.cards(conn, {r["item_id"] for r in rows})
    name_of = {e["id"]: e["name"] for e in encs}
    out = []
    for r in rows:
        card = cards.get(r["item_id"]) or {}
        out.append({
            "id": r["id"],
            "ts": r["ts"],
            "encounter_id": r["encounter_id"],
            "fight": name_of.get(r["encounter_id"]),
            "attribution": r["attribution"],
            "chest": r["chest"],
            "mob": r["mob"],
            "item_id": r["item_id"],
            # Census's name when we have it, the log's when we don't — they
            # agree, and the log's is never missing.
            "name": card.get("name") or r["item_name"],
            "qty": r["qty"],
            "looter": r["looter"],
            "method": r["method"],
            # Census tier is the rarity a raider reads; the log's `looted the
            # Fabled ...` line only prints for people standing near you, so it
            # is the fallback rather than the source. See backend/items.py.
            "rarity": (card.get("tier") or r["rarity"] or "").title() or None,
            "confirmed": bool(r["confirmed"]),
            # Who else wanted it: the lotto's NEED/GREED block, or the /random
            # dice when the raid used those. Already in resolution order.
            "rolls": json.loads(r["rolls_json"]) if r["rolls_json"] else None,
            "icon": card.get("icon"),
            "wiki": card.get("wiki"),
            "type": card.get("type"),
            "slot": card.get("slot"),
            "level": card.get("level"),
            "tier": items.tier_of(card.get("level")),
            # The examine window, prebuilt at resolve time — the hover card is
            # a read of this, never a request (backend/items.py: stat_block).
            "stats": card.get("stats"),
            # The item's own proc — name, tier and its indented description.
            # From the WIKI: Census has no field for it.
            "effects": card.get("effects"),
        })
    return {
        "loot": out,
        # How many of these items nobody has looked up yet — the Loot tab says
        # so rather than silently showing a shorter card.
        "unresolved": len(items.unresolved(conn, {r["item_id"] for r in rows})),
    }


IMAGE_CACHE = {"Cache-Control": "public, max-age=604800, immutable"}


@router.get("/items/icon/{iconid}.png")
def item_icon(iconid: int):
    """One item icon, cached from the wiki (`backend/items.py`).

    Keyed by Census's ICON id rather than by item: one 42x42 picture serves
    every item that uses it, and there is no account, session or raid behind
    it — this is a fact about the game, so it needs no visibility check and it
    is `public` in every cache between here and the browser."""
    path = items.icon_path(iconid)
    if not path.exists():
        raise HTTPException(404, "no icon")
    return Response(path.read_bytes(), media_type=items.image_type(path),
                    headers=IMAGE_CACHE)


@router.get("/items/adorn/{color}.png")
def adorn_gem(color: str):
    """One adornment-slot gem. A fixed set of ten, the same on every item, so
    it is cached once and shared like the icons."""
    if color not in items.ADORN_COLORS:
        raise HTTPException(404, "no such slot colour")
    path = items.adorn_path(color)
    if not path.exists():
        raise HTTPException(404, "no gem")
    return Response(path.read_bytes(), media_type=items.image_type(path),
                    headers=IMAGE_CACHE)


# -------------------------------------------------------------- class stats ---

@router.get("/encounters/class-stats")
def encounters_class_stats(ids: str = Query(...), user=Depends(optional_user)):
    """The Class tab: one section per class present, holding that class's own
    metrics (`pipeline/classstats.py`). A class with nothing written for it yet
    is still a section — the tab shows who is in the raid and where their stats
    will live.

    Class resolution is not redone here: the actor list comes from the same
    aggregate the Damage tab already asked for, so "what class is this raider"
    has exactly one answer per page and it is the memoized one."""
    conn = get_db()
    enc_ids, encs, session_ids, sess_of = _selection(conn, user, ids)
    agg = memo.get_or_build(
        ("agg", tuple(enc_ids)),
        lambda: _agg(conn, enc_ids, encs, session_ids, sess_of))
    live = [e["id"] for e in encs if not sess_of[e["session_id"]]["pruned"]]
    ctx = classstats.Ctx(
        conn=conn, enc_ids=enc_ids, encs=list(encs), live_enc_ids=live,
        session_ids=session_ids, actors=agg["actors"])
    payload = classstats.collect(ctx)
    payload["pruned_encounters"] = len(enc_ids) - len(live)
    payload["pruned"] = not live
    return payload


@router.get("/encounters/report")
def encounters_report(ids: str = Query(...), user=Depends(optional_user)):
    """The raid report for an arbitrary encounter set — the same payload
    `/zone-runs/{id}/report` returns, scoped to the fights asked for.

    The zone-run form is what the raid page reads, because there the selection
    is always a subset of one run. The dashboard has no run to ask about while
    the night is still arriving: it holds the fights the live writer has
    committed, and the columns the report feeds (overheal, time dead, damage
    lost dead, engage) are the difference between the parse there and the parse
    on `/zones/:id`. Memoized on the id set like every other selection payload,
    so the fight that just ended is built once however many people are looking
    at it."""
    from coach.raidreport import build_for_encounters

    conn = get_db()
    enc_ids, encs, _session_ids, _sess_of = _selection(conn, user, ids)
    return memo.get_or_build(
        ("enc-report", tuple(enc_ids)),
        lambda: build_for_encounters(conn, encs))


def _detail(conn, enc) -> dict:
    ent_key_of = _entity_keys(conn, [enc["session_id"]])
    strong_here = _strong_here(conn, [enc["id"]])
    actors = []
    for r in conn.execute(
            "SELECT a.*, e.name, e.kind, e.class_guess FROM encounter_actor_stats a "
            "JOIN entities e ON e.id = a.entity_id "
            "WHERE a.encounter_id=? ORDER BY a.damage DESC",
            (enc["id"],)):
        a = dict(r)
        a["key"] = _ent_key(a["name"], a["kind"])
        a["entity_ids"] = [a["entity_id"]]
        a["avg_delay_s"] = _avg_delay(a)
        a["avg_delay_adj_s"] = _avg_delay_adj(a)
        a.update(_class_fields(parse_class_guess(a.pop("class_guess", None)),
                               enc["started_ts"], strong_here.get(a["key"])))
        actors.append(a)
    _add_census_facts(conn, actors, [enc["session_id"]])
    pet_abilities = _pet_ability_names(conn)
    proc_abilities = _proc_ability_names(conn)
    ability_classes = _catalog_classes(conn)
    abilities = []
    class_of = {a["key"]: a["class"] for a in actors}
    scribed = _scribed_classes(conn)
    proc_why = _proc_evidence(conn)
    for r in conn.execute(
            _ABILITY_SELECT + "WHERE s.encounter_id=? ORDER BY s.total DESC",
            (enc["id"],)):
        source_key = _ent_key(r["source_name"], r["source_kind"])
        row = _finish_ability_row(dict(r), pet_abilities, proc_abilities,
                                  ability_classes, class_of.get(source_key), scribed,
                                  proc_why)
        row["source_key"] = source_key
        row["rollup_key"] = ent_key_of.get(row["rollup_to"]) or (
            row["source_key"] if row["source_kind"] == "player" else None)
        abilities.append(row)
    return {
        "encounter": row_to_dict(enc),
        "encounter_ids": [enc["id"]],
        "session_ids": [enc["session_id"]],
        "actors": actors,
        "abilities": abilities,
    }


@router.get("/encounters/{encounter_id}")
def encounter_detail(encounter_id: int, user=Depends(optional_user)):
    conn = get_db()
    enc = conn.execute("SELECT * FROM encounters WHERE id=?", (encounter_id,)).fetchone()
    if enc is None:
        raise HTTPException(404, "no such encounter")
    sess = visible_encounters(conn, user, [enc])[enc["session_id"]]
    backfill_session(conn, enc["session_id"], cache=(sess["status"] == "ready"))
    return _detail(conn, enc)
