"""Zone runs: the organizing entity above sessions. A run is a contiguous
visit to one zone by one character, derived entirely from encounter rows —
sessions (files) stay the ingest unit, runs are what the UI navigates.

`rebuild_zone_runs` is idempotent and deterministic; it owns three concerns:

1. Content dedupe — overlapping uploaded files produce byte-identical
   encounters (segmentation is deterministic per parse_version, so identical
   log bytes yield identical (started_ts, ended_ts, zone, name)). Duplicates
   are MARKED (`encounters.dup_of` -> canonical id), never deleted, so every
   parse stays complete and the marking re-derives after any reparse. The
   newest parse of a fight is canonical. Scope is ONE CHARACTER's own
   overlapping files: two raiders who both logged the same pull are two
   observations, not a duplicate (see `_dedupe`).

2. Segmentation — canonical encounters in time order split into runs on a
   zone change or an idle gap > ZONE_RUN_GAP_S. Encounters before any zone
   line (zone IS NULL) form their own "Unknown zone" runs.

3. Id-preserving upsert — a recomputed run reuses the existing row's id when
   zone matches and the time windows overlap, so /zones/:id URLs survive
   reparses and backfills that extend a run.

Hand edits (`run_edits`) ride on top of all three. They are keyed by encounter
FINGERPRINT, not id, because a reparse drops and recreates every encounter row
— deleting a fight or splitting a run has to mean the same thing after the next
backfill as it did when you clicked it. Four kinds:
  delete — the fight is gone everywhere, including for the owner
           (`encounters.deleted_ts` is the denormalized mark; this table stays
           the source of truth)
  hide   — the fight is the OWNER'S and nobody else's: still in their rail so
           they can put it back, absent from every viewer's payload
           (`security.py`) and out of every total on the page
           (`encounters.hidden_ts` is its mark). Delete and hide are separate
           kinds rather than one flag because they answer different questions —
           "this pull never happened" and "this pull is not the raid's business"
           — and un-hiding must not resurrect something deleted.
  break  — always start a new run at this fight (unmerge)
  join   — never start a new run at this fight (merge with the one before)

A hidden fight still SEGMENTS: it happened, the raid was in the zone, and
dropping it from the stream would split a night in two at a 40-minute gap that
only exists because somebody hid the pull that spans it. It is only the totals
it stays out of — `encounter_count` and the roster are what a viewer reads, so
they count the visible fights and `hidden_count` carries the rest.
"""

import math
import sqlite3
import statistics
import time

import groups
import memo
import zones
from db import json_dumps

# Split a same-zone encounter stream when combat pauses this long. Observed
# real idle gaps (zoned-out overnight etc.) are >= 70 min; real raid breaks
# stay well under ~35 min.
ZONE_RUN_GAP_S = 3600


def _dedupe(encs: list[sqlite3.Row]) -> tuple[list[sqlite3.Row], dict[int, int]]:
    """-> (canonical encounters in time order, {dup encounter id: canonical id}).

    ONE CHARACTER'S OWN OVERLAPPING FILES, and nothing wider. Two RAIDERS who
    both upload the same pull are not duplicates and must never be merged here:
    each parse is that player's own observation, with their own `YOU` lines and
    their own visibility, and a run is one character's visit by definition. The
    thing that genuinely wants a notion of "one real pull" across characters is
    timer learning, and it has its own (`aoelearn.pull_key`).

    PARSE VERSION IS NOT A PARTITION, and used to be. The guard read "differing
    versions may segment differently, so let the reparse sweep converge them
    first" — which is true of segmentation in general and cannot apply here,
    because the group key IS the segmentation result. Every member of a group
    already agreed on `(started_ts, ended_ts, zone, name)`; there is no
    disagreement left for a sweep to converge. A fight the two versions really
    did segment differently lands in two different groups and is untouched by
    any of this, exactly as before.

    What the partition actually did was leave permanent duplicates behind
    whenever a session STOPPED being sweepable. `_reparse_stale` only walks
    `ready`/`parsing`, so 20 sessions sitting at `error` held an older
    `parse_version` forever, and every fight they shared with a healthy session
    stayed doubled — 28 encounters, and one of them is why `aoelearn.MIN_FIGHTS`
    could be satisfied by a single pull. A guard that waits for an event that
    will never happen is not being careful."""
    groups: dict[tuple, list[sqlite3.Row]] = {}
    for e in encs:
        key = (e["started_ts"], e["ended_ts"], e["zone"] or "", e["name"] or "")
        groups.setdefault(key, []).append(e)

    canonical: list[sqlite3.Row] = []
    dup_of: dict[int, int] = {}
    for members in groups.values():
        # NEWEST PARSE FIRST, then widest raw coverage (the superset file),
        # then lowest session. Version leads because the fight is settled — the
        # group key says both versions segmented it the same way — so the only
        # thing left to choose between is two analyses of it, and the later one
        # is the better one by construction. Coverage stays the tiebreak it was
        # for the far commoner case of two files at the SAME version, where it
        # is the only thing separating them. `-1` sorts a NULL version (a
        # session that has never been parsed) last rather than crashing.
        members.sort(key=lambda e: (-(e["parse_version"] if e["parse_version"]
                                      is not None else -1),
                                    -e["coverage"], e["session_id"]))
        winner = members[0]
        canonical.append(winner)
        for loser in members[1:]:
            dup_of[loser["id"]] = winner["id"]
    canonical.sort(key=lambda e: (e["started_ts"], e["ended_ts"], e["session_id"]))
    return canonical, dup_of


