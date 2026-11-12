"""Writes that a reader never sees half done.

Every whole-file writer here streams into a temporary file beside the target, flushes it to disk
and renames it over the target with `os.replace`, which is atomic on the same file system: a
reader finds the old file or the new one, never a mixture, and a crash leaves the old file and at
most a stray temporary (named `.<start of the name>.<random>.tmp`, so a glob for `*.json` never
picks it up, and no longer than the file's own name, so a folder that can hold the file can hold
its temporary). `append_line` adds one line to a log with O_APPEND under a lock, so concurrent
writers, threads or processes, each land a whole line.

A path Windows refuses for its length alone is said as that (`PathTooLongError`): the system's own
answer is "no such file or directory" about a folder that is plainly there.

    write_bytes_atomic(path, data)        write_text_atomic(path, text)
    write_json_atomic(path, obj)          savez_atomic(path, x=arr)
    gzip_json_atomic(path, obj)           append_line(path, line)
"""

from __future__ import annotations

import errno
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
# antivirus scan of the new temporary file); a bounded retry with a growing wait rides that out
# instead of failing the write: 10 ms doubling to 200 ms, about 3 s in all on a busy machine.
_REPLACE_TRIES = 20
_REPLACE_WAIT_S = 0.01
_REPLACE_WAIT_MAX_S = 0.2


# What Windows opens unless long paths are switched on for the machine (LongPathsEnabled): a path
# of MAX_PATH characters with its final NUL, and a folder with room left for an 8.3 file name.
WINDOWS_PATH_LIMIT = 259
WINDOWS_FOLDER_LIMIT = 247


class PathTooLongError(OSError):
    """A path Windows refused for its length alone. An OSError like the one it replaces, so what
    catches that still does; its text says the length, the limit and what to do, and ends with the
    whole path."""


def too_long(path: str | Path, folder: bool = False, windows: bool | None = None) -> PathTooLongError | None:
    """The error to raise for `path` if its length is why Windows will not open it, else None.
    `windows` stands in for the platform in a test."""
    if not (os.name == "nt" if windows is None else windows):
        return None
    full = os.path.abspath(path)
    limit = WINDOWS_FOLDER_LIMIT if folder else WINDOWS_PATH_LIMIT
    if len(full) <= limit:
        return None
    return PathTooLongError(
        errno.ENAMETOOLONG,
        f"this {'folder' if folder else 'path'} is {len(full)} characters long and Windows opens at most {limit} "
        "unless long paths are switched on: keep the data or the study in a shorter folder, or enable long "
        "paths on the machine (LongPathsEnabled)", full)


def _explained(exc: OSError, path: Path, folder: bool = False) -> OSError:
    """`exc`, or what it means: Windows answers "not found" (errno 2, WinError 3 or 206) for a path
    past its limit, whatever is on the disk."""
    if isinstance(exc, FileNotFoundError) or getattr(exc, "winerror", None) in (3, 206):
        return too_long(path, folder) or exc
    return exc


def _make_folder(folder: Path) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        said = _explained(exc, folder, folder=True)
        if said is exc:
            raise
        raise said from exc


def _open(path: Path, flags: int, target: Path | None = None) -> int:
    """os.open. `target` is the file a temporary stands for: when both are too long, the error
    names the one the caller knows."""
    try:
        return os.open(path, flags, 0o666)
    except FileExistsError:
        raise
    except OSError as exc:
        said = _explained(exc, target) if target is not None else exc
        if said is exc:
            said = _explained(exc, path)
        if said is exc:
            raise
        raise said from exc


_TEMPORARY_TAIL = 13      # `.` + eight hex digits + `.tmp`


def temporary_for(path: Path) -> Path:
    """A hidden name beside `path` for its temporary file: the start of the file's name, eight random
    hex digits and `.tmp`. It is never longer than the name itself (13 characters for a shorter
    name), so the temporary fits wherever the file does: a 64-digit name plus a 14-character
    suffix was what pushed a deep study folder past what Windows opens."""
    head = path.name[: max(0, len(path.name) - _TEMPORARY_TAIL - 1)]
    tail = f".{secrets.token_hex(4)}.tmp"
    return path.with_name(f".{head}{tail}" if head else tail)


def _replace(tmp: Path, path: Path) -> None:
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if os.name != "nt" or attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(min(_REPLACE_WAIT_MAX_S, _REPLACE_WAIT_S * 2 ** attempt))


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
    _make_folder(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    while True:
        tmp = temporary_for(path)
        try:
            fd = _open(tmp, flags, target=path)
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


def savez_atomic(path: str | Path, *, compressed: bool = False, stable: bool = False, **arrays: Any) -> None:
    """numpy's .npz under the exact name given (np.savez appends .npz only to a bare file name).

    `stable` writes the same bytes for the same arrays: np.savez stamps each member with the time
    it was written, so two writes of one array differ; here every member carries the zip format's
    earliest date, in the order given. The queue's rebuild (engine/queue.rebuild) relies on it."""
    import numpy as np

    if not stable:
        with atomic_file(path) as fh:
            (np.savez_compressed if compressed else np.savez)(fh, **arrays)
        return
    import io
    import zipfile

    from numpy.lib import format as npformat

    how = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
    with atomic_file(path) as fh, zipfile.ZipFile(fh, mode="w", compression=how, allowZip64=True) as zf:
        for name, value in arrays.items():
            buf = io.BytesIO()
            npformat.write_array(buf, np.asanyarray(value), allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = how
            info.external_attr = 0o600 << 16
            zf.writestr(info, buf.getvalue())


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
    _make_folder(path.parent)
    data = (line + "\n").encode("utf-8")
    with _thread_lock(path):
        fd = _open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0))
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
