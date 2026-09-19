"""Additional precomputations: sensitivity (tornado), 2030 scenarios, request baselines, validation."""

from __future__ import annotations

import json
import logging
import time
import warnings

import numpy as np

from ..config import A, set_overrides, settings
from ..paths import processed
from . import doe, doe_import, hosting
from .netmodel import NetModel
from .scenario import Scenario, adoption_order, customers, mask_from_share, year_profiles

log = logging.getLogger(__name__)


def _write(name: str, obj) -> None:
    from .run import _default
    processed("results", name).write_text(json.dumps(obj, indent=1, default=_default), encoding="utf-8")


def _largest_true(pred, lo: int, hi: int) -> int:
    if pred(hi):
        return hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if pred(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _z_doe(m: NetModel, order, ms, k, years, hp, ev) -> float:
    C = m.C
    nf = order[ms:k]
    if len(nf) == 0:
        return 0.0
    pvm = np.zeros(C, dtype=bool)
    pvm[order[:k]] = True
    curt = pot = 0.0
    for y in years:
        arr = Scenario(year=y, pv=pvm, hp=hp, ev=ev).arrays()
        r = doe.compute(m, arr, nf)
        curt += float(r.curtailed.sum())
        pot += float(arr["pv"][nf].sum())
    return 100.0 * curt / pot if pot > 0 else 0.0


# ------------------------------------------------------------------------------ tornado

TORNADO_CASES = [
    ("mv_voltage", "MV voltage at the substation", "0.98 pu", {"vm_slack": 0.98}, {}),
    ("mv_voltage", "MV voltage at the substation", "1.03 pu", {"vm_slack": 1.03}, {}),
    ("v_margin", "Voltage planning margin", "0 %", {}, {"v_margin_pu": 0.0}),
    ("v_margin", "Voltage planning margin", "2 %", {}, {"v_margin_pu": 0.02}),
    ("trafo", "Transformer rating", "0.25 MVA", {"trafo_type": "0.25 MVA 10/0.4 kV"}, {}),
    ("trafo", "Transformer rating", "0.63 MVA", {"trafo_type": "0.63 MVA 10/0.4 kV"}, {}),
    ("dcac", "PV DC/AC ratio", "1.0", {}, {"pv_dc_ac_ratio": 1.0}),
    ("dcac", "PV DC/AC ratio", "1.4", {}, {"pv_dc_ac_ratio": 1.4}),
    ("minload", "Minimum load in the static snapshot", "×0 (no load)", {}, {"static_min_load_factor": 0.0}),
    ("minload", "Minimum load in the static snapshot", "×2", {}, {"static_min_load_factor": 2.0}),
    ("household", "Household demand", "−20 %", {}, {"household_load_factor": 0.8}),
    ("household", "Household demand", "+20 %", {}, {"household_load_factor": 1.2}),
]


def _tornado_case(args) -> dict:
    warnings.filterwarnings("ignore")
    case, o, years, z_star = args
    key, label, level, model_kw, overrides = case
    set_overrides(overrides)
    try:
        m = NetModel(**model_kw)
        from .run import background_masks
        hp, ev = background_masks(m.C)
        order = adoption_order(o, m.C)
        cap = doe.customer_caps()
        ms = hosting.static_adopters(m, order, hp, ev)
        mts = hosting.ts_adopters(m, order, hp, ev, years, lo=ms)
        yd = _largest_true(lambda k: _z_doe(m, order, ms, k, years, hp, ev) <= z_star, mts, m.C)
        return {"key": key, "label": label, "level": level, "static_kw": float(cap[order[:ms]].sum()),
                "timeseries_kw": float(cap[order[:mts]].sum()), "doe_kw": float(cap[order[:yd]].sum()),
                "static_adopters": ms, "doe_adopters": yd}
    finally:
        set_overrides(None)


def tornado(o: int, years, z_star: float, workers: int = 8) -> None:
    from concurrent.futures import ProcessPoolExecutor

    from .run import _init_worker
    base_case = ("base", "Base case", "base", {}, {})
    cases = [base_case] + TORNADO_CASES
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as ex:
        rows = list(ex.map(_tornado_case, [(c, o, tuple(years), z_star) for c in cases]))
    _write("tornado.json", {"order": o, "years": list(years), "z_star_pct": z_star, "base": rows[0],
                            "cases": rows[1:]})


# ------------------------------------------------------------------------------ 2030 scenarios

PV_SHARES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
EV_SHARES = (0.0, 0.25, 0.5, 0.75, 1.0)
HP_SHARES = (0.3, 0.6, 1.0)


def _scenario_case(args) -> dict:
    warnings.filterwarnings("ignore")
    pv_s, ev_s, hp_s, o, ms_static, year = args
    from .run import model
    m = model()
    C = m.C
    order = adoption_order(o, C)
    pvm = mask_from_share(order, pv_s)
    hp = mask_from_share(adoption_order(101, C), hp_s)
    ev = mask_from_share(adoption_order(202, C), ev_s)
    n_pv = int(pvm.sum())
    arr = Scenario(year=year, pv=pvm, hp=hp, ev=ev).arrays()
    # static verdicts
    static_pv_ok = n_pv <= ms_static
    pw, qw = hosting.winter_snapshot(hp, ev)
    vw = m.violations(m.solve(pw[:, None], qw[:, None]), pw[:, None])
    # winter snapshot uses the record peak of *today*; for the scenario take its own peak hour too
    tot = arr["load"].sum(axis=0)
    hpk = int(np.argmax(tot))
    vpk = m.violations(m.solve(arr["p_net"][:, [hpk]], arr["q"][:, [hpk]]), arr["p_net"][:, [hpk]])
    static_imp_ok = bool(~vw.import_side[0]) and bool(~vpk.import_side[0])
    # uncontrolled time series
    exp_h = len(m.export_critical_hours(arr["p_net"].astype(float), arr["q"].astype(float)))
    imp_h = len(m.import_critical_hours(arr["p_net"].astype(float), arr["q"].astype(float)))
    # DOE export
    nf = order[:n_pv][ms_static:] if n_pv > ms_static else np.zeros(0, int)
    z = 0.0
    if len(nf):
        r = doe.compute(m, arr, nf)
        pot = float(arr["pv"][nf].sum())
        z = 100 * float(r.curtailed.sum()) / pot if pot else 0.0
    # EV smart charging
    from ..profiles.ev import EvSessions
    d = np.load(processed("ev", f"ev_sessions_{year}.npz"))
    sess = EvSessions(d["customer"], d["start"], d["end"], d["energy_kwh"])
    unmet_pct = 0.0
    firm_imp = residual = 0
    if ev.any():
        ri = doe_import.smart_charging(m, arr, year_profiles(year)["ev"], sess, ev)
        unmet_pct = 100 * ri.unmet_kwh / max(ri.requested_kwh, 1e-9)
        firm_imp = ri.firm_violation_hours
        residual = ri.residual_violation_hours
    else:
        firm_imp = imp_h
    z_star = A("z_star_pct")
    static_ok = static_pv_ok and static_imp_ok
    doe_ok = (z <= z_star) and (unmet_pct <= 1.0) and firm_imp == 0 and residual == 0
    return {"pv": pv_s, "ev": ev_s, "hp": hp_s, "n_pv": n_pv, "static_pv_ok": static_pv_ok,
            "static_import_ok": static_imp_ok, "static_ok": static_ok,
            "uncontrolled_export_hours": exp_h, "uncontrolled_import_hours": imp_h,
            "doe_z_pct": z, "ev_unmet_pct": unmet_pct, "firm_import_violation_hours": firm_imp,
            "residual_import_hours": residual, "doe_ok": doe_ok,
            "peak_load_kw": float(tot.max()), "annual_load_mwh": float(tot.sum() / 1000)}


def scenarios_2030(o: int, ms_static: int, year: int, quick: bool, workers: int = 8) -> None:
    from concurrent.futures import ProcessPoolExecutor

    from .run import _init_worker
    pvs = PV_SHARES if not quick else (0.0, 0.6, 1.0)
    evs = EV_SHARES if not quick else (0.0, 1.0)
    hps = HP_SHARES if not quick else (0.3,)
    jobs = [(p, e, h, o, ms_static, year) for h in hps for e in evs for p in pvs]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as ex:
        rows = list(ex.map(_scenario_case, jobs))
    _write("scenarios.json", {"year": year, "order": o, "static_adopters": ms_static,
                              "pv_shares": list(pvs), "ev_shares": list(evs), "hp_shares": list(hps),
                              "z_star_pct": A("z_star_pct"), "ev_unmet_limit_pct": 1.0, "cases": rows})


# ------------------------------------------------------------------------------ request baselines

def request_baselines(years) -> None:
    from . import request
    from .run import background_masks, model
    m = model()
    hp, ev = background_masks(m.C)
    for name in request.BASELINES:
        t0 = time.perf_counter()
        request.precompute(m, name, years, hp, ev)
        log.info("request baseline %s (%.0f s)", name, time.perf_counter() - t0)


# ------------------------------------------------------------------------------ validation

def validation() -> None:
    """Numbers for the Method & Data page (all computed, none typed in)."""
    import pandapower as pp
    import pandas as pd
    import pvlib

    from ..grid.builder import load_net
    from ..ingest import smhi
    from ..profiles.weather import diffuse_closure_check, load_weather
    from .run import model

    cfg = settings()
    m = model()
    out: dict = {}

    # Z-bus vs pandapower Newton-Raphson on random operating points of our network
    net = load_net()
    rng = np.random.default_rng(3)
    C = m.C
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    n = 12
    load = rng.uniform(0, 6, (C, n))
    pv = rng.uniform(0, 12, (C, n)) * (rng.random((C, n)) < 0.7)
    res = m.pf.solve(pv - load, -load * tanphi, 1.0)
    dv = dl = 0.0
    t_nr = 0.0
    for h in range(n):
        net.load["p_mw"] = load[:, h] / 1000
        net.load["q_mvar"] = load[:, h] * tanphi / 1000
        net.sgen["p_mw"] = pv[:, h] / 1000
        t0 = time.perf_counter()
        pp.runpp(net, calculate_voltage_angles=False, tolerance_mva=1e-11)
        t_nr += time.perf_counter() - t0
        dv = max(dv, float(np.max(np.abs(net.res_bus.vm_pu.to_numpy() - res.vm[:, h]))))
        dl = max(dl, float(np.max(np.abs(net.res_line.loading_percent.to_numpy() - res.line_loading[:, h]))))
    big = rng.uniform(-3, 8, (C, 8760))
    t0 = time.perf_counter()
    m.pf.solve(big, -np.abs(big) * tanphi, 1.0)
    t_year = time.perf_counter() - t0
    out["powerflow"] = {"cases": n, "max_abs_dv_pu": dv, "max_abs_dloading_pct": dl,
                        "pandapower_ms_per_case": 1000 * t_nr / n, "zbus_seconds_per_year": t_year,
                        "buses": int(len(net.bus)), "lines": int(len(net.line))}

    # linear sensitivity vs nonlinear voltage for a heavy PV hour
    order = adoption_order(0, C)
    pvm = np.ones(C, dtype=bool)
    w_idx = None
    arr = Scenario(year=cfg.reference_year, pv=pvm, hp=np.zeros(C, bool), ev=np.zeros(C, bool)).arrays()
    tot = arr["p_net"].sum(axis=0)
    w_idx = np.argsort(tot)[-200:]
    p, q = arr["p_net"][:, w_idx].astype(float), arr["q"][:, w_idx].astype(float)
    v_nl = np.abs(m.solve(p, q).v[m.check_rows])
    v_lin = m.v_noload[:, None] + m.dv_dp @ np.clip(p, 0, None)
    err = v_lin - v_nl
    out["linearisation"] = {"hours": int(len(w_idx)), "mean_error_pu": float(err.mean()),
                            "max_error_pu": float(err.max()), "min_error_pu": float(err.min()),
                            "note": "Screen estimate minus nonlinear voltage on the 200 highest-export hours "
                                    "(all 120 houses with PV); positive = conservative."}
    _ = order

    # STRÅNG timestamp convention
    ghi = smhi.read_strang(cfg.strang["ghi"], cfg.reference_year)
    loc = pvlib.location.Location(cfg.site.center_lat, cfg.site.center_lon, altitude=20)
    shifts = []
    for shift in (-60, -30, -15, 0, 15, 30, 60):
        t = ghi.index + pd.Timedelta(minutes=shift)
        cs = loc.get_clearsky(t, model="ineichen")["ghi"].to_numpy()
        msk = (cs > 200) & ghi.index.month.isin([4, 5, 6, 7, 8])
        k = ghi.to_numpy()[msk] / cs[msk]
        clear = (k > 0.9) & (k < 1.15)
        hrs = ghi.index[msk][clear]
        noon = 12 - cfg.site.center_lon / 15
        hh = hrs.hour + hrs.minute / 60
        shifts.append({"shift_min": shift, "clear_hours": int(clear.sum()),
                       "morning_k": float(k[clear][hh < noon].mean()), "afternoon_k": float(k[clear][hh > noon].mean())})
    out["strang_timestamp"] = {"shifts": shifts, "conclusion": "values describe the hour mark (symmetry at 0–15 min)"}
    out["diffuse_closure"] = diffuse_closure_check(cfg.reference_year)

    # weather and PV yields
    out["weather"] = []
    for y in cfg.weather_years:
        w = load_weather(y)
        out["weather"].append({"year": y, "ghi_kwh_m2": float(w["ghi"].sum() / 1000), "t_mean_c": float(w["t_air"].mean()),
                               "t_min_c": float(w["t_air"].min()), "t_max_c": float(w["t_air"].max()),
                               "t_filled_hours": int(w["t_filled"].sum())})
    cust = customers()
    per_kwp = year_profiles(cfg.reference_year)["pv_per_kwp"]
    out["pv_yield_kwh_per_kwp"] = {"median": float(np.median(per_kwp.sum(axis=1))), "min": float(per_kwp.sum(axis=1).min()),
                                   "max": float(per_kwp.sum(axis=1).max()), "year": cfg.reference_year}
    meta = pd.read_parquet(processed("households", "pool_meta.parquet"))
    info = json.loads(processed("households", "pool_info.json").read_text(encoding="utf-8"))
    out["households"] = {"n_pool": int(len(meta)), "mean_kwh": float(meta.annual_kwh.mean()),
                         "p10_kwh": float(meta.annual_kwh.quantile(0.1)), "p90_kwh": float(meta.annual_kwh.quantile(0.9)),
                         "peak_hourly_kw_median": float(meta.peak_hourly_kw.median()),
                         "peak_10min_kw_median": float(meta.peak_10min_kw.median()),
                         "peak_1min_kw_median": float(meta.peak_1min_kw.median()),
                         "crest_raw_mean_kwh": info["crest_raw_mean_kwh"], "scale_factor": info["scale_factor"],
                         "occupant_shares": info["occupant_shares"]}
    shape = processed("validation", "household_shape.json")
    if shape.exists():
        out["household_shape"] = json.loads(shape.read_text(encoding="utf-8"))
    hp_sum = processed("heatpump", "summary.json")
    if hp_sum.exists():
        out["heat_pumps"] = json.loads(hp_sum.read_text(encoding="utf-8"))
    grid_sum = processed("grid", "summary.json")
    out["grid"] = json.loads(grid_sum.read_text(encoding="utf-8"))
    out["customers"] = {"fuse_counts": cust["fuse_a"].value_counts().to_dict(), "kwp_total": float(cust["kwp"].sum()),
                        "kwp_median": float(cust["kwp"].median()), "east_west": int(cust["east_west"].sum())}
    _write("validation.json", out)
