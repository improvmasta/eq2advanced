"""Reading and writing the hand marks. `backend/marks.py` says what they are.

Two endpoints and no cleverness, because the interesting decisions are all in
the store: the patch MERGES, `null` deletes, and the caps are the store's.

THE THIRD DOOR IS NOT HERE. The in-game window and the stream overlay have no
cookie — the token in their URL is the whole credential — so they receive the
marks alongside their config on the poll they already run
(`routers/overlay_api.py`). That is deliberate: it gives those screens the
marks without giving them an account-authenticated endpoint, and it means a
pill toggled on the dashboard reaches the window beside somebody's hotbars on
the next tick rather than needing anything to be reopened.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import marks
from db import get_db
from security import require_user

router = APIRouter(tags=["marks"])


class MarksIn(BaseModel):
    # `{kind: {ability: true|false|null}}`. Free-form on the way in: the keys
    # are ability names out of a game the server has no list of, and the values
    # carry a third state a `bool` field cannot.
    marks: dict[str, dict[str, bool | None]] = Field(default_factory=dict)


@router.get("/marks")
def get_marks(user=Depends(require_user)):
    return {"marks": marks.read(get_db(), user["id"])}


@router.put("/marks")
def put_marks(body: MarksIn, user=Depends(require_user)):
    """Merge a patch in and hand back the whole set.

    The whole set, not the patch, because the client's next question is always
    "what do I have now" — and on the one call that matters, a browser handing
    over the marks it had before this table existed, the merged answer is the
    thing it needs to hold."""
    size = sum(len(v or {}) for v in body.marks.values())
    if size > marks.MAX_PATCH:
        raise HTTPException(413, "too many marks in one request")
    conn = get_db()
    marks.write(conn, user["id"], body.marks)
    return {"marks": marks.read(conn, user["id"])}
