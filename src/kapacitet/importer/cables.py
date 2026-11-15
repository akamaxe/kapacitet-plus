"""Swedish low-voltage cable types, and how an export's network information system names them.

A NIS names its cables its own way ("AXQJ 4x150/41", "N1XE-AR 4G150"), which pandapower's library does
not know (H1 of the technical review of 14-11-2026). Two things meet that:

- the **catalogue**, `config/cable_types_se.csv`: common Swedish LV cables with their resistance,
  reactance, capacitance and current rating, each row with the manufacturer's document it comes from,
  its address and the day it was read. A type without a source does not enter (`catalogue` refuses it);
- the optional table **`cable_types.csv`** of an export: each name of `lines.csv`'s std_type translated to
  a type of the catalogue, or to its own electrical data.

`resolve` gives the electrical data a name stands for, the export's translation first, then the
catalogue; pandapower's library is the build's own last resort, and a name none of them knows is an
error of the check that names it.
"""

from __future__ import annotations

import csv
import math

import pandas as pd

from ..cache import memo
from ..paths import CONFIG

CATALOGUE = CONFIG / "cable_types_se.csv"
FIELDS = ("r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka")
SOURCE = ("source", "url", "accessed")


class CatalogueError(ValueError):
    """config/cable_types_se.csv has a row that cannot be used."""


@memo(maxsize=1)
def catalogue() -> dict[str, dict]:
    """The catalogue by type: its electrical data as numbers, its rating and its source. A row
    without its source, its address or the day it was read is refused, as is one without a number."""
    out: dict[str, dict] = {}
    with open(CATALOGUE, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("type") or "").strip()
            if not name:
                continue
            missing = [k for k in SOURCE if not (row.get(k) or "").strip()]
            if missing:
                raise CatalogueError(f"{name}: without its {', '.join(missing)} a type does not enter the catalogue")
            try:
                values = {k: float(row[k]) for k in FIELDS}
            except (KeyError, TypeError, ValueError):
                raise CatalogueError(f"{name}: r, x, c and max_i are numbers") from None
            if values["r_ohm_per_km"] <= 0 or values["max_i_ka"] <= 0:
                raise CatalogueError(f"{name}: r and max_i are positive")
            out[name] = {**{k: (v or "").strip() for k, v in row.items()}, **values}
    return out


def _number(value) -> float | None:
    try:
        v = float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def translation(data: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """The export's cable_types.csv as {name: {type, r_ohm_per_km, ...}}: a row naming a catalogue type
    takes its data (a value the row gives itself wins), a row without one its own data; empty
    without the table. Rows that are neither are left out here; the check names them."""
    table = data.get("cable_types")
    if table is None or "name" not in table.columns:
        return {}
    known = catalogue()
    out: dict[str, dict] = {}
    for r in table.itertuples(index=False):
        name = str(getattr(r, "name", "") or "").strip()
        if not name or name.lower() == "nan":
            continue
        kind = str(getattr(r, "type", "") or "").strip()
        base = dict(known[kind]) if kind in known else {}
        own = {k: _number(getattr(r, k, None)) for k in FIELDS}
        values = {k: own[k] if own[k] is not None else base.get(k) for k in FIELDS}
        if all(values[k] is not None for k in FIELDS):
            out[name] = {"type": kind if kind in known else name, **values}
    return out


def resolve(name: str, mapping: dict[str, dict]) -> tuple[str, dict] | None:
    """(the type it is built as, its electrical data) for a std_type name: the export's translation
    first, then the catalogue; None when neither knows it."""
    key = name.strip()
    if key in mapping:
        m = mapping[key]
        return m["type"], {k: m[k] for k in FIELDS}
    known = catalogue()
    if key in known:
        return key, {k: known[key][k] for k in FIELDS}
    return None
