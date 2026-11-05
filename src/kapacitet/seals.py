"""Seals: every answer and every decision, made tamper-evident.

A DSO that limits a house's export has to show later what it decided and on what grounds. When an
assessment, a queue decision, an emptying of the queue or a change of the DSO's settings is kept,
it is sealed here:

- its canonical JSON (keys sorted, fixed separators, non-finite numbers written as null) is hashed
  with SHA-256, and that exact text is stored as an immutable copy in
  `data/requests/sealed/<kind>/<id>.json`;
- a line is appended to `data/requests/seals.jsonl` with the digest, the fingerprint of the model
  and data it was computed with, and a chain hash over the previous line, so removing, reordering
  or editing any line breaks every line after it.

This is tamper-evident, not tamper-proof: whoever can write the folder can rewrite the whole chain.
Writing the chain head somewhere else (a printed record, an e-mail to the regulator) is what
anchors it. The canonical form is this module's, not RFC 8785.

Standard library only, so the engine, the API and the tests can all use it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .paths import CONFIG, DATA, PROCESSED, RAW

_LOCK = threading.Lock()
GENESIS = "0" * 64
KINDS = ("assessment", "decision", "queue_reset", "settings", "application", "schedule", "eipack")

# What an answer depends on, besides the engine's code: the network, the profiles behind it, the
# published results and the inputs. A change to any of these changes the fingerprint. The "study"
# root is the study folder (KAPACITET_STUDY) of a station's process, and is absent elsewhere.
FINGERPRINT_FILES = (
    ("processed", "grid/net.json"),
    ("processed", "grid/customers.parquet"),
    ("processed", "pv/roofs.parquet"),
    ("processed", "request/doe.json"),
    ("processed", "results/meta.json"),
    ("processed", "results/showcase.json"),
    ("processed", "results/operation/index.json"),
    ("raw", "manifest.json"),
    ("config", "assumptions.toml"),
    ("config", "site.toml"),
    ("study", "study.toml"),
    ("study", "inputs/stations.csv"),
    ("study", "inputs/equipment.csv"),
    ("study", "inputs/upstream.csv"),
)


def folder() -> Path:
    return DATA / "requests"


def _chain_file() -> Path:
    return folder() / "seals.jsonl"


def _clean(value: Any) -> Any:
    """The record as JSON can hold it: non-finite numbers become null, tuples become lists."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def canonical(obj: Any) -> bytes:
    """The one text a record is hashed as."""
    return json.dumps(_clean(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


_FILE_CACHE: dict[str, tuple[int, int, str]] = {}


def fingerprint() -> dict:
    """The engine version and a hash of each input file there is, cached by size and mtime."""
    roots = {"processed": PROCESSED, "raw": RAW, "config": CONFIG}
    if os.environ.get("KAPACITET_STUDY"):
        roots["study"] = Path(os.environ["KAPACITET_STUDY"])
    files: dict[str, str] = {}
    for root, rel in FINGERPRINT_FILES:
        if root not in roots:
            continue
        path = roots[root] / rel
        if not path.exists():
            continue
        st = path.stat()
        key = str(path)
        cached = _FILE_CACHE.get(key)
        if cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
            files[f"{root}/{rel}"] = cached[2]
            continue
        sha = _sha256_file(path)
        _FILE_CACHE[key] = (st.st_size, st.st_mtime_ns, sha)
        files[f"{root}/{rel}"] = sha
    combined = hashlib.sha256("\n".join(f"{k}:{v}" for k, v in sorted(files.items())).encode()).hexdigest()
    return {"engine": __version__, "files": files, "digest": combined}


def _lines() -> list[dict]:
    path = _chain_file()
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            out.append(json.loads(raw))
    return out


def _chain_hash(prev: str, entry: dict) -> str:
    body = {k: entry[k] for k in ("seq", "at", "kind", "id", "digest", "fingerprint")}
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


def _sealed_path(kind: str, item_id: str) -> Path:
    return folder() / "sealed" / kind / f"{item_id}.json"


def seal(kind: str, item_id: str, obj: Any) -> dict:
    """Seal one record: keep its canonical text and chain its digest. Returns the chain entry."""
    if kind not in KINDS:
        raise ValueError(f"unknown seal kind {kind!r}")
    if not item_id or "/" in item_id or "\\" in item_id or item_id.startswith("."):
        raise ValueError(f"bad seal id {item_id!r}")
    text = canonical(obj)
    dig = hashlib.sha256(text).hexdigest()
    fp = fingerprint()
    with _LOCK:
        path = _sealed_path(kind, item_id)
        if path.exists() and path.read_bytes() != text:
            raise ValueError(f"{kind} {item_id} is already sealed with different content")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text)
        lines = _lines()
        prev = lines[-1]["chain"] if lines else GENESIS
        entry = {"seq": len(lines) + 1, "at": datetime.now(timezone.utc).isoformat(), "kind": kind,
                 "id": item_id, "digest": dig, "fingerprint": fp["digest"], "prev": prev}
        entry["chain"] = _chain_hash(prev, entry)
        with _chain_file().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    # the fingerprint's files are kept once per distinct fingerprint, so a verifier can say what changed
    fp_path = folder() / "sealed" / "fingerprints" / f"{fp['digest']}.json"
    if not fp_path.exists():
        fp_path.parent.mkdir(parents=True, exist_ok=True)
        fp_path.write_text(json.dumps(fp, sort_keys=True, indent=1), encoding="utf-8")
    return entry


def entry_for(kind: str, item_id: str) -> dict | None:
    """The latest chain entry of a record, or None if it was never sealed."""
    found = None
    for e in _lines():
        if e["kind"] == kind and e["id"] == item_id:
            found = e
    return found


def verify_chain() -> dict:
    """Every line follows from the one before it."""
    prev = GENESIS
    lines = _lines()
    for i, e in enumerate(lines, start=1):
        if e.get("seq") != i or e.get("prev") != prev or _chain_hash(prev, e) != e.get("chain"):
            return {"ok": False, "length": len(lines), "broken_at": i, "head": lines[-1]["chain"] if lines else GENESIS}
        prev = e["chain"]
    return {"ok": True, "length": len(lines), "broken_at": None, "head": prev}


def verify(kind: str, item_id: str, current: Any = None) -> dict:
    """Whether a sealed record is intact: its kept copy hashes to the chained digest, the chain
    holds, and (when given) the working record still says the same as the copy."""
    entry = entry_for(kind, item_id)
    if entry is None:
        return {"sealed": False}
    path = _sealed_path(kind, item_id)
    copy_ok = path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["digest"]
    chain = verify_chain()
    out = {"sealed": True, "kind": kind, "id": item_id, "digest": entry["digest"], "sealed_at": entry["at"],
           "seq": entry["seq"], "fingerprint": entry["fingerprint"], "copy_ok": bool(copy_ok),
           "chain_ok": chain["ok"], "chain_length": chain["length"], "chain_head": chain["head"]}
    if current is not None:
        out["record_ok"] = digest(current) == entry["digest"]
    out["ok"] = bool(copy_ok and chain["ok"] and out.get("record_ok", True))
    return out


def sealed_copy(kind: str, item_id: str) -> Any | None:
    path = _sealed_path(kind, item_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def sealed_of_kind(kind: str) -> list[dict]:
    """Every sealed record of a kind with its chain entry, oldest first."""
    out = []
    for e in _lines():
        if e["kind"] != kind:
            continue
        rec = sealed_copy(kind, e["id"])
        if rec is not None:
            out.append({"entry": e, "record": rec})
    return out
