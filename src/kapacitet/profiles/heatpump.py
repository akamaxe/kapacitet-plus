"""Heat-pump electricity demand driven by real SMHI temperature.

Heat demand per house = specific demand (Energimyndigheten, kWh/m² for houses built ≤1940)
× heated floor area, split into space heating and hot water. Space heating follows
degree-hours below the SMHI base temperature (+17 °C); the heat-loss coefficient UA of each
house is fixed from the reference year, so colder years need more heat. COP follows the
when2heat regressions (Ruhnau, Hirth & Praktiknjo 2019, CC BY 4.0):
    COP = c0 + c1·ΔT + c2·ΔT²,  ΔT = T_sink − T_source (≥ 15 K),  × 0.85 real-world correction,
with radiator sink 40 − T_air and hot water at 50 °C. The heat pump covers a share of the
design heat load; an electric backup heater (COP = 1) covers the rest on the coldest hours.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import A
from ..timeindex import hour_of_day

COP_COEF = {"air": (6.08, -0.0941, 0.000464), "ground": (10.29, -0.2084, 0.001322)}
# Diurnal hot-water draw shape (share of daily energy per hour, local standard time).
DHW_SHAPE = np.array([0.5, 0.3, 0.3, 0.3, 0.5, 1.5, 4.0, 5.0, 3.5, 2.0, 1.5, 1.5,
                      2.0, 1.5, 1.5, 2.0, 3.0, 4.5, 5.0, 4.5, 3.5, 3.0, 2.0, 1.0])
DHW_SHAPE = DHW_SHAPE / DHW_SHAPE.sum()


def cop(delta_t: np.ndarray, source: str) -> np.ndarray:
    c0, c1, c2 = COP_COEF[source]
    dt = np.maximum(delta_t, 15.0)
    return (c0 + c1 * dt + c2 * dt ** 2) * 0.85


def degree_hours(t_air: np.ndarray) -> np.ndarray:
    return np.maximum(A("hp_balance_temp_c") - t_air, 0.0)


def heat_pump_types(n: int, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.where(rng.random(n) < A("hp_ground_source_share"), "ground", "air")


def electric_demand(customers: pd.DataFrame, weather: pd.DataFrame, weather_ref: pd.DataFrame,
                    design_temp_c: float) -> tuple[np.ndarray, np.ndarray]:
    """Electric heat-pump + backup demand (kW), shape (C, H), and heat delivered (kW_th)."""
    floor = customers["floor_m2"].to_numpy(dtype=float)
    annual_heat = A("hp_specific_heat_kwh_m2") * floor                       # kWh_th in the reference year
    dhw_annual = annual_heat * A("hp_dhw_share")
    sh_annual_ref = annual_heat - dhw_annual
    dh_ref = degree_hours(weather_ref["t_air"].to_numpy(dtype=float)).sum()
    ua = sh_annual_ref / dh_ref                                               # kW_th per K
    t = weather["t_air"].to_numpy(dtype=float)
    sh = np.outer(ua, degree_hours(t))                                        # kW_th
    n_days = len(t) // 24
    hod = hour_of_day(weather.index)
    dhw = np.outer(dhw_annual / 365.0, DHW_SHAPE[hod])                        # kW_th (daily energy × shape)
    types = heat_pump_types(len(customers))
    t_src_air = t
    t_src_ground = np.full_like(t, weather_ref["t_air"].mean() - 5.0)        # soil ≈ annual mean air T, −5 K brine
    el = np.zeros_like(sh)
    design_load = ua * max(A("hp_balance_temp_c") - design_temp_c, 1.0)       # kW_th at design temperature
    hp_cap = A("hp_capacity_share") * design_load                             # HP thermal capacity
    for src in ("air", "ground"):
        m = types == src
        if not m.any():
            continue
        tsrc = t_src_air if src == "air" else t_src_ground
        cop_sh = cop((40.0 - t) - tsrc, src)
        cop_dhw = cop(50.0 - tsrc, src)
        heat = sh[m] + dhw[m]
        hp_heat = np.minimum(heat, hp_cap[m, None])
        backup = heat - hp_heat
        # share of HP heat that is hot water vs space heating (for the COP mix)
        frac_dhw = np.divide(dhw[m], heat, out=np.zeros_like(heat), where=heat > 0)
        el[m] = hp_heat * (frac_dhw / cop_dhw + (1 - frac_dhw) / cop_sh) + backup
    _ = n_days
    return el.astype(np.float32), (sh + dhw).astype(np.float32)
