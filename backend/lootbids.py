"""Guild-scoped state for the raid-night loot experiment.

Live plugin batches are observed on their way through. Chest lines add items;
an item link in raid chat opens the newest matching, unopened item. Raid chat
text is never stored here -- only the speaker name and linked item id needed to
run the auction survive.

Each Skill Issue member has one durable personal token. Portal managers can
promote that member record, so the same token gains officer powers without a
new credential. A lightweight member can optionally link the record to a full
site account later. Bids stay sealed from everyone except their owner while the
timer is running; officers receive the bid table only after the auction closes.
"""

import hashlib
import os
import re
import secrets
import time

import items
from parser.prefix import split_prefix
from pipeline import loot

OPEN_S = int(os.environ.get("LOOT_BID_SECONDS", "45"))
MIN_BID = 5
BOARD_S = 12 * 60 * 60
DROP_DEDUPE_S = 20
ANNOUNCE_LOOKBACK_S = BOARD_S

_ITEM_LINK = re.compile(loot._ITEM)  # the log/Census id contract lives in loot.py
_RAID_LINE = re.compile(
    r'^(?P<speaker>.+?) says to the raid party, "(?P<message>.*)"$')
_PC_SPEAKER = re.compile(r"^\\aPC -?\d+ [^:]+:(?P<name>[^\\]+)\\/a$")
_OPEN_CHEST = re.compile(
    r"^(?P<who>.+?) opens (?P<chest>" + "|".join(loot.CHESTS) +
    r") and discovers:\s*$")
_DISCOVERED = re.compile(r"^\s+" + loot._ITEM + r"\s*$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z'`-]{1,29}$")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _name(value: str) -> str:
    value = (value or "").strip()
    if not _NAME.fullmatch(value):
        raise ValueError("enter a valid EQ2 character name")
    return value


def _code(prefix: str, size: int) -> str:
    return prefix + "-" + secrets.token_urlsafe(size).replace("_", "").replace("-", "").upper()


def ensure_portal(conn, now: int | None = None):
    now = int(now or time.time())
    room = conn.execute("SELECT * FROM loot_bid_rooms WHERE id=1").fetchone()
    if room is not None:
        return room
    invite = _code("SKILL-ISSUE", 10)
    with conn:
        conn.execute(
            "INSERT INTO loot_bid_rooms "
            "(id,label,invite_code_hash,invite_code_plain,created_ts,last_event_ts) "
            "VALUES (1,'Skill Issue',?,?,?,?)",
            (_hash(invite), invite, now, now))
    return conn.execute("SELECT * FROM loot_bid_rooms WHERE id=1").fetchone()


def _mint_member(conn, name: str, role: str = "bidder", can_manage: bool = False,
                 user_id: int | None = None, now: int | None = None) -> tuple[str, dict]:
    now = int(now or time.time())
    ensure_portal(conn, now)
    name = _name(name)
    if conn.execute("SELECT 1 FROM loot_bid_participants WHERE name=? COLLATE NOCASE",
                    (name,)).fetchone():
        raise ValueError("that player name already belongs to a portal member")
    token = _code("SI", 18)
    with conn:
        conn.execute(
            "INSERT INTO loot_bid_participants "
            "(token_hash,token_plain,room_id,user_id,name,role,can_manage,created_ts,last_seen_ts) "
            "VALUES (?,?,1,?,?,?,?,?,?)",
            (_hash(token), token, user_id, name, role, int(can_manage), now, now))
    return token, state(conn, token, now)


def enroll(conn, name: str, invite_code: str, now: int | None = None) -> tuple[str, dict]:
    """Exchange the private invite link's code for one durable member token."""
    now = int(now or time.time())
    room = ensure_portal(conn, now)
    if not secrets.compare_digest(_hash((invite_code or "").strip().upper()),
                                  room["invite_code_hash"]):
        raise PermissionError("that Skill Issue invite link is not valid")
    return _mint_member(conn, name, now=now)


