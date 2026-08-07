"""The single write path: raw lines -> parsed events -> entities/abilities ->
encounter segmentation -> events + rollup rows, in one transaction per session
parse. Bulk uploads use `parse_upload`; the live path (phase 3) will reuse the
same resolution + rollup code per batch.
"""

import gzip
import logging
import os
import sqlite3
import time
import traceback
from pathlib import Path

from db import get_db, json_dumps
from parser import parse_lines
from parser import petnames
from parser.events import ParsedEvent, Subject
from parser.subjects import classify_entity_kind, decompose
from pipeline.classguess import guess_session_classes
from pipeline.encounters import (encounter_label, segment_events,
                                 split_trailing_corpse)
from pipeline.refine import refine_bare_pets, refine_known_mobs, roster_prescan
from pipeline.statsroll import (ABILITY_INSERT, ACTOR_INSERT,
                                ability_rows, actor_rows, roll_encounter)

# bump whenever parser/attribution/rollup semantics change; stale sessions are
# reparsed by the startup sweep (main.py) or POST /api/sessions/{id}/reparse
PARSE_VERSION = 20    # 13: every rez family, revives + time dead, intercepts,
#                            presses ("adjusted delay")
#                      15: the clock stops at the group's last action; a dead
#                            mob's trailing ticks leave the fight
#                            (pipeline.encounters.split_trailing_corpse)
#                      16: a boss's self-heal no longer makes it player-like,
#                            so one-word bosses that heal (Wuoshi) refine to
#                            mobs instead of landing in the raider table and
#                            handing their fight's title to the adds; and an
#                            actor the log never proves is a person gets no
#                            class claim (pipeline.refine.roster_prescan)
#                      17: a segment is a FIGHT only if the raid engaged it —
#                            proc-pet taps and stray DoT ticks stop counting as
#                            named pulls, and a wipe the boss AoEd down before
#                            anyone swung records success=0 instead of NULL
#                            (pipeline.encounters.encounter_label)
#                      18: owning a swarm pet no longer proves personhood, so
#                            an encounter that holds the raid's pets ("Enynti's
#                            protoflame") stops promoting the BOSS to a
#                            confirmed player and vetoing its own reclassing —
#                            it sat in the raider table with 872k damage while
#                            24 people attacked it (pipeline.refine)
#                      19: curated buff lines (parser/buffs.py) — `buff_cast`
#                            and `buff` events for the handful of abilities
#                            whose apply the log prints, ANY raider's log
#                            seeing them. Rollups and segmentation are
#                            untouched: statsroll ignores both types and
#                            segment_events only opens on damage/avoid, so ACT
#                            parity is unchanged and the events are read by the
#                            Class tab alone
#                      20: pets and procs stop being inferred. Census `found=1`
#                            vetoes the bare-name pet guess (Gululu, a level 70
#                            shadowknight, was a dumbfire), and the catalog no
#                            longer takes a pet or proc LABEL from a sighting or
#                            from "may cast X" — so `proc_names` narrows and the
#                            rollup's press counting changes with it
#                            (census/catalog.py, pipeline/refine.py)

PET_KINDS = ("own_pet", "swarm_pet", "named_pet")

log = logging.getLogger("parse")

ROSTER_LOOKUP_BUDGET = 60      # unseen names one parse may ask Census about


def _sync_roster_classes(conn, session_id: int, character_id: int) -> None:
    """Resolve this session's raiders against Census and re-run the class pass
    if anything new came back.

    Budgeted, because a first upload can carry two hundred names nobody has
    looked up yet and a parse should not turn into a thirty-second HTTP loop;
    whatever is left stays stale and the next parse (or
    `backend/tools/sync_roster.py`) takes another bite. Cached answers cost
    nothing, so the steady state is zero requests per raid."""
    if os.environ.get("CENSUS_AUTO_REFRESH", "1") == "0":
        return
    try:
        from census import client as census_client
        from census import guilds as census_guilds
        from census import roster as census_roster

        names = [r["name"] for r in conn.execute(
            "SELECT DISTINCT name FROM entities WHERE session_id=? AND kind='player'",
            (session_id,))]
        world = conn.execute("SELECT world_id FROM characters WHERE id=?",
                             (character_id,)).fetchone()
        report = census_roster.resolve(
            conn, census_client.shared_client(), names,
            world["world_id"] if world else census_roster.DEFAULT_WORLD,
            budget=ROSTER_LOOKUP_BUDGET)
        if report["found"]:
            with conn:
                guess_session_classes(conn, session_id)
        # the guild rode along with those answers, so the raid can wear its tag
        # on the first page load rather than after the hourly sweep
        with conn:
            census_guilds.retag_runs(conn, character_id)
    except Exception:
        log.exception("roster class sync failed for session %s", session_id)


