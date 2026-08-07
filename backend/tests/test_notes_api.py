"""Raid notes: where a note lands, and who can read it.

The filing rule is the feature, so it is what these test. A note written on
trash belongs to the ZONE; one written on a named belongs to that boss; and
both accumulate across nights under the same key, because that pile is the raid
outline this exists to grow. Everything else is the two rules a private thing
has to keep: nobody else can read it, and deleting it takes its screenshots
with it.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import db as dbmod

ZONE = "The Estate of Unrest"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-notes")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    mp.setattr(dbmod, "NOTESHOTS_DIR", tmp / "noteshots")
    import routers.notes_api as notes_api
    mp.setattr(notes_api, "NOTESHOTS_DIR", tmp / "noteshots")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as c:
        c.tmp = tmp
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


def png(color=(30, 80, 160)):
    buf = io.BytesIO()
    Image.new("RGB", (600, 340), color).save(buf, format="PNG")
    return buf.getvalue()


def add(c, body, mob=None, zone=ZONE):
    r = c.post("/api/notes", json={"zone": zone, "mob_name": mob, "body": body})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def owner(client):
    sign_in(client, "noteowner", fresh=True)
    return "noteowner"


# --- where a note lands ---------------------------------------------------

def test_trash_notes_are_the_zones_and_named_notes_are_the_bosss(client, owner):
    add(client, "adds path along the left wall")
    add(client, "he casts the AE at 70%", mob="Garanel Rucksif")

    zone_notes = client.get("/api/notes", params={"zone": ZONE}).json()["notes"]
    assert [n["body"] for n in zone_notes] == ["adds path along the left wall"]
    assert zone_notes[0]["mob_name"] is None

    boss_notes = client.get(
        "/api/notes", params={"zone": ZONE, "mob": "Garanel Rucksif"}).json()["notes"]
    assert [n["body"] for n in boss_notes] == ["he casts the AE at 70%"]


def test_notes_accumulate_under_one_boss_across_nights(client, owner):
    """The point of keying on the boss instead of the pull: last week's note is
    right there next to tonight's."""
    add(client, "second attempt: save the raid cure", mob="Garanel Rucksif")
    notes = client.get(
        "/api/notes", params={"zone": ZONE, "mob": "Garanel Rucksif"}).json()["notes"]
    assert len(notes) == 2
    # newest first — the last thing learned is the thing being looked for
    assert notes[0]["body"].startswith("second attempt")


def test_the_outline_lists_the_zone_and_its_nameds(client, owner):
    zones = client.get("/api/notes/outline").json()["zones"]
    keyed = {(z["zone"], z["mob_name"]): z for z in zones}
    assert keyed[(ZONE, None)]["notes"] == 1
    assert keyed[(ZONE, "Garanel Rucksif")]["notes"] == 2


def test_a_note_can_be_moved_off_a_boss_it_was_not_about(client, owner):
    """Live, a one-word boss reads as a raider and vice versa — the filing has
    to be correctable without retyping what was written."""
    note = add(client, "actually this was trash", mob="Wrongname")
    moved = client.patch(f"/api/notes/{note['id']}", json={"mob_name": ""}).json()
    assert moved["mob_name"] is None
    assert moved["body"] == "actually this was trash"


def test_a_note_needs_a_zone(client, owner):
    assert client.post("/api/notes", json={"zone": "  ", "body": "x"}).status_code == 422


# --- screenshots ----------------------------------------------------------

def test_a_screenshot_is_re_encoded_kept_and_owner_checked(client, owner):
    note = add(client, "where everyone was standing", mob="Garanel Rucksif")
    original = png()
    r = client.post(f"/api/notes/{note['id']}/shots",
                    files={"file": ("raid.png", original, "image/png")})
    assert r.status_code == 200, r.text
    shot = r.json()["shots"][0]

    kept = list(dbmod.NOTESHOTS_DIR.glob("*.webp"))
    assert len(kept) == 2                       # a viewable copy and a thumbnail
    assert not list(client.tmp.rglob("*.png"))  # never the bytes that arrived
    for path in kept:
        assert path.read_bytes() != original

    img = client.get(f"/api/notes/{note['id']}/shots/{shot['id']}/image")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/webp"
    assert "private" in img.headers["cache-control"]


def test_deleting_a_note_takes_its_screenshots_with_it(client, owner):
    note = add(client, "one to throw away")
    client.post(f"/api/notes/{note['id']}/shots",
                files={"file": ("x.png", png((10, 10, 10)), "image/png")})
    before = len(list(dbmod.NOTESHOTS_DIR.glob("*.webp")))
    assert client.delete(f"/api/notes/{note['id']}").status_code == 200
    assert len(list(dbmod.NOTESHOTS_DIR.glob("*.webp"))) == before - 2
    assert client.get(f"/api/notes/{note['id']}").status_code == 404
    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) FROM raid_note_shots WHERE note_id=?",
                        (note["id"],)).fetchone()[0] == 0


# --- whose they are -------------------------------------------------------

def test_notes_are_private_to_whoever_wrote_them(client, owner):
    mine = add(client, "mine alone", mob="Garanel Rucksif")
    r = client.post(f"/api/notes/{mine['id']}/shots",
                    files={"file": ("x.png", png((90, 10, 10)), "image/png")})
    shot_id = r.json()["shots"][0]["id"]

    sign_in(client, "notestranger", fresh=True)
    # ids are sequential, so "absent from your list" is not an access rule
    assert client.get(f"/api/notes/{mine['id']}").status_code == 404
    assert client.patch(f"/api/notes/{mine['id']}",
                        json={"body": "not yours"}).status_code == 404
    assert client.delete(f"/api/notes/{mine['id']}").status_code == 404
    assert client.get(
        f"/api/notes/{mine['id']}/shots/{shot_id}/image").status_code == 404
    assert client.get("/api/notes", params={"zone": ZONE}).json()["notes"] == []
    assert client.get("/api/notes/outline").json()["zones"] == []
    sign_in(client, "noteowner")


def test_notes_need_an_account(client):
    client.cookies.clear()
    assert client.get("/api/notes").status_code == 401
    assert client.post("/api/notes", json={"zone": ZONE, "body": "x"}).status_code == 401
