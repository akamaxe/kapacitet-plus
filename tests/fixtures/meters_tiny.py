"""Quarter-hour meter readings for the tests, made at test time.

A few customers over the two days the clocks change in 2025, 30 March (23 hours on the wall) and
26 October (25), each with the days around it, written in Swedish wall-clock time with the UTC
offset of each instant: the way a metering head-end exports them. Every value is a function of
the instant alone, so a test can say what any hour must sum to. `frame` gives the table, `write`
puts it in a CSV file, in RFC 4180 or in the Swedish Excel dialect (`;`, decimal comma).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TZ = "Europe/Stockholm"
DST_DAYS = ("2025-03-30", "2025-10-26")
CUSTOMERS = ("C1", "C2", "C3")


def instants(days=DST_DAYS, around: int = 1) -> pd.DatetimeIndex:
    """The quarter-hours (UTC) of the Swedish days `days`, each with `around` days either side."""
    parts = []
    for day in days:
        first = (pd.Timestamp(day) - pd.Timedelta(days=around)).tz_localize(TZ)
        last = (pd.Timestamp(day) + pd.Timedelta(days=around + 1)).tz_localize(TZ)
        parts.append(pd.date_range(first.tz_convert("UTC"), last.tz_convert("UTC"), freq="15min", inclusive="left"))
    return parts[0].append(parts[1:]) if len(parts) > 1 else parts[0]


def kwh(customer: int, t: pd.DatetimeIndex) -> np.ndarray:
    """The energy of a quarter-hour: a function of the customer and the instant alone."""
    q = (t.asi8 // 900_000_000_000) % 96
    return np.round(0.05 + 0.01 * customer + 0.002 * (q % 24), 4)


def stamp(t: pd.DatetimeIndex) -> list[str]:
    """ISO 8601 in Swedish wall-clock time with its offset, as a head-end writes it."""
    return [x.isoformat() for x in t.tz_convert(TZ)]


def frame(customers=CUSTOMERS, days=DST_DAYS, around: int = 1, export: bool = False,
          voltages: bool = False, id_column: str = "customer_id") -> pd.DataFrame:
    t = instants(days, around)
    rows = []
    for i, cid in enumerate(customers):
        df = pd.DataFrame({id_column: cid, "start": stamp(t), "kwh_import": kwh(i, t)})
        if export:
            df["kwh_export"] = np.where((t.hour >= 9) & (t.hour < 14), 0.1, 0.0)
        if voltages:
            for k, name in enumerate(("v_l1", "v_l2", "v_l3")):
                df[name] = np.round(230.0 + k + 0.5 * i, 1)
        rows.append(df)
    return pd.concat(rows, ignore_index=True)


def write(folder: str | Path, df: pd.DataFrame | None = None, name: str = "readings.csv",
          excel: bool = False) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    df = frame() if df is None else df
    path = folder / name
    if excel:
        df.to_csv(path, sep=";", decimal=",", index=False, encoding="utf-8-sig")
    else:
        df.to_csv(path, index=False)
    return path
