"""Pack the computed data so another machine can run the interface without the 15-minute pipeline.

    python -m kapacitet bundle                 # full: every screen and live requests (~400 MB of data)
    python -m kapacitet bundle --level lite    # the published results only, no live requests (~20 MB)
    python -m kapacitet bundle --web           # also web/dist, so the receiver needs no Node
    python -m kapacitet unbundle kapacitet-data.zip

The archive holds data/... (and web/dist/... with --web) plus bundle-manifest.json, which records
the version, the level and a sha256 for every file; unbundle checks each one. The data are derived
from OpenStreetMap (ODbL) and are not published with the MIT code: share a bundle privately.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

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


class BundleError(ValueError):
    """A bundle that cannot be made or unpacked as asked, with the reason."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
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
        raise BundleError(f"unknown level {level!r}: use lite or full")
    data_dir = Path(data_dir or DATA)
    if not (data_dir / "processed" / "results" / "showcase.json").exists():
        raise BundleError(f"nothing computed in {data_dir}: run `python -m kapacitet all` first")
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
                raise BundleError("web/dist is not built: run `npm run build` in web/ first")
            for f in sorted(p for p in WEB_DIST.rglob("*") if p.is_file()):
                rel = "web/dist/" + f.relative_to(WEB_DIST).as_posix()
                manifest["files"][rel] = _sha256(f)
                z.write(f, rel)
        z.writestr(MANIFEST, json.dumps(manifest, indent=1))
    return manifest


def unpack_bundle(path: str | Path, data_dir: Path | None = None, web_dir: Path | None = None,
                  force: bool = False) -> dict:
    """Extract a bundle into the data folder (and web/dist), checking every file's sha256.

    Existing files are kept unless `force`, so unpacking never overwrites someone's own results
    by accident. Entries that would land outside the target folders are refused.
    """
    data_dir = Path(data_dir or DATA).resolve()
    web_dir = Path(web_dir or WEB_DIST).resolve()
    written = skipped = 0
    with zipfile.ZipFile(path) as z:
        try:
            manifest = json.loads(z.read(MANIFEST))
        except KeyError as err:
            raise BundleError(f"{path} is not a Kapacitet+ bundle (no {MANIFEST})") from err
        for name, digest in manifest["files"].items():
            if name.startswith("data/"):
                base, dest = data_dir, data_dir / name[len("data/"):]
            elif name.startswith("web/dist/"):
                base, dest = web_dir, web_dir / name[len("web/dist/"):]
            else:
                raise BundleError(f"unexpected entry {name!r}")
            dest = dest.resolve()
            if not dest.is_relative_to(base):
                raise BundleError(f"entry {name!r} would land outside {base}")
            if dest.exists() and not force:
                skipped += 1
                continue
            body = z.read(name)
            if hashlib.sha256(body).hexdigest() != digest:
                raise BundleError(f"{name} does not match its checksum: the bundle is damaged")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            written += 1
    return {**{k: manifest[k] for k in ("kapacitet", "created", "level", "web")},
            "written": written, "skipped": skipped}
