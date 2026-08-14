"""Serve the built Vite SPA from frontend/dist with an index.html fallback so
client-side routes deep-link correctly. API routes are registered first and
take precedence.

This is also where a VISIT is counted (`visitors.py`), and it is the right place
for exactly one reason: index.html going out is somebody ARRIVING. A raider
moving between tabs inside the app never comes back here, an API call never
reaches this route at all, and a static file returns above the count. So the
tally is arrivals rather than requests, which is the number the admin page
wants and is also far cheaper than middleware over everything.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import auth
import siteconfig
import visitors
from db import get_db

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _count(request: Request, path: str) -> None:
    """Never let counting break a page. `visitors.note` already swallows its own
    failures; this catches the two lookups in front of it (the address and the
    session) for the same reason."""
    try:
        conn = get_db()
        visitors.maybe_sweep(conn)
        # `siteconfig` owns the client address — the proxies falsify
        # `request.client.host`, and a tally of the reverse proxy's own IP would
        # be one visitor a day forever.
        visitors.note(
            conn,
            siteconfig.client_ip(request),
            request.headers.get("user-agent"),
            path,
            # signed in is a FLAG on the day and never a user id. The cookie is
            # already here, so the lookup is free, and knowing the split between
            # strangers and accounts is the entire question being asked.
            auth.session_user(conn, request.cookies.get(auth.COOKIE)) is not None,
        )
    except Exception:                                   # noqa: BLE001
        pass


def mount_spa(app: FastAPI) -> None:
    if not DIST.exists():
        @app.get("/")
        def _no_ui():
            return JSONResponse({"detail": "frontend/dist missing — run npm run build"}, status_code=503)
        return

    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        # Starlette hands the path through unnormalized, so `..` reaches here and
        # DIST / path escapes the build dir — data/eq2advanced.db is two levels up.
        # Resolve and require containment before serving anything.
        if path:
            try:
                candidate = (DIST / path).resolve()
                candidate.relative_to(DIST.resolve())
            except (ValueError, OSError):
                pass
            else:
                if candidate.is_file():
                    return FileResponse(candidate)
        _count(request, path)
        return FileResponse(DIST / "index.html")
