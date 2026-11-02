"""Pack the computed data so another machine can run the interface without the 15-minute pipeline.

    python -m kapacitet bundle                 # full: every screen and live requests (~400 MB of data)
    python -m kapacitet bundle --level lite    # the published results only, no live requests (~20 MB)
    python -m kapacitet bundle --web           # also web/dist, so the receiver needs no Node
    python -m kapacitet unbundle kapacitet-data.zip

The archive holds data/... (and web/dist/... with --web) plus bundle-manifest.json, which records
the version, the level and a sha256 for every file; unbundle checks each one. The data are derived
from OpenStreetMap (ODbL) and are not published with the MIT code: share a bundle privately.

Unpacking is also what the setup page does with a bundle uploaded from the browser
(api/setup.py), so it trusts nothing in the archive: it writes only under data/processed,
data/raw and web/dist, caps how much an archive may unpack to, streams every file through its
checksum into a temporary name and renames it only when the sum matches, and writes
showcase.json last, because the results count as present only once it is there.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
import zlib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import __version__
from .paths import DATA, WEB_DIST

# What the API reads to show the screens, without the live connection request.
LITE = (
    ("processed", "results"), ("processed", "ui"), ("processed", "grid"), ("processed", "validation"),
    ("processed", "pv", "roofs.parquet"), ("raw", "zones"), ("raw", "manifest.json"),
)
# Everything the pipeline wrote: the live request also needs the hourly profiles, the request
# baselines and the cached prices and weather.
FULL = (("processed",), ("raw",))
MANIFEST = "bundle-manifest.json"
# The folders an archive may write into. The API's own records (data/requests), the imported
# networks and the choice of network never come from a bundle.
DATA_PARTS = ("processed", "raw")
# /health counts the results as present once this file exists, so it is written last: an unpack
# that stops half-way leaves a copy that still asks for its data.
LAST = "data/processed/results/showcase.json"
# What one archive may unpack to (the full data are about 0.5 GB), against archives built to fill a disk.
MAX_FILE = 2 * 2**30
MAX_TOTAL = 8 * 2**30
MAX_MANIFEST = 32 * 2**20
_CHUNK = 1 << 20


ERRORS = {
    "nothing_computed": "nothing computed in {folder}: run `python -m kapacitet all` first",
    "unknown_level": "unknown level {level}: use lite or full",
    "web_not_built": "web/dist is not built: run `npm run build` in web/ first",
    "not_a_bundle": "this file is not a Kapacitet+ data bundle",
    "damaged": "{entry} is missing or does not match its checksum: the bundle is damaged",
    "unexpected_entry": "the bundle holds {entry}, outside the folders a bundle may write",
    "bundle_too_large": "the bundle unpacks to more than {gb} GB",
}


class BundleError(ValueError):
    """A bundle that cannot be made or unpacked as asked: a code the interface translates."""

    def __init__(self, code: str, **params):
        super().__init__(ERRORS[code].format(**params))
        self.code = code
        self.params = params


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(data_dir: Path, level: str):
    parts = LITE if level == "lite" else FULL
    for p in parts:
        root = data_dir.joinpath(*p)
        if root.is_file():
            yield root
        elif root.is_dir():
            yield from sorted(f for f in root.rglob("*") if f.is_file())


def make_bundle(out: str | Path, level: str = "full", web: bool = False, data_dir: Path | None = None) -> dict:
    """Write the archive; return its manifest."""
    if level not in ("lite", "full"):
        raise BundleError("unknown_level", level=level)
    data_dir = Path(data_dir or DATA)
    if not (data_dir / "processed" / "results" / "showcase.json").exists():
        raise BundleError("nothing_computed", folder=str(data_dir))
    out = Path(out)
    manifest: dict = {"kapacitet": __version__, "created": datetime.now(timezone.utc).isoformat(),
                      "level": level, "web": web, "files": {}}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in _files(data_dir, level):
            rel = "data/" + f.relative_to(data_dir).as_posix()
            manifest["files"][rel] = _sha256(f)
            z.write(f, rel)
        if web:
            if not (WEB_DIST / "index.html").exists():
                raise BundleError("web_not_built")
            for f in sorted(p for p in WEB_DIST.rglob("*") if p.is_file()):
                rel = "web/dist/" + f.relative_to(WEB_DIST).as_posix()
                manifest["files"][rel] = _sha256(f)
                z.write(f, rel)
        z.writestr(MANIFEST, json.dumps(manifest, indent=1))
    return manifest


def _read_manifest(z: zipfile.ZipFile) -> tuple[str, dict]:
    """The manifest and the folder the archive wraps everything in ("" when none).

    Re-compressing an unpacked bundle (macOS unpacks downloaded zips by itself) puts the whole
    archive inside one folder: a single wrapping folder is accepted, anything else is not a bundle.
    """
    names = set(z.namelist())
    if MANIFEST in names:
        prefix = ""
    else:
        wrapped = [n for n in names if n.endswith("/" + MANIFEST) and n.count("/") == 1 and not n.startswith("__MACOSX/")]
        if len(wrapped) != 1:
            raise BundleError("not_a_bundle")
        prefix = wrapped[0][: -len(MANIFEST)]
    info = z.getinfo(prefix + MANIFEST)
    if info.file_size > MAX_MANIFEST:
        raise BundleError("not_a_bundle")
    try:
        manifest = json.loads(z.read(info))
    except (ValueError, zipfile.BadZipFile, zlib.error, EOFError) as err:
        raise BundleError("not_a_bundle") from err
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        raise BundleError("not_a_bundle")
    return prefix, manifest


def _destination(name: str, data_dir: Path, web_dir: Path) -> tuple[str, Path]:
    """Where an entry lands, refusing anything outside data/processed, data/raw and web/dist."""
    parts = PurePosixPath(name).parts
    if (".." in parts or "." in parts or name.startswith("/") or any(":" in p or "\\" in p for p in parts)
            or len(parts) < 3):
        raise BundleError("unexpected_entry", entry=name)
    if parts[0] == "data" and parts[1] in DATA_PARTS:
        kind, base = "data", data_dir
        dest = data_dir.joinpath(*parts[1:]).resolve()
    elif parts[:2] == ("web", "dist"):
        kind, base = "web", web_dir
        dest = web_dir.joinpath(*parts[2:]).resolve()
    else:
        raise BundleError("unexpected_entry", entry=name)
    if not dest.is_relative_to(base):
        raise BundleError("unexpected_entry", entry=name)
    return kind, dest


def _extract(z: zipfile.ZipFile, info: zipfile.ZipInfo, name: str, digest: str, dest: Path) -> None:
    """Stream one entry through its checksum into a temporary file, then put it in place."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    size = 0
    try:
        with z.open(info) as src, tmp.open("wb") as out:
            for chunk in iter(lambda: src.read(_CHUNK), b""):
                size += len(chunk)
                if size > info.file_size or size > MAX_FILE:
                    raise BundleError("damaged", entry=name)
                h.update(chunk)
                out.write(chunk)
        if h.hexdigest() != digest.lower():
            raise BundleError("damaged", entry=name)
        os.replace(tmp, dest)
    except (zipfile.BadZipFile, zlib.error, EOFError) as err:
        raise BundleError("damaged", entry=name) from err
    finally:
        if tmp.exists():
            tmp.unlink()


