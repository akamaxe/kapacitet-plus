"""What the machine has for a study's station processes (task 2.7 of the technical review).

Six station processes at a time, the earlier default, left 0.7 GB of commit free on a 16 GB laptop
with four of them (finding H17), and the laptop suspended in the middle of a night's build (H16).
The number of processes now comes from the free memory and what a station takes; the build keeps
Windows awake and says when the clock jumped all the same.
"""
from __future__ import annotations

import sys
import time

import pytest

from kapacitet import machine
from kapacitet.study import portfolio

MEMORY = {"total_gb": 16.0, "available_gb": 9.0, "commit_free_gb": 5.0}


def test_the_memory_decides_how_many_station_processes_run_at_once():
    # the smaller of what is free and the commit left, 2 GB kept, 1.5 GB a station: (5 - 2) / 1.5
    out = machine.station_jobs(20, memory=MEMORY, cores=12)
    assert out == {"jobs": 2, "basis": "memory", "free_gb": 5.0, "per_station_gb": 1.5, "keep_free_gb": 2.0,
                   "cores": 12}
    assert machine.station_jobs(20, 1.0, memory=MEMORY, cores=12)["jobs"] == 3
    # never more than the cores or the stations, never fewer than one
    roomy = {"total_gb": 64.0, "available_gb": 60.0, "commit_free_gb": None}
    assert machine.station_jobs(20, memory=roomy, cores=4) | {} == {
        "jobs": 4, "basis": "cores", "free_gb": 60.0, "per_station_gb": 1.5, "keep_free_gb": 2.0, "cores": 4}
    assert machine.station_jobs(3, memory=roomy, cores=12)["basis"] == "stations"
    tight = {"total_gb": 8.0, "available_gb": 2.2, "commit_free_gb": 2.4}
    assert machine.station_jobs(20, memory=tight, cores=12)["jobs"] == 1


def test_without_a_reading_of_the_memory_two_at_a_time(monkeypatch):
    monkeypatch.setattr(machine, "free_memory", lambda: None)
    out = machine.station_jobs(20, cores=12)
    assert out["jobs"] == 2 and out["basis"] == "unknown" and out["free_gb"] is None


def test_this_machine_says_what_it_has_free_and_what_this_process_took():
    mem = machine.free_memory()
    assert mem is not None, "the operating system's figures could not be read"
    assert 0 < mem["available_gb"] <= mem["total_gb"]
    if sys.platform == "win32":
        assert mem["commit_free_gb"] > 0
    assert machine.peak_memory_mb() > 10


def test_a_jump_of_the_clock_is_noted_while_awake(monkeypatch):
    calls = {"n": 0}
    real = time.time

    def clock() -> float:
        calls["n"] += 1
        return real() + (300.0 if calls["n"] > 3 else 0.0)       # the lid closed for five minutes

    monkeypatch.setattr(machine.time, "time", clock)
    with machine.awake(every_s=0.01, gap_s=1.0) as gaps:
        deadline = real() + 5
        while not gaps and real() < deadline:
            time.sleep(0.01)
    assert len(gaps) == 1 and 295 < gaps[0]["seconds"] < 301
    assert gaps[0]["from"] < gaps[0]["to"]


def test_awake_notes_nothing_when_the_clock_runs_true():
    with machine.awake(every_s=0.01, gap_s=1.0) as gaps:
        time.sleep(0.1)
    assert gaps == []


@pytest.fixture
def study(tmp_path):
    """A study of three stations from pandapower's benchmark networks (`study demo`)."""
    from kapacitet.study.demo import create

    return create(tmp_path / "demo")


def _stand_in(st, sid):
    return [sys.executable, "-c", ""]


def test_the_build_runs_what_the_memory_holds_and_says_why(study, monkeypatch):
    monkeypatch.setattr(machine, "free_memory", lambda: MEMORY)
    told = []
    out = portfolio.run(study, make_command=_stand_in, announce=lambda todo, how: told.append((list(todo), how)))
    assert out["jobs"]["jobs"] == 2 and out["jobs"]["basis"] == "memory"
    assert out["jobs"]["per_station"] == "default" and out["jobs"]["per_station_gb"] == machine.STATION_GB
    assert told == [(study.station_ids(), out["jobs"])] and out["suspended"] == []
    assert all("_wall" not in r for r in out["built"].values())


def test_a_measured_peak_is_what_the_next_build_reserves(study, monkeypatch):
    monkeypatch.setattr(machine, "free_memory", lambda: MEMORY)
    sid = study.station_ids()[0]
    study.save(study.record(sid) | {"peak_memory_mb": 2000.0, "quick": True})
    per, how = portfolio.per_station_gb(study, quick=True)
    assert how == "measured" and per == pytest.approx(2000.0 / 1024 * portfolio.PEAK_MARGIN)
    assert portfolio.per_station_gb(study, quick=False) == (machine.STATION_GB, "default")
    assert portfolio.jobs_for(study, 3, None, True)["jobs"] == 1           # (5 - 2) / 2.15
    assert portfolio.jobs_for(study, 3, 3, True) == {"jobs": 3, "basis": "asked"}


def test_a_station_building_while_the_machine_was_away_says_so(study, monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime

    now = time.time()
    gap = {"from": datetime.fromtimestamp(now - 60).astimezone().isoformat(timespec="seconds"),
           "to": datetime.fromtimestamp(now + 3600).astimezone().isoformat(timespec="seconds"), "seconds": 3660.0}

    @contextmanager
    def away():
        yield [gap]

    monkeypatch.setattr(machine, "awake", away)
    sid = study.station_ids()[0]
    # a station process that lasts long enough for its overlap with the gap to show at 0.1 s
    out = portfolio.run(study, stations=[sid], make_command=lambda st, s: [sys.executable, "-c", "import time; time.sleep(0.5)"])
    assert out["suspended"] == [gap] and out["built"][sid]["suspended_s"] >= 0.4
    assert study.record(sid)["suspended_s"] == out["built"][sid]["suspended_s"]
