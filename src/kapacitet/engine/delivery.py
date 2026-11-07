"""Running the day-ahead envelopes: a day's schedule, a season replayed in the shadow, a meter checked.

Pure functions over what the operation stage wrote (engine/operation.py), so they run in the API
and in tests without the engine:

- `schedule`: a house's limits for one date, each with its interval start in UTC and on
  Stockholm's own clock, in the step the schedule is issued in: 60 minutes gives the study day's
  24 hours (UTC+1 all year, timeindex.py); 15 minutes gives the market's delivery day, midnight to
  midnight on Stockholm's clock, as the day-ahead market (SDAC) trades it since its 15-minute MTU
  of 1 October 2025: 96 quarter-hours, 92 on the day the clocks go forward and 100 on the day they
  go back. The envelopes are verified hour by hour, so where the inputs are hourly (the demo) each
  quarter carries its hour's envelope and the schedule says so (`terms`, `inputs_resolution_min`);
  a load side metered at 15 minutes (a study's meters, M10c) enters as a quarter-hour series of
  the export the house would make. `within_envelope` checks a schedule hour by hour against the
  verified envelope;
- `go_no_go`: the season replayed day by day, against the two criteria a shadow pilot would be
  judged by: not one hour above a grid limit, and every agreement within its agreed level;
- `meter_check`: a house's measured export against the limits it was issued, interval by interval
  of the schedule, counting a breach as SA Power Networks' Flexible Exports trial did (more than a
  tolerance above the limit). It reads power at any step and quarter-hour energy (`export_kwh`),
  as a Swedish meter reports it.
"""

from __future__ import annotations

import io
from datetime import date as Date
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..timeindex import local_standard, year_index

STOCKHOLM = ZoneInfo("Europe/Stockholm")

# what the interface says when a request about operation cannot be answered (err.<code> in i18n)
ERRORS = {
    "meter_unreadable": "the meter file could not be read: {detail}",
    "meter_columns": "the meter file needs a timestamp column and an export_kw, export_w or export_kwh column",
    "meter_kwh_grid": "export_kwh is the energy of the quarter-hour a timestamp starts, so every timestamp "
                      "must start a quarter-hour, and {example} does not",
    "meter_naive_time": "every timestamp needs its UTC offset, such as +01:00 or Z",
    "meter_outside_year": "no reading falls in the study year {year}",
    "not_an_agreement": "house {cid} has no conditional agreement, so it receives no schedule",
    "no_charger": "house {cid} has no EV charger under an import envelope, so it receives no charging profile",
    "import_unverified": "the issued import envelopes break a limit in {hours} hours, so they are not sent",
    "outside_year": "the date is outside the study year {year}",
    "resolution": "a schedule is issued in {allowed} minutes, not {minutes}",
}


def day_of(year: int, d: Date) -> int:
    """The study day (0-based) of a calendar date in the study's UTC+1 year."""
    if d.year != year:
        raise ValueError(f"{d.isoformat()} is not in the study year {year}")
    return (d - Date(year, 1, 1)).days


RESOLUTIONS = (15, 60)
# the step the capacity and the envelopes are verified at, whatever a schedule is issued in
VERIFIED_RESOLUTION_MIN = 60


def resolution(value: int | None = None) -> int:
    """The minutes a schedule is issued in: `value` when given, else `schedule_resolution_min`."""
    from ..config import A

    r = int(A("schedule_resolution_min") if value is None else value)
    if r not in RESOLUTIONS:
        raise ValueError(f"a schedule is issued in {' or '.join(map(str, RESOLUTIONS))} minutes, not {r}")
    return r