def encounter_fp(e) -> str:
    """Reparse-stable identity for one fight: when it started, where, and what
    was fought. Matches the dedupe key (minus ended_ts) so every copy of a
    duplicated fight carries the same fingerprint and one delete covers them
    all."""
    return f"{e['started_ts']}|{e['zone'] or ''}|{e['name'] or ''}"


def load_edits(conn: sqlite3.Connection, character_id: int) -> dict[str, set[str]]:
    """-> {'delete': {fp, ...}, 'hide': {...}, 'break': {...}, 'join': {...}}."""
    out: dict[str, set[str]] = {
        "delete": set(), "hide": set(), "break": set(), "join": set()}
    for r in conn.execute(
            "SELECT fp, kind FROM run_edits WHERE character_id=?", (character_id,)):
        out.setdefault(r["kind"], set()).add(r["fp"])
    return out


CONTESTED_ROSTER_AGREEMENT = 0.50
MIN_ZONE_EVIDENCE = 2
MIN_HEROIC_HP_SAMPLES = 20
RAID_HP_MEDIAN_MULTIPLIER = 10
HEROIC_INSTANCES = frozenset({
    "Group", "Solo-Group", "Heroic", "Event Heroic", "Public",
})


def _segment(canonical: list[sqlite3.Row],
             edits: dict[str, set[str]] | None = None,
             zone_of: dict[int, str | None] | None = None,
             encounter_rosters: dict[int, set[str]] | None = None,
             ) -> list[list[sqlite3.Row]]:
    """Cut the encounter stream into visits.

    Public raid targets add one boundary the log cannot state: a new guild can
    pull the same contested mob seconds after the last guild. When consecutive
    raid pulls share under half the smaller contributing roster they are two
    runs, even though the logger never left the zone. That is what lets each
    pull carry its own guild and observer facts."""
    breaks = (edits or {}).get("break", set())
    joins = (edits or {}).get("join", set())
    zone_of = zone_of or {}
    encounter_rosters = encounter_rosters or {}
    runs: list[list[sqlite3.Row]] = []
    for e in canonical:
        prev = runs[-1][-1] if runs else None
        fp = encounter_fp(e)
        zone = zone_of.get(e["id"], e["zone"])
        prev_zone = zone_of.get(prev["id"], prev["zone"]) if prev is not None else None
        natural = (prev is None or zone != prev_zone
                   or e["started_ts"] - prev["ended_ts"] > ZONE_RUN_GAP_S)

        contested = False
        if (not natural and zones.is_public(zone)
                and zones.is_raid_encounter(zone, e["name"])):
            # Trash between pulls stays with the pull before it; compare the
            # new raid encounter with the most recent raid encounter in this
            # run, not merely with the immediately preceding row.
            prior = next((p for p in reversed(runs[-1])
                          if zones.is_raid_encounter(
                              zone_of.get(p["id"], p["zone"]), p["name"])), None)
            if prior is not None:
                old = encounter_rosters.get(prior["id"])
                new = encounter_rosters.get(e["id"])
                if old and new:
                    shared = len(old & new)
                    contested = shared / min(len(old), len(new)) < CONTESTED_ROSTER_AGREEMENT
        # a join can never promote the very first fight into a previous run
        cut = natural or contested
        if fp in breaks or (cut and not (fp in joins and prev is not None)):
            runs.append([e])
        else:
            runs[-1].append(e)
    return runs


