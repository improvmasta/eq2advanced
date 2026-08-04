"""v11: the ACT plugin's sharing controls, over a device token.

The plugin has two, and the difference is the whole point:
  * the STANDING default (`PUT /api/ingest/shares`) -> character_shares. Every
    raid this character ever records, back catalogue included.
  * THIS raid (`share_groups` on a batch) -> session_shares. Only the session
    the batch opens.

Both are gated on a token minted with `can_share`, both are read at query time
by the one visibility predicate, and both are beaten by a `hide` set on the
site. What must NOT happen: a token that can only send logs quietly changing
who can read them, or a shared session leaking the OTHER sessions on the
account.
"""

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-ingshare")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    import routers.uploads_api as uploads_api
    mp.setattr(uploads_api, "UPLOADS_DIR", tmp / "uploads")
    import pipeline.live as live
    mp.setattr(live, "RAW_DIR", tmp / "raw")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as c:
        yield c
    mp.undo()


def line(ts, body):
    return f"({ts})[Thu Aug  1 21:00:00 2026] {body}\r\n"


T0 = 1722556800


def fight(base, mob):
    return [
        line(base, "You have entered The Estate of Unrest."),
        line(base + 1, f"YOU hit {mob} for 100 crushing damage."),
        line(base + 3, f"YOU hit {mob} for 120 crushing damage."),
        line(base + 4, f"You have killed {mob}."),
    ]


