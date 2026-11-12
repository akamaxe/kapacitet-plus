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


@pytest.mark.files_only
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


def _of_length(base, length: int) -> str:
    """An absolute path under `base` of exactly `length` characters, in components a disk accepts."""
    path = str(base)
    while len(path) + 1 + 40 < min(length, 200):        # the folders stay well inside any limit
        path = os.path.join(path, "d" * 40)
    rest = length - len(path) - 1
    assert 0 < rest < 200, (len(str(base)), length)
    return os.path.join(path, "e" * rest)


def test_a_temporary_is_never_longer_than_its_file(tmp_path):
    """The temporary was the file's whole name plus 14 characters, so the deepest file of a study
    (a 64-digit fingerprint) failed in a folder that could hold the file itself."""
    for name in ("a", "a.json", "settings.json", "0123456789abc", "0123456789abcd", "seals.jsonl",
                 "f" * 64 + ".json", "stations.parquet"):
        tmp = atomic.temporary_for(tmp_path / name)
        assert tmp.parent == tmp_path and tmp.name != name
        assert tmp.name.startswith(".") and tmp.name.endswith(".tmp") and not tmp.match("*.json")
        assert len(tmp.name) <= max(len(name), 13), name
    long = atomic.temporary_for(tmp_path / ("f" * 64 + ".json")).name
    assert long.startswith(".ffff") and len(long) == 69                 # it still says whose it is
    assert atomic.temporary_for(tmp_path / "a.json") != atomic.temporary_for(tmp_path / "a.json")


def test_a_path_past_windows_limit_is_said_as_that(tmp_path):
    """Windows answers "no such file or directory" for a path past its limit, about a folder that is
    plainly there. The limit is the system's, so the platform is a parameter here."""
    import pickle

    fits, over = _of_length(tmp_path, 259), _of_length(tmp_path, 260)
    assert atomic.too_long(fits, windows=True) is None
    assert atomic.too_long(over, windows=False) is None                 # elsewhere a long path is just a path
    said = atomic.too_long(over, windows=True)
    assert type(said) is atomic.PathTooLongError and isinstance(said, OSError) and said.filename == over
    text = str(said)
    assert "260 characters" in text and "at most 259" in text and "LongPathsEnabled" in text
    assert text.rstrip("'").endswith(over.replace("\\", "\\\\"))        # the whole path, at the end
    # a folder has less room: Windows keeps space in it for a short file name
    assert atomic.too_long(_of_length(tmp_path, 247), folder=True, windows=True) is None
    assert "at most 247" in str(atomic.too_long(_of_length(tmp_path, 248), folder=True, windows=True))
    # a worker process hands its error to its parent pickled
    again = pickle.loads(pickle.dumps(said))
    assert type(again) is atomic.PathTooLongError and str(again) == text and again.filename == over
    # only "not found" is read as length: any other refusal of a long path stays what it was
    denied = PermissionError(13, "Permission denied", over)
    assert atomic._explained(denied, over) is denied


@pytest.mark.skipif(os.name != "nt", reason="the limit is Windows' own")
def test_a_write_windows_refuses_for_its_length_names_the_path(tmp_path):
    probe = _of_length(tmp_path, 265)
    os.makedirs(os.path.dirname(probe), exist_ok=True)
    try:
        os.close(os.open(probe, os.O_WRONLY | os.O_CREAT, 0o666))
    except FileNotFoundError:
        pass
    else:
        pytest.skip("long paths are switched on for this machine")

    over = _of_length(tmp_path, 262)
    with pytest.raises(atomic.PathTooLongError, match="262 characters") as said:
        atomic.write_text_atomic(over, "{}")
    assert said.value.filename == over and isinstance(said.value.__cause__, FileNotFoundError)
    with pytest.raises(atomic.PathTooLongError, match="262 characters"):
        atomic.append_line(over, "a line")
    with pytest.raises(atomic.PathTooLongError, match="this folder is 250 characters"):
        atomic.write_text_atomic(os.path.join(_of_length(tmp_path, 250), "a.json"), "{}")
    # a short name in a folder too deep for its temporary: the temporary's path is the one said
    near = _of_length(tmp_path, 247)
    with pytest.raises(atomic.PathTooLongError) as tmp_said:
        atomic.write_text_atomic(os.path.join(near, "a.json"), "{}")
    assert tmp_said.value.filename.endswith(".tmp") and "261 characters" in str(tmp_said.value)

    # the case that failed: a 64-digit name whose path fits and whose old temporary did not
    folder = _of_length(tmp_path, 259 - 69 - 1)
    target = os.path.join(folder, "f" * 64 + ".json")
    assert len(target) == 259
    atomic.write_text_atomic(target, "{}")
    with open(target, encoding="utf-8") as fh:
        assert fh.read() == "{}"
    assert [n for n in os.listdir(folder) if n.endswith(".tmp")] == []