def _infer_unknown_zone(conn: sqlite3.Connection,
                        members: list[sqlite3.Row]) -> str | None:
    """Recover a missing zone from repeated, unambiguous named-mob evidence.

    A live client attached after zoning can deliver a whole raid without the
    ``You have entered`` line. Named mobs already seen in correctly zoned logs
    are durable game knowledge: two distinct names agreeing on one canonical
    zone is enough; one name or a tied vote remains Unknown."""
    names = sorted({e["name"] for e in members if e["is_named"] and e["name"]})
    if len(names) < MIN_ZONE_EVIDENCE:
        return None
    ph = ",".join("?" * len(names))
    votes: dict[str, set[str]] = {}
    for row in conn.execute(
            f"SELECT DISTINCT zone, name FROM encounters "
            f"WHERE zone IS NOT NULL AND is_named=1 AND name IN ({ph})", names):
        place = zones.display_name(row["zone"])
        if place:
            votes.setdefault(place, set()).add(row["name"])
    ranked = sorted(votes.items(), key=lambda x: (-len(x[1]), x[0]))
    if not ranked or len(ranked[0][1]) < MIN_ZONE_EVIDENCE:
        return None
    if len(ranked) > 1 and len(ranked[0][1]) == len(ranked[1][1]):
        return None
    return ranked[0][0]


def _encounter_rosters(conn: sqlite3.Connection,
                       enc_ids: list[int]) -> dict[int, set[str]]:
    """Contributors per encounter for contested-pull boundaries."""
    out: dict[int, set[str]] = {}
    for start in range(0, len(enc_ids), 500):
        chunk = enc_ids[start:start + 500]
        ph = ",".join("?" * len(chunk))
        for row in conn.execute(
                f"SELECT eas.encounter_id, en.name FROM encounter_actor_stats eas "
                f"JOIN entities en ON en.id=eas.entity_id "
                f"WHERE eas.encounter_id IN ({ph}) AND en.kind='player' "
                f"AND en.name <> ? AND ({_CONTRIBUTED})",
                (*chunk, POOLED_UNKNOWN)):
            out.setdefault(row["encounter_id"], set()).add(row["name"])
    return out


def _heroic_hp_medians(conn: sqlite3.Connection) -> dict[str, float]:
    """Expansion -> median observed HP of successful named heroic content.

    The median is learned from the parse corpus rather than encoded as a
    level-cap number. Heroics are numerous, so a few mis-zoned raid targets
    cannot drag it upward the way a maximum or mean would. Known raid zones and
    explicit mixed-zone raid targets never enter the sample. Sparse eras make
    no claim until enough observations exist."""
    samples: dict[str, list[int]] = {}
    for row in conn.execute(
            "SELECT e.zone, e.name, "
            "MAX(CASE WHEN en.kind='mob' AND en.name=e.name "
            "THEN a.damage_taken END) AS hp "
            "FROM encounters e "
            "JOIN encounter_actor_stats a ON a.encounter_id=e.id "
            "JOIN entities en ON en.id=a.entity_id "
            "WHERE e.zone IS NOT NULL AND e.is_named=1 AND e.success=1 "
            "AND e.dup_of IS NULL AND e.deleted_ts IS NULL AND e.hidden_ts IS NULL "
            "GROUP BY e.id"):
        info = zones.info(row["zone"])
        era = zones.era_of(row["zone"])
        if (not info or info.get("instance") not in HEROIC_INSTANCES
                or era not in zones.ERA_ORDER or not row["hp"]
                or zones.is_raid_encounter(row["zone"], row["name"])):
            continue
        samples.setdefault(era, []).append(row["hp"])
    return {era: statistics.median(values)
            for era, values in samples.items()
            if len(values) >= MIN_HEROIC_HP_SAMPLES}


