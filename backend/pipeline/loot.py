"""What the chests gave the raid.

**Chests only, never corpses.** The log writes both with the same verbs and the
source clause is the entire difference:

    Buls wins the lotto for <ITEM> from the Exquisite Chest of Zylphax the Shredder.
    Bobby wins the lotto for <ITEM> from the corpse of a doomed visitant.

A corpse drop is a shard, a body part and a vendor coin — it is not what a raid
remembers about a night, and mixing the two buries eight real drops under two
hundred. So the source clause is REQUIRED and must name a chest: a line with no
source at all (`wins the lotto for a <ITEM>.`, 45 of them in the archive) is
dropped too, because "probably a chest" is not evidence.

**Loot is not an event.** It is written beside the parse into `loot_drops` and
never into `events`, for a reason that would be expensive to discover later: a
looter is a bare NAME on a line, and pushing it through `EntityResolver` would
mint an entity — putting somebody who only walked past the chest into the
fight's roster, its class vote and its ACT parity. Nothing here resolves, rolls
up or segments; it only records.

**Two lines say what happened, and only one of them knows where it came from.**
The lotto/loot line names the chest and the mob; a second line confirms the
winner actually took it and is the only place the RARITY is written:

    Buls looted the Fabled <ITEM>.

The second line names no chest, so it can never create a drop — it is matched
back onto an unconfirmed one by (item, looter) and enriches it. An unconfirmed
win is kept rather than dropped: winning the roll and then declining the item is
a thing that happens, and the raid remembers the roll either way.

**The fight is found by the mob's name, not by the clock.** The chest names who
dropped it, which is exact where a time window is a guess — a chest is looted a
median 26s after the fight ends but the tail runs to 25 minutes (people finish
the pull, buff, then walk back). See `attribute()` for the ladder.
"""

import json
import re

from parser.prefix import split_prefix

# The four chests on this server. Named explicitly rather than matched as
# `\w+ Chest`, so a mob that happens to be called something Chest cannot
# manufacture a drop.
CHESTS = ("Exquisite Chest", "Ornate Chest", "Treasure Chest", "Small Chest")
_CHEST = "|".join(CHESTS)

# \aITEM <id> <crc>[ 0 0 0 2 <id>]:<Name>\/a — the FIRST id is the item, and it
# is the Census item id written signed (see items.unsign).
_ITEM = r"\\aITEM (?P<item>-?\d+)[-\d ]*:(?P<name>[^\\]+)\\/a"

# `X wins the lotto for [4 ]<ITEM> from the <Chest> of <Mob>.` — second person
# is `You win`, and quantities appear on trash chests ("4 <ITEM>").
RE_CHEST = re.compile(
    r"^(?P<who>\S+) (?:wins? the lotto for|loots?) "
    r"(?:(?P<qty>\d+) |an? |the )?" + _ITEM +
    r" from the (?P<chest>" + _CHEST + r") of (?P<mob>.+?)\.?$"
)
# The confirmation, which carries the rarity and nothing about the source.
RE_LOOTED = re.compile(
    r"^(?P<who>\S+) loot(?:ed)? the (?P<rarity>Mythical|Fabled|Legendary|Treasured|Handcrafted) "
    + _ITEM + r"\.?$"
)

