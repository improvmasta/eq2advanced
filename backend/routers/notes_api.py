"""Raid notes — what you write down while the raid is happening.

The dashboard's right-hand column. Type a line, paste a screenshot, and it is
filed against what the raid was doing at that moment: a named being pulled
makes it a NAMED note, anything else makes it a ZONE note. Nothing here guesses
which — the client says, because the client is the thing that knows what is on
screen, and a server reading it back off encounter rows would be reconstructing
an answer it was already told.

The key is `(user_id, zone, mob_name)` and it is deliberately not an encounter.
Encounter ids do not survive: a live session is rebuilt from raw when it closes
and every id in it changes. Keying on the zone and the boss instead means a
note written tonight sits with the ones from every other attempt on that boss,
which is the pile this is meant to grow into — an outline of the zone, written
one pull at a time.

Notes are PRIVATE, exactly as imported parses are (routers/parseshots_api.py).
There is no group predicate here and there must not be one: `groups.py` owns
the single visibility rule, and sharing notes later means running them through
that, not adding a weaker rule beside it.

Screenshots are stored the way parse shots are — re-encoded to WebP under
`NOTESHOTS_DIR`, never the uploaded bytes, served by an owner-checked endpoint
rather than a static mount. The ~30 lines are duplicated from
`parseshots_api.py` rather than shared: the two have the same shape today and
different reasons to change (those are evidence for a number, these are notes
on a fight), and a common helper would make each one's rules the other's
problem.
"""

import io
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from PIL import Image
from pydantic import BaseModel, Field

from db import NOTESHOTS_DIR, get_db, rows_to_dicts
from security import require_user

router = APIRouter(tags=["notes"])

MAX_BYTES = 12 << 20
CONTENT_TYPES = ("image/png", "image/jpeg", "image/webp", "image/bmp", "image/gif")
MAX_BODY = 8000            # a note, not an essay

# A raid screenshot is read for what is IN it — a wall of adds, where the raid
# was standing — not for digits, so it is sized for looking at rather than for
# verifying a Crit%.
VIEW_MAX_W = 1800
VIEW_QUALITY = 80
THUMB_W = 320
THUMB_QUALITY = 72


class NoteIn(BaseModel):
    zone: str = Field(min_length=1, max_length=200)
    mob_name: str | None = Field(default=None, max_length=200)
    body: str = Field(default="", max_length=MAX_BODY)
    encounter_id: int | None = None
    zone_run_id: int | None = None


class NotePatch(BaseModel):
    body: str | None = Field(default=None, max_length=MAX_BODY)
    mob_name: str | None = Field(default=None, max_length=200)
    zone: str | None = Field(default=None, max_length=200)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _encode(im, max_w, quality):
    im = im.convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="WEBP", quality=quality, method=4)
    return buf.getvalue(), im.width, im.height


def _store_copies(data):
    """A viewable copy plus a thumbnail. The random stem is not a permission —
    the endpoint checks the owner — but a name nobody can derive means a leaked
    path never becomes one either."""
    stem = f"{int(time.time())}-{secrets.token_hex(8)}"
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        view, w, h = _encode(im, VIEW_MAX_W, VIEW_QUALITY)
        thumb, _, _ = _encode(im, THUMB_W, THUMB_QUALITY)
    NOTESHOTS_DIR.mkdir(parents=True, exist_ok=True)
    (NOTESHOTS_DIR / f"{stem}.webp").write_bytes(view)
    (NOTESHOTS_DIR / f"{stem}.thumb.webp").write_bytes(thumb)
    return {"image_name": f"{stem}.webp", "thumb_name": f"{stem}.thumb.webp",
            "image_w": w, "image_h": h, "image_bytes": len(view) + len(thumb)}


def _drop_copies(rows):
    """Best effort: a missing file must not stop a delete."""
    for row in rows:
        for key in ("image_name", "thumb_name"):
            name = row[key] if key in row.keys() else None
            if not name:
                continue
            try:
                (NOTESHOTS_DIR / name).unlink(missing_ok=True)
            except OSError:
                pass


