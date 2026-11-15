"""The other shapes of meter readings (ingest/meter_formats.py, task 2.4 of the technical review).

The same quarter-hours of tests/fixtures/meters_tiny.py, over the two days the clocks change in
2025, written as a metering system might: in the long format with each instant's offset, in Swedish
local time without one, in Wh, and as one row per meter and day with 96 to 100 quarter-hour
columns or 24 to 25 hourly ones. Each must give the same complete hours on the year index.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fixtures import meters_tiny as mt
from kapacitet.ingest import meters

TZ = mt.TZ


def hours(tmp_path, name: str, df: pd.DataFrame, sep: str = ","):
    folder = tmp_path / name
    folder.mkdir(parents=True)
    df.to_csv(folder / "readings.csv", index=False, sep=sep)
    report, hourly = meters.ingest(meters.meter_files(folder), None)
    return report, hourly


def codes(report) -> set[str]:
    return {f.code for f in report.findings}


@pytest.fixture(scope="module")
def long():
    return mt.frame()


def local(df: pd.DataFrame) -> pd.DataFrame:
    """The same rows with the wall-clock time and no offset, in the order a meter gives them."""
    return df.assign(start=df["start"].str[:19])


def wide(df: pd.DataFrame, per_hour: bool = False) -> pd.DataFrame:
    """One row per meter and local day: each day's quarter-hours (or hours) in time order, with as
    many columns as the longest day has (100 or 25); a shorter day leaves its last ones blank."""
    t = pd.to_datetime(df["start"], utc=True).dt.tz_convert(TZ)
    d = df.assign(_t=t, _day=t.dt.strftime("%Y-%m-%d"), kwh=df["kwh_import"].astype(float))
    if per_hour:
        # floored in UTC, so the hour the clocks show twice in October stays two hours
        d = d.assign(_t=d["_t"].dt.tz_convert("UTC").dt.floor("h").dt.tz_convert(TZ))
        d = d.groupby(["customer_id", "_day", "_t"], as_index=False)["kwh"].sum()
    width = 25 if per_hour else 100
    rows = []
    for (cid, day), g in d.sort_values("_t").groupby(["customer_id", "_day"], sort=True):
        values = [f"{v:.6f}" for v in g["kwh"]] + [""] * (width - len(g))
        rows.append([cid, day, *values])
    cols = ["customer_id", "date", *[f"{'h' if per_hour else 'q'}{k + 1}" for k in range(width)]]
    return pd.DataFrame(rows, columns=cols)


def same(a: pd.DataFrame, b: pd.DataFrame) -> None:
    key = ["customer_id", "year", "hour"]
    a, b = a.sort_values(key).reset_index(drop=True), b.sort_values(key).reset_index(drop=True)
    assert a[key].equals(b[key]), "not the same hours"
    np.testing.assert_allclose(a["kwh_import"].to_numpy(), b["kwh_import"].to_numpy(), atol=1e-9)


def test_local_time_gives_the_hours_of_the_offsets_with_the_october_hour_in_order(tmp_path, long):
    ref_report, ref = hours(tmp_path, "long", long)
    assert ref_report.usable
    report, got = hours(tmp_path, "local", local(long))
    assert report.usable, report.text()
    assert {"meter_local_time", "meter_repeated_hour"} <= codes(report)
    assert "meter_naive_time" not in codes(report)
    same(ref, got)


def test_one_row_per_day_gives_the_same_hours_in_quarter_hours_and_in_hours(tmp_path, long):
    _, ref = hours(tmp_path, "long", long)
    report, got = hours(tmp_path, "wide", wide(long))
    assert report.usable, report.text()
    assert "meter_wide" in codes(report) and "meter_wide_misfit" not in codes(report)
    same(ref, got)
    report, by_hour = hours(tmp_path, "wide-hours", wide(long, per_hour=True), sep=";")
    assert report.usable, report.text()
    assert "meter_hourly" in codes(report)
    same(ref, by_hour)


def test_wh_is_divided_by_a_thousand(tmp_path, long):
    _, ref = hours(tmp_path, "long", long)
    wh = long.assign(wh_import=(long["kwh_import"].astype(float) * 1000).round(6)).drop(columns="kwh_import")
    report, got = hours(tmp_path, "wh", wh)
    assert report.usable and "meter_wh" in codes(report)
    same(ref, got)


def test_an_hour_the_clocks_skip_or_show_once_is_left_out_and_said(tmp_path, long):
    df = local(long[long["customer_id"] == "C1"]).reset_index(drop=True)
    # one reading at 02:15 on 30 March, which no clock showed; the second 02:30 of October dropped
    df.loc[len(df)] = {"customer_id": "C1", "start": "2025-03-30T02:15:00", "kwh_import": "0.1"}
    repeated = df.index[df["start"] == "2025-10-26T02:30:00"]
    assert len(repeated) == 2
    df = df.drop(index=repeated[1])
    report, hourly = hours(tmp_path, "gaps", df)
    found = {f.code: f for f in report.findings}
    assert found["meter_nonexistent_time"].count == 1 and found["meter_repeated_once"].count == 1
    assert report.usable


def test_a_day_that_does_not_fit_its_columns_is_refused_and_named(tmp_path, long):
    df = wide(long)
    spring = df.index[df["date"] == "2025-03-30"][0]
    df.loc[spring, "q96"] = "0.5"                  # a value where the 23-hour day has no quarter-hour
    report, hourly = hours(tmp_path, "misfit", df)
    bad = next(f for f in report.findings if f.code == "meter_wide_misfit")
    assert bad.severity == "error" and "2025-03-30" in bad.params["example"] and hourly is None


def test_a_station_is_built_and_validated_with_readings_in_rows_of_a_day(tmp_path):
    """The acceptance of task 2.4: a station of the demonstration study takes its customers'
    readings as one row per meter and day, builds with them (metered load) and is validated against
    them with the tolerances of study.toml. Needs the reference weather and household pool."""
    import json

    from test_study import _has_reference_profiles

    from kapacitet import procs
    from kapacitet.study import open_study, portfolio
    from kapacitet.study.demo import create

    if not _has_reference_profiles():
        pytest.skip("no reference weather and household pool (run: python -m kapacitet all)")
    study = create(tmp_path / "demo", allow_git=True)
    sid = study.station_ids()[0]
    ids = pd.read_csv(study.station_dir(sid) / "import" / "customers.csv", dtype=str)["customer_id"].tolist()
    days = pd.date_range("2025-01-01", "2025-12-31", freq="D")
    hours = [f"h{k}" for k in range(1, 26)]
    rows = []
    for i, cid in enumerate(ids):
        for d in days:
            n = len(pd.date_range(pd.Timestamp(d).tz_localize(TZ), (pd.Timestamp(d) + pd.Timedelta(days=1)).tz_localize(TZ),
                                  freq="h", inclusive="left"))
            vals = [f"{0.3 + 0.01 * i + 0.2 * (8 <= h < 22):.3f}" for h in range(n)]
            rows.append([cid, d.strftime("%Y-%m-%d"), *vals, *[""] * (25 - n)])
    (study.inputs / "meters").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["customer_id", "date", *hours]).to_csv(study.inputs / "meters" / "days.csv", index=False)
    toml = study.toml.read_text(encoding="utf-8")
    # inside the template's own [validation] table, whose figures are only comments
    study.toml.write_text(toml.replace("[validation]\n", '[validation]\nmin_metered_share = 0.5\nsource = "test, 2026-11-15"\n', 1),
                          encoding="utf-8")
    study = open_study(study.root, allow_git=True)
    out = portfolio.run(study, stations=[sid], quick=True, timeout=1500, allow_git=True)
    assert out["built"][sid]["status"] == "ready", procs.log_tail(study.station_dir(sid), 30)
    quality = study.record(sid)["quality"]["meters"]
    assert quality["usable"] and {"meter_wide", "meter_hourly"} <= {f["code"] for f in quality["findings"]}
    done = portfolio.validate(study, stations=[sid], allow_git=True)
    assert done[sid]["code"] == 0, procs.log_tail(study.station_dir(sid), 30)
    v = json.loads((study.station_dir(sid) / "processed" / "results" / "validation_measured.json").read_text())
    assert v["measured"].get("status") != "no meter readings"
    assert v["verdict"]["criteria"]["min_metered_share"] == 0.5