def delivery_window(year: int, day: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The market's delivery day for the study day's date, as its first and its end instant in
    UTC: midnight to midnight on Stockholm's clock (23, 24 or 25 hours)."""
    d = pd.Timestamp(year=year, month=1, day=1) + pd.Timedelta(days=int(day))
    start = d.tz_localize(STOCKHOLM)
    end = (d + pd.Timedelta(days=1)).tz_localize(STOCKHOLM)
    return start.tz_convert("UTC"), end.tz_convert("UTC")


def _per_quarter(series, H: int) -> bool:
    return series is not None and len(series) == 4 * H


def schedule(limits_kw: np.ndarray, desired_kw: np.ndarray | None, year: int, day: int,
             resolution_min: int = 60) -> list[dict]:
    """One row per interval of a day: its start (UTC, UTC+1, Stockholm), its length, the limit and
    the export the house would make without one.

    `limits_kw` are the year's hourly verified envelopes. `desired_kw` is hourly too, or one value
    per quarter-hour of the year (4 x the hours) where the load side was metered at 15 minutes. At
    60 minutes the rows are the study day's 24 hours; at 15 minutes, the quarter-hours of the
    delivery day on Stockholm's clock, each with the envelope of the hour it falls in."""
    idx = year_index(year)                  # tz-aware UTC
    study = local_standard(idx)             # the study's own clock (UTC+1 all year)
    H = len(limits_kw)
    quarterly = _per_quarter(desired_kw, H)
    if resolution_min == 60:
        if quarterly:
            # an hour's export is the mean of its four quarters
            desired_kw = np.asarray(desired_kw, dtype=float).reshape(H, 4).mean(axis=1)
        rows = []
        for h in range(day * 24, day * 24 + 24):
            utc = idx[h]
            local = utc.tz_convert(STOCKHOLM)
            rows.append({
                "hour": int(h),
                "start_utc": utc.isoformat(),
                "start_epoch": int(utc.timestamp()),
                "duration_s": 3600,
                "study_clock": study[h].strftime("%Y-%m-%d %H:%M"),
                "stockholm": local.strftime("%Y-%m-%d %H:%M %Z"),
                "limit_kw": round(float(limits_kw[h]), 3),
                "desired_kw": None if desired_kw is None else round(float(desired_kw[h]), 3),
            })
        return rows
    if resolution_min != 15:
        raise ValueError(f"a schedule is issued in 15 or 60 minutes, not {resolution_min}")
    start, end = delivery_window(year, day)
    starts = pd.date_range(start, end, freq="15min", inclusive="left")
    quarters = ((starts - idx[0]) // pd.Timedelta(minutes=15)).to_numpy().astype(int)
    if quarters[0] < 0 or quarters[-1] >= 4 * H:
        raise ValueError("the delivery day reaches outside the study year")
    shift = study[0] - idx[0].tz_localize(None)          # UTC -> the study's clock
    rows = []
    for t, k in zip(starts, quarters):
        h = int(k // 4)
        if desired_kw is None:
            want = None
        else:
            want = round(float(desired_kw[k] if quarterly else desired_kw[h]), 3)
        rows.append({
            "hour": h,
            "quarter": int(k),
            "start_utc": t.isoformat(),
            "start_epoch": int(t.timestamp()),
            "duration_s": 900,
            "study_clock": (t.tz_localize(None) + shift).strftime("%Y-%m-%d %H:%M"),
            "stockholm": t.tz_convert(STOCKHOLM).strftime("%Y-%m-%d %H:%M %Z"),
            "limit_kw": round(float(limits_kw[h]), 3),
            "desired_kw": want,
        })
    return rows


def terms(resolution_min: int, desired_kw: np.ndarray | None = None, H: int | None = None) -> dict:
    """What a schedule says about its own time step: the step it is issued in, the step of its
    inputs (the coarsest: the envelopes are hourly, so 60 wherever they are used, each quarter
    carrying its hour's), the step its envelopes were verified at and the day it covers."""
    desired_min = None if desired_kw is None else (15 if H is not None and _per_quarter(desired_kw, H) else 60)
    return {
        "resolution_min": int(resolution_min),
        "interval_s": 60 * int(resolution_min),
        "inputs_resolution_min": VERIFIED_RESOLUTION_MIN,
        "inputs": {"limit_kw": VERIFIED_RESOLUTION_MIN, "desired_kw": desired_min},
        "verified_resolution_min": VERIFIED_RESOLUTION_MIN,
        "day": "study" if int(resolution_min) == 60 else "delivery",
        "day_clock": "UTC+1" if int(resolution_min) == 60 else "Europe/Stockholm",
    }


def within_envelope(rows: list[dict], limits_kw: np.ndarray) -> dict:
    """A schedule checked hour by hour against the verified envelope: in no hour does any of its
    intervals carry a limit above the hour's envelope (so the lowest quarter does not either)."""
    hours: dict[int, list[float]] = {}
    for r in rows:
        hours.setdefault(int(r["hour"]), []).append(float(r["limit_kw"]))
    above = sorted(h for h, v in hours.items() if max(v) > round(float(limits_kw[h]), 3) + 1e-9)
    return {"hours": len(hours), "intervals": len(rows), "hours_above": len(above), "ok": not above,
            "first_above": above[0] if above else None}


def season_days(year: int, months: tuple[int, int]) -> np.ndarray:
    """Study days whose (UTC+1) date falls in the months, inclusive."""
    start = pd.Timestamp(year=year, month=1, day=1)
    days = pd.date_range(start, periods=len(year_index(year)) // 24, freq="D")
    return np.flatnonzero((days.month >= months[0]) & (days.month <= months[1]))


def share_pct(curtailed, potential) -> np.ndarray:
    """Curtailed energy as a percentage of the potential, element by element; 0 where the potential
    is 0 (a house that produced nothing lost nothing). The share an agreement is held to, here and
    in the capacity search (run.curtailment, forecast.multiyear)."""
    c = np.asarray(curtailed, dtype=float)
    p = np.asarray(potential, dtype=float)
    return np.divide(c, p, out=np.zeros(np.broadcast(c, p).shape), where=p > 0) * 100.0


def go_no_go(days: dict, per_house: dict, season: np.ndarray, agreed_level_pct: float) -> dict:
    """The season's two criteria, and each agreement's share over the whole replayed year.

    `days` is days.json (per-day series), `per_house` days.npz (curtailed, pv per house and day).
    The level is a share of the house's solar energy over the year, the measure the capacity search
    itself is held to; the season is reported beside it.
    """
    viol = np.asarray(days["violation_hours"], dtype=int)
    curtailed = np.asarray(per_house["curtailed"], dtype=float)
    pv = np.asarray(per_house["pv"], dtype=float)
    year_share = share_pct(curtailed.sum(axis=1), pv.sum(axis=1))
    season_share = share_pct(curtailed[:, season].sum(axis=1), pv[:, season].sum(axis=1))
    within = year_share <= agreed_level_pct + 1e-9
    safety = int(viol[season].sum())
    return {
        "season_days": int(len(season)),
        "violation_hours": safety,
        "safety_ok": safety == 0,
        "agreed_level_pct": float(agreed_level_pct),
        "agreements": int(len(pv)),
        "within_level": int(within.sum()),
        "level_ok": bool(within.all()),
        "go": bool(safety == 0 and within.all()),
        "worst_year_share_pct": round(float(year_share.max()), 2) if len(pv) else 0.0,
        "median_year_share_pct": round(float(np.median(year_share)), 2) if len(pv) else 0.0,
        "worst_season_share_pct": round(float(season_share.max()), 2) if len(pv) else 0.0,
        "per_house": [{"customer_id": int(c), "year_share_pct": round(float(y), 2), "season_share_pct": round(float(s), 2),
                       "within": bool(w)} for c, y, s, w in zip(per_house["cids"], year_share, season_share, within)],
    }


class MeterError(ValueError):
    """A meter file the check cannot read honestly."""

    def __init__(self, code: str, **params):
        super().__init__(ERRORS[code].format(**params))
        self.code = code
        self.params = params


def meter_check(csv_text: str, limits_kw: np.ndarray, year: int, tolerance_w: float,
                resolution_min: int = 60) -> dict:
    """Readings of a house's export against the limit of the schedule interval each falls in.

    The file has a `timestamp` column in ISO 8601 with its UTC offset (a reading without one could
    be either clock) and one of: `export_kw` or `export_w`, the power at that instant (at any step,
    five minutes or a quarter-hour), or `export_kwh`, the energy of the quarter-hour the timestamp
    starts, as a meter reports it, compared as its mean power (4 x the kWh). Each reading is
    compared with the limit of its interval of `resolution_min` minutes (each quarter carries its
    hour's envelope); a breach is a reading more than `tolerance_w` above it.
    """
    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except Exception as err:  # noqa: BLE001 - any parse failure is the file's
        raise MeterError("meter_unreadable", detail=str(err)[:200]) from err
    if "timestamp" not in df.columns or not ({"export_kw", "export_w", "export_kwh"} & set(df.columns)):
        raise MeterError("meter_columns")
    stamps = df["timestamp"].astype(str)
    if not stamps.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True).all():
        raise MeterError("meter_naive_time")
    t = pd.to_datetime(stamps, utc=True, errors="coerce")
    if t.isna().any():
        raise MeterError("meter_unreadable", detail="unparseable timestamps")
    first = year_index(year)[0]
    minutes = ((t - first) / pd.Timedelta(minutes=1)).astype(float).to_numpy()
    if "export_w" in df.columns:
        watts, reading = df["export_w"].astype(float), "power"
    elif "export_kw" in df.columns:
        watts, reading = df["export_kw"].astype(float) * 1000.0, "power"
    else:
        off = np.flatnonzero(np.mod(minutes, 15) != 0)
        if len(off):
            raise MeterError("meter_kwh_grid", example=stamps.iloc[int(off[0])])
        watts, reading = df["export_kwh"].astype(float) * 4000.0, "energy_15min"   # kWh in 15 min -> mean W
    step = int(resolution_min)
    interval = np.floor(minutes / step).astype(int)
    hour = (interval * step) // 60
    inside = (minutes >= 0) & (hour < len(limits_kw))
    if not inside.any():
        raise MeterError("meter_outside_year", year=year)
    limit_w = np.full(len(hour), np.nan)
    limit_w[inside] = np.asarray(limits_kw, dtype=float)[hour[inside]] * 1000.0
    excess = watts.to_numpy() - limit_w
    breach = inside & (excess > tolerance_w)
    rows = [{"timestamp": t.iloc[i].isoformat(),
             "interval_start": (first + pd.Timedelta(minutes=int(interval[i]) * step)).isoformat(),
             "export_w": round(float(watts.iloc[i]), 1),
             "limit_w": round(float(limit_w[i]), 1), "excess_w": round(float(excess[i]), 1)}
            for i in np.flatnonzero(breach)[:200]]
    n = int(inside.sum())
    return {"readings": n, "outside_year": int((~inside).sum()), "breaches": int(breach.sum()),
            "reading": reading, "resolution_min": step,
            "intervals": int(len(np.unique(interval[inside]))),
            "breached_intervals": int(len(np.unique(interval[breach]))),
            "compliance_pct": round(100.0 * (1 - breach.sum() / n), 3) if n else None,
            "max_excess_w": round(float(np.nanmax(excess[inside])), 1) if n else None,
            "tolerance_w": float(tolerance_w), "breach_rows": rows}


def meter_example(limits_kw: np.ndarray, desired_kw: np.ndarray, year: int, day: int, seed: int = 7) -> str:
    """A synthetic day of 5-minute readings for one house: an inverter that holds its export at or
    under the issued limit, with measurement noise, except for ten minutes (two readings, 400 W
    above) in the hour the limit held the house back most, so the check has exactly one breach to
    find, and finds it where a limit was doing its job. Labelled synthetic wherever it is offered."""
    rng = np.random.default_rng(seed)
    first = year_index(year)[0]
    hours = np.arange(day * 24, day * 24 + 24)
    limit = np.asarray(limits_kw, dtype=float)[hours]
    wanted = np.asarray(desired_kw, dtype=float)[hours]
    export = np.minimum(wanted, limit)
    held = wanted - limit
    # a day the limit never bound still gets its breach, at its sunniest hour
    at = int(np.argmax(held)) if (held > 1e-6).any() else int(np.argmax(export))
    lines = ["timestamp,export_kw"]
    for j, h in enumerate(hours):
        for k in range(12):
            t = first + pd.Timedelta(hours=int(h)) + pd.Timedelta(minutes=5 * k)
            v = min(limit[j], max(0.0, export[j] * (1 + rng.normal(0, 0.02))))
            if j == at and k in (5, 6):
                v = limit[j] + 0.4
            lines.append(f"{t.isoformat()},{v:.3f}")
    return "\n".join(lines) + "\n"
