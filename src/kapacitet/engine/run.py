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
from ..atomic import atomic_file, write_bytes_atomic, write_json_atomic, write_text_atomic
from ..cache import on_clear
from ..config import A, settings
from ..paths import processed
from ..timeindex import day_of_year, hour_of_day, year_index
from . import compliance, doe, economics, hosting
from .delivery import share_pct
from .netmodel import NetModel
from .scenario import Scenario, adoption_order, customers, mask_from_share
from .stateblob import encode_state

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
    """One results file, replaced whole (kapacitet.atomic): the server, which rereads a file when
    its date changes, never reads one half written."""
    write_json_atomic(processed("results", name), obj, indent=1, default=_default)


def save_npy(path, array: np.ndarray) -> None:
    """np.save, replacing the file whole."""
    with atomic_file(path) as fh:
        np.save(fh, array)


def spread(values) -> dict:
    """Median, 10th and 90th percentile, minimum and maximum: how a figure spreads over the
    adoption orders, in the shape every summary of them uses."""
    v = np.asarray(values, dtype=float)
    return {"median": float(np.median(v)), "p10": float(np.percentile(v, 10)),
            "p90": float(np.percentile(v, 90)), "min": float(v.min()), "max": float(v.max())}


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


@on_clear
def _forget_model() -> None:
    global _MODEL
    _MODEL = None


def model() -> NetModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = NetModel()
    return _MODEL


def curtailment(order: np.ndarray, ms: int, k: int, years, hp, ev, *, method: str = "doe",
                policy: str = REF_POLICY, verify: str = "full") -> dict:
    """Curtailment of the non-firm adopters order[ms:k] summed over `years`.

    `verify` goes to doe.compute: the bisections pass "critical", the points they publish are
    verified in full and report `full_year_violations` (None when not checked).

    Both bases of the budget come from the same run: `z_pct` is the aggregate share (all their
    curtailed energy over all their solar energy), `z_customer_max_pct` the highest share of any
    one installation over the years, and `z_customer_year_max_pct` its highest in a single year.
    `per_customer` holds the energies behind them, in the order of order[ms:k].
    """
    m = model()
    C = m.C
    nf = order[ms:k]
    pvm = pv_mask(order, k, C)
    curt = pot = 0.0
    per_year = []
    kappa = None
    full = 0 if method == "doe" and verify == "full" else None
    cust_curt, cust_pot, year_max = np.zeros(len(nf)), np.zeros(len(nf)), np.zeros(len(nf))
    for y in years:
        arr = Scenario(year=y, pv=pvm, hp=hp, ev=ev).arrays()
        pot_y = float(arr["pv"][nf].sum())
        if len(nf) == 0:
            per_year.append({"year": y, "curtailed_kwh": 0.0, "potential_kwh": pot_y})
            continue
        if method == "doe":
            r = doe.compute(m, arr, nf, policy=policy, verify=verify)
            c_y = float(r.curtailed.sum())
            c_c = r.curtailed.astype(float).sum(axis=1)
            if full is not None:
                full += r.full_year_violations
        else:
            if kappa is None:
                p_s, q_s = hosting.summer_snapshot(pv_mask(order, ms, C))
                kappa, _ = doe.fixed_cap(m, arr, nf, p_s, q_s)
            cap = doe.customer_caps()[nf]
            cut = np.maximum(np.clip(arr["p_net"][nf], 0, None) - kappa * cap[:, None], 0)
            c_y = float(cut.sum())
            c_c = cut.astype(float).sum(axis=1)
        p_c = arr["pv"][nf].astype(float).sum(axis=1)
        year_max = np.maximum(year_max, share_pct(c_c, p_c))
        cust_curt += c_c
        cust_pot += p_c
        curt += c_y
        pot += pot_y
        per_year.append({"year": y, "curtailed_kwh": c_y, "potential_kwh": pot_y})
    cust_share = share_pct(cust_curt, cust_pot)
    return {"curtailed_kwh": curt, "potential_kwh": pot, "z_pct": 100.0 * curt / pot if pot > 0 else 0.0,
            "z_customer_max_pct": float(cust_share.max()) if len(nf) else 0.0,
            "z_customer_year_max_pct": float(year_max.max()) if len(nf) else 0.0,
            "kappa": kappa, "per_year": per_year, "full_year_violations": full,
            "per_customer": {"curtailed_kwh": cust_curt, "potential_kwh": cust_pot, "share_pct": cust_share,
                             "year_max_pct": year_max}}


