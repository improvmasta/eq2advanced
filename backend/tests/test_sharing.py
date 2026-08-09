"""Groups, sharing and publishing — the privacy model end to end.

The rules under test:
  * a raid is visible to its owner, to groups it is shared with, and to everyone
    if it has been published
  * sharing a raid shares THAT RAID — not the log file it came out of, and not
    the other fights that happen to sit in the same upload
  * being able to see a raid never lets you change it
  * leaving a group revokes access on the next request, with nothing to clean up
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

# Two zones, an hour apart, so one upload makes two zone runs. Sharing one of
# them must not expose the other — same file, same session.
ZONE_A = (
    "(1722556800)[Thu Aug  1 21:00:00 2026] You have entered The Estate of Unrest.\r\n"
    "(1722556801)[Thu Aug  1 21:00:01 2026] YOU hit a training dummy for 100 crushing damage.\r\n"
    "(1722556803)[Thu Aug  1 21:00:03 2026] YOU hit a training dummy for 120 crushing damage.\r\n"
    "(1722556804)[Thu Aug  1 21:00:04 2026] You have killed a training dummy.\r\n"
)
ZONE_B = (
    "(1722570000)[Fri Aug  2 00:40:00 2026] You have entered Freethinker Hideout.\r\n"
    "(1722570001)[Fri Aug  2 00:40:01 2026] YOU hit a wandering ghost for 300 slashing damage.\r\n"
    "(1722570003)[Fri Aug  2 00:40:03 2026] YOU hit a wandering ghost for 250 slashing damage.\r\n"
    "(1722570004)[Fri Aug  2 00:40:04 2026] You have killed a wandering ghost.\r\n"
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-share")
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


@pytest.fixture(scope="module")
def world(client):
    """owner (admin, uploads a two-zone night), mate (groupmate), stranger."""
    sign_in(client, "owner", fresh=True)
    session_id = upload(client, "Bobby", ZONE_A + ZONE_B)
    runs = client.get("/api/zone-runs?scope=mine").json()["zone_runs"]
    assert len(runs) == 2, runs
    shared_run, private_run = sorted(runs, key=lambda r: r["started_ts"])
    char_id = next(c["id"] for c in client.get("/api/characters").json()["characters"])

    group = client.post("/api/groups", json={"name": "Tuesday Raid"}).json()["group"]
    sign_in(client, "mate", fresh=True)
    sign_in(client, "stranger", fresh=True)
    return {"session_id": session_id, "shared": shared_run, "private": private_run,
            "character_id": char_id, "group": group}


def encounters_of(client, run_id):
    return client.get(f"/api/zone-runs/{run_id}").json()["encounters"]


# ---- groups ----

def test_join_by_code_and_by_invite(client, world):
    sign_in(client, "owner")
    detail = client.get(f"/api/groups/{world['group']['id']}").json()["group"]
    code = detail["join_code"]
    assert len(code) == 6 and code.isdigit()

    # a member sees the group but not the code; a non-member doesn't see it at all
    sign_in(client, "mate")
    assert client.get(f"/api/groups/{world['group']['id']}").status_code == 404
    assert client.post("/api/groups/join", json={"code": code}).status_code == 200
    assert "join_code" not in client.get(
        f"/api/groups/{world['group']['id']}").json()["group"]

    # invite by username, accepted from the invitee's side
    sign_in(client, "owner")
    assert client.post(f"/api/groups/{world['group']['id']}/invites",
                       json={"username": "nobody"}).status_code == 404
    assert client.post(f"/api/groups/{world['group']['id']}/invites",
                       json={"username": "stranger"}).status_code == 200
    sign_in(client, "stranger")
    inv = client.get("/api/groups").json()["invites"]
    assert [i["group_name"] for i in inv] == ["Tuesday Raid"]
    assert client.post(f"/api/invites/{inv[0]['id']}/decline").status_code == 200
    assert client.get("/api/groups").json()["groups"] == []


def test_create_claims_the_code_it_showed_you(client):
    """The create form shows a code and its link while you're still typing the
    name, so the group has to end up with THAT code — otherwise the link
    somebody already pasted into chat points at nothing."""
    sign_in(client, "codemaker", fresh=True)
    code = client.get("/api/groups/new-code").json()["code"]
    assert len(code) == 6 and code.isdigit()
    # nothing is reserved: asking again is free, and the code is still unused
    assert client.get(f"/api/groups/preview/{code}").status_code == 404

    made = client.post("/api/groups", json={"name": "Preflight", "join_code": code}
                       ).json()["group"]
    assert made["join_code"] == code
    assert client.get(f"/api/groups/preview/{code}").json()["group"]["name"] == "Preflight"

    # a code taken between showing and creating falls back instead of failing
    second = client.post("/api/groups", json={"name": "Raced", "join_code": code}
                         ).json()["group"]
    assert second["join_code"] and second["join_code"] != code
    # so does junk
    third = client.post("/api/groups", json={"name": "Junk", "join_code": "abc"}
                        ).json()["group"]
    assert len(third["join_code"]) == 6 and third["join_code"].isdigit()
    for gid, name in ((made["id"], "Preflight"), (second["id"], "Raced"),
                      (third["id"], "Junk")):
        assert client.delete(f"/api/groups/{gid}?confirm={name}").status_code == 200


def test_invite_link_preview_and_join(client, world):
    """The /join/<code> landing page has to name the group BEFORE the visitor
    has an account, or the invitation is a bare code with no context."""
    import ratelimit
    ratelimit.reset_all()
    sign_in(client, "owner")
    code = client.get(f"/api/groups/{world['group']['id']}").json()["group"]["join_code"]

    client.cookies.clear()
    r = client.get(f"/api/groups/preview/{code}")
    assert r.status_code == 200
    g = r.json()["group"]
    assert g["name"] == "Tuesday Raid" and g["member"] is False
    # thin on purpose: no roster, nothing the group can see
    assert "members" not in g and "join_code" not in g
    # signed out you still can't join, and a dead code says nothing
    assert client.post("/api/groups/join", json={"code": code}).status_code == 401
    assert client.get("/api/groups/preview/000000").status_code == 404
    assert client.get("/api/groups/preview/not-a-code").status_code == 404

    # a brand-new account follows the link straight into the group
    sign_in(client, "linkjoiner", fresh=True)
    assert client.get(f"/api/groups/preview/{code}").json()["group"]["member"] is False
    assert client.post("/api/groups/join", json={"code": code}).status_code == 200
    assert client.get(f"/api/groups/preview/{code}").json()["group"]["member"] is True
    client.post(f"/api/groups/{world['group']['id']}/leave")
    ratelimit.reset_all()


def test_preview_is_rate_limited_too(client):
    """Otherwise the preview route is an unauthenticated oracle for walking the
    whole 6-digit space."""
    import ratelimit
    ratelimit.reset_all()
    client.cookies.clear()
    for _ in range(ratelimit.MAX_FAILURES):
        assert client.get("/api/groups/preview/000002").status_code == 404
    assert client.get("/api/groups/preview/000002").status_code == 429
    ratelimit.reset_all()


def test_rotating_the_code_keeps_members_and_locks_out_newcomers(client, world):
    """Rotation is the "the code got out" lever: the people already in stay in
    and keep seeing what the group shares, while the old code and the link
    built from it stop working."""
    import ratelimit
    ratelimit.reset_all()
    sign_in(client, "owner")
    gid = world["group"]["id"]
    old = client.get(f"/api/groups/{gid}").json()["group"]["join_code"]
    members_before = {m["username"] for m in
                      client.get(f"/api/groups/{gid}").json()["group"]["members"]}

    new = client.post(f"/api/groups/{gid}/code/rotate").json()["group"]["join_code"]
    assert new != old
    after = client.get(f"/api/groups/{gid}").json()["group"]
    assert {m["username"] for m in after["members"]} == members_before

    # the old code and its /join/<code> link are both dead
    sign_in(client, "codetester", fresh=True)
    assert client.get(f"/api/groups/preview/{old}").status_code == 404
    assert client.post("/api/groups/join", json={"code": old}).status_code == 404
    ratelimit.reset_all()
    assert client.post("/api/groups/join", json={"code": new}).status_code == 200
    client.post(f"/api/groups/{gid}/leave")

    # and joining can be switched off entirely without touching the roster
    sign_in(client, "owner")
    off = client.post(f"/api/groups/{gid}/code/rotate",
                      json={"enabled": False}).json()["group"]
    assert off["join_code"] is None
    assert {m["username"] for m in off["members"]} == members_before
    ratelimit.reset_all()
    sign_in(client, "codetester")
    assert client.post("/api/groups/join", json={"code": new}).status_code == 404
    sign_in(client, "owner")
    client.post(f"/api/groups/{gid}/code/rotate")     # back on for later tests
    ratelimit.reset_all()


def test_removing_a_member_revokes_their_access(client, world):
    """Kicking someone out of the group takes the shared raids with them, and
    leaves everyone else untouched."""
    import ratelimit
    ratelimit.reset_all()
    shared = world["shared"]
    sign_in(client, "owner")
    gid = world["group"]["id"]
    client.put(f"/api/zone-runs/{shared['id']}/shares", json={"group_ids": [gid]})
    code = client.get(f"/api/groups/{gid}").json()["group"]["join_code"]

    sign_in(client, "kickme", fresh=True)
    client.post("/api/groups/join", json={"code": code})
    assert client.get(f"/api/zone-runs/{shared['id']}").status_code == 200
    kicked_id = client.get("/api/auth/me").json()["user"]["id"]

    # a member can't remove anyone; the owner can
    assert client.delete(f"/api/groups/{gid}/members/"
                         f"{world['group']['owner_user_id']}").status_code == 403
    sign_in(client, "owner")
    assert client.delete(f"/api/groups/{gid}/members/{kicked_id}").status_code == 200
    # ...but never themselves, which would leave the group ownerless
    assert client.delete(f"/api/groups/{gid}/members/"
                         f"{world['group']['owner_user_id']}").status_code == 409

    sign_in(client, "kickme")
    assert client.get(f"/api/zone-runs/{shared['id']}").status_code == 404
    assert client.get(f"/api/groups/{gid}").status_code == 404
    sign_in(client, "mate")
    assert client.get(f"/api/zone-runs/{shared['id']}").status_code == 200
    sign_in(client, "owner")
    client.put(f"/api/zone-runs/{shared['id']}/shares", json={"group_ids": []})
    ratelimit.reset_all()


def test_deleting_a_group_needs_its_name_and_can_be_restored(client, world):
    """Two rules in one round trip, because they answer the same worry:
    deleting a group is one click from the member list, so it costs you typing
    the name back exactly — and if it happens anyway, an admin puts it back.

    The restore has to bring the SHARES with it, not just the roster. A group
    with no members and nothing shared is not the thing that was deleted."""
    import ratelimit
    ratelimit.reset_all()
    shared = world["shared"]
    sign_in(client, "owner")
    gone = client.post("/api/groups", json={"name": "Delete Me"}).json()["group"]
    code = gone["join_code"]
    client.put(f"/api/zone-runs/{shared['id']}/shares", json={"group_ids": [gone["id"]]})
    sign_in(client, "mate")
    client.post("/api/groups/join", json={"code": code})
    assert client.get(f"/api/zone-runs/{shared['id']}").status_code == 200

    # the name, exactly: wrong case is wrong, and so is nothing at all
    sign_in(client, "owner")
    assert client.delete(f"/api/groups/{gone['id']}").status_code == 422
    assert client.delete(f"/api/groups/{gone['id']}?confirm=delete me").status_code == 422
    assert client.delete(f"/api/groups/{gone['id']}?confirm=Delete%20Me").status_code == 200

    # gone means gone: off everyone's list, unjoinable, and the raid it was
    # carrying is private again
    assert [g["name"] for g in client.get("/api/groups").json()["groups"]] \
        == ["Tuesday Raid"]
    assert client.get(f"/api/groups/{gone['id']}").status_code == 404
    sign_in(client, "mate")
    assert client.get(f"/api/zone-runs/{shared['id']}").status_code == 404
    assert client.get(f"/api/groups/preview/{code}").status_code == 404
    assert client.post("/api/groups/join", json={"code": code}).status_code == 404
    ratelimit.reset_all()

    # the admin sees it in the restore list, with what would come back
    sign_in(client, "owner")
    listed = [g for g in client.get("/api/admin/groups").json()["groups"]
              if g["id"] == gone["id"]]
    assert len(listed) == 1 and listed[0]["member_count"] == 2
    assert listed[0]["run_share_count"] == 1 and listed[0]["owner"] == "owner"
    assert client.post(f"/api/admin/groups/{gone['id']}/restore").status_code == 200
    assert client.post(f"/api/admin/groups/{gone['id']}/restore").status_code == 409

    # ...and everything comes back: the roster, the code, and the shared raid
    sign_in(client, "mate")
    assert client.get(f"/api/zone-runs/{shared['id']}").status_code == 200
    assert [g["name"] for g in client.get("/api/groups").json()["groups"]] \
        == ["Delete Me", "Tuesday Raid"]
    assert client.get(f"/api/groups/preview/{code}").json()["group"]["member"] is True
    sign_in(client, "owner")
    client.put(f"/api/zone-runs/{shared['id']}/shares", json={"group_ids": []})
    assert client.delete(f"/api/groups/{gone['id']}?confirm=Delete%20Me").status_code == 200
    ratelimit.reset_all()


def test_wrong_join_code_is_rate_limited(client, world):
    import ratelimit
    ratelimit.reset_all()
    sign_in(client, "stranger")
    for _ in range(ratelimit.MAX_FAILURES):
        assert client.post("/api/groups/join", json={"code": "000001"}).status_code == 404
    r = client.post("/api/groups/join", json={"code": "000001"})
    assert r.status_code == 429 and r.headers["Retry-After"]
    ratelimit.reset_all()


# ---- sharing one raid ----

def test_share_one_raid_exposes_only_that_raid(client, world):
    shared, private = world["shared"], world["private"]
    sign_in(client, "owner")
    r = client.put(f"/api/zone-runs/{shared['id']}/shares",
                   json={"group_ids": [world["group"]["id"]]})
    assert r.status_code == 200 and r.json()["groups"][0]["shared"] is True
    shared_encs = [e["id"] for e in encounters_of(client, shared["id"])]
    private_encs = [e["id"] for e in encounters_of(client, private["id"])]
    assert shared_encs and private_encs

    sign_in(client, "mate")
    assert client.get(f"/api/zone-runs/{shared['id']}").status_code == 200
    assert client.get(f"/api/zone-runs/{shared['id']}/report").status_code == 200
    ids = ",".join(str(i) for i in shared_encs)
    assert client.get(f"/api/encounters/agg?ids={ids}").status_code == 200
    assert client.get(f"/api/encounters/timeline?ids={ids}").status_code == 200
    assert client.get(f"/api/encounters/deaths?ids={ids}").status_code == 200
    assert client.get(f"/api/encounters/{shared_encs[0]}").status_code == 200

    # the OTHER run in the very same uploaded file stays invisible...
    assert client.get(f"/api/zone-runs/{private['id']}").status_code == 404
    assert client.get(f"/api/encounters/{private_encs[0]}").status_code == 404
    assert client.get(
        f"/api/encounters/agg?ids={private_encs[0]}").status_code == 404
    # ...including when smuggled in alongside authorized ids
    assert client.get(
        f"/api/encounters/agg?ids={ids},{private_encs[0]}").status_code == 404
    # and the log itself is never shared
    assert client.get(f"/api/sessions/{world['session_id']}").status_code == 404
    assert client.get(f"/api/sessions/{world['session_id']}/coach").status_code == 404
    assert client.get(f"/api/sessions/{world['session_id']}/raid-report").status_code == 404
    assert client.get(f"/api/sessions/{world['session_id']}/stream").status_code == 404


def test_shared_raid_is_read_only(client, world):
    shared = world["shared"]
    sign_in(client, "mate")
    enc = encounters_of(client, shared["id"])[0]
    assert client.delete(f"/api/zone-runs/{shared['id']}").status_code == 403
    assert client.post(f"/api/zone-runs/{shared['id']}/unmerge").status_code == 403
    assert client.post(f"/api/zone-runs/{shared['id']}/split",
                       json={"encounter_id": enc["id"]}).status_code == 403
    assert client.post("/api/encounters/delete",
                       json={"ids": [enc["id"]]}).status_code == 404
    assert client.put(f"/api/zone-runs/{shared['id']}/shares",
                      json={"group_ids": []}).status_code == 403
    # ...and it can't be re-shared onward into a group of the viewer's own
    mine = client.post("/api/groups", json={"name": "Mine"}).json()["group"]
    assert client.put(f"/api/zone-runs/{shared['id']}/shares",
                      json={"group_ids": [mine["id"]]}).status_code == 403
    client.delete(f"/api/groups/{mine['id']}?confirm=Mine")


def test_scopes_split_mine_from_shared(client, world):
    sign_in(client, "mate")
    listing = client.get("/api/zone-runs").json()
    assert [r["id"] for r in listing["zone_runs"]] == [world["shared"]["id"]]
    assert listing["zone_runs"][0]["mine"] is False
    # the list names the CHARACTER who logged it; the account name is not the
    # answer to "who did I raid with"
    assert listing["zone_runs"][0]["character_name"] == "Bobby"
    # reachable through the group, so the Public switch never takes it away
    assert listing["zone_runs"][0]["via_public"] is False
    # a viewer is not told who else the raid reaches…
    assert listing["zone_runs"][0]["shared_with"] == []
    # …but IS told which of their own groups brought it to them — the list's
    # Shared column would otherwise sit empty on exactly the rows it explains
    assert [g["name"] for g in listing["zone_runs"][0]["shared_via"]] == ["Tuesday Raid"]
    # the id too — the list filters by group, and a name is not a handle
    assert listing["zone_runs"][0]["shared_via"][0]["group_id"] == world["group"]["id"]
    # the list's raid-wide DPS: total player damage over combat time
    assert listing["zone_runs"][0]["raid_dps"] > 0
    assert client.get("/api/zone-runs?scope=mine").json()["zone_runs"] == []
    assert len(client.get("/api/zone-runs?scope=shared").json()["zone_runs"]) == 1

    sign_in(client, "owner")
    assert len(client.get("/api/zone-runs?scope=mine").json()["zone_runs"]) == 2
    assert client.get("/api/zone-runs?scope=shared").json()["zone_runs"] == []
    assert client.get("/api/zone-runs?scope=nonsense").status_code == 422


def test_leaving_the_group_revokes_it(client, world):
    shared = world["shared"]
    sign_in(client, "mate")
    assert client.get(f"/api/zone-runs/{shared['id']}").status_code == 200
    assert client.post(f"/api/groups/{world['group']['id']}/leave").status_code == 200
    assert client.get(f"/api/zone-runs/{shared['id']}").status_code == 404
    assert client.get("/api/zone-runs").json()["zone_runs"] == []
    # rejoin for the tests that follow
    sign_in(client, "owner")
    code = client.get(f"/api/groups/{world['group']['id']}").json()["group"]["join_code"]
    sign_in(client, "mate")
    client.post("/api/groups/join", json={"code": code})


def test_auto_share_covers_the_back_catalogue_and_hide_overrides_it(client, world):
    """Auto-share is a standing instruction, not a copy: switching it on reaches
    raids that already exist, and one raid can still be pulled back out."""
    sign_in(client, "owner")
    client.put(f"/api/zone-runs/{world['shared']['id']}/shares", json={"group_ids": []})
    sign_in(client, "mate")
    assert client.get("/api/zone-runs").json()["zone_runs"] == []

    sign_in(client, "owner")
    r = client.put(f"/api/characters/{world['character_id']}/shares",
                   json={"group_ids": [world["group"]["id"]]})
    assert r.json()["groups"][0]["shared"] is True
    sign_in(client, "mate")
    assert len(client.get("/api/zone-runs").json()["zone_runs"]) == 2

    # hide one night from that group; the rest keep flowing
    sign_in(client, "owner")
    listed = client.get(f"/api/zone-runs/{world['private']['id']}/shares").json()
    assert listed["groups"][0]["auto"] is True
    client.put(f"/api/zone-runs/{world['private']['id']}/shares", json={"group_ids": []})
    sign_in(client, "mate")
    assert [r["id"] for r in client.get("/api/zone-runs").json()["zone_runs"]] \
        == [world["shared"]["id"]]

    sign_in(client, "owner")
    client.put(f"/api/characters/{world['character_id']}/shares", json={"group_ids": []})
    sign_in(client, "mate")
    assert client.get("/api/zone-runs").json()["zone_runs"] == []


def test_auto_share_new_raids_only_excludes_the_back_catalogue(client, world):
    """The back catalogue is a choice per share: `history: false` reaches only
    raids recorded after the share was turned on. Both of the world's runs
    predate any share made now, so a new-raids-only share shows the groupmate
    nothing — until history is flipped on, which opens everything, and flipping
    it back off closes it again (since_ts is pinned to the FIRST enable, so the
    round trip cannot move the cutoff)."""
    gid = world["group"]["id"]
    sign_in(client, "owner")
    r = client.put(f"/api/characters/{world['character_id']}/shares",
                   json={"shares": [{"group_id": gid, "history": False}]})
    g = r.json()["groups"][0]
    assert g["shared"] is True and g["history"] is False
    sign_in(client, "mate")
    assert client.get("/api/zone-runs").json()["zone_runs"] == []
    # not reachable through the standing share, so the run's own Share control
    # reports auto=False — and saving it empty must write a plain delete, not a
    # `hide` that would block the opt-in below (it also clears the hide the
    # previous test left on the private run)
    sign_in(client, "owner")
    listed = client.get(f"/api/zone-runs/{world['shared']['id']}/shares").json()
    assert listed["groups"][0]["auto"] is False
    client.put(f"/api/zone-runs/{world['shared']['id']}/shares", json={"group_ids": []})
    client.put(f"/api/zone-runs/{world['private']['id']}/shares", json={"group_ids": []})

    r = client.put(f"/api/characters/{world['character_id']}/shares",
                   json={"shares": [{"group_id": gid, "history": True,
                                     "group_content": True}]})
    assert r.json()["groups"][0]["history"] is True
    sign_in(client, "mate")
    assert len(client.get("/api/zone-runs").json()["zone_runs"]) == 2

    sign_in(client, "owner")
    client.put(f"/api/characters/{world['character_id']}/shares",
               json={"shares": [{"group_id": gid, "history": False}]})
    sign_in(client, "mate")
    assert client.get("/api/zone-runs").json()["zone_runs"] == []

    sign_in(client, "owner")
    client.put(f"/api/characters/{world['character_id']}/shares", json={"shares": []})


def test_share_survives_a_reparse(client, world):
    """A reparse drops and recreates every encounter row and re-derives runs.
    A share that evaporated there would be a silent privacy change in the other
    direction — the owner thinks it's shared and it isn't."""
    shared = world["shared"]
    sign_in(client, "owner")
    client.put(f"/api/zone-runs/{shared['id']}/shares",
               json={"group_ids": [world["group"]["id"]]})
    assert client.post(f"/api/sessions/{world['session_id']}/reparse").status_code == 200
    for _ in range(60):
        if client.get(f"/api/sessions/{world['session_id']}"
                      ).json()["session"]["status"] == "ready":
            break
        time.sleep(0.1)
    sign_in(client, "mate")
    assert [r["id"] for r in client.get("/api/zone-runs").json()["zone_runs"]] == [shared["id"]]


# ---- publishing ----

def test_published_raid_needs_no_account(client, world):
    """The testing switch: an admin publishes one of their own raids and it
    reads without a cookie."""
    sign_in(client, "owner")
    assert client.get("/api/auth/me").json()["user"]["role"] == "admin"
    run_id = world["private"]["id"]
    encs = [e["id"] for e in encounters_of(client, run_id)]
    assert client.put(f"/api/zone-runs/{run_id}/public",
                      json={"public": True}).json()["public"] is True

    client.cookies.clear()
    assert client.get("/api/auth/me").json()["user"] is None
    assert client.get(f"/api/zone-runs/{run_id}").status_code == 200
    assert client.get(f"/api/zone-runs/{run_id}/report").status_code == 200
    assert client.get(f"/api/encounters/agg?ids={encs[0]}").status_code == 200
    assert client.get(f"/api/encounters/{encs[0]}").status_code == 200
    listing = client.get("/api/zone-runs").json()
    assert [r["id"] for r in listing["zone_runs"]] == [run_id]
    assert listing["signed_in"] is False
    # published means readable, not writable, and nothing else opens up
    assert client.delete(f"/api/zone-runs/{run_id}").status_code == 401
    assert client.get(f"/api/zone-runs/{world['shared']['id']}").status_code == 404
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/characters").status_code == 401
    assert client.get("/api/groups").status_code == 401

    # `via_public` is "the ONLY reason you can see this" — it drives the raid
    # list's Public switch. True for a stranger; false for the owner, whose own
    # raid does not stop being theirs because it is also published.
    sign_in(client, "stranger")
    strangers = client.get("/api/zone-runs").json()["zone_runs"]
    assert [(r["id"], r["via_public"], r["mine"]) for r in strangers] == [(run_id, True, False)]

    sign_in(client, "owner")
    ours = next(r for r in client.get("/api/zone-runs").json()["zone_runs"]
                if r["id"] == run_id)
    assert ours["public"] is True and ours["via_public"] is False and ours["mine"] is True
    assert client.put(f"/api/zone-runs/{run_id}/public",
                      json={"public": False}).json()["public"] is False
    client.cookies.clear()
    assert client.get(f"/api/zone-runs/{run_id}").status_code == 404


def test_only_admins_publish_and_only_their_own(client, world):
    sign_in(client, "owner")
    run_id = world["private"]["id"]
    sign_in(client, "mate")            # plain user: no publish route at all
    assert client.put(f"/api/zone-runs/{run_id}/public",
                      json={"public": True}).status_code == 403
    mate_session = upload(client, "Mate", ZONE_A)
    mate_run = client.get("/api/zone-runs?scope=mine").json()["zone_runs"][0]
    client.put(f"/api/zone-runs/{mate_run['id']}/shares",
               json={"group_ids": [world["group"]["id"]]})

    # the admin can SEE mate's raid (it was shared with the group) but must not
    # be able to publish someone else's data to the world
    sign_in(client, "owner")
    assert client.get(f"/api/zone-runs/{mate_run['id']}").status_code == 200
    assert client.put(f"/api/zone-runs/{mate_run['id']}/public",
                      json={"public": True}).status_code == 403
    client.cookies.clear()
    assert client.get(f"/api/zone-runs/{mate_run['id']}").status_code == 404
    assert mate_session


def test_publishing_is_audited(client, world):
    sign_in(client, "owner")
    entries = client.get("/api/admin/audit").json()["entries"]
    assert [e["action"] for e in entries[:2]] == ["unpublish", "publish"]
    assert entries[0]["actor"] == "owner"
    sign_in(client, "mate")
    assert client.get("/api/admin/audit").status_code == 403
    assert client.get("/api/admin/users").status_code == 403


# A real raid roster and a solo zone in one log, two hours apart so they are two
# runs: the pair a raids-only share has to tell apart.
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


def test_auto_share_carries_raids_only_by_default(client, world):
    """A standing share is for RAIDS. "Share my raids with the guild" is not a
    request to broadcast every six-man zone and solo dummy parse, and the two
    readings cost differently: opting in is one tick, while noticing you have
    been leaking is luck. So a new auto-share reaches runs with a raid's roster
    and nothing else, until `group_content` says otherwise.

    Uploaded as its own character with a real eight-man roster, because that is
    the number under test — and it goes last in this file so the extra runs
    can't move the counts every test above asserts on."""
    gid = world["group"]["id"]
    sign_in(client, "owner")
    # start from nothing: earlier tests leave explicit shares and published runs
    # behind, and either would show the groupmate a run the standing share is
    # supposed to be holding back. Auto-share goes first — clearing a run's
    # shares while a standing one reaches it writes a `hide`.
    client.put(f"/api/characters/{world['character_id']}/shares", json={"shares": []})
    for key in ("shared", "private"):
        client.put(f"/api/zone-runs/{world[key]['id']}/shares", json={"group_ids": []})
        client.put(f"/api/zone-runs/{world[key]['id']}/public", json={"public": False})

    upload(client, "Raidy", RAID_NIGHT + SOLO_ZONE)
    raidy = next(c["id"] for c in client.get("/api/characters").json()["characters"]
                 if c["name"] == "Raidy")
    runs = {r["zone"]: r for r in client.get("/api/zone-runs?scope=mine").json()["zone_runs"]}
    assert runs["The Emerald Halls"]["raider_count"] == 8
    assert runs["The Estate of Unrest"]["raider_count"] == 1

    r = client.put(f"/api/characters/{raidy}/shares",
                   json={"shares": [{"group_id": gid, "history": True}]})
    assert r.json()["groups"][0]["group_content"] is False

    # the raid arrives; the solo zone from the same log and the same standing
    # share does not
    sign_in(client, "mate")
    # mate has runs of their own by now; the question is what REACHED them
    theirs = [x for x in client.get("/api/zone-runs").json()["zone_runs"] if not x["mine"]]
    assert [x["zone"] for x in theirs] == ["The Emerald Halls"]
    assert [g["name"] for g in theirs[0]["shared_via"]] == ["Tuesday Raid"]

    # a run the standing share does NOT reach must not be marked auto, or
    # unticking it would leave a `hide` blocking a later opt-in
    sign_in(client, "owner")
    solo = runs["The Estate of Unrest"]["id"]
    assert client.get(f"/api/zone-runs/{solo}/shares").json()["groups"][0]["auto"] is False

    client.put(f"/api/characters/{raidy}/shares",
               json={"shares": [{"group_id": gid, "history": True, "group_content": True}]})
    sign_in(client, "mate")
    opened = [x for x in client.get("/api/zone-runs").json()["zone_runs"] if not x["mine"]]
    assert sorted(x["zone"] for x in opened) == ["The Emerald Halls", "The Estate of Unrest"]


