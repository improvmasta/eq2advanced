"""The ACT plugin download.

The DLL is built by CI in `improvmasta/eq2advanced-act` and committed here under
`backend/refdata/plugin/`. Serving it ourselves rather than linking GitHub is
not laziness — a GitHub Actions artifact needs an authenticated session, lives
in a PRIVATE repo, and expires after 90 days, so a link to one is useless to
anybody who isn't Lindsay with a token, and stops working even for him. A raider
told "install the uploader" has to be able to click one link and get a file.

It is served ZIPPED: a bare `.dll` is on Chrome's and Edge's dangerous-file
list and gets blocked.

`GET /api/plugin`          -> {filename, size, sha256, built_ts, download_name,
                              download_size}, no auth. `size`/`sha256` are the
                              DLL's, so they still match what CI built.
`GET /api/plugin/download` -> the zip

Refresh it with `scripts/update-plugin.sh` after shipping the plugin repo.
"""

import hashlib
import io
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(tags=["plugin"])

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "refdata" / "plugin"
PLUGIN_DLL = PLUGIN_DIR / "EQ2Advanced.dll"
FILENAME = "EQ2Advanced.dll"
ZIP_NAME = "EQ2Advanced.zip"

_meta_cache: dict | None = None
_zip_cache: bytes | None = None


def _zip_bytes() -> bytes | None:
    """Build the zip once per process and hold it — it is 35 KB, the source
    only changes on redeploy, and the entry timestamp comes from the DLL's
    mtime so the same build always produces the same bytes."""
    global _zip_cache
    if _zip_cache is not None:
        return _zip_cache
    if not PLUGIN_DLL.exists():
        return None
    entry = zipfile.ZipInfo(FILENAME,
                            date_time=time.localtime(PLUGIN_DLL.stat().st_mtime)[:6])
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o644 << 16
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(entry, PLUGIN_DLL.read_bytes())
    _zip_cache = buf.getvalue()
    return _zip_cache


def _meta() -> dict | None:
    """Hash it once per process — the file only changes when the app is
    redeployed, and hashing 35 KB on every page load is pointless work."""
    global _meta_cache
    if _meta_cache is not None:
        return _meta_cache
    zipped = _zip_bytes()
    if zipped is None:
        return None
    data = PLUGIN_DLL.read_bytes()
    stat = PLUGIN_DLL.stat()
    _meta_cache = {
        "filename": FILENAME,
        "size": stat.st_size,
        "sha256": hashlib.sha256(data).hexdigest(),
        "built_ts": int(stat.st_mtime),
        "download_name": ZIP_NAME,
        "download_size": len(zipped),
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
    zipped = _zip_bytes()
    if zipped is None:
        raise HTTPException(404, "the plugin build is not on this server")
    # Content-Disposition so the browser saves it under a name that says what
    # it is instead of inventing one from the URL.
    return Response(zipped, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{ZIP_NAME}"',
    })
