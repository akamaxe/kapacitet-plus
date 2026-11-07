"""Property tests of the atomic writer (kapacitet/atomic.py): no reader ever sees a partial file.

Hypothesis draws the file there before (or none), the chunks a writer streams and the point where
it fails (or none): at every moment of the write the target holds the old bytes, a failure leaves
them and no temporary file, and success leaves exactly the new bytes. A second property races a
reader against a writer replacing the file with whole versions: every read is one of them.
"""

from __future__ import annotations

import itertools
import threading
import time

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import examples


class Boom(Exception):
    pass


class Stop(BaseException):
    """Not an Exception: what an interrupt or a thread being torn down raises."""


_COUNTER = itertools.count()


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return tmp_path_factory.mktemp("atomic")


def _folder(root):
    d = root / f"e{next(_COUNTER):05d}"
    d.mkdir()
    return d


def _read(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


@settings(max_examples=examples(40))
@given(old=st.one_of(st.none(), st.binary(max_size=4096)),
       chunks=st.lists(st.binary(max_size=8192), max_size=6),
       fail_at=st.one_of(st.none(), st.integers(0, 6)),
       error=st.sampled_from((Boom, Stop)))
def test_a_write_shows_the_old_file_until_it_has_finished_and_leaves_it_on_failure(root, old, chunks, fail_at, error):
    from kapacitet.atomic import atomic_file

    folder = _folder(root)
    path = folder / "result.json"
    if old is not None:
        path.write_bytes(old)
    fails = fail_at is not None and fail_at <= len(chunks)

    def write():
        with atomic_file(path) as fh:
            for i, chunk in enumerate(chunks):
                if fails and i == fail_at:
                    raise error("cut")
                fh.write(chunk)
                fh.flush()
                assert _read(path) == old, "a reader saw the new file before the write had finished"
                assert sorted(folder.glob("*.json")) == ([path] if old is not None else [])
            if fails and fail_at == len(chunks):
                raise error("cut after the last chunk")

    if fails:
        with pytest.raises(error):
            write()
        assert _read(path) == old, "a failed write changed the file"
    else:
        write()
        assert _read(path) == b"".join(chunks)
    assert [p.name for p in folder.iterdir()] == ([] if not path.exists() else [path.name]), \
        "a temporary file was left behind"


@settings(max_examples=examples(5))
@given(versions=st.lists(st.binary(min_size=1, max_size=200_000), min_size=2, max_size=4, unique=True),
       rounds=st.integers(2, 6))
def test_a_reader_racing_the_writer_sees_only_whole_versions(root, versions, rounds):
    from kapacitet.atomic import write_bytes_atomic

    path = _folder(root) / "state.bin"
    write_bytes_atomic(path, versions[0])
    seen, done, reading = [], threading.Event(), threading.Event()

    def reader():
        # a reader that never lets go of the file would starve the rename on Windows, which cannot
        # replace a file held open (the writer retries for about 3 s, then fails, the old file
        # intact): every real reader opens, reads and closes, so this one pauses a millisecond
        while not done.is_set():
            try:
                seen.append(path.read_bytes())
            except (FileNotFoundError, PermissionError):   # Windows: the rename holds the name a moment
                pass
            reading.set()
            time.sleep(0.001)

    t = threading.Thread(target=reader)
    t.start()
    reading.wait(5)
    try:
        for _ in range(rounds):
            for v in versions:
                write_bytes_atomic(path, v)
    finally:
        done.set()
        t.join()
    whole = set(versions)
    assert seen and all(s in whole for s in seen), "a reader saw a mixture of two versions or a partial one"
    assert path.read_bytes() == versions[-1]
