"""A study's stations, built side by side: a bounded pool of station processes, incremental.

`run` starts `kapacitet station build <sid> --study <dir>` for every station that needs it, at most
`jobs` at a time (six by default, what 16 GB holds, and never more than the machine's cores). Each
runs as its own process, with its own environment:

    KAPACITET_DATA_DIR       stations/<sid>        the only folder it writes
    KAPACITET_HOME           <study>
    KAPACITET_RAW_DIR        <study>/raw           the study's downloads
    KAPACITET_STUDY          <study>               study.toml, merged by config.py
    KAPACITET_REFERENCE_DIR  this process's HOME   whose weather and household pool it may reuse
    KAPACITET_INBOX_WORKER   0

Nothing here touches active.json or a running server. A station is built again only when the
digests of its inputs, its method version or its mode (quick or not) differ from those of its last
finished build, or when it is forced. A station whose process fails marks only its own record.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .. import paths, procs
from . import Study
from .config import METHOD_VERSION

MAX_JOBS = 6
BUILD = ("forced", "never built", "failed", "inputs changed", "method changed", "mode changed", "interrupted")


def env_for(study: Study, sid: str) -> dict[str, str]:
    """The environment of a station's process: nothing of this process's KAPACITET_* survives."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("KAPACITET_")}
    env.update(procs.child_env(study.station_dir(sid), home=study.root, raw=study.raw, study=study.root,
                               reference_dir=paths.REFERENCE, inbox_worker="0"))
    env["PYTHONUNBUFFERED"] = "1"
    return env


def reason(study: Study, sid: str, force: bool = False, quick: bool = False) -> str:
    """Why a station would be built, or why not ("unchanged", "building")."""
    if study.building(sid):
        return "building"
    if force:
        return "forced"
    rec = study.record(sid)
    status = rec.get("status")
    if status == "failed":
        return "failed"
    if status == "building":                     # a record left "building" by a process that died
        return "interrupted"
    if status != "ready":
        return "never built"
    if rec.get("inputs") != study.digests(sid):
        return "inputs changed"
    if rec.get("method_version") != METHOD_VERSION:
        return "method changed"
    if bool(rec.get("quick")) != bool(quick):
        return "mode changed"
    return "unchanged"


def plan(study: Study, stations: list[str] | None = None, force: bool = False, quick: bool = False) -> dict[str, str]:
    """Each station's reason, in id order."""
    return {sid: reason(study, sid, force, quick) for sid in (stations or study.station_ids())}


def command(study: Study, sid: str, quick: bool = False, allow_git: bool = False) -> list[str]:
    cmd = [sys.executable, "-m", "kapacitet", "station", "build", sid, "--study", str(study.root)]
    if quick:
        cmd.append("--quick")
    if allow_git:
        cmd.append("--allow-git")
    return cmd


def run(study: Study, stations: list[str] | None = None, jobs: int | None = None, force: bool = False,
        quick: bool = False, allow_git: bool = False, timeout: float | None = None,
        make_command: Callable[[Study, str], list[str]] | None = None) -> dict:
    """Build what needs building; returns {"built": {sid: outcome}, "skipped": {sid: reason}}.
    `make_command` replaces the station process's command line (a test's stand-in)."""
    why = plan(study, stations, force, quick)
    todo = [sid for sid, r in why.items() if r in BUILD]
    skipped = {sid: r for sid, r in why.items() if r not in BUILD}
    if not todo:
        return {"built": {}, "skipped": skipped}
    n = max(1, min(jobs or MAX_JOBS, os.cpu_count() or 1, len(todo)))

    def one(sid: str) -> dict:
        cmd = make_command(study, sid) if make_command else command(study, sid, quick, allow_git)
        return _run_one(study, sid, cmd, timeout)

    with ThreadPoolExecutor(max_workers=n) as ex:
        built = dict(zip(todo, ex.map(one, todo)))
    return {"built": built, "skipped": skipped}


def _run_one(study: Study, sid: str, cmd: list[str], timeout: float | None) -> dict:
    folder = study.station_dir(sid)
    folder.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    code: int | None
    with open(folder / procs.LOG, "ab") as log:
        log.write(f"\n=== {procs.now()} {' '.join(cmd)}\n".encode("utf-8"))
        log.flush()
        try:
            code = subprocess.run(cmd, env=env_for(study, sid), cwd=str(paths.ROOT), stdout=log,
                                  stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout).returncode
            error = None if code == 0 else f"the station process exited with code {code}"
        except subprocess.TimeoutExpired:
            code, error = None, f"the station process took longer than {timeout:g} s and was stopped"
    rec = study.record(sid)
    if error and (rec.get("status") != "failed" or code is None):
        # the process ended before it could say so itself: say it here, in its record only
        rec.update(status="failed", error=error, finished=procs.now(), pid=None)
        for s in rec.get("steps", []):
            if s.get("status") == "running":
                s.update(status="failed", error=error, finished=procs.now())
        study.save(rec)
        procs.lock_path(folder).unlink(missing_ok=True)
    return {"status": rec["status"], "code": code, "seconds": round(time.perf_counter() - t0, 1),
            "error": rec.get("error")}