def within_pct(c: dict, z_star: float) -> float:
    """The share (%) of a curtailment's installations whose own share over the years is inside
    the budget (all of them when there are none)."""
    share = c["per_customer"]["share_pct"]
    return float(100.0 * np.mean(share <= z_star + 1e-9)) if len(share) else 100.0


def analyze_order(args: tuple[int, tuple[int, ...], float]) -> dict:
    """The capacity of one adoption order by every method.

    The envelope capacity is searched on both bases of the budget, on one memo of the runs:
    `doe` holds the aggregate share to `z_star` (the headline), `doe_installation` every
    installation's own share over the years. The second can only be lower, so it is searched
    between the time-series capacity and the first. `z_doe_*` and `customers_within_pct`
    describe the `doe` point; `z_doe_installation` the other one, verified in full the same way.
    """
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
    runs: dict[int, dict] = {}

    def searched(k: int) -> dict:
        if k not in runs:
            runs[k] = curtailment(order, ms, k, years, hp, ev, verify="critical")
        return runs[k]

    yd = largest_true(lambda k: searched(k)["z_pct"] <= z_star, mts, C)
    yd_i = largest_true(lambda k: searched(k)["z_customer_max_pct"] <= z_star, mts, yd)
    z_at_yd = curtailment(order, ms, yd, years, hp, ev)
    z_at_ydi = z_at_yd if yd_i == yd else curtailment(order, ms, yd_i, years, hp, ev)
    z_at_yf = curtailment(order, ms, yf, years, hp, ev, method="fixed")
    doe.require_verified(z_at_yd["full_year_violations"], f"order {o}, {yd} adopters (aggregate budget)")
    doe.require_verified(z_at_ydi["full_year_violations"], f"order {o}, {yd_i} adopters (per-installation budget)")

    def kw(k):
        return {"adopters": int(k), "kw_ac": float(cap[order[:k]].sum()), "kwp": float(kwp[order[:k]].sum())}

    return {"order": o, "static": kw(ms), "timeseries": kw(mts), "fixed_cap": kw(yf), "doe": kw(yd),
            "doe_installation": kw(yd_i),
            "z_doe_pct": z_at_yd["z_pct"], "z_fixed_pct": z_at_yf["z_pct"], "kappa": z_at_yf["kappa"],
            "z_doe_customer_max_pct": z_at_yd["z_customer_max_pct"],
            "z_doe_customer_year_max_pct": z_at_yd["z_customer_year_max_pct"],
            "customers_within_pct": within_pct(z_at_yd, z_star),
            "z_doe_installation": {"z_pct": z_at_ydi["z_pct"], "customer_max_pct": z_at_ydi["z_customer_max_pct"],
                                   "customer_year_max_pct": z_at_ydi["z_customer_year_max_pct"],
                                   "full_year_violations": z_at_ydi["full_year_violations"]},
            # beyond the firm state (DoeResult.full_year_violations), unlike the showcase's
            # verification.doe_full_year_violations, which counts every export-side hour
            "doe_full_year_violations_beyond_firm": z_at_yd["full_year_violations"],
            "seconds": time.perf_counter() - t0}


