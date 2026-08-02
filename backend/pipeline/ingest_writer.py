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
from parser.events import ParsedEvent, Subject
from parser.subjects import classify_entity_kind
from pipeline.encounters import segment_events
from pipeline.statsroll import ACTOR_INSERT, actor_rows, roll_encounter


class EntityResolver:
    """Session-scoped entity/ability id caches. Resolution depends on who the
    logger is (bare logger-name = their pet)."""

    def __init__(self, conn: sqlite3.Connection, session_id: int, logger: str):
        self.conn = conn
        self.session_id = session_id
        self.logger = logger
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

    def resolve_subject(self, s: Subject) -> tuple[int, int | None]:
        """-> (entity_id, rollup_entity_id)"""
        if s.unit == "player":
            eid = self.player(s.name)
            return eid, eid
        if s.unit == "own_pet":
            owner = self.player(self.logger)
            eid = self._entity(s.name, "own_pet", owner_id=owner, rollup=owner)
            return eid, owner
        if s.unit == "swarm_pet":
            # In the possessive owner slot the logger's name means the PLAYER
            # ("Bobby's blighted horde" = the person's swarm pet) — the bare-name-
            # is-pet rule applies only to a whole subject, never to an owner.
            owner_kind = ("player" if s.name == self.logger
                          else classify_entity_kind(s.name, "unknown", self.logger))
            if owner_kind == "player":
                owner = self.player(s.name)
                roll = owner
            else:
                owner = self._entity(s.name, owner_kind)
                roll = None
            eid = self._entity(f"{s.name}'s {s.pet}", "swarm_pet", owner_id=owner, rollup=roll)
            return eid, roll
        kind = classify_entity_kind(s.name, "unknown", self.logger)
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
        bare logger-name = their pet."""
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
        kind = classify_entity_kind(name, "unknown", self.logger)
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
        tgt_kind = None
        if ev.src is not None:
            src_id, src_roll = res.resolve_subject(ev.src)
        if ev.tgt is not None and ev.type != "zone":
            tgt_id, tgt_roll, tgt_kind = res.resolve_target(ev.tgt)
        resolved.append({
            "ts": ev.ts, "seq": seq, "type": ev.type,
            "src_entity": src_id, "src_rollup": src_roll,
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

        line_count = 0

        def counted():
            nonlocal line_count
            for line in _iter_lines(path):
                line_count += 1
                yield line

        events = list(parse_lines(counted(), logger))

        with conn:
            clear_derived(conn, session_id)
            res = EntityResolver(conn, session_id, logger)
            resolved = _resolve_events(events, res)
            segments = segment_events(events, logger)

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
                    "INSERT INTO encounter_ability_stats (encounter_id, entity_id, ability_id, "
                    "kind, casts, hits, crits, misses, resists, total, min, max) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (enc_id, src, res.ability_id(name), kind, st["casts"], st["hits"],
                         st["crits"], st["misses"], st["resists"], st["total"], st["min"], st["max"])
                        for (src, name, kind), st in ability_stats.items()
                    ],
                )

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

            started = events[0].ts if events else None
            ended = events[-1].ts if events else None
            conn.execute(
                "UPDATE sessions SET status='ready', started_ts=?, ended_ts=?, line_count=? "
                "WHERE id=?",
                (started, ended, line_count, session_id),
            )
    except Exception:
        conn.execute(
            "UPDATE sessions SET status='error', error=? WHERE id=?",
            (traceback.format_exc(limit=5), session_id),
        )
        conn.commit()
        raise