def account_access(conn, user, now: int | None = None) -> tuple[str, dict] | None:
    """Recover portal access from a linked site account; bootstrap its managers."""
    if user is None:
        return None
    now = int(now or time.time())
    row = conn.execute("SELECT token_plain FROM loot_bid_participants WHERE user_id=?",
                       (user["id"],)).fetchone()
    if row is not None:
        return row["token_plain"], state(conn, row["token_plain"], now)
    if user["username"].casefold() not in {"bobby", "spades", "gabriel"}:
        return None
    named = conn.execute(
        "SELECT token_plain,token_hash,user_id FROM loot_bid_participants "
        "WHERE name=? COLLATE NOCASE", (user["username"],)).fetchone()
    if named is not None and named["user_id"] is None:
        with conn:
            conn.execute(
                "UPDATE loot_bid_participants SET user_id=?,role='officer',can_manage=1 "
                "WHERE token_hash=?", (user["id"], named["token_hash"]))
        return named["token_plain"], state(conn, named["token_plain"], now)
    return _mint_member(conn, user["username"].capitalize(), "officer", True,
                        user["id"], now)


def update_profile(conn, token: str, name: str, now: int | None = None) -> dict:
    who = participant(conn, token, touch=True, now=now)
    if who is None:
        raise PermissionError("this portal token is not valid")
    name = _name(name)
    duplicate = conn.execute(
        "SELECT 1 FROM loot_bid_participants WHERE name=? COLLATE NOCASE AND token_hash<>?",
        (name, who["token_hash"])).fetchone()
    if duplicate:
        raise ValueError("that player name already belongs to a portal member")
    with conn:
        conn.execute("UPDATE loot_bid_participants SET name=? WHERE token_hash=?",
                     (name, who["token_hash"]))
    return state(conn, token, now)


def set_member_role(conn, token: str, member_id: int, officer: bool,
                    now: int | None = None) -> dict:
    who = participant(conn, token, touch=True, now=now)
    if who is None or not who["can_manage"]:
        raise PermissionError("portal admin access required")
    target = conn.execute("SELECT token_hash,can_manage FROM loot_bid_participants WHERE rowid=?",
                          (member_id,)).fetchone()
    if target is None:
        raise LookupError("portal member not found")
    if target["can_manage"]:
        raise PermissionError("Spades and Gabriel remain portal admins")
    with conn:
        conn.execute("UPDATE loot_bid_participants SET role=? WHERE rowid=?",
                     ("officer" if officer else "bidder", member_id))
    return state(conn, token, now)


def participant(conn, token: str, touch: bool = False, now: int | None = None):
    if not token:
        return None
    row = conn.execute(
        "SELECT rowid member_id,token_hash,token_plain,room_id,user_id,name,role,can_manage "
        "FROM loot_bid_participants WHERE token_hash=?",
        (_hash(token),)).fetchone()
    if row is not None and touch:
        conn.execute("UPDATE loot_bid_participants SET last_seen_ts=? WHERE token_hash=?",
                     (int(now or time.time()), row["token_hash"]))
    return row


def _close_expired(conn, now: int) -> None:
    conn.execute(
        "UPDATE loot_bid_items SET state='closed' "
        "WHERE state='open' AND closes_ts<=?", (now,))


def _speaker(raw: str, logger: str) -> str:
    if raw == "You":
        return logger
    m = _PC_SPEAKER.match(raw)
    return m["name"] if m else raw


def _recent_mob(conn, session_id: int | None, ts: int, room_id: int,
                supplied: str | None = None) -> str:
    if supplied:
        return supplied
    if session_id is None:
        row = conn.execute("SELECT current_mob FROM loot_bid_rooms WHERE id=?",
                           (room_id,)).fetchone()
        return row["current_mob"] if row and row["current_mob"] else "Unattributed chest"
    row = conn.execute(
        "SELECT name FROM encounters WHERE session_id=? AND ended_ts<=? "
        "AND ended_ts>=? ORDER BY ended_ts DESC,id DESC LIMIT 1",
        (session_id, ts, ts - loot.NEAREST_S)).fetchone()
    return row["name"] if row else "Unattributed chest"


