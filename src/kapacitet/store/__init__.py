"""Where the server keeps the state it writes: files under data/requests/, or PostgreSQL.

The server's mutable state is six things, each behind one small interface with two backends:

    what                          interface (files backend)          PostgreSQL tables
    saved assessments + index     Records (store/files.py)           assessments
    the applications log          EventLog (store/files.py)          applications_log
    the DSO's settings            Document (store/files.py)          dso_settings
    the market alternatives       EventLog (store/files.py)          market_alternatives
    the seal chain                seals.FolderChain                  seal_chain, sealed_copies,
                                                                     seal_fingerprints, store_meta,
                                                                     seal_timestamps
    the queue                     engine/queue.FolderQueue           queues, queue_files

`files` is the default and is today's behaviour, byte for byte. `postgres` is chosen by setting
KAPACITET_DATABASE_URL (for instance postgresql://kapacitet@127.0.0.1:5433/kapacitet); its schema
comes from plain SQL migrations (store/migrations/NNN_*.sql) applied by `kapacitet db migrate`.
With it, transactions take the place of the file locks: every append that numbers a line takes an
advisory lock for that log inside its transaction, and `queue.decide` runs in one transaction that
holds the queue's row with SELECT ... FOR UPDATE, so two servers deciding at once on one database
cannot both reserve the same headroom (the second one sees the new version and is refused).

The rows of one data folder carry its `scope()`: "main" for the reference network's folder,
"dataset:<id>" for an imported network (data/datasets/<id>), "path:<folder>" for anything else, or
KAPACITET_STORE_SCOPE when it is set. A study's station process (KAPACITET_STUDY) and a chain
opened with `seals.chain_at` always use files: a study keeps its evidence in its own folder.

What stays in files whatever the backend: the request's injection cache (data/requests/cache, a
cache), the setup upload's chunks and the server lock (one writer per data folder).

`kapacitet db import-files` copies an existing data/requests/ into the database (idempotent, one
way) and `kapacitet db export-files` writes the database back in the same layout, for backups and
for `kapacitet seals verify` on the exported folder (store/transfer.py).
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_URL = "KAPACITET_DATABASE_URL"
ENV_SCOPE = "KAPACITET_STORE_SCOPE"


class QueueChanged(RuntimeError):
    """A queue's swap (`replace_with`) refused: the queue changed after its rebuild began."""


def database_url() -> str | None:
    """KAPACITET_DATABASE_URL, or None when the server keeps its state in files."""
    url = os.environ.get(ENV_URL, "").strip()
    return url or None


def backend() -> str:
    """"postgres" when a database is configured (outside a study's station), else "files"."""
    if database_url() and not os.environ.get("KAPACITET_STUDY"):
        return "postgres"
    return "files"


def is_pg() -> bool:
    return backend() == "postgres"


def scope() -> str:
    """The key of this data folder's rows in the database (see the module's docstring)."""
    given = os.environ.get(ENV_SCOPE, "").strip()
    if given:
        return given
    from .. import paths

    data = Path(paths.DATA)
    if data == Path(paths.HOME):
        return "main"
    if data.parent == Path(paths.DATASETS):
        return f"dataset:{data.name}"
    return "path:" + data.as_posix()


def records(root: Path):
    """The saved assessments: in `root` (data/requests) with files, or in the database."""
    if is_pg():
        from . import postgres

        return postgres.Records()
    from .files import Records

    return Records(root)


def applications(path: Path):
    """The applications log: `path` (applications.jsonl) with files, or the database."""
    if is_pg():
        from . import postgres

        return postgres.EventLog("applications_log")
    from .files import EventLog

    return EventLog(path, "applications")


def market(path: Path):
    """The market alternatives log: `path` (market_alternatives.jsonl) with files, or the database."""
    if is_pg():
        from . import postgres

        return postgres.EventLog("market_alternatives")
    from .files import EventLog

    return EventLog(path, "market")


def settings(path: Path):
    """The DSO's settings document: `path` (settings.json) with files, or the database."""
    if is_pg():
        from . import postgres

        return postgres.Document("dso_settings")
    from .files import Document

    return Document(path)