# --- who rolled what ----------------------------------------------------------
#
# The lotto prints a BLOCK, and the block is the whole record of a contested
# drop: the item, everyone who wanted it and what they rolled, then the winner.
#
#     Now rolling on <ITEM>...
#     - Khael chooses GREED and rolls 43.
#     - Sadenx chooses GREED and rolls 79.
#     - Erin chose NEED.
#     Sadenx wins the lotto for <ITEM> from the Exquisite Chest of Bonesnapper.
#
# NEED beats GREED whatever the numbers say, which is why the choice is kept
# beside the roll rather than reduced to it. `chose X.` with no number is a
# real line (3,919 of them in the archive) — the choice was declared and the
# roll was not shown — so it is recorded as a roll with no VALUE rather than
# dropped, because "they wanted it" is most of what a loot list is for.
RE_ROLLING = re.compile(r"^Now rolling on (?:a |an |the )?" + _ITEM + r"\.*$")
RE_ROLL = re.compile(
    r"^- (?P<who>\S+) (?:chooses|choose) (?P<choice>NEED|GREED) "
    r"and rolls? (?P<value>\d+)\.?$"
)
RE_CHOSE = re.compile(r"^- (?P<who>\S+) chose (?P<choice>NEED|GREED)\.?$")
# `/random`, for the nights a raid used the dice instead of the lotto:
#
#     Random: Reyfiler rolls from 1 to 100 on the magic dice...and scores a 2!
#
# The `Random: ` prefix is the channel tag and it is NOT optional in practice —
# every one of the 122 in the archive carries it. It is written optional here
# only because the roller may instead arrive as a chat link (`\aPC …`), and a
# survey that normalised the first token mistook the tag for the name once
# already.
RE_DICE = re.compile(
    r"^(?:Random:\s*)?(?:\\aPC -?\d+ [^:]+:)?(?P<who>[^\\\s]+)(?:\\/a)? "
    r"rolls from (?P<lo>\d+) to (?P<hi>\d+) on the magic dice\.*"
    r"and scores a (?P<value>\d+)!?$"
)
# A raid that does not use the in-game lotto runs its loot BY HAND: somebody
# announces the item, everyone /randoms, and the winner is looted to. That is
# the whole reason the dice need attributing at all, and it is why the
# attribution is a LADDER rather than one rule — see `attach_dice`.
#
# The announcement is the only thing that names the item, and it is often
# missing: on the raid this was built against (Vestigial, MMIS 2026-08-08) just
# 5 of 38 drops had the item linked in chat, because the call goes out in
# Discord or in a channel where nobody links it. So the link is used when it is
# there and proximity is the fallback, marked as the guess it is.
#
# The dice themselves are not item-scoped — nothing in a `/random` line says
# what is being rolled for — so they are grouped into BURSTS (a run with no
# more than DICE_GAP_S between rolls is one contest) and matched whole. The
# window is TWO-SIDED: the burst sits on either side of the loot line depending
# on whether the chest was opened before or after the call, and on that night
# 22 of 39 drops had their nearest burst before them and 12 after — which is
# why a backward-only window found nothing at all.
DICE_S = 300
DICE_GAP_S = 30
# How long after an announcement its rolls may still arrive.
ANNOUNCE_S = 600
# `Rorschach says to the raid party, "<ITEM link>"` — the item being called.
RE_ANNOUNCE = re.compile(r"(?: says | tells | shouts | say | tell ).*?" + _ITEM)

# How long after a win its `looted` line may still confirm it. Generous on
# purpose: the pairing key is (item, looter), which is already tight, and a
# raider fumbling for a free bag slot is not a different drop.
CONFIRM_S = 900

# How long after a fight ends its chest may still be looted. The archive's
# median is 26s and its tail runs past twenty minutes — a raid finishes the
# pull, rebuffs, and walks back for the chest. Used both as the last rung of
# `attribute()` and as the window an UNATTRIBUTED chest is offered in.
NEAREST_S = 900


def _lotto_rolls(block: list[dict]) -> list[dict]:
    """One lotto block, in the order the game resolves it: NEED before GREED,
    and highest first inside each — a NEED of 12 beats a GREED of 98. Checked
    against 752 real blocks in the archive; the winner is the top line in every
    one of them."""
    return sorted(block, key=lambda r: (r["choice"] != "NEED",
                                        -(r["value"] or -1)))


def _bursts(dice: list[dict]) -> list[list[dict]]:
    """Loose `/random` rolls grouped into contests."""
    out: list[list[dict]] = []
    for d in dice:
        if out and d["ts"] - out[-1][-1]["ts"] <= DICE_GAP_S:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def attach_dice(drops: list[dict], dice: list[dict], announced: list[tuple]) -> None:
    """Give the drops the lotto said nothing about their `/random` contest.

    A POST-pass, not part of the scan, because a burst commonly lands AFTER
    the loot line and nothing inside a single forward pass can see that yet.

    The ladder, and every rung is marked on the result so a reader can tell
    them apart:

    1. **`lotto`** — already set by the scan. It names the item, so it cannot
       be wrong, and dice are never mixed in beside it: that would invent a
       contest out of two unrelated rolls.
    2. **`announced`** — somebody linked this exact item in chat and the rolls
       came in after. The link is the only thing that ties dice to an item, so
       where it exists it wins.
    3. **`nearby`** — the nearest burst, either side, within `DICE_S`. A
       PROXIMITY claim and nothing more, and the panel says so.
    """
    if not dice:
        return
    bursts = _bursts(dice)

    def use(d, burst, source):
        d["rolls"] = {"source": source,
                      "rolls": sorted(burst, key=lambda r: -(r["value"] or -1))}

    for d in drops:
        if d["rolls"]:
            continue
        # rung 2: the last time this exact item was called out before it landed
        calls = [ts for ts, iid in announced
                 if iid == d["item_id"] and ts <= d["ts"]]
        if calls:
            call = max(calls)
            after = [b for b in bursts
                     if 0 <= b[0]["ts"] - call <= ANNOUNCE_S
                     and b[0]["ts"] <= d["ts"] + DICE_S]
            if after:
                use(d, after[0], "announced")
                continue
        # rung 3: whatever was rolled nearest to it
        near = min(bursts, key=lambda b: min(abs(d["ts"] - r["ts"]) for r in b))
        if min(abs(d["ts"] - r["ts"]) for r in near) <= DICE_S:
            use(d, near, "nearby")


