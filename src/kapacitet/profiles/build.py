"""Build every hourly profile the engine needs, for all weather years."""

from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from ..config import settings
from ..grid.builder import load_tables
from ..ingest import esett
from ..paths import RAW, processed
from ..timeindex import hour_of_day, year_index
from . import ev, heatpump, household, pv
from .weather import build_weather, load_weather

log = logging.getLogger(__name__)


def design_temperature(years) -> float:
    return float(min(load_weather(y)["t_air"].min() for y in years))


def build_all(quick: bool = False) -> None:
    cfg = settings()
    years = cfg.weather_years if not quick else (cfg.reference_year,)
    _, _, customers = load_tables()
    C = len(customers)

    log.info("weather %s", years)
    build_weather(years)

    roofs = pv.roof_geometry(customers)
    roofs.to_parquet(processed("pv", "roofs.parquet"), index=False)
    log.info("roofs: %d, kWp median %.1f (min %.1f, max %.1f), east-west %d",
             len(roofs), roofs.kwp.median(), roofs.kwp.min(), roofs.kwp.max(), int(roofs.east_west.sum()))
    pv.build_pv(roofs, years)

    household.ensure_pool()

    t_design = design_temperature(years)
    weather_ref = load_weather(cfg.reference_year)
    hp_info = {}
    for y in years:
        w = load_weather(y)
        el, heat = heatpump.electric_demand(customers, w, weather_ref, t_design)
        np.save(processed("heatpump", f"hp_el_{y}.npy"), el)
        hp_info[y] = {"mean_el_kwh": float(el.sum(axis=1).mean()), "mean_heat_kwh": float(heat.sum(axis=1).mean()),
                      "seasonal_cop": float(heat.sum() / max(el.sum(), 1e-9)), "peak_el_kw": float(el.max())}
        power, sessions = ev.simulate(C, year_index(y), seed=y)
        np.save(processed("ev", f"ev_uncontrolled_{y}.npy"), power)
        np.savez(processed("ev", f"ev_sessions_{y}.npz"), customer=sessions.customer, start=sessions.start,
                 end=sessions.end, energy_kwh=sessions.energy_kwh)
    processed("heatpump", "summary.json").write_text(json.dumps({"design_temp_c": t_design, "years": hp_info},
                                                                 indent=1), encoding="utf-8")
    validate_household_shape()


def validate_household_shape() -> dict:
    """Compare the simulated household base load with eSett's real Stockholm load profile (EXP18)."""
    cfg = settings()
    y = household.POOL_YEAR
    try:
        df = esett.read_json_table(RAW / "esett" / f"exp18_loadprofile_{cfg.site.metering_grid_area}_{y}.json")
    except Exception as exc:  # pragma: no cover - validation is optional
        log.warning("eSett validation skipped: %s", exc)
        return {}
    idx = year_index(y)
    real = (-df["quantity"]).reindex(idx).interpolate(limit=3).to_numpy()
    sim = household.pool_hourly(y).mean(axis=0)
    hod = hour_of_day(idx)
    real_hod = np.array([np.nanmean(real[hod == h]) for h in range(24)])
    sim_hod = np.array([sim[hod == h].mean() for h in range(24)])
    w = load_weather(y)
    t = w["t_air"].to_numpy()
    month = pd.DatetimeIndex(idx).month
    real_m = np.array([np.nanmean(real[month == m]) for m in range(1, 13)])
    sim_m = np.array([sim[month == m].mean() for m in range(1, 13)])
    ok = np.isfinite(real)
    slope_real = float(np.polyfit(t[ok], real[ok] / np.nanmean(real), 1)[0])
    slope_sim = float(np.polyfit(t, sim / sim.mean(), 1)[0])
    out = {
        "year": y,
        "hour_of_day_real": (real_hod / real_hod.mean()).round(4).tolist(),
        "hour_of_day_sim": (sim_hod / sim_hod.mean()).round(4).tolist(),
        "month_real": (real_m / real_m.mean()).round(4).tolist(),
        "month_sim": (sim_m / sim_m.mean()).round(4).tolist(),
        "corr_hour_of_day": float(np.corrcoef(real_hod, sim_hod)[0, 1]),
        "temperature_slope_real_per_c": slope_real,
        "temperature_slope_sim_per_c": slope_sim,
        "note": "eSett EXP18 is the aggregate of profile-settled small customers in Ellevio's Stockholm "
                "grid area and includes electric heating; the simulated base load excludes heating, "
                "so it should match the daily shape but show a weaker temperature dependence.",
    }
    processed("validation", "household_shape.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    log.info("household shape vs eSett: hour-of-day corr %.2f; temperature slope real %.3f/°C sim %.3f/°C",
             out["corr_hour_of_day"], slope_real, slope_sim)
    return out
