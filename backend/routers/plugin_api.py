"""The ACT plugin download.

The DLL is built by CI in `improvmasta/eq2advanced-act` and committed here under
`backend/refdata/plugin/`. Serving it ourselves rather than linking GitHub is
not laziness — a GitHub Actions artifact needs an authenticated session, lives
in a PRIVATE repo, and expires after 90 days, so a link to one is useless to
anybody who isn't Lindsay with a token, and stops working even for him. A raider
told "install the uploader" has to be able to click one link and get a file.

It is served ZIPPED: a bare `.dll` is on Chrome's and Edge's dangerous-file
list and gets blocked.

`GET /api/plugin`          -> {filename, version, size, sha256, built_ts,
                              download_name, download_size}, no auth.
                              `size`/`sha256` are the DLL's, so they still match
                              what CI built. Signed in, it also carries
                              `your_version` and `update_available` — see below.
`GET /api/plugin/download` -> the zip

**The update pill is for people who ALREADY have the plugin, and only them.**
A raider who has never paired is looking at the install steps; telling them
there is a newer version of a thing they do not have is noise. So
`update_available` is true only when this account has a pairing that has
reported a version (`device_tokens.client_version`, v30 — read off the
uploader's User-Agent) and that version is behind `VERSION`. Never heard from
is not behind: a NULL stays quiet, and so does anything that fails to parse as
a version, because the cost of a false pill is somebody reinstalling a plugin
that was already current.

Refresh it with `scripts/update-plugin.sh` after shipping the plugin repo.
"""

import hashlib
import io
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from db import get_db
from security import optional_user

router = APIRouter(tags=["plugin"])


def version_tuple(v: str | None) -> tuple[int, ...] | None:
    """`"0.2.0"` -> `(0, 2, 0)`, or None for anything that is not a version.

    Compared as numbers, never as strings: `"0.10.0" < "0.9.0"` is true of text
    and false of software, and getting that backwards would hide the pill from
    exactly the people a tenth release was for."""
    if not v:
        return None
    parts = v.strip().split(".")
    if not 1 <= len(parts) <= 4 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "refdata" / "plugin"
PLUGIN_DLL = PLUGIN_DIR / "EQ2Advanced.dll"
# Written beside the DLL by scripts/update-plugin.sh. A .NET assembly version
# cannot be read back without a PE parser, and this number decides who is shown
# an update — so it is committed as a fact rather than guessed from the file.
PLUGIN_VERSION_FILE = PLUGIN_DIR / "VERSION"
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


def _version() -> str | None:
    """What build the site is serving. Missing file = we do not know, and every
    version comparison downstream then answers "no update"."""
    try:
        return PLUGIN_VERSION_FILE.read_text().strip() or None
    except OSError:
        return None


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
        "version": _version(),
        "size": stat.st_size,
        "sha256": hashlib.sha256(data).hexdigest(),
        "built_ts": int(stat.st_mtime),
        "download_name": ZIP_NAME,
        "download_size": len(zipped),
    }
    return _meta_cache


@router.get("/plugin")
def plugin_info(user=Depends(optional_user)):
    meta = _meta()
    if meta is None:
        return {"available": False}
    out = {"available": True, **meta}
    if user is None:
        return out
    # The newest version any of this account's pairings has reported. Newest,
    # not oldest: two machines and one of them updated is a person who has seen
    # the new build and knows where it lives, and a pill that keeps nagging
    # about the laptop they raid from twice a year is a pill people learn to
    # ignore.
    seen = [version_tuple(r["client_version"]) for r in get_db().execute(
        "SELECT client_version FROM device_tokens "
        "WHERE user_id=? AND revoked_ts IS NULL AND client_version IS NOT NULL",
        (user["id"],))]
    seen = [v for v in seen if v is not None]
    current = version_tuple(out.get("version"))
    out["your_version"] = ".".join(str(n) for n in max(seen)) if seen else None
    out["update_available"] = bool(seen and current and max(seen) < current)
    return out


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
