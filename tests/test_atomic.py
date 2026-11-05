"""Writes that a reader never sees half done (kapacitet.atomic). Standard library and numpy only."""

from __future__ import annotations

import gzip
import json
import threading

import numpy as np
import pytest

from kapacitet import atomic


def _strays(folder) -> list:
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp"))


def test_each_writer_round_trips_and_leaves_no_temporary_file(tmp_path):
    atomic.write_bytes_atomic(tmp_path / "a.bin", b"\x00\x01")
    atomic.write_text_atomic(tmp_path / "b.txt", "Smedslätten\n")
    atomic.write_json_atomic(tmp_path / "sub" / "c.json", {"k": [1, 2.5, "å"]})
    atomic.savez_atomic(tmp_path / "d.npz", p=np.arange(4.0), q=np.ones(2, dtype=np.float32))
    atomic.savez_atomic(tmp_path / "e.data", compressed=True, x=np.zeros(3))
    atomic.gzip_json_atomic(tmp_path / "f.json.gz", {"id": "abc"})

    assert (tmp_path / "a.bin").read_bytes() == b"\x00\x01"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "Smedslätten\n"
    assert json.loads((tmp_path / "sub" / "c.json").read_text(encoding="utf-8")) == {"k": [1, 2.5, "å"]}
    with np.load(tmp_path / "d.npz") as z:
        assert z["p"].tolist() == [0.0, 1.0, 2.0, 3.0] and z["q"].dtype == np.float32
    # the exact name given: numpy would otherwise add .npz to it
    assert not (tmp_path / "e.data.npz").exists()
    with np.load(tmp_path / "e.data") as z:
        assert z["x"].tolist() == [0.0, 0.0, 0.0]
    with gzip.open(tmp_path / "f.json.gz", "rt", encoding="utf-8") as fh:
        assert json.load(fh) == {"id": "abc"}
    assert _strays(tmp_path) == [] and _strays(tmp_path / "sub") == []


def test_the_same_object_gives_the_same_gzip_bytes(tmp_path):
    atomic.gzip_json_atomic(tmp_path / "a.gz", {"x": 1})
    atomic.gzip_json_atomic(tmp_path / "b.gz", {"x": 1})
    assert (tmp_path / "a.gz").read_bytes() == (tmp_path / "b.gz").read_bytes()


def test_a_failed_write_keeps_the_old_file_and_cleans_up(tmp_path):
    target = tmp_path / "state.json"
    atomic.write_json_atomic(target, {"version": 1})
    with pytest.raises(TypeError):
        atomic.write_json_atomic(target, {"bad": object()})
    with pytest.raises(RuntimeError):
        with atomic.atomic_file(target) as fh:
            fh.write(b'{"version": 2, "half')
            raise RuntimeError("the disk filled up half-way")
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}
    assert _strays(tmp_path) == []


def test_a_replace_never_shows_a_reader_a_partial_file(tmp_path):
    """Readers racing a writer only ever see one whole version or the other."""
    target = tmp_path / "big.json"
    versions = [{"v": i, "pad": "x" * 200_000} for i in range(2)]
    atomic.write_json_atomic(target, versions[0])
    seen, stop = set(), threading.Event()

    def read():
        while not stop.is_set():
            try:
                seen.add(json.loads(target.read_text(encoding="utf-8"))["v"])
            except PermissionError:     # Windows: the file was being replaced at that instant
                continue

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for i in range(30):
            atomic.write_json_atomic(target, versions[i % 2])
    finally:
        stop.set()
        reader.join()
    assert seen <= {0, 1} and seen


def test_appended_lines_from_many_writers_stay_whole(tmp_path):
    log = tmp_path / "logs" / "chain.jsonl"

    def write(n: int):
        for i in range(50):
            atomic.append_line(log, json.dumps({"writer": n, "i": i, "pad": "y" * 300}))

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = log.read_bytes().split(b"\n")
    assert lines[-1] == b"" and len(lines) == 4 * 50 + 1
    records = [json.loads(line) for line in lines[:-1]]
    assert sorted((r["writer"], r["i"]) for r in records) == [(n, i) for n in range(4) for i in range(50)]


def test_appended_lines_from_several_processes_stay_whole(tmp_path):
    """Windows emulates O_APPEND with a seek and a write: without the lock, two processes lose lines."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    import kapacitet

    log = tmp_path / "shared.jsonl"
    src = str(Path(kapacitet.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([src, os.environ.get("PYTHONPATH", "")])}
    code = ("import json, sys\nfrom kapacitet.atomic import append_line\n"
            "for i in range(100):\n"
            "    append_line(sys.argv[1], json.dumps({'p': int(sys.argv[2]), 'i': i, 'pad': 'z' * 500}))\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(log), str(n)], env=env) for n in range(3)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert sorted((r["p"], r["i"]) for r in records) == [(n, i) for n in range(3) for i in range(100)]


def test_append_line_takes_exactly_one_line(tmp_path):
    with pytest.raises(ValueError):
        atomic.append_line(tmp_path / "x.log", "two\nlines")
    atomic.append_line(tmp_path / "x.log", "one")
    atomic.append_line(tmp_path / "x.log", "two")
    assert (tmp_path / "x.log").read_bytes() == b"one\ntwo\n"