def unsign(item_id: int) -> int:
    """The log writes item ids as SIGNED 32-bit; Census answers to the unsigned
    value, and the two are the same number. Verified against `gamelink`, which
    Census returns in the log's own notation."""
    return item_id + 2**32 if item_id < 0 else item_id


def scan(lines, logger: str) -> list[dict]:
    """Chest drops from raw log lines, in log order. Pure: no DB, no network."""
    drops: list[dict] = []
    # (item_id, looter) -> indices of drops still waiting for a `looted` line
    open_wins: dict[tuple[int, str], list[int]] = {}
    # item_id -> the rolls of the lotto block open for it. Keyed by item rather
    # than "the last block", because several chests open at once and their
    # blocks interleave.
    rolls: dict[int, list[dict]] = {}
    rolling: int | None = None          # the block currently taking rolls
    dice: list[dict] = []               # loose /random rolls, newest last
    announced: list[tuple] = []         # (ts, item_id) an item was called out

    def whom(name: str) -> str:
        return logger if name in ("You", "you") else name

    for line in lines:
        split = split_prefix(line)
        if split is None:
            continue
        ts, body = split

        if body.startswith("- "):
            m = RE_ROLL.match(body) or RE_CHOSE.match(body)
            if m is not None and rolling is not None:
                got = m.groupdict()
                rolls.setdefault(rolling, []).append({
                    "who": whom(got["who"]),
                    "choice": got["choice"],
                    "value": int(got["value"]) if got.get("value") else None,
                })
            continue

        if "ITEM " not in body:
            m = RE_DICE.match(body)
            if m is not None:
                dice.append({"ts": ts, "who": whom(m["who"]),
                             "choice": None, "value": int(m["value"]),
                             "range": [int(m["lo"]), int(m["hi"])]})
            continue

        # Somebody calling the item out. Chat, so it is the ONE thing here a
        # raid can do differently — in Discord, in a channel, or by name with
        # no link at all — which is exactly why it is a rung and not the rule.
        m = RE_ANNOUNCE.search(body)
        if m is not None:
            announced.append((ts, unsign(int(m["item"]))))
            continue

        m = RE_ROLLING.match(body)
        if m is not None:
            rolling = unsign(int(m["item"]))
            # A second chest rolling the same item restarts its list rather
            # than appending to the last one's.
            rolls[rolling] = []
            continue

        m = RE_CHEST.match(body)
        if m is not None:
            looter = whom(m["who"])
            item_id = unsign(int(m["item"]))
            drops.append({
                "ts": ts,
                "chest": m["chest"],
                "mob": m["mob"],
                "item_id": item_id,
                "item_name": m["name"],
                "qty": int(m["qty"]) if m["qty"] else 1,
                "looter": looter,
                "method": "lotto" if "lotto" in body else "loot",
                "rarity": None,
                # Taking it outright IS the confirmation; there is no second
                # line coming for a `loots` drop.
                "confirmed": 0 if "lotto" in body else 1,
                "rolls": ({"source": "lotto",
                           "rolls": _lotto_rolls(rolls[item_id])}
                          if rolls.get(item_id) else None),
            })
            if "lotto" in body:
                open_wins.setdefault((item_id, looter), []).append(len(drops) - 1)
                # The block is spent: a second win for the same item is a
                # second chest, and it must not inherit this one's rolls.
                rolls.pop(item_id, None)
                if rolling == item_id:
                    rolling = None
            continue

        m = RE_LOOTED.match(body)
        if m is not None:
            key = (unsign(int(m["item"])), whom(m["who"]))
            waiting = open_wins.get(key)
            while waiting:
                idx = waiting.pop(0)
                if ts - drops[idx]["ts"] <= CONFIRM_S:
                    drops[idx]["rarity"] = m["rarity"]
                    drops[idx]["confirmed"] = 1
                    break
            # A `looted` line with no win behind it is a corpse drop, a quest
            # reward or a trade. It names no chest, so it is not ours.

    attach_dice(drops, dice, announced)
    return drops