def _add_discovered(conn, room_id: int, session_id: int | None, ts: int, chest: str, mob: str,
                    match, now: int) -> int:
    got = match.groupdict()
    item_id = loot.unsign(int(got["item"]))
    same_chest = conn.execute(
        "SELECT id FROM loot_bid_items WHERE room_id=? AND source_session_id IS ? AND item_id=? "
        "AND chest=? AND event_ts=? ORDER BY id LIMIT 1",
        (room_id, session_id, item_id, chest, ts)).fetchone()
    if same_chest:
        conn.execute("UPDATE loot_bid_items SET qty=qty+1 WHERE id=?",
                     (same_chest["id"],))
        return same_chest["id"]
    existing = conn.execute(
        "SELECT id FROM loot_bid_items WHERE room_id=? AND source_session_id IS NOT ? AND item_id=? "
        "AND chest=? AND mob=? AND ABS(event_ts-?)<=? ORDER BY ABS(event_ts-?),id LIMIT 1",
        (room_id, session_id, item_id, chest, mob, ts, DROP_DEDUPE_S, ts)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO loot_bid_items "
        "(room_id,source_session_id,event_ts,chest,mob,item_id,item_name,qty,created_ts) "
        "VALUES (?,?,?,?,?,?,?,1,?)",
        (room_id, session_id, ts, chest, mob, item_id, got["name"], now))
    return cur.lastrowid


def _add_drop(conn, room_id: int, session_id: int | None, ts: int, match,
              logger: str, now: int) -> int:
    got = match.groupdict()
    item_id = loot.unsign(int(got["item"]))

    # First enrich the quantity row from the earlier discovery block.
    # `confirmed_qty` consumes duplicate copies one by one, so their later
    # loot lines do not manufacture a second auction.
    discovered = conn.execute(
        "SELECT id FROM loot_bid_items WHERE room_id=? AND source_session_id IS ? AND item_id=? "
        "AND confirmed_qty<qty AND event_ts BETWEEN ? AND ? "
        "ORDER BY event_ts,id LIMIT 1",
        (room_id, session_id, item_id, ts - ANNOUNCE_LOOKBACK_S, ts)).fetchone()
    if discovered:
        conn.execute(
            "UPDATE loot_bid_items SET chest=?,mob=?,confirmed_ts=?,"
            "confirmed_qty=MIN(qty,confirmed_qty+?) WHERE id=?",
            (got["chest"], got["mob"], ts, int(got.get("qty") or 1),
             discovered["id"]))
        return discovered["id"]

    # The same raid line commonly arrives from several raiders' plugins. Reuse
    # another session's near-identical observation; repeated identical drops
    # from one source remain distinct rather than being silently collapsed.
    existing = conn.execute(
        "SELECT id FROM loot_bid_items WHERE room_id=? AND source_session_id IS NOT ? AND item_id=? "
        "AND mob=? AND ABS(event_ts-?)<=? ORDER BY ABS(event_ts-?) LIMIT 1",
        (room_id, session_id, item_id, got["mob"], ts, DROP_DEDUPE_S, ts)).fetchone()
    if existing:
        return existing["id"]

    cur = conn.execute(
        "INSERT INTO loot_bid_items "
        "(room_id,source_session_id,event_ts,chest,mob,item_id,item_name,qty,confirmed_ts,"
        "confirmed_qty,created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (room_id, session_id, ts, got["chest"], got["mob"], item_id, got["name"],
         int(got.get("qty") or 1), ts, int(got.get("qty") or 1), now))
    return cur.lastrowid


def _open_item(conn, room_id: int, ts: int, item_id: int, speaker: str,
               now: int) -> int | None:
    row = conn.execute(
        "SELECT id,state FROM loot_bid_items WHERE room_id=? AND item_id=? "
        "AND state IN ('waiting','closed','awarded') "
        "AND event_ts BETWEEN ? AND ? ORDER BY event_ts DESC, id DESC LIMIT 1",
        (room_id, item_id, ts - ANNOUNCE_LOOKBACK_S, ts + DROP_DEDUPE_S)).fetchone()
    if row is None:
        return None
    if row["state"] in ("closed", "awarded"):
        conn.execute("DELETE FROM loot_bid_awards WHERE item_row_id=?", (row["id"],))
        conn.execute("DELETE FROM loot_bids WHERE item_row_id=?", (row["id"],))
    conn.execute(
        "UPDATE loot_bid_items SET state='open',looter=?,opened_ts=?,closes_ts=?,"
        "winner_bid_id=NULL WHERE id=?",
        (speaker, now, now + OPEN_S, row["id"]))
    return row["id"]