def _content_era(conn: sqlite3.Connection, zone: str | None,
                 names: list[str]) -> str | None:
    """Resolve the expansion whose heroic median should judge this content.

    Usually the zone is enough. A malformed zone line can instead say a city
    or another non-expansion label; two named mobs previously seen in one real
    expansion recover the content era without pretending one shared name is
    proof. This handles the Trial of Leadership log that claimed Qeynos."""
    era = zones.era_of(zone)
    if era in zones.ERA_ORDER:
        return era
    if len(names) < MIN_ZONE_EVIDENCE:
        return None
    ph = ",".join("?" * len(names))
    support: dict[str, set[str]] = {}
    hits: dict[str, int] = {}
    for row in conn.execute(
            f"SELECT zone, name, COUNT(*) AS hits FROM encounters "
            f"WHERE zone IS NOT NULL AND is_named=1 AND name IN ({ph}) "
            f"GROUP BY zone, name", names):
        candidate = zones.era_of(row["zone"])
        if candidate not in zones.ERA_ORDER:
            continue
        support.setdefault(candidate, set()).add(row["name"])
        hits[candidate] = hits.get(candidate, 0) + row["hits"]
    ranked = sorted(support, key=lambda candidate: (
        -len(support[candidate]), -hits[candidate], zones.era_rank(candidate)))
    if not ranked or len(support[ranked[0]]) < MIN_ZONE_EVIDENCE:
        return None
    if (len(ranked) > 1
            and len(support[ranked[0]]) == len(support[ranked[1]])
            and hits[ranked[0]] == hits[ranked[1]]):
        return None
    return ranked[0]


def _raid_hp_outlier(conn: sqlite3.Connection, zone: str | None,
                     members: list[sqlite3.Row], roster_size: int,
                     heroic_medians: dict[str, float]) -> bool:
    """Corroborate uncatalogued raid content from its era's heroic corpus.

    Require a named target, more than a group's contributing roster, a stable
    heroic sample, and observed target HP at least ten times that era's median.
    The EoF corpus median is about 330K while its largest observed heroic is
    about 2.5M, so the ratio admits the lowest raid targets without turning a
    crowded Castle Mistmoore heroic into a raid."""
    if roster_size < groups.RAID_MIN_RAIDERS:
        return False
    named = sorted({e["name"] for e in members if e["is_named"] and e["name"]})
    if not named:
        return False
    era = _content_era(conn, zone, named)
    baseline = heroic_medians.get(era) if era else None
    if not baseline:
        return False
    enc_ids = [e["id"] for e in members if e["name"] in named]
    enc_ph = ",".join("?" * len(enc_ids))
    observed = conn.execute(
        f"SELECT COALESCE(MAX(a.damage_taken), 0) FROM encounters e "
        f"JOIN encounter_actor_stats a ON a.encounter_id=e.id "
        f"JOIN entities en ON en.id=a.entity_id "
        f"WHERE e.id IN ({enc_ph}) AND en.kind='mob' AND en.name=e.name",
        enc_ids).fetchone()[0]
    return observed >= baseline * RAID_HP_MEDIAN_MULTIPLIER


# The roster: who was in this raid, judged over the whole run instead of the
# one fight that happened to be busiest. An encounter is a time slice
# (pipeline/encounters.py), so it contains everyone the log heard from while
# you were fighting — the group killing something else nearby included. Three
# rules, each removing a different kind of non-raider seen in real logs:
#
#   player   — mobs and the pooled `Unknown` source are not people. A
#              single-token capitalized mob name is indistinguishable from a
#              player until pipeline/refine.py proves otherwise, and the ones
#              it misses land in this table looking like raiders.
#   acted    — a row that only ever TOOK damage is a bystander caught in an AE.
#              Being hit near the raid is not being in it.
#   presence — the real discriminator. A raid is with you all night: on the
#              logs here the core sits at 45-100% of a run's fights while
#              passers-by sit at 3-15%. Castle Mistmoore is the shape of it —
#              four regulars across 12-18 of 23 fights, and another whole group
#              that shows up in exactly 2 as they fight past you.
#
# Counted by NAME: a run can span two log files, entities are session-scoped,
# and the same person is a different row in each.
ROSTER_PRESENCE = 0.25      # share of the run's fights a raider turns up in
MIN_ROSTER_FIGHTS = 2       # ...and never fewer than this
SHORT_RUN_FIGHTS = 4        # below this there is no attendance to read
POOLED_UNKNOWN = "Unknown"  # sourceless damage, pooled by the resolver

_CONTRIBUTED = ("eas.damage > 0 OR eas.heals > 0 OR eas.wards_absorbed > 0 "
                "OR eas.cure_count > 0 OR eas.power_fed > 0 "
                "OR eas.rez_casts > 0 OR eas.atk_swings > 0")


def roster_min_fights(fight_count: int) -> int:
    if fight_count < SHORT_RUN_FIGHTS:
        return 1
    return max(MIN_ROSTER_FIGHTS, math.ceil(fight_count * ROSTER_PRESENCE))