def attribute(conn, session_id: int, drops: list[dict]) -> None:
    """Fill each drop's `encounter_id` from the mob the chest names.

    A ladder, most exact first, because a chest is looted after the fight and
    sometimes after the NEXT one has started:

    1. **The fight was named for that mob** — `encounters.name`, the common case.
    2. **That mob was IN the fight** — a chain pull is named for one mob and
       drops from another; the events say which fight it died in.
    3. **The last fight before the chest** — only within `NEAREST_S`, and marked
       as the guess it is, so a reader can tell it from the two above.

    Rung 2 reads `events`, which pruning eventually removes (`PRUNE_DAYS`); an
    old session falls to rung 3 or to nothing rather than to a WRONG fight. A
    drop with no fight is kept with `encounter_id` NULL — it still belongs to
    the raid, and the run page lists it under the night rather than a pull.
    """
    if not drops:
        return
    fights = conn.execute(
        "SELECT id, name, started_ts, ended_ts FROM encounters "
        "WHERE session_id=? ORDER BY started_ts", (session_id,)).fetchall()
    if not fights:
        for d in drops:
            d["encounter_id"], d["attribution"] = None, "none"
        return

    by_name: dict[str, list] = {}
    for f in fights:
        if f["name"]:
            by_name.setdefault(f["name"].lower(), []).append(f)

    # Which fight each mob NAME appears in, from the events themselves. One
    # query rather than one per drop: a session has a few hundred fights and a
    # handful of chests, and the join is the expensive half.
    wanted = {d["mob"].lower() for d in drops}
    in_fight: dict[str, list] = {}
    if wanted:
        rows = conn.execute(
            "SELECT DISTINCT e.encounter_id AS eid, LOWER(n.name) AS nm, "
            "       c.started_ts AS st "
            "FROM events e "
            "JOIN entities n ON n.id IN (e.src_entity, e.tgt_entity) "
            "JOIN encounters c ON c.id = e.encounter_id "
            "WHERE e.session_id=? AND e.encounter_id IS NOT NULL "
            f"  AND LOWER(n.name) IN ({','.join('?' * len(wanted))})",
            (session_id, *wanted)).fetchall()
        for r in rows:
            in_fight.setdefault(r["nm"], []).append(r)

    for d in drops:
        mob, ts = d["mob"].lower(), d["ts"]
        eid = kind = None

        prior = [f for f in by_name.get(mob, ()) if f["started_ts"] <= ts]
        if prior:
            eid, kind = max(prior, key=lambda f: f["started_ts"])["id"], "name"
        else:
            seen = [r for r in in_fight.get(mob, ()) if r["st"] <= ts]
            if seen:
                eid, kind = max(seen, key=lambda r: r["st"])["eid"], "entity"
            else:
                before = [f for f in fights if f["started_ts"] <= ts]
                if before and ts - before[-1]["ended_ts"] <= NEAREST_S:
                    eid, kind = before[-1]["id"], "nearest"

        d["encounter_id"], d["attribution"] = eid, kind or "none"


INSERT = (
    "INSERT OR REPLACE INTO loot_drops "
    "(session_id, encounter_id, ts, chest, mob, item_id, item_name, qty, "
    " looter, method, rarity, confirmed, attribution, rolls_json) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def write(conn, session_id: int, drops: list[dict]) -> int:
    """Replace this session's drops. Idempotent — a reparse or a re-run of the
    backfill lands on the same rows, and `INSERT OR REPLACE` on the natural key
    keeps a re-attributed drop pointing at the rebuilt fight."""
    conn.execute("DELETE FROM loot_drops WHERE session_id=?", (session_id,))
    conn.executemany(INSERT, [
        (session_id, d["encounter_id"], d["ts"], d["chest"], d["mob"],
         d["item_id"], d["item_name"], d["qty"], d["looter"], d["method"],
         d["rarity"], d["confirmed"], d["attribution"],
         json.dumps(d["rolls"]) if d.get("rolls") else None)
        for d in drops
    ])
    return len(drops)


def record(conn, session_id: int, lines, logger: str) -> int:
    """Scan, attribute and store in one call — what both the parse and the
    backfill actually want. The caller owns the transaction."""
    drops = scan(lines, logger)
    attribute(conn, session_id, drops)
    return write(conn, session_id, drops)