def test_a_reader_sweeps_a_shared_raid_off_their_own_list(client, world):
    """The reading side of `hide`. Auto-share means somebody's whole raid week
    arrives whether or not you were on it, and the only answers before this were
    "read past it every night" and "leave the group".

    What it must NOT be is a revocation: it is one person's list, so the owner
    keeps their audience, the link still opens, and nobody else's list moves."""
    sign_in(client, "mate")
    theirs = [x for x in client.get("/api/zone-runs").json()["zone_runs"] if not x["mine"]]
    target = next(x for x in theirs if x["zone"] == "The Emerald Halls")
    assert target["dismissed"] is False
    r = client.post(f"/api/zone-runs/{target['id']}/dismiss", json={})
    assert r.status_code == 200 and r.json()["dismissed"] is True

    listing = client.get("/api/zone-runs").json()
    assert target["id"] not in [x["id"] for x in listing["zone_runs"]]
    # the other raid the same standing share brings is untouched — a sweep is
    # about one night, not about the person who shared it
    assert "The Estate of Unrest" in [x["zone"] for x in listing["zone_runs"]]
    # …and the list is told it is holding something back, or a raid that simply
    # stopped appearing is indistinguishable from a share that was revoked
    assert listing["dismissed_count"] == 1
    assert client.get("/api/zone-runs?scope=shared").json()["dismissed_count"] == 1
    assert target["id"] not in [
        x["id"] for x in client.get("/api/zone-runs?scope=shared").json()["zone_runs"]]
    # asked for, it comes back flagged rather than as a second endpoint
    asked = {x["id"]: x for x in
             client.get("/api/zone-runs?dismissed=1").json()["zone_runs"]}
    assert asked[target["id"]]["dismissed"] is True

    # not a revocation: the raid still opens, and so do its fights
    detail = client.get(f"/api/zone-runs/{target['id']}")
    assert detail.status_code == 200
    enc = detail.json()["encounters"][0]
    assert client.get(f"/api/encounters/agg?ids={enc['id']}").status_code == 200

    # the owner is told nothing, loses nothing, and cannot sweep their own
    sign_in(client, "owner")
    owners = client.get("/api/zone-runs").json()
    assert target["id"] in [x["id"] for x in owners["zone_runs"]]
    assert owners["dismissed_count"] == 0
    assert client.post(f"/api/zone-runs/{target['id']}/dismiss",
                       json={}).status_code == 422

    # A merge re-derives run ids, and a dismissal that evaporated there would
    # put a swept raid back on the reader's list with no edit of their own. The
    # SWEPT run has to be the one that disappears for this to test anything —
    # the survivor keeps the earliest id — so the sweep moves to the run the
    # merge will consume, and the row it lands on was never swept itself.
    sign_in(client, "mate")
    client.post(f"/api/zone-runs/{target['id']}/dismiss", json={"dismissed": False})
    solo = next(x for x in client.get("/api/zone-runs").json()["zone_runs"]
                if x["zone"] == "The Estate of Unrest" and not x["mine"])
    client.post(f"/api/zone-runs/{solo['id']}/dismiss", json={})

    sign_in(client, "owner")
    assert client.post("/api/zone-runs/merge",
                       json={"ids": [target["id"], solo["id"]]}).status_code == 200
    merged = next(x for x in client.get("/api/zone-runs?scope=mine").json()["zone_runs"]
                  if x["character_name"] == "Raidy")
    assert merged["id"] != solo["id"], "the swept run survived; carry untested"
    sign_in(client, "mate")
    after = client.get("/api/zone-runs").json()
    assert merged["id"] not in [x["id"] for x in after["zone_runs"]]
    assert after["dismissed_count"] == 1

    sign_in(client, "owner")
    client.post(f"/api/zone-runs/{merged['id']}/unmerge")

    # and putting it back is the same button again
    sign_in(client, "mate")
    swept = client.get("/api/zone-runs?dismissed=1").json()["zone_runs"]
    for x in [y for y in swept if y["dismissed"]]:
        assert client.post(f"/api/zone-runs/{x['id']}/dismiss",
                           json={"dismissed": False}).status_code == 200
    back = client.get("/api/zone-runs").json()
    assert back["dismissed_count"] == 0
    assert "The Emerald Halls" in [x["zone"] for x in back["zone_runs"]]
