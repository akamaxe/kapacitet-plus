"""Saved connection assessments: every finished evaluation is kept on disk as evidence.

A DSO decision has to be traceable later, so each answer the engine gives is written to
`data/requests/<id>.json.gz` with the request, the full result, the engine version and the run
it was computed against, and a one-line summary is appended to `data/requests/index.jsonl`.
This folder is where the API keeps what it writes; everything it reads lives in data/processed.
The two other places it writes are data/datasets/ (imported networks, api/datasets.py) and, only
on a copy that has no results yet, the bundle uploaded from the setup page (api/setup.py).

No address is written: a house is kept by its number (`cid`), and its address is read from the
network when a record is read (`address_of`), as the portal's list of houses does. What is sealed
is the record as kept, so the evidence holds no address either. The writes are atomic
(kapacitet.atomic): a crash leaves the old file or the new one, and an index line whole or absent.

With KAPACITET_RETENTION_DAYS set, assessments older than that are purged once a day (`purge`,
also `kapacitet purge --older-than N`), except those a decision or an open application rests on.

With KAPACITET_DATABASE_URL set, the records and their index are rows of the database instead
(kapacitet.store); everything else here is the same.

One server writes a data folder at a time: it holds `data/requests/server.lock` while it runs (an
OS lock, procs.hold, so a server that crashed leaves nothing to clean up), and `kapacitet purge`
takes the same lock.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import procs, seals, store
from ..paths import DATA

log = logging.getLogger("kapacitet.records")

_ID = re.compile(r"[0-9a-f]{12}")
SERVER_LOCK = "server.lock"


def folder() -> Path:
    return DATA / "requests"


def _store():
    """Where the records are kept: this folder, or the database (kapacitet.store)."""
    return store.records(folder())


def valid_id(job_id: str) -> bool:
    return bool(_ID.fullmatch(job_id))


# ---------------------------------------------------------------- addresses, read and never kept

def address_of(cid: Any) -> str | None:
    """A house's address from the network in use, or None (no network built, an unknown house)."""
    if cid is None:
        return None
    try:
        from ..engine.scenario import customers

        value = customers().loc[int(cid), "address"]
    except Exception:  # noqa: BLE001 - an address is a convenience of the reader, never required
        return None
    return value if isinstance(value, str) and value else None


def redact(record: dict[str, Any]) -> dict[str, Any]:
    """The record as it is kept and sealed: everything but the house's address."""
    res = record.get("result")
    if not isinstance(res, dict) or not isinstance(res.get("customer"), dict) or "address" not in res["customer"]:
        return record
    customer = {k: v for k, v in res["customer"].items() if k != "address"}
    return {**record, "result": {**res, "customer": customer}}


def _with_address(record: dict[str, Any]) -> dict[str, Any]:
    res = record.get("result")
    if not isinstance(res, dict):
        return record
    customer = res.get("customer") if isinstance(res.get("customer"), dict) else {}
    if customer.get("address"):
        return record                      # kept so before the addresses were left out
    cid = customer.get("cid", (record.get("request") or {}).get("cid"))
    return {**record, "result": {**res, "customer": {**customer, "address": address_of(cid)}}}


# ---------------------------------------------------------------- what is kept

def json_default(o):
    """numpy's scalars and arrays as JSON (a non-finite float as null)."""
    import numpy as np

    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


def make_record(job_id: str, created: float, request: dict, result: dict, provenance: dict | None = None) -> dict:
    """What is kept of an assessment: the question, the full answer and what it was computed with.
    The API passes the provenance of its grid (api/main.make_record); a study's case check passes
    its station's (study/cases.py)."""
    from .. import __version__

    return {"id": job_id, "created": datetime.fromtimestamp(created, timezone.utc).isoformat(),
            "request": request, "result": json.loads(json.dumps(result, default=json_default)),
            "version": __version__, "provenance": provenance or {}}


# ---------------------------------------------------------------- writing and reading

def save(record: dict[str, Any]) -> None:
    """Write one finished assessment (already JSON-safe) without its address, index it and seal it."""
    job_id = record["id"]
    kept = redact(record)
    res = kept["result"]
    line = {"id": job_id, "created": kept["created"], "request": kept["request"], "summary": res.get("summary", {}),
            "seconds": res.get("seconds")}
    _store().put(job_id, kept, line)
    # kept as evidence: its canonical text sealed and chained (seals.py)
    seals.seal("assessment", job_id, kept)


