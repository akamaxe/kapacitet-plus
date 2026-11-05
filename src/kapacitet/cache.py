"""One registry for the in-process caches of what was read from data/processed.

Several engine functions keep what they read (the network, the customers, a weather year's
profiles, the bounds built from them) for the life of the process. That is right for a server
and for `compute`, which read one processed folder from start to finish, and wrong as soon as
the same process reads another one: a test's own tiny grid, or a tool that walks several
networks. A cache missed there does not fail, it answers about the wrong grid.

`memo` is `functools.lru_cache` that also registers itself, and `on_clear` registers the reset
of a cache that `lru_cache` cannot express (one keyed on arrays, or a module global), so
`clear_all()` forgets every one of them without the caller having to know the list.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import TypeVar

F = TypeVar("F", bound=Callable)

_CLEARERS: list[Callable[[], None]] = []


def memo(maxsize: int | None = 128) -> Callable[[F], F]:
    """`functools.lru_cache(maxsize)`, emptied by `clear_all()`."""
    def wrap(fn: F) -> F:
        cached = lru_cache(maxsize=maxsize)(fn)
        _CLEARERS.append(cached.cache_clear)
        return cached  # type: ignore[return-value]
    return wrap


def on_clear(reset: Callable[[], None]) -> Callable[[], None]:
    """Register a function that empties a cache of processed data; returns it unchanged."""
    _CLEARERS.append(reset)
    return reset


def clear_all() -> None:
    """Forget everything read from data/processed, so the next read sees the folder in use."""
    for reset in _CLEARERS:
        reset()
