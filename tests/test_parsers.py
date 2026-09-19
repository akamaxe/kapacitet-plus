"""Parsers for the external feeds, on small offline fixtures (no network access)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kapacitet.ingest import prices, smhi
from kapacitet.timeindex import year_index

FIXTURES = Path(__file__).parent / "fixtures"


def test_metobs_csv(monkeypatch):
    monkeypatch.setattr(smhi, "metobs_path", lambda station, param=1: FIXTURES / "metobs_sample.csv")
    s = smhi.read_metobs(97200)
    assert s.index.tz is not None and str(s.index[0]) == "2025-01-01 00:00:00+00:00"
    assert s.iloc[0] == pytest.approx(-1.8)
    assert len(s) == 4                       # the empty row is dropped
    assert s.loc["2025-01-01 04:00:00+00:00"] == pytest.approx(-3.0)   # first of the duplicates
    assert s.index.is_monotonic_increasing and not s.index.duplicated().any()


def test_strang_values_are_clipped_and_utc(monkeypatch, tmp_path):
    raw = [{"date_time": "2025-06-01T10:00:00Z", "value": 512.5},
           {"date_time": "2025-06-01T11:00:00Z", "value": -0.4}]
    p = tmp_path / "strang.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(smhi, "strang_path", lambda param, year: p)
    s = smhi.read_strang(117, 2025)
    assert s.iloc[0] == pytest.approx(512.5)
    assert s.iloc[1] == 0.0                  # negative irradiance clipped to zero
    assert s.index.tz is not None


def test_hourly_prices_average_quarter_hours_and_flag_gaps():
    idx = year_index(2025)[:3]
    rows = []
    for j, price in enumerate([1.0, 2.0, 3.0, 4.0]):          # four 15-min values in hour 0
        start = idx[0] + pd.Timedelta(minutes=15 * j)
        rows.append({"start": start, "end": start + pd.Timedelta(minutes=15),
                     "sek_kwh": price, "eur_kwh": price / 11.0})
    rows.append({"start": idx[2], "end": idx[2] + pd.Timedelta(hours=1),
                 "sek_kwh": 5.0, "eur_kwh": 5.0 / 11.0})       # hour 1 missing
    out = prices.hourly_prices(pd.DataFrame(rows), idx)
    assert out["sek_kwh"].iloc[0] == pytest.approx(2.5)        # mean of the quarter hours
    assert out["filled"].to_list() == [False, True, False]
    assert out["sek_kwh"].iloc[1] == pytest.approx(3.75)       # interpolated across the gap


def test_entsoe_xml_handles_both_resolutions_and_missing_positions():
    s = prices.parse_entsoe_xml((FIXTURES / "entsoe_sample.xml").read_bytes())
    assert len(s) == 7                                        # 3 hourly + 4 quarter-hourly
    assert s.iloc[0] == pytest.approx(12.34)
    assert s.iloc[1] == pytest.approx(-5.0)                   # negative prices are kept
    assert s.iloc[2] == pytest.approx(-5.0)                   # A03: missing position repeats
    assert s.index[3] == pd.Timestamp("2025-01-01T03:00Z")
    assert (np.diff(s.index[3:].to_numpy()).astype("timedelta64[m]") == np.timedelta64(15, "m")).all()
    assert s.index.is_monotonic_increasing