def _roster(conn: sqlite3.Connection, enc_ids: list[int]) -> list[str] | None:
    """The names, sorted. `raider_count` is its length; the names themselves are
    stored (`zone_runs.roster_json`) because who was in a night is what tells
    two people's uploads of the SAME night apart from two different nights —
    see `backend/raidmatch.py`."""
    if not enc_ids:
        return None
    ph = ",".join("?" * len(enc_ids))
    rows = conn.execute(
        f"SELECT en.name, COUNT(DISTINCT eas.encounter_id) AS fights "
        f"FROM encounter_actor_stats eas "
        f"JOIN entities en ON en.id = eas.entity_id "
        f"WHERE eas.encounter_id IN ({ph}) AND en.kind = 'player' "
        f"AND en.name <> ? AND ({_CONTRIBUTED}) "
        f"GROUP BY en.name", (*enc_ids, POOLED_UNKNOWN)).fetchall()
    need = roster_min_fights(len(enc_ids))
    return sorted(r["name"] for r in rows if r["fights"] >= need)


def rebuild_zone_runs(conn: sqlite3.Connection, character_id: int,
                      heroic_medians: dict[str, float] | None = None) -> None:
    """Recompute dedupe marks, run segmentation, and run rollups for one
    character. Caller owns the transaction."""
    if heroic_medians is None:
        heroic_medians = _heroic_hp_medians(conn)

    all_encs = conn.execute(
        "SELECT e.id, e.session_id, e.zone, e.name, e.is_named, e.started_ts, "
        "e.ended_ts, e.duration_s, e.success, e.zone_run_id, e.dup_of, e.deleted_ts, "
        "e.hidden_ts, "
        "s.parse_version, COALESCE(s.ended_ts, 0) - COALESCE(s.started_ts, 0) AS coverage "
        "FROM encounters e JOIN sessions s ON s.id = e.session_id "
        "WHERE s.character_id = ? "
        "ORDER BY e.started_ts, e.ended_ts, e.session_id", (character_id,)).fetchall()

    # re-apply the two hand marks first: they are derived, run_edits is the
    # truth, so a fight deleted or hidden before a reparse comes back that way
    edits = load_edits(conn, character_id)
    now = int(time.time())
    for e in all_encs:
        fp = encounter_fp(e)
        for col, kind in (("deleted_ts", "delete"), ("hidden_ts", "hide")):
            want = now if fp in edits[kind] else None
            if (e[col] is None) != (want is None):
                conn.execute(f"UPDATE encounters SET {col}=? WHERE id=?", (want, e["id"]))
    encs = [e for e in all_encs if encounter_fp(e) not in edits["delete"]]
    hidden_fps = edits["hide"]

    canonical, dup_of = _dedupe(encs)

    # First find each raw visit, then give a zone-less visit one conservative
    # chance to recover its place from named mobs seen in correctly zoned logs.
    # The second pass uses that answer for real segmentation, so an inferred
    # visit behaves exactly like one whose zone line survived.
    effective_zone: dict[int, str | None] = {}
    for members in _segment(canonical, edits):
        place = members[0]["zone"]
        if place is None:
            place = _infer_unknown_zone(conn, members)
        for e in members:
            effective_zone[e["id"]] = place
    contested_ids = [e["id"] for e in canonical
                     if zones.is_public(effective_zone.get(e["id"], e["zone"]))
                     and zones.is_raid_encounter(
                         effective_zone.get(e["id"], e["zone"]), e["name"])]
    runs = _segment(canonical, edits, effective_zone,
                    _encounter_rosters(conn, contested_ids))

    existing = conn.execute(
        "SELECT id, zone, started_ts, ended_ts FROM zone_runs "
        "WHERE character_id = ? ORDER BY started_ts", (character_id,)).fetchall()

    consumed: set[int] = set()
    run_id_of_enc: dict[int, int] = {}
    for members in runs:
        zone = effective_zone.get(members[0]["id"], members[0]["zone"])
        # Every rollup below describes the fights a READER gets, so it is taken
        # over the shown ones — a night with its last two pulls hidden must not
        # still claim to have run until midnight.
        #
        # A run with NOTHING shown falls back to the whole thing for the two
        # fields that say what KIND of night it was: the window and the roster.
        # Only its owner can see it at that point, and describing it as a
        # zero-length night with nobody in it is not the truer statement — it is
        # how a hidden raid disappeared off its owner's own list. `raider_count`
        # is what partitions Raids from Solo/Group (`lib/raids.js`), so a NULL
        # there moved a 24-man raid to the other side of a filter that is on by
        # default, and the only way back to it was a switch nobody would think
        # to flip. Hiding a raid must never make it hard to un-hide.
        counted = [e for e in members if encounter_fp(e) not in hidden_fps]
        described = counted or members
        start, end = described[0]["started_ts"], described[-1]["ended_ts"]
        match = next(
            (ex for ex in existing
             if ex["id"] not in consumed and ex["zone"] == zone
             and start <= ex["ended_ts"] and end >= ex["started_ts"]), None)
        enc_ids = [e["id"] for e in members]
        roster = _roster(conn, [e["id"] for e in described])
        is_raid = int(
            zones.is_raid_run(zone, (e["name"] for e in described))
            or _raid_hp_outlier(
                conn, zone, described, len(roster or []), heroic_medians))
        fields = (
            zone, start, end, len(counted), len(members) - len(counted),
            sum(1 for e in counted if e["is_named"]),
            # named KILLS, not every success: trash carries a real success flag
            # now too, and "9/9 named" must mean nine bosses killed out of nine
            # engaged — which is only a meaningful ratio because a wipe can
            # finally be recorded as success=0
            sum(1 for e in counted if e["is_named"] and e["success"] == 1),
            sum(e["duration_s"] for e in counted),
            len(roster) if roster is not None else None,
            json_dumps(roster) if roster is not None else None, is_raid, now)
        if match is not None:
            consumed.add(match["id"])
            run_id = match["id"]
            conn.execute(
                "UPDATE zone_runs SET zone=?, started_ts=?, ended_ts=?, "
                "encounter_count=?, hidden_count=?, named_count=?, success_count=?, "
                "combat_s=?, raider_count=?, roster_json=?, is_raid=?, updated_ts=? "
                "WHERE id=?",
                fields + (run_id,))
        else:
            run_id = conn.execute(
                "INSERT INTO zone_runs (character_id, zone, started_ts, ended_ts, "
                "encounter_count, hidden_count, named_count, success_count, combat_s, "
                "raider_count, roster_json, is_raid, updated_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (character_id,) + fields).lastrowid
        for eid in enc_ids:
            run_id_of_enc[eid] = run_id

    stale = [ex["id"] for ex in existing if ex["id"] not in consumed]
    if stale:
        # a run that stopped existing usually didn't vanish — its fights joined
        # a neighbour (a merge edit, or a re-segmentation after a reparse). Hand
        # its shares to whichever run took the fights, or sharing would quietly
        # switch itself off behind the owner's back.
        gone = set(stale)
        successor: dict[int, int] = {}
        for e in all_encs:
            old, new = e["zone_run_id"], run_id_of_enc.get(e["id"])
            if old in gone and new is not None and old != new:
                successor.setdefault(old, new)
        groups.carry_shares(conn, successor)
        # the survivor has its copy; the originals still point at a row that is
        # about to go, and foreign_keys=ON refuses the delete while they do
        groups.drop_shares_for_runs(conn, stale)
        ph = ",".join("?" * len(stale))
        conn.execute(f"DELETE FROM zone_runs WHERE id IN ({ph})", stale)
        groups.drop_orphan_shares(conn)

    # deleted rows fall out of every run (run_id_of_enc/dup_of never name them)
    for e in all_encs:
        want_run = run_id_of_enc.get(e["id"])
        want_dup = dup_of.get(e["id"])
        if e["zone_run_id"] != want_run or e["dup_of"] != want_dup:
            conn.execute("UPDATE encounters SET zone_run_id=?, dup_of=? WHERE id=?",
                         (want_run, want_dup, e["id"]))

    # the guild tag was voted by a roster, so it has to follow the roster —
    # a merge, a split or a delete rewrites who was there, and a stale tag
    # would be a claim about a night that no longer exists. Pure SQL over
    # cached Census answers; it asks nobody anything.
    from census.guilds import retag_runs
    retag_runs(conn, character_id)

    # this function is the funnel every write ends in — uploads, live closes,
    # reparses, deletes and hand edits — so it is where cached read payloads
    # are thrown away (see memo.py)
    memo.invalidate()


def relink_all(conn: sqlite3.Connection) -> int:
    """Rebuild runs for every character that has encounters. -> characters seen."""
    heroic_medians = _heroic_hp_medians(conn)
    chars = [r["character_id"] for r in conn.execute(
        "SELECT DISTINCT s.character_id AS character_id FROM sessions s "
        "JOIN encounters e ON e.session_id = s.id")]
    for cid in chars:
        with conn:
            rebuild_zone_runs(conn, cid, heroic_medians)
    return len(chars)
