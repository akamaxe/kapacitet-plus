"""The DSO's own settings: the choices an operator makes, kept per data folder and sealed on change.

They live in data/requests/settings.json, the one folder the API writes. A change is validated,
written atomically (a temporary file, then a replace) and sealed with what it was before and
after, so the evidence trail says which rule was in force when.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import seals
from .paths import DATA

POLICIES = ("maxmin", "prorata", "propfair", "maxtotal")
_LOCK = threading.Lock()


class DsoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # how curtailment is shared between the houses on conditional agreements (engine/doe.py)
    allocation_policy: Literal["maxmin", "prorata", "propfair", "maxtotal"] = "maxmin"
    # the most curtailment an agreement promises, as a share of the house's solar energy in a year;
    # by default the budget the capacity search itself is held to (z_star_pct)
    agreed_level_pct: float = Field(default_factory=lambda: float(_z_star()), ge=0, le=100)


def _z_star() -> float:
    from .config import A

    return A("z_star_pct")


class DsoSettingsChange(BaseModel):
    """A partial change: only the fields given are changed."""

    model_config = ConfigDict(extra="forbid")

    allocation_policy: Literal["maxmin", "prorata", "propfair", "maxtotal"] | None = None
    agreed_level_pct: float | None = Field(None, ge=0, le=100)


def path():
    return DATA / "requests" / "settings.json"


def load() -> DsoSettings:
    """The settings in force; the defaults when none were ever saved or the file is unreadable."""
    p = path()
    if not p.exists():
        return DsoSettings()
    try:
        return DsoSettings.model_validate(json.loads(p.read_text(encoding="utf-8")))
    except (ValueError, ValidationError):
        return DsoSettings()


def update(change: DsoSettingsChange) -> DsoSettings:
    """Apply a change, write it atomically and seal before and after."""
    with _LOCK:
        before = load()
        after = DsoSettings.model_validate({**before.model_dump(), **change.model_dump(exclude_none=True)})
        if after == before:
            return before
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(after.model_dump(), indent=1), encoding="utf-8")
        os.replace(tmp, p)
        stamp = datetime.now(timezone.utc)
        seals.seal("settings", stamp.strftime("%Y%m%dT%H%M%S%fZ"),
                   {"changed_at": stamp.isoformat(), "before": before.model_dump(), "after": after.model_dump()})
        return after
