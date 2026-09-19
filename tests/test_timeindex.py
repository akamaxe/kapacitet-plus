"""Time conventions: a study year is a calendar year in Swedish standard time (UTC+1)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kapacitet.timeindex import (
    day_of_year,
    hour_of_day,
    hours_in_year,
    interval_mean_from_instants,
    local_standard,
    year_index,
)


def test_year_has_24_hour_days_and_starts_at_utc_23():
    idx = year_index(2025)
    assert len(idx) == hours_in_year(2025) == 8760
    assert str(idx[0]) == "2024-12-31 23:00:00+00:00"       # 1 Jan 00:00 in UTC+1
    assert (np.diff(idx.to_numpy()).astype("timedelta64[m]") == np.timedelta64(60, "m")).all()
    assert len(year_index(2024)) == 8784                     # leap year


def test_local_views_cover_every_hour_of_every_day():
    idx = year_index(2025)
    hod, doy = hour_of_day(idx), day_of_year(idx)
    assert hod[0] == 0 and hod[-1] == 23
    assert doy[0] == 0 and doy[-1] == 364
    counts = np.bincount(hod, minlength=24)
    assert (counts == 365).all()                             # no DST gap or repeat
    assert local_standard(idx)[0] == pd.Timestamp("2025-01-01 00:00")


def test_interval_mean_is_the_trapezoid_of_the_instants():
    idx = year_index(2025)[:5]
    samples = pd.Series(np.arange(6.0) * 10.0, index=year_index(2025)[:6])
    got = interval_mean_from_instants(samples, idx)
    assert np.allclose(got.to_numpy(), [5.0, 15.0, 25.0, 35.0, 45.0])


def test_short_gaps_are_interpolated_but_long_ones_are_not():
    full = year_index(2025)[:8]
    values = pd.Series([0.0, 10.0, np.nan, np.nan, 40.0, 50.0, 60.0, 70.0], index=full).dropna()
    got = interval_mean_from_instants(values, full[:6])
    assert not np.isnan(got.to_numpy()[:5]).any()            # 2-hour gap filled

    sparse = pd.Series([0.0, 10.0, 90.0], index=full[[0, 1, 7]])
    got2 = interval_mean_from_instants(sparse, full[:6])
    assert np.isnan(got2.to_numpy()).any()                   # gaps beyond 3 hours stay empty
