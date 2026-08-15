"""The Planner (`/plan`) — gear targets for an expansion. See docs/planner.md.

  GET /api/plan/meta?eras=rok,eof   -> the expansions on offer + the facets in them
  GET /api/plan/items?…             -> the item table, ranked against an ORDER
  GET /api/plan/sets?…              -> the set-adornment view

**Open to anybody, signed in or not**, for the same reason `/chat` is: none of
these routes reaches a parse, a session or an account. Every row is reference
data about the GAME — one row serves every reader forever — and there is no
POST here at all. The catalog is filled by `tools/sync_planner.py`, run by hand.

**WHICH EXPANSIONS COUNT IS THE READER'S**, which is why `eras` is a parameter
on all three and never a constant: EoF, RoK, or both. An era nobody has synced
answers with `items: 0` rather than an empty table, so the page can say so.

Nothing here fetches. `items.ensure` is network-bound and never runs in a
request handler (`docs/sharing.md`), and the same rule holds for the wiki: a
reader pressing a filter must not start a crawl.
"""

from fastapi import APIRouter, Query

from db import get_db
from planner import catalog, wiki

router = APIRouter(tags=["planner"])

# A hand-built URL should not be able to ask for the whole catalog in one
# answer. The table is paged by score, and 400 rows is well past what anybody
# reads — the filters are the way to a shorter list.
MAX_LIMIT = 400


def _list(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def _eras(value: str | None) -> list[str]:
    return [e for e in _list(value) if e in wiki.ERAS] or list(wiki.DEFAULT_ERAS)


@router.get("/plan/meta")
def plan_meta(eras: str | None = Query(None)):
    return catalog.meta(get_db(), _eras(eras))


@router.get("/plan/items")
def plan_items(
    eras: str | None = Query(None),
    order: str | None = Query(None, description="stat keys, best first"),
    required: str | None = Query(None, description="stats an item must have"),
    classes: str | None = Query(None),
    slots: str | None = Query(None),
    tiers: str | None = Query(None),
    kinds: str | None = Query(None, description="raid|group|solo|quest"),
    armor: str | None = Query(None, description="Cloth|Leather|Chain|Plate"),
    level_min: int | None = Query(None, ge=1, le=200),
    level_max: int | None = Query(None, ge=1, le=200),
    q: str | None = Query(None, max_length=80),
    carries_set: bool = Query(False),
    hosts_turquoise: bool = Query(False),
    has_proc: bool = Query(False),
    # How many of the priority stats a row must actually carry. Omitted means
    # the four-stat floor (`catalog.default_match_min`), which is what makes
    # naming three stats show items with two of them rather than everything
    # with one.
    match_min: int | None = Query(None, ge=0, le=13),
    limit: int = Query(200, ge=1, le=MAX_LIMIT),
):
    return catalog.search(
        get_db(), eras=_eras(eras), order=_list(order), required=_list(required),
        classes=_list(classes), slots=_list(slots), tiers=_list(tiers),
        kinds=_list(kinds), armor=_list(armor), level_min=level_min,
        level_max=level_max, q=q, carries_set=carries_set,
        hosts_turquoise=hosts_turquoise, has_proc=has_proc,
        match_min=match_min, limit=limit)


@router.get("/plan/sets")
def plan_sets(eras: str | None = Query(None), order: str | None = Query(None),
              classes: str | None = Query(None)):
    return catalog.sets(get_db(), eras=_eras(eras), order=_list(order),
                        classes=_list(classes))
