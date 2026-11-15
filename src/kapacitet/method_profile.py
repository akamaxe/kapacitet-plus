"""A DSO's own static method, as a profile (task 2.5 of the technical review of 14-11-2026, H14).

A DSO compares a new tool with the static study it already trusts. The profile states that study
in the terms config/assumptions.toml already has, so the static capacity the engine computes is the
DSO's, reproduced, beside the hourly study:

    pv_factor          the solar output in the summer snapshot, a share of each AC rating
    load_rule          the household load then: the lowest daytime summer load (min_daytime_summer),
                       none (no_load), or the DSO's fixed figure per customer (fixed_per_customer)
    min_load_factor    with min_daytime_summer, a scaling of that lowest load
    fixed_load_kw      with fixed_per_customer, the figure (it has no default)
    v_rise_limit_pct   the largest voltage rise at a connection point over the same snapshot with
                       nobody added (none: the voltage band alone)
    max_loading_pct    the highest loading of a cable or the transformer (100: the rating)
    source             who set the profile, and when: a profile that changes anything needs one

The default profile changes nothing: it is today's static study. A project keeps its profile in
data/requests/method_profile.json (the API's place to write, beside the DSO's settings), and every
process of that data folder reads it into config's study layer (`profile_layer`), the engine's
worker processes included; a study states the same keys in its study.toml [assumptions], each
with its source, as any other override. A profile changed while a server runs takes effect at
the next computation of the capacity, and in that server once it starts again: the published
static figures and the live checks always come from one profile.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from . import paths

FILE = "method_profile.json"


class MethodProfile(BaseModel):
    pv_factor: float = Field(1.0, ge=0.1, le=1.5, description="solar output in the summer snapshot, × AC rating")
    load_rule: Literal["min_daytime_summer", "no_load", "fixed_per_customer"] = Field(
        "min_daytime_summer", description="the household load of the summer snapshot")
    min_load_factor: float = Field(1.0, ge=0.0, le=3.0, description="with min_daytime_summer: × the lowest load")
    fixed_load_kw: float | None = Field(None, ge=0.0, le=20.0, description="with fixed_per_customer: kW per customer")
    v_rise_limit_pct: float | None = Field(None, gt=0.0, le=10.0,
                                           description="largest voltage rise at a connection point, % (none: the band)")
    max_loading_pct: float = Field(100.0, ge=50.0, le=100.0, description="highest loading of a cable or the transformer")
    source: str = Field("", max_length=200, description="who set the profile, and when")

    @model_validator(mode="after")
    def _complete(self):
        if self.load_rule == "fixed_per_customer" and self.fixed_load_kw is None:
            raise ValueError("a fixed load per customer needs its figure (fixed_load_kw): it has no default")
        if self.overrides() and not self.source.strip():
            raise ValueError("a method profile that changes the static study needs its source (who set it, and when)")
        return self

    def overrides(self) -> dict[str, object]:
        """The assumptions this profile sets away from config/assumptions.toml's values, as
        config.set_overrides takes them: {} for the default profile."""
        from .config import assumptions

        base = assumptions()
        want: dict[str, object] = {"static_pv_factor": float(self.pv_factor), "static_load_rule": self.load_rule,
                                   "static_min_load_factor": float(self.min_load_factor),
                                   "static_v_rise_limit_pct": float(self.v_rise_limit_pct or 0.0),
                                   "static_max_loading_pct": float(self.max_loading_pct)}
        if self.load_rule == "fixed_per_customer":
            want["static_fixed_load_kw"] = float(self.fixed_load_kw or 0.0)
        return {k: v for k, v in want.items() if base[k].value != v}


def path(data_dir: Path | None = None) -> Path:
    return Path(data_dir or paths.DATA) / "requests" / FILE


def load(data_dir: Path | None = None) -> MethodProfile:
    """The profile of the network in `data_dir` (the one in use by default), or the default one."""
    p = path(data_dir)
    if not p.is_file():
        return MethodProfile()
    return MethodProfile.model_validate(json.loads(p.read_text(encoding="utf-8")))


def save(profile: MethodProfile, data_dir: Path | None = None) -> Path:
    from .atomic import write_json_atomic

    p = path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(p, profile.model_dump(mode="json"))
    return p


def profile_layer() -> dict[str, object]:
    """What the profile of the data folder in use sets, for config's study layer; {} without one,
    or with one that cannot be read (the API refuses to write such a file, so only a hand edit
    makes one, and the computation then says nothing has been changed rather than stopping)."""
    try:
        return load().overrides()
    except (OSError, ValueError):
        import logging

        logging.getLogger("kapacitet").warning("%s cannot be read: the static study is computed without it", path())
        return {}
