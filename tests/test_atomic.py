"""Writes that a reader never sees half done (kapacitet.atomic). Standard library and numpy only."""

from __future__ import annotations

import gzip
import json
import os
import stat
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
    seen, bad, stop = set(), [], threading.Event()
    reads = 0

    def read():
        nonlocal reads
        while not stop.is_set():
            try:
                seen.add(json.loads(target.read_text(encoding="utf-8"))["v"])
                reads += 1
            except PermissionError:     # Windows: the file was being replaced at that instant
                continue
            except (ValueError, OSError) as err:   # a partial or missing file: what must never happen
                bad.append(repr(err))

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for i in range(30):
            atomic.write_json_atomic(target, versions[i % 2])
        # the reader must have read while the writer ran, not only before or after it
        for _ in range(200):
            if reads >= 5:
                break
            atomic.write_json_atomic(target, versions[1])
    finally:
        stop.set()
        reader.join()
    assert not bad, bad[:3]
    assert seen <= {0, 1} and reads >= 5


@pytest.mark.skipif(os.name == "nt", reason="Windows files have no permission bits to keep")
def test_a_replaced_file_keeps_its_permissions(tmp_path):
    """A record kept private (0600) stays private after the rename puts a new file in its place."""
    target = tmp_path / "settings.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o600)
    atomic.write_json_atomic(target, {"v": 2})
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}


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


# ---------------------------------------------------------------- the readers of what these write

def test_a_cut_request_cache_is_computed_again_not_an_error(tmp_path):
    from kapacitet.engine import request as rq

    path = tmp_path / "static_2025.npz"
    p = np.arange(12, dtype=np.float32).reshape(3, 4)
    atomic.savez_atomic(path, p=p, q=-p, stamp=7.0)
    got = rq._cached_pq(path, 7.0, 3)
    assert got is not None and np.array_equal(got[0], p) and got[0].dtype == float
    assert rq._cached_pq(path, 8.0, 3) is None                      # another baseline file
    assert rq._cached_pq(path, 7.0, 5) is None                      # another network
    path.write_bytes(path.read_bytes()[:40])                        # cut short, as a crash left it
    assert rq._cached_pq(path, 7.0, 3) is None
    assert rq._cached_pq(tmp_path / "none.npz", 7.0, 3) is None


def test_an_index_line_cut_short_is_skipped(tmp_path, monkeypatch):
    from kapacitet.api import records

    monkeypatch.setattr(records, "folder", lambda: tmp_path)
    monkeypatch.setattr(records, "address_of", lambda cid: None)
    for job_id in ("a00000000001", "a00000000002"):
        records.save({"id": job_id, "created": "2026-11-05T10:00:00+00:00", "request": {"cid": 1},
                      "result": {"customer": {"cid": 1, "address": "Gatan 1"}, "summary": {}}})
    with (tmp_path / "index.jsonl").open("ab") as fh:
        fh.write(b'{"id": "a00000000003", "crea')
    assert [it["id"] for it in records.listing()] == ["a00000000002", "a00000000001"]
    assert not _strays(tmp_path)
    kept = gzip.decompress((tmp_path / "a00000000001.json.gz").read_bytes())
    assert b"Gatan" not in kept and json.loads(kept)["result"]["customer"] == {"cid": 1}


def test_append_after_a_torn_tail_keeps_the_new_line_whole(tmp_path):
    log = tmp_path / "log.jsonl"
    atomic.append_line(log, json.dumps({"i": 1}))
    with open(log, "ab") as fh:
        fh.write(b'{"i": 2, "tor')           # a power cut mid-append
    atomic.append_line(log, json.dumps({"i": 3}))
    lines = log.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1]) == {"i": 3} and json.loads(lines[0]) == {"i": 1}
