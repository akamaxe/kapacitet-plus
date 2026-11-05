"""Meter readings (ingest/meters.py): read with DuckDB, checked, and summed to the hours of the year index.

Everything runs on tests/fixtures/meters_tiny.py, quarter-hour readings over the two days the clocks
change in 2025, written in Swedish wall-clock time with the offset of each instant."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fixtures import meters_tiny as mt
from kapacitet.ingest import meters
from kapacitet.timeindex import year_index


def run(tmp_path, df, keep=None, known=None, excel=False, out=True):
    folder = tmp_path / "in"
    mt.write(folder, df, excel=excel)
    return meters.ingest(meters.meter_files(folder), tmp_path / "out" if out else None, keep=keep, known=known)


def codes(report, severity=None):
    return {f.code for f in report.findings if severity is None or f.severity == severity}


def expected_hours(customer: int, days=mt.DST_DAYS, around=1) -> pd.Series:
    """What every complete hour has to sum to, from the fixture's own values."""
    t = mt.instants(days, around)
    q = pd.Series(mt.kwh(customer, t), index=t)
    return q.groupby(q.index.floor("h")).sum()


def test_the_days_the_clocks_change_have_24_hours_on_the_year_index(tmp_path):
    report, hourly = run(tmp_path, mt.frame())
    assert report.usable, report.text()
    assert not codes(report) & {"meter_dst", "meter_duplicate", "meter_duplicate_same", "meter_off_grid"}
    c1 = hourly[hourly["customer_id"] == "C1"].set_index("start")
    exp = expected_hours(0)
    assert len(c1) == len(exp)
    np.testing.assert_allclose(c1["kwh_import"].to_numpy(), exp.reindex(c1.index).to_numpy(), atol=1e-9)

    # on the year index (UTC+1, interval start) both days have 24 hours, though the wall clock had 23 and 25
    idx = year_index(2025)
    for day in mt.DST_DAYS:
        n = pd.Timestamp(day).dayofyear - 1
        hours = set(range(24 * n, 24 * n + 24))
        got = set(hourly.loc[(hourly["customer_id"] == "C2") & (hourly["year"] == 2025), "hour"])
        assert hours <= got, day
        assert (idx[24 * n] == pd.Timestamp(day, tz="UTC") - pd.Timedelta(hours=1))
    # the hour column is the position on year_index
    row = c1.iloc[5]
    assert idx[int(row["hour"])] == c1.index[5]
    assert (tmp_path / "out" / "hourly.parquet").is_file()


def test_the_wall_clock_days_are_23_and_25_hours_long_in_the_files():
    """The fixture itself: what a head-end writes on those days."""
    df = mt.frame(customers=("C1",), around=0)
    local_days = pd.Series(df["start"].str[:10]).value_counts()
    assert local_days["2025-03-30"] == 23 * 4 and local_days["2025-10-26"] == 25 * 4
    assert df["start"].str.endswith("+02:00").any() and df["start"].str.endswith("+01:00").any()


def test_a_time_without_its_offset_is_refused(tmp_path):
    df = mt.frame()
    df.loc[10, "start"] = df.loc[10, "start"][:19]                 # 2025-03-29T02:30:00, which instant?
    report, hourly = run(tmp_path, df)
    assert not report.usable and hourly is None
    finding = next(f for f in report.findings if f.code == "meter_naive_time")
    assert finding.severity == "error" and finding.count == 1 and "+" not in finding.params["ts"]


def test_three_of_four_quarters_is_not_an_hour(tmp_path):
    df = mt.frame(customers=("C1",))
    t = mt.instants()
    gone = t[40]                                                    # one quarter of one hour
    df = df[pd.to_datetime(df["start"], utc=True) != gone]
    report, hourly = run(tmp_path, df)
    assert report.usable
    starts = set(pd.DatetimeIndex(hourly["start"]))
    assert gone.floor("h") not in starts
    assert gone.floor("h") - pd.Timedelta(hours=1) in starts and gone.floor("h") + pd.Timedelta(hours=1) in starts
    gaps = next(f for f in report.findings if f.code == "meter_gaps")
    # the one quarter, and the months between the two windows of the fixture
    assert gaps.count >= 1


