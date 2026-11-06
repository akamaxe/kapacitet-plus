"""Pack the computed data so another machine can run the interface without the 15-minute pipeline.

    python -m kapacitet bundle                 # full: every screen and live requests (~400 MB of data)
    python -m kapacitet bundle --level lite    # the published results only, no live requests (~20 MB)
    python -m kapacitet bundle --web           # also web/dist, so the receiver needs no Node
    python -m kapacitet unbundle kapacitet-data.zip --sha256   # only the pinned release (below)

The archive holds data/... (and web/dist/... with --web) plus bundle-manifest.json, which records
the version, the level, a sha256 for every file and its modification time; unbundle checks each
sum and gives every file back its time, so a result unpacked elsewhere is still dated by when it
was computed (the API's provenance reads those times), not by when it was unpacked. The data are
derived from OpenStreetMap (ODbL), so a bundle is never committed with the MIT code: it is
published as the asset of the release `data`, under ODbL (DATA-LICENSE.md).

The published bundle is pinned: config/data-release.json, committed with the code, records the
release asset's url, sha256 and size (`bundle --pin` writes it). The codespace checks the download
against it before unpacking (.devcontainer/data.sh), `unbundle --sha256` does the same, and the
setup page refuses any other file unless its server allows one (KAPACITET_SETUP_ANY_BUNDLE=1).
The per-file sums inside an archive only say it arrived whole; the pinned digest says it is the
one published.

Unpacking is also what the setup page does with a bundle uploaded from the browser
(api/setup.py), so it trusts nothing in the archive: it writes only under data/processed,
data/raw and web/dist, caps how much an archive may unpack to, streams every file through its
checksum into a temporary name and renames it only when the sum matches, checks a network file
(net.json) the way loading it would before it is in place (grid/netcheck.py: pandapower builds
whatever objects such a file names), and writes showcase.json last, because the results count as
present only once it is there.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import zipfile
import zlib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import __version__
from .atomic import write_text_atomic
from .paths import CONFIG, DATA, WEB_DIST

# What the API reads to show the screens, without the live connection request.
LITE = (
    ("processed", "results"), ("processed", "ui"), ("processed", "grid"), ("processed", "validation"),
    ("processed", "pv", "roofs.parquet"), ("raw", "zones"), ("raw", "manifest.json"),
)
# Everything the pipeline wrote: the live request also needs the hourly profiles, the request
# baselines and the cached prices and weather.
FULL = (("processed",), ("raw",))
# Files a bundle never carries, by their path under data/. ENTSO-E's raw day-ahead XML stays out
# until the owner decides on its licence (the Transparency Platform's terms are not ODbL, the
# release's licence); the figures derived from it, in data/processed, may travel. A copy unpacked
# from a bundle therefore prices the live request on the elprisetjustnu years only.
EXCLUDED = (re.compile(r"raw/prices/entsoe_[^/]*$"),)
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
# The published data release this version of the code expects (committed; `bundle --pin` writes it).
RELEASE = CONFIG / "data-release.json"
RELEASE_URL = "https://github.com/akamaxe/kapacitet-plus/releases/download/data/kapacitet-data.zip"
_SHA256 = re.compile(r"[0-9a-f]{64}")


ERRORS = {
    "nothing_computed": "nothing computed in {folder}: run `python -m kapacitet all` first",
    "unknown_level": "unknown level {level}: use lite or full",
    "web_not_built": "web/dist is not built: run `npm run build` in web/ first",
    "not_a_bundle": "this file is not a Kapacitet+ data bundle",
    "damaged": "{entry} is missing or does not match its checksum: the bundle is damaged",
    "unexpected_entry": "the bundle holds {entry}, outside the folders a bundle may write",
    "bundle_too_large": "the bundle unpacks to more than {gb} GB",
    "not_pinned": "this file is not the pinned data release: its sha256 is not the one expected",
    "bad_network": "{entry} is not a network file this version can load safely",
    "secret_in_manifest": "{entry} holds the ENTSO-E token: fetch again with this version, which never writes it",
}
# What unpacking can refuse a bundle for: the setup page shows these, so each has its words in the
# interface (err.<code>, tests/test_messages.py).
UNPACK_ERRORS = ("not_a_bundle", "damaged", "unexpected_entry", "bundle_too_large", "not_pinned", "bad_network")


class BundleError(ValueError):
    """A bundle that cannot be made or unpacked as asked: a code the interface translates."""

    def __init__(self, code: str, **params):
        super().__init__(ERRORS[code].format(**params))
        self.code = code
        self.params = params


def file_sha256(path: str | Path) -> str:
    """The sha256 of a whole file, as `sha256sum` prints it."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def pinned_release(path: str | Path | None = None) -> dict | None:
    """The data release this code expects ({url, sha256, size, created, ...}), or None when the
    file is missing or holds no valid digest."""
    try:
        rel = json.loads(Path(path or RELEASE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rel, dict) or not isinstance(rel.get("sha256"), str) or not _SHA256.fullmatch(rel["sha256"]):
        return None
    return rel


def pin_release(archive: str | Path, manifest: dict, url: str | None = None, path: str | Path | None = None) -> dict:
    """Record an archive as the published data release: its digest and size, and when and by which
    version it was made. The url stays the one already pinned unless another is given."""
    archive = Path(archive)
    current = pinned_release(path) or {}
    rel = {"url": url or current.get("url") or RELEASE_URL, "sha256": file_sha256(archive),
           "size": archive.stat().st_size, "created": manifest.get("created"),
           "kapacitet": manifest.get("kapacitet"), "level": manifest.get("level")}
    # a committed file: it ends with a newline
    write_text_atomic(Path(path or RELEASE), json.dumps(rel, indent=1) + "\n")
    return rel


def excluded(rel: str) -> bool:
    """Whether a path under data/ (posix, e.g. raw/prices/entsoe_SE3_2019.xml) may never be bundled."""
    return any(p.search(rel) for p in EXCLUDED)


def _files(data_dir: Path, level: str):
    parts = LITE if level == "lite" else FULL
    for p in parts:
        root = data_dir.joinpath(*p)
        found = [root] if root.is_file() else sorted(f for f in root.rglob("*") if f.is_file()) if root.is_dir() else []
        yield from (f for f in found if not excluded(f.relative_to(data_dir).as_posix()))


def _secret_free(manifest: Path) -> None:
    """The download manifest travels in every bundle: refuse one that holds the ENTSO-E token (record()
    writes *** in its place, so this only trips on a manifest written by hand or by older code)."""
    if not manifest.is_file():
        return
    from .ingest.prices import entsoe_token

    tok = entsoe_token()
    if tok and tok in manifest.read_text(encoding="utf-8", errors="replace"):
        raise BundleError("secret_in_manifest", entry="data/raw/manifest.json")


def make_bundle(out: str | Path, level: str = "full", web: bool = False, data_dir: Path | None = None) -> dict:
    """Write the archive; return its manifest."""
    if level not in ("lite", "full"):
        raise BundleError("unknown_level", level=level)
    data_dir = Path(data_dir or DATA)
    if not (data_dir / "processed" / "results" / "showcase.json").exists():
        raise BundleError("nothing_computed", folder=str(data_dir))
    out = Path(out)
    manifest: dict = {"kapacitet": __version__, "created": datetime.now(timezone.utc).isoformat(),
                      "level": level, "web": web, "files": {}, "mtimes": {}}

    def add(z: zipfile.ZipFile, f: Path, rel: str) -> None:
        manifest["files"][rel] = file_sha256(f)
        manifest["mtimes"][rel] = round(f.stat().st_mtime, 3)
        z.write(f, rel)

    _secret_free(data_dir / "raw" / "manifest.json")     # before a byte of the archive is written
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in _files(data_dir, level):
            add(z, f, "data/" + f.relative_to(data_dir).as_posix())
        if web:
            if not (WEB_DIST / "index.html").exists():
                raise BundleError("web_not_built")
            for f in sorted(p for p in WEB_DIST.rglob("*") if p.is_file()):
                add(z, f, "web/dist/" + f.relative_to(WEB_DIST).as_posix())
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


# Modification times outside this range are not believed (a hostile manifest, a broken clock).
_MTIME_RANGE = (0.0, 2.0**33)


def _mtime(manifest: dict, name: str, info: zipfile.ZipInfo) -> float | None:
    """When the file was last written where the bundle was made: the manifest's time, or, for a
    bundle made before it recorded them, the archive's own entry time (local time, to 2 s)."""
    mtimes = manifest.get("mtimes")
    value = mtimes.get(name) if isinstance(mtimes, dict) else None
    finite = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if finite and _MTIME_RANGE[0] < value < _MTIME_RANGE[1]:
        return float(value)
    try:
        return time.mktime(info.date_time + (0, 0, -1))
    except (OverflowError, ValueError):
        return None


# A pandapower network: loading one builds whatever objects it names, so it is checked on the way in.
NET_FILE = "net.json"


def _is_network(name: str) -> bool:
    return name.startswith("data/") and PurePosixPath(name).name == NET_FILE


def _check_network(path: Path, name: str) -> None:
    """Refuse a net.json that loading would refuse, while the bundle can still say so: a copy that
    takes it would open without a network, and its setup page would already be closed."""
    from .grid.netcheck import GridFileError, check_net_file

    try:
        check_net_file(path)
    except (GridFileError, OSError) as err:
        raise BundleError("bad_network", entry=name) from err


def _extract(z: zipfile.ZipFile, info: zipfile.ZipInfo, name: str, digest: str, dest: Path,
             mtime: float | None = None) -> None:
    """Stream one entry through its checksum into a temporary file, give it its modification time,
    then put it in place."""
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
        if _is_network(name):
            _check_network(tmp, name)
        if mtime is not None:
            try:
                os.utime(tmp, (mtime, mtime))
            except (OSError, OverflowError, ValueError):
                pass        # a file system that keeps no times: the file still counts
        os.replace(tmp, dest)
    except (zipfile.BadZipFile, zlib.error, EOFError) as err:
        raise BundleError("damaged", entry=name) from err
    finally:
        if tmp.exists():
            tmp.unlink()


def unpack_bundle(path: str | Path, data_dir: Path | None = None, web_dir: Path | None = None,
                  force: bool = False, *, only_data: bool = False,
                  progress: Callable[[int, int], None] | None = None,
                  expected_sha256: str | None = None) -> dict:
    """Extract a bundle into the data folder (and web/dist), checking every file's sha256.

    Existing files are kept unless `force`, so unpacking never overwrites someone's own results
    by accident. `only_data` leaves out the archive's web/dist (a server that builds its own
    interface). `progress(done, total)` is called after each file. With `expected_sha256`, the
    whole archive must have that digest (the pinned release's) before anything is read from it.
    """
    data_dir = Path(data_dir or DATA).resolve()
    web_dir = Path(web_dir or WEB_DIST).resolve()
    if expected_sha256 is not None:
        try:
            actual = file_sha256(path)
        except OSError as err:
            raise BundleError("not_a_bundle") from err
        if actual != expected_sha256.strip().lower():
            raise BundleError("not_pinned", sha256=actual, expected=expected_sha256.strip().lower())
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
            if excluded(name.removeprefix("data/")):  # an older or hand-made archive: never unpack these
                continue
            try:
                info = z.getinfo(prefix + name)
            except KeyError as err:
                raise BundleError("damaged", entry=name) from err
            total += info.file_size
            if info.file_size > MAX_FILE or total > MAX_TOTAL:
                raise BundleError("bundle_too_large", gb=MAX_TOTAL // 2**30)
            plan.append((name, digest, info, dest))
        # a network file first, so a refused one leaves nothing written; showcase.json last
        plan.sort(key=lambda item: (item[0] == LAST, not _is_network(item[0])))
        written = skipped = 0
        for done, (name, digest, info, dest) in enumerate(plan, start=1):
            if dest.exists() and not force:
                skipped += 1
            else:
                _extract(z, info, name, digest, dest, _mtime(manifest, name, info))
                written += 1
            if progress is not None:
                progress(done, len(plan))
    return {**{k: manifest.get(k) for k in ("kapacitet", "created", "level", "web")},
            "written": written, "skipped": skipped}
