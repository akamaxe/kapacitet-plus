"""Synthetic hourly profiles for the tiny test grid, written where the engine reads its own.

The pipeline builds its profiles from downloaded weather and a household pool that only exists
on a machine that ran it. The tests need the same files, with the same shapes and time axis, on
a fresh clone: this module writes them at test time from closed-form shapes and seeded noise.
It is our own code and copies nothing from any data source. The numbers are plausible for a
Swedish villa street and deliberately nothing more: they are there to exercise the engine.

What is written under `<processed>/`, for each year in `YEARS` unless noted:

    weather/weather_<y>.parquet       ghi, dni, dhi, t_air, zenith, azimuth, t_filled, irr_filled
    pv/ac_per_kwp_<y>.npy             (C, H) AC output per kWp, capped at the inverter (1 / DC-AC ratio)
    pv/roofs.parquet                  once: the columns `profiles.pv.roof_geometry` writes
    heatpump/hp_el_<y>.npy            (C, H) heat-pump electricity, kW
    ev/ev_uncontrolled_<y>.npy        (C, H) uncontrolled home charging, kW
    ev/ev_sessions_<y>.npz            the charging sessions behind it
    households/pool_hourly_<POOL>.npy once: (POOL_SIZE, 8760) household base load, kW

Two years, one of them a leap year, so the code that lines years up on the calendar is
exercised. `settings().weather_years` has to match them; tests/conftest.py sees to that.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from kapacitet.config import A
from kapacitet.profiles.household import POOL_YEAR
from kapacitet.timeindex import year_index

YEARS = (2024, 2025)
POOL_SIZE = 24
LAT, LON = 59.3, 18.0


def _sun(index: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sine of the solar elevation, zenith and azimuth (degrees) at the middle of each hour."""
    mid = index + pd.Timedelta(minutes=30)
    doy = mid.dayofyear.to_numpy()
    decl = np.radians(23.44) * np.sin(2 * np.pi * (284 + doy) / 365.0)
    solar_hour = mid.hour.to_numpy() + mid.minute.to_numpy() / 60.0 + LON / 15.0
    ha = np.radians(15.0 * (solar_hour - 12.0))
    lat = np.radians(LAT)
    sin_el = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(ha)
    zenith = np.degrees(np.arccos(np.clip(sin_el, -1.0, 1.0)))
    azimuth = (np.degrees(np.arctan2(np.sin(ha), np.cos(ha) * np.sin(lat) - np.tan(decl) * np.cos(lat)))
               + 180.0) % 360.0
    return sin_el, zenith, azimuth


def weather(year: int) -> pd.DataFrame:
    """One year of hourly weather on the engine's time axis, from a clear-sky shape and seeded clouds."""
    idx = year_index(year)
    rng = np.random.default_rng(year)
    n_days = len(idx) // 24
    doy = np.arange(n_days)
    summer = 0.5 - 0.5 * np.cos(2 * np.pi * (doy - 10) / 365.0)          # 0 in January, 1 in July
    clear = np.clip(0.25 + 0.45 * summer + rng.normal(0.0, 0.25, n_days), 0.05, 1.0)
    clear_h = np.repeat(clear, 24)
    sin_el, zenith, azimuth = _sun(idx)
    sun = np.clip(sin_el, 0.0, None)
    ghi = 1000.0 * sun * clear_h
    dhi = ghi * (1.0 - 0.75 * clear_h)
    dni = np.where(sin_el > 0.05, (ghi - dhi) / np.maximum(sin_el, 0.05), 0.0)
    hour = np.tile(np.arange(24), n_days)
    daily = np.repeat(rng.normal(0.0, 2.5, n_days), 24)
    t_air = (6.0 - 11.0 * np.cos(2 * np.pi * (np.repeat(doy, 24) - 20) / 365.0)
             + 3.0 * np.sin(2 * np.pi * (hour - 9) / 24.0) + daily)
    df = pd.DataFrame({"ghi": ghi, "dni": dni, "dhi": dhi, "t_air": t_air, "zenith": zenith,
                       "azimuth": azimuth, "t_filled": False, "irr_filled": False}, index=idx)
    df.index.name = "ts_utc"
    return df.astype({c: "float32" for c in ["ghi", "dni", "dhi", "t_air", "zenith", "azimuth"]})


def pv_per_kwp(w: pd.DataFrame, n_customers: int) -> np.ndarray:
    """(C, H) AC per kWp: global irradiance scaled by a per-roof factor, capped at the inverter."""
    factor = np.random.default_rng(7).uniform(0.88, 1.0, n_customers)
    ac = factor[:, None] * (w["ghi"].to_numpy(dtype=float) / 1000.0)[None, :]
    return np.clip(ac, 0.0, 1.0 / A("pv_dc_ac_ratio")).astype(np.float32)


