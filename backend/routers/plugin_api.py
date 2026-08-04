"""The ACT plugin download.

The DLL is built by CI in `improvmasta/eq2advanced-act` and committed here under
`backend/refdata/plugin/`. Serving it ourselves rather than linking GitHub is
not laziness — a GitHub Actions artifact needs an authenticated session, lives
in a PRIVATE repo, and expires after 90 days, so a link to one is useless to
anybody who isn't Lindsay with a token, and stops working even for him. A raider
told "install the uploader" has to be able to click one link and get a file.

`GET /api/plugin`          -> {version, size, sha256, built_ts} (no auth: the
                              Import page shows it, and there is nothing here
                              worth hiding)
`GET /api/plugin/download` -> the DLL

Refresh it with `scripts/update-plugin.sh` after shipping the plugin repo.
"""

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["plugin"])

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "refdata" / "plugin"
PLUGIN_DLL = PLUGIN_DIR / "EQ2Advanced.dll"
FILENAME = "EQ2Advanced.dll"

_meta_cache: dict | None = None


def _meta() -> dict | None:
    """Hash it once per process — the file only changes when the app is
    redeployed, and hashing 35 KB on every page load is pointless work."""
    global _meta_cache
    if _meta_cache is not None:
        return _meta_cache
    if not PLUGIN_DLL.exists():
        return None
    data = PLUGIN_DLL.read_bytes()
    stat = PLUGIN_DLL.stat()
    _meta_cache = {
        "filename": FILENAME,
        "size": stat.st_size,
        "sha256": hashlib.sha256(data).hexdigest(),
        "built_ts": int(stat.st_mtime),
    }
    return _meta_cache


@router.get("/plugin")
def plugin_info():
    meta = _meta()
    if meta is None:
        return {"available": False}
    return {"available": True, **meta}


@router.get("/plugin/download")
def plugin_download():
    if not PLUGIN_DLL.exists():
        raise HTTPException(404, "the plugin build is not on this server")
    # Content-Disposition so the browser saves it as the name ACT expects
    # instead of inventing one from the URL.
    return FileResponse(PLUGIN_DLL, media_type="application/octet-stream",
                        filename=FILENAME)
