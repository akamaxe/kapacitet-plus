"""Household load from meter readings, corrected for temperature and laid over every weather year.

Each metered customer's hourly import (processed/meters/hourly.parquet, ingest/meters.py) is fitted as

    L(t) = a[daytype, h] + b[daytype, h] * max(Tb - T(t), 0) + r(t)

with daytype a working day (Monday to Friday) or a weekend day, h the hour of the day in UTC+1, Tb
the base temperature of the heating model (`hp_balance_temp_c`), T the hour's air temperature at
the study's SMHI station, a and b at least zero by non-negative least squares (scipy's nnls), and r
the residual: what the weather does not explain. Every weather year is then synthesised from its
own temperatures, with the residual of the metered day of the same daytype nearest in the calendar:

    L_y(d, h) = a + b * max(Tb - T_y(d, h), 0) + r(m(d), h),   and never below zero

and written as processed/households/metered_<y>.npy (customers by hours; a row of NaN for a customer
without enough readings), which household.customer_base_load takes in place of the CREST pool. The
fit report goes to processed/validation/metered_fit.json.

What the readings are is energy taken from the grid. Behind a meter with PV the daytime import
understates the consumption, so the report marks the customers whose readings show export; and the
load includes whatever electric heating the house already has.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import nnls

from ..config import A, settings
from ..paths import processed, processed_ro
from ..timeindex import interval_mean_from_instants, local_standard, year_index

log = logging.getLogger(__name__)
DAYTYPES = ("workday", "weekend")
MIN_DAYS = 14               # complete metered days a customer needs to be fitted
SCHEMA = "kapacitet.metered-fit/1"


def daytype_of(index: pd.DatetimeIndex) -> np.ndarray:
    """0 for a working day, 1 for Saturday and Sunday, by the day in UTC+1."""
    return (local_standard(index).dayofweek >= 5).astype(int)


def degree(t_air, base_c: float) -> np.ndarray:
    return np.maximum(base_c - np.asarray(t_air, dtype=float), 0.0)


@dataclass
class Fit:
    a: np.ndarray               # (2, 24) kW
    b: np.ndarray               # (2, 24) kW per kelvin below the base temperature
    day_of_year: np.ndarray     # (days,) of the complete metered days, 0-based, in UTC+1
    day_type: np.ndarray        # (days,)
    residual: np.ndarray        # (days, 24) kW
    hours: int
    r2: float
    cv_rmse: float


def fit(load: pd.Series, temperature: pd.Series, base_c: float) -> Fit:
    """`load` in kW per hour on UTC hour starts, `temperature` in °C on the same index; hours
    without either are left out."""
    ok = load.notna().to_numpy() & temperature.reindex(load.index).notna().to_numpy()
    idx = pd.DatetimeIndex(load.index[ok])
    y = load.to_numpy(dtype=float)[ok]
    d = degree(temperature.reindex(load.index).to_numpy(dtype=float)[ok], base_c)
    dt = daytype_of(idx)
    hod = local_standard(idx).hour.to_numpy()
    a, b = np.full((2, 24), np.nan), np.zeros((2, 24))
    for k in range(2):
        for h in range(24):
            m = (dt == k) & (hod == h)
            if m.any():
                coef, _ = nnls(np.column_stack([np.ones(int(m.sum())), d[m]]), y[m])
                a[k, h], b[k, h] = coef
    for k in range(2):                       # a daytype never metered at some hour: the other one's
        empty = np.isnan(a[k])
        a[k, empty], b[k, empty] = a[1 - k, empty], b[1 - k, empty]
    a = np.nan_to_num(a, nan=float(y.mean()) if len(y) else 0.0)
    pred = a[dt, hod] + b[dt, hod] * d
    res = y - pred
    total = float(((y - y.mean()) ** 2).sum()) if len(y) else 0.0
    r2 = 1.0 - float((res ** 2).sum()) / total if total > 0 else float("nan")
    cv = float(np.sqrt((res ** 2).mean()) / y.mean()) if len(y) and y.mean() > 0 else float("nan")

    local = local_standard(idx)
    by_day = pd.DataFrame({"day": local.normalize(), "h": hod, "r": res}).pivot_table(
        index="day", columns="h", values="r", aggfunc="first")
    by_day = by_day.reindex(columns=range(24)).dropna()
    days = pd.DatetimeIndex(by_day.index)
    return Fit(a=a, b=b, day_of_year=(days.dayofyear.to_numpy() - 1), day_type=(days.dayofweek >= 5).astype(int),
               residual=by_day.to_numpy(dtype=float), hours=int(len(y)), r2=r2, cv_rmse=cv)


def aligned_days(f: Fit, index: pd.DatetimeIndex) -> np.ndarray:
    """For each day of `index` (a year_index), the complete metered day of the same daytype nearest
    in the calendar (round the year), or of any daytype when the readings have none of it."""
    n_days = len(index) // 24
    local = local_standard(index[::24])
    doy = local.dayofyear.to_numpy() - 1
    kind = (local.dayofweek >= 5).astype(int)
    gap = np.abs(doy[:, None] - f.day_of_year[None, :])
    gap = np.minimum(gap, 365.25 - gap)                       # round the year
    gap += 1000.0 * (kind[:, None] != f.day_type[None, :])
    return np.argmin(gap, axis=1)[:n_days]


def synthesise(f: Fit, index: pd.DatetimeIndex, temperature, base_c: float) -> np.ndarray:
    """One weather year's hourly load (kW) on `index` (its year_index) from its temperatures."""
    dt = daytype_of(index)
    hod = local_standard(index).hour.to_numpy()
    out = f.a[dt, hod] + f.b[dt, hod] * degree(np.nan_to_num(np.asarray(temperature, dtype=float),
                                                             nan=base_c), base_c)
    if len(f.residual):
        out = out + f.residual[aligned_days(f, index)].reshape(-1)[: len(index)]
    return np.maximum(out, 0.0)


