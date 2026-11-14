"""Which parts of the product an installation shows (task 1.4 of the technical review of 14-11-2026).

What sells is the hourly capacity of a low-voltage network, for generation and for consumption,
and the pre-check of a connection request against it. Four parts stay in the code, tested, and are
switched on where they are wanted:

- `envelopes`: dynamic operating envelopes and everything that operates them: the Operation screen,
  the annual report, the envelope scenario of the map and of a request, the business case of a
  deferral, the forecast and intraday sections. Off by default because what can be issued never
  beat the hourly study in any network measured (docs/envelopes-by-network.md); the code stays.
- `national`: the Sweden screen, the national figures.
- `scenarios`: the 2030 scenarios, whose growth rates are the team's assumptions without a source.
- `portal`: the applicant's portal (#/apply), its applications and its brand.

All four are off unless KAPACITET_FEATURES names them, comma separated ("envelopes,portal"), or says
"all"; KAPACITET_ENVELOPES=1 switches the first on alone. The variables are read on every call, so
a server and its tests see a change at once; /health and /meta say which parts are on, and a route
of a part that is off answers 404 with the code `feature_off`.
"""

from __future__ import annotations

import os

FEATURES = ("envelopes", "national", "scenarios", "portal")
_TRUE = frozenset({"1", "true", "yes", "on"})


def enabled() -> dict[str, bool]:
    """Every part and whether it is on."""
    named = {x.strip().lower() for x in os.environ.get("KAPACITET_FEATURES", "").split(",") if x.strip()}
    on_ = set(FEATURES) if "all" in named else named & set(FEATURES)
    if os.environ.get("KAPACITET_ENVELOPES", "").strip().lower() in _TRUE:
        on_.add("envelopes")
    return {name: name in on_ for name in FEATURES}


def unknown() -> list[str]:
    """Names in KAPACITET_FEATURES that are no part: a typo switches nothing on, and `instance
    check` says so."""
    named = [x.strip().lower() for x in os.environ.get("KAPACITET_FEATURES", "").split(",") if x.strip()]
    return [n for n in named if n != "all" and n not in FEATURES]


def on(name: str) -> bool:
    if name not in FEATURES:
        raise KeyError(name)
    return enabled()[name]


def require(name: str):
    """A FastAPI dependency for a route of part `name`: 404 `feature_off` while that part is off."""
    if name not in FEATURES:
        raise KeyError(name)

    def dependency() -> None:
        if not on(name):
            from .api.main import api_error        # at call time: the API is loaded by then

            raise api_error(404, "feature_off", feature=name)
    dependency.__name__ = f"requires_{name}"
    return dependency
