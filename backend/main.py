"""eq2advanced API — FastAPI app assembly."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import get_db, init_db
from routers import (auth_api, census_api, characters_api, coach_api, encounters_api,
                     ingest_api, sessions_api, tokens_api, uploads_api, zoneruns_api)
from spa import mount_spa

CENSUS_REFRESH_INTERVAL_S = 3600  # check hourly; each character syncs when >24h stale
PRUNE_CHECK_INTERVAL_S = 6 * 3600  # PRUNE_DAYS env sets retention (0 disables)


async def _census_refresh_loop():
    from census import client as census_client
    from census.sync import refresh_stale
    while True:
        await asyncio.sleep(CENSUS_REFRESH_INTERVAL_S)
        try:
            n = await asyncio.to_thread(
                lambda: refresh_stale(get_db(), census_client.shared_client()))
            if n:
                logging.getLogger("census").info("nightly refresh synced %d characters", n)
        except Exception:
            logging.getLogger("census").exception("census refresh loop iteration failed")


async def _prune_loop(days: int):
    from pipeline.prune import prune_once
    while True:
        await asyncio.sleep(PRUNE_CHECK_INTERVAL_S)
        try:
            await asyncio.to_thread(lambda: prune_once(get_db(), days))
        except Exception:
            logging.getLogger("prune").exception("prune loop iteration failed")


def _reparse_stale():
    """Reparse ready, un-pruned sessions whose parse_version is stale — parser
    or rollup semantics changed since they were parsed. Oldest first, so
    knowledge learned from early sessions feeds later ones. Runs in a worker
    thread at startup; pruned sessions are skipped (frozen by design).

    Zone runs relink before AND after: the pre-pass gives the UI runs
    immediately (the linker only reads existing encounter rows — this IS the
    migration for pre-zone_runs databases), and each reparse relinks its own
    character anyway, so the post-pass just converges any mixed-version dedupe."""
    from pipeline.ingest_writer import PARSE_VERSION, parse_session, session_raw_paths
    from pipeline.zoneruns import relink_all
    log = logging.getLogger("reparse")
    conn = get_db()
    try:
        relink_all(conn)
    except Exception:
        log.exception("zone-run relink failed")
    # 'parsing' at startup is always an orphan — parse threads die with the
    # process (incl. dev hot reloads), leaving the flag behind
    rows = conn.execute(
        "SELECT id FROM sessions WHERE status IN ('ready','parsing') AND pruned=0 "
        "AND (parse_version IS NULL OR parse_version < ?) ORDER BY id",
        (PARSE_VERSION,)).fetchall()
    if not rows:
        return
    log.info("reparsing %d stale sessions (parse_version < %d)", len(rows), PARSE_VERSION)
    for r in rows:
        try:
            paths = session_raw_paths(conn, r["id"])
            if paths:
                parse_session(r["id"], paths)
        except Exception:
            log.exception("reparse of session %d failed", r["id"])
    try:
        relink_all(conn)
    except Exception:
        log.exception("zone-run relink failed")
    log.info("reparse sweep done")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from census.catalog import seed_curated
    from parser import petnames
    seed_curated(get_db())
    petnames.seed_curated(get_db())
    import threading
    threading.Thread(target=_reparse_stale, daemon=True).start()
    tasks = []
    if os.environ.get("CENSUS_AUTO_REFRESH", "1") != "0":
        tasks.append(asyncio.create_task(_census_refresh_loop()))
    prune_days = int(os.environ.get("PRUNE_DAYS", "180"))
    if prune_days > 0:
        tasks.append(asyncio.create_task(_prune_loop(prune_days)))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="eq2advanced", lifespan=lifespan)

app.include_router(auth_api.router, prefix="/api")
app.include_router(characters_api.router, prefix="/api")
app.include_router(tokens_api.router, prefix="/api")
app.include_router(uploads_api.router, prefix="/api")
app.include_router(ingest_api.router, prefix="/api")
app.include_router(sessions_api.router, prefix="/api")
app.include_router(encounters_api.router, prefix="/api")
app.include_router(zoneruns_api.router, prefix="/api")
app.include_router(census_api.router, prefix="/api")
app.include_router(coach_api.router, prefix="/api")

mount_spa(app)
