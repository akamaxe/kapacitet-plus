"""Connections to PostgreSQL, and the transactions that replace the file locks.

`connection()` lends a connection from a small pool per database URL (psycopg 3, autocommit; every
write runs inside `with conn.transaction()`). `locked(name)` opens a transaction that holds a
transaction-level advisory lock on (scope, name) until it commits: the database's form of the
lock a file backend takes within its process, held across every server that shares the database.
Inside it, the store's calls for that same name join the open transaction (`held(name)`), so a
check-then-append (an application refused while another is open for the house) is one transaction.
Another name opens a transaction of its own: a decision's seal is committed after the decision,
as with files, not inside it.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import database_url, scope

_POOL: dict[str, list[Any]] = {}
_POOL_GUARD = threading.Lock()
_POOL_MAX = 8
_HELD: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("kapacitet_store_held", default={})


class NoDatabase(RuntimeError):
    """The postgres backend was asked for without KAPACITET_DATABASE_URL."""


def _url(url: str | None = None) -> str:
    got = url or database_url()
    if not got:
        raise NoDatabase("KAPACITET_DATABASE_URL is not set")
    return got


def connect(url: str | None = None):
    """A new connection of its own (the caller closes it)."""
    import psycopg

    return psycopg.connect(_url(url), autocommit=True, connect_timeout=10)


@contextmanager
def connection(url: str | None = None) -> Iterator[Any]:
    """A pooled connection, given back when the block ends (and dropped if it broke)."""
    key = _url(url)
    with _POOL_GUARD:
        idle = _POOL.setdefault(key, [])
        conn = idle.pop() if idle else None
    if conn is None or conn.closed:
        conn = connect(key)
    ok = False
    try:
        yield conn
        ok = True
    finally:
        reusable = ok and not conn.closed and not conn.broken and conn.info.transaction_status == 0
        with _POOL_GUARD:
            idle = _POOL.setdefault(key, [])
            if reusable and len(idle) < _POOL_MAX:
                idle.append(conn)
                conn = None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - a connection already gone
                pass


def close_all() -> None:
    """Close every pooled connection (tests, and a server that stops)."""
    with _POOL_GUARD:
        conns = [c for idle in _POOL.values() for c in idle]
        _POOL.clear()
    for c in conns:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def lock_key(name: str) -> str:
    return f"{scope()}:{name}"


@contextmanager
def locked(name: str) -> Iterator[Any]:
    """A transaction holding the advisory lock (scope, name); re-entrant within one context."""
    held = _HELD.get()
    if name in held:
        yield held[name]
        return
    with connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key(name),))
        token = _HELD.set({**held, name: conn})
        try:
            yield conn
        finally:
            _HELD.reset(token)


@contextmanager
def held(name: str) -> Iterator[Any]:
    """The transaction `locked(name)` holds in this context, or a pooled connection of its own
    (autocommit, so a single statement is its own transaction)."""
    conn = _HELD.get().get(name)
    if conn is not None:
        yield conn
        return
    with connection() as conn:
        yield conn
