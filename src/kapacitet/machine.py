"""What this machine has for the processes a study starts: free memory, and whether it may sleep.

`free_memory` reads the operating system's own figures. `station_jobs` turns them into how many
station processes to run at once: what the free memory holds after some is kept for the rest of the
machine, one station each at what a station process takes, never more than the cores or the
stations. Six at a time, the earlier default, left 0.7 GB of commit free on a 16 GB laptop with
four (finding H17 of the technical review of 14-11-2026). `peak_memory_mb` is what this process
took at most, which a station records so the next build knows. `awake` keeps Windows from
suspending while a study builds, and says when the clock jumped all the same (a lid closed, a
battery that ran out): a station building then took that much longer than it says.
"""

from __future__ import annotations

import contextlib
import math
import os
import sys
import threading
import time
from datetime import datetime
from typing import Iterator

GB = 1024 ** 3
STATION_GB = 1.5        # what a station process commits at most, before a build of the study has measured it
KEEP_FREE_GB = 2.0      # what a study leaves to the rest of the machine
GAP_S = 60.0            # a clock that moved this much more than the watcher slept: the machine was suspended


def free_memory() -> dict | None:
    """{"total_gb", "available_gb", "commit_free_gb"}: the physical memory, what of it is free to
    use, and the commit still allowed (Windows' commit limit less its charge; Linux's CommitLimit
    less Committed_AS, None where the kernel overcommits). None where none of it can be read."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return {"total_gb": st.ullTotalPhys / GB, "available_gb": st.ullAvailPhys / GB,
                "commit_free_gb": st.ullAvailPageFile / GB}
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            info = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in fh if ":" in line}
    except (OSError, ValueError, IndexError):
        return None
    if "MemTotal" not in info or "MemAvailable" not in info:
        return None
    strict = False
    with contextlib.suppress(OSError, ValueError):
        with open("/proc/sys/vm/overcommit_memory", encoding="ascii") as fh:
            strict = fh.read().strip() == "2"
    commit = (info["CommitLimit"] - info["Committed_AS"]) / GB if strict and "CommitLimit" in info else None
    return {"total_gb": info["MemTotal"] / GB, "available_gb": info["MemAvailable"] / GB, "commit_free_gb": commit}


def peak_memory_mb() -> float | None:
    """What this process took at most, in MB: its peak commit on Windows, its peak resident set
    elsewhere; None where it cannot be read."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class COUNTERS(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        # an instance of its own, so the argument types set here touch no one else's kernel32
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(COUNTERS), wintypes.DWORD]
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        c = COUNTERS()
        c.cb = ctypes.sizeof(COUNTERS)
        if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return None
        return round(max(c.PeakPagefileUsage, c.PeakWorkingSetSize) / 1024 ** 2, 1)
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / 1024 ** 2 if sys.platform == "darwin" else peak / 1024, 1)


def station_jobs(stations: int, per_station_gb: float = STATION_GB, keep_free_gb: float = KEEP_FREE_GB,
                 memory: dict | None = None, cores: int | None = None) -> dict:
    """How many station processes to run at once, and why: {"jobs", "basis", "free_gb",
    "per_station_gb", "keep_free_gb", "cores"}. The free memory is the smaller of what is available
    and the commit still allowed; `basis` is "memory" when that decides, "cores" or "stations"
    when they do, and "unknown" when the memory could not be read (then two at a time)."""
    cores = cores or os.cpu_count() or 1
    memory = memory if memory is not None else free_memory()
    out = {"per_station_gb": round(per_station_gb, 2), "keep_free_gb": keep_free_gb, "cores": cores}
    if not memory:
        return {**out, "jobs": max(1, min(2, cores, stations)), "basis": "unknown", "free_gb": None}
    free = memory["available_gb"]
    if memory.get("commit_free_gb") is not None:
        free = min(free, memory["commit_free_gb"])
    by_memory = max(1, math.floor((free - keep_free_gb) / max(per_station_gb, 0.1)))
    jobs = max(1, min(by_memory, cores, stations))
    basis = "memory" if jobs == by_memory else ("stations" if jobs == stations else "cores")
    return {**out, "jobs": jobs, "basis": basis, "free_gb": round(free, 1)}


def _set_awake(on: bool) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags)))


@contextlib.contextmanager
def awake(every_s: float = 5.0, gap_s: float = GAP_S) -> Iterator[list[dict]]:
    """While the block runs, Windows is asked not to suspend (SetThreadExecutionState, from the
    thread that enters; the display may still turn off), and a watcher notes every time the clock
    moved more than `gap_s` beyond its own `every_s` naps. Yields that list, filled as it goes:
    {"from", "to", "seconds"}, local times as ISO strings."""
    gaps: list[dict] = []
    stop = threading.Event()

    def watch() -> None:
        last = time.time()
        while not stop.wait(every_s):
            now = time.time()
            if now - last > every_s + gap_s:
                gaps.append({"from": datetime.fromtimestamp(last).astimezone().isoformat(timespec="seconds"),
                             "to": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
                             "seconds": round(now - last - every_s, 1)})
            last = now

    held = _set_awake(True)
    watcher = threading.Thread(target=watch, name="kapacitet-awake", daemon=True)
    watcher.start()
    try:
        yield gaps
    finally:
        stop.set()
        watcher.join(timeout=every_s + 1)
        if held:
            _set_awake(False)