def _announce(conn, room_id: int, session_id: int | None, ts: int, item_id: int,
              speaker: str, now: int) -> int | None:
    # The same raid-chat line arrives through several raiders' plugins. One
    # link must start one auction, not consume the next identical waiting drop.
    duplicate = conn.execute(
        "SELECT id FROM loot_bid_announcements WHERE room_id=? AND item_id=? "
        "AND speaker=? COLLATE NOCASE AND ABS(event_ts-?)<=? LIMIT 1",
        (room_id, item_id, speaker, ts, DROP_DEDUPE_S)).fetchone()
    if duplicate:
        return None
    conn.execute(
        "INSERT INTO loot_bid_announcements "
        "(room_id,source_session_id,event_ts,item_id,speaker,created_ts) "
        "VALUES (?,?,?,?,?,?)",
        (room_id, session_id, ts, item_id, speaker, now))
    return _open_item(conn, room_id, ts, item_id, speaker, now)


def absorb(conn, lines: list[str], logger: str, mode: str, now: int,
           session_id: int | None = None, room_id: int | None = None,
           mob: str | None = None) -> None:
    """Observe accepted live lines inside ingest's existing transaction."""
    if mode != "live" or not lines or room_id is None:
        return
    opened = None  # batches never split a log second, so the item block stays whole
    for line in lines:
        split = split_prefix(line)
        if split is None:
            continue
        ts, body = split
        opening = _OPEN_CHEST.match(body)
        if opening is not None:
            opened = (ts, opening["chest"], _recent_mob(conn, session_id, ts, room_id, mob))
            continue
        discovered = _DISCOVERED.match(body) if opened and ts == opened[0] else None
        if discovered is not None:
            _add_discovered(conn, room_id, session_id, ts, opened[1], opened[2], discovered, now)
            continue
        if opened and ts != opened[0]:
            opened = None

        chest = loot.RE_CHEST.match(body) if "ITEM " in body else None
        if chest is not None:
            _add_drop(conn, room_id, session_id, ts, chest, logger, now)
            continue

        raid = _RAID_LINE.match(body)
        if raid is None or "ITEM " not in raid["message"]:
            continue
        speaker = _speaker(raid["speaker"], logger)
        for link in _ITEM_LINK.finditer(raid["message"]):
            _announce(conn, room_id, session_id, ts, loot.unsign(int(link["item"])), speaker, now)
    conn.execute("UPDATE loot_bid_rooms SET last_event_ts=? WHERE id=?", (now, room_id))


