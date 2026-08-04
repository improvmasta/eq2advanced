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
    for gid in (made["id"], second["id"], third["id"]):
        client.delete(f"/api/groups/{gid}")


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
    client.delete(f"/api/groups/{mine['id']}")


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
    # a viewer is not told who else the raid reaches
    assert listing["zone_runs"][0]["shared_with"] == []
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