class EntityResolver:
    """Session-scoped entity/ability id caches. Resolution depends on who the
    logger is (bare logger-name = their pet), the named-pet knowledge base,
    and the behavioral known-mob set."""

    def __init__(self, conn: sqlite3.Connection, session_id: int, logger: str,
                 pet_names: frozenset[str] = frozenset(),
                 known_mobs: frozenset[str] = frozenset(),
                 known_pets: frozenset[str] = frozenset()):
        self.conn = conn
        self.session_id = session_id
        self.logger = logger
        self.pet_names = pet_names
        self.known_mobs = known_mobs
        self.known_pets = known_pets
        self._entities: dict[tuple[str, str], int] = {}
        self._rollups: dict[int, int | None] = {}
        self._kinds: dict[int, str] = {}
        self._names: dict[int, str] = {}
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
            self._names[eid] = name
        return eid

    def player(self, name: str) -> int:
        eid = self._entity(name, "player")
        if self._rollups.get(eid) is None:
            self._rollups[eid] = eid
        return eid

    def kind_of(self, eid: int) -> str | None:
        return self._kinds.get(eid)

    def name_of(self, eid: int) -> str | None:
        return self._names.get(eid)

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
                          # an owner is never a bare dumbfire, so known_pets
                          # has no business here
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
        kind = classify_entity_kind(s.name, "unknown", self.logger, self.known_mobs,
                                    self.known_pets)
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
        kind = classify_entity_kind(name, "unknown", self.logger, self.known_mobs,
                                    self.known_pets)
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


def drop_raw_if_unwanted(conn: sqlite3.Connection, session_id: int) -> bool:
    """`retain_raw=0` means "parse it, don't keep it" — the trade a huge log can
    make instead of being refused. The stats stay forever; the bytes go as soon
    as the parse lands.

    Two consequences, both enforced rather than hoped for: the file is only
    unlinked when no other session points at that content address (uploads are
    shared between people who were on the same raid), and the session can never
    be re-parsed, so the PARSE_VERSION sweep must skip it — a parser improvement
    will not reach these sessions."""
    from db import UPLOADS_DIR
    row = conn.execute(
        "SELECT source, upload_sha256, retain_raw, raw_deleted_ts FROM sessions "
        "WHERE id=?", (session_id,)).fetchone()
    if row is None or row["retain_raw"] or row["raw_deleted_ts"]:
        return False
    chunks = [r["path"] for r in conn.execute(
        "SELECT path FROM raw_chunks WHERE session_id=?", (session_id,))]
    with conn:
        conn.execute("DELETE FROM raw_chunks WHERE session_id=?", (session_id,))
        conn.execute("UPDATE sessions SET raw_deleted_ts=?, raw_bytes=0 WHERE id=?",
                     (int(time.time()), session_id))
        shared = conn.execute(
            "SELECT 1 FROM sessions WHERE upload_sha256=? AND id<>? LIMIT 1",
            (row["upload_sha256"], session_id)).fetchone() if row["upload_sha256"] else None
    for p in chunks:
        Path(p).unlink(missing_ok=True)
    if row["source"] == "upload" and row["upload_sha256"] and shared is None:
        (UPLOADS_DIR / f"{row['upload_sha256']}.txt.gz").unlink(missing_ok=True)
    return True


