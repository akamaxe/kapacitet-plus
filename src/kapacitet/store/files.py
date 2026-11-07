"""The files backend: the server's state under data/requests/, as it has always been kept.

Each class is bound to the file or folder its module names (records.folder(), inbox.log_path(),
market.log_path(), dso.path()), so moving that one name moves the state. Writes are atomic
(kapacitet.atomic) and `locked()` is a lock per file within this process: one server writes a data
folder at a time (records.claim_server_lock). The queue's and the seal chain's file backends live
beside their logic (engine/queue.FolderQueue, seals.FolderChain).
"""

from __future__ import annotations

import gzip
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..atomic import append_line, gzip_json_atomic, write_text_atomic

_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(path))
    with _GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def parse_lines(lines: list[str]) -> list[Any]:
    """The lines that parse as JSON (a line cut short by a crash is left out)."""
    out = []
    for raw in lines:
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


class Records:
    """Saved assessments: `<id>.json.gz` (gzipped JSON, same bytes for the same record) and one
    line per assessment in `index.jsonl`."""

    backend = "files"

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json.gz"

    @contextmanager
    def locked(self) -> Iterator[None]:
        with _lock_for(self.root / "index.jsonl"):
            yield

    def put(self, job_id: str, kept: dict, line: dict) -> None:
        gzip_json_atomic(self._path(job_id), kept)
        with self.locked():
            append_line(self.root / "index.jsonl", json.dumps(line))

    def get(self, job_id: str) -> dict | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)

    def exists(self, job_id: str) -> bool:
        return self._path(job_id).exists()

    def index(self) -> list[Any]:
        """The index lines that parse, oldest first."""
        with self.locked():
            lines = read_lines(self.root / "index.jsonl")
        return parse_lines(lines)

    def stored(self) -> dict[str, datetime]:
        """{id: the time its file was last written} for every record kept (a fallback for an index
        line that does not say when)."""
        out = {}
        if not self.root.exists():
            return out
        for path in sorted(self.root.glob("*.json.gz")):
            out[path.name[:-len(".json.gz")]] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        return out

    def delete(self, ids: set[str]) -> None:
        for job_id in ids:
            self._path(job_id).unlink(missing_ok=True)
        with self.locked():
            index = self.root / "index.jsonl"
            if not index.exists():
                return
            left = []
            for raw in read_lines(index):
                try:
                    item = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("id") not in ids:
                    left.append(raw)
            write_text_atomic(index, "".join(line + "\n" for line in left))


class EventLog:
    """An append-only log of JSON lines (applications.jsonl, market_alternatives.jsonl)."""

    backend = "files"

    def __init__(self, path: Path, name: str):
        self.path = Path(path)
        self.name = name

    @contextmanager
    def locked(self) -> Iterator[None]:
        with _lock_for(self.path):
            yield

    def entries(self) -> list[Any]:
        return parse_lines(read_lines(self.path))

    def next_seq(self) -> int:
        """The next line's number (call it under `locked()`): one more than the lines there are."""
        return len(read_lines(self.path)) + 1

    def append(self, obj: dict, text: str) -> None:
        """Append `text`, the line `obj` is kept as."""
        with self.locked():
            append_line(self.path, text)


class Document:
    """One JSON document replaced whole (settings.json)."""

    backend = "files"

    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def locked(self) -> Iterator[None]:
        with _lock_for(self.path):
            yield

    def get(self) -> Any | None:
        """The document, None when there is none; ValueError when it does not parse."""
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def put(self, obj: Any) -> None:
        write_text_atomic(self.path, json.dumps(obj, indent=1))
