"""The postgres backend: the same interfaces as the files backend, on the tables of
store/migrations. Every row carries the data folder's scope (store.scope()), and every text kept
is the exact text the files backend writes, so an export gives the files back byte for byte.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from . import db, scope


def _parse(lines: list[str]) -> list[Any]:
    out = []
    for raw in lines:
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


class Records:
    backend = "postgres"

    @contextmanager
    def locked(self) -> Iterator[None]:
        with db.locked("records"):
            yield

    def put(self, job_id: str, kept: dict, line: dict) -> None:
        with db.held("records") as conn:
            conn.execute("INSERT INTO assessments (scope, id, created, doc, line) VALUES (%s, %s, %s, %s, %s)"
                         " ON CONFLICT (scope, id) DO UPDATE SET doc = EXCLUDED.doc, line = EXCLUDED.line,"
                         " created = EXCLUDED.created",
                         (scope(), job_id, kept.get("created"), json.dumps(kept), json.dumps(line)))

    def get(self, job_id: str) -> dict | None:
        with db.held("records") as conn:
            row = conn.execute("SELECT doc FROM assessments WHERE scope = %s AND id = %s", (scope(), job_id)).fetchone()
        return json.loads(row[0]) if row else None

    def exists(self, job_id: str) -> bool:
        with db.held("records") as conn:
            return conn.execute("SELECT 1 FROM assessments WHERE scope = %s AND id = %s",
                                (scope(), job_id)).fetchone() is not None

    def index(self) -> list[Any]:
        with db.held("records") as conn:
            rows = conn.execute("SELECT line FROM assessments WHERE scope = %s AND line IS NOT NULL ORDER BY pos",
                                (scope(),)).fetchall()
        return _parse([r[0] for r in rows])

    def stored(self) -> dict[str, datetime | None]:
        with db.held("records") as conn:
            rows = conn.execute("SELECT id, created FROM assessments WHERE scope = %s ORDER BY pos", (scope(),)).fetchall()
        out: dict[str, datetime | None] = {}
        for job_id, created in rows:
            try:
                out[job_id] = datetime.fromisoformat(created) if created else None
            except ValueError:
                out[job_id] = None
        return out

    def delete(self, ids: set[str]) -> None:
        if not ids:
            return
        with db.held("records") as conn:
            conn.execute("DELETE FROM assessments WHERE scope = %s AND id = ANY(%s)", (scope(), sorted(ids)))


class EventLog:
    """applications_log (numbered by `seq`) or market_alternatives (numbered, unique by `id`)."""

    backend = "postgres"

    def __init__(self, table: str):
        assert table in ("applications_log", "market_alternatives")
        self.table = table

    @contextmanager
    def locked(self) -> Iterator[None]:
        with db.locked(self.table):
            yield

    def entries(self) -> list[Any]:
        with db.held(self.table) as conn:
            rows = conn.execute(f"SELECT line FROM {self.table} WHERE scope = %s ORDER BY seq", (scope(),)).fetchall()
        return _parse([r[0] for r in rows])

    def next_seq(self) -> int:
        with db.held(self.table) as conn:
            got = conn.execute(f"SELECT coalesce(max(seq), 0) FROM {self.table} WHERE scope = %s", (scope(),)).fetchone()
        return int(got[0]) + 1

    def append(self, obj: dict, text: str) -> None:
        with db.locked(self.table) as conn:
            seq = obj.get("seq") if self.table == "applications_log" else None
            if seq is None:
                seq = self.next_seq()
            if self.table == "applications_log":
                conn.execute("INSERT INTO applications_log (scope, seq, line) VALUES (%s, %s, %s)", (scope(), seq, text))
            else:
                conn.execute("INSERT INTO market_alternatives (scope, seq, id, line) VALUES (%s, %s, %s, %s)"
                             " ON CONFLICT (scope, id) DO NOTHING", (scope(), seq, obj["id"], text))


class Document:
    backend = "postgres"

    def __init__(self, table: str):
        assert table == "dso_settings"
        self.table = table

    @contextmanager
    def locked(self) -> Iterator[None]:
        with db.locked(self.table):
            yield

    def get(self) -> Any | None:
        with db.held(self.table) as conn:
            row = conn.execute("SELECT doc FROM dso_settings WHERE scope = %s", (scope(),)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, obj: Any) -> None:
        with db.held(self.table) as conn:
            conn.execute("INSERT INTO dso_settings (scope, doc) VALUES (%s, %s) ON CONFLICT (scope)"
                         " DO UPDATE SET doc = EXCLUDED.doc, updated_at = now()", (scope(), json.dumps(obj, indent=1)))


class Blob:
    """The DSO's logo (brand-logo.png), one row per scope."""

    backend = "postgres"

    def __init__(self, table: str):
        assert table == "brand_logo"
        self.table = table

    @contextmanager
    def locked(self) -> Iterator[None]:
        with db.locked(self.table):
            yield

    def get(self) -> bytes | None:
        with db.held(self.table) as conn:
            row = conn.execute("SELECT png FROM brand_logo WHERE scope = %s", (scope(),)).fetchone()
        return bytes(row[0]) if row else None

    def put(self, data: bytes) -> None:
        with db.held(self.table) as conn:
            conn.execute("INSERT INTO brand_logo (scope, png) VALUES (%s, %s) ON CONFLICT (scope)"
                         " DO UPDATE SET png = EXCLUDED.png, updated_at = now()", (scope(), data))

    def delete(self) -> None:
        with db.held(self.table) as conn:
            conn.execute("DELETE FROM brand_logo WHERE scope = %s", (scope(),))


