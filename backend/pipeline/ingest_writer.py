"""The single write path: raw lines -> parsed events -> entities/abilities ->
encounter segmentation -> events + rollup rows, in one transaction per session
parse. Bulk uploads use `parse_upload`; the live path (phase 3) will reuse the
same resolution + rollup code per batch.
"""

import gzip
import sqlite3
import time
import traceback
from pathlib import Path

from db import get_db, json_dumps
from parser import parse_lines
from parser import petnames
from parser.events import ParsedEvent, Subject
from parser.subjects import classify_entity_kind, decompose
from pipeline.encounters import segment_events
from pipeline.refine import refine_known_mobs
from pipeline.statsroll import (ABILITY_INSERT, ACTOR_INSERT,
                                ability_rows, actor_rows, roll_encounter)

# bump whenever parser/attribution/rollup semantics change; stale sessions are
# reparsed by the startup sweep (main.py) or POST /api/sessions/{id}/reparse
PARSE_VERSION = 7

PET_KINDS = ("own_pet", "swarm_pet", "named_pet")


class EntityResolver:
    """Session-scoped entity/ability id caches. Resolution depends on who the
    logger is (bare logger-name = their pet), the named-pet knowledge base,
    and the behavioral known-mob set."""

    def __init__(self, conn: sqlite3.Connection, session_id: int, logger: str,
                 pet_names: frozenset[str] = frozenset(),
                 known_mobs: frozenset[str] = frozenset()):
        self.conn = conn
        self.session_id = session_id
        self.logger = logger
        self.pet_names = pet_names
        self.known_mobs = known_mobs
        self._entities: dict[tuple[str, str], int] = {}
        self._rollups: dict[int, int | None] = {}
        self._kinds: dict[int, str] = {}
        self._abilities: dict[str, int] = {}

    def _entity(self, name: str, kind: str, owner_id: int | None = None,
                rollup: int | None = None) -> int:
        key = (name, kind)
        eid = self._entities.get(key)
        if eid is None:
            cur = self.conn.execute(
                "INSERT INTO entities (session_id, name, kind, owner_entity_id, rollup_to) "
                "VALUES (?,?,?,?,?) ON CONFLICT(session_id, name, kind) DO NOTHING",
                (self.session_id, name, kind, owner_id, rollup),
            )
            # rowcount, not lastrowid: on an ignored insert lastrowid is the
            # connection's PREVIOUS successful insert (any table) — garbage
            if cur.rowcount:
                eid = cur.lastrowid
            else:
                eid = self.conn.execute(
                    "SELECT id FROM entities WHERE session_id=? AND name=? AND kind=?",
                    (self.session_id, name, kind),
                ).fetchone()[0]
            self._entities[key] = eid
            self._rollups[eid] = rollup
            self._kinds[eid] = kind
        return eid

    def player(self, name: str) -> int:
        eid = self._entity(name, "player")
        if self._rollups.get(eid) is None:
            self._rollups[eid] = eid
        return eid

    def kind_of(self, eid: int) -> str | None:
        return self._kinds.get(eid)

    def unknown(self) -> int:
        """The session's pooled Unknown source (sourceless passive damage)."""
        return self._entity("Unknown", "other")

    def resolve_subject(self, s: Subject) -> tuple[int, int | None]:
        """-> (entity_id, rollup_entity_id)"""
        if s.unit == "player":
            eid = self.player(s.name)
            return eid, eid
        if s.unit == "own_pet":
            owner = self.player(self.logger)
            eid = self._entity(s.name, "own_pet", owner_id=owner, rollup=owner)
            return eid, owner
        if s.unit in ("swarm_pet", "named_pet"):
            # In the possessive owner slot the logger's name means the PLAYER
            # ("Bobby's blighted horde" = the person's swarm pet) — the bare-name-
            # is-pet rule applies only to a whole subject, never to an owner.
            owner_kind = ("player" if s.name == self.logger
                          else classify_entity_kind(s.name, "unknown", self.logger,
                                                    self.known_mobs))
            if owner_kind == "player":
                owner = self.player(s.name)
                roll = owner
            else:
                owner = self._entity(s.name, owner_kind)
                roll = None
            eid = self._entity(f"{s.name}'s {s.pet}", s.unit, owner_id=owner, rollup=roll)
            return eid, roll
        kind = classify_entity_kind(s.name, "unknown", self.logger, self.known_mobs)
        if kind == "player":
            eid = self.player(s.name)
            return eid, eid
        if kind == "own_pet":
            owner = self.player(self.logger)
            eid = self._entity(s.name, "own_pet", owner_id=owner, rollup=owner)
            return eid, owner
        eid = self._entity(s.name, kind)
        return eid, None

    def resolve_target(self, name: str) -> tuple[int, int | None, str]:
        """-> (entity_id, rollup_entity_id, kind). YOU/YOURSELF = the player;
        bare logger-name = their pet. Possessive pet targets ("Ellea's blighted
        horde") decompose exactly like sources so damage TAKEN by a pet lands
        on the same entity row as damage it dealt."""
        if name in ("YOU", "YOURSELF"):
            eid = self.player(self.logger)
            return eid, eid, "player"
        if name == "Unknown":
            eid = self._entity("Unknown", "other")
            return eid, None, "other"
        if name == self.logger:
            owner = self.player(self.logger)
            eid = self._entity(name, "own_pet", owner_id=owner, rollup=owner)
            return eid, owner, "own_pet"
        subj, remainder = decompose(name, self.logger, self.pet_names)
        if remainder is None and subj.unit in ("swarm_pet", "named_pet"):
            eid, roll = self.resolve_subject(subj)
            return eid, roll, subj.unit
        kind = classify_entity_kind(name, "unknown", self.logger, self.known_mobs)
        if kind == "player":
            eid = self.player(name)
            return eid, eid, "player"
        eid = self._entity(name, kind)
        return eid, None, kind

    def ability_id(self, name: str) -> int:
        aid = self._abilities.get(name)
        if aid is None:
            cur = self.conn.execute(
                "INSERT INTO abilities (name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name,)
            )
            if cur.rowcount:
                aid = cur.lastrowid
            else:
                aid = self.conn.execute(
                    "SELECT id FROM abilities WHERE name=?", (name,)
                ).fetchone()[0]
            self._abilities[name] = aid
        return aid


