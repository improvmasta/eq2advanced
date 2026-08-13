"""Parses imported from an ACT screenshot.

The comparison people actually do is a screenshot from Discord next to their
own parse, so this is the door for the half that never had a log. The reading
itself is `pipeline/actshot.py`; everything here is ownership and storage.

Three rules the rest of the app depends on:

* **A shot is never a session.** It writes one row in `imported_parses` and
  touches nothing else — no character, no encounter, no zone run. Nothing that
  rolls up, ranks or matches raids can reach it, which is the point: these are
  claims about somebody else's night, read off a JPEG.
* **A shot is PRIVATE to whoever imported it.** There is no sharing here and
  no group predicate to get wrong. `groups.py` owns the one visibility rule
  for real parses; adding a second, weaker one beside it is how that rule
  starts drifting. If shots ever need sharing, they get run through the
  existing predicate rather than a new one.
* **The image is kept, but never the original.** A re-encoded copy and a
  thumbnail land in `PARSESHOTS_DIR`; the bytes that were uploaded do not.
  Keeping a picture earns its place because some columns cannot be checked by
  arithmetic — the screenshot is the only other evidence those numbers have,
  and a parse you can't put beside its source is a parse you have to take on
  faith. Re-encoding means what sits on disk is an image this app produced at
  a size it chose, and it drops whatever the original file carried besides
  pixels. The copies are as private as the row: same owner check, no
  guessable path, served by an endpoint rather than from a static mount.

Reading a shot takes seconds, not milliseconds. The endpoint is a plain `def`,
so FastAPI runs it in the threadpool and one slow import doesn't stall the
event loop for everyone else.
"""

import io
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from PIL import Image
from pydantic import BaseModel, Field

from db import PARSESHOTS_DIR, get_db, json_dumps, row_to_dict, rows_to_dicts
from pipeline.actshot import ShotError, extract
from security import require_user

router = APIRouter(tags=["parseshots"])

# A screenshot is a screenshot. Well past a 4K PNG of a full ACT window, and
# far short of anything worth streaming to disk.
MAX_BYTES = 12 << 20
CONTENT_TYPES = ("image/png", "image/jpeg", "image/webp", "image/bmp", "image/gif")

# The kept copy is for READING NUMBERS OFF, so it is not shrunk to a
# convenient web size — the whole point is checking a Crit% nothing else can
# verify. It is only bounded so a 4K capture can't sit on disk at full size,
# and the quality is high because WebP artefacts land hardest on exactly what
# matters here: small antialiased digits.
VIEW_MAX_W = 2200
VIEW_QUALITY = 82
THUMB_W = 320
THUMB_QUALITY = 72


def _encode(im, max_w, quality):
    im = im.convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="WEBP", quality=quality, method=4)
    return buf.getvalue(), im.width, im.height


def _store_copies(data):
    """Write a viewable copy and a thumbnail; return what to record.

    Names carry a random token rather than the row id: the files are served by
    an owner-checked endpoint, so the name is not a permission — but a name
    nobody can derive means a stray path never becomes one either."""
    stem = f"{int(time.time())}-{secrets.token_hex(8)}"
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        view, w, h = _encode(im, VIEW_MAX_W, VIEW_QUALITY)
        thumb, _, _ = _encode(im, THUMB_W, THUMB_QUALITY)
    PARSESHOTS_DIR.mkdir(parents=True, exist_ok=True)
    (PARSESHOTS_DIR / f"{stem}.webp").write_bytes(view)
    (PARSESHOTS_DIR / f"{stem}.thumb.webp").write_bytes(thumb)
    return {"image_name": f"{stem}.webp", "thumb_name": f"{stem}.thumb.webp",
            "image_w": w, "image_h": h, "image_bytes": len(view) + len(thumb)}


