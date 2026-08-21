"""The Planner (`/plan`) — gear targets for an expansion. See docs/planner.md.

  GET /api/plan/meta?eras=rok,eof   -> the expansions on offer + the facets in them
  GET /api/plan/items?…             -> the item table, ranked against an ORDER
  GET /api/plan/sets?…              -> the set-adornment view
  GET /api/plan/adornments           -> ordinary white socket choices
  GET /api/plan/outline?…           -> source mobs, quests and hard prerequisites
  GET /api/plan/character?name=…    -> a public character, no account needed
  GET/PUT /api/plan/saved-sets/…    -> five private loadouts per public character

**The catalog is open to anybody, signed in or not**, for the same reason
`/chat` is. Saved sets are the narrow exception: their two routes require an
    account and reach only that account's rows, keyed by whichever public
    character they are planning for. The key is organization, not ownership.
    Guests use character-keyed localStorage.

**`/plan/character` is the ONE route here that can reach the network**, and it
is the exception the rule was already making elsewhere: it runs on a name a
reader TYPED and pressed, never on a page load, which is the same shape as
`POST /characters/{id}/census/refresh`. It answers from cache first, falls back
to a stale answer when Census is unavailable, then asks EQ2 Lexicon only for a
first-ever uncached lookup. It writes nothing anybody owns — these character
records are public, and trying gear on your own toon should not be the one part
of this page that needs an account.

**WHICH EXPANSIONS COUNT IS THE READER'S**, which is why `eras` is a parameter
on catalog/outline reads and never a constant: EoF, RoK, or both. An era nobody has synced
answers with `items: 0` rather than an empty table, so the page can say so.

Nothing here fetches. `items.ensure` is network-bound and never runs in a
request handler (`docs/sharing.md`), and the same rule holds for the wiki: a
reader pressing a filter must not start a crawl.
"""

import ratelimit
import siteconfig
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from census import client as census_client
from census import sync as census_sync
from db import get_db
from planner import adornments, catalog, outline, saved_sets, wiki
from security import require_user

router = APIRouter(tags=["planner"])

# A hand-built URL should not be able to ask for the whole catalog in one
# answer. The table is paged by score, and 400 rows is well past what anybody
# reads — the filters are the way to a shorter list.
MAX_LIMIT = 400


class SavedSetIn(BaseModel):
    owner_key: str = Field(min_length=1, max_length=saved_sets.MAX_OWNER_KEY)
    owner_name: str = Field(min_length=1, max_length=saved_sets.MAX_OWNER_NAME)
    name: str = Field(min_length=0, max_length=saved_sets.MAX_NAME)
    payload: dict | None = None


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
    match_min: int | None = Query(None, ge=0, le=14),
    limit: int = Query(200, ge=1, le=MAX_LIMIT),
    sample: int | None = Query(None, ge=1, le=50),
):
    return catalog.search(
        get_db(), eras=_eras(eras), order=_list(order), required=_list(required),
        classes=_list(classes), slots=_list(slots), tiers=_list(tiers),
        kinds=_list(kinds), armor=_list(armor), level_min=level_min,
        level_max=level_max, q=q, carries_set=carries_set,
        hosts_turquoise=hosts_turquoise, has_proc=has_proc,
        match_min=match_min, limit=limit, sample=sample)


@router.get("/plan/sets")
def plan_sets(eras: str | None = Query(None), order: str | None = Query(None),
              classes: str | None = Query(None)):
    return catalog.sets(get_db(), eras=_eras(eras), order=_list(order),
                        classes=_list(classes))


@router.get("/plan/adornments")
def plan_adornments(color: str = Query("white", pattern="^white$")):
    """Static ordinary-adornment choices for the equipment socket picker.

    This is deliberately independent of the item-search expansion toggles.
    Whether an adornment fits is a property of its own predicate, level and
    legal host slots, not of which catalog the reader happens to be browsing.
    """
    return {"adornments": adornments.white_catalog()}


@router.get("/plan/epics")
def plan_epics(class_name: str = Query(..., alias="class", min_length=2,
                                      max_length=30)):
    return catalog.epics(get_db(), class_name)


@router.get("/plan/saved-sets")
def plan_saved_sets(owner_key: str = Query(..., min_length=1,
                                           max_length=saved_sets.MAX_OWNER_KEY),
                    user=Depends(require_user)):
    return {"sets": saved_sets.read(get_db(), user["id"], owner_key)}


@router.get("/plan/saved-set-owners")
def plan_saved_set_owners(user=Depends(require_user)):
    return {"characters": saved_sets.owners(get_db(), user["id"])}


@router.put("/plan/saved-sets/{slot}")
def put_plan_saved_set(slot: int, body: SavedSetIn,
                       user=Depends(require_user)):
    try:
        row = saved_sets.write(get_db(), user["id"], body.owner_key,
                               body.owner_name, slot, body.name, body.payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"set": row}


@router.get("/plan/outline")
def plan_outline(
    eras: str | None = Query(None),
    class_name: str | None = Query(None, alias="class"),
    # REPEATED PARAMETERS, NOT A COMMA LIST — every other list on this router
    # is a comma list because a stat key cannot contain a comma, and a page
    # title can: `One Fish, Two Fish` and `Mischief, Mayhem, Clockwork` are
    # real quests, and `Warm Skins, Fat Bellies` is a real prerequisite. The
    # separator that works on stat keys would invent five quests here and lose
    # the three that exist.
    item: list[str] | None = Query(None, description="shortlisted item pages"),
    set_: list[str] | None = Query(None, alias="set",
                                   description="tracked set-adornment pieces"),
):
    """One character's Outline. The character-keyed shortlist lives in the
    reader's browser, so it arrives with the request rather than being stored.
    The class is repeated here to enforce known item/epic eligibility on the
    read side as well as at the browser click."""
    return outline.outline(get_db(), eras=_eras(eras), items=item or [],
                           sets=set_ or [], class_name=class_name)


# A typed name is not a credential, but a FORCED refresh is a way to make this
# server talk to Census on demand, so it is counted the way every other
# unauthenticated way of spending our budget is. `ratelimit` counts to
# `MAX_FAILURES` in a 15-minute window, which is the right order for this:
# nobody legitimately re-reads the same toon six times in a quarter of an hour,
# and an ordinary lookup is not counted at all.
LOOKUP_SCOPE = "plan_character"


@router.get("/plan/character")
def plan_character(request: Request,
                   name: str = Query(..., min_length=2, max_length=40),
                   refresh: bool = Query(False)):
    """One public character, by the name a reader typed.

    404 when Census does not know the name AND nothing was cached for it — the
    two are one answer here, because "we cannot tell you" and "there is no such
    character" lead to the same next move and distinguishing them would mean
    reporting Census's health to somebody who did not ask about it."""
    conn = get_db()
    if refresh:
        # Only the FORCED path is limited. A plain lookup is answered from the
        # cache almost always, and rate-limiting a cache read would punish the
        # reader for the page being useful.
        who = siteconfig.client_ip(request)
        wait = ratelimit.retry_after(LOOKUP_SCOPE, who)
        if wait:
            raise HTTPException(429, f"try again in {wait}s")
        ratelimit.fail(LOOKUP_SCOPE, who)
    out = census_sync.lookup_by_name(
        conn, census_client.shared_client(), name, refresh=refresh)
    if out is None:
        raise HTTPException(404, f"no character record for {name!r}")
    return out
