"""Hosting capacity: the conservative static method and the time-series view.

Static (worst-case) method used in connection studies:
  summer snapshot: every PV system at its AC rating while each household draws its minimum
  load (lowest daytime summer load in the simulated profiles, all weather years);
  winter snapshot: the hour with the highest simulated neighbourhood demand in the whole
  record (2015–2025) with today's heat pumps and EVs; a new charger counts at full power.
A deployment is accepted when both snapshots are within limits.

Time-series method: the same deployment is simulated for every hour of every weather year
(2015–2025) with real weather; hosting capacity is the largest deployment without any
violation (all customers firm, nobody curtailed).
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from ..config import A, settings
from ..paths import processed
from ..timeindex import local_standard, year_index
from .netmodel import NetModel
from .scenario import Scenario, customers, year_profiles

log = logging.getLogger(__name__)


# ------------------------------------------------------------------------------ snapshot inputs

def min_daytime_load() -> np.ndarray:
    """Per-customer minimum household load (kW) on summer days 10–16 h (× sensitivity factor)."""
    return _min_daytime_load() * A("static_min_load_factor") * A("household_load_factor")


@lru_cache(maxsize=1)
def _min_daytime_load() -> np.ndarray:
    out = None
    for y in settings().weather_years:
        idx = local_standard(year_index(y))
        m = (idx.month >= 5) & (idx.month <= 8) & (idx.hour >= 10) & (idx.hour < 16)
        base = year_profiles(y)["base"][:, m].min(axis=1)
        out = base if out is None else np.minimum(out, base)
    return out.astype(float)


@lru_cache(maxsize=1)
def winter_design_load() -> dict[str, np.ndarray]:
    """Per-customer demand at the peak hour of the record (today's heat pumps and EVs)."""
    from ..profiles.weather import load_weather
    from .run import background_masks

    C = len(customers())
    hp, ev = background_masks(C)
    best = None
    for y in settings().weather_years:
        prof = year_profiles(y)
        total = prof["base"].sum(axis=0) + prof["hp"][hp].sum(axis=0) + prof["ev"][ev].sum(axis=0)
        h = int(np.argmax(total))
        if best is None or total[h] > best[2]:
            best = (y, h, float(total[h]))
    y, h, peak = best
    prof = year_profiles(y)
    t = float(load_weather(y)["t_air"].iloc[h])
    return {"base": prof["base"][:, h].astype(float), "hp": prof["hp"][:, h].astype(float),
            "ev": prof["ev"][:, h].astype(float), "year": y, "hour": h, "t_air": t, "peak_kw": peak}


def cap_kw() -> np.ndarray:
    cust = customers()
    return np.minimum(cust["kwp"].to_numpy() / A("pv_dc_ac_ratio"), cust["fuse_kva"].to_numpy()).astype(float)


def summer_snapshot(pv_mask: np.ndarray, pv_kw: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Net injection (kW) and reactive power (kvar) of the static summer worst case."""
    load = min_daytime_load()
    pv = np.where(pv_mask, cap_kw() if pv_kw is None else pv_kw, 0.0)
    # (cap_kw follows the DC/AC ratio, so the static method sees the same inverters)
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    return pv - load, -load * tanphi


def winter_snapshot(hp_mask: np.ndarray, ev_mask: np.ndarray, extra_kw: np.ndarray | None = None):
    w = winter_design_load()
    load = w["base"] + np.where(hp_mask, w["hp"], 0.0) + np.where(ev_mask, w["ev"], 0.0)
    if extra_kw is not None:
        load = load + extra_kw
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    return -load, -load * tanphi


# ------------------------------------------------------------------------------ static method

def nodal_pv_hc(model: NetModel, base_pv: np.ndarray | None = None, max_kw: float = 250.0) -> np.ndarray:
    """Largest PV (kW AC) at each house alone that keeps the summer snapshot within limits."""
    C = model.C
    base_pv = np.zeros(C, dtype=bool) if base_pv is None else base_pv
    p0, q0 = summer_snapshot(base_pv)
    lo, hi = np.zeros(C), np.full(C, max_kw)
    eye = np.eye(C)
    for _ in range(24):
        mid = (lo + hi) / 2.0
        P = p0[:, None] + eye * mid[None, :]
        Q = np.repeat(q0[:, None], C, axis=1)
        ok = ~model.violations(model.solve(P, Q), P).export_side
        lo = np.where(ok, mid, lo)
        hi = np.where(ok, hi, mid)
    return lo


def nodal_import_hc(model: NetModel, hp_mask: np.ndarray, ev_mask: np.ndarray, max_kw: float = 250.0) -> np.ndarray:
    """Largest extra load (kW) at each house alone in the winter snapshot."""
    C = model.C
    p0, q0 = winter_snapshot(hp_mask, ev_mask)
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    lo, hi = np.zeros(C), np.full(C, max_kw)
    eye = np.eye(C)
    for _ in range(24):
        mid = (lo + hi) / 2.0
        P = p0[:, None] - eye * mid[None, :]
        Q = q0[:, None] - eye * (mid * tanphi)[None, :]
        ok = ~model.violations(model.solve(P, Q), P).import_side
        lo = np.where(ok, mid, lo)
        hi = np.where(ok, hi, mid)
    return lo


def static_adopters(model: NetModel, order: np.ndarray, hp_mask: np.ndarray, ev_mask: np.ndarray) -> int:
    """Largest m such that the first m adopters of `order` pass both static snapshots."""
    C = model.C
    masks = np.zeros((C, C + 1), dtype=bool)
    for m in range(1, C + 1):
        masks[order[:m], m] = True
    load = min_daytime_load()
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    P = np.where(masks, cap_kw()[:, None], 0.0) - load[:, None]
    Q = np.repeat((-load * tanphi)[:, None], C + 1, axis=1)
    ok_summer = ~model.violations(model.solve(P, Q), P).export_side
    bad = np.flatnonzero(~ok_summer)
    return int(bad[0] - 1) if len(bad) else C


# ------------------------------------------------------------------------------ time-series view

def year_violations(model: NetModel, scn: Scenario) -> dict:
    arr = scn.arrays()
    res = model.solve(arr["p_net"], arr["q"])
    v = model.violations(res)
    return {"violations": v, "result": res, "arrays": arr}


def uncontrolled_ok(model: NetModel, pv_mask: np.ndarray, hp_mask: np.ndarray, ev_mask: np.ndarray,
                    years, screen_v: float = 0.012, screen_pct: float = 6.0) -> bool:
    """True when the deployment has no export-side violation in any hour of `years`.

    A linear screen flags candidate hours; only those are solved with the nonlinear power flow.
    The screen margins are validated against full nonlinear runs (see validation page).
    """
    for y in years:
        scn = Scenario(year=y, pv=pv_mask, hp=hp_mask, ev=ev_mask)
        arr = scn.arrays()
        p, q = arr["p_net"].astype(float), arr["q"].astype(float)
        if len(model.export_critical_hours(p, q)):
            return False
    return True


def ts_adopters(model: NetModel, order: np.ndarray, hp_mask: np.ndarray, ev_mask: np.ndarray, years,
                lo: int) -> int:
    """Largest m (≥ lo) such that the first m adopters run the whole record without violation."""
    C = model.C

    def ok(m: int) -> bool:
        mask = np.zeros(C, dtype=bool)
        mask[order[:m]] = True
        return uncontrolled_ok(model, mask, hp_mask, ev_mask, years)

    if not ok(lo):
        return lo
    hi = C + 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def save_json(name: str, obj) -> None:
    import json
    processed("results", name).write_text(json.dumps(obj, indent=1), encoding="utf-8")
