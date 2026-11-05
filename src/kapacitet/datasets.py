"""Imported networks: each one a data folder of its own, built by its own process.

    data/datasets/<id>/
        dataset.json   what it is and how its build went, step by step (written atomically)
        import/        the operator's four CSV files, as validated
        processed/     what the engine computed for it (KAPACITET_DATA_DIR points here)
        requests/      what the API writes while it is in use
        build.log      the build process's output
        build.lock     the PID of a build in progress
    data/active.json   which network the server uses: "reference" or an id

A build runs as `python -m kapacitet dataset build <id>`, separate from the server, with
KAPACITET_DATA_DIR on the network's folder and KAPACITET_RAW_DIR on the reference network's
downloads (weather and prices are the same), and the reference network's household profiles
copied in. The server uses one network at a time and switches only when it runs under the
supervisor (`kapacitet serve`), which restarts it on the new folder: no cache survives a switch.

The record, the lock, the log and the step runner are procs.py's, shared with a study's stations.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from . import procs
from .paths import DATASETS, HOME, RAW, ROOT

REFERENCE = "reference"
RESTART = 75                       # the exit code that asks the supervisor for a restart
STEPS = ("import", "households", "profiles", "compute")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")


class DatasetError(ValueError):
    """A request about a network that cannot be honoured: a code the interface translates."""

    def __init__(self, code: str, **params):
        super().__init__(ERRORS[code].format(**params))
        self.code = code
        self.params = params


ERRORS = {
    "unknown_dataset": "unknown network {id}",
    "not_usable": "the export does not pass the check, so it cannot be built",
    "building": "network {id} is being built",
    "not_ready": "network {id} is not ready: its build has not finished",
    "in_use": "network {id} is in use; switch to another one first",
    "not_supervised": "the server was not started with `kapacitet serve`, so it cannot switch networks",
    "jobs_running": "an evaluation or a decision is running; switch when it has finished",
}


_now = procs.now
_write_json = procs.write_json
_alive = procs.alive


def folder(ds_id: str) -> Path:
    if not _ID.match(ds_id or ""):
        raise DatasetError("unknown_dataset", id=ds_id)
    return DATASETS / ds_id


def record(ds_id: str) -> dict:
    path = folder(ds_id) / "dataset.json"
    if not path.exists():
        raise DatasetError("unknown_dataset", id=ds_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _save(rec: dict) -> dict:
    _write_json(folder(rec["id"]) / "dataset.json", rec)
    return rec


def listing() -> list[dict]:
    """The imported networks, newest first, each with its build's state."""
    if not DATASETS.exists():
        return []
    out = []
    for path in DATASETS.glob("*/dataset.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        rec["building"] = building(rec["id"])
        out.append(rec)
    return sorted(out, key=lambda r: r.get("created", ""), reverse=True)


# ---------------------------------------------------------------- which network is in use

def active() -> str:
    path = HOME / "active.json"
    if not path.exists():
        return REFERENCE
    try:
        ds = json.loads(path.read_text(encoding="utf-8")).get("dataset", REFERENCE)
    except ValueError:
        return REFERENCE
    return ds if ds == REFERENCE or (DATASETS / ds / "dataset.json").exists() else REFERENCE


def set_active(ds_id: str) -> None:
    if ds_id != REFERENCE:
        rec = record(ds_id)
        if rec.get("status") != "ready":
            raise DatasetError("not_ready", id=ds_id)
    _write_json(HOME / "active.json", {"dataset": ds_id, "since": _now()})


def child_env(ds_id: str | None = None) -> dict[str, str]:
    """The environment the server runs with for a network: its data folder and the shared downloads."""
    ds_id = ds_id or active()
    if ds_id == REFERENCE:
        return procs.child_env(home=HOME, dataset=ds_id)
    return procs.child_env(folder(ds_id), home=HOME, raw=RAW, dataset=ds_id)


def current() -> str:
    """The network this process runs on."""
    return os.environ.get("KAPACITET_DATASET", REFERENCE)


def restart_soon(delay: float = 0.5) -> None:
    """Hand the server back to its supervisor in a moment (after the answer has gone out), which
    starts it again: on another network after a switch, on new data after a bundle was loaded."""
    threading.Timer(delay, lambda: os._exit(RESTART)).start()


# ---------------------------------------------------------------- creating one

def create(name: str, files: dict[str, str], quick: bool = True) -> dict:
    """Validate an export and keep it as a new network, ready to build. Nothing is written for an
    export that does not pass the check."""
    import io

    import pandas as pd

    from .importer.validate import validate

    data = {k: pd.read_csv(io.StringIO(v)) for k, v in files.items()}
    report = validate(data)
    if not report.usable:
        raise DatasetError("not_usable")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "network"
    ds_id = f"{slug}-{secrets.token_hex(3)}"
    root = folder(ds_id)
    (root / "import").mkdir(parents=True, exist_ok=False)
    for table, text in files.items():
        (root / "import" / f"{table}.csv").write_text(text, encoding="utf-8")
    rec = {"id": ds_id, "name": name.strip()[:80] or ds_id, "created": _now(), "status": "uploaded", "quick": quick,
           "report": report.to_dict(), "customers": int(len(data["customers"])),
           "steps": [{"name": s, "status": "waiting"} for s in STEPS]}
    return _save(rec)


def delete(ds_id: str) -> None:
    rec = record(ds_id)
    if building(ds_id):
        raise DatasetError("building", id=ds_id)
    if ds_id in (active(), current()):
        raise DatasetError("in_use", id=ds_id)
    shutil.rmtree(folder(rec["id"]))


# ---------------------------------------------------------------- building one

def _lock(ds_id: str) -> Path:
    return procs.lock_path(folder(ds_id))


def building(ds_id: str) -> bool:
    """A build holds the lock and its process is alive (a stale lock does not count)."""
    return procs.building(folder(ds_id))


def launch(ds_id: str, workers: int = 2) -> int:
    """Start the build in its own process, detached from the server, and return its PID."""
    rec = record(ds_id)
    if building(ds_id):
        raise DatasetError("building", id=ds_id)
    root = folder(ds_id)
    env = {**os.environ, **child_env(ds_id), "PYTHONUNBUFFERED": "1"}
    env.pop("KAPACITET_SUPERVISED", None)
    cmd = [sys.executable, "-m", "kapacitet", "dataset", "build", ds_id, "--workers", str(workers)]
    if rec.get("quick"):
        cmd.append("--quick")
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    log = open(root / procs.LOG, "ab")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **kwargs)
    log.close()
    rec.update(status="building", launched=_now(), pid=proc.pid)
    _save(rec)
    return proc.pid


