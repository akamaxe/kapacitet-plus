"""The studies' signed publications over HTTP: read-only, without sign-in, and off unless asked for.

With `KAPACITET_PUBLICATIONS=1` and `KAPACITET_STUDY_DIR` naming the folder of the studies, the
server serves what `kapacitet study publish` wrote under each study's runs/<run>/publication/,
once signed: the files a DSO publishes on its own website anyway (they are public by design), so a
partner can query them and the embeddable map (embed/) can be framed straight from here.

    GET /api/v1/publications                              each study with a signed publication: its id,
                                                          the latest signed run, when and under which
                                                          method version it was signed, and its files
    GET /api/v1/publications/{study}/{run}/{file}         one file of that run's publication; `run` may
                                                          be `latest`, `file` a file of the manifest or
                                                          embed/… under it

A publication is served only while it is signed: its manifest says `signerad` with a valid signoff,
the signoff in runs/<run>/signoff.json is the one the manifest names and still verifies in the
study's own seal chain, and every file is the one the manifest lists (sha256). A draft (UTKAST), a
file outside the publication folder (`..`), a file the manifest does not list and
disclosure_report.csv (which says which station is in which cell, and is never published) are not
served. An answer carries the file's sha256 from the manifest as a strong ETag, and an
If-None-Match that names it is answered 304. Off, every route answers 404 with the code
`publications_off`, which says how to turn them on.

"Signed" is what the last `kapacitet study publish` wrote: the manifest records the signoff's
validity at that moment. Outputs recomputed after signing are not seen here until `study publish`
runs again (the study module would call the run a draft at once); the API checks the seal, the
files and that the manifest belongs to the study folder it is served from.

The routes are reads, so they have no entry in guard.ROUTE_POLICY (which lists the writes): they
are public reads (guard.PUBLIC_READS) and limited reads (guard.LIMITED_READS, bucket "public"), and
CORS follows KAPACITET_CORS_ORIGINS like every other route.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .. import seals
from ..cache import on_clear
from ..paths import inside
from ..study import runs as run_ids
from ..study import valid_id

router = APIRouter(prefix="/api/v1", tags=["publications"])

ERRORS = {
    "publications_off": "the studies' publications are not served here: set KAPACITET_PUBLICATIONS=1 and "
                        "KAPACITET_STUDY_DIR to the folder of the studies",
    "no_publication": "study {study} has no signed publication for {run}",
    "publication_file": "{file} is not a file of the publication",
}
SIGNED = "signerad"                 # study/publish.SIGNED, without importing the study's computation
MANIFEST = "manifest.json"
NEVER = frozenset({"disclosure_report.csv", "disclosure_report.machine.csv"})
TYPES = {".csv": "text/csv; charset=utf-8", ".geojson": "application/geo+json", ".json": "application/json",
         ".md": "text/markdown; charset=utf-8", ".html": "text/html; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


def refusal(status: int, code: str, **params) -> HTTPException:
    return HTTPException(status, {"code": code, "message": ERRORS[code].format(**params), "params": params})


def studies_dir() -> Path | None:
    """The folder of the studies when the publications are served, else None."""
    if os.environ.get("KAPACITET_PUBLICATIONS", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    folder = os.environ.get("KAPACITET_STUDY_DIR", "").strip()
    return Path(folder) if folder else None


def _root() -> Path:
    root = studies_dir()
    if root is None:
        raise refusal(404, "publications_off")
    return root


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


_VERIFIED: dict[tuple, dict | None] = {}
on_clear(_VERIFIED.clear)


def study_folder(root: Path, study: str) -> Path | None:
    """The folder of a study that can be served: an id that is a folder name, inside the studies'
    folder, with its study.toml. None for anything else."""
    if not valid_id(study):
        return None
    try:
        folder = inside(root, study)
    except ValueError:
        return None
    return folder if (folder / "study.toml").is_file() else None


def run_folder(study_root: Path, run: str) -> Path | None:
    """runs/<run> of a study, for a run id (a quarter or a month); None for anything else."""
    if not run_ids.valid(run):
        return None
    try:
        return inside(study_root, "runs", run)
    except ValueError:
        return None


def signed_manifest(study_root: Path, run: str) -> dict | None:
    """The manifest of runs/<run>/publication when that publication is signed (see the module's
    docstring), else None. The check (seal chain and a sha256 of every file) is remembered while
    the manifest, the signoff, the seal chain and every listed file keep their mtime and size."""
    run_dir = run_folder(study_root, run)
    if run_dir is None:
        return None
    pub = run_dir / "publication"
    try:
        listed = sorted(((json.loads((pub / MANIFEST).read_text(encoding="utf-8")).get("files")) or {}))
    except (OSError, ValueError, AttributeError):
        return None
    key = (str(study_root), run, _stamp(pub / MANIFEST), _stamp(run_dir / "signoff.json"),
           _stamp(study_root / "evidence" / "seals.jsonl"), tuple(_stamp(pub / n) for n in listed))
    if key not in _VERIFIED:
        if len(_VERIFIED) > 256:
            _VERIFIED.clear()
        _VERIFIED[key] = _check_signed(study_root, run)
    return _VERIFIED[key]


def _check_signed(study_root: Path, run: str) -> dict | None:
    run_dir = run_folder(study_root, run)
    if run_dir is None:
        return None
    pub = run_dir / "publication"
    try:
        manifest = json.loads((pub / MANIFEST).read_text(encoding="utf-8"))
        signoff = json.loads((run_dir / "signoff.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    named = manifest.get("signoff") or {}
    seal = signoff.get("seal") or {}
    if (manifest.get("status") != SIGNED or not named.get("valid") or manifest.get("quarter") != run
            or manifest.get("study") != study_root.name or signoff.get("study") != study_root.name
            or signoff.get("quarter") != run or not seal.get("id") or seal.get("id") != (named.get("seal") or {}).get("id")):
        return None
    with seals.chain_at(study_root / "evidence"):
        check = seals.verify("publication", seal["id"], current={k: v for k, v in signoff.items() if k != "seal"})
    if not check.get("ok"):
        return None
    files = manifest.get("files") or {}
    if not files or any(_digest(pub / name) != (f or {}).get("sha256") for name, f in files.items()):
        return None
    return manifest


def study_roots(root: Path) -> list[Path]:
    """The studies under the folder: subfolders with a study.toml and an id that is a folder name."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and valid_id(p.name) and (p / "study.toml").is_file())