def sign_in(c, username, fresh=False):
    c.cookies.clear()
    body = {"username": username, "password": "hunter2hunter2"}
    if fresh:
        body |= {"sq_id": 1, "answer": "pet"}
    r = c.post(f"/api/auth/{'register' if fresh else 'login'}", json=body)
    assert r.status_code == 200, r.text
    return r.json()["user"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def send(client, token, lines, share_groups=None, mode="live"):
    payload = {"batch_id": str(uuid.uuid4()), "mode": mode, "lines": lines}
    if share_groups is not None:
        payload["share_groups"] = share_groups
    return client.post("/api/ingest/batch", content=json.dumps(payload).encode(),
                       headers=auth(token))


def finish(client, token):
    """Close the live session so it rebuilds and the zone runs are derived."""
    r = client.post("/api/ingest/backfill/done", headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def mint(client, char_id, can_share):
    r = client.post(f"/api/characters/{char_id}/tokens",
                    json={"label": "raid pc", "can_share": can_share})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def world(client):
    """owner with a character and two tokens (one scoped to share, one not),
    a groupmate, and a stranger."""
    sign_in(client, "shareowner", fresh=True)
    char_id = client.post("/api/characters", json={"name": "Bobby"}).json()["id"]
    group = client.post("/api/groups", json={"name": "Tuesday Raid"}).json()["group"]
    code = client.get(f"/api/groups/{group['id']}").json()["group"]["join_code"]
    tokens = {"share": mint(client, char_id, True),
              "plain": mint(client, char_id, False)}

    sign_in(client, "sharemate", fresh=True)
    assert client.post("/api/groups/join", json={"code": code}).status_code == 200
    sign_in(client, "sharestranger", fresh=True)
    return {"character_id": char_id, "group": group, **tokens}


def runs_visible_to(client, username, scope="shared"):
    sign_in(client, username)
    return {r["id"] for r in client.get(f"/api/zone-runs?scope={scope}").json()["zone_runs"]}


# ---- the scope gate ----

def test_plain_token_can_read_the_panel_but_not_change_it(client, world):
    """A send-only token still reports who can see the raids — that is the
    question the raider wants answered before they start streaming — but every
    write is refused."""
    r = client.get("/api/ingest/shares", headers=auth(world["plain"]))
    assert r.status_code == 200, r.text
    panel = r.json()
    assert panel["can_share"] is False
    assert [g["name"] for g in panel["groups"]] == ["Tuesday Raid"]
    assert panel["session_groups"] == []

    gid = world["group"]["id"]
    assert client.put("/api/ingest/shares", json={"auto_groups": [gid]},
                      headers=auth(world["plain"])).status_code == 403
    assert send(client, world["plain"], fight(T0, "a training dummy"),
                share_groups=[gid]).status_code == 403

    # and refusing the batch refused the LINES too — a 403 must not have
    # quietly ingested the raid without the sharing the client asked for
    assert client.get("/api/ingest/hello",
                      headers=auth(world["plain"])).json()["session"] is None


def test_hello_carries_the_panel(client, world):
    hello = client.get("/api/ingest/hello", headers=auth(world["share"])).json()
    assert hello["character"]["name"] == "Bobby"
    assert hello["sharing"]["can_share"] is True
    assert hello["sharing"]["groups"][0]["auto"] is False


def test_unknown_group_is_404_not_silently_dropped(client, world):
    """Sharing with fewer people than the checkboxes show would be the one lie
    this panel must never tell."""
    assert client.put("/api/ingest/shares", json={"auto_groups": [9999]},
                      headers=auth(world["share"])).status_code == 404
    assert send(client, world["share"], fight(T0, "a training dummy"),
                share_groups=[9999]).status_code == 404
    assert client.put("/api/ingest/shares", json={"auto_groups": "all"},
                      headers=auth(world["share"])).status_code == 422


# ---- this raid: session_shares ----

def test_share_groups_on_a_batch_shares_that_session_only(client, world):
    gid = world["group"]["id"]
    token = world["share"]

    # night one, shared from the plugin
    send(client, token, fight(T0, "a training dummy"), share_groups=[gid])
    shared_sid = finish(client, token)
    # night two, same character, same token, no sharing
    send(client, token, fight(T0 + 100_000, "a sparring golem"), share_groups=[])
    private_sid = finish(client, token)
    assert shared_sid != private_sid

    sign_in(client, "shareowner")
    mine = client.get("/api/zone-runs?scope=mine").json()["zone_runs"]
    by_session = {}
    for run in mine:
        encs = client.get(f"/api/zone-runs/{run['id']}").json()["encounters"]
        by_session[encs[0]["session_id"]] = run["id"]
    shared_run, private_run = by_session[shared_sid], by_session[private_sid]

    # the groupmate gets the shared night and nothing else
    assert runs_visible_to(client, "sharemate") == {shared_run}
    sign_in(client, "sharemate")
    assert client.get(f"/api/zone-runs/{shared_run}").status_code == 200
    assert client.get(f"/api/zone-runs/{private_run}").status_code == 404
    # sharing a raid never shares the log it came out of
    assert client.get(f"/api/sessions/{shared_sid}").status_code == 404
    # nor the right to change it
    assert client.delete(f"/api/zone-runs/{shared_run}").status_code in (403, 404)

    assert runs_visible_to(client, "sharestranger") == set()

    # the owner's share control names where the share came from
    sign_in(client, "shareowner")
    entry = client.get(f"/api/zone-runs/{shared_run}/shares").json()
    assert entry["groups"] == [
        {"group_id": gid, "name": "Tuesday Raid", "shared": True,
         "auto": True, "source": "session"}]
    sign_in(client, "shareowner")
    assert client.get(f"/api/zone-runs/{private_run}/shares").json()["groups"] == [
        {"group_id": gid, "name": "Tuesday Raid", "shared": False,
         "auto": False, "source": None}]
    world["shared_run"], world["private_run"] = shared_run, private_run
    world["shared_session"] = shared_sid


def test_hide_on_the_site_beats_the_plugins_share(client, world):
    sign_in(client, "shareowner")
    gid = world["group"]["id"]
    r = client.put(f"/api/zone-runs/{world['shared_run']}/shares",
                   json={"group_ids": []})
    assert r.status_code == 200, r.text
    assert runs_visible_to(client, "sharemate") == set()

    # and putting it back is one tick, without the plugin involved
    sign_in(client, "shareowner")
    client.put(f"/api/zone-runs/{world['shared_run']}/shares", json={"group_ids": [gid]})
    assert runs_visible_to(client, "sharemate") == {world["shared_run"]}


def test_unticking_mid_raid_takes_the_night_back(client, world):
    """The list is authoritative on every batch, so the next one revokes."""
    sign_in(client, "shareowner")
    gid = world["group"]["id"]
    token = world["share"]
    # clear the run-level share the previous test left behind
    client.put(f"/api/zone-runs/{world['shared_run']}/shares", json={"group_ids": []})

    send(client, token, fight(T0 + 200_000, "a rusty sentry"), share_groups=[gid])
    sid = client.get("/api/ingest/hello", headers=auth(token)).json()["session"]
    assert client.get("/api/ingest/shares",
                      headers=auth(token)).json()["session_groups"] == [gid]

    send(client, token, [line(T0 + 200_010, "YOU hit a rusty sentry for 5 crushing damage.")],
         share_groups=[])
    assert client.get("/api/ingest/shares",
                      headers=auth(token)).json()["session_groups"] == []
    finish(client, token)

    sign_in(client, "shareowner")
    runs = client.get("/api/zone-runs?scope=mine").json()["zone_runs"]
    tonight = {r["id"] for r in runs} - {world["shared_run"], world["private_run"]}
    assert tonight, "the third night should have made a run"
    assert runs_visible_to(client, "sharemate") & tonight == set()


def test_omitting_share_groups_leaves_the_session_alone(client, world):
    """A plugin without the scope sends no field at all, and that must not read
    as 'share with nobody' — it would silently undo the site's own setting."""
    gid = world["group"]["id"]
    token = world["share"]
    send(client, token, fight(T0 + 300_000, "a bog sludge"), share_groups=[gid])
    send(client, token, [line(T0 + 300_010, "YOU hit a bog sludge for 5 crushing damage.")])
    assert client.get("/api/ingest/shares",
                      headers=auth(token)).json()["session_groups"] == [gid]
    finish(client, token)


# ---- the standing default: character_shares ----

def test_put_shares_sets_the_standing_default_including_the_back_catalogue(client, world):
    gid = world["group"]["id"]
    r = client.put("/api/ingest/shares", json={"auto_groups": [gid]},
                   headers=auth(world["share"]))
    assert r.status_code == 200, r.text
    assert r.json()["groups"] == [{"group_id": gid, "name": "Tuesday Raid", "auto": True}]

    # the site agrees, and the night the plugin explicitly did NOT share is now
    # visible too: that is what a standing default means
    sign_in(client, "shareowner")
    site = client.get(f"/api/characters/{world['character_id']}/shares").json()
    assert site["groups"] == [{"group_id": gid, "name": "Tuesday Raid", "shared": True}]
    assert world["private_run"] in runs_visible_to(client, "sharemate")

    # turning it off closes the back catalogue again
    assert client.put("/api/ingest/shares", json={"auto_groups": []},
                      headers=auth(world["share"])).status_code == 200
    assert world["private_run"] not in runs_visible_to(client, "sharemate")


def test_leaving_the_group_revokes_everything_the_plugin_shared(client, world):
    """No cleanup step, because nothing was ever materialised onto the run."""
    gid = world["group"]["id"]
    client.put("/api/ingest/shares", json={"auto_groups": [gid]},
               headers=auth(world["share"]))
    sign_in(client, "sharemate")
    assert client.post(f"/api/groups/{gid}/leave").status_code == 200
    assert client.get("/api/zone-runs?scope=shared").json()["zone_runs"] == []
    assert client.get(f"/api/zone-runs/{world['shared_run']}").status_code == 404
