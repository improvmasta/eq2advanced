"""Serve the built Vite SPA from frontend/dist with an index.html fallback so
client-side routes deep-link correctly. API routes are registered first and
take precedence."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def mount_spa(app: FastAPI) -> None:
    if not DIST.exists():
        @app.get("/")
        def _no_ui():
            return JSONResponse({"detail": "frontend/dist missing — run npm run build"}, status_code=503)
        return

    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
