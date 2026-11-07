"""Who is acting: the signed-in person a request runs for, when sign-in is on (kapacitet.auth).

The API's guard sets it for each request it lets through (api/guard.py), and the seals and the
queue read it: every seal line made while it is set carries `actor`, and a decision keeps it. A
thread does not inherit it, so work handed to one (a decision, an assessment's save) carries it
over with `acting_as`.

An actor is `{"sub", "name", "role"}`: the identity provider's subject identifier, the display name
it gave, and the role the action was allowed under. It is employee data of the DSO (docs/sso.md),
kept with the records it signs and nowhere else. With sign-in off there is no actor, and nothing
records one.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

ACTOR: contextvars.ContextVar[dict | None] = contextvars.ContextVar("kapacitet_actor", default=None)


def current() -> dict | None:
    """The actor of the request or the work in hand, or None (sign-in off, a background rule)."""
    who = ACTOR.get()
    return dict(who) if who else None


@contextmanager
def acting_as(who: dict | None):
    """Run a block as `who` (None leaves it without an actor), restoring what was there before."""
    token = ACTOR.set(dict(who) if who else None)
    try:
        yield
    finally:
        ACTOR.reset(token)
