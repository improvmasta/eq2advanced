"""Every raider's class from Census, by NAME — the game answering instead of
the parser guessing.

`pipeline/classguess.py` reads a spellbook and votes. It is good, and it is
still an inference: about half the ability names in a raid log have no Census
spell row (AAs, gear procs, item effects), so a third of the roster came out
blank and the rest carried a confidence number. Census has the actual answer
for anyone who exists on the server, and it does not care how many of their
abilities we recognise:

    character/?name.first_lower=zooey&locationdata.worldid=618  ->  Mystic

That is the whole idea. `characters` already held this for the handful of
people with an account here; `roster_classes` holds it for everybody who has
ever appeared in one of their raids.

**A miss is an answer and is cached too.** `Enynti` is not a character — it is
the Mistmoore's Inner Sanctum boss — and `found=0` records that so it is not
re-queried on every parse. Names go stale slowly (people betray, and the log's
own timeline catches that faster than a re-query would), so a hit is refreshed
on the order of weeks, a miss much sooner in case the name was simply typed
into the log before the character existed.

Census is authoritative about NOW, not about the night of the raid. Someone who
betrayed reads as their current class here, which is why `classguess` dates a
Census answer to the LATEST era and lets the log answer the earlier ones.

The GUILD rides along for free. The character doc that answers "what class" also
carries "what guild", so capturing it costs no extra request — and once every
raider's guild is cached, the raid's own guild is a vote over the roster
(census/guilds.py) rather than anything the log has to say. `guild_checked`
separates "asked, no guild" from "never asked": both leave `guild_name` NULL,
and only the first is allowed to count against a guild in that vote.
"""

import time

HIT_TTL_S = 30 * 86400      # a class is stable; the log catches a change sooner
MISS_TTL_S = 7 * 86400      # a name that was not there yet may be next week
DEFAULT_WORLD = 618         # Wuoshi (TLE)


def known_classes(conn, world_id: int = DEFAULT_WORLD) -> dict[str, str]:
    """lowercase name -> class, for every name Census resolved. This is the
    truth table `classguess` votes against."""
    return {r["name_lower"]: r["class"] for r in conn.execute(
        "SELECT name_lower, class FROM roster_classes "
        "WHERE world_id=? AND found=1 AND class IS NOT NULL", (world_id,))
        if r["class"]}


def stale_names(conn, names, world_id: int = DEFAULT_WORLD, now: int | None = None):
    """The subset of `names` worth asking Census about, oldest first so a
    budgeted run makes progress through a big roster instead of re-checking the
    same head of it."""
    now = int(time.time() if now is None else now)
    cached = {r["name_lower"]: r for r in conn.execute(
        "SELECT name_lower, found, checked_ts FROM roster_classes WHERE world_id=?",
        (world_id,))}
    out = []
    for name in names:
        row = cached.get(name.lower())
        if row is None:
            out.append((0, name))
            continue
        ttl = HIT_TTL_S if row["found"] else MISS_TTL_S
        if now - row["checked_ts"] >= ttl:
            out.append((row["checked_ts"], name))
    return [n for _, n in sorted(out)]


def resolve(conn, client, names, world_id: int = DEFAULT_WORLD,
            budget: int | None = None, now: int | None = None,
            force: bool = False, pace_s: float = 0.0) -> dict:
    """Look `names` up and cache what comes back. -> a small report.

    `budget` caps the requests one call may make, because this runs on the
    parse path and a first upload can carry 200 unseen names. Whatever is left
    over is simply still stale, and the next parse or the admin sweep picks it
    up — the cache is the point, not any single pass through it.

    `force` skips the TTL filter and asks about every name given. That is how
    the guild backfill re-reads rows cached before guilds existed: they are not
    stale by any clock, they are just missing a field. `pace_s` sleeps between
    requests so that background sweep stays a polite neighbour; the parse path
    leaves it at 0.

    A network failure is not an answer: it is swallowed per name and NOT
    cached, so a Census outage costs a retry rather than writing `found=0` over
    a real character. Runs in its own transaction."""
    now = int(time.time() if now is None else now)
    todo = list(names) if force else stale_names(conn, names, world_id, now)
    if budget is not None:
        todo = todo[:budget]
    found = missing = failed = 0
    rows = []
    for i, name in enumerate(todo):
        if pace_s and i:
            time.sleep(pace_s)
        try:
            doc = client.character_by_name(name, world_id)
        except Exception:
            failed += 1
            continue
        ctype = (doc or {}).get("type") or {}
        cls = (ctype.get("class") or "").strip().lower() or None
        if doc and cls:
            found += 1
            # `name` on a character doc is an object (prefix/first/last/...),
            # not a string — its `first` is what the log prints
            spelled = ((doc.get("name") or {}).get("first") or name)
            # no `guild` key is a real answer (they are in none), which is why
            # the checked flag goes to 1 either way
            g = doc.get("guild") or {}
            rows.append((name.lower(), world_id, spelled, cls,
                         ctype.get("level"), doc.get("id"), 1, now,
                         (g.get("name") or None), g.get("guildid"), 1))
        else:
            missing += 1
            # a name Census does not have has no guild fact at all — checked=0
            # keeps it out of the vote instead of reading as guildless
            rows.append((name.lower(), world_id, name, None, None, None, 0, now,
                         None, None, 0))
    if rows:
        with conn:
            conn.executemany(
                "INSERT INTO roster_classes (name_lower, world_id, name, class, level, "
                "census_character_id, found, checked_ts, guild_name, guild_id, "
                "guild_checked) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(name_lower, world_id) DO UPDATE SET "
                "name=excluded.name, class=excluded.class, level=excluded.level, "
                "census_character_id=excluded.census_character_id, "
                "found=excluded.found, checked_ts=excluded.checked_ts, "
                "guild_name=excluded.guild_name, guild_id=excluded.guild_id, "
                "guild_checked=excluded.guild_checked", rows)
    return {"asked": len(todo), "found": found, "missing": missing,
            "failed": failed, "remaining": len(stale_names(conn, names, world_id, now))}


def missing_names(conn, world_id: int = DEFAULT_WORLD) -> frozenset[str]:
    """Names Census was asked about and does not have. A definitive answer, not
    an absence of one: it means the query ran and returned no character. Used
    as one of the two negatives that demote a bare name out of the raid table
    (pipeline/refine.py)."""
    return frozenset(r["name"] for r in conn.execute(
        "SELECT name FROM roster_classes WHERE world_id=? AND found=0", (world_id,)))