def state(conn, token: str, now: int | None = None) -> dict:
    now = int(now or time.time())
    who = participant(conn, token, touch=True, now=now)
    if who is None:
        raise PermissionError("join the loot board first")
    with conn:
        _close_expired(conn, now)
        conn.execute("DELETE FROM loot_bid_announcements WHERE event_ts<?",
                     (now - BOARD_S,))
        conn.execute("DELETE FROM loot_bid_items WHERE event_ts<?", (now - BOARD_S,))

    rows = conn.execute(
        "SELECT * FROM loot_bid_items WHERE room_id=? AND event_ts>=? "
        "ORDER BY event_ts DESC, id DESC", (who["room_id"], now - BOARD_S)).fetchall()
    cards = items.cards(conn, {r["item_id"] for r in rows}) if rows else {}
    out = []
    for row in rows:
        mine = conn.execute(
            "SELECT b.id,CAST(b.bid AS INTEGER) bid,b.updated_ts FROM loot_bids b "
            "JOIN loot_bid_participants p ON p.token_hash=b.participant_hash "
            "WHERE b.item_row_id=? AND p.name=? COLLATE NOCASE",
            (row["id"], who["name"])).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) FROM loot_bids WHERE item_row_id=?", (row["id"],)
        ).fetchone()[0]
        is_looter = who["role"] == "officer"
        bids = []
        # Sealed bidding: even officers and the linking looter cannot inspect
        # amounts or names until the countdown has ended.
        if is_looter and row["state"] in ("closed", "awarded"):
            bids = [dict(r) for r in conn.execute(
                "SELECT b.id,p.name,CAST(b.bid AS INTEGER) bid,b.updated_ts, "
                "CASE WHEN b.id=i.winner_bid_id THEN 1 ELSE 0 END winner "
                "FROM loot_bids b JOIN loot_bid_participants p "
                "ON p.token_hash=b.participant_hash "
                "JOIN loot_bid_items i ON i.id=b.item_row_id "
                "WHERE b.item_row_id=? ORDER BY CAST(b.bid AS INTEGER) DESC, "
                "b.updated_ts,b.id", (row["id"],))]
        awards = [dict(r) for r in conn.execute(
            "SELECT p.name,a.price FROM loot_bid_awards a "
            "JOIN loot_bids b ON b.id=a.bid_id JOIN loot_bid_participants p "
            "ON p.token_hash=b.participant_hash WHERE a.item_row_id=? "
            "ORDER BY CAST(b.bid AS INTEGER) DESC,b.id", (row["id"],))]
        projected = _resolution(conn, row) if is_looter and row["state"] == "closed" else []
        card = cards.get(row["item_id"]) or {}
        out.append({
            **items.display(card),
            "id": row["id"], "ts": row["event_ts"], "chest": row["chest"],
            "mob": row["mob"], "item_id": row["item_id"],
            "name": card.get("name") or row["item_name"], "qty": row["qty"],
            "state": row["state"], "looter": row["looter"],
            "opened_ts": row["opened_ts"], "closes_ts": row["closes_ts"],
            "bid_count": count, "my_bid": dict(mine) if mine else None,
            "is_looter": is_looter, "is_officer": is_looter, "bids": bids,
            "projected_winners": projected, "awards": awards,
        })
    room = conn.execute("SELECT label,current_zone FROM loot_bid_rooms WHERE id=?",
                        (who["room_id"],)).fetchone()
    award_log = [dict(r) for r in conn.execute(
        "SELECT id,item_id,item_name,mob,winner_name name,price,awarded_ts "
        "FROM loot_bid_award_log WHERE room_id=? AND awarded_ts>=? "
        "ORDER BY awarded_ts DESC,id DESC LIMIT 100",
        (who["room_id"], now - BOARD_S))]
    members = []
    invite_code = None
    if who["can_manage"]:
        members = [dict(r) for r in conn.execute(
            "SELECT rowid id,name,role,can_manage,user_id IS NOT NULL has_account "
            "FROM loot_bid_participants ORDER BY can_manage DESC,role DESC,name COLLATE NOCASE")]
        invite_code = conn.execute(
            "SELECT invite_code_plain FROM loot_bid_rooms WHERE id=?",
            (who["room_id"],)).fetchone()[0]
    return {"player": who["name"], "role": who["role"],
            "access_label": "Portal admin" if who["can_manage"] else (
                "Officer" if who["role"] == "officer" else "Member"),
            "can_manage": bool(who["can_manage"]),
            "has_account": who["user_id"] is not None,
            "guild": room["label"] if room else None,
            "zone": room["current_zone"] if room else None,
            "room": room["label"] if room else None, "server_time": now,
            "bid_seconds": OPEN_S, "minimum_bid": MIN_BID, "items": out,
            "award_log": award_log, "members": members,
            "invite_code": invite_code}


def put_bid(conn, token: str, item_row_id: int, value: str,
            now: int | None = None) -> dict:
    now = int(now or time.time())
    who = participant(conn, token, touch=True, now=now)
    if who is None:
        raise PermissionError("join the loot board first")
    try:
        value = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("bid must be a whole number")
    if value < MIN_BID:
        raise ValueError(f"minimum bid is {MIN_BID}")
    if value > 999_999:
        raise ValueError("bid is too large")
    with conn:
        _close_expired(conn, now)
        item = conn.execute(
            "SELECT state,closes_ts FROM loot_bid_items WHERE id=? AND room_id=?",
            (item_row_id, who["room_id"])).fetchone()
        if item is None:
            raise LookupError("loot item not found")
        if item["state"] != "open" or item["closes_ts"] <= now:
            raise RuntimeError("bidding has closed")
        # Name is the experimental identity. A returning browser with a new
        # key updates the same player's bid rather than creating a second one.
        conn.execute(
            "DELETE FROM loot_bids WHERE item_row_id=? AND participant_hash<>? "
            "AND participant_hash IN (SELECT token_hash FROM loot_bid_participants "
            "WHERE name=? COLLATE NOCASE)",
            (item_row_id, who["token_hash"], who["name"]))
        conn.execute(
            "INSERT INTO loot_bids "
            "(item_row_id,participant_hash,bid,created_ts,updated_ts) VALUES (?,?,?,?,?) "
            "ON CONFLICT(item_row_id,participant_hash) DO UPDATE SET "
            "bid=excluded.bid,updated_ts=excluded.updated_ts",
            (item_row_id, who["token_hash"], str(value), now, now))
    return state(conn, token, now)


