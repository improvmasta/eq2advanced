"""Importing a parse from an ACT screenshot.

The reader itself is covered by test_actshot.py. What matters here is
everything AROUND it, because an imported parse is a claim about somebody
else's night and the app has to keep it in its place:

  * it is one row in `imported_parses` and creates no session, character,
    encounter or zone run
  * it belongs to whoever imported it and nobody else can read it — including
    by guessing its id, which is sequential
  * a RE-ENCODED copy of the screenshot is kept (the picture is the only
    evidence behind the columns arithmetic can't check) but never the original
    bytes, and it is behind the same owner check as the numbers
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

SHOTS = Path(__file__).parent / "fixtures" / "act_shots"
pytest.importorskip("pytesseract")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-shots")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    mp.setattr(dbmod, "PARSESHOTS_DIR", tmp / "parseshots")
    import routers.parseshots_api as shots_api
    mp.setattr(shots_api, "PARSESHOTS_DIR", tmp / "parseshots")
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


def upload(c, name="emerald-halls-bobby.png"):
    return c.post("/api/parseshots",
                  files={"file": (name, (SHOTS / name).read_bytes(), "image/png")})


@pytest.fixture(scope="module")
def imported(client):
    sign_in(client, "shotowner", fresh=True)
    r = upload(client)
    assert r.status_code == 200, r.text
    return r.json()


def test_import_reads_the_parse(imported):
    assert imported["zone"] == "The Emerald Halls"
    assert imported["encounter"] == "Galiel Spirithoof"
    assert imported["character_name"] == "Bobby"
    assert imported["duration_s"] == 391
    assert imported["kind"] == "damage"
    assert imported["source"] == "screenshot"
    assert len(imported["rows"]) == 43
    assert imported["total"]["damage"] == 5386632


def test_rows_carry_the_columns_that_shot_had(imported):
    assert "avg_delay" in imported["columns"]
    row = next(r for r in imported["rows"] if r["name"] == "Lifeburn")
    assert row["damage"] == 591967
    assert row["hits"] == 42


def test_import_creates_no_session_or_run(client, imported):
    """A shot must not enter the parse world at all — the moment it has a
    session or a zone run, a rollup somewhere will find it."""
    conn = dbmod.get_db()
    for table in ("sessions", "encounters", "zone_runs", "entities", "characters"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert conn.execute("SELECT COUNT(*) FROM imported_parses").fetchone()[0] == 1


def test_a_re_encoded_copy_is_kept_but_never_the_original(client, imported):
    """The picture is the only evidence behind the columns arithmetic cannot
    check, so it is kept — as WebP this app wrote, not as the bytes that were
    uploaded."""
    assert imported["has_image"] is True
    kept = list(dbmod.PARSESHOTS_DIR.glob("*.webp"))
    assert len(kept) == 2, [p.name for p in kept]          # view + thumbnail
    assert not list(client.tmp.rglob("*.png"))
    original = (SHOTS / "emerald-halls-bobby.png").read_bytes()
    for path in kept:
        assert path.read_bytes() != original
        assert path.read_bytes()[:4] == b"RIFF"            # a real WebP
    # the row points at files; it does not carry the image itself
    conn = dbmod.get_db()
    row = conn.execute("SELECT * FROM imported_parses").fetchone()
    assert not any(isinstance(v, (bytes, bytearray)) for v in tuple(row))


def test_the_thumbnail_is_small_and_the_copy_is_readable(client, imported):
    """The kept copy is for reading numbers off, so it keeps its size; the
    thumbnail is a thumbnail."""
    full = client.get(f"/api/parseshots/{imported['id']}/image")
    thumb = client.get(f"/api/parseshots/{imported['id']}/image?thumb=1")
    assert full.status_code == thumb.status_code == 200
    assert full.headers["content-type"] == "image/webp"
    assert "private" in full.headers.get("cache-control", "")
    from PIL import Image
    import io
    with Image.open(io.BytesIO(full.content)) as im:
        assert im.width == 1422                            # unshrunk: it must stay legible
    with Image.open(io.BytesIO(thumb.content)) as im:
        assert im.width == 320
    assert len(thumb.content) < len(full.content)


def test_the_image_is_behind_the_same_owner_check(client, imported):
    """Served rather than mounted, so the filename is never the permission."""
    sign_in(client, "peeker", fresh=True)
    assert client.get(f"/api/parseshots/{imported['id']}/image").status_code == 404
    assert client.get(f"/api/parseshots/{imported['id']}/image?thumb=1").status_code == 404
    client.cookies.clear()
    assert client.get(f"/api/parseshots/{imported['id']}/image").status_code == 401


def test_the_stored_name_is_never_sent_to_the_client(client, imported):
    """One door to the images: ask by id. The on-disk name stays server-side."""
    sign_in(client, "shotowner")
    body = client.get(f"/api/parseshots/{imported['id']}").json()
    assert "image_name" not in body and "thumb_name" not in body
    listed = client.get("/api/parseshots").json()["items"][0]
    assert "thumb_name" not in listed and listed["has_image"] == 1


def test_it_is_listed_for_its_owner(client, imported):
    sign_in(client, "shotowner")
    items = client.get("/api/parseshots").json()["items"]
    assert [i["id"] for i in items] == [imported["id"]]
    assert "rows" not in items[0], "a listing carries metadata, not 43 rows"


def test_nobody_else_can_read_it(client, imported):
    """Ids are sequential, so 404 has to be the answer to a guess as well as
    to a miss — otherwise the response says whether it exists."""
    sign_in(client, "stranger", fresh=True)
    assert client.get(f"/api/parseshots/{imported['id']}").status_code == 404
    assert client.get("/api/parseshots").json()["items"] == []
    assert client.delete(f"/api/parseshots/{imported['id']}").status_code == 404


def test_signing_out_closes_it(client, imported):
    client.cookies.clear()
    assert client.get("/api/parseshots").status_code == 401
    assert client.post("/api/parseshots", files={
        "file": ("x.png", b"not an image", "image/png")}).status_code == 401


def test_a_non_table_image_is_refused(client):
    sign_in(client, "shotowner")
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(buf, format="PNG")
    r = client.post("/api/parseshots",
                    files={"file": ("blank.png", buf.getvalue(), "image/png")})
    assert r.status_code == 422
    assert "grid" in r.json()["detail"] or "ACT" in r.json()["detail"]


def test_rubbish_is_refused_without_a_traceback(client):
    sign_in(client, "shotowner")
    r = client.post("/api/parseshots",
                    files={"file": ("x.png", b"nowhere near a png", "image/png")})
    assert r.status_code == 422


def test_naming_a_shot_the_ocr_could_not_read(client, imported):
    """A screenshot cropped to the table carries no title bar, so the metadata
    around the numbers is the reader's to supply."""
    sign_in(client, "shotowner")
    r = client.patch(f"/api/parseshots/{imported['id']}",
                     json={"character_name": "Zylphax", "zone": "Ascent of the Awakened",
                           "encounter": "Mayong Mistmoore", "kind": "heal",
                           "when_text": "8/2/2026 21:14"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["character_name"] == "Zylphax"
    assert got["zone"] == "Ascent of the Awakened"
    assert got["encounter"] == "Mayong Mistmoore"
    assert got["kind"] == "heal"
    assert got["when_text"] == "8/2/2026 21:14"
    # the numbers are untouched: this names a parse, it does not review one
    assert got["total"]["damage"] == 5386632
    assert len(got["rows"]) == 43
    # and back, so the rest of the module reads the shot it expects
    client.patch(f"/api/parseshots/{imported['id']}",
                 json={"character_name": "Bobby", "zone": "The Emerald Halls",
                       "encounter": "Galiel Spirithoof", "kind": "damage",
                       "when_text": None})


def test_a_fitted_length_is_not_typed_over(client, imported):
    """The duration is arithmetic over every row of the table, which beat the
    title bar in a way that was measured. A shot that has one keeps it."""
    sign_in(client, "shotowner")
    r = client.patch(f"/api/parseshots/{imported['id']}", json={"duration_s": 12})
    assert r.status_code == 409
    assert client.get(f"/api/parseshots/{imported['id']}").json()["duration_s"] == 391


def test_a_shot_with_no_clock_takes_one(client, imported):
    """Without a length the column refuses per-second numbers entirely, so the
    reader's own clock beats the refusal."""
    sign_in(client, "shotowner")
    conn = dbmod.get_db()
    with conn:
        conn.execute("UPDATE imported_parses SET duration_s=NULL WHERE id=?",
                     (imported["id"],))
    r = client.patch(f"/api/parseshots/{imported['id']}", json={"duration_s": 391})
    assert r.status_code == 200, r.text
    assert r.json()["duration_s"] == 391


def test_only_a_kind_the_app_knows(client, imported):
    sign_in(client, "shotowner")
    assert client.patch(f"/api/parseshots/{imported['id']}",
                        json={"kind": "threat"}).status_code == 422


def test_nobody_else_can_name_it(client, imported):
    sign_in(client, "stranger")
    assert client.patch(f"/api/parseshots/{imported['id']}",
                        json={"zone": "somewhere else"}).status_code == 404
    client.cookies.clear()
    assert client.patch(f"/api/parseshots/{imported['id']}",
                        json={"zone": "somewhere else"}).status_code == 401


def test_delete_is_the_owners_and_takes_the_files(client):
    sign_in(client, "shotowner")
    before = {p.name for p in dbmod.PARSESHOTS_DIR.glob("*.webp")}
    made = upload(client, "freethinker-zylphax-asame.png").json()
    assert made["decimal_mark"] == ","          # the German-locale fixture
    added = {p.name for p in dbmod.PARSESHOTS_DIR.glob("*.webp")} - before
    assert len(added) == 2
    assert client.delete(f"/api/parseshots/{made['id']}").status_code == 200
    assert client.get(f"/api/parseshots/{made['id']}").status_code == 404
    # deleting the parse deletes its picture — nothing is left behind on disk
    assert not (added & {p.name for p in dbmod.PARSESHOTS_DIR.glob("*.webp")})


def test_a_refused_image_leaves_nothing_on_disk(client):
    """Copies are written only after the table reads: a picture of something
    that isn't an ACT window has no reason to be on this disk."""
    sign_in(client, "shotowner")
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (700, 500), "white").save(buf, format="PNG")
    before = {p.name for p in dbmod.PARSESHOTS_DIR.glob("*")}
    r = client.post("/api/parseshots",
                    files={"file": ("nope.png", buf.getvalue(), "image/png")})
    assert r.status_code == 422
    assert {p.name for p in dbmod.PARSESHOTS_DIR.glob("*")} == before
