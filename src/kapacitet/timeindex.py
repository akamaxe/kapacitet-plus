"""Time conventions.

A study year is the calendar year in Swedish standard time (UTC+1, no daylight saving time),
so every day has 24 hours and hour-of-day views are unambiguous. Timestamps are stored in UTC
and mark the *beginning* of each hourly interval [t, t+1h).
"""

from __future__ import annotations

import calendar

import numpy as np
import pandas as pd

from .config import settings


def hours_in_year(year: int) -> int:
    return 8784 if calendar.isleap(year) else 8760


def year_index(year: int) -> pd.DatetimeIndex:
    """UTC timestamps of the hour-beginnings of `year` in UTC+1 standard time."""
    offset = settings().display_utc_offset_h
    start = pd.Timestamp(f"{year}-01-01 00:00", tz="UTC") - pd.Timedelta(hours=offset)
    return pd.date_range(start, periods=hours_in_year(year), freq="h")


def local_standard(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Convert UTC timestamps to naive Swedish standard time (UTC+1)."""
    offset = settings().display_utc_offset_h
    return (index + pd.Timedelta(hours=offset)).tz_localize(None)


def hour_of_day(index: pd.DatetimeIndex) -> np.ndarray:
    return local_standard(index).hour.to_numpy()


def day_of_year(index: pd.DatetimeIndex) -> np.ndarray:
    return local_standard(index).dayofyear.to_numpy() - 1


def interval_mean_from_instants(series: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Hour-beginning interval means from instantaneous samples at the hour marks.

    For samples x(t) at every full hour, the mean over [t, t+1h) is approximated by the
    trapezoid (x(t) + x(t+1h)) / 2. Gaps of up to 3 hours are linearly interpolated first.
    """
    s = series.sort_index()
    s = s[~s.index.duplicated(keep="first")]
    full = pd.date_range(index[0] - pd.Timedelta(hours=3), index[-1] + pd.Timedelta(hours=4), freq="h")
    s = s.reindex(full).interpolate(limit=3, limit_area="inside")
    mean = (s + s.shift(-1)) / 2.0
    return mean.reindex(index)
