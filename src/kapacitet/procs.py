"""Folders built by a process of their own: the record, the lock, the log and the step runner.

An imported network (datasets.py) and a study's station (study/) are both a data folder that one
child process fills, step by step, while whoever started it reads its record:

    <root>/<record>.json   what it is and how its build went, step by step (written atomically)
    <root>/build.log       the build process's output
    <root>/build.lock      the PID of a build in progress

Nothing here knows what the steps do. Standard library only.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

LOCK = "build.lock"
LOG = "build.log"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, obj: dict) -> None:
    """Write a record atomically: a reader sees the old text or the new one, never half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def lock_path(root: Path) -> Path:
    return Path(root) / LOCK


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259                                          # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def holder(path: Path) -> int | None:
    """The PID in a lock file when that process is alive; None for no lock or a stale one."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip() or 0)
    except (ValueError, OSError):
        return None
    return pid if alive(pid) else None


def building(root: Path) -> bool:
    """A build holds the folder's lock and its process is alive (a stale lock does not count)."""
    return holder(lock_path(root)) is not None


def claim(path: Path) -> int | None:
    """Take the lock file `path` for this process, by exclusive create, taking over a stale one.
    Returns None when taken, or the PID of the live process that holds it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pid = holder(path)
            if pid is not None:
                return pid
            path.unlink(missing_ok=True)                 # stale: its process is gone
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return None
    return holder(path) or -1


def link_files(src: Path, dst: Path, replace: bool = True) -> int:
    """Every file of the folder `src` into `dst`, hard-linked where the disk allows, else copied;
    `replace=False` keeps a file already there. Only for files nothing rewrites in place: a write
    to a linked file is a write to the original."""
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.iterdir()):
        if not f.is_file():
            continue
        target = dst / f.name
        if target.exists():
            if not replace:
                continue
            target.unlink()
        try:
            os.link(f, target)
        except OSError:
            shutil.copy2(f, target)
        n += 1
    return n


def log_tail(root: Path, lines: int = 40) -> list[str]:
    path = Path(root) / LOG
    if not path.exists():
        return []
    text = path.read_bytes()[-20000:].decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]


def child_env(data_dir: Path | None = None, home: Path | None = None, raw: Path | None = None,
              **extra: str) -> dict[str, str]:
    """The KAPACITET_* variables a child process runs with: its data folder, where HOME is and
    whose downloads it reads. Only what is given is set; `extra` adds KAPACITET_<NAME> entries."""
    env: dict[str, str] = {}
    if data_dir is not None:
        env["KAPACITET_DATA_DIR"] = str(data_dir)
    if home is not None:
        env["KAPACITET_HOME"] = str(home)
    if raw is not None:
        env["KAPACITET_RAW_DIR"] = str(raw)
    for key, value in extra.items():
        env[f"KAPACITET_{key.upper()}"] = str(value)
    return env


def set_step(rec: dict, name: str, status: str, **extra) -> None:
    for s in rec["steps"]:
        if s["name"] == name:
            s["status"] = status
            s["started" if status == "running" else "finished"] = now()
            s.update(extra)


def run_steps(rec: dict, names: Iterable[str], steps: dict[str, Callable[[], dict | None]],
              save: Callable[[dict], object], lock: Path, *, logger: str = "kapacitet.procs",
              what: str = "build") -> dict:
    """Run `steps` in the order of `names` under `lock`, saving the record after every change.

    The record ends "ready", or "failed" with the step that raised marked failed and its error
    kept (the log keeps the traceback); the steps after it stay "waiting". The caller checks
    that no other build holds the lock before calling.
    """
    names = tuple(names)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))
    rec.update(status="building", started=now())
    rec["steps"] = [{"name": s, "status": "waiting"} for s in names]
    save(rec)
    try:
        for name in names:
            set_step(rec, name, "running")
            save(rec)
            t0 = time.perf_counter()
            extra = steps[name]() or {}
            set_step(rec, name, "done", seconds=round(time.perf_counter() - t0, 1), **extra)
            save(rec)
        rec.update(status="ready", finished=now())
    except Exception as exc:  # the record says where it stopped and why; the log keeps the traceback
        logging.getLogger(logger).exception("%s of %s failed", what, rec.get("id"))
        for s in rec["steps"]:
            if s["status"] == "running":
                s.update(status="failed", error=str(exc)[:400], finished=now())
        rec.update(status="failed", finished=now(), error=str(exc)[:400])
    finally:
        save(rec)
        lock.unlink(missing_ok=True)
    return rec