def _owned(conn, note_id: int, user):
    row = conn.execute("SELECT * FROM raid_notes WHERE id=? AND user_id=?",
                       (note_id, user["id"])).fetchone()
    if row is None:
        # the same answer for "not yours" as for "not there"
        raise HTTPException(404, "no such note")
    return row


def _with_shots(conn, rows):
    notes = [dict(r) for r in rows]
    if not notes:
        return notes
    by_id = {n["id"]: n for n in notes}
    for n in notes:
        n["shots"] = []
    ph = ",".join("?" * len(by_id))
    for s in conn.execute(
            f"SELECT id, note_id, image_w, image_h FROM raid_note_shots "
            f"WHERE note_id IN ({ph}) ORDER BY id", tuple(by_id)):
        by_id[s["note_id"]]["shots"].append(
            {"id": s["id"], "w": s["image_w"], "h": s["image_h"]})
    return notes


@router.get("/notes")
def list_notes(zone: str | None = None, mob: str | None = None,
               user=Depends(require_user)):
    """Every note on one subject, oldest night first.

    No `zone` means everything you have written, which is what the outline
    view lists from. `mob` absent means the ZONE's notes — the trash ones —
    and that is a real filter, not a missing one: `mob_name IS NULL` is what
    makes a zone note a zone note.
    """
    conn = get_db()
    sql = "SELECT * FROM raid_notes WHERE user_id=?"
    args = [user["id"]]
    if zone:
        sql += " AND zone=?"
        args.append(zone)
        sql += " AND mob_name IS NULL" if not mob else " AND mob_name=?"
        if mob:
            args.append(mob)
    sql += " ORDER BY created_ts DESC, id DESC"
    return {"notes": _with_shots(conn, conn.execute(sql, args).fetchall())}


@router.get("/notes/outline")
def outline(user=Depends(require_user)):
    """The pile so far: every zone you have notes on, and the nameds inside it.

    This is the raid outline in its first form — a table of contents that
    wrote itself out of what got filed during pulls."""
    conn = get_db()
    return {"zones": rows_to_dicts(conn.execute(
        "SELECT n.zone, n.mob_name, COUNT(*) AS notes, "
        "  (SELECT COUNT(*) FROM raid_note_shots s "
        "     JOIN raid_notes n2 ON n2.id = s.note_id "
        "    WHERE n2.user_id = n.user_id AND n2.zone = n.zone "
        "      AND n2.mob_name IS n.mob_name) AS shots, "
        "  MAX(n.updated_ts) AS updated_ts "
        "FROM raid_notes n WHERE n.user_id=? "
        "GROUP BY n.zone, n.mob_name "
        "ORDER BY n.zone, (n.mob_name IS NULL) DESC, n.mob_name",
        (user["id"],)))}


@router.post("/notes")
def add_note(body: NoteIn, user=Depends(require_user)):
    zone = _clean(body.zone)
    if not zone:
        raise HTTPException(422, "a note needs a zone")
    text = body.body.strip()
    now = int(time.time())
    conn = get_db()
    with conn:
        cur = conn.execute(
            "INSERT INTO raid_notes (user_id, zone, mob_name, body, encounter_id, "
            "zone_run_id, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
            (user["id"], zone, _clean(body.mob_name), text,
             body.encounter_id, body.zone_run_id, now, now))
    return get_note(cur.lastrowid, user)


@router.get("/notes/{note_id}")
def get_note(note_id: int, user=Depends(require_user)):
    conn = get_db()
    return _with_shots(conn, [_owned(conn, note_id, user)])[0]