# ---------------------------------------------------------------- a station's readings

def metered_temperature(index: pd.DatetimeIndex) -> pd.Series:
    """The air temperature (°C, hour-beginning means) of each metered hour: the weather years'
    files where they cover it, else the SMHI station's observations in the downloads (the corrected
    archive, then the latest months); NaN where neither has it."""
    from ..ingest import smhi

    out = pd.Series(np.nan, index=index, dtype=float)
    for y in np.unique(local_standard(index).year) if len(index) else ():
        path = processed_ro("weather", f"weather_{int(y)}.parquet")
        if path.is_file():
            t = pd.read_parquet(path, columns=["t_air"])["t_air"].astype(float)
            if len(t) == len(year_index(int(y))):
                t.index = year_index(int(y))              # the file is on the year's index
            out = out.fillna(t.reindex(index))
    missing = out.isna()
    if missing.any():
        obs = smhi.read_metobs_all(settings().temperature_station)
        if len(obs):
            gap = index[missing.to_numpy()]
            full = pd.date_range(gap.min(), gap.max(), freq="h")
            out[missing] = interval_mean_from_instants(obs, full).reindex(gap).to_numpy()
    return out


def _write(path, array: np.ndarray) -> None:
    """Written beside and moved in, so nothing is ever written through a link."""
    tmp = path.with_name(path.name + ".tmp.npy")
    np.save(tmp, array)
    os.replace(tmp, path)