def signed_runs(study_root: Path) -> list[tuple[str, dict]]:
    """(run, manifest) of every signed publication of a study, the latest in time first."""
    found = run_ids.listed(study_root / "runs", f"publication/{MANIFEST}")
    out = []
    for run in reversed(found):
        manifest = signed_manifest(study_root, run)
        if manifest is not None:
            out.append((run, manifest))
    return out


@router.get("/publications")
def publications() -> dict:
    """Each study with a signed publication, and its latest signed run."""
    out = []
    for study_root in study_roots(_root()):
        signed = signed_runs(study_root)
        if not signed:
            continue
        run, m = signed[0]
        out.append({"id": study_root.name, "dso": m.get("dso"), "latest_run": run,
                    "signed_at": (m.get("signoff") or {}).get("signed_at"), "published_at": m.get("published_at"),
                    "method_version": m.get("method_version"), "period": m.get("period"),
                    "files": sorted([MANIFEST, *(m.get("files") or {})]),
                    "embed": f"/api/v1/publications/{study_root.name}/{run}/embed/index.html"
                    if "embed/index.html" in (m.get("files") or {}) else None,
                    "runs": [r for r, _ in signed]})
    return {"publications": out}


def _clean(file: str) -> str | None:
    """The file's path inside the publication, or None for anything that leaves it."""
    if not file or "\\" in file or "\x00" in file or file.startswith("/"):
        return None
    parts = PurePosixPath(file).parts
    if any(p in ("", ".", "..") or ":" in p for p in parts):
        return None
    return "/".join(parts)


def _csp(manifest: dict) -> str:
    """The page's own policy: nothing outside its folder, but the tile host when the DSO named one."""
    tiles = (manifest.get("embed") or {}).get("map_tiles") or ""
    host = urlsplit(tiles).netloc if tiles.startswith("https://") else ""
    img = f"img-src 'self' https://{host}; " if host else ""
    return f"default-src 'self'; {img}base-uri 'none'; form-action 'none'"


@router.get("/publications/{study}/{run}/{file:path}")
def publication_file(study: str, run: str, file: str, request: Request) -> Response:
    """One file of a signed publication, with its sha256 as a strong ETag."""
    root = _root()
    study_root = study_folder(root, study)
    if study_root is None:
        raise refusal(404, "no_publication", study=study[:64], run=run[:16])
    if run == "latest":
        signed = signed_runs(study_root)
        if not signed:
            raise refusal(404, "no_publication", study=study, run=run)
        run, manifest = signed[0]
    elif run_ids.valid(run):
        manifest = signed_manifest(study_root, run)
        if manifest is None:
            raise refusal(404, "no_publication", study=study, run=run)
    else:
        raise refusal(404, "no_publication", study=study, run=run[:16])
    name = _clean(file)
    files = manifest.get("files") or {}
    if name is None or PurePosixPath(name).name in NEVER or (name != MANIFEST and name not in files):
        raise refusal(404, "publication_file", file=file[:120])
    try:
        # the name is one of the manifest's own (or the manifest), and still has to stay in the folder
        pub = inside(study_root, "runs", run, "publication").resolve()
        path = inside(pub, *name.split("/")).resolve()
    except ValueError:
        raise refusal(404, "publication_file", file=file[:120]) from None
    if not path.is_relative_to(pub) or not path.is_file():      # a link that leads out of the folder
        raise refusal(404, "publication_file", file=file[:120])
    body = path.read_bytes()
    sha = hashlib.sha256(body).hexdigest() if name == MANIFEST else files[name]["sha256"]
    tag = f'"{sha}"'
    headers = {"ETag": tag, "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
               "Referrer-Policy": "no-referrer"}
    if name.endswith(".html"):
        headers["Content-Security-Policy"] = _csp(manifest)
    if tag in [t.strip() for t in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=TYPES.get(path.suffix.lower(), "application/octet-stream"), headers=headers)


__all__ = ["ERRORS", "publication_file", "publications", "router", "signed_manifest", "signed_runs", "studies_dir",
           "study_roots"]