@router.patch("/notes/{note_id}")
def edit_note(note_id: int, patch: NotePatch, user=Depends(require_user)):
    conn = get_db()
    _owned(conn, note_id, user)
    sets, args = [], []
    if patch.body is not None:
        sets.append("body=?")
        args.append(patch.body.strip())
    if patch.zone is not None:
        zone = _clean(patch.zone)
        if not zone:
            raise HTTPException(422, "a note needs a zone")
        sets.append("zone=?")
        args.append(zone)
    if patch.mob_name is not None:
        # "" means move it back to the zone — the only way to say "this turned
        # out to be trash" without deleting what was written
        sets.append("mob_name=?")
        args.append(_clean(patch.mob_name))
    if sets:
        sets.append("updated_ts=?")
        args += [int(time.time()), note_id, user["id"]]
        with conn:
            conn.execute(f"UPDATE raid_notes SET {', '.join(sets)} "
                         "WHERE id=? AND user_id=?", args)
    return get_note(note_id, user)


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, user=Depends(require_user)):
    conn = get_db()
    _owned(conn, note_id, user)
    shots = conn.execute(
        "SELECT image_name, thumb_name FROM raid_note_shots WHERE note_id=?",
        (note_id,)).fetchall()
    with conn:
        conn.execute("DELETE FROM raid_note_shots WHERE note_id=?", (note_id,))
        conn.execute("DELETE FROM raid_notes WHERE id=? AND user_id=?",
                     (note_id, user["id"]))
    _drop_copies(shots)
    return {"id": note_id, "deleted": True}


@router.post("/notes/{note_id}/shots")
def add_shot(note_id: int, file: UploadFile, user=Depends(require_user)):
    conn = get_db()
    _owned(conn, note_id, user)
    data = file.file.read(MAX_BYTES + 1)
    if not data:
        raise HTTPException(422, "no image received")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"keep the screenshot under {MAX_BYTES >> 20} MB")
    if file.content_type and file.content_type not in CONTENT_TYPES:
        raise HTTPException(415, "that isn't an image")
    try:
        img = _store_copies(data)
    except Exception:
        raise HTTPException(422, "could not read that image")
    finally:
        del data                      # the ORIGINAL bytes stop here either way
    with conn:
        conn.execute(
            "INSERT INTO raid_note_shots (note_id, image_name, thumb_name, "
            "image_w, image_h, image_bytes, created_ts) VALUES (?,?,?,?,?,?,?)",
            (note_id, img["image_name"], img["thumb_name"], img["image_w"],
             img["image_h"], img["image_bytes"], int(time.time())))
    return get_note(note_id, user)


@router.get("/notes/{note_id}/shots/{shot_id}/image")
def shot_image(note_id: int, shot_id: int, thumb: int = 0,
               user=Depends(require_user)):
    """Behind the same owner check as the note. Served rather than mounted, and
    cached `private` — a shared cache must not hold somebody's raid."""
    conn = get_db()
    _owned(conn, note_id, user)
    row = conn.execute(
        "SELECT image_name, thumb_name FROM raid_note_shots WHERE id=? AND note_id=?",
        (shot_id, note_id)).fetchone()
    if row is None:
        raise HTTPException(404, "no such screenshot")
    name = row["thumb_name"] if thumb else row["image_name"]
    path = NOTESHOTS_DIR / name if name else None
    if path is None or not path.is_file():
        raise HTTPException(404, "the screenshot is no longer on disk")
    return Response(path.read_bytes(), media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/notes/{note_id}/shots/{shot_id}")
def delete_shot(note_id: int, shot_id: int, user=Depends(require_user)):
    conn = get_db()
    _owned(conn, note_id, user)
    row = conn.execute(
        "SELECT image_name, thumb_name FROM raid_note_shots WHERE id=? AND note_id=?",
        (shot_id, note_id)).fetchone()
    if row is None:
        raise HTTPException(404, "no such screenshot")
    with conn:
        conn.execute("DELETE FROM raid_note_shots WHERE id=?", (shot_id,))
    _drop_copies([row])
    return get_note(note_id, user)
