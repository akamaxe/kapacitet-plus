"""The database schema, as plain SQL files applied in order.

Each file `store/migrations/NNN_<name>.sql` is one migration, applied whole in one transaction and
recorded in `schema_migrations` with the SHA-256 of its text. `migrate` applies those not yet
recorded, so running it again changes nothing; a recorded migration whose file has since changed
is reported (`status`) and stops `migrate`, since the database no longer matches the file. Two
`migrate` runs at once wait for each other on an advisory lock.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import db

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
_NAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
_LOCK = "kapacitet:schema_migrations"


class MigrationError(RuntimeError):
    pass


def available() -> list[tuple[int, str, str, str]]:
    """(version, name, sha256, sql) of every migration file, in order."""
    out = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        m = _NAME.match(path.name)
        if not m:
            raise MigrationError(f"{path.name} is not named NNN_<name>.sql")
        # line endings as in the repository, so a checkout that turns them into CRLF hashes the same
        sql = path.read_text(encoding="utf-8").replace("
", "
")
        out.append((int(m[1]), m[2], hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql))
    versions = [v for v, *_ in out]
    if len(set(versions)) != len(versions):
        raise MigrationError("two migrations share a number")
    return out


def _ensure_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                 " version integer PRIMARY KEY, name text NOT NULL, sha256 text NOT NULL,"
                 " applied_at timestamptz NOT NULL DEFAULT now())")


def _applied(conn) -> dict[int, tuple[str, str, str]]:
    exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()[0]
    if not exists:
        return {}
    rows = conn.execute("SELECT version, name, sha256, applied_at::text FROM schema_migrations").fetchall()
    return {v: (n, s, at) for v, n, s, at in rows}


def status(url: str | None = None) -> dict:
    """Which migrations are applied, which are pending, and any whose file changed since."""
    conn = db.connect(url)
    try:
        done = _applied(conn)
    finally:
        conn.close()
    rows, changed = [], []
    for version, name, sha, _sql in available():
        got = done.get(version)
        if got and got[1] != sha:
            changed.append(version)
        rows.append({"version": version, "name": name, "applied": bool(got), "applied_at": got[2] if got else None,
                     "changed": bool(got and got[1] != sha)})
    return {"migrations": rows, "pending": [r["version"] for r in rows if not r["applied"]], "changed": changed,
            "unknown": sorted(set(done) - {r["version"] for r in rows})}


def migrate(url: str | None = None) -> list[int]:
    """Apply every pending migration, each in its own transaction; return the versions applied."""
    conn = db.connect(url)
    applied = []
    try:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_LOCK,))
            _ensure_table(conn)
        for version, name, sha, sql in available():
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_LOCK,))
                got = conn.execute("SELECT sha256 FROM schema_migrations WHERE version = %s", (version,)).fetchone()
                if got:
                    if got[0] != sha:
                        raise MigrationError(f"migration {version:03d}_{name} was applied from a different file")
                    continue
                conn.execute(sql)
                conn.execute("INSERT INTO schema_migrations (version, name, sha256) VALUES (%s, %s, %s)",
                             (version, name, sha))
                applied.append(version)
    finally:
        conn.close()
    return applied
