"""Precompute every result served by the API (python -m kapacitet compute)."""

from __future__ import annotations

import json
import logging
import time
import warnings
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from .. import __version__
from ..config import A, settings
from ..paths import processed
from ..timeindex import day_of_year, hour_of_day, year_index
from . import compliance, doe, economics, hosting
from .netmodel import NetModel
from .scenario import Scenario, adoption_order, customers, mask_from_share

log = logging.getLogger(__name__)
REF_POLICY = "maxmin"
N_ORDERS = 20
_MODEL: NetModel | None = None


# ------------------------------------------------------------------------------------ helpers

def background_masks(C: int) -> tuple[np.ndarray, np.ndarray]:
    """Heat pumps and EVs present today (fixed seeds; independent of PV adoption)."""
    return (mask_from_share(adoption_order(101, C), A("today_hp_share")),
            mask_from_share(adoption_order(202, C), A("today_ev_share")))


def pv_mask(order: np.ndarray, k: int, C: int) -> np.ndarray:
    m = np.zeros(C, dtype=bool)
    m[order[:k]] = True
    return m


def largest_true(pred, lo: int, hi: int) -> int:
    """Largest k in [lo, hi] with pred(k) True, assuming pred(lo) and monotone."""
    if pred(hi):
        return hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if pred(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _write(name: str, obj) -> None:
    processed("results", name).write_text(json.dumps(obj, indent=1, default=_default), encoding="utf-8")


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


# ------------------------------------------------------------------------------------ worker side

def _init_worker() -> None:
    global _MODEL
    warnings.filterwarnings("ignore")
    try:
        from threadpoolctl import threadpool_limits
        threadpool_limits(2)
    except Exception:  # pragma: no cover
        pass
    _MODEL = NetModel()


def model() -> NetModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = NetModel()
    return _MODEL


def curtailment(order: np.ndarray, ms: int, k: int, years, hp, ev, *, method: str = "doe",
                policy: str = REF_POLICY) -> dict:
    """Curtailment of the non-firm adopters order[ms:k] summed over `years`."""
    m = model()
    C = m.C
    nf = order[ms:k]
    pvm = pv_mask(order, k, C)
    curt = pot = 0.0
    per_year = []
    kappa = None
    for y in years:
        arr = Scenario(year=y, pv=pvm, hp=hp, ev=ev).arrays()
        pot_y = float(arr["pv"][nf].sum())
        if len(nf) == 0:
            per_year.append({"year": y, "curtailed_kwh": 0.0, "potential_kwh": pot_y})
            continue
        if method == "doe":
            r = doe.compute(m, arr, nf, policy=policy)
            c_y = float(r.curtailed.sum())
        else:
            if kappa is None:
                p_s, q_s = hosting.summer_snapshot(pv_mask(order, ms, C))
                kappa, _ = doe.fixed_cap(m, arr, nf, p_s, q_s)
            cap = doe.customer_caps()[nf]
            c_y = float(np.maximum(np.clip(arr["p_net"][nf], 0, None) - kappa * cap[:, None], 0).sum())
        curt += c_y
        pot += pot_y
        per_year.append({"year": y, "curtailed_kwh": c_y, "potential_kwh": pot_y})
    return {"curtailed_kwh": curt, "potential_kwh": pot, "z_pct": 100.0 * curt / pot if pot > 0 else 0.0,
            "kappa": kappa, "per_year": per_year}


def analyze_order(args: tuple[int, tuple[int, ...], float]) -> dict:
    o, years, z_star = args
    m = model()
    C = m.C
    hp, ev = background_masks(C)
    order = adoption_order(o, C)
    cap = doe.customer_caps()
    kwp = customers()["kwp"].to_numpy()
    t0 = time.perf_counter()
    ms = hosting.static_adopters(m, order, hp, ev)
    mts = hosting.ts_adopters(m, order, hp, ev, years, lo=ms)
    yf = largest_true(lambda k: curtailment(order, ms, k, years, hp, ev, method="fixed")["z_pct"] <= z_star, ms, C)
    yd = largest_true(lambda k: curtailment(order, ms, k, years, hp, ev)["z_pct"] <= z_star, mts, C)
    z_at_yd = curtailment(order, ms, yd, years, hp, ev)
    z_at_yf = curtailment(order, ms, yf, years, hp, ev, method="fixed")

    def kw(k):
        return {"adopters": int(k), "kw_ac": float(cap[order[:k]].sum()), "kwp": float(kwp[order[:k]].sum())}

    return {"order": o, "static": kw(ms), "timeseries": kw(mts), "fixed_cap": kw(yf), "doe": kw(yd),
            "z_doe_pct": z_at_yd["z_pct"], "z_fixed_pct": z_at_yf["z_pct"], "kappa": z_at_yf["kappa"],
            "seconds": time.perf_counter() - t0}


def _curve_point(args) -> dict:
    o, ms, k, years = args
    m = model()
    hp, ev = background_masks(m.C)
    order = adoption_order(o, m.C)
    d = curtailment(order, ms, k, years, hp, ev)
    f = curtailment(order, ms, k, years, hp, ev, method="fixed")
    cap = doe.customer_caps()
    return {"adopters": k, "kw_ac": float(cap[order[:k]].sum()), "z_doe_pct": d["z_pct"],
            "z_fixed_pct": f["z_pct"], "kappa": f["kappa"],
            "z_doe_by_year": [100 * p["curtailed_kwh"] / p["potential_kwh"] if p["potential_kwh"] else 0
                              for p in d["per_year"]]}


def _risk_case(args) -> dict:
    o, ms, k, year, seed = args
    m = model()
    hp, ev = background_masks(m.C)
    order = adoption_order(o, m.C)
    nf = order[ms:k]
    arr = Scenario(year=year, pv=pv_mask(order, k, m.C), hp=hp, ev=ev, load_seed=seed).arrays()
    r = doe.compute(m, arr, nf, policy=REF_POLICY)
    pot = arr["pv"][nf].sum(axis=1)
    return {"year": year, "seed": seed, "curtailed_kwh": r.curtailed.sum(axis=1).astype(float).tolist(),
            "potential_kwh": pot.astype(float).tolist(), "critical_hours": r.info.get("critical_hours", 0),
            "verified_violations": r.verified_violations}


# ------------------------------------------------------------------------------------ orchestration


def _versions() -> dict[str, str]:
    """Library versions behind the numbers, for reproducibility."""
    import importlib.metadata as md

    out = {"kapacitet": __version__}
    for name in ("numpy", "scipy", "pandas", "pandapower", "cvxpy", "pvlib"):
        try:
            out[name] = md.version(name)
        except Exception:  # pragma: no cover - not installed
            pass
    return out

def compute_all(quick: bool = False) -> None:
    warnings.filterwarnings("ignore")
    cfg = settings()
    years = cfg.weather_years if not quick else (cfg.reference_year,)
    n_orders = N_ORDERS if not quick else 4
    z_star = A("z_star_pct")
    m = model()
    C = m.C
    hp, ev = background_masks(C)
    t0 = time.perf_counter()

    # 1) nodal hosting capacity maps
    nodal_pv = hosting.nodal_pv_hc(m)
    nodal_imp = hosting.nodal_import_hc(m, hp, ev)
    cust = customers()
    _write("nodal.json", {
        "customers": cust["cid"].tolist(),
        "pv_kw": np.round(nodal_pv, 2), "import_kw": np.round(nodal_imp, 2),
        "fuse_kva": cust["fuse_kva"].tolist(), "cap_kw": np.round(doe.customer_caps(), 2),
        "winter_design": {k: v for k, v in hosting.winter_design_load().items()
                          if k in ("year", "hour", "t_air", "peak_kw")},
    })
    log.info("nodal HC done (%.0f s)", time.perf_counter() - t0)

    # 2) hosting capacity per adoption order (parallel)
    jobs = [(o, tuple(years), z_star) for o in range(n_orders)]
    with ProcessPoolExecutor(max_workers=min(6, n_orders), initializer=_init_worker) as ex:
        per_order = list(ex.map(analyze_order, jobs))
    log.info("orders done (%.0f s)", time.perf_counter() - t0)

    def stats(key: str, field: str) -> dict:
        v = np.array([r[key][field] for r in per_order], dtype=float)
        return {"median": float(np.median(v)), "p10": float(np.percentile(v, 10)),
                "p90": float(np.percentile(v, 90)), "min": float(v.min()), "max": float(v.max())}

    summary = {
        "z_star_pct": z_star, "years": list(years), "n_orders": n_orders, "customers": C,
        "total_cap_kw": float(doe.customer_caps().sum()), "total_kwp": float(cust["kwp"].sum()),
        "measures": {k: {"kw_ac": stats(k, "kw_ac"), "kwp": stats(k, "kwp"), "adopters": stats(k, "adopters")}
                     for k in ("static", "timeseries", "fixed_cap", "doe")},
        "per_order": per_order,
    }
    _write("hosting_summary.json", summary)

    # 3) showcase: the order whose DOE capacity is the median
    yd = np.array([r["doe"]["kw_ac"] for r in per_order])
    best = per_order[int(np.argsort(yd)[len(yd) // 2])]
    o = best["order"]
    ms, mts, k = best["static"]["adopters"], best["timeseries"]["adopters"], best["doe"]["adopters"]
    showcase(o, ms, mts, k, years, quick)
    log.info("showcase done (%.0f s)", time.perf_counter() - t0)

    # 4) curve of curtailment vs deployment for the showcase order
    ks = sorted(set(list(range(ms, C + 1, 4 if not quick else 12)) + [C, mts, k]))
    with ProcessPoolExecutor(max_workers=6, initializer=_init_worker) as ex:
        pts = list(ex.map(_curve_point, [(o, ms, kk, tuple(years)) for kk in ks]))
    _write("zcurve.json", {"order": o, "static_adopters": ms, "timeseries_adopters": mts, "doe_adopters": k,
                           "z_star_pct": z_star, "years": list(years), "points": pts})
    log.info("curve done (%.0f s)", time.perf_counter() - t0)

    # 5) risk: every weather year × load seeds for the showcase deployment
    seeds = (0, 1, 2) if not quick else (0,)
    cases = [(o, ms, k, y, s) for y in years for s in seeds]
    with ProcessPoolExecutor(max_workers=6, initializer=_init_worker) as ex:
        risk = list(ex.map(_risk_case, cases))
    write_risk(risk, o, ms, k)
    log.info("risk done (%.0f s)", time.perf_counter() - t0)

    write_series(cfg.reference_year)

    compute_extra(quick, t0=t0)


def compute_extra(quick: bool = False, t0: float | None = None) -> None:
    """Stages that build on the showcase: request baselines, 2030 scenarios, tornado, validation.

    Heavy stages use 4 worker processes so the run fits next to a desktop session in 16 GB.
    """
    from . import national, run_extra

    warnings.filterwarnings("ignore")
    cfg = settings()
    t0 = t0 if t0 is not None else time.perf_counter()
    years = cfg.weather_years if not quick else (cfg.reference_year,)
    z_star = A("z_star_pct")
    sc = json.loads(processed("results", "showcase.json").read_text(encoding="utf-8"))
    o, ms = sc["order"], sc["firm"]
    national.build()
    run_extra.request_baselines(years)
    log.info("request baselines done (%.0f s)", time.perf_counter() - t0)
    run_extra.scenarios_2030(o, ms, cfg.reference_year, quick, workers=4)
    log.info("2030 scenarios done (%.0f s)", time.perf_counter() - t0)
    run_extra.tornado(o, years, z_star, workers=4)
    log.info("tornado done (%.0f s)", time.perf_counter() - t0)
    run_extra.validation()
    _write("meta.json", {"computed_at": pd.Timestamp.now(tz="UTC").isoformat(), "quick": quick,
                         "seconds": time.perf_counter() - t0, "site": cfg.site.name,
                         "reference_year": cfg.reference_year, "policy": REF_POLICY,
                         "years": list(years), "versions": _versions()})
    log.info("compute finished in %.0f s", time.perf_counter() - t0)


def showcase(o: int, ms: int, mts: int, k: int, years, quick: bool) -> None:
    """Detailed results for one representative deployment in the reference year."""
    cfg = settings()
    y = cfg.reference_year
    m = model()
    C = m.C
    hp, ev = background_masks(C)
    order = adoption_order(o, C)
    nf = order[ms:k]
    firm = order[:ms]
    pvm = pv_mask(order, k, C)
    arr = Scenario(year=y, pv=pvm, hp=hp, ev=ev).arrays()
    idx = year_index(y)
    H = len(idx)
    cust = customers()

    policies = {}
    results = {}
    for pol in ("maxmin", "prorata", "propfair", "maxtotal"):
        t0 = time.perf_counter()
        r = doe.compute(m, arr, nf, policy=pol)
        results[pol] = r
        pot = arr["pv"][nf].sum(axis=1)
        curt = r.curtailed.sum(axis=1)
        limited_h = (r.curtailed > 1e-3).sum(axis=1)
        frac = np.divide(curt, pot, out=np.zeros_like(curt), where=pot > 0)
        jain = float(frac.sum() ** 2 / (len(frac) * (frac ** 2).sum())) if (frac ** 2).sum() > 0 else 1.0
        econ = economics.value_sek(r.curtailed, y)
        dist = m.dist_m[nf]
        policies[pol] = {
            "curtailed_kwh": float(curt.sum()), "potential_kwh": float(pot.sum()),
            "z_pct": float(100 * curt.sum() / pot.sum()) if pot.sum() else 0.0,
            "z_all_pct": float(100 * curt.sum() / arr["pv"].sum()),
            "intervention_hours": int((r.curtailed.sum(axis=0) > 1e-3).sum()),
            "critical_hours": int(r.info.get("critical_hours", 0)),
            "customer_hours_limited": int(limited_h.sum()),
            "max_customer_z_pct": float(100 * frac.max()) if len(frac) else 0.0,
            "jain_index": jain,
            "corr_distance_curtailment": float(np.corrcoef(dist, frac)[0, 1]) if frac.std() > 0 else 0.0,
            "economics": econ, "iterations": r.iterations, "backoff_hours": r.backoff_hours,
            "verified_violations": r.verified_violations, "seconds": time.perf_counter() - t0,
            "per_customer": {"cid": cust["cid"].to_numpy()[nf].tolist(), "curtailed_kwh": np.round(curt, 1),
                             "z_pct": np.round(100 * frac, 3), "hours_limited": limited_h,
                             "dist_m": np.round(dist, 1)},
        }
        log.info("showcase %s: Z=%.2f%% critical h=%d (%.1f s)", pol, policies[pol]["z_pct"],
                 policies[pol]["critical_hours"], policies[pol]["seconds"])

    r = results[REF_POLICY]
    # Full-year nonlinear verification with the envelopes applied.
    p_doe = arr["p_net"].astype(float).copy()
    p_doe[nf] = np.minimum(p_doe[nf], 0.0)
    p_doe[nf] += np.minimum(np.clip(arr["p_net"][nf], 0, None), r.envelope(H))
    res_doe = m.solve(p_doe, arr["q"])
    v_doe = m.violations(res_doe, p_doe)
    res_unc = m.solve(arr["p_net"], arr["q"])
    v_unc = m.violations(res_unc, arr["p_net"])
    p_static = Scenario(year=y, pv=pv_mask(order, ms, C), hp=hp, ev=ev).arrays()
    res_st = m.solve(p_static["p_net"], p_static["q"])
    v_st = m.violations(res_st, p_static["p_net"])

    # Screen validation: every hour the full nonlinear run flags must be in the linear screen.
    screened = set(m.screen_export(arr["p_net"], arr["q"]).tolist())
    missed = int(sum(1 for h in np.flatnonzero(v_unc.export_side) if h not in screened))

    hod, doy = hour_of_day(idx), day_of_year(idx)
    n_days = H // 24

    def grid(values: np.ndarray) -> list:
        g = np.zeros((24, n_days))
        g[hod, doy] = values
        return np.round(g, 3).tolist()

    heat = {
        "limited_customers": grid((r.curtailed > 1e-3).sum(axis=0)),
        "curtailed_kw": grid(r.curtailed.sum(axis=0)),
        "uncontrolled_violation": grid(v_unc.export_side.astype(float)),
        "uncontrolled_vmax": grid(v_unc.vmax),
        "uncontrolled_trafo_loading": grid(v_unc.trafo_loading),
    }
    _write("heatmaps.json", {"year": y, "days": n_days, "tz": "UTC+1", **heat})

    showcase_obj = {
        "year": y, "order": o, "adopters": k, "firm": ms, "timeseries_adopters": mts,
        "firm_cids": cust["cid"].to_numpy()[firm].tolist(), "nonfirm_cids": cust["cid"].to_numpy()[nf].tolist(),
        "pv_cids": cust["cid"].to_numpy()[order[:k]].tolist(),
        "kw_ac_firm": float(doe.customer_caps()[firm].sum()), "kw_ac_total": float(doe.customer_caps()[order[:k]].sum()),
        "policies": {pol: {kk: vv for kk, vv in d.items()} for pol, d in policies.items()},
        "verification": {
            "doe_full_year_violations": int(v_doe.export_side.sum()),
            "doe_max_voltage_pu": float(v_doe.vmax.max()), "doe_max_trafo_loading_pct": float(v_doe.trafo_loading.max()),
            "doe_max_line_loading_pct": float(v_doe.max_line_loading.max()),
            "uncontrolled_violation_hours": int(v_unc.export_side.sum()),
            "uncontrolled_overvoltage_hours": int(v_unc.over_v.sum()),
            "uncontrolled_trafo_overload_hours": int(v_unc.trafo_rev.sum()),
            "uncontrolled_line_overload_hours": int(v_unc.line_rev.sum()),
            "uncontrolled_max_voltage_pu": float(v_unc.vmax.max()),
            "uncontrolled_max_trafo_loading_pct": float(v_unc.trafo_loading.max()),
            "static_violation_hours": int(v_st.export_side.sum()),
            "screen_missed_hours": missed, "screen_candidate_hours": len(screened),
            "pf_iterations": int(res_doe.iterations),
        },
        "energy": {
            "pv_kwh": float(arr["pv"].sum()), "load_kwh": float(arr["load"].sum()),
            "curtailed_kwh": float(r.curtailed.sum()),
        },
    }
    _write("showcase.json", showcase_obj)

    # The paperwork the agreement leaves behind: one row per curtailment with the component
    # that was out of room, and the annual filing derived from it.
    trail = compliance.audit_trail(m, r, arr, y, cids=cust["cid"].to_numpy())
    trail.events.to_csv(processed("results", "audit_trail.csv"), index=False)
    report = compliance.annual_report(trail, r, cids=cust["cid"].to_numpy())
    _write("compliance.json", report)
    processed("results", "compliance.txt").write_text(compliance.report_text(report), encoding="utf-8")
    log.info("compliance: %d events in %d hours, %.0f kWh",
             len(trail.events), trail.hours_with_curtailment, trail.energy_kwh)

    # State payloads for the time slider (uint8, time-major) and per-hour aggregates.
    write_state("static", res_st, v_st, p_static["p_net"], p_static["pv"], p_static["load"], None)
    write_state("uncontrolled", res_unc, v_unc, arr["p_net"], arr["pv"], arr["load"], None)
    pv_real = arr["pv"].astype(float).copy()
    pv_real[nf] -= r.curtailed
    write_state("doe", res_doe, v_doe, p_doe, pv_real, arr["load"], r.curtailed.sum(axis=0))
    # envelopes of the non-firm customers (kW, float16) for the request / customer views
    env = r.envelope(H).astype(np.float16)
    np.save(processed("ui", "envelopes_doe.npy"), env)
    _write("envelopes_index.json", {"cids": cust["cid"].to_numpy()[nf].tolist(), "hours": H})


def write_state(name: str, res, viol, p_net, pv, load, curtailed_total) -> None:
    m = model()
    H = res.v.shape[1]
    vm = np.abs(res.v)                                               # (n_bus, H)
    v_q = np.clip(np.round((vm - 0.85) / 0.30 * 255), 0, 255).astype(np.uint8)
    l_q = np.clip(np.round(res.line_loading / 150.0 * 255), 0, 255).astype(np.uint8)
    p_q = np.clip(np.round((np.asarray(p_net) + 25.0) / 50.0 * 255), 0, 255).astype(np.uint8)
    blob = np.concatenate([v_q.T.ravel(), l_q.T.ravel(), p_q.T.ravel()])   # time-major blocks
    processed("ui", f"state_{name}.bin").write_bytes(blob.tobytes())
    agg = {
        "trafo_loading": np.round(res.trafo_loading, 2), "trafo_p_kw": np.round(res.trafo_p_mw * 1000, 2),
        "vmax": np.round(viol.vmax, 4), "vmin": np.round(viol.vmin, 4),
        "max_line_loading": np.round(viol.max_line_loading, 2),
        "violation": viol.export_side.astype(int) | viol.import_side.astype(int),
        "pv_kw": np.round(np.asarray(pv).sum(axis=0), 2), "load_kw": np.round(np.asarray(load).sum(axis=0), 2),
        "curtailed_kw": np.round(curtailed_total, 2) if curtailed_total is not None else None,
    }
    header = {
        "name": name, "hours": H, "n_bus": int(vm.shape[0]), "n_line": int(res.line_loading.shape[0]),
        "n_cust": int(np.asarray(p_net).shape[0]),
        "layout": "time-major blocks: V[H][bus] uint8, loading[H][line] uint8, p[H][cust] uint8",
        "v_offset": 0.85, "v_scale": 0.30 / 255, "loading_scale": 150.0 / 255, "p_offset": -25.0,
        "p_scale": 50.0 / 255, "bus_index": m.net.bus.index.tolist(), "line_index": m.net.line.index.tolist(),
        "aggregates": agg,
    }
    processed("ui", f"state_{name}.json").write_text(json.dumps(header, default=_default), encoding="utf-8")


def write_risk(risk: list[dict], o: int, ms: int, k: int) -> None:
    rows = []
    for r in risk:
        c, p = np.array(r["curtailed_kwh"]), np.array(r["potential_kwh"])
        rows.append({"year": r["year"], "seed": r["seed"], "z_pct": 100 * c.sum() / p.sum() if p.sum() else 0.0,
                     "curtailed_kwh": float(c.sum()), "potential_kwh": float(p.sum()),
                     "critical_hours": r["critical_hours"], "verified_violations": r["verified_violations"]})
    z = np.array([row["z_pct"] for row in rows])
    tail = np.sort(z)[-max(1, int(np.ceil(0.05 * len(z)))):]
    # per-customer annual curtailment share across all cases
    cust_z = np.array([np.divide(np.array(r["curtailed_kwh"]), np.array(r["potential_kwh"]),
                                 out=np.zeros(len(r["potential_kwh"])), where=np.array(r["potential_kwh"]) > 0)
                       for r in risk]) * 100
    ghi = {}
    from ..profiles.weather import load_weather
    for y in sorted({row["year"] for row in rows}):
        ghi[y] = float(load_weather(y)["ghi"].sum() / 1000.0)
    _write("risk.json", {
        "order": o, "firm": ms, "adopters": k, "cases": rows,
        "stats": {"mean_z_pct": float(z.mean()), "p90_z_pct": float(np.percentile(z, 90)),
                  "worst_z_pct": float(z.max()), "cvar95_z_pct": float(tail.mean()), "n_cases": int(len(z))},
        "customer_z_pct": {"mean": np.round(cust_z.mean(axis=0), 3), "p90": np.round(np.percentile(cust_z, 90, axis=0), 3),
                           "max": np.round(cust_z.max(axis=0), 3)},
        "ghi_kwh_m2": ghi,
    })


def write_series(year: int) -> None:
    from ..profiles.weather import load_weather
    w = load_weather(year)
    p = economics.hourly(year)
    idx = year_index(year)
    _write(f"series_{year}.json", {
        "year": year, "start_utc": idx[0].isoformat(), "hours": len(idx), "tz_display": "UTC+1",
        "ghi": np.round(w["ghi"].to_numpy(), 1), "t_air": np.round(w["t_air"].to_numpy(), 1),
        "price_sek_kwh": np.round(p["sek_kwh"].to_numpy(), 4) if p is not None else None,
    })
