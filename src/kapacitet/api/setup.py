"""Load a data bundle from the setup page: for someone who has the data file but no terminal.

The server must allow it (KAPACITET_SETUP_UPLOAD=1, which the codespace sets), it must run on the
reference network, and the results must be missing: a copy that has data never takes a bundle
this way. The browser sends the zip in chunks of a few MB, each a PUT with its byte offset, so a
dropped connection or a reloaded page carries on where it stopped and no proxy sees one huge
request. The chunks are appended under data/requests/setup-upload/. When the last one arrives,
bundle.unpack_bundle writes the data into data/processed and data/raw, checking every file's
sha256 and refusing anything else, and the server restarts under its supervisor so that no cache
outlives the empty copy.

This is the API's third place to write, after data/requests/ and data/datasets/.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .. import bundle
from .. import datasets as ds
from ..paths import DATA
from . import records

router = APIRouter(prefix="/api/v1", tags=["setup"])
log = logging.getLogger("kapacitet.api")

CHUNK_MAX = 16 * 2**20          # one PUT
TOTAL_MAX = 2 * 2**30           # one bundle (the full data zip to about 250 MB)
BUSY_S = 30                     # an upload that sent nothing for this long may be replaced by another
ZIP_MAGIC = b"PK\x03\x04"

ERRORS = {
    "setup_closed": "loading a data bundle from the browser is not enabled on this server",
    "already_computed": "this copy already has results; a bundle is only loaded into an empty copy",
    "other_network": "an imported network is in use; switch back to the reference network first",
    "upload_busy": "another upload is in progress",
    "offset_mismatch": "the upload is at byte {received}",
    "chunk_too_large": "a chunk is larger than {mb} MB",
    "no_space": "not enough free disk space: {need_mb} MB needed, {free_mb} MB free",
    "unpack_failed": "the bundle could not be unpacked: {detail}",
    **{c: bundle.ERRORS[c] for c in ("not_a_bundle", "damaged", "unexpected_entry", "bundle_too_large")},
}

_LOCK = threading.Lock()
# what is happening now; the bytes received are the partial file's size on disk
_STATE: dict = {"state": "idle", "files_done": 0, "files_total": 0, "error": None, "restart": None, "total": None}


def target() -> Path:
    """The data folder a bundle is unpacked into (a test points it elsewhere)."""
    return DATA


def _folder() -> Path:
    return records.folder() / "setup-upload"


def _part() -> Path:
    return _folder() / "bundle.zip.part"


def _meta_path() -> Path:
    return _folder() / "upload.json"


def _meta() -> dict:
    try:
        return json.loads(_meta_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_meta(meta: dict) -> None:
    path = _meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta), encoding="utf-8")
    os.replace(tmp, path)


def _clear() -> None:
    for p in (_part(), _meta_path(), _folder() / "bundle.zip"):
        if p.exists():
            p.unlink()


def closed_because() -> str | None:
    """Why the upload is closed (a code), or None when it is open."""
    from .main import results_ready

    if os.environ.get("KAPACITET_SETUP_UPLOAD") != "1":
        return "setup_closed"
    if ds.current() != ds.REFERENCE:
        return "other_network"
    if results_ready():
        return "already_computed"
    return None


def refusal(status: int, code: str, **params) -> HTTPException:
    return HTTPException(status, {"code": code, "message": ERRORS[code].format(**params), "params": params})


def _status() -> dict:
    part = _part()
    received = part.stat().st_size if part.exists() else 0
    meta = _meta()
    state = _STATE["state"]
    total = meta.get("total")
    if state == "idle" and received:
        state = "receiving"         # a page reloaded, or a server restarted, mid-upload: it resumes
    elif state in ("unpacking", "done", "failed") and _STATE["total"]:
        received = total = _STATE["total"]    # the whole file arrived; its partial copy is gone
    return {"open": closed_because() is None, "state": state, "received": received, "total": total,
            "fp": meta.get("fp"), "files_done": _STATE["files_done"], "files_total": _STATE["files_total"],
            "error": _STATE["error"], "restart": _STATE["restart"]}


@router.get("/setup/upload")
def upload_status() -> dict:
    """How far an upload has gone: bytes received, then files unpacked, then done or why it failed."""
    with _LOCK:
        return _status()


def _unpack(path: Path) -> None:
    def progress(done: int, total: int) -> None:
        with _LOCK:
            _STATE.update(files_done=done, files_total=total)

    try:
        r = bundle.unpack_bundle(path, data_dir=target(), force=True, only_data=True, progress=progress)
    except bundle.BundleError as err:
        with _LOCK:
            _STATE.update(state="failed", error={"code": err.code, "params": err.params})
        log.warning("setup upload refused: %s", err)
        return
    except Exception as err:     # a full disk half-way, a file the system would not write
        with _LOCK:
            _STATE.update(state="failed", error={"code": "unpack_failed", "params": {"detail": str(err)[:200]}})
        log.exception("setup upload could not be unpacked")
        return
    finally:
        if path.exists():
            path.unlink()
    supervised = os.environ.get("KAPACITET_SUPERVISED") == "1"
    with _LOCK:
        _STATE.update(state="done", restart="auto" if supervised else "manual")
        _clear()
    log.info("setup upload unpacked: %s files written", r["written"])
    if supervised:
        ds.restart_soon(1.0)


def _receive(body: bytes, offset: int, total: int, fp: str) -> dict:
    with _LOCK:
        if _STATE["state"] == "unpacking":
            raise refusal(409, "upload_busy")
        meta = _meta()
        part = _part()
        received = part.stat().st_size if part.exists() else 0
        recent = received and time.time() - meta.get("last", 0) < BUSY_S
        if offset == 0:
            if recent and meta.get("fp") != fp:
                raise refusal(409, "upload_busy")
            if not body.startswith(ZIP_MAGIC):
                raise refusal(422, "not_a_bundle")
            _folder().mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(_folder()).free
            if free < 3 * total:
                raise refusal(507, "no_space", need_mb=-(-3 * total // 2**20), free_mb=free // 2**20)
            _clear()
            _STATE.update(state="receiving", files_done=0, files_total=0, error=None, restart=None, total=None)
            meta = {"total": total, "fp": fp}
            received = 0
        elif meta.get("total") != total or meta.get("fp") != fp:
            # another file's upload: wait while it is alive, else start this one from the beginning
            if recent:
                raise refusal(409, "upload_busy")
            raise refusal(409, "offset_mismatch", received=0)
        if offset != received or received + len(body) > total:
            raise refusal(409, "offset_mismatch", received=received)
        with part.open("ab") as fh:
            fh.write(body)
        received += len(body)
        meta["last"] = time.time()
        _save_meta(meta)
        if received == total:
            done = _folder() / "bundle.zip"
            os.replace(part, done)
            _STATE.update(state="unpacking", files_done=0, files_total=0, error=None, total=total)
            threading.Thread(target=_unpack, args=(done,), daemon=True).start()
        else:
            _STATE["state"] = "receiving"
        return _status()


@router.put("/setup/upload", response_model=None)
async def upload_chunk(request: Request, offset: int = Query(..., ge=0), total: int = Query(..., gt=0),
                       fp: str = Query("", max_length=300)) -> dict | Response:
    """One chunk of the bundle, as raw bytes at `offset` of a file of `total` bytes (`fp` names the
    file, so two tabs cannot interleave two files). Offset 0 starts again from the beginning."""
    why = closed_because()
    if why is not None:
        raise refusal(404 if why == "setup_closed" else 409, why)
    if total > TOTAL_MAX:
        raise refusal(413, "bundle_too_large", gb=TOTAL_MAX // 2**30)
    if int(request.headers.get("content-length") or 0) > CHUNK_MAX:
        raise refusal(413, "chunk_too_large", mb=CHUNK_MAX // 2**20)
    try:
        body = await request.body()
    except ClientDisconnect:
        # the page was reloaded or closed mid-chunk: nothing was written, and the next try resumes
        return Response(status_code=400)
    if len(body) > CHUNK_MAX:
        raise refusal(413, "chunk_too_large", mb=CHUNK_MAX // 2**20)
    if not body:
        raise refusal(409, "offset_mismatch", received=_status()["received"])
    return await run_in_threadpool(_receive, body, offset, total, fp)
