"""HTTP access with caching and a provenance manifest (data/raw/manifest.json).

Every file in data/raw has a manifest entry with the source, request URL and parameters,
license, retrieval time and SHA-256, so every number shown in the UI can be traced back.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ..config import settings
from ..paths import RAW
from .sources import SOURCES

MANIFEST = RAW / "manifest.json"


class FetchError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_manifest() -> dict[str, dict[str, Any]]:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def save_manifest(manifest: dict[str, dict[str, Any]]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(MANIFEST)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record(entry_id: str, *, source: str, path: Path, url: str, params: dict | None = None,
           rows: int | None = None, notes: str = "") -> dict[str, Any]:
    """Add or replace a manifest entry for a file in data/raw."""
    src = SOURCES[source]
    manifest = load_manifest()
    entry = {
        "id": entry_id,
        "source": source,
        "dataset": src.name,
        "provider": src.provider,
        "license": src.license,
        "attribution": src.attribution,
        "tag": src.tag,
        "url": url,
        "params": params or {},
        "path": path.relative_to(RAW).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "fetched_at": _now(),
        "rows": rows,
        "notes": notes,
    }
    manifest[entry_id] = entry
    save_manifest(manifest)
    return entry


_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": settings().user_agent, "Accept": "application/json, text/csv, */*"})
    return _session


def http(method: str, url: str, *, params: dict | None = None, data: dict | None = None,
         timeout: float = 90, retries: int = 5, backoff: float = 3.0) -> requests.Response:
    """GET/POST with retries on transient errors (429, 5xx, timeouts)."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = session().request(method, url, params=params, data=data, timeout=timeout)
        except requests.RequestException as exc:
            last = exc
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code not in (429, 500, 502, 503, 504):
                # Client errors (404, 400, ...) will not go away by retrying.
                raise FetchError(f"HTTP {resp.status_code} from {resp.url}")
            last = FetchError(f"HTTP {resp.status_code} from {url}")
        if attempt < retries - 1:
            time.sleep(backoff * (attempt + 1))
    raise FetchError(f"{method} {url} failed after {retries} attempts: {last}")


def cached(entry_id: str, dest: Path, *, refresh: bool = False) -> bool:
    """True when the file exists and has a manifest entry (so no download is needed)."""
    return (not refresh) and dest.exists() and entry_id in load_manifest()