def _drop_copies(row):
    """Best effort: a missing file must not stop a delete. The row is the
    record; the images are derived from something we no longer have."""
    for key in ("image_name", "thumb_name"):
        name = row[key] if key in row.keys() else None
        if not name:
            continue
        try:
            (PARSESHOTS_DIR / name).unlink(missing_ok=True)
        except OSError:
            pass

# What a listing needs; the rows themselves are fetched one shot at a time.
# `thumb_name` is not sent — the client asks for /thumb by id, so the on-disk
# name stays server-side and there is one door to the images, not two.
LIST_COLS = ("id, title, zone, encounter, character_name, kind, duration_s, "
             "when_text, source, created_ts, image_w, image_h, "
             "(thumb_name IS NOT NULL) AS has_image")


def _shot_row(row):
    """One stored shot, JSON columns expanded and file names withheld."""
    d = row_to_dict(row)
    if d is None:
        return None
    for col, key in (("columns_json", "columns"), ("rows_json", "rows"),
                     ("total_json", "total"), ("notes_json", "notes")):
        if col in d:
            raw = d.pop(col)
            d[key] = json.loads(raw) if raw else ([] if key != "total" else None)
    d["has_image"] = bool(d.pop("image_name", None))
    d.pop("thumb_name", None)
    return d


@router.get("/parseshots")
def list_shots(user=Depends(require_user)):
    """Your imported parses. Yours only — see the module docstring."""
    conn = get_db()
    return {"items": rows_to_dicts(conn.execute(
        f"SELECT {LIST_COLS} FROM imported_parses WHERE user_id=? "
        "ORDER BY created_ts DESC, id DESC", (user["id"],)))}


@router.get("/parseshots/{shot_id}")
def get_shot(shot_id: int, user=Depends(require_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM imported_parses WHERE id=? AND user_id=?",
                       (shot_id, user["id"])).fetchone()
    if row is None:
        # Same answer for "not yours" as for "not there": whether a stranger's
        # import exists is not a question this endpoint answers.
        raise HTTPException(404, "no such imported parse")
    return _shot_row(row)


@router.post("/parseshots")
def import_shot(file: UploadFile, user=Depends(require_user)):
    data = file.file.read(MAX_BYTES + 1)
    if not data:
        raise HTTPException(422, "no image received")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"keep the screenshot under {MAX_BYTES >> 20} MB")
    if file.content_type and file.content_type not in CONTENT_TYPES:
        raise HTTPException(415, "that isn't an image")

    try:
        shot = extract(data)
    except ShotError as e:
        # The reader's own refusals are the useful message — "no table grid
        # found in this image" tells someone they pasted the wrong window.
        raise HTTPException(422, str(e))
    except Exception:
        raise HTTPException(422, "could not read an ACT table out of that image")

    # Only once the table read: a picture of something that isn't an ACT window
    # has no reason to be on this disk.
    try:
        img = _store_copies(data)
    except Exception:
        img = {"image_name": None, "thumb_name": None,
               "image_w": None, "image_h": None, "image_bytes": None}
    finally:
        del data                      # the ORIGINAL bytes stop here either way

    now = int(time.time())
    conn = get_db()
    with conn:
        cur = conn.execute(
            "INSERT INTO imported_parses (user_id, title, zone, encounter, "
            "character_name, kind, duration_s, when_text, decimal_mark, "
            "columns_json, total_json, rows_json, notes_json, source, "
            "image_name, thumb_name, image_w, image_h, image_bytes, created_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'screenshot',?,?,?,?,?,?)",
            (user["id"], shot.title, shot.zone, shot.encounter, shot.character,
             shot.kind or "damage", shot.duration_s, shot.when, shot.decimal,
             json_dumps(shot.columns), json_dumps(shot.total),
             json_dumps(shot.rows), json_dumps(shot.notes),
             img["image_name"], img["thumb_name"], img["image_w"],
             img["image_h"], img["image_bytes"], now))
    return get_shot(cur.lastrowid, user)


