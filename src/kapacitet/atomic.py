"""Writes that a reader never sees half done.

Every whole-file writer here streams into a temporary file beside the target, flushes it to disk
and renames it over the target with `os.replace`, which is atomic on the same file system: a
reader finds the old file or the new one, never a mixture, and a crash leaves the old file and at
most a stray temporary (named `.<name>.<random>.tmp`, so a glob for `*.json` never picks it up).
`append_line` adds one line to a log with O_APPEND under a lock, so concurrent writers, threads or
processes, each land a whole line.

    write_bytes_atomic(path, data)        write_text_atomic(path, text)
    write_json_atomic(path, obj)          savez_atomic(path, x=arr)
    gzip_json_atomic(path, obj)           append_line(path, line)
"""

from __future__ import annotations

import gzip
import json
import os
import secrets
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

# Windows refuses to replace a file another process holds open for a moment (a reader, an
# antivirus scan); a short, bounded retry rides that out instead of failing the write.
_REPLACE_TRIES = 10
_REPLACE_WAIT_S = 0.05


def _replace(tmp: Path, path: Path) -> None:
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if os.name != "nt" or attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(_REPLACE_WAIT_S)


def _sync_dir(folder: Path) -> None:
    """Make the rename itself durable (POSIX; Windows cannot open a folder this way)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _keep_mode(path: Path, tmp: Path) -> None:
    """Give the replacement the permission bits of the file it replaces (POSIX; Windows has no
    such bits to lose)."""
    if os.name == "nt":
        return
    try:
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
    except OSError:
        pass        # no file yet, or one we may not inspect: the new file keeps the umask's bits


@contextmanager
def atomic_file(path: str | Path) -> Iterator[IO[bytes]]:
    """A binary file handle whose content replaces `path` only if the block finishes.

    The temporary file is flushed and fsynced before the rename. It takes the permissions of the
    file it replaces, so a record kept private (0600) stays so; a new file gets the process's usual
    ones (the umask applies). On any exception it is removed and `path` is left as it was.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    while True:
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            fd = os.open(tmp, flags, 0o666)
            break
        except FileExistsError:
            continue
    try:
        with os.fdopen(fd, "wb") as fh:
            yield fh
            fh.flush()
            os.fsync(fh.fileno())
        _keep_mode(path, tmp)
        _replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _sync_dir(path.parent)


def write_bytes_atomic(path: str | Path, data: bytes) -> None:
    with atomic_file(path) as fh:
        fh.write(data)


def write_text_atomic(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    write_bytes_atomic(path, text.encode(encoding))


def write_json_atomic(path: str | Path, obj: Any, *, indent: int | None = 1, **dumps: Any) -> None:
    """JSON as UTF-8. The text is built before the file is opened, so an object that cannot be
    serialised leaves no temporary file behind."""
    write_text_atomic(path, json.dumps(obj, indent=indent, **dumps))


def savez_atomic(path: str | Path, *, compressed: bool = False, **arrays: Any) -> None:
    """numpy's .npz under the exact name given (np.savez appends .npz only to a bare file name)."""
    import numpy as np

    with atomic_file(path) as fh:
        (np.savez_compressed if compressed else np.savez)(fh, **arrays)


def gzip_json_atomic(path: str | Path, obj: Any, *, level: int = 6, **dumps: Any) -> None:
    """Gzipped JSON (the API's saved assessments). The archive's own timestamp is zero, so the same
    object always gives the same bytes."""
    data = json.dumps(obj, **dumps).encode("utf-8")
    with atomic_file(path) as fh, gzip.GzipFile(fileobj=fh, mode="wb", compresslevel=level, mtime=0) as gz:
        gz.write(data)


_APPEND_LOCKS: dict[str, threading.Lock] = {}
_APPEND_LOCKS_GUARD = threading.Lock()
# Windows: a byte far past any real end of file, locked as a mutex between processes. It guards
# no data (nothing is ever written there), so readers and the appended bytes are never blocked.
_MUTEX_OFFSET = 2**62
_MUTEX_WAIT_S = 30.0


def _thread_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(path))
    with _APPEND_LOCKS_GUARD:
        return _APPEND_LOCKS.setdefault(key, threading.Lock())


@contextmanager
def _process_lock(fd: int) -> Iterator[None]:
    """Hold the log against other processes while one line goes in.

    On POSIX, O_APPEND alone already lands each write whole at the end; flock adds nothing but is
    cheap, and a file system without locks just goes without. Windows emulates O_APPEND as a seek
    followed by a write, which two writers can interleave, so there the mutex is what keeps lines
    whole.
    """
    if os.name == "nt":
        import msvcrt

        deadline = time.monotonic() + _MUTEX_WAIT_S
        while True:
            os.lseek(fd, _MUTEX_OFFSET, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.005)
        try:
            yield
        finally:
            os.lseek(fd, _MUTEX_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            yield
            return
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def append_line(path: str | Path, line: str) -> None:
    """Add one line to a log, durably and whole: every write goes to the end (O_APPEND), one
    writer at a time (a lock across threads and processes), and fsync keeps it across a power cut.

    The line must not contain a newline of its own (one record per line); the newline is added.
    """
    if "\n" in line or "\r" in line:
        raise ValueError("append_line takes one line, without line breaks")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (line + "\n").encode("utf-8")
    with _thread_lock(path):
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o666)
        try:
            with _process_lock(fd):
                size = os.lseek(fd, 0, os.SEEK_END)
                if size:              # a torn tail (power cut mid-append): keep it on its own line
                    os.lseek(fd, size - 1, os.SEEK_SET)
                    if os.read(fd, 1) != b"\n":
                        data = b"\n" + data
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
        finally:
            os.close(fd)
