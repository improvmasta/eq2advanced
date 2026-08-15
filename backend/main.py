"""eq2advanced API — FastAPI app assembly."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import get_db, init_db
from routers import (admin_api, auth_api, census_api, characters_api, chat_api,
                     coach_api, discord_api, encounters_api, feedback_api, groups_api,
                     ingest_api, marks_api, notes_api, overlay_api,
                     parseshots_api, planner_api, plugin_api, replay_api,
                     sessions_api, tokens_api, uploads_api, zoneruns_api)
from spa import mount_spa

CENSUS_REFRESH_INTERVAL_S = 3600  # check hourly; each character syncs when >24h stale
PRUNE_CHECK_INTERVAL_S = 6 * 3600  # PRUNE_DAYS env sets retention (0 disables)
DISCORD_DELIVERY_INTERVAL_S = 2


async def _census_refresh_loop():
    from census import client as census_client
    from census.guilds import backfill_stale_guilds, retag_runs
    from census.sync import refresh_stale
    log = logging.getLogger("census")

    def _guild_pass():
        conn = get_db()
        # rows cached before guilds existed have a class and no guild; a slow
        # paced trickle re-reads them without leaning on a free public API
        report = backfill_stale_guilds(conn, census_client.shared_client())
        with conn:
            report["retagged"] = retag_runs(conn)
        return report

    while True:
        await asyncio.sleep(CENSUS_REFRESH_INTERVAL_S)
        try:
            n = await asyncio.to_thread(
                lambda: refresh_stale(get_db(), census_client.shared_client()))
            if n:
                log.info("nightly refresh synced %d characters", n)
        except Exception:
            log.exception("census refresh loop iteration failed")
        try:
            report = await asyncio.to_thread(_guild_pass)
            if report["asked"] or report["retagged"]:
                log.info("guild pass %s", report)
        except Exception:
            log.exception("guild backfill iteration failed")


LIVE_REAP_INTERVAL_S = 5 * 60     # how often to look for a live session nobody is feeding


async def _live_reap_loop():
    """Close live sessions whose client went away. Without this a session only
    closes when the SAME character's next batch arrives, so quitting EQ2 and ACT
    leaves it 'receiving' forever — still badged Live, and never rebuilt from
    raw, which also puts it out of reach of the PARSE_VERSION sweep."""
    from pipeline.live import reap_idle_live_sessions
    while True:
        await asyncio.sleep(LIVE_REAP_INTERVAL_S)
        try:
            closed = await asyncio.to_thread(lambda: reap_idle_live_sessions(get_db()))
            if closed:
                logging.getLogger("live").info("closed idle live sessions: %s", closed)
        except Exception:
            logging.getLogger("live").exception("live reap loop iteration failed")


async def _prune_loop(days: int):
    from pipeline.prune import prune_once
    while True:
        await asyncio.sleep(PRUNE_CHECK_INTERVAL_S)
        try:
            await asyncio.to_thread(lambda: prune_once(get_db(), days))
        except Exception:
            logging.getLogger("prune").exception("prune loop iteration failed")


async def _discord_delivery_loop():
    """Drain the durable chat-alert outbox. Matching happens at chat commit;
    this loop is only transport, so a Discord outage never slows ACT ingest."""
    import discord_alerts
    while True:
        try:
            report = await asyncio.to_thread(discord_alerts.deliver_pending)
            if report["sent"] or report["failed"]:
                logging.getLogger("discord-alerts").info("delivery pass %s", report)
        except Exception:
            logging.getLogger("discord-alerts").exception("delivery loop iteration failed")
        await asyncio.sleep(DISCORD_DELIVERY_INTERVAL_S)


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
    # `raw_deleted_ts` sessions were uploaded as parse-only: their stats are all
    # there is, and no parser improvement can ever reach them
    rows = conn.execute(
        "SELECT id FROM sessions WHERE status IN ('ready','parsing') AND pruned=0 "
        "AND raw_deleted_ts IS NULL "
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


def _startup_worker():
    """Reap first, then reparse. A live session abandoned across a restart is
    still 'receiving', which the reparse sweep skips — closing it first rebuilds
    it from raw at the current PARSE_VERSION in one pass instead of leaving it
    stale until the next reap tick."""
    from pipeline.live import reap_idle_live_sessions
    try:
        closed = reap_idle_live_sessions(get_db())
        if closed:
            logging.getLogger("live").info("closed idle live sessions at startup: %s", closed)
    except Exception:
        logging.getLogger("live").exception("startup live reap failed")
    _reparse_stale()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from census.catalog import backfill_scribed, reset_verdicts, seed_curated
    from parser import petnames
    # order matters: demote every machine-written pet/proc label to a candidate
    # FIRST, then put the curated verdicts back. A database that already learned
    # wrong loses its bad badges on the next restart, with no reparse — the
    # labels live in ability_catalog, not in the rolled-up rows.
    reset_verdicts(get_db())
    seed_curated(get_db())
    backfill_scribed(get_db())
    petnames.seed_curated(get_db())
    import threading
    threading.Thread(target=_startup_worker, daemon=True).start()
    tasks = [asyncio.create_task(_live_reap_loop())]
    import discord_alerts
    if discord_alerts.configured():
        tasks.append(asyncio.create_task(_discord_delivery_loop()))
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
app.include_router(groups_api.router, prefix="/api")
app.include_router(admin_api.router, prefix="/api")
app.include_router(plugin_api.router, prefix="/api")
app.include_router(feedback_api.router, prefix="/api")
app.include_router(parseshots_api.router, prefix="/api")
app.include_router(notes_api.router, prefix="/api")
app.include_router(marks_api.router, prefix="/api")
app.include_router(overlay_api.router, prefix="/api")
app.include_router(replay_api.router, prefix="/api")
app.include_router(chat_api.router, prefix="/api")
app.include_router(discord_api.router, prefix="/api")
app.include_router(planner_api.router, prefix="/api")

mount_spa(app)
