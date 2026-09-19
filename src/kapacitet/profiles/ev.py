"""Home charging of electric cars: one car per EV household, stochastic daily use.

Daily distance: lognormal around the Swedish mean annual mileage (Trafikanalys); energy use
per km from Energimyndigheten; the car plugs in on arrival and charges at the wallbox power
until the day's energy is replaced (uncontrolled charging). The plug-in window (arrival to
next departure) and the energy needed are kept, so import envelopes can be evaluated by the
energy that is still undelivered at departure.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import A
from ..timeindex import local_standard


@dataclass
class EvSessions:
    customer: np.ndarray    # (S,) customer index
    start: np.ndarray       # (S,) hour index of arrival (plug-in)
    end: np.ndarray         # (S,) hour index of departure (exclusive)
    energy_kwh: np.ndarray  # (S,) energy to deliver from the grid


def simulate(n_customers: int, index: pd.DatetimeIndex, seed: int = 0) -> tuple[np.ndarray, EvSessions]:
    """Uncontrolled charging power (kW, (C, H)) and the charging sessions."""
    rng = np.random.default_rng(5000 + seed)
    H = len(index)
    local = local_standard(index)
    n_days = H // 24
    weekday = np.array([local[d * 24].weekday() for d in range(n_days)])
    # Mean distance on driving days, so that the annual mean mileage matches the source.
    mean_km_day = A("ev_km_per_year") / (365.0 * A("ev_drive_probability"))
    sigma = A("ev_daily_km_sigma")
    mu = np.log(mean_km_day) - 0.5 * sigma ** 2
    kwh_per_km = A("ev_kwh_per_km") / A("ev_charging_efficiency")
    p_max = A("ev_charger_kw")
    power = np.zeros((n_customers, H), dtype=np.float32)
    cs, st, en, ek = [], [], [], []
    for c in range(n_customers):
        km = rng.lognormal(mu, sigma, n_days)
        drives = rng.random(n_days) < A("ev_drive_probability")
        km = np.where(drives, km, 0.0)
        for d in range(n_days):
            if km[d] <= 0:
                continue
            wk = weekday[d] >= 5
            arr = rng.normal(A("ev_arrival_weekend_h") if wk else A("ev_arrival_weekday_h"),
                             A("ev_arrival_sd_h") * (1.5 if wk else 1.0))
            arr = float(np.clip(arr, 12.0, 23.5))
            nxt_wk = weekday[min(d + 1, n_days - 1)] >= 5
            dep = A("ev_departure_weekend_h") if nxt_wk else A("ev_departure_weekday_h")
            s = d * 24 + int(arr)
            e = min((d + 1) * 24 + int(dep), H)
            if s >= H:
                continue
            need = km[d] * kwh_per_km
            cs.append(c)
            st.append(s)
            en.append(e)
            ek.append(need)
            h = s
            first_frac = 1.0 - (arr - int(arr))  # plugged in part-way through the first hour
            rem = need
            while rem > 1e-9 and h < e:
                cap = p_max * (first_frac if h == s else 1.0)
                p = min(cap, rem)
                power[c, h] += p
                rem -= p
                h += 1
    sessions = EvSessions(np.array(cs, dtype=np.int32), np.array(st, dtype=np.int32), np.array(en, dtype=np.int32),
                          np.array(ek, dtype=np.float32))
    return power, sessions