def session_raw_paths(conn: sqlite3.Connection, session_id: int) -> list[Path]:
    """The stored raw source for a session: the gzipped upload, or a live
    session's chunk files in order. Empty when nothing is on disk."""
    from db import UPLOADS_DIR
    row = conn.execute(
        "SELECT source, upload_sha256 FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row is None:
        return []
    if row["source"] == "upload" and row["upload_sha256"]:
        path = UPLOADS_DIR / f"{row['upload_sha256']}.txt.gz"
        return [path] if path.exists() else []
    return [Path(r["path"]) for r in conn.execute(
        "SELECT path FROM raw_chunks WHERE session_id=? ORDER BY seq", (session_id,))
        if Path(r["path"]).exists()]


def _iter_lines(paths: Path | list[Path]):
    for path in [paths] if isinstance(paths, Path) else paths:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            yield from fh


def clear_derived(conn: sqlite3.Connection, session_id: int) -> None:
    """Drop everything a parse produced (events/encounters/stats/entities) so a
    session can be re-parsed from raw. ingest_lines stays — it is the dedupe
    record for the live path, not derived data."""
    conn.execute(
        "DELETE FROM encounter_actor_stats WHERE encounter_id IN "
        "(SELECT id FROM encounters WHERE session_id=?)", (session_id,))
    conn.execute(
        "DELETE FROM encounter_ability_stats WHERE encounter_id IN "
        "(SELECT id FROM encounters WHERE session_id=?)", (session_id,))
    conn.execute("DELETE FROM encounters WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM entities WHERE session_id=?", (session_id,))


def _resolve_events(events: list[ParsedEvent], res: EntityResolver) -> list[dict]:
    resolved = []
    for seq, ev in enumerate(events):
        src_id = src_roll = tgt_id = tgt_roll = None
        src_kind = tgt_kind = None
        if ev.src is not None:
            src_id, src_roll = res.resolve_subject(ev.src)
            src_kind = res.kind_of(src_id)
        elif ev.type == "damage":
            # sourceless "X is hit for N" — pooled under Unknown, as ACT does
            src_id = src_roll = res.unknown()
            src_kind = "other"
        if ev.tgt is not None and ev.type != "zone":
            tgt_id, tgt_roll, tgt_kind = res.resolve_target(ev.tgt)
        resolved.append({
            "ts": ev.ts, "seq": seq, "type": ev.type,
            "src_entity": src_id, "src_rollup": src_roll, "src_kind": src_kind,
            "tgt_entity": tgt_id, "tgt_rollup": tgt_roll, "tgt_kind": tgt_kind,
            "ability": ev.ability, "amount": ev.amount, "dtype": ev.dtype,
            "flags": ev.flags, "extra": ev.extra or None,
        })
    return resolved


def parse_session(session_id: int, path: Path | list[Path]) -> None:
    """Parse stored raw (one upload file, or a live session's chunk files in
    order) into events/encounters/rollups. Idempotent: derived rows are cleared
    first, so the live path reuses it to rebuild at session close. Runs in a
    worker thread; owns its own connection."""
    conn = get_db()
    row = conn.execute(
        "SELECT s.id, c.name AS char_name FROM sessions s "
        "JOIN characters c ON c.id = s.character_id WHERE s.id=?",
        (session_id,),
    ).fetchone()
    if row is None:
        return
    logger = row["char_name"]

    try:
        conn.execute("UPDATE sessions SET status='parsing' WHERE id=?", (session_id,))
        conn.commit()

        # pass 1 (prescan): named-pet death evidence from this file, union the
        # global knowledge base — a pet that only dies at the end still
        # attributes from line 1
        observed_pets = petnames.prescan(_iter_lines(path), logger)
        pet_names = petnames.load(conn) | set(observed_pets)

        line_count = 0

        def counted():
            nonlocal line_count
            for line in _iter_lines(path):
                line_count += 1
                yield line

        events = list(parse_lines(counted(), logger, pet_names))
        known_mobs = refine_known_mobs(events, logger)

        with conn:
            clear_derived(conn, session_id)
            res = EntityResolver(conn, session_id, logger, pet_names, known_mobs)
            resolved = _resolve_events(events, res)
            segments = segment_events(events, logger, known_mobs=known_mobs)
            pet_cast: set[str] = set()

            # encounter ids per event index
            enc_of: dict[int, int] = {}
            for seg in segments:
                cur = conn.execute(
                    "INSERT INTO encounters (session_id, zone, name, is_named, started_ts, "
                    "ended_ts, duration_s, success) VALUES (?,?,?,?,?,?,?,?)",
                    (session_id, seg.zone, seg.name, int(seg.is_named), seg.start_ts,
                     seg.end_ts, max(seg.end_ts - seg.start_ts, 1), seg.success),
                )
                enc_id = cur.lastrowid
                for i in seg.event_indices:
                    enc_of[i] = enc_id

                seg_events = [resolved[i] for i in seg.event_indices]
                actor_stats, ability_stats = roll_encounter(
                    seg_events, max(seg.end_ts - seg.start_ts, 1)
                )
                conn.executemany(ACTOR_INSERT, actor_rows(enc_id, actor_stats))
                conn.executemany(
                    ABILITY_INSERT, ability_rows(enc_id, ability_stats, res.ability_id))
                pet_cast.update(
                    name for (src, name, _kind) in ability_stats
                    if not name.startswith("(")     # skip (melee)/(multi attack)/…
                    and res.kind_of(src) in PET_KINDS)

            conn.executemany(
                "INSERT INTO events (session_id, encounter_id, ts, seq, type, src_entity, "
                "tgt_entity, ability_id, amount, dtype, flags, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (session_id, enc_of.get(i), r["ts"], r["seq"], r["type"], r["src_entity"],
                     r["tgt_entity"],
                     res.ability_id(r["ability"]) if r["ability"] else None,
                     r["amount"], r["dtype"], r["flags"],
                     json_dumps(r["extra"]) if r["extra"] else None)
                    for i, r in enumerate(resolved)
                ],
            )

            # learn-back: newly observed pet names + abilities pets actually
            # cast feed every future parse (and reparses of older sessions)
            petnames.learn(conn, observed_pets, session_id)
            if pet_cast:
                from census.catalog import observe_pet_abilities
                observe_pet_abilities(conn, pet_cast)

            started = events[0].ts if events else None
            ended = events[-1].ts if events else None
            conn.execute(
                "UPDATE sessions SET status='ready', started_ts=?, ended_ts=?, "
                "line_count=?, parse_version=? WHERE id=?",
                (started, ended, line_count, PARSE_VERSION, session_id),
            )
    except Exception:
        conn.execute(
            "UPDATE sessions SET status='error', error=? WHERE id=?",
            (traceback.format_exc(limit=5), session_id),
        )
        conn.commit()
        raise