def parse_session(session_id: int, path: Path | list[Path]) -> None:
    """Parse stored raw (one upload file, or a live session's chunk files in
    order) into events/encounters/rollups. Idempotent: derived rows are cleared
    first, so the live path reuses it to rebuild at session close. Runs in a
    worker thread; owns its own connection."""
    conn = get_db()
    row = conn.execute(
        "SELECT s.id, s.character_id, c.name AS char_name FROM sessions s "
        "JOIN characters c ON c.id = s.character_id WHERE s.id=?",
        (session_id,),
    ).fetchone()
    if row is None:
        return
    logger = row["char_name"]
    character_id = row["character_id"]

    try:
        conn.execute("UPDATE sessions SET status='parsing' WHERE id=?", (session_id,))
        conn.commit()

        # pass 1 (prescan): named-pet death evidence from this file, union the
        # global knowledge base — a pet that only dies at the end still
        # attributes from line 1
        observed_pets = petnames.prescan(_iter_lines(path), logger)
        pet_names = petnames.load(conn) | set(observed_pets)
        # who this log PROVES is a person — see classguess.guess_session_classes
        roster = roster_prescan(_iter_lines(path), logger)

        line_count = 0

        def counted():
            nonlocal line_count
            for line in _iter_lines(path):
                line_count += 1
                yield line

        events = list(parse_lines(counted(), logger, pet_names))
        known_mobs = refine_known_mobs(events, logger, roster)
        from census.catalog import pet_ability_names
        from census.roster import found_names, missing_names
        known_pets = refine_bare_pets(
            events, logger, roster, pet_ability_names(conn), known_mobs,
            missing_names(conn), found_names(conn))
        from census.catalog import press_inputs
        periods, proc_names = press_inputs(conn)

        with conn:
            clear_derived(conn, session_id)
            res = EntityResolver(conn, session_id, logger, pet_names, known_mobs,
                                 known_pets)
            resolved = _resolve_events(events, res)
            segments = [
                piece
                for seg in segment_events(events, logger, known_mobs=known_mobs)
                for piece in split_trailing_corpse(
                    seg, [resolved[i] for i in seg.event_indices])
            ]
            pet_cast: set[str] = set()

            # encounter ids per event index
            enc_of: dict[int, int] = {}
            for seg in segments:
                seg_events = [resolved[i] for i in seg.event_indices]
                # naming needs resolved entities (which target is a mob), so it
                # happens here rather than inside the pure segmenter
                name, is_named, success = encounter_label(
                    seg_events, res.name_of, logger, known_mobs)
                cur = conn.execute(
                    "INSERT INTO encounters (session_id, zone, name, is_named, started_ts, "
                    "ended_ts, duration_s, success) VALUES (?,?,?,?,?,?,?,?)",
                    (session_id, seg.zone, name, int(is_named), seg.start_ts,
                     seg.end_ts, max(seg.end_ts - seg.start_ts, 1), success),
                )
                enc_id = cur.lastrowid
                for i in seg.event_indices:
                    enc_of[i] = enc_id

                actor_stats, ability_stats = roll_encounter(
                    seg_events, max(seg.end_ts - seg.start_ts, 1),
                    periods, proc_names,
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

            # class inference needs the finished ability rollups (it votes on
            # the abilities each player actually used), so it runs last
            guess_session_classes(conn, session_id, roster)

            # learn-back: newly observed pet names + abilities pets actually
            # cast feed every future parse (and reparses of older sessions)
            petnames.learn(conn, observed_pets, session_id)
            if pet_cast:
                from census.catalog import observe_pet_abilities
                observe_pet_abilities(conn, pet_cast, session_id)

            started = events[0].ts if events else None
            ended = events[-1].ts if events else None
            conn.execute(
                "UPDATE sessions SET status='ready', started_ts=?, ended_ts=?, "
                "line_count=?, parse_version=? WHERE id=?",
                (started, ended, line_count, PARSE_VERSION, session_id),
            )

            from pipeline.zoneruns import rebuild_zone_runs
            rebuild_zone_runs(conn, character_id)

        # Census knows every raider's class by name, which is the answer the
        # vote above can only approximate — but it is an HTTP round trip per
        # unseen name, so it runs OUTSIDE the write transaction and the classes
        # land on a second, cheap pass over the same session. Failing here
        # costs a raid its ground truth, never its parse.
        _sync_roster_classes(conn, session_id, character_id)

        # Ingest already dropped the private channels; this drops the group/raid
        # talk that happened outside any fight, which needs the fights to exist
        # and so cannot happen any earlier.
        from pipeline.redact import trim_to_fights
        trimmed = trim_to_fights(conn, session_id)
        if trimmed:
            with conn:
                conn.execute(
                    "UPDATE sessions SET redacted_lines = redacted_lines + ? WHERE id=?",
                    (trimmed, session_id))

        drop_raw_if_unwanted(conn, session_id)
    except Exception:
        conn.execute(
            "UPDATE sessions SET status='error', error=? WHERE id=?",
            (traceback.format_exc(limit=5), session_id),
        )
        conn.commit()
        raise