def load(job_id: str, resolve: bool = True) -> dict[str, Any] | None:
    """A saved assessment; with `resolve`, its address read from the network (as kept otherwise:
    what its seal covers)."""
    if not valid_id(job_id):
        return None
    rec = _store().get(job_id)
    if rec is None:
        return None
    return _with_address(rec) if resolve else rec


def _index_lines() -> list[dict[str, Any]]:
    """The index, oldest first (a line cut short by a crash before the appends were whole is left out)."""
    return [item for item in _store().index() if isinstance(item, dict) and valid_id(str(item.get("id", "")))]


def listing(limit: int = 50) -> list[dict[str, Any]]:
    """The most recent assessments first, skipping any whose record has been removed."""
    out: list[dict[str, Any]] = []
    st = _store()
    for item in reversed(_index_lines()):
        if st.exists(item["id"]):
            out.append({**item, "address": item.get("address") or address_of((item.get("request") or {}).get("cid"))})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- retention

def retention_days() -> int | None:
    """KAPACITET_RETENTION_DAYS as a whole number of days, or None when retention is off."""
    raw = os.environ.get("KAPACITET_RETENTION_DAYS", "").strip()
    if not raw:
        return None
    try:
        days = int(raw)
    except ValueError:
        log.warning("KAPACITET_RETENTION_DAYS=%r is not a whole number of days: retention is off", raw)
        return None
    return days if days > 0 else None


def _kept_for_decisions() -> set[str]:
    """Assessments a decision in the queue or an application still waiting rests on."""
    from ..engine import queue as qu
    from . import inbox

    keep = {it.get("assessment") for it in qu.load()["items"]}
    for app in inbox.applications().values():
        if app["status"] in ("pending", "review"):
            keep.update(app.get("assessments") or [app.get("assessment")])
    return {k for k in keep if k}


def purge(older_than_days: int, now: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Remove the assessments saved more than `older_than_days` ago: the working copy, its index
    line and its sealed copy, whose seal is erased in the chain (seals.erase keeps the digest and
    the chain valid). Those a queue decision or an application still waiting rests on are kept."""
    if older_than_days < 0:
        raise ValueError("older_than_days must not be negative")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=older_than_days)
    st = _store()
    created = {item["id"]: item.get("created") for item in _index_lines()}
    keep = _kept_for_decisions()
    old: list[str] = []
    for job_id, written in st.stored().items():
        if not valid_id(job_id) or job_id in keep:
            continue
        try:
            when = datetime.fromisoformat(created[job_id]) if created.get(job_id) else None
        except ValueError:
            when = None
        if when is None:
            when = written
        if when is None:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < cutoff:
            old.append(job_id)
    out = {"older_than_days": older_than_days, "cutoff": cutoff.isoformat(), "assessments": len(old),
           "ids": old, "kept_for_decisions": len(keep), "dry_run": dry_run}
    if dry_run or not old:
        return out
    st.delete(set(old))
    for job_id in old:
        if seals.entry_for("assessment", job_id) is not None and not seals.erased("assessment", job_id):
            seals.erase("assessment", job_id, f"retention: older than {older_than_days} days")
    return out


# ---------------------------------------------------------------- one writer per folder

def server_lock_path() -> Path:
    return folder() / SERVER_LOCK


class FolderInUse(RuntimeError):
    """Another live process writes this data folder."""

    def __init__(self, pid: int, where: Path):
        who = f"another Kapacitet+ process (PID {pid})" if pid > 0 else "another Kapacitet+ process"
        super().__init__(f"{who} writes {where}: stop it first, or point KAPACITET_DATA_DIR at another folder")
        self.pid = pid


def claim_server_lock(wait_s: float = 2.0) -> Path | None:
    """Hold data/requests/server.lock for this process (procs.hold). Returns the lock to release, or
    None when the folder cannot be written (a read-only copy writes nothing anyway). Raises
    FolderInUse when another live process still holds it after `wait_s` (the OS may take a moment
    to let go of a lock whose process has just ended). A second claim by this process (two apps in
    one process, as in the tests) holds it again and needs its own release."""
    path = server_lock_path()
    deadline = time.monotonic() + wait_s
    while True:
        try:
            holder = procs.hold(path)
        except OSError as exc:
            log.warning("could not take %s (%s): no other server is kept from writing this folder", path, exc)
            return None
        if holder is None:
            return path
        if time.monotonic() >= deadline:
            raise FolderInUse(holder, folder())
        time.sleep(0.1)


def release_server_lock(path: Path | None) -> None:
    if path is not None:
        procs.let_go(path)