def build(years=None) -> dict:
    """processed/meters/hourly.parquet into processed/households/metered_<y>.npy for every weather
    year and the fit report; returns the report ({} when there are no readings)."""
    from ..grid.builder import load_tables

    src = processed_ro("meters", "hourly.parquet")
    if not src.is_file():
        return {}
    cfg = settings()
    years = tuple(years or cfg.weather_years)
    base_c = float(A("hp_balance_temp_c"))
    _, _, cust = load_tables()
    ids = cust["osm_id"].astype(str).to_numpy()
    hourly = pd.read_parquet(src)
    hourly["start"] = pd.DatetimeIndex(pd.to_datetime(hourly["start"], utc=True))
    temps = metered_temperature(pd.DatetimeIndex(hourly["start"].drop_duplicates().sort_values()))
    weather = {y: pd.read_parquet(processed_ro("weather", f"weather_{y}.parquet"), columns=["t_air"])["t_air"]
               .to_numpy(dtype=float) for y in years}
    arrays = {y: np.full((len(ids), len(year_index(y))), np.nan, dtype=np.float32) for y in years}
    rows, short = [], []
    groups = dict(tuple(hourly.groupby("customer_id", sort=False)))
    for i, cid in enumerate(ids):
        g = groups.get(cid)
        if g is None:
            continue
        load = pd.Series(g["kwh_import"].to_numpy(dtype=float), index=pd.DatetimeIndex(g["start"]))
        f = fit(load, temps.reindex(load.index), base_c)
        entry = {"customer_id": cid, "cid": int(i), "hours": f.hours,
                 "hours_without_temperature": int(load.notna().sum()) - f.hours, "complete_days": int(len(f.residual)),
                 "first": str(load.index.min()), "last": str(load.index.max()),
                 "export_seen": bool(np.nansum(g["kwh_export"].to_numpy(dtype=float)) > 0)}
        if len(f.residual) < MIN_DAYS:
            short.append(cid)
            rows.append(entry | {"fitted": False})
            continue
        synth = {}
        for y in years:
            arrays[y][i] = synthesise(f, year_index(y), weather[y], base_c)
            synth[str(y)] = round(float(arrays[y][i].sum()), 1)
        rows.append(entry | {"fitted": True, "r2": _round(f.r2), "cv_rmse": _round(f.cv_rmse),
                             "base_kw_mean": round(float(f.a.mean()), 4),
                             "kw_per_kelvin_mean": round(float(f.b.mean()), 5),
                             "metered_kwh_per_hour": round(float(load.mean()), 4),
                             "synthesised_kwh": synth})
    for y in years:
        _write(processed("households", f"metered_{y}.npy"), arrays[y])
    fitted = [r for r in rows if r.get("fitted")]
    report = {
        "schema": SCHEMA,
        "model": "L = a[daytype, h] + b[daytype, h] * max(Tb - T, 0) + residual, a and b >= 0 (nnls)",
        "base_temp_c": base_c, "daytypes": list(DAYTYPES), "min_complete_days": MIN_DAYS,
        "temperature_station": cfg.temperature_station, "years": list(years),
        "customers": {"total": int(len(ids)), "fitted": len(fitted), "too_few_days": len(short),
                      "without_readings": int(len(ids) - len(rows)),
                      "export_seen": int(sum(r["export_seen"] for r in rows))},
        "median": {"r2": _round(_median([r["r2"] for r in fitted])),
                   "cv_rmse": _round(_median([r["cv_rmse"] for r in fitted]))},
        "hours_without_temperature": int(sum(r["hours_without_temperature"] for r in rows)),
        "unmatched_meters": int(len(set(groups) - set(ids))),
        "note": ("Fitted on energy taken from the grid: where a customer has PV behind the meter (export_seen), "
                 "the daytime load is understated. The load includes any electric heating the house has. "
                 "A working day is Monday to Friday by local standard time, so Swedish public holidays count as "
                 "working days. Hours without a temperature are left out of the fit "
                 "(hours_without_temperature)."),
        "per_customer": rows,
    }
    processed("validation", "metered_fit.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    log.info("metered load: %d of %d customers fitted, median R² %s", len(fitted), len(ids), report["median"]["r2"])
    return report


def _median(values) -> float:
    values = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.median(values)) if values else float("nan")


def _round(x: float, n: int = 4):
    return round(float(x), n) if x is not None and np.isfinite(x) else None