def _curve_point(args) -> dict:
    o, ms, k, years = args
    m = model()
    hp, ev = background_masks(m.C)
    order = adoption_order(o, m.C)
    d = curtailment(order, ms, k, years, hp, ev)
    doe.require_verified(d["full_year_violations"], f"curve point of order {o}, {k} adopters")
    f = curtailment(order, ms, k, years, hp, ev, method="fixed")
    cap = doe.customer_caps()
    return {"adopters": k, "kw_ac": float(cap[order[:k]].sum()), "z_doe_pct": d["z_pct"],
            "z_doe_customer_max_pct": d["z_customer_max_pct"],
            "z_doe_customer_year_max_pct": d["z_customer_year_max_pct"],
            "z_fixed_pct": f["z_pct"], "kappa": f["kappa"], "full_year_violations": d["full_year_violations"],
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
    doe.require_verified(r.full_year_violations, f"risk case of {year}, seed {seed}")
    pot = arr["pv"][nf].sum(axis=1)
    return {"year": year, "seed": seed, "curtailed_kwh": r.curtailed.sum(axis=1).astype(float).tolist(),
            "potential_kwh": pot.astype(float).tolist(), "critical_hours": r.info.get("critical_hours", 0),
            "verified_violations": r.verified_violations, "full_year_violations": r.full_year_violations}


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

def compute_all(quick: bool = False, workers: int = 6) -> None:
    """The whole computation. `workers` processes run the parallel stages: six for the reference
    network on a 16 GB machine, two for a network built while the server is running."""
    warnings.filterwarnings("ignore")
    cfg = settings()
    years = cfg.weather_years if not quick else (cfg.reference_year,)
    n_orders = N_ORDERS if not quick else 4
    z_star = A("z_star_pct")
    C = model().C
    t0 = time.perf_counter()

    # 1) nodal hosting capacity maps, 2) hosting capacity per adoption order (parallel)
    per_order, _summary = capacity(years, n_orders, z_star, workers, t0=t0)

    # 3) showcase: the order whose DOE capacity is the median
    yd = np.array([r["doe"]["kw_ac"] for r in per_order])
    best = per_order[int(np.argsort(yd)[len(yd) // 2])]
    o = best["order"]
    ms, mts, k = best["static"]["adopters"], best["timeseries"]["adopters"], best["doe"]["adopters"]
    showcase(o, ms, mts, k, years, quick)
    log.info("showcase done (%.0f s)", time.perf_counter() - t0)

    # 4) curve of curtailment vs deployment for the showcase order
    ks = sorted(set(list(range(ms, C + 1, 4 if not quick else 12))
                    + [C, mts, k, best["doe_installation"]["adopters"]]))
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as ex:
        pts = list(ex.map(_curve_point, [(o, ms, kk, tuple(years)) for kk in ks]))
    _write("zcurve.json", {"order": o, "static_adopters": ms, "timeseries_adopters": mts, "doe_adopters": k,
                           "doe_installation_adopters": best["doe_installation"]["adopters"],
                           "z_star_pct": z_star, "years": list(years), "points": pts})
    log.info("curve done (%.0f s)", time.perf_counter() - t0)

    # 5) risk: every weather year × load seeds for the showcase deployment
    seeds = (0, 1, 2) if not quick else (0,)
    cases = [(o, ms, k, y, s) for y in years for s in seeds]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as ex:
        risk = list(ex.map(_risk_case, cases))
    write_risk(risk, o, ms, k)
    log.info("risk done (%.0f s)", time.perf_counter() - t0)

    write_series(cfg.reference_year)

    compute_extra(quick, t0=t0, workers=min(workers, 4))


def capacity(years, n_orders: int, z_star: float, workers: int = 1, t0: float | None = None,
             extra: dict | None = None) -> tuple[list[dict], dict]:
    """The first two stages of the computation, written to results/: the nodal maps (nodal.json)
    and the capacity of every adoption order with their spread (hosting_summary.json, with the
    fields of `extra` added). `workers` > 1 runs the orders in that many processes; with one,
    they run in this process (a study's station). Returns the orders and the summary."""
    t0 = t0 if t0 is not None else time.perf_counter()
    m = model()
    C = m.C
    hp, ev = background_masks(C)
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

    jobs = [(o, tuple(years), z_star) for o in range(n_orders)]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=min(workers, n_orders), initializer=_init_worker) as ex:
            per_order = list(ex.map(analyze_order, jobs))
    else:
        per_order = [analyze_order(job) for job in jobs]
    log.info("orders done (%.0f s)", time.perf_counter() - t0)

    def stats(key: str, field: str) -> dict:
        return spread([r[key][field] for r in per_order])

    summary = {
        **(extra or {}),
        "z_star_pct": z_star, "years": list(years), "n_orders": n_orders, "customers": C,
        "total_cap_kw": float(doe.customer_caps().sum()), "total_kwp": float(cust["kwp"].sum()),
        "static_definition": hosting.static_definition(m),
        # the headline holds the aggregate share to the budget; doe_installation each installation's
        "budget_basis": {"doe": "aggregate", "doe_installation": "installation"},
        "measures": {k: {"kw_ac": stats(k, "kw_ac"), "kwp": stats(k, "kwp"), "adopters": stats(k, "adopters")}
                     for k in ("static", "timeseries", "fixed_cap", "doe", "doe_installation")},
        "per_order": per_order,
    }
    _write("hosting_summary.json", summary)
    return per_order, summary


EXTRA_STAGES = ("national", "requests", "scenarios", "tornado", "mvband", "foresight", "overinstallation",
                "consumption", "phases", "validation", "protection", "operation")


def compute_extra(quick: bool = False, t0: float | None = None, only: set[str] | None = None,
                  workers: int = 4) -> None:
    """Stages that build on the showcase: request baselines, 2030 scenarios, tornado, MV band,
    foresight, over-installation, consumption capacity, validation. `only` runs a subset of
    EXTRA_STAGES and leaves meta.json alone.

    Heavy stages use `workers` processes (4 fits next to a desktop session in 16 GB).
    """
    from . import national, run_extra

    warnings.filterwarnings("ignore")
    cfg = settings()
    t0 = t0 if t0 is not None else time.perf_counter()
    years = cfg.weather_years if not quick else (cfg.reference_year,)
    z_star = A("z_star_pct")
    sc = json.loads(processed("results", "showcase.json").read_text(encoding="utf-8"))
    o, ms = sc["order"], sc["firm"]
    want = (lambda name: True) if only is None else (lambda name: name in only)
    if want("national"):
        national.build()
    if want("requests"):
        run_extra.request_baselines(years)
        log.info("request baselines done (%.0f s)", time.perf_counter() - t0)
    if want("scenarios"):
        run_extra.scenarios_2030(o, ms, cfg.reference_year, quick, workers=workers)
        log.info("2030 scenarios done (%.0f s)", time.perf_counter() - t0)
    if want("tornado"):
        run_extra.tornado(o, years, z_star, workers=workers)
        log.info("tornado done (%.0f s)", time.perf_counter() - t0)
    if want("mvband"):
        run_extra.mv_band(o, ms, sc["adopters"], years, z_star, workers=workers)
        log.info("MV band done (%.0f s)", time.perf_counter() - t0)
    if want("foresight"):
        run_extra.foresight(years, z_star, workers=workers)
        log.info("foresight done (%.0f s)", time.perf_counter() - t0)
    if want("overinstallation"):
        run_extra.overinstallation(workers=workers)
        log.info("over-installation done (%.0f s)", time.perf_counter() - t0)
    if want("consumption"):
        run_extra.consumption(years, N_ORDERS if not quick else 4, workers=workers)
        log.info("consumption capacity done (%.0f s)", time.perf_counter() - t0)
    if want("phases"):
        run_extra.phase_cost()
        log.info("phase cost done (%.0f s)", time.perf_counter() - t0)
    if want("validation"):
        run_extra.validation()
    if want("protection"):
        from . import protection
        protection.write()
    if want("operation"):
        from . import operation
        operation.build()
        log.info("operation done (%.0f s)", time.perf_counter() - t0)
    if only is not None:
        log.info("stages %s finished in %.0f s", ",".join(sorted(only)), time.perf_counter() - t0)
        return
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
        doe.require_verified(r.full_year_violations, f"showcase, policy {pol}, {y}")
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
            "verified_violations": r.verified_violations, "full_year_violations": r.full_year_violations,
            "seconds": time.perf_counter() - t0,
            "per_customer": {"cid": cust["cid"].to_numpy()[nf].tolist(), "curtailed_kwh": np.round(curt, 1),
                             "z_pct": np.round(100 * frac, 3), "hours_limited": limited_h,
                             "dist_m": np.round(dist, 1)},
        }
        log.info("showcase %s: Z=%.2f%% critical h=%d (%.1f s)", pol, policies[pol]["z_pct"],
                 policies[pol]["critical_hours"], policies[pol]["seconds"])

    r = results[REF_POLICY]
    # Full-year nonlinear verification with the envelopes applied, kept for the map.
    full = doe.verify_full_year(m, arr, r, keep_state=True)
    doe.require_verified(full["violation_hours"], f"showcase state, policy {REF_POLICY}, {y}",
                         nonconverged_hours=full["nonconverged_hours"])
    p_doe, res_doe, v_doe = full["p"], full["result"], full["violations"]
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
            # every export-side violation hour of the year with the envelopes held, the hours the
            # firm customers alone break included; `_beyond_firm` leaves those out, as every
            # DoeResult.full_year_violations does, and `doe_firm_violation_hours` counts them
            "doe_full_year_violations": full["export_violation_hours"],
            "doe_full_year_violations_beyond_firm": full["violation_hours"],
            "doe_firm_violation_hours": full["firm_violation_hours"],
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
            "pf_iterations": full["pf_iterations"],
            "pf_nonconverged_hours": full["nonconverged_hours"],
            "pf_max_mismatch": full["pf_max_mismatch"],
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
    write_text_atomic(processed("results", "audit_trail.csv"), trail.events.to_csv(index=False))
    report = compliance.annual_report(trail, r, cids=cust["cid"].to_numpy())
    _write("compliance.json", report)
    write_text_atomic(processed("results", "compliance.txt"), compliance.report_text(report))
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
    save_npy(processed("ui", "envelopes_doe.npy"), env)
    _write("envelopes_index.json", {"cids": cust["cid"].to_numpy()[nf].tolist(), "hours": H})


def write_state(name: str, res, viol, p_net, pv, load, curtailed_total, folder: tuple[str, ...] = ("ui",)) -> None:
    """One scenario's year for the map (the contract is engine/stateblob.py)."""
    m = model()
    agg = {
        "trafo_loading": np.round(res.trafo_loading, 2), "trafo_p_kw": np.round(res.trafo_p_mw * 1000, 2),
        "vmax": np.round(viol.vmax, 4), "vmin": np.round(viol.vmin, 4),
        "max_line_loading": np.round(viol.max_line_loading, 2),
        "violation": viol.export_side.astype(int) | viol.import_side.astype(int),
        "pv_kw": np.round(np.asarray(pv).sum(axis=0), 2), "load_kw": np.round(np.asarray(load).sum(axis=0), 2),
        "curtailed_kw": np.round(curtailed_total, 2) if curtailed_total is not None else None,
    }
    header, blob = encode_state(name, np.abs(res.v), res.line_loading, np.asarray(p_net),
                                m.net.bus.index.tolist(), m.net.line.index.tolist(), agg)
    write_bytes_atomic(processed(*folder, f"state_{name}.bin"), blob)
    write_json_atomic(processed(*folder, f"state_{name}.json"), header, indent=None, default=_default)


def write_risk(risk: list[dict], o: int, ms: int, k: int) -> None:
    rows = []
    for r in risk:
        c, p = np.array(r["curtailed_kwh"]), np.array(r["potential_kwh"])
        rows.append({"year": r["year"], "seed": r["seed"], "z_pct": 100 * c.sum() / p.sum() if p.sum() else 0.0,
                     "curtailed_kwh": float(c.sum()), "potential_kwh": float(p.sum()),
                     "critical_hours": r["critical_hours"], "verified_violations": r["verified_violations"],
                     "full_year_violations": r.get("full_year_violations")})
    z = np.array([row["z_pct"] for row in rows])
    full = [row["full_year_violations"] for row in rows]
    for row in rows:
        doe.require_verified(row["full_year_violations"], f"risk case of {row['year']}, seed {row['seed']}")
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
                  "worst_z_pct": float(z.max()), "cvar95_z_pct": float(tail.mean()), "n_cases": int(len(z)),
                  # every case verified in every hour with the envelopes held (doe.verify_full_year)
                  "full_year_violations": int(sum(full))},
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
