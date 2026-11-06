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

A crash never leaves half a record: the copy is written atomically, and each chain line is one
fsynced append (kapacitet.atomic). A line cut short by a crash during its append is a torn tail:
the reader leaves it out, `verify_chain` reports it, and the next `seal` cuts it off before it
appends. A record that must not be kept any longer (a retention period, a request to delete it) is
erased with `erase`: its copy goes, and an `erasure` record is sealed in its place, so the chain,
which holds only digests, stays whole and still says that the record existed and when it went.

Standard library only, so the engine, the API and the tests can all use it.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .atomic import append_line, write_bytes_atomic, write_json_atomic
from .paths import CONFIG, DATA, PROCESSED, RAW

_LOCK = threading.RLock()
GENESIS = "0" * 64
KINDS = ("assessment", "decision", "queue_reset", "settings", "application", "schedule", "eipack", "erasure",
         "publication", "sync", "rule_signature", "cases", "market_alternatives")
# a chain kept somewhere else than data/requests: a study's evidence/ folder (`chain_at`)
_CHAIN_AT: contextvars.ContextVar[Path | None] = contextvars.ContextVar("kapacitet_seal_chain", default=None)

# What an answer depends on, besides the engine's code: the network, the profiles behind it, the
# published results and the inputs. A change to any of these changes the fingerprint. The "study"
# root is the study folder (KAPACITET_STUDY) of a station's process, and is absent elsewhere;
# "{station}" is that station's id (the name of its data folder).
FINGERPRINT_FILES = (
    ("processed", "grid/net.json"),
    ("processed", "grid/customers.parquet"),
    ("processed", "pv/roofs.parquet"),
    ("processed", "request/doe.json"),
    ("processed", "results/meta.json"),
    ("processed", "results/showcase.json"),
    ("processed", "results/operation/index.json"),
    ("processed", "meters/hourly.parquet"),
    ("raw", "manifest.json"),
    ("config", "assumptions.toml"),
    ("config", "site.toml"),
    ("study", "study.toml"),
    ("study", "inputs/stations.csv"),
    ("study", "inputs/equipment.csv"),
    ("study", "inputs/upstream.csv"),
    ("study", "inputs/mv/{station}.csv"),
)


def folder() -> Path:
    return DATA / "requests"


def _root() -> Path:
    """Where the chain in use lives: the folder of `chain_at`, or data/requests."""
    return _CHAIN_AT.get() or folder()


@contextmanager
def chain_at(path: str | Path):
    """Seal into, and read from, the chain in `path` instead of data/requests: a study's sign-off
    is chained in the study's own evidence/ folder (study/publish.py), apart from any server's."""
    token = _CHAIN_AT.set(Path(path))
    try:
        yield Path(path)
    finally:
        _CHAIN_AT.reset(token)


def _chain_file() -> Path:
    return _root() / "seals.jsonl"


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
        rel = rel.format(station=DATA.name) if root == "study" else rel
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


def _read_chain() -> tuple[list[dict], int | None, bool]:
    """The chain's lines; the byte offset where a torn last line starts (None when there is none);
    and whether the file lacks its final newline after a whole last line.

    A last line without its newline that does not parse is torn: an append cut short. One that
    parses lost only its newline and counts. A damaged line anywhere else is kept as an empty
    entry, so the chain breaks there instead of skipping it.
    """
    path = _chain_file()
    if not path.exists():
        return [], None, False
    data = path.read_bytes()
    out: list[dict] = []
    pos = 0
    for raw in data.splitlines(keepends=True):
        start, pos = pos, pos + len(raw)
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            entry = json.loads(text)
        except ValueError:
            entry = None
        last = pos == len(data) and not raw.endswith((b"\n", b"\r"))
        if isinstance(entry, dict):
            out.append(entry)
            if last:
                return out, None, True
        elif last:
            return out, start, False
        else:
            out.append({})
    return out, None, False


def _lines() -> list[dict]:
    """Every whole line of the chain, oldest first (a torn last line left out)."""
    return _read_chain()[0]


def _mend_tail() -> list[dict]:
    """Cut off a torn last line, or put back a lost final newline, so the next append starts a line
    of its own; return the chain's lines."""
    lines, torn, unterminated = _read_chain()
    if torn is None and not unterminated:
        return lines
    with _chain_file().open("r+b") as fh:
        if torn is not None:
            fh.truncate(torn)
        else:
            fh.seek(0, os.SEEK_END)
            fh.write(b"\n")
        fh.flush()
        os.fsync(fh.fileno())
    return lines


def _chain_hash(prev: str, entry: dict) -> str:
    body = {k: entry[k] for k in ("seq", "at", "kind", "id", "digest", "fingerprint")}
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


