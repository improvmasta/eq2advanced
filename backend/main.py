"""eq2advanced API — FastAPI app assembly."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import get_db, init_db
from routers import (auth_api, census_api, characters_api, coach_api, encounters_api,
                     ingest_api, sessions_api, tokens_api, uploads_api)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from census.catalog import seed_curated
    seed_curated(get_db())
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
app.include_router(census_api.router, prefix="/api")
app.include_router(coach_api.router, prefix="/api")

mount_spa(app)