TEST_ITEMS = (
    (2481544834, "Hoop of War", 2),
    (1788430006, "Dreamer's Sash", 1),
    (1135882972, "Fabled Faydwer Plate Pattern: Chest", 1),
)


def open_test_chest(conn, token: str, now: int | None = None) -> dict:
    """Reveal three waiting items without ACT, clearly labeled as test."""
    now = int(now or time.time())
    who = participant(conn, token, touch=True, now=now)
    if who is None:
        raise PermissionError("join the loot board first")
    if who["role"] != "officer":
        raise PermissionError("officer access required")
    with conn:
        for item_id, item_name, qty in TEST_ITEMS:
            conn.execute(
                "INSERT INTO loot_bid_items "
                "(room_id,event_ts,chest,mob,item_id,item_name,qty,state,created_ts) "
                "VALUES (?,?,?,?,?,?,?,'waiting',?)",
                (who["room_id"], now, "Exquisite Chest", "Training Dummy · test chest", item_id,
                 item_name, qty, now))
    return state(conn, token, now)


def link_test_item(conn, token: str, item_row_id: int,
                   now: int | None = None) -> dict:
    """Simulate this player linking one test item in raid chat."""
    now = int(now or time.time())
    who = participant(conn, token, touch=True, now=now)
    if who is None:
        raise PermissionError("join the loot board first")
    with conn:
        item = conn.execute("SELECT * FROM loot_bid_items WHERE id=? AND room_id=?",
                            (item_row_id, who["room_id"])).fetchone()
        if item is None:
            raise LookupError("loot item not found")
        if who["role"] != "officer":
            raise PermissionError("officer access required")
        if item["mob"] != "Training Dummy · test chest":
            raise PermissionError("only test-chest items can be simulated here")
        if item["state"] == "open":
            raise RuntimeError("that item's bidding is already open")
        if item["state"] in ("closed", "awarded"):
            conn.execute("DELETE FROM loot_bid_awards WHERE item_row_id=?", (item_row_id,))
            conn.execute("DELETE FROM loot_bids WHERE item_row_id=?", (item_row_id,))
        conn.execute(
            "UPDATE loot_bid_items SET state='open',looter=?,opened_ts=?,closes_ts=?,"
            "winner_bid_id=NULL WHERE id=?",
            (who["name"], now, now + OPEN_S, item_row_id))
    return state(conn, token, now)


def relay_events(conn, token: str, lines: list[str], logger: str,
                 mob: str | None = None, zone: str | None = None,
                 now: int | None = None) -> dict:
    """Officer plugin relay: raw local signal lines into exactly one guild.

    The lines are scanned inside this request and never stored. This is separate
    from the uploader/device-token contract: a bidder/officer room key can do
    loot and nothing involving parses.
    """
    now = int(now or time.time())
    who = participant(conn, token, touch=True, now=now)
    if who is None or who["role"] != "officer":
        raise PermissionError("officer access required")
    if (not isinstance(lines, list) or len(lines) > 250
            or not all(isinstance(line, str) and len(line) <= 8192 for line in lines)):
        raise ValueError("lines must be a list of at most 250 log lines")
    logger = _name(logger)
    mob = (mob or "").strip()[:120] or None
    zone = (zone or "").strip()[:120] or None
    with conn:
        if mob or zone:
            previous = conn.execute(
                "SELECT current_zone,current_mob FROM loot_bid_rooms WHERE id=?",
                (who["room_id"],)).fetchone()
            changed_zone = zone is not None and (
                previous is None or previous["current_zone"] != zone)
            next_mob = mob if mob is not None else (
                None if changed_zone else (previous["current_mob"] if previous else None))
            conn.execute(
                "UPDATE loot_bid_rooms SET current_mob=?,"
                "current_zone=COALESCE(?,current_zone),last_event_ts=? WHERE id=?",
                (next_mob, zone, now, who["room_id"]))
        absorb(conn, lines, logger, "live", now, room_id=who["room_id"], mob=mob)
    return state(conn, token, now)


