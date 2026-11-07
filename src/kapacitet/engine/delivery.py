"""Running the day-ahead envelopes: a day's schedule, a season replayed in the shadow, a meter checked.

Pure functions over what the operation stage wrote (engine/operation.py), so they run in the API
and in tests without the engine:

- `schedule`: a house's 24 limits for one date, each with its interval start in UTC (the study's
  calendar is UTC+1 all year, timeindex.py) and in Stockholm's own clock;
- `go_no_go`: the season replayed day by day, against the two criteria a shadow pilot would be
  judged by: not one hour above a grid limit, and every agreement within its agreed level;
- `meter_check`: a house's measured export against the limits it was issued, counting a breach as
  SA Power Networks' Flexible Exports trial did (more than a tolerance above the limit).
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
    "meter_columns": "the meter file needs a timestamp column and an export_kw (or export_w) column",
    "meter_naive_time": "every timestamp needs its UTC offset, such as +01:00 or Z",
    "meter_outside_year": "no reading falls in the study year {year}",
    "not_an_agreement": "house {cid} has no conditional agreement, so it receives no schedule",
    "no_charger": "house {cid} has no EV charger under an import envelope, so it receives no charging profile",
    "import_unverified": "the issued import envelopes break a limit in {hours} hours, so they are not sent",
    "outside_year": "the date is outside the study year {year}",
}


def day_of(year: int, d: Date) -> int:
    """The study day (0-based) of a calendar date in the study's UTC+1 year."""
    if d.year != year:
        raise ValueError(f"{d.isoformat()} is not in the study year {year}")
    return (d - Date(year, 1, 1)).days


def schedule(limits_kw: np.ndarray, desired_kw: np.ndarray | None, year: int, day: int) -> list[dict]:
    """24 rows for one study day: interval start (UTC, UTC+1, Stockholm), limit and desired export."""
    idx = year_index(year)                  # tz-aware UTC
    study = local_standard(idx)             # the study's own clock (UTC+1 all year)
    rows = []
    for h in range(day * 24, day * 24 + 24):
        utc = idx[h]
        local = utc.tz_convert(STOCKHOLM)
        rows.append({
            "hour": int(h),
            "start_utc": utc.isoformat(),
            "start_epoch": int(utc.timestamp()),
            "study_clock": study[h].strftime("%Y-%m-%d %H:%M"),
            "stockholm": local.strftime("%Y-%m-%d %H:%M %Z"),
            "limit_kw": round(float(limits_kw[h]), 3),
            "desired_kw": None if desired_kw is None else round(float(desired_kw[h]), 3),
        })
    return rows


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


def meter_check(csv_text: str, limits_kw: np.ndarray, year: int, tolerance_w: float) -> dict:
    """Readings of a house's export against the hour's issued limit.

    The file has a `timestamp` column in ISO 8601 with its UTC offset (a reading without one could
    be either clock) and an `export_kw` column (or `export_w`). Each reading is compared with the
    limit of the hour it falls in; a breach is a reading more than `tolerance_w` above it.
    """
    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except Exception as err:  # noqa: BLE001 - any parse failure is the file's
        raise MeterError("meter_unreadable", detail=str(err)[:200]) from err
    if "timestamp" not in df.columns or not ({"export_kw", "export_w"} & set(df.columns)):
        raise MeterError("meter_columns")
    stamps = df["timestamp"].astype(str)
    if not stamps.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True).all():
        raise MeterError("meter_naive_time")
    t = pd.to_datetime(stamps, utc=True, errors="coerce")
    if t.isna().any():
        raise MeterError("meter_unreadable", detail="unparseable timestamps")
    watts = df["export_w"].astype(float) if "export_w" in df.columns else df["export_kw"].astype(float) * 1000.0
    first = year_index(year)[0]
    hour = ((t - first) / pd.Timedelta(hours=1)).astype(float).to_numpy()
    hour = np.floor(hour).astype(int)
    inside = (hour >= 0) & (hour < len(limits_kw))
    if not inside.any():
        raise MeterError("meter_outside_year", year=year)
    limit_w = np.full(len(hour), np.nan)
    limit_w[inside] = np.asarray(limits_kw, dtype=float)[hour[inside]] * 1000.0
    excess = watts.to_numpy() - limit_w
    breach = inside & (excess > tolerance_w)
    rows = [{"timestamp": t.iloc[i].isoformat(), "export_w": round(float(watts.iloc[i]), 1),
             "limit_w": round(float(limit_w[i]), 1), "excess_w": round(float(excess[i]), 1)}
            for i in np.flatnonzero(breach)[:200]]
    n = int(inside.sum())
    return {"readings": n, "outside_year": int((~inside).sum()), "breaches": int(breach.sum()),
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