# ---------------------------------------------------------------- the seal chain

_CHAIN_COLUMNS = ("seq", "at", "kind", "id", "digest", "fingerprint", "prev", "chain", "key_id", "signed_since", "sig")


class Chain:
    """The server's seal chain (seals.py drives it; seals.FolderChain is the files backend)."""

    backend = "postgres"

    @property
    def name(self) -> str:
        """What this installation's signed-line marks are kept under (seals._note_signed)."""
        return f"postgres:{scope()}"

    def where(self) -> str:
        return f"the database ({scope()})"

    @contextmanager
    def locked(self) -> Iterator[None]:
        with db.locked("seals"):
            yield

    def read(self) -> tuple[list[dict], int | None, bool]:
        """The chain's lines; a database append is whole or absent, so nothing is ever torn."""
        with db.held("seals") as conn:
            rows = conn.execute("SELECT line FROM seal_chain WHERE scope = %s ORDER BY seq", (scope(),)).fetchall()
        out = []
        for (raw,) in rows:
            try:
                entry = json.loads(raw)
            except ValueError:
                entry = None
            out.append(entry if isinstance(entry, dict) else {})
        return out, None, False

    def mend(self) -> list[dict]:
        return self.read()[0]

    def exists(self) -> bool:
        with db.held("seals") as conn:
            return conn.execute("SELECT 1 FROM seal_chain WHERE scope = %s LIMIT 1", (scope(),)).fetchone() is not None

    def append(self, entry: dict) -> None:
        cols = {k: entry.get(k) for k in _CHAIN_COLUMNS}
        with db.held("seals") as conn:
            conn.execute("INSERT INTO seal_chain (scope, " + ", ".join(_CHAIN_COLUMNS) + ", line) VALUES ("
                         + ", ".join(["%s"] * (len(_CHAIN_COLUMNS) + 2)) + ")",
                         (scope(), *cols.values(), json.dumps(entry, sort_keys=True)))

    def copy_get(self, kind: str, item_id: str) -> bytes | None:
        with db.held("seals") as conn:
            row = conn.execute("SELECT body FROM sealed_copies WHERE scope = %s AND kind = %s AND id = %s",
                               (scope(), kind, item_id)).fetchone()
        return bytes(row[0]) if row else None

    def copy_put(self, kind: str, item_id: str, text: bytes) -> None:
        with db.held("seals") as conn:
            conn.execute("INSERT INTO sealed_copies (scope, kind, id, body) VALUES (%s, %s, %s, %s)"
                         " ON CONFLICT DO NOTHING", (scope(), kind, item_id, text))

    def copy_delete(self, kind: str, item_id: str) -> None:
        with db.held("seals") as conn:
            conn.execute("DELETE FROM sealed_copies WHERE scope = %s AND kind = %s AND id = %s", (scope(), kind, item_id))

    def fingerprint_put(self, fp_digest: str, text: str) -> None:
        with db.held("seals") as conn:
            conn.execute("INSERT INTO seal_fingerprints (scope, digest, body) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                         (scope(), fp_digest, text))

    def published_key(self) -> bytes | None:
        with db.held("seals") as conn:
            row = conn.execute("SELECT value FROM store_meta WHERE scope = %s AND key = 'seal_key.pub'",
                               (scope(),)).fetchone()
        return row[0].encode("ascii") if row else None

    def publish_key(self, pem: str) -> None:
        with db.held("seals") as conn:
            conn.execute("INSERT INTO store_meta (scope, key, value) VALUES (%s, 'seal_key.pub', %s)"
                         " ON CONFLICT (scope, key) DO UPDATE SET value = EXCLUDED.value"
                         " WHERE store_meta.value <> EXCLUDED.value", (scope(), pem))

    # the RFC 3161 tokens (seals.FolderChain: sealed/<seq>.tsr and sealed/<seq>.tsa_pending)
    def stamps(self) -> dict[int, bytes]:
        with db.held("seals") as conn:
            rows = conn.execute("SELECT seq, token FROM seal_timestamps WHERE scope = %s AND token IS NOT NULL",
                                (scope(),)).fetchall()
        return {int(seq): bytes(token) for seq, token in rows}

    def stamps_pending(self) -> dict[int, str]:
        with db.held("seals") as conn:
            rows = conn.execute("SELECT seq, pending FROM seal_timestamps WHERE scope = %s AND token IS NULL",
                                (scope(),)).fetchall()
        return {int(seq): text for seq, text in rows}

    def stamp_put(self, seq: int, token: bytes) -> None:
        """Keep a line's token, never over one already kept, and clear what it was owed."""
        with db.held("seals") as conn:
            conn.execute("INSERT INTO seal_timestamps (scope, seq, token, pending) VALUES (%s, %s, %s, NULL)"
                         " ON CONFLICT (scope, seq) DO UPDATE SET token = EXCLUDED.token, pending = NULL"
                         " WHERE seal_timestamps.token IS NULL", (scope(), seq, token))

    def stamp_pending(self, seq: int, text: str) -> None:
        with db.held("seals") as conn:
            conn.execute("INSERT INTO seal_timestamps (scope, seq, token, pending) VALUES (%s, %s, NULL, %s)"
                         " ON CONFLICT (scope, seq) DO UPDATE SET pending = EXCLUDED.pending"
                         " WHERE seal_timestamps.token IS NULL", (scope(), seq, text))


# ---------------------------------------------------------------- the queue

STAGING = ".next."                  # a rebuild's staging queue: <name>.next.<unix time>.<random>
STAGING_STALE_S = 24 * 3600         # left by a rebuild that never swapped once this old

def npz_bytes(arrays: dict, stable: bool = False) -> bytes:
    """numpy's .npz as bytes: the same bytes as atomic.savez_atomic writes to a file."""
    import numpy as np

    if not stable:
        buf = io.BytesIO()
        np.savez(buf, **arrays)
        return buf.getvalue()
    import zipfile

    from numpy.lib import format as npformat

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for name, value in arrays.items():
            one = io.BytesIO()
            npformat.write_array(one, np.asanyarray(value), allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o600 << 16
            zf.writestr(info, one.getvalue())
    return buf.getvalue()


def npz_arrays(data: bytes) -> dict:
    import numpy as np

    with np.load(io.BytesIO(data)) as d:
        return {k: d[k] for k in d.files}


class Queue:
    """One queue (`name`: queue, queue_mv098, ...) as a row of `queues` and its arrays in
    `queue_files`. `transaction()` holds the row with SELECT ... FOR UPDATE until it commits: the
    queue's version is what every decision is checked against, so a second decision waits for the
    first to commit and then meets the new version."""

    backend = "postgres"

    def __init__(self, name: str):
        self.name = name
        self._conn = None           # the open transaction, inside `transaction()`

    def where(self) -> str:
        return f"the database ({scope()}, {self.name})"

    @contextmanager
    def _db(self) -> Iterator[Any]:
        if self._conn is not None:
            yield self._conn
        else:
            with db.connection() as conn:
                yield conn

    @contextmanager
    def transaction(self) -> Iterator["Queue"]:
        if self._conn is not None:      # re-entrant: decide's checks inside its own transaction
            yield self
            return
        with db.connection() as conn, conn.transaction():
            conn.execute("INSERT INTO queues (scope, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                         (scope(), self.name))
            conn.execute("SELECT version FROM queues WHERE scope = %s AND name = %s FOR UPDATE", (scope(), self.name))
            self._conn = conn
            try:
                yield self
            finally:
                self._conn = None

    @contextmanager
    def snapshot(self) -> Iterator["Queue"]:
        """One consistent view of the queue row and its arrays (REPEATABLE READ, read only): a
        decision that commits meanwhile, and the collection of the old version's states after it,
        stay invisible until the snapshot ends."""
        if self._conn is not None:
            yield self
            return
        with db.connection() as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self._conn = conn
            try:
                yield self
            finally:
                self._conn = None

    def load(self) -> dict:
        with self._db() as conn:
            row = conn.execute("SELECT doc FROM queues WHERE scope = %s AND name = %s", (scope(), self.name)).fetchone()
        if not row or row[0] is None:
            return {"version": 0, "baseline": None, "items": []}
        return json.loads(row[0])

    def exists(self) -> bool:
        with self._db() as conn:
            return conn.execute("SELECT 1 FROM queues WHERE scope = %s AND name = %s AND doc IS NOT NULL",
                                (scope(), self.name)).fetchone() is not None

    def commit(self, q: dict, marker: dict | None) -> None:
        """Write the queue and the seal it owes in one statement (the commit point of a decision)."""
        with self._db() as conn:
            conn.execute("INSERT INTO queues (scope, name, version, baseline, doc, seal_pending)"
                         " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (scope, name) DO UPDATE SET"
                         " version = EXCLUDED.version, baseline = EXCLUDED.baseline, doc = EXCLUDED.doc,"
                         " seal_pending = EXCLUDED.seal_pending",
                         (scope(), self.name, int(q.get("version", 0)), q.get("baseline"), json.dumps(q, indent=1),
                          json.dumps(marker, indent=1) if marker is not None else None))

    def marker(self) -> Any | None:
        with self._db() as conn:
            row = conn.execute("SELECT seal_pending FROM queues WHERE scope = %s AND name = %s",
                               (scope(), self.name)).fetchone()
        if not row or row[0] is None:
            return None
        try:
            return json.loads(row[0])
        except ValueError:
            return {}

    def clear_marker(self) -> None:
        with self._db() as conn:
            conn.execute("UPDATE queues SET seal_pending = NULL WHERE scope = %s AND name = %s", (scope(), self.name))

    def put_npz(self, file: str, arrays: dict, stable: bool = False) -> None:
        self.put_bytes(file, npz_bytes(arrays, stable))

    def put_bytes(self, file: str, data: bytes) -> None:
        with self._db() as conn:
            conn.execute("INSERT INTO queue_files (scope, name, file, body) VALUES (%s, %s, %s, %s)"
                         " ON CONFLICT (scope, name, file) DO UPDATE SET body = EXCLUDED.body",
                         (scope(), self.name, file, data))

    def get_npz(self, file: str) -> dict | None:
        data = self.get_bytes(file)
        return npz_arrays(data) if data is not None else None

    def get_bytes(self, file: str) -> bytes | None:
        with self._db() as conn:
            row = conn.execute("SELECT body FROM queue_files WHERE scope = %s AND name = %s AND file = %s",
                               (scope(), self.name, file)).fetchone()
        return bytes(row[0]) if row else None

    def files(self) -> list[str]:
        with self._db() as conn:
            rows = conn.execute("SELECT file FROM queue_files WHERE scope = %s AND name = %s ORDER BY file",
                                (scope(), self.name)).fetchall()
        return [r[0] for r in rows]

    def delete(self, file: str) -> None:
        with self._db() as conn:
            conn.execute("DELETE FROM queue_files WHERE scope = %s AND name = %s AND file = %s",
                         (scope(), self.name, file))

    def drop(self) -> None:
        """Remove the queue and its arrays."""
        with self._db() as conn:
            conn.execute("DELETE FROM queue_files WHERE scope = %s AND name = %s", (scope(), self.name))
            conn.execute("DELETE FROM queues WHERE scope = %s AND name = %s", (scope(), self.name))

    def staging(self) -> "Queue":
        """An empty queue beside this one, for `rebuild` to write whole. Its name is its own
        (`<name>.next.<unix time>.<random>`), so two servers' rebuilds never share it, and it keeps
        the live row's doc as it was, which `replace_with` checks before the swap."""
        import time
        import uuid

        with db.connection() as conn:
            row = conn.execute("SELECT doc FROM queues WHERE scope = %s AND name = %s", (scope(), self.name)).fetchone()
        nxt = Queue(f"{self.name}{STAGING}{int(time.time())}.{uuid.uuid4().hex[:8]}")
        nxt.drop()
        nxt._from = row[0] if row else None
        return nxt

    def replace_with(self, staging: "Queue") -> None:
        """Put `staging` in this queue's place, in one transaction holding this queue's row. Refused
        (store.QueueChanged) when the queue changed after `staging()`: a decision committed while
        the rebuild ran, or another server swapped first; the rebuild would wipe it."""
        from . import QueueChanged

        with db.connection() as conn, conn.transaction():
            conn.execute("INSERT INTO queues (scope, name) VALUES (%s, %s) ON CONFLICT DO NOTHING", (scope(), self.name))
            doc = conn.execute("SELECT doc FROM queues WHERE scope = %s AND name = %s FOR UPDATE",
                               (scope(), self.name)).fetchone()[0]
            if doc != getattr(staging, "_from", None):
                raise QueueChanged(f"{self.name} changed while it was rebuilt")
            conn.execute("DELETE FROM queue_files WHERE scope = %s AND name = %s", (scope(), self.name))
            conn.execute("DELETE FROM queues WHERE scope = %s AND name = %s", (scope(), self.name))
            conn.execute("UPDATE queue_files SET name = %s WHERE scope = %s AND name = %s",
                         (self.name, scope(), staging.name))
            conn.execute("UPDATE queues SET name = %s WHERE scope = %s AND name = %s", (self.name, scope(), staging.name))

    def recover_swap(self, older_than_s: float = STAGING_STALE_S) -> None:
        """A rebuild that never swapped leaves its staging queue: removed once it is older than
        `older_than_s`, so another server's rebuild still running keeps its own."""
        import time

        cut = time.time() - older_than_s
        prefix = self.name + STAGING
        with db.connection() as conn:
            names = [r[0] for r in conn.execute("SELECT name FROM queues WHERE scope = %s AND starts_with(name, %s)",
                                                (scope(), prefix)).fetchall()]
            names += [r[0] for r in conn.execute("SELECT DISTINCT name FROM queue_files WHERE scope = %s"
                                                 " AND starts_with(name, %s)", (scope(), prefix)).fetchall()]
        Queue(self.name + ".next").drop()           # the shared name the first version staged under
        for name in sorted(set(names)):
            stamp = name[len(prefix):].split(".", 1)[0]
            if not stamp.isdigit() or int(stamp) < cut:
                Queue(name).drop()


def queue_names() -> list[str]:
    with db.connection() as conn:
        rows = conn.execute("SELECT name FROM queues WHERE scope = %s ORDER BY name", (scope(),)).fetchall()
    return [r[0] for r in rows]


_SCOPED = ("assessments", "applications_log", "market_alternatives", "dso_settings", "seal_chain", "queues")


def other_scopes_when_empty() -> list[str]:
    """The scopes that hold rows when this one holds none (else []): a data folder moved or unpacked
    elsewhere gets a new `path:` scope and so an empty view, which the server warns about."""
    with db.connection() as conn:
        for table in _SCOPED:
            if conn.execute(f"SELECT 1 FROM {table} WHERE scope = %s LIMIT 1", (scope(),)).fetchone():
                return []
        found: set[str] = set()
        for table in _SCOPED:
            found |= {r[0] for r in conn.execute(f"SELECT DISTINCT scope FROM {table}").fetchall()}
    return sorted(found)
