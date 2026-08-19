"""Real-time loot board: discovery blocks, private bids and test chest."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db as dbmod
import lootbids

TS = 1_786_924_324
HOOP = r"\aITEM -1813422462 -590025310:Hoop of War\/a"
HOOP_ID = 2481544834
SASH = r"\aITEM 1788430006 -1066324666:Dreamer's Sash\/a"
INVITE = ADMIN = None
ROOM_ID = None


def line(offset, body):
    return f"({TS + offset})[Sun Aug 16 19:52:04 2026] {body}"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    global INVITE, ADMIN, ROOM_ID
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "test.db")
    dbmod._local.conn = None
    dbmod.init_db()
    c = dbmod.get_db()
    with c:
        c.execute("INSERT INTO users (id,username,pw_hash,salt,created_ts) "
                  "VALUES (1,'test',X'00',X'00',?)", (TS,))
        c.execute("INSERT INTO characters (id,user_id,name) VALUES (1,1,'Logger')")
        c.execute("INSERT INTO sessions (id,character_id,source,status,created_ts) "
                  "VALUES (1,1,'live','receiving',?)", (TS,))
        c.execute("INSERT INTO encounters "
                  "(id,session_id,name,started_ts,ended_ts,duration_s) "
                  "VALUES (1,1,'Mayong Mistmoore',?,?,10)", (TS - 20, TS - 10))
    portal = lootbids.ensure_portal(c, TS)
    INVITE, ROOM_ID = portal["invite_code_plain"], portal["id"]
    ADMIN = lootbids._mint_member(c, "Rorschach", "officer", True, now=TS)[0]
    yield c
    c.close()
    dbmod._local.conn = None


def test_discovery_lists_every_item_before_loot_and_link_opens_one(conn):
    lootbids.absorb(conn, [
        line(0, "Rorschach opens Exquisite Chest and discovers: "),
        line(0, "     " + HOOP),
        line(0, "     " + HOOP),
        line(0, "     " + SASH),
        line(4, r'\aPC -1 Rorschach:Rorschach\/a says to the raid party, "' + HOOP + '"'),
        line(20, f"Alpha loots {HOOP} from the Exquisite Chest of Mayong Mistmoore."),
        line(21, f"Beta loots {HOOP} from the Exquisite Chest of Mayong Mistmoore."),
    ], "Logger", "live", TS + 4, session_id=1, room_id=ROOM_ID)
    rows = conn.execute("SELECT * FROM loot_bid_items ORDER BY id").fetchall()
    assert len(rows) == 2
    hoop = next(r for r in rows if r["item_id"] == HOOP_ID)
    assert hoop["qty"] == 2 and hoop["confirmed_qty"] == 2
    assert {r["mob"] for r in rows} == {"Mayong Mistmoore"}
    assert [r["state"] for r in rows].count("open") == 1
    assert next(r for r in rows if r["state"] == "open")["looter"] == "Rorschach"


def test_bids_are_sealed_even_from_officers_until_cutoff(conn):
    looter_token = ADMIN
    bidder_token, _ = lootbids.enroll(conn, "Daine", INVITE, TS)
    with conn:
        item = conn.execute(
            "INSERT INTO loot_bid_items "
            "(room_id,event_ts,chest,mob,item_id,item_name,state,looter,opened_ts,closes_ts,created_ts) "
            "VALUES (?,?,?,?,?,?,'open','Rorschach',?,?,?) RETURNING id",
            (ROOM_ID, TS, "Exquisite Chest", "Mayong", HOOP_ID, "Hoop of War", TS, TS + 45, TS)
        ).fetchone()[0]
    lootbids.put_bid(conn, bidder_token, item, 50, TS + 2)
    bidder_view = lootbids.state(conn, bidder_token, TS + 3)["items"][0]
    looter_view = lootbids.state(conn, looter_token, TS + 3)["items"][0]
    assert bidder_view["my_bid"]["bid"] == 50
    assert bidder_view["bids"] == []
    assert looter_view["bid_count"] == 1
    assert looter_view["bids"] == []
    with pytest.raises(RuntimeError, match="wait"):
        lootbids.finalize(conn, looter_token, item, now=TS + 3)
    awarded = lootbids.finalize(conn, looter_token, item, now=TS + 46)
    assert awarded["items"][0]["awards"] == [{"name": "Daine", "price": 5}]
    assert awarded["award_log"][0]["name"] == "Daine"
    assert awarded["award_log"][0]["item_name"] == "Hoop of War"
    assert awarded["award_log"][0]["price"] == 5


def test_two_copies_take_top_two_at_third_plus_one(conn):
    looter = ADMIN
    bidders = [lootbids.enroll(conn, name, INVITE, TS)[0]
               for name in ("Alpha", "Beta", "Gamma", "Delta")]
    with conn:
        item = conn.execute(
            "INSERT INTO loot_bid_items "
            "(room_id,event_ts,chest,mob,item_id,item_name,qty,state,looter,opened_ts,closes_ts,created_ts) "
            "VALUES (?,?,?,?,?,?,2,'open','Rorschach',?,?,?) RETURNING id",
            (ROOM_ID, TS, "Exquisite Chest", "Mayong", HOOP_ID, "Hoop of War", TS, TS + 45, TS)
        ).fetchone()[0]
    for token, bid in zip(bidders, (50, 40, 25, 5)):
        lootbids.put_bid(conn, token, item, bid, TS + 2)
    closed = lootbids.state(conn, looter, TS + 46)["items"][0]
    assert [(w["name"], w["price"]) for w in closed["projected_winners"]] == [
        ("Alpha", 27), ("Beta", 26)]
    result = lootbids.finalize(conn, looter, item, now=TS + 46)["items"][0]
    assert [(w["name"], w["price"]) for w in result["awards"]] == [
        ("Alpha", 27), ("Beta", 26)]


def test_officer_example_and_looter_can_adjust_before_confirming(conn):
    looter = ADMIN
    bidders = [lootbids.enroll(conn, name, INVITE, TS)[0] for name in ("Top", "Second", "Third")]
    with conn:
        item = conn.execute(
            "INSERT INTO loot_bid_items "
            "(room_id,event_ts,chest,mob,item_id,item_name,qty,state,looter,opened_ts,closes_ts,created_ts) "
            "VALUES (?,?,?,?,?,?,2,'open','Rorschach',?,?,?) RETURNING id",
            (ROOM_ID, TS, "Exquisite Chest", "Mayong", HOOP_ID, "Hoop of War", TS, TS + 45, TS)
        ).fetchone()[0]
    for token, bid in zip(bidders, (15, 11, 8)):
        lootbids.put_bid(conn, token, item, bid, TS + 2)
    closed = lootbids.state(conn, looter, TS + 46)["items"][0]
    assert [(w["name"], w["price"]) for w in closed["projected_winners"]] == [
        ("Top", 10), ("Second", 9)]
    # An officer can adjust the private draft before making the result public.
    adjusted = [{"bid_id": closed["bids"][0]["id"], "price": 9}]
    result = lootbids.finalize(conn, looter, item, adjusted, TS + 46)["items"][0]
    assert result["awards"] == [{"name": "Top", "price": 9}]


def test_button_opens_waiting_chest_and_links_one_item_at_a_time(conn):
    token, _ = lootbids._mint_member(conn, "Tester", "officer", now=TS)
    board = lootbids.open_test_chest(conn, token, TS + 1)
    assert len(board["items"]) == 3
    assert {i["state"] for i in board["items"]} == {"waiting"}
    assert {i["mob"] for i in board["items"]} == {"Training Dummy · test chest"}
    linked = lootbids.link_test_item(conn, token, board["items"][0]["id"], TS + 2)
    assert [i["state"] for i in linked["items"]].count("open") == 1
    assert [i["state"] for i in linked["items"]].count("waiting") == 2
    assert next(i for i in linked["items"] if i["state"] == "open")["is_looter"]


def test_officer_can_bid_but_player_cannot_run_loot(conn):
    officer, _ = lootbids._mint_member(conn, "Officerbid", "officer", now=TS)
    player, _ = lootbids.enroll(conn, "Member", INVITE, TS)
    board = lootbids.open_test_chest(conn, officer, TS + 1)
    item = board["items"][0]["id"]
    lootbids.link_test_item(conn, officer, item, TS + 2)
    assert lootbids.put_bid(conn, officer, item, 12, TS + 3)["items"][0]["my_bid"]["bid"] == 12
    with pytest.raises(PermissionError, match="officer"):
        lootbids.open_test_chest(conn, player, TS + 4)
    with pytest.raises(PermissionError, match="officer"):
        lootbids.relay_events(conn, player, [], "Member", now=TS + 4)


def test_invite_mints_one_persistent_token_and_admin_promotes_it(conn):
    member, board = lootbids.enroll(conn, "Returning", INVITE, TS)
    assert member.startswith("SI-") and board["guild"] == "Skill Issue"
    assert board["role"] == "bidder" and not board["can_manage"]
    admin = lootbids.state(conn, ADMIN, TS + 1)
    target = next(m for m in admin["members"] if m["name"] == "Returning")
    promoted = lootbids.set_member_role(conn, ADMIN, target["id"], True, TS + 2)
    assert next(m for m in promoted["members"] if m["name"] == "Returning")["role"] == "officer"
    # The member keeps exactly the same credential after promotion and next raid night.
    later = lootbids.state(conn, member, TS + 86400)
    assert later["role"] == "officer" and later["player"] == "Returning"


def test_member_name_can_follow_the_active_log_without_changing_token(conn):
    member, _ = lootbids.enroll(conn, "Oldname", INVITE, TS)
    changed = lootbids.update_profile(conn, member, "Newname", TS + 1)
    assert changed["player"] == "Newname"
    assert lootbids.state(conn, member, TS + 2)["player"] == "Newname"


def test_spades_account_bootstraps_as_portal_admin(conn):
    user = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
    with conn:
        conn.execute("UPDATE users SET username='spades' WHERE id=1")
    user = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
    token, board = lootbids.account_access(conn, user, TS)
    assert token and board["role"] == "officer" and board["can_manage"]
    assert board["access_label"] == "Portal admin"


def test_officer_relay_updates_live_zone_context(conn):
    board = lootbids.relay_events(
        conn, ADMIN, [line(0, "You have entered Veeshan's Peak.")],
        "Rorschach", zone="Veeshan's Peak", now=TS)
    assert board["zone"] == "Veeshan's Peak"