def heat_pump(w: pd.DataFrame, n_customers: int) -> np.ndarray:
    """(C, H) electricity of a heat pump: degree-hours below 17 °C over a temperature-dependent COP, plus hot water."""
    rng = np.random.default_rng(11)
    ua = rng.uniform(0.10, 0.18, n_customers)                          # kW of heat per K
    t = w["t_air"].to_numpy(dtype=float)
    cop = np.clip(3.2 + 0.08 * t, 1.6, 4.5)
    hour = np.arange(len(t)) % 24
    hot_water = 0.25 * (1.0 + 0.8 * np.isin(hour, (6, 7, 8, 18, 19, 20)))
    return (np.outer(ua, np.maximum(17.0 - t, 0.0) / cop) + hot_water[None, :] / 2.5).astype(np.float32)


def charging(year: int, n_customers: int) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """(C, H) uncontrolled charging and its sessions: plug in in the evening, charge at full power."""
    rng = np.random.default_rng(5000 + year)
    H = len(year_index(year))
    n_days = H // 24
    p_max = A("ev_charger_kw")
    power = np.zeros((n_customers, H), dtype=np.float32)
    sessions: dict[str, list] = {"customer": [], "start": [], "end": [], "energy_kwh": []}
    for c in range(n_customers):
        drives = rng.random(n_days) < 0.7
        arrive = rng.integers(16, 21, n_days)                          # local hour, interval start
        need = rng.uniform(4.0, 14.0, n_days)
        for d in np.flatnonzero(drives):
            start = d * 24 + int(arrive[d])                            # hour 0 is local midnight, 1 January
            end = min((d + 1) * 24 + 7, H)
            left, h = float(need[d]), start
            while left > 1e-9 and h < end:
                power[c, h] += min(p_max, left)
                left -= min(p_max, left)
                h += 1
            for key, value in zip(sessions, (c, start, end, need[d])):
                sessions[key].append(value)
    out = {"customer": np.asarray(sessions["customer"], dtype=np.int32),
           "start": np.asarray(sessions["start"], dtype=np.int32),
           "end": np.asarray(sessions["end"], dtype=np.int32),
           "energy_kwh": np.asarray(sessions["energy_kwh"], dtype=np.float32)}
    return power, out


def household_pool(n: int = POOL_SIZE) -> np.ndarray:
    """(n, 8760) household base load in kW for POOL_YEAR: a daily shape, seasons, weekends and noise."""
    idx = year_index(POOL_YEAR)
    rng = np.random.default_rng(POOL_YEAR)
    hour = np.arange(len(idx)) % 24
    day = np.arange(len(idx)) // 24
    shape = np.array([0.30, 0.25, 0.22, 0.22, 0.24, 0.32, 0.55, 0.80, 0.70, 0.50, 0.45, 0.45,
                      0.48, 0.45, 0.45, 0.50, 0.65, 0.95, 1.15, 1.10, 0.95, 0.80, 0.60, 0.42])[hour]
    weekend = np.isin((day + pd.Timestamp(f"{POOL_YEAR}-01-01").weekday()) % 7, (5, 6))
    shape = np.where(weekend & (hour >= 9) & (hour < 16), shape * 1.3, shape)
    season = 1.0 + 0.25 * np.cos(2 * np.pi * (day - 15) / 365.0)
    scale = rng.lognormal(0.0, 0.25, n)
    noise = rng.lognormal(-0.06, 0.35, (n, len(idx)))
    load = scale[:, None] * (shape * season)[None, :] * noise
    load *= A("household_kwh_per_year") / load.sum(axis=1).mean()
    return load.astype(np.float32)


def roofs_table(roofs: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    """roofs.csv (cid, kwp) completed with the columns `profiles.pv.roof_geometry` gives an imported grid."""
    fuse_kva = customers.set_index("cid")["fuse_kva"]
    kwp = roofs["kwp"].astype(float)
    return pd.DataFrame({
        "cid": roofs["cid"].astype(int),
        "ridge_bearing": 90.0, "azimuth": 180.0, "tilt": float(A("pv_roof_tilt_deg")), "east_west": False,
        "usable_area_m2": (kwp / A("pv_power_density_kwp_m2")).round(1),
        "kwp": kwp.round(2),
        "inverter_kw": [round(min(k / A("pv_dc_ac_ratio"), float(fuse_kva[c])), 2)
                        for c, k in zip(roofs["cid"].astype(int), kwp)],
    })


def write(processed: str | Path, customers: pd.DataFrame, roofs: pd.DataFrame, years=YEARS) -> None:
    """Write every profile file the engine reads into `processed` (a data/processed folder)."""
    root = Path(processed)
    for sub in ("weather", "pv", "heatpump", "ev", "households"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    C = len(customers)
    roofs_table(roofs, customers).to_parquet(root / "pv" / "roofs.parquet", index=False)
    np.save(root / "households" / f"pool_hourly_{POOL_YEAR}.npy", household_pool())
    for y in years:
        w = weather(y)
        w.to_parquet(root / "weather" / f"weather_{y}.parquet")
        np.save(root / "pv" / f"ac_per_kwp_{y}.npy", pv_per_kwp(w, C))
        np.save(root / "heatpump" / f"hp_el_{y}.npy", heat_pump(w, C))
        power, sessions = charging(y, C)
        np.save(root / "ev" / f"ev_uncontrolled_{y}.npy", power)
        np.savez(root / "ev" / f"ev_sessions_{y}.npz", **sessions)
