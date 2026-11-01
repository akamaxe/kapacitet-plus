"""Saved connection assessments: every finished evaluation is kept on disk as evidence.

A DSO decision has to be traceable later, so each answer the engine gives is written to
`data/requests/<id>.json.gz` with the request, the full result, the engine version and the run
it was computed against, and a one-line summary is appended to `data/requests/index.jsonl`.
This folder is the only place the API writes; everything it reads lives in data/processed.
"""

from __future__ import annotations

import gzip
import json
import re
import threading
from pathlib import Path
from typing import Any

from ..paths import DATA

_ID = re.compile(r"[0-9a-f]{12}")
_LOCK = threading.Lock()


def folder() -> Path:
    return DATA / "requests"


def valid_id(job_id: str) -> bool:
    return bool(_ID.fullmatch(job_id))


def save(record: dict[str, Any]) -> None:
    """Write one finished assessment (already JSON-safe) and index it."""
    root = folder()
    root.mkdir(parents=True, exist_ok=True)
    job_id = record["id"]
    with gzip.open(root / f"{job_id}.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(record, fh)
    res = record["result"]
    line = {"id": job_id, "created": record["created"], "request": record["request"],
            "address": res.get("customer", {}).get("address"), "summary": res.get("summary", {}),
            "seconds": res.get("seconds")}
    with _LOCK, (root / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def load(job_id: str) -> dict[str, Any] | None:
    if not valid_id(job_id):
        return None
    path = folder() / f"{job_id}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def listing(limit: int = 50) -> list[dict[str, Any]]:
    """The most recent assessments first, skipping any whose record has been removed."""
    index = folder() / "index.jsonl"
    if not index.exists():
        return []
    out: list[dict[str, Any]] = []
    with _LOCK:
        lines = index.read_text(encoding="utf-8").splitlines()
    for raw in reversed(lines):
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if (folder() / f"{item['id']}.json.gz").exists():
            out.append(item)
        if len(out) >= limit:
            break
    return out