class ShotPatch(BaseModel):
    """What a reader may say about a shot the reader can see and OCR could not.

    NAMES only, plus the two things that decide how the parse is read. Not one
    number out of the table: those are checked against each other at import
    (`pipeline/actshot.py`), and a hand-typed cell would be the one figure on
    the page with no evidence behind it — the review step this deliberately
    does not have. What is here is the metadata around the numbers, which is
    exactly what OCR misses when somebody crops the title bar away.
    """

    character_name: str | None = Field(default=None, max_length=64)
    zone: str | None = Field(default=None, max_length=200)
    encounter: str | None = Field(default=None, max_length=200)
    when_text: str | None = Field(default=None, max_length=64)
    kind: str | None = Field(default=None, pattern="^(damage|heal)$")
    # Seconds. Accepted ONLY into a shot that has none — see below.
    duration_s: int | None = Field(default=None, ge=1, le=86_400)


@router.patch("/parseshots/{shot_id}")
def edit_shot(shot_id: int, patch: ShotPatch, user=Depends(require_user)):
    """Name a shot the reader can read and the OCR could not.

    A screenshot cropped to the table carries no title bar, so the character,
    the zone, the fight and the date arrive empty and the import is called
    `Imported parse #12` for the rest of its life. Everything here is a CLAIM
    by the person who imported it, which is what the row already was.

    The LENGTH is the exception, and only half an exception: it is arithmetic
    (`_duration_from_table` — the mode of Damage/EncDPS over forty rows, which
    beats the title bar and beat it in a way that was measured), so a shot that
    HAS one does not take a typed replacement. A shot with none has nothing to
    overrule: without it the column refuses to show per-second numbers at all,
    and a length off the reader's own clock is better than that refusal.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT duration_s FROM imported_parses WHERE id=? AND user_id=?",
        (shot_id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "no such imported parse")

    fields = patch.model_dump(exclude_unset=True)
    if "duration_s" in fields and row["duration_s"] is not None:
        raise HTTPException(
            409, "the fight length was read off the table — it can't be typed over")

    sets, values = [], []
    for key, value in fields.items():
        if isinstance(value, str):
            value = value.strip() or None
        sets.append(f"{key}=?")
        values.append(value)
    if sets:
        with conn:
            conn.execute(
                f"UPDATE imported_parses SET {', '.join(sets)} WHERE id=? AND user_id=?",
                (*values, shot_id, user["id"]))
    return get_shot(shot_id, user)


@router.get("/parseshots/{shot_id}/image")
def shot_image(shot_id: int, thumb: int = 0, user=Depends(require_user)):
    """The kept copy, behind the same owner check as the numbers.

    Served rather than mounted: a static directory would make the filename the
    permission, and these are somebody's screenshots. Cached `private` for the
    same reason — a shared cache must not hold them."""
    conn = get_db()
    row = conn.execute(
        "SELECT image_name, thumb_name FROM imported_parses WHERE id=? AND user_id=?",
        (shot_id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "no such imported parse")
    name = row["thumb_name"] if thumb else row["image_name"]
    if not name:
        raise HTTPException(404, "no screenshot kept for this one")
    path = PARSESHOTS_DIR / name
    if not path.is_file():
        raise HTTPException(404, "the screenshot is no longer on disk")
    return Response(path.read_bytes(), media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/parseshots/{shot_id}")
def delete_shot(shot_id: int, user=Depends(require_user)):
    conn = get_db()
    row = conn.execute(
        "SELECT image_name, thumb_name FROM imported_parses WHERE id=? AND user_id=?",
        (shot_id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "no such imported parse")
    with conn:
        conn.execute("DELETE FROM imported_parses WHERE id=? AND user_id=?",
                     (shot_id, user["id"]))
    # after the row is gone: an orphaned file is tidy-up, an orphaned row
    # pointing at a deleted image is a broken page
    _drop_copies(row)
    return {"id": shot_id, "deleted": True}
