"""Guild-tag sharing: the second standing-share branch.

A user connects a guild tag one of their characters wears to a group they are
in, and their uploads from that character flow there — no per-character rule to
remember the next time they level an alt.

What these tests pin down, beyond "it works":
  * the match is on the UPLOADER's character's Census guild, and only when
    Census has actually been asked (`guild_checked=1`)
  * a guild share is reported to the owner's Share control as a standing one,
    because ShareDialog saves back what that GET tells it
  * unticking one night writes a `hide` and the standing rule survives it
  * leaving the group takes the rule with you

A NEW FILE on purpose: `test_sharing.py` shares one module-scoped world between
its tests in a fixed order, and its last test says so — extra runs bolted onto
it would move the counts everything above asserts on.
"""

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

GUILD = "Gin And Jumjum"

# An eight-man roster and a solo dummy parse in one log, two hours apart so they
# land in two runs — the pair `raids_only` has to tell apart.
RAID_NIGHT = (
    "(1722643200)[Sat Aug  2 21:00:00 2026] You have entered The Emerald Halls.\r\n"
    "(1722643201)[Sat Aug  2 21:00:01 2026] YOU hit a dread lord for 100 crushing damage.\r\n"
    "(1722643202)[Sat Aug  2 21:00:02 2026] Alpha hits a dread lord for 110 crushing damage.\r\n"
    "(1722643203)[Sat Aug  2 21:00:03 2026] Bravo hits a dread lord for 120 crushing damage.\r\n"
    "(1722643204)[Sat Aug  2 21:00:04 2026] Charlie hits a dread lord for 130 crushing damage.\r\n"
    "(1722643205)[Sat Aug  2 21:00:05 2026] Delta hits a dread lord for 140 crushing damage.\r\n"
    "(1722643206)[Sat Aug  2 21:00:06 2026] Echo hits a dread lord for 150 crushing damage.\r\n"
    "(1722643207)[Sat Aug  2 21:00:07 2026] Foxtrot hits a dread lord for 160 crushing damage.\r\n"
    "(1722643208)[Sat Aug  2 21:00:08 2026] Golf hits a dread lord for 170 crushing damage.\r\n"
    "(1722643209)[Sat Aug  2 21:00:09 2026] You have killed a dread lord.\r\n"
)
SOLO_ZONE = (
    "(1722650400)[Sat Aug  2 23:00:00 2026] You have entered The Estate of Unrest.\r\n"
    "(1722650401)[Sat Aug  2 23:00:01 2026] YOU hit a training dummy for 100 crushing damage.\r\n"
    "(1722650403)[Sat Aug  2 23:00:03 2026] YOU hit a training dummy for 120 crushing damage.\r\n"
    "(1722650404)[Sat Aug  2 23:00:04 2026] You have killed a training dummy.\r\n"
)
# The groupmate's own night — a different zone and a different week, so nothing
# here is the same raid as the owner's.
MATE_NIGHT = (
    "(1723334400)[Mon Aug 11 21:00:00 2026] You have entered Munzok's Material Bastion.\r\n"
    "(1723334401)[Mon Aug 11 21:00:01 2026] YOU hit a war golem for 100 crushing damage.\r\n"
    "(1723334402)[Mon Aug 11 21:00:02 2026] Hotel hits a war golem for 110 crushing damage.\r\n"
    "(1723334403)[Mon Aug 11 21:00:03 2026] India hits a war golem for 120 crushing damage.\r\n"
    "(1723334404)[Mon Aug 11 21:00:04 2026] Juliet hits a war golem for 130 crushing damage.\r\n"
    "(1723334405)[Mon Aug 11 21:00:05 2026] Kilo hits a war golem for 140 crushing damage.\r\n"
    "(1723334406)[Mon Aug 11 21:00:06 2026] Lima hits a war golem for 150 crushing damage.\r\n"
    "(1723334407)[Mon Aug 11 21:00:07 2026] Mike hits a war golem for 160 crushing damage.\r\n"
    "(1723334408)[Mon Aug 11 21:00:08 2026] November hits a war golem for 170 crushing damage.\r\n"
    "(1723334409)[Mon Aug 11 21:00:09 2026] You have killed a war golem.\r\n"
)
# A second night, uploaded later, to prove the rule keeps firing after a `hide`.
SECOND_NIGHT = (
    "(1722729600)[Sun Aug  3 21:00:00 2026] You have entered Vaults of El'Arad.\r\n"
    "(1722729601)[Sun Aug  3 21:00:01 2026] YOU hit a vault guardian for 100 crushing damage.\r\n"
    "(1722729602)[Sun Aug  3 21:00:02 2026] Alpha hits a vault guardian for 110 crushing damage.\r\n"
    "(1722729603)[Sun Aug  3 21:00:03 2026] Bravo hits a vault guardian for 120 crushing damage.\r\n"
    "(1722729604)[Sun Aug  3 21:00:04 2026] Charlie hits a vault guardian for 130 crushing damage.\r\n"
    "(1722729605)[Sun Aug  3 21:00:05 2026] Delta hits a vault guardian for 140 crushing damage.\r\n"
    "(1722729606)[Sun Aug  3 21:00:06 2026] Echo hits a vault guardian for 150 crushing damage.\r\n"
    "(1722729607)[Sun Aug  3 21:00:07 2026] Foxtrot hits a vault guardian for 160 crushing damage.\r\n"
    "(1722729608)[Sun Aug  3 21:00:08 2026] Golf hits a vault guardian for 170 crushing damage.\r\n"
    "(1722729609)[Sun Aug  3 21:00:09 2026] You have killed a vault guardian.\r\n"
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-guildshare")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    import routers.uploads_api as uploads_api
    mp.setattr(uploads_api, "UPLOADS_DIR", tmp / "uploads")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as c:
        yield c
    mp.undo()


@pytest.fixture()
def conn(client):
    """A connection of our own, for seeding what Census would have cached. The
    app's is thread-local and the requests above may be served on another
    thread, so borrowing it is not safe."""
    c = sqlite3.connect(dbmod.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def sign_in(c, username, fresh=False):
    c.cookies.clear()
    body = {"username": username, "password": "hunter2hunter2"}
    if fresh:
        body |= {"sq_id": 1, "answer": "pet"}
    r = c.post(f"/api/auth/{'register' if fresh else 'login'}", json=body)
    assert r.status_code == 200, r.text
    return r.json()["user"]


def upload(c, name, content):
    r = c.post("/api/uploads", files={"file": ("log.txt", content.encode())},
               data={"character_name": name})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    for _ in range(60):
        s = c.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] in ("ready", "error"):
            assert s["status"] == "ready", s["error"]
            return sid
        time.sleep(0.1)
    raise AssertionError("parse never finished")


def set_guild(conn, name, guild, checked=1):
    """What `census/roster.py` would have written. Tests never call Census."""
    with conn:
        conn.execute(
            "INSERT INTO roster_classes (name_lower, world_id, name, class, found, "
            "checked_ts, guild_name, guild_checked) VALUES (?,618,?,'mystic',1,?,?,?) "
            "ON CONFLICT(name_lower, world_id) DO UPDATE SET "
            "guild_name=excluded.guild_name, guild_checked=excluded.guild_checked",
            (name.lower(), name, int(time.time()), guild, checked))


def connect_tag(client, gid, guild=GUILD, **opts):
    body = {"guild_name": guild, "history": opts.get("history", True),
            "group_content": opts.get("group_content", False)}
    return client.put(f"/api/groups/{gid}/guild-shares", json={"shares": [body]})


def theirs(client):
    """Runs that reached me from someone else, by zone."""
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    return {r["zone"]: r for r in runs if not r["mine"]}


def mine_in(shares, world):
    """The group under test's row out of `GET /shares`, which lists every group
    the caller is in."""
    return next(x for x in shares["groups"] if x["group_id"] == world["group"]["id"])


@pytest.fixture(scope="module")
def world(client):
    """owner uploads a raid night + a solo zone as Guildy; mate is in the group
    with them, stranger is in nothing."""
    sign_in(client, "gsowner", fresh=True)
    upload(client, "Guildy", RAID_NIGHT + SOLO_ZONE)
    runs = {r["zone"]: r for r in
            client.get("/api/zone-runs?scope=mine").json()["zone_runs"]}
    assert runs["The Emerald Halls"]["raider_count"] == 8
    assert runs["The Estate of Unrest"]["raider_count"] == 1
    group = client.post("/api/groups", json={"name": "Guild Night"}).json()["group"]
    code = client.get(f"/api/groups/{group['id']}").json()["group"]["join_code"]

    sign_in(client, "gsmate", fresh=True)
    assert client.post("/api/groups/join", json={"code": code}).status_code == 200
    sign_in(client, "gsstranger", fresh=True)
    return {"group": group, "runs": runs}


@pytest.fixture(autouse=True)
def clean_rules(client, world, conn):
    """Each test starts from no standing rules and no per-run overrides — a
    `hide` or a pinned since_ts left behind would quietly answer the next
    test's question for it."""
    yield
    with conn:
        conn.execute("DELETE FROM guild_shares")
        conn.execute("DELETE FROM run_shares")
    set_guild(conn, "Guildy", GUILD)


# ---- the visibility branch ----

def test_connected_tag_carries_the_raid_and_not_the_solo_zone(client, world, conn):
    """The point of the feature, and the default reading of it: raids from the
    character wearing the tag, nothing else, and only for people in the group."""
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    assert connect_tag(client, world["group"]["id"]).status_code == 200

    sign_in(client, "gsmate")
    got = theirs(client)
    assert list(got) == ["The Emerald Halls"]
    assert [g["name"] for g in got["The Emerald Halls"]["shared_via"]] == ["Guild Night"]

    sign_in(client, "gsstranger")
    assert theirs(client) == {}


def test_unchecked_guild_abstains(client, world, conn):
    """`guild_checked=0` means nobody ever asked — the same abstention the raid
    tag makes. A share that fired on it would leak a raid on the strength of a
    backfill that hasn't run, and one that fired the other way would go missing
    for as long as the queue is long."""
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    connect_tag(client, world["group"]["id"])
    set_guild(conn, "Guildy", GUILD, checked=0)

    sign_in(client, "gsmate")
    assert theirs(client) == {}

    # and it starts flowing the moment Census answers, with no re-save
    set_guild(conn, "Guildy", GUILD)
    assert list(theirs(client)) == ["The Emerald Halls"]


def test_tag_match_is_case_insensitive(client, world, conn):
    """Census's spelling and the user's are both just text. The COLLATE NOCASE
    on the comparison is what makes this pass — the column default alone would
    lose to `roster_classes.guild_name` being BINARY."""
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    assert connect_tag(client, world["group"]["id"],
                       guild="gin and jumjum").status_code == 200
    sign_in(client, "gsmate")
    assert list(theirs(client)) == ["The Emerald Halls"]


def test_history_false_withholds_the_back_catalogue(client, world, conn):
    """"From now on" means from now on: an already-uploaded night stays put, and
    the next one arrives."""
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    assert connect_tag(client, world["group"]["id"], history=False).status_code == 200

    sign_in(client, "gsmate")
    assert theirs(client) == {}

    # since_ts is a wall clock against the run's START, and these logs are dated
    # 2026 — a later upload of an older night is still older. Pull the pin back
    # to just before the raid to say "recorded while the share was on".
    with conn:
        conn.execute("UPDATE guild_shares SET since_ts=?",
                     (world["runs"]["The Emerald Halls"]["started_ts"] - 1,))
    assert list(theirs(client)) == ["The Emerald Halls"]


def test_group_content_brings_the_solo_zone(client, world, conn):
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    assert connect_tag(client, world["group"]["id"],
                       group_content=True).status_code == 200
    sign_in(client, "gsmate")
    assert sorted(theirs(client)) == ["The Emerald Halls", "The Estate of Unrest"]


# ---- the owner's share control ----

def test_owner_sees_the_group_as_a_standing_share(client, world, conn):
    """`auto: true` is what ShareDialog draws the tick from — and it saves back
    the set this GET returns, so a reaching group that went unmentioned here
    would be dropped on the next save and written a `hide`.

    The run the rule does NOT reach must come back `auto: false`, or unticking
    it would leave a `hide` blocking a later opt-in."""
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    connect_tag(client, world["group"]["id"])

    raid = world["runs"]["The Emerald Halls"]["id"]
    solo = world["runs"]["The Estate of Unrest"]["id"]
    on = mine_in(client.get(f"/api/zone-runs/{raid}/shares").json(), world)
    assert (on["shared"], on["auto"]) == (True, True)
    off = mine_in(client.get(f"/api/zone-runs/{solo}/shares").json(), world)
    assert (off["shared"], off["auto"]) == (False, False)


def test_untick_hides_one_night_and_the_rule_survives(client, world, conn):
    """One wipe stays yours without dismantling the standing rule — the reason
    `hide` exists at all. The rule has to keep firing for the nights around it,
    including ones uploaded afterwards, and re-ticking has to give the night
    back."""
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    connect_tag(client, world["group"]["id"])
    raid = world["runs"]["The Emerald Halls"]["id"]

    client.put(f"/api/zone-runs/{raid}/shares", json={"group_ids": []})
    sign_in(client, "gsmate")
    assert theirs(client) == {}

    # the rule is intact, so a later upload from the same character arrives
    sign_in(client, "gsowner")
    upload(client, "Guildy", SECOND_NIGHT)
    sign_in(client, "gsmate")
    assert list(theirs(client)) == ["Vaults of El'Arad"]

    sign_in(client, "gsowner")
    client.put(f"/api/zone-runs/{raid}/shares",
               json={"group_ids": [world["group"]["id"]]})
    sign_in(client, "gsmate")
    assert sorted(theirs(client)) == ["The Emerald Halls", "Vaults of El'Arad"]


def test_unrelated_save_does_not_hide_a_guild_shared_run(client, world, conn):
    """The four-site regression, as a test. ShareDialog seeds its save from
    `GET /shares`; if that GET failed to report the guild-shared group, the PUT
    would arrive without it and `set_run_shares` would write a `hide` — a raid
    silently unshared by an edit about a different group entirely."""
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    connect_tag(client, world["group"]["id"])
    other = client.post("/api/groups", json={"name": "Somewhere Else"}).json()["group"]
    raid = world["runs"]["The Emerald Halls"]["id"]

    # exactly what the dialog does: read, add one group, write the lot back
    seeded = [x["group_id"] for x in
              client.get(f"/api/zone-runs/{raid}/shares").json()["groups"] if x["shared"]]
    assert world["group"]["id"] in seeded
    client.put(f"/api/zone-runs/{raid}/shares",
               json={"group_ids": sorted({*seeded, other["id"]})})

    assert conn.execute("SELECT COUNT(*) FROM run_shares WHERE zone_run_id=? "
                        "AND mode='hide'", (raid,)).fetchone()[0] == 0
    sign_in(client, "gsmate")
    # `in`, not `==`: the untick test above left a second night behind, and the
    # rule under test reaches that too
    assert "The Emerald Halls" in theirs(client)
    sign_in(client, "gsowner")
    client.delete(f"/api/groups/{other['id']}?confirm=Somewhere Else")


# ---- who may do what ----

def test_a_groupmate_cannot_reshare_what_reached_them(client, world, conn):
    """Seeing is never changing. The raid is the owner's; a member it reached
    has no say in where it goes next."""
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    connect_tag(client, world["group"]["id"])
    raid = world["runs"]["The Emerald Halls"]["id"]

    sign_in(client, "gsmate")
    r = client.put(f"/api/zone-runs/{raid}/shares", json={"group_ids": []})
    assert r.status_code == 403, r.text


def test_a_tag_none_of_my_characters_wear_is_refused(client, world, conn):
    """Free text would be a rule that can never fire, sitting there looking
    like it works."""
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    r = connect_tag(client, world["group"]["id"], guild="Somebody Else's Guild")
    assert r.status_code == 422, r.text
    assert client.get("/api/guild-shares").json()["shares"] == []


def test_non_members_cannot_connect_a_tag(client, world, conn):
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsstranger")
    assert connect_tag(client, world["group"]["id"]).status_code == 404


# ---- leaving ----

def test_leaving_and_removal_take_the_rule_with_them(client, world, conn):
    """Rejoining must not silently reopen a back catalogue nobody re-asked for,
    so the rows go with the membership — the same thing `leave_group` has
    always done for a character's auto-share."""
    gid = world["group"]["id"]
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    connect_tag(client, gid)
    # a rule belonging to the member who gets removed, on a character of theirs.
    # A different night on purpose: re-uploading RAID_NIGHT verbatim is the same
    # night by the same roster, and raidmatch would rightly cluster the two.
    sign_in(client, "gsmate")
    upload(client, "Matey", MATE_NIGHT)
    set_guild(conn, "Matey", GUILD)
    assert connect_tag(client, gid).status_code == 200
    assert len(client.get("/api/guild-shares").json()["shares"]) == 1

    # the owner can't leave their own group; removing the member is the same path
    sign_in(client, "gsowner")
    mate_id = next(m["user_id"] for m in
                   client.get(f"/api/groups/{gid}").json()["group"]["members"]
                   if m["username"] == "gsmate")
    assert client.delete(f"/api/groups/{gid}/members/{mate_id}").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM guild_shares WHERE group_id=? "
                        "AND user_id=?", (gid, mate_id)).fetchone()[0] == 0

    sign_in(client, "gsmate")
    assert client.get("/api/guild-shares").json()["shares"] == []
    # rejoining gives back the membership and nothing else
    sign_in(client, "gsowner")
    code = client.get(f"/api/groups/{gid}").json()["group"]["join_code"]
    sign_in(client, "gsmate")
    assert client.post("/api/groups/join", json={"code": code}).status_code == 200
    assert client.get("/api/guild-shares").json()["shares"] == []


# ---- the report the Sharing page draws from ----

def test_guild_shares_report_names_the_unresolved_characters(client, world, conn):
    """A character Census hasn't been read for is not a character with no guild.
    The page has to be able to say "not resolved yet" rather than offering
    nothing and looking broken."""
    set_guild(conn, "Guildy", GUILD)
    sign_in(client, "gsowner")
    client.post("/api/characters", json={"name": "Freshalt"})
    d = client.get("/api/guild-shares").json()
    # one tag on offer, from the character Census has actually been read for
    assert d["guilds"] == [GUILD]
    by_name = {c["name"]: c for c in d["characters"]}
    assert by_name["Guildy"]["guild_name"] == GUILD
    assert by_name["Guildy"]["guild_checked"] == 1
    assert by_name["Freshalt"]["guild_checked"] == 0

    connect_tag(client, world["group"]["id"], group_content=True, history=False)
    assert client.get("/api/guild-shares").json()["shares"] == [
        {"group_id": world["group"]["id"], "guild_name": GUILD,
         "history": False, "group_content": True}]