def test_daylight_saving_time_written_wrong_is_found_and_left_out(tmp_path):
    df = mt.frame(customers=("C1",))
    i = df.index[df["start"] == "2025-10-25T12:00:00+02:00"][0]
    df.loc[i, "start"] = "2025-10-25T12:00:00+01:00"               # summer wall-clock time, winter offset
    report, hourly = run(tmp_path, df)
    dst = next(f for f in report.findings if f.code == "meter_dst")
    assert dst.severity == "warning" and dst.count == 1 and dst.params["ts"] == "2025-10-25T12:00:00+01:00"
    assert pd.Timestamp("2025-10-25 10:00", tz="UTC") not in set(pd.DatetimeIndex(hourly["start"]))
    # one offset all year round is unambiguous, whichever it is
    fixed = mt.frame(customers=("C1",))
    fixed["start"] = [t.tz_convert("Etc/GMT-1").isoformat() for t in mt.instants()]
    report, hourly = run(tmp_path / "fixed", fixed)
    assert "meter_dst" not in codes(report) and report.usable


def test_bad_rows_are_counted_and_left_out(tmp_path):
    df = mt.frame(customers=("C1", "C2"), export=True)
    df.loc[0, "kwh_import"] = -0.2                                  # negative
    df.loc[1, "kwh_export"] = "n/a"                                 # unreadable
    df.loc[2, "start"] = "2025-03-29T00:37:00+01:00"                # off the grid
    extra = df.iloc[[100, 101]].copy()                              # one copy the same, one different
    extra.loc[extra.index[1], "kwh_import"] = 9.0
    df = pd.concat([df, extra], ignore_index=True)
    report, hourly = run(tmp_path, df, keep={"C1", "C2"})
    found = codes(report)
    assert {"meter_negative", "meter_unreadable", "meter_off_grid", "meter_duplicate",
            "meter_duplicate_same"} <= found, report.text()
    assert report.usable
    first = pd.Timestamp("2025-03-28 23:00", tz="UTC")               # the hour of rows 0-2
    c1 = hourly[hourly["customer_id"] == "C1"]
    assert first not in set(pd.DatetimeIndex(c1["start"]))
    dup = pd.to_datetime(df.loc[101, "start"], utc=True).floor("h")
    assert dup not in set(pd.DatetimeIndex(c1["start"]))          # a reading given twice differently is no reading
    assert hourly["kwh_export"].notna().any()


def test_meters_of_other_stations_and_of_nobody(tmp_path):
    report, hourly = run(tmp_path, mt.frame(), keep={"C1", "C9"}, known={"C1", "C2", "C9"})
    assert set(hourly["customer_id"]) == {"C1"}                     # C2 is another station's
    unmapped = next(f for f in report.findings if f.code == "meter_unmapped")
    assert unmapped.count == 1 and unmapped.params["meter"] == "C3"
    assert "meter_no_readings" in codes(report)                    # C9 has none


def test_the_swedish_excel_dialect_reads_like_rfc_4180(tmp_path):
    _, a = run(tmp_path / "a", mt.frame(export=True))
    _, b = run(tmp_path / "b", mt.frame(export=True), excel=True)
    pd.testing.assert_frame_equal(a, b)


def test_a_meter_id_column_and_the_voltages_at_15_minutes(tmp_path):
    report, hourly = run(tmp_path, mt.frame(voltages=True, id_column="meter_id"))
    assert report.usable and set(hourly["customer_id"]) == set(mt.CUSTOMERS)
    v = pd.read_parquet(tmp_path / "out" / "voltage_15min.parquet")
    assert list(v.columns) == ["customer_id", "start", "v_l1", "v_l2", "v_l3"]
    assert len(v) == len(mt.instants()) * len(mt.CUSTOMERS)        # every quarter-hour, not summed
    assert v["v_l2"].iloc[0] == pytest.approx(231.0)


def test_a_file_without_the_columns_says_which(tmp_path):
    report, hourly = run(tmp_path, mt.frame().drop(columns=["kwh_import"]).rename(columns={"customer_id": "id"}))
    assert hourly is None
    f = next(f for f in report.findings if f.code == "meter_missing_column")
    assert "customer_id or meter_id" in f.params["columns"] and "kwh_import" in f.params["columns"]
    report, _ = meters.ingest([])
    assert codes(report) == {"meter_empty"}