def _copy_households(dest_processed: Path) -> int:
    """The reference network's household profile pool, linked where the disk allows, else copied."""
    src = HOME / "processed" / "households"
    if not src.exists():
        return 0
    dst = dest_processed / "households"
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in src.iterdir():
        if not f.is_file():
            continue
        target = dst / f.name
        if target.exists():
            continue
        try:
            os.link(f, target)
        except OSError:
            shutil.copy2(f, target)
        n += 1
    return n


def _annual_check(year: int) -> dict | None:
    """The export's measured annual consumption beside the modelled household demand, when given."""
    import numpy as np

    from .grid.builder import load_tables
    from .profiles import household

    _, _, cust = load_tables()
    if "annual_kwh" not in cust.columns:
        return None
    given = cust["annual_kwh"].to_numpy(dtype=float)
    ok = np.isfinite(given) & (given > 0)
    if not ok.any():
        return None
    model = household.customer_base_load(year, len(cust)).sum(axis=1)
    ratio = given[ok] / np.maximum(model[ok], 1e-9)
    return {"customers": int(ok.sum()), "median_given_kwh": float(np.median(given[ok])),
            "median_model_kwh": float(np.median(model[ok])), "median_ratio": float(np.median(ratio))}


def build(ds_id: str, workers: int = 2, quick: bool = True, steps: dict | None = None) -> dict:
    """Run every step of a network's build in this process, recording each one. `steps` replaces
    the real steps (a test's fakes); the order and the record are the same."""
    rec = record(ds_id)
    if building(ds_id):
        raise DatasetError("building", id=ds_id)
    rec.update(workers=workers, quick=quick)
    steps = steps or _real_steps(ds_id, workers, quick)
    return procs.run_steps(rec, STEPS, steps, _save, _lock(ds_id), logger="kapacitet.datasets")


def _real_steps(ds_id: str, workers: int, quick: bool) -> dict:
    """The build as the CLI runs it, in a process whose KAPACITET_DATA_DIR is the network's folder."""
    from .paths import PROCESSED

    def do_import():
        from .importer.build import import_directory

        network, _report, applied = import_directory(folder(ds_id) / "import")
        return {"summary": {k: network.summary[k] for k in ("customers", "feeders", "buses", "lines", "trafo_type")},
                "fallbacks": applied}

    def do_households():
        return {"files": _copy_households(PROCESSED)}

    def do_profiles():
        from .config import settings
        from .profiles import build as profiles

        # every weather year, quick or not: the static study takes its minimum load over all of
        # them, and the profiles are the cheap part; "quick" trims the computation that follows
        profiles.build_all(quick=False)
        check = _annual_check(settings().reference_year)
        return {"annual_kwh": check} if check else {}

    def do_compute():
        # everything the screens read: capacity, envelopes, risk, the extra stages (operation and
        # protection included) and meta.json
        from .engine import forecast, run

        run.compute_all(quick=quick, workers=workers)
        forecast.write()          # the forecast ladder the Method screen reads (its own CLI command)
        return {}

    return {"import": do_import, "households": do_households, "profiles": do_profiles, "compute": do_compute}


def log_tail(ds_id: str, lines: int = 40) -> list[str]:
    return procs.log_tail(folder(ds_id), lines)