def _sealed_path(kind: str, item_id: str) -> Path:
    return _root() / "sealed" / kind / f"{item_id}.json"


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
        if path.exists():
            if path.read_bytes() != text:
                raise ValueError(f"{kind} {item_id} is already sealed with different content")
        else:
            write_bytes_atomic(path, text)
        lines = _mend_tail()
        prev = lines[-1].get("chain", GENESIS) if lines else GENESIS
        entry = {"seq": len(lines) + 1, "at": datetime.now(timezone.utc).isoformat(), "kind": kind,
                 "id": item_id, "digest": dig, "fingerprint": fp["digest"], "prev": prev}
        entry["chain"] = _chain_hash(prev, entry)
        append_line(_chain_file(), json.dumps(entry, sort_keys=True))
        # the fingerprint's files are kept once per distinct fingerprint, so a verifier can say what changed
        fp_path = _root() / "sealed" / "fingerprints" / f"{fp['digest']}.json"
        if not fp_path.exists():
            write_json_atomic(fp_path, fp, sort_keys=True)
    return entry


def entry_for(kind: str, item_id: str) -> dict | None:
    """The latest chain entry of a record, or None if it was never sealed."""
    found = None
    for e in _lines():
        if e.get("kind") == kind and e.get("id") == item_id:
            found = e
    return found


def verify_chain() -> dict:
    """Every line follows from the one before it. `torn_tail` says that the last append was cut
    short by a crash; the lines before it still count, and the next seal cuts it off."""
    prev = GENESIS
    lines, torn, _ = _read_chain()
    for i, e in enumerate(lines, start=1):
        try:
            ok = e.get("seq") == i and e.get("prev") == prev and _chain_hash(prev, e) == e.get("chain")
        except KeyError:      # a line without the fields a chain line has
            ok = False
        if not ok:
            return {"ok": False, "length": len(lines), "broken_at": i,
                    "head": lines[-1].get("chain", GENESIS) if lines else GENESIS, "torn_tail": torn is not None}
        prev = e["chain"]
    return {"ok": True, "length": len(lines), "broken_at": None, "head": prev, "torn_tail": torn is not None}


def _erasure_id(kind: str, item_id: str) -> str:
    return f"{kind}.{item_id}"


def _erasure_of(kind: str, item_id: str) -> dict | None:
    """The erasure of a record's latest seal, or None when that seal stands."""
    entry = entry_for(kind, item_id)
    gone = entry_for("erasure", _erasure_id(kind, item_id))
    if entry is None or gone is None or gone.get("seq", 0) < entry.get("seq", 0):
        return None
    return gone


def erased(kind: str, item_id: str) -> bool:
    return _erasure_of(kind, item_id) is not None


def erase(kind: str, item_id: str, reason: str) -> dict:
    """Remove a sealed record's copy and seal its erasure: the kind, the id, the digest it had and
    why it went. The chain keeps every line, so it stays valid; `verify` then says `erased`.
    Erasing an erased record again only makes sure its copy is gone."""
    if kind not in KINDS or kind == "erasure":
        raise ValueError(f"cannot erase a seal of kind {kind!r}")
    with _LOCK:
        entry = entry_for(kind, item_id)
        if entry is None:
            raise ValueError(f"{kind} {item_id} has no seal")
        done = _erasure_of(kind, item_id)
        if done is None:
            done = seal("erasure", _erasure_id(kind, item_id),
                        {"kind": kind, "id": item_id, "digest": entry["digest"], "seq": entry["seq"],
                         "reason": str(reason)[:200]})
        _sealed_path(kind, item_id).unlink(missing_ok=True)
    return done


def verify(kind: str, item_id: str, current: Any = None) -> dict:
    """Whether a sealed record is intact: its kept copy hashes to the chained digest, the chain
    holds, and (when given) the working record still says the same as the copy."""
    entry = entry_for(kind, item_id)
    if entry is None:
        return {"sealed": False}
    chain = verify_chain()
    out = {"sealed": True, "kind": kind, "id": item_id, "digest": entry["digest"], "sealed_at": entry["at"],
           "seq": entry["seq"], "fingerprint": entry["fingerprint"],
           "chain_ok": chain["ok"], "chain_length": chain["length"], "chain_head": chain["head"], "erased": False}
    gone = _erasure_of(kind, item_id)
    if gone is not None:     # nothing left to compare: the chain says it existed and when it went
        why = sealed_copy("erasure", _erasure_id(kind, item_id)) or {}
        out.update(erased=True, erased_at=gone.get("at"), erasure_seq=gone.get("seq"), reason=why.get("reason"),
                   copy_ok=None, ok=bool(chain["ok"]))
        return out
    path = _sealed_path(kind, item_id)
    copy_ok = path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["digest"]
    out["copy_ok"] = bool(copy_ok)
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
        if e.get("kind") != kind:
            continue
        rec = sealed_copy(kind, e["id"])
        if rec is not None:
            out.append({"entry": e, "record": rec})
    return out
