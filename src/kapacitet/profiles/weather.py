"""Hourly weather on the study-year index: irradiance (SMHI STRÅNG) and temperature (SMHI metobs).

STRÅNG values describe the irradiance at the hour mark (verified: the clear-sky index is
symmetric between morning and afternoon for a time shift of 0–15 min). Hour-beginning means
over [t, t+1h) are built from adjacent marks:
  GHI_mean = trapezoid(GHI);  BHI_mean = trapezoid(DNI·cos z);  DHI_mean = GHI_mean − BHI_mean;
  DNI_mean = BHI_mean / cos z(mid-interval).
This keeps the closure GHI = DHI + DNI·cos z at the solar position used for transposition.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pvlib

from ..config import settings
from ..ingest import smhi
from ..paths import processed
from ..timeindex import interval_mean_from_instants, year_index


def location() -> pvlib.location.Location:
    s = settings().site
    return pvlib.location.Location(s.center_lat, s.center_lon, altitude=20, tz="UTC", name=s.name)


def weather_year(year: int) -> pd.DataFrame:
    cfg = settings()
    idx = year_index(year)
    loc = location()
    ghi_i = smhi.read_strang(cfg.strang["ghi"], year)
    dni_i = smhi.read_strang(cfg.strang["dni"], year)
    sp_i = loc.get_solarposition(ghi_i.index)
    cosz_i = np.cos(np.radians(sp_i["zenith"].to_numpy())).clip(min=0.0)
    bhi_i = pd.Series(dni_i.reindex(ghi_i.index).to_numpy() * cosz_i, index=ghi_i.index)

    ghi = interval_mean_from_instants(ghi_i, idx)
    bhi = interval_mean_from_instants(bhi_i, idx).clip(lower=0.0)
    bhi = np.minimum(bhi, ghi)
    mid = idx + pd.Timedelta(minutes=30)
    sp = loc.get_solarposition(mid)
    cosz = np.cos(np.radians(sp["apparent_zenith"].to_numpy()))
    low_sun = cosz < 0.05
    bhi = bhi.where(~low_sun, 0.0)
    dhi = (ghi - bhi).clip(lower=0.0)
    dni = pd.Series(np.where(low_sun, 0.0, bhi.to_numpy() / np.maximum(cosz, 0.05)), index=idx)

    temp_i = smhi.read_metobs(cfg.temperature_station)
    temp_i = temp_i[(temp_i.index >= idx[0] - pd.Timedelta(hours=6)) & (temp_i.index <= idx[-1] + pd.Timedelta(hours=6))]
    t_air = interval_mean_from_instants(temp_i, idx)
    t_missing = t_air.isna()
    if t_missing.any():  # longer gaps: fill with the same hour of the neighbouring days
        t_air = t_air.fillna(t_air.shift(24)).fillna(t_air.shift(-24)).interpolate(limit_direction="both")

    df = pd.DataFrame({
        "ghi": ghi.to_numpy(), "dni": dni.to_numpy(), "dhi": dhi.to_numpy(), "t_air": t_air.to_numpy(),
        "zenith": sp["apparent_zenith"].to_numpy(), "azimuth": sp["azimuth"].to_numpy(),
        "t_filled": t_missing.to_numpy(), "irr_filled": ghi.isna().to_numpy(),
    }, index=idx)
    df[["ghi", "dni", "dhi"]] = df[["ghi", "dni", "dhi"]].fillna(0.0)
    df.index.name = "ts_utc"
    return df.astype({c: "float32" for c in ["ghi", "dni", "dhi", "t_air", "zenith", "azimuth"]})


def build_weather(years) -> dict[int, pd.DataFrame]:
    out = {}
    for y in years:
        df = weather_year(y)
        df.to_parquet(processed("weather", f"weather_{y}.parquet"))
        out[y] = df
    return out


def load_weather(year: int) -> pd.DataFrame:
    return pd.read_parquet(processed("weather", f"weather_{year}.parquet"))


def diffuse_closure_check(year: int) -> dict:
    """Compare derived DHI with STRÅNG's published diffuse irradiance (parameter 122)."""
    cfg = settings()
    try:
        dhi_pub = smhi.read_strang(cfg.strang["dhi"], year)
    except FileNotFoundError:
        return {}
    ghi = smhi.read_strang(cfg.strang["ghi"], year)
    dni = smhi.read_strang(cfg.strang["dni"], year)
    sp = location().get_solarposition(ghi.index)
    cosz = np.cos(np.radians(sp["zenith"].to_numpy())).clip(min=0.0)
    derived = (ghi.to_numpy() - dni.reindex(ghi.index).to_numpy() * cosz).clip(min=0.0)
    pub = dhi_pub.reindex(ghi.index).to_numpy()
    ok = np.isfinite(pub) & (ghi.to_numpy() > 20)
    diff = derived[ok] - pub[ok]
    return {"year": year, "hours": int(ok.sum()), "mean_abs_w_m2": float(np.mean(np.abs(diff))),
            "p99_abs_w_m2": float(np.percentile(np.abs(diff), 99)), "mean_dhi_w_m2": float(np.mean(pub[ok]))}