def unpack_bundle(path: str | Path, data_dir: Path | None = None, web_dir: Path | None = None,
                  force: bool = False, *, only_data: bool = False,
                  progress: Callable[[int, int], None] | None = None) -> dict:
    """Extract a bundle into the data folder (and web/dist), checking every file's sha256.

    Existing files are kept unless `force`, so unpacking never overwrites someone's own results
    by accident. `only_data` leaves out the archive's web/dist (a server that builds its own
    interface). `progress(done, total)` is called after each file.
    """
    data_dir = Path(data_dir or DATA).resolve()
    web_dir = Path(web_dir or WEB_DIST).resolve()
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as err:
        raise BundleError("not_a_bundle") from err
    with z:
        prefix, manifest = _read_manifest(z)
        # check the whole archive before writing anything: names, presence and sizes
        plan = []
        total = 0
        for name, digest in manifest["files"].items():
            kind, dest = _destination(name, data_dir, web_dir)
            if kind == "web" and only_data:
                continue
            try:
                info = z.getinfo(prefix + name)
            except KeyError as err:
                raise BundleError("damaged", entry=name) from err
            total += info.file_size
            if info.file_size > MAX_FILE or total > MAX_TOTAL:
                raise BundleError("bundle_too_large", gb=MAX_TOTAL // 2**30)
            plan.append((name, digest, info, dest))
        plan.sort(key=lambda item: item[0] == LAST)
        written = skipped = 0
        for done, (name, digest, info, dest) in enumerate(plan, start=1):
            if dest.exists() and not force:
                skipped += 1
            else:
                _extract(z, info, name, digest, dest)
                written += 1
            if progress is not None:
                progress(done, len(plan))
    return {**{k: manifest.get(k) for k in ("kapacitet", "created", "level", "web")},
            "written": written, "skipped": skipped}
