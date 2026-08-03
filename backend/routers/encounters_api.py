"""Encounter detail + multi-encounter aggregation: actor table (rolled up to
players; mobs and Unknown keep their own rows) and per-ability breakdown with
pet rows kept visible under their owner.

`GET /encounters/agg?ids=1,2,3` returns the same shape as single-encounter
detail with counters summed and DPS recomputed over the summed duration — it
powers the workspace tree's All / zone / collapsed-trash nodes.

`GET /encounters/timeline?ids=…` and `GET /encounters/deaths?ids=…` read the
stored EVENTS for the same selection (a pruned session contributes nothing —
its events are gone — and says so).

Everything here takes the same `ids` list and the same visibility rule: every
session touched must be visible to the caller, unknown ids 404, junk 422."""

import json
import math
import statistics
from bisect import bisect_left

from fastapi import APIRouter, Depends, HTTPException, Query

import memo
from coach.descriptive import archetype_for
from db import get_db, row_to_dict
from parser.events import F_AUTOATTACK, F_CRIT, F_SELF_FOCUS
from pipeline.classguess import backfill_session, parse_class_guess
from pipeline.statsroll import _melee_bucket
from routers.sessions_api import visible_session
from security import require_user

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
    "s.median, s.avg_delay_s, s.dtypes "
    "FROM encounter_ability_stats s "
    "JOIN entities ent ON ent.id = s.entity_id "
    "JOIN abilities ab ON ab.id = s.ability_id ")


def _pet_ability_names(conn) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT ability_name FROM ability_catalog WHERE unit='pet'")}


def _proc_ability_names(conn) -> set[str]:
    """Names that fire on their own (buff/item procs). The UI separates them
    from cast abilities — they are gear, not rotation."""
    return {r[0] for r in conn.execute(
        "SELECT ability_name FROM ability_catalog WHERE proc=1")}


def _proc_evidence(conn) -> dict[str, str]:
    """ability name -> why it is flagged, in words, for the badge's tooltip.

    A proc mark that turns out to be wrong is only reportable if the row says
    where the claim came from — "curated" and "Census says a shadowknight buff
    casts this" are different kinds of wrong."""
    out = {}
    for r in conn.execute(
            "SELECT ability_name, source, class FROM ability_catalog WHERE proc=1"):
        if r["source"] == "curated":
            out[r["ability_name"]] = "seen firing with no cast, all night"
        elif r["class"]:
            out[r["ability_name"]] = f"Census: cast by a {r['class'].replace(',', '/')} buff or item"
        else:
            out[r["ability_name"]] = "Census: something casts this on its own"
    return out


def _catalog_classes(conn) -> dict[str, str]:
    """ability name -> catalog class string (Census stores every class that can
    scribe the spell, comma-joined — passed through verbatim)."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT ability_name, class FROM ability_catalog WHERE class IS NOT NULL")}


def _class_fields(guess: dict | None) -> dict:
    """The four class columns every actor row carries, from a parsed
    `entities.class_guess`. Archetype is NULL when the class is unknown —
    `archetype_for` defaults to "dps", which would read as a claim we never
    made."""
    if guess is None:
        return {"class": None, "class_confidence": None, "class_source": None,
                "archetype": None}
    cls = guess["class"]
    return {"class": cls, "class_confidence": guess.get("confidence"),
            "class_source": guess.get("source"), "archetype": archetype_for(cls)}


def _avg_delay(a: dict) -> float | None:
    """ACT-style per-combatant Avg Delay: swing span over swing gaps. Sums
    exactly across encounters (sum of spans / sum of gaps)."""
    swings, span = a.get("atk_swings") or 0, a.get("atk_span_s") or 0
    return round(span / (swings - 1), 2) if swings >= 2 and span else None


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
    -> (enc_ids, encounter rows in started_ts order, session_ids, sess_of)."""
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
    session_ids = sorted({e["session_id"] for e in encs})
    sess_of = {sid: visible_session(conn, user, sid) for sid in session_ids}
    for sid, sess in sess_of.items():
        # existing parses light up without a reparse; one indexed lookup when
        # there is nothing to do, and nothing at all once a session is done
        backfill_session(conn, sid, cache=(sess["status"] == "ready"))
    return enc_ids, encs, session_ids, sess_of


@router.get("/encounters/agg")
def encounters_agg(ids: str = Query(...), user=Depends(require_user)):
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
                  "deaths", "time_dead_s", "rez_casts", "cure_count", "active_s",
                  "atk_swings", "atk_span_s"):
            a[k] = (a[k] or 0) + (r[k] or 0)
    for key, a in actor_sum.items():
        a["dps"] = round((a["damage"] or 0) / duration, 1)
        a["avg_delay_s"] = _avg_delay(a)
        a.update(_class_fields(best_guess.get(key)))
    actors = sorted(actor_sum.values(), key=lambda a: -(a["damage"] or 0))
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
                        user=Depends(require_user)):
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
                      user=Depends(require_user)):
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


def _detail(conn, enc) -> dict:
    ent_key_of = _entity_keys(conn, [enc["session_id"]])
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
        a.update(_class_fields(parse_class_guess(a.pop("class_guess", None))))
        actors.append(a)
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
def encounter_detail(encounter_id: int, user=Depends(require_user)):
    conn = get_db()
    enc = conn.execute("SELECT * FROM encounters WHERE id=?", (encounter_id,)).fetchone()
    if enc is None:
        raise HTTPException(404, "no such encounter")
    sess = visible_session(conn, user, enc["session_id"])
    backfill_session(conn, enc["session_id"], cache=(sess["status"] == "ready"))
    return _detail(conn, enc)
