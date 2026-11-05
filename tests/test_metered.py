"""Metered household load (profiles/metered.py): the temperature fit, the synthesis of every weather
year, and the one switch in household.customer_base_load. Synthetic arrays only: no data needed."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from kapacitet import paths
from kapacitet.profiles import household, metered
from kapacitet.timeindex import local_standard, year_index

BASE = 17.0


def temperature(year: int, shift: float = 0.0) -> pd.Series:
    """A year of hourly temperatures: a season and a day, well below and above the base."""
    idx = year_index(year)
    doy = local_standard(idx).dayofyear.to_numpy()
    hod = local_standard(idx).hour.to_numpy()
    t = 6.0 - 12.0 * np.cos(2 * np.pi * (doy - 15) / 365.0) + 3.0 * np.sin(2 * np.pi * (hod - 9) / 24.0) + shift
    return pd.Series(t, index=idx)


def known_ab(seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.uniform(0.2, 1.5, size=(2, 24)), rng.uniform(0.0, 0.12, size=(2, 24))


def load_from(a, b, temps: pd.Series, residual: np.ndarray | None = None) -> pd.Series:
    idx = temps.index
    dt = metered.daytype_of(idx)
    hod = local_standard(idx).hour.to_numpy()
    y = a[dt, hod] + b[dt, hod] * np.maximum(BASE - temps.to_numpy(), 0.0)
    if residual is not None:
        y = y + residual
    return pd.Series(y, index=idx)


def test_a_known_a_and_b_are_recovered():
    a, b = known_ab()
    temps = temperature(2025)
    f = metered.fit(load_from(a, b, temps), temps, BASE)
    np.testing.assert_allclose(f.a, a, atol=1e-8)
    np.testing.assert_allclose(f.b, b, atol=1e-9)
    assert f.r2 == pytest.approx(1.0) and f.hours == len(temps)
    assert f.residual.shape == (365, 24) and np.abs(f.residual).max() < 1e-8


def test_noise_leaves_a_and_b_close_and_the_residual_carries_it():
    a, b = known_ab(1)
    temps = temperature(2025)
    noise = np.random.default_rng(2).normal(0.0, 0.05, size=len(temps))
    f = metered.fit(load_from(a, b, temps, noise), temps, BASE)
    assert np.abs(f.a - a).max() < 0.05 and np.abs(f.b - b).max() < 0.005
    assert 0.0 < f.r2 < 1.0 and f.residual.std() == pytest.approx(0.05, rel=0.2)


def test_the_heating_slope_is_never_negative():
    """A load that falls as it gets colder is no heating: b stays at zero."""
    temps = temperature(2025)
    load = pd.Series(1.0 + 0.05 * np.maximum(temps.to_numpy() - 5.0, 0.0), index=temps.index)
    f = metered.fit(load, temps, BASE)
    assert (f.b >= 0).all() and f.b.max() == pytest.approx(0.0, abs=1e-12)


def test_missing_hours_and_a_daytype_never_metered_are_handled():
    a, b = known_ab(3)
    temps = temperature(2025)
    load = load_from(a, b, temps)
    weekday = metered.daytype_of(load.index) == 0
    f = metered.fit(load[weekday], temps, BASE)                    # only working days metered
    np.testing.assert_allclose(f.a[1], f.a[0])
    np.testing.assert_allclose(f.b[1], f.b[0])
    gappy = load.copy()
    gappy.iloc[::7] = np.nan
    g = metered.fit(gappy, temps, BASE)
    np.testing.assert_allclose(g.a, a, atol=1e-8)
    assert len(g.residual) < 365                                   # a day with a missing hour is not a residual day


def test_the_metered_year_is_reproduced_and_another_follows_its_own_weather():
    a, b = known_ab(4)
    temps = temperature(2025)
    rng = np.random.default_rng(5)
    residual = np.repeat(rng.normal(0.0, 0.1, size=365), 24) * np.tile(np.linspace(0.5, 1.5, 24), 365)
    load = load_from(a, b, temps, residual)
    f = metered.fit(load, temps, BASE)
    # the same year and weather: every day is its own nearest day, so the readings come back
    same = metered.synthesise(f, year_index(2025), temps.to_numpy(), BASE)
    np.testing.assert_allclose(same, np.maximum(load.to_numpy(), 0.0), atol=1e-8)
    # a colder leap year: the fitted heating follows its temperatures, days aligned by daytype
    cold = temperature(2024, shift=-4.0)
    other = metered.synthesise(f, year_index(2024), cold.to_numpy(), BASE)
    assert len(other) == 8784 and (other >= 0).all() and other.sum() > same.sum()
    days = metered.aligned_days(f, year_index(2024))
    kinds = (local_standard(year_index(2024)[::24]).dayofweek >= 5).astype(int)
    assert (f.day_type[days] == kinds).all()
    gap = np.abs(f.day_of_year[days] - np.arange(366))
    assert np.minimum(gap, 365 - gap).max() <= 4          # the nearest such day, round the year


def test_customer_base_load_takes_the_metered_rows_and_the_pool_for_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PROCESSED", tmp_path)
    (tmp_path / "households").mkdir()
    pool = np.random.default_rng(0).uniform(0.1, 1.0, size=(6, 8760)).astype(np.float32)
    np.save(tmp_path / "households" / f"pool_hourly_{household.POOL_YEAR}.npy", pool)
    synthetic = household.customer_base_load(household.POOL_YEAR, 4, load_seed=1)
    assert synthetic.shape == (4, 8760)
    m = np.full((4, 8760), np.nan, dtype=np.float32)
    m[[0, 2]] = 0.5
    np.save(tmp_path / "households" / f"metered_{household.POOL_YEAR}.npy", m)
    mixed = household.customer_base_load(household.POOL_YEAR, 4, load_seed=1)
    assert (mixed[[0, 2]] == 0.5).all()
    np.testing.assert_array_equal(mixed[[1, 3]], synthetic[[1, 3]])  # the seed varies only the synthetic ones
    with pytest.raises(ValueError, match="customers"):
        household.customer_base_load(household.POOL_YEAR, 5)
    np.save(tmp_path / "households" / f"metered_{household.POOL_YEAR}.npy", np.full((4, 8760), 0.25, np.float32))
    (tmp_path / "households" / f"pool_hourly_{household.POOL_YEAR}.npy").unlink()
    assert (household.customer_base_load(household.POOL_YEAR, 4) == 0.25).all()  # all metered: no pool needed


def test_a_stations_readings_become_its_metered_load_and_a_fit_report(tmp_path, monkeypatch):
    """metered.build on a stand-in station: two customers with a year of readings, one with a week."""
    monkeypatch.setattr(paths, "PROCESSED", tmp_path)
    grid = tmp_path / "grid"
    grid.mkdir()
    cust = pd.DataFrame({"cid": [0, 1, 2, 3], "osm_id": ["A", "B", "C", "D"]})
    for name in ("buses", "lines"):
        pd.DataFrame({"x": [0]}).to_parquet(grid / f"{name}.parquet")
    cust.to_parquet(grid / "customers.parquet")
    (tmp_path / "weather").mkdir()
    for y, shift in ((2024, -2.0), (2025, 0.0)):
        temperature(y, shift).rename("t_air").to_frame().to_parquet(tmp_path / "weather" / f"weather_{y}.parquet")
    a, b = known_ab(6)
    temps = temperature(2025)
    rows = []
    for cid, scale, hours in (("A", 1.0, slice(None)), ("C", 2.0, slice(None)), ("D", 1.0, slice(0, 24 * 7)),
                              ("Z", 1.0, slice(0, 48))):
        load = load_from(a * scale, b * scale, temps).iloc[hours]
        rows.append(pd.DataFrame({"customer_id": cid, "start": load.index, "kwh_import": load.to_numpy(),
                                  "kwh_export": 0.0}))
    (tmp_path / "meters").mkdir()
    pd.concat(rows, ignore_index=True).to_parquet(tmp_path / "meters" / "hourly.parquet")

    report = metered.build(years=(2024, 2025))
    assert report["customers"] == {"total": 4, "fitted": 2, "too_few_days": 1, "without_readings": 1,
                                   "export_seen": 0}
    assert report["unmatched_meters"] == 1 and report["median"]["r2"] == pytest.approx(1.0)
    m25 = np.load(tmp_path / "households" / "metered_2025.npy")
    m24 = np.load(tmp_path / "households" / "metered_2024.npy")
    assert m25.shape == (4, 8760) and m24.shape == (4, 8784)
    assert np.isnan(m25[[1, 3]]).all() and np.isfinite(m25[[0, 2]]).all()
    np.testing.assert_allclose(m25[0], load_from(a, b, temps).to_numpy(), atol=1e-5)
    np.testing.assert_allclose(m25[2], 2 * m25[0], rtol=1e-5)
    assert m24[0].sum() > m25[0].sum()                             # the colder year needs more heat
    on_disk = json.loads((tmp_path / "validation" / "metered_fit.json").read_text(encoding="utf-8"))
    assert on_disk["schema"] == metered.SCHEMA and on_disk["base_temp_c"] == BASE
    assert {r["customer_id"]: r["fitted"] for r in on_disk["per_customer"]} == {"A": True, "C": True, "D": False}