def _resolution(conn, item) -> list[dict]:
    ranked = [dict(r) for r in conn.execute(
        "SELECT b.id,p.name,CAST(b.bid AS INTEGER) bid FROM loot_bids b "
        "JOIN loot_bid_participants p ON p.token_hash=b.participant_hash "
        "WHERE b.item_row_id=? ORDER BY CAST(b.bid AS INTEGER) DESC,b.updated_ts,b.id",
        (item["id"],))]
    winners = ranked[:item["qty"]]
    cutoff = ranked[item["qty"]]["bid"] if len(ranked) > item["qty"] else MIN_BID - 1
    # Officer-confirmed duplicate ladder: the lowest winning copy is first
    # loser +1, the next is +2, and so on. 15/11/8 for two copies -> 10 and 9.
    return [
        {**winner, "price": min(winner["bid"],
                                 max(MIN_BID, cutoff + len(winners) - rank))}
        for rank, winner in enumerate(winners)
    ]


def finalize(conn, token: str, item_row_id: int, adjustments: list | None = None,
             now: int | None = None) -> dict:
    now = int(now or time.time())
    who = participant(conn, token, touch=True, now=now)
    if who is None:
        raise PermissionError("join the loot board first")
    with conn:
        _close_expired(conn, now)
        item = conn.execute("SELECT * FROM loot_bid_items WHERE id=? AND room_id=?",
                            (item_row_id, who["room_id"])).fetchone()
        if item is None:
            raise LookupError("loot item not found")
        if who["role"] != "officer":
            raise PermissionError("officer access required")
        if item["state"] == "open":
            raise RuntimeError("wait for bidding to close")
        if item["state"] != "closed":
            raise RuntimeError("only a closed auction can be awarded")
        winners = _resolution(conn, item)
        if adjustments is not None:
            if not isinstance(adjustments, list) or not adjustments:
                raise ValueError("choose at least one winner")
            if len(adjustments) > item["qty"]:
                raise ValueError("too many winners for this item")
            if (any(not isinstance(a, dict) for a in adjustments)
                    or len({a.get("bid_id") for a in adjustments}) != len(adjustments)):
                raise ValueError("a bidder can only win once")
            chosen = []
            for award in adjustments:
                bid = conn.execute(
                    "SELECT b.id,p.name,CAST(b.bid AS INTEGER) bid FROM loot_bids b "
                    "JOIN loot_bid_participants p ON p.token_hash=b.participant_hash "
                    "WHERE b.id=? AND b.item_row_id=?",
                    (award.get("bid_id"), item_row_id)).fetchone()
                try:
                    price = int(award.get("price"))
                except (TypeError, ValueError):
                    raise ValueError("winner price must be a whole number")
                if bid is None:
                    raise ValueError("that bid does not belong to this item")
                if price < MIN_BID or price > bid["bid"]:
                    raise ValueError(f"winner price must be between {MIN_BID} and their bid")
                chosen.append({**dict(bid), "price": price})
            winners = chosen
        if not winners:
            raise ValueError("there are no bids to award")
        conn.executemany(
            "INSERT OR REPLACE INTO loot_bid_awards (item_row_id,bid_id,price,created_ts) "
            "VALUES (?,?,?,?)",
            [(item_row_id, w["id"], w["price"], now) for w in winners])
        conn.executemany(
            "INSERT INTO loot_bid_award_log "
            "(room_id,item_row_id,item_id,item_name,mob,winner_name,price,awarded_ts) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(who["room_id"], item_row_id, item["item_id"], item["item_name"],
              item["mob"], w["name"], w["price"], now) for w in winners])
        conn.execute("UPDATE loot_bid_items SET state='awarded' WHERE id=?",
                     (item_row_id,))
    return state(conn, token, now)
