"""The raid's guild, voted by its roster.

Nothing in an EQ2 log says which guild a raid belongs to. The roster does, one
name at a time: `census/roster.py` already asks Census who each raider is, and
the doc that answers carries their guild. So the raid's guild is a majority
opinion of the people who were in it, and this module is the vote.

Two rules keep the tag from libelling somebody:

**Abstain on thin evidence.** If fewer than half the roster resolved, there is
no guild here — a tag drawn from six of twenty-four names is a guess wearing a
fact's clothes. The unresolved names are not neutral; they are unknown, and
about 18% of a real roster never resolves at all (pets, mobs, deleted names).

**Tag only on a strict majority of what IS known**, with known-guildless
counting AGAINST. Twelve Freethinkers and ten pick-ups is a Freethinkers raid;
three Freethinkers and eight unguilded friends is a pick-up group that happens
to have three guildies in it, and printing "Freethinkers" on it would be wrong
about the evening. Ties fail the strict test for free.

The tag is derived, never authored, so it is recomputed rather than maintained:
`retag_runs` is pure SQL over already-cached rows (zero Census calls), and every
write path that can change a roster calls it afterwards. NULL means "no majority
holds" and "not computed yet" alike — to every reader they are the same thing,
which is why there is no staleness column.
"""

import json

from census.roster import DEFAULT_WORLD, resolve
from groups import RAID_RUN

GUILD_BACKFILL_BUDGET = 120     # per hourly tick; drains ~1100 rows in a day
GUILD_BACKFILL_PACE_S = 0.75    # be a polite neighbour to a free public API


def majority_guild(roster, guild_of) -> str | None:
    """The guild a roster mostly belongs to, or None.

    `roster` is the night's names; `guild_of` maps lowercase name -> guild name
    or None, and holds ONLY names Census was actually asked about. A name that
    is absent abstains — it is not evidence of being unguilded.
    """
    roster = list(roster or [])
    if not roster:
        return None
    known = [guild_of[n.lower()] for n in roster if n.lower() in guild_of]
    # half a roster of strangers cannot name the raid
    if len(known) * 2 < len(roster):
        return None
    counts: dict[str, int] = {}
    for g in known:
        if g:
            counts[g] = counts.get(g, 0) + 1
    if not counts:
        return None
    top, n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    # strict majority OF THE KNOWN, so the guildless count against; a tie can
    # never clear it, which is the tie-break we want anyway
    return top if n * 2 > len(known) else None


def known_guilds(conn, world_id: int = DEFAULT_WORLD) -> dict[str, str | None]:
    """lowercase name -> guild name or None, for every name Census answered
    about. Mirrors `roster.known_classes`; the NULLs are meaningful here, so
    this cannot filter them out."""
    return {r["name_lower"]: r["guild_name"] for r in conn.execute(
        "SELECT name_lower, guild_name FROM roster_classes "
        "WHERE world_id=? AND found=1 AND guild_checked=1", (world_id,))}


def retag_runs(conn, character_id: int | None = None) -> int:
    """Recompute `zone_runs.guild` from the cached roster answers. -> rows
    changed. Optionally scoped to one character's runs (the parse path).

    Non-raid content is forced NULL: a guild pill on Castle Mistmoore's heroic
    crowd says something about people nearby, not about a raid night. Caller
    owns the transaction."""
    where = f"z.roster_json IS NOT NULL AND {RAID_RUN('z')}"
    params: list = []
    if character_id is not None:
        where += " AND z.character_id = ?"
        params.append(character_id)
    rows = conn.execute(
        f"SELECT z.id, z.guild, z.roster_json, "
        f"COALESCE(c.world_id, {DEFAULT_WORLD}) AS world_id "
        f"FROM zone_runs z JOIN characters c ON c.id = z.character_id "
        f"WHERE {where}", params).fetchall()

    maps: dict[int, dict] = {}
    changed = []
    for r in rows:
        wid = r["world_id"]
        if wid not in maps:
            maps[wid] = known_guilds(conn, wid)
        try:
            roster = json.loads(r["roster_json"]) or []
        except Exception:
            roster = []
        tag = majority_guild(roster, maps[wid])
        if tag != r["guild"]:
            changed.append((tag, r["id"]))

    # non-raid content always answers NULL, and a relink can reclassify a run
    # after it was tagged
    small = f"NOT {RAID_RUN('zone_runs')} OR roster_json IS NULL"
    small_params: list = []
    if character_id is not None:
        small = f"({small}) AND character_id = ?"
        small_params.append(character_id)
    cleared = conn.execute(
        f"UPDATE zone_runs SET guild = NULL WHERE guild IS NOT NULL "
        f"AND ({small})", small_params).rowcount

    if changed:
        conn.executemany("UPDATE zone_runs SET guild=? WHERE id=?", changed)
    return len(changed) + max(cleared, 0)


def backfill_stale_guilds(conn, client, budget: int = GUILD_BACKFILL_BUDGET,
                          world_id: int = DEFAULT_WORLD) -> dict:
    """Re-ask Census about rows cached before guilds existed. -> resolve report
    plus `remaining`.

    These rows are not stale by any TTL — their class is fine — so the refetch
    is forced, and it refreshes the class and the TTL clock for free while it is
    there. Oldest first, so repeated budgeted calls walk the whole backlog."""
    names = [r["name"] for r in conn.execute(
        "SELECT name FROM roster_classes WHERE world_id=? AND found=1 "
        "AND guild_checked=0 ORDER BY checked_ts LIMIT ?", (world_id, budget))]
    report = {"asked": 0, "found": 0, "missing": 0, "failed": 0}
    if names:
        report = resolve(conn, client, names, world_id, force=True,
                         pace_s=GUILD_BACKFILL_PACE_S)
    report["remaining"] = conn.execute(
        "SELECT COUNT(*) FROM roster_classes WHERE world_id=? AND found=1 "
        "AND guild_checked=0", (world_id,)).fetchone()[0]
    return report
