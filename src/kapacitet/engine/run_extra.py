"""Additional precomputations: sensitivity (tornado, MV band), 2030 scenarios, request baselines, validation."""

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
    from .run import _write as write
    write(name, obj)


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
    """Curtailment share of order[ms:k] over `years`: a bisection predicate, so the envelopes are
    verified on their critical hours only (doe.compute's verify="critical")."""
    C = m.C
    nf = order[ms:k]
    if len(nf) == 0:
        return 0.0
    pvm = np.zeros(C, dtype=bool)
    pvm[order[:k]] = True
    curt = pot = 0.0
    for y in years:
        arr = Scenario(year=y, pv=pvm, hp=hp, ev=ev).arrays()
        r = doe.compute(m, arr, nf, verify="critical")
        curt += float(r.curtailed.sum())
        pot += float(arr["pv"][nf].sum())
    return 100.0 * curt / pot if pot > 0 else 0.0


# ------------------------------------------------------------------------------ tornado

def _mv_case(vm_pu: float) -> tuple:
    """The case with the MV voltage at `vm_pu`, as the tornado and mv_band.json run it."""
    return ("mv_voltage", "MV voltage at the substation", f"{vm_pu:.2f} pu", {"vm_slack": vm_pu}, {})


# The inputs varied one at a time besides the MV voltage, whose two levels are the ends of its
# planning band (tornado_cases).
TORNADO_CASES = [
    ("v_margin", "Voltage planning margin", "0 %", {}, {"v_margin_pu": 0.0}),
    ("v_margin", "Voltage planning margin", "2 %", {}, {"v_margin_pu": 0.02}),
    ("trafo", "Transformer rating", "0.25 MVA", {"trafo_type": "0.25 MVA 10/0.4 kV"}, {}),
    ("trafo", "Transformer rating", "0.63 MVA", {"trafo_type": "0.63 MVA 10/0.4 kV"}, {}),
    ("dcac", "PV DC/AC ratio", "1.0", {}, {"pv_dc_ac_ratio": 1.0}),
    ("dcac", "PV DC/AC ratio", "1.4", {}, {"pv_dc_ac_ratio": 1.4}),
    ("minload", "Minimum load in the static snapshot", "×0 (no load)", {}, {"static_min_load_factor": 0.0}),
    ("minload", "Minimum load in the static snapshot", "×2", {}, {"static_min_load_factor": 2.0}),
    ("static_pv", "Solar output in the static snapshot", "×0.8", {}, {"static_pv_factor": 0.8}),
    ("static_pv", "Solar output in the static snapshot", "×0.9", {}, {"static_pv_factor": 0.9}),
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
                "static_adopters": ms, "timeseries_adopters": mts, "doe_adopters": yd}
    finally:
        set_overrides(None)


def tornado_cases() -> list[tuple]:
    """Every case of the tornado: the MV voltage at both ends of its planning band, then the
    other inputs of TORNADO_CASES."""
    levels = mv_band_levels()
    return [_mv_case(levels[0]), _mv_case(levels[-1])] + TORNADO_CASES


def tornado(o: int, years, z_star: float, workers: int = 8) -> None:
    from concurrent.futures import ProcessPoolExecutor

    from .run import _init_worker
    base_case = ("base", "Base case", "base", {}, {})
    cases = [base_case] + tornado_cases()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as ex:
        rows = list(ex.map(_tornado_case, [(c, o, tuple(years), z_star) for c in cases]))
    _write("tornado.json", {"order": o, "years": list(years), "z_star_pct": z_star, "base": rows[0],
                            "cases": rows[1:]})


# ------------------------------------------------------------------------------ the MV band

MV_BAND_STEP_PU = 0.02
# What mv_band.json keeps of each level's capacity search (_tornado_case) and of the full-year
# check of the published envelopes there (doe.verify_full_year).
_MV_CAPACITY = ("static_kw", "timeseries_kw", "doe_kw", "static_adopters", "timeseries_adopters", "doe_adopters")
_MV_HELD = ("hours_checked", "violation_hours", "firm_violation_hours", "export_violation_hours",
            "nonconverged_hours", "max_voltage_pu", "max_trafo_loading_pct", "max_line_loading_pct")


def mv_band_levels(step: float = MV_BAND_STEP_PU) -> list[float]:
    """The MV voltages (pu) of the band's sensitivity: both ends of the planning band
    (mv_voltage_band_pu), the demo's own level (mv_voltage_pu) and every `step` from it between."""
    lo, hi = (float(v) for v in A("mv_voltage_band_pu"))
    base = float(A("mv_voltage_pu"))
    if not lo <= base <= hi:
        raise ValueError(f"mv_voltage_pu {base} lies outside mv_voltage_band_pu [{lo}, {hi}]")
    down, up = int((base - lo) / step + 1e-9), int((hi - base) / step + 1e-9)
    return sorted({round(base + i * step, 4) for i in range(-down, up + 1)} | {round(lo, 4), round(hi, 4)})


def mv_band_envelopes(o: int, ms: int, k: int, levels) -> list[dict]:
    """The showcase deployment in the reference year (order `o`: order[:ms] firm, order[ms:k] on
    envelopes) at each MV level of `levels`, in two ways:

    - `published`: the envelopes the demo publishes, computed at its own level (mv_voltage_pu),
      held in every hour and solved again with the MV at this level (doe.verify_full_year).
      `violation_hours` are the hours they no longer keep within a limit there, and
      `firm_violation_hours` those the firm customers alone break, which no envelope can fix.
    - `recomputed`: envelopes computed afresh with the MV at this level, verified the same way,
      and the curtailment they impose on the same customers (the showcase's measures).
    """
    from .delivery import share_pct
    from .run import REF_POLICY, background_masks, model, pv_mask

    m = model()
    hp, ev = background_masks(m.C)
    order = adoption_order(o, m.C)
    nf = order[ms:k]
    arr = Scenario(year=settings().reference_year, pv=pv_mask(order, k, m.C), hp=hp, ev=ev).arrays()
    published = doe.compute(m, arr, nf, policy=REF_POLICY)
    pot = arr["pv"][nf].astype(float).sum(axis=1)
    rows = []
    for v in levels:
        held = doe.verify_full_year(m, arr, published, vm_slack=v)
        r = published if abs(v - m.vm_slack) < 1e-9 else doe.compute(m, arr, nf, policy=REF_POLICY, vm_slack=v)
        curt = r.curtailed.astype(float).sum(axis=1)
        rows.append({
            "published": {key: held[key] for key in _MV_HELD},
            "recomputed": {
                "curtailed_kwh": float(curt.sum()), "potential_kwh": float(pot.sum()),
                "z_pct": float(100 * curt.sum() / pot.sum()) if pot.sum() > 0 else 0.0,
                "max_customer_z_pct": float(share_pct(curt, pot).max()) if len(nf) else 0.0,
                "critical_hours": int(r.info.get("critical_hours", 0)),
                "intervention_hours": int((r.curtailed.sum(axis=0) > 1e-3).sum()),
                "full_year_violations": r.full_year_violations,
                "firm_violation_hours": int(r.info["full_year"]["firm_violation_hours"]),
            },
        })
    return rows


def mv_band(o: int, ms: int, k: int, years, z_star: float, workers: int = 4) -> dict:
    """mv_band.json: the showcase order across the planning band of the MV voltage. The demo's
    envelopes stay at mv_voltage_pu; this is a sensitivity published beside them.

    At each level of mv_band_levels(): the static, hourly and envelope capacity of order `o`,
    searched over `years` as the tornado searches them (_tornado_case); and, for the deployment
    the showcase publishes (order[:ms] firm, order[:k] with solar), the full-year check of its
    envelopes and the curtailment of envelopes recomputed at that level (mv_band_envelopes).
    With `workers` > 1 the searches run in that many processes while this one checks the
    envelopes; with one, everything runs here. Returns what it writes.
    """
    from .run import REF_POLICY

    levels = mv_band_levels()
    jobs = [(_mv_case(v), o, tuple(years), z_star) for v in levels]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        from .run import _init_worker
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), initializer=_init_worker) as ex:
            searches = ex.map(_tornado_case, jobs)          # submitted now, collected after the checks
            checks = mv_band_envelopes(o, ms, k, levels)
            capacity = list(searches)
    else:
        capacity = [_tornado_case(job) for job in jobs]
        checks = mv_band_envelopes(o, ms, k, levels)
    out = {"order": o, "years": list(years), "z_star_pct": z_star, "policy": REF_POLICY,
           "year": settings().reference_year, "firm": ms, "adopters": k,
           "base_pu": float(A("mv_voltage_pu")), "band_pu": [float(v) for v in A("mv_voltage_band_pu")],
           "levels": [{"vm_pu": v, **{key: c[key] for key in _MV_CAPACITY}, **chk}
                      for v, c, chk in zip(levels, capacity, checks)]}
    _write("mv_band.json", out)
    return out


# ------------------------------------------------------------------- the cost of the forecast

def _foresight_case(args) -> dict:
    """The capacity one adoption order carries on envelopes issued a day ahead, on both bases of
    the budget: the aggregate one (the headline's) and every installation's own, from one memo
    of the counts measured."""
    warnings.filterwarnings("ignore")
    o, ms, mts, yd, yd_i, years, z_star, window = args
    from . import forecast
    from .run import background_masks, model
    m = model()
    hp, ev = background_masks(m.C)
    order = adoption_order(o, m.C)
    cap = doe.customer_caps()
    t0 = time.perf_counter()
    runs: dict[int, dict] = {}
    k, det = forecast.adopters(m, order, ms, mts, years, hp, ev, "climatological", z_star,
                               window_days=window, hi=yd, memo=runs)
    # each installation's share is at least the aggregate one, so this count is at most `k`,
    # and a dominating state never carries more than perfect foresight on the same basis
    if det["z_customer_max_pct"] <= z_star and k <= yd_i:
        k_i, det_i = k, det
    else:
        k_i, det_i = forecast.adopters(m, order, ms, mts, years, hp, ev, "climatological", z_star,
                                       window_days=window, hi=min(k, yd_i), budget="installation", memo=runs)
    return {"order": o, "adopters": int(k), "kw_ac": float(cap[order[:k]].sum()),
            "z_pct": det["z_pct"], "violation_hours": det["violation_hours"],
            "z_customer_max_pct": det["z_customer_max_pct"],
            "at_floor": bool(k <= mts),
            "exceedance": det["exceedance"],
            "exceedance_per_year": [{"year": p["year"], **p["exceedance"]} for p in det["per_year"] if p["exceedance"]],
            "installation": {"adopters": int(k_i), "kw_ac": float(cap[order[:k_i]].sum()),
                             "z_pct": det_i["z_pct"], "z_customer_max_pct": det_i["z_customer_max_pct"],
                             "z_customer_year_max_pct": det_i["z_customer_year_max_pct"],
                             "violation_hours": det_i["violation_hours"], "at_floor": bool(k_i <= mts),
                             "exceedance": det_i["exceedance"]},
            "seconds": time.perf_counter() - t0}


def foresight(years, z_star: float, window_days: int | None = None, workers: int = 4) -> None:
    """What the published capacity owes to perfect foresight, measured rather than caveated.

    The published envelopes are allocated knowing the hour they apply to. This stage repeats
    the same capacity search against the day-ahead planning state of
    `forecast.climatological` — generation at the highest, and load at the lowest, the same
    fortnight reached in the other weather years, each widened by its day-ahead margin —
    which needs no weather forecast at all and is therefore the pessimistic end of what any
    operator could do. The published figure and this one bracket real day-ahead practice.

    That state is meant to dominate the year, and the record says how often it did not: every
    year of every order is audited (`forecast.plan_exceedance`), per order and per year here.
    Per order, `at_floor` says the day-ahead search ended at or below the time-series capacity
    it starts from, and `kw_minus_timeseries` by how much it rose above it (or fell below).

    Both bases of the budget are searched, from one memo per order: the top level holds the
    aggregate share to it (the headline's basis), `installation` each installation's own share
    over the years, against the published `doe_installation`.
    """
    from concurrent.futures import ProcessPoolExecutor

    from . import forecast
    from .run import _init_worker, background_masks, spread
    window = forecast.WINDOW_DAYS if window_days is None else window_days
    summary = json.loads(processed("results", "hosting_summary.json").read_text(encoding="utf-8"))
    cap_total = summary["total_cap_kw"]
    hp, ev = background_masks(summary["customers"])
    # a summary from before the per-installation basis has no doe_installation: its aggregate
    # count is still a ceiling for the installation search, only a looser one
    installation = lambda r: r.get("doe_installation", r["doe"])
    jobs = [(r["order"], r["static"]["adopters"], r["timeseries"]["adopters"],
             r["doe"]["adopters"], installation(r)["adopters"], tuple(years), z_star, window)
            for r in summary["per_order"]]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as ex:
        rows = list(ex.map(_foresight_case, jobs))

    by_order = {r["order"]: r for r in rows}
    med = lambda xs: float(np.median(xs))
    measures = ("static", "timeseries", "fixed_cap", "doe") + \
        (("doe_installation",) if all("doe_installation" in r for r in summary["per_order"]) else ())
    published = {m: med([r[m]["kw_ac"] for r in summary["per_order"]]) for m in measures}
    climatological_kw = med([r["kw_ac"] for r in rows])
    inst = [r["installation"] for r in rows]
    inst_kw = med([r["kw_ac"] for r in inst])
    pv_margin, load_margin = forecast.day_ahead_margins()
    by_year = {y: forecast.total_exceedance([next((p for p in r["exceedance_per_year"] if p["year"] == y), None)
                                             for r in rows]) for y in years}
    per_order = []
    for r in summary["per_order"]:
        row = {k: v for k, v in by_order[r["order"]].items() if k != "exceedance_per_year"}
        per_order.append({**row, "doe_kw": r["doe"]["kw_ac"], "static_kw": r["static"]["kw_ac"],
                          "timeseries_kw": r["timeseries"]["kw_ac"],
                          "kw_minus_timeseries": row["kw_ac"] - r["timeseries"]["kw_ac"]})
        per_order[-1]["installation"] = {**row["installation"], "doe_kw": installation(r)["kw_ac"]}
    _write("foresight.json", {
        "years": list(years), "z_star_pct": z_star, "window_days": window,
        # the top level holds the aggregate share to the budget (the headline's basis);
        # `installation` repeats the search with each installation's own share held to it
        "budget_basis": "aggregate",
        "installation": {
            "budget_basis": "installation",
            "median_kw": {"climatological": inst_kw, "doe": published.get("doe_installation")},
            "kw_ac_stats": {"climatological": spread([r["kw_ac"] for r in inst])},
            "median_adopters": {"climatological": med([r["adopters"] for r in inst]),
                                "doe": med([installation(r)["adopters"] for r in summary["per_order"]])},
            "foresight_premium_kw": (published["doe_installation"] - inst_kw
                                     if "doe_installation" in published else None),
            "violation_hours": int(sum(r["violation_hours"] for r in inst)),
            "within_budget_orders": int(sum(r["z_customer_max_pct"] <= z_star + 1e-9 for r in inst)),
            "at_floor_orders": int(sum(r["at_floor"] for r in inst)),
            "plan_exceedance": forecast.total_exceedance([r["exceedance"] for r in inst]),
        },
        "margins": {"pv_pct": round(pv_margin * 100.0, 6), "load_pct": round(load_margin * 100.0, 6)},
        "n_orders": len(rows), "total_cap_kw": cap_total,
        "exceedance": forecast.exceedance_audit(settings().reference_year, hp, ev, window),
        "plan_exceedance": {"total": forecast.total_exceedance([r["exceedance"] for r in rows]),
                            "by_year": {str(y): ex for y, ex in by_year.items()}},
        "median_kw": {**published, "climatological": climatological_kw},
        "kw_ac_stats": {"climatological": spread([r["kw_ac"] for r in rows])},
        "median_adopters": {"climatological": med([r["adopters"] for r in rows]),
                            "doe": med([r["doe"]["adopters"] for r in summary["per_order"]]),
                            "static": med([r["static"]["adopters"] for r in summary["per_order"]])},
        "foresight_premium_kw": published["doe"] - climatological_kw,
        "violation_hours": int(sum(r["violation_hours"] for r in rows)),
        "within_budget_orders": int(sum(r["z_pct"] <= z_star + 1e-9 for r in rows)),
        "at_floor_orders": int(sum(r["at_floor"] for r in rows)),
        "per_order": per_order,
    })
    log.info("foresight: climatological %.0f kW vs published %.0f kW (%.0f kW of the gain is "
             "the forecast), %d violation hours", climatological_kw, published["doe"],
             published["doe"] - climatological_kw, sum(r["violation_hours"] for r in rows))


# ------------------------------------------------------------------- over-installation

def _overinstall_point(args) -> dict:
    """What the non-firm adopters order[ms:k] produce, lose to envelopes, and are worth at spot."""
    warnings.filterwarnings("ignore")
    o, ms, k, years = args
    from . import economics
    from .run import REF_POLICY, background_masks, model, pv_mask
    m = model()
    hp, ev = background_masks(m.C)
    order = adoption_order(o, m.C)
    nf = order[ms:k]
    cap = doe.customer_caps()
    per_year = []
    for y in years:
        arr = Scenario(year=y, pv=pv_mask(order, k, m.C), hp=hp, ev=ev).arrays()
        pv = arr["pv"][nf].astype(float)
        cut = (doe.compute(m, arr, nf, policy=REF_POLICY).curtailed.astype(float) if len(nf)
               else np.zeros_like(pv))
        per_year.append({"year": y, "potential_kwh": float(pv.sum()), "curtailed_kwh": float(cut.sum()),
                         "delivered_value": economics.value_sek(pv - cut, y),
                         "curtailed_value": economics.value_sek(cut, y)})
    return {"adopters": int(k), "kw_ac": float(cap[order[:k]].sum()),
            "kw_ac_nonfirm": float(cap[nf].sum()), "per_year": per_year}


def overinstallation(workers: int = 4) -> None:
    """Connecting beyond the static limit: energy gained and lost, and its value at spot prices.

    Same showcase order and the same deployment points as `zcurve.json`, so the two curves line
    up. Values are spot only (no network tariff, energy tax or compensation), on the years with
    price data; energies cover every weather year. The envelopes are the published ones, which
    know each hour's weather (`envelope_basis: "foresight"`), not those issued a day ahead.
    """
    from concurrent.futures import ProcessPoolExecutor

    from . import economics
    from .run import _init_worker
    zc = json.loads(processed("results", "zcurve.json").read_text(encoding="utf-8"))
    o, ms, years = zc["order"], zc["static_adopters"], tuple(zc["years"])
    ks = [p["adopters"] for p in zc["points"]]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as ex:
        pts = list(ex.map(_overinstall_point, [(o, ms, k, years) for k in ks]))
    priced = [y for y in economics.price_years() if y in years]
    for p in pts:
        rows = p["per_year"]
        pot = sum(r["potential_kwh"] for r in rows)
        cut = sum(r["curtailed_kwh"] for r in rows)
        pr = [r for r in rows if r["delivered_value"] is not None]
        p["mean"] = {"potential_kwh": pot / len(rows), "curtailed_kwh": cut / len(rows),
                     "z_pct": 100.0 * cut / pot if pot else 0.0}
        p["mean_priced"] = {key: float(np.mean([r[src][field] for r in pr])) if pr else None
                            for key, src, field in (("delivered_net_sek", "delivered_value", "net_sek"),
                                                    ("delivered_gross_sek", "delivered_value", "gross_sek"),
                                                    ("curtailed_net_sek", "curtailed_value", "net_sek"),
                                                    ("curtailed_gross_sek", "curtailed_value", "gross_sek"))}
    _write("overinstallation.json", {"order": o, "static_adopters": ms, "doe_adopters": zc["doe_adopters"],
                                     "z_star_pct": zc["z_star_pct"], "years": list(years),
                                     "price_years": priced, "value_basis": "spot price of the bidding zone",
                                     "envelope_basis": "foresight",
                                     "points": pts})


# ------------------------------------------------------------------- consumption, hour by hour

def _consumption_order(args) -> dict:
    """How many new devices of one kind one adoption order carries (hosting.ts_import_adopters)."""
    warnings.filterwarnings("ignore")
    o, device, years, flat_kw = args
    from .run import background_masks, model
    m = model()
    hp, ev = background_masks(m.C)
    t0 = time.perf_counter()
    k = hosting.ts_import_adopters(m, adoption_order(o, m.C), device, hp, ev, years, flat_kw=flat_kw)
    return {"order": o, "device": device, "adopters": int(k), "seconds": time.perf_counter() - t0}


def _consumption_nodal(args) -> dict:
    warnings.filterwarnings("ignore")
    years, = args
    from .run import background_masks, model
    m = model()
    hp, ev = background_masks(m.C)
    t0 = time.perf_counter()
    return {"timeseries_kw": hosting.nodal_import_hc_ts(m, hp, ev, years),
            "static_kw": hosting.nodal_import_hc(m, hp, ev), "seconds": time.perf_counter() - t0}


def consumption(years, n_orders: int, workers: int = 4) -> None:
    """The consumption capacity of the hourly record, next to the static one (results/consumption.json).

    Each house alone: the most load it can add, constant in every hour of every weather year,
    with no import-side violation (`hosting.nodal_import_hc_ts`), beside the static winter
    snapshot's (`hosting.nodal_import_hc`, also in nodal.json). And per adoption order, how
    many new EV chargers (each house's own home charging), heat pumps (its own heat pump) or
    flat loads of the charger rating the street takes, house after house, before an hour of
    the record breaks a limit (`hosting.ts_import_adopters`). Nobody's solar is counted: it
    only relieves the import side.
    """
    from concurrent.futures import ProcessPoolExecutor

    from .run import _init_worker, background_masks, spread
    years = tuple(years)
    flat_kw = float(A("ev_charger_kw"))
    cust = customers()
    C = len(cust)
    hp, ev = background_masks(C)
    jobs = [(o, d, years, flat_kw) for d in hosting.IMPORT_DEVICES for o in range(n_orders)]
    with ProcessPoolExecutor(max_workers=max(1, workers), initializer=_init_worker) as ex:
        nodal = ex.submit(_consumption_nodal, (years,))
        rows = list(ex.map(_consumption_order, jobs))
        nodal = nodal.result()
    has = {"ev": ev, "hp": hp, "flat": np.zeros(C, dtype=bool)}
    device_kw = {"ev": flat_kw, "hp": None, "flat": flat_kw}
    devices = {}
    for d in hosting.IMPORT_DEVICES:
        mine = [r for r in rows if r["device"] == d]
        devices[d] = {"device_kw": device_kw[d], "existing": int(has[d].sum()),
                      "candidates": int(C - has[d].sum()),
                      "adopters": spread([r["adopters"] for r in mine]),
                      "per_order": [{"order": r["order"], "adopters": r["adopters"]} for r in mine]}
    winter = hosting.winter_design_load()
    _write("consumption.json", {
        "years": list(years), "n_orders": n_orders, "customers": C,
        "basis": "every hour of every weather year, today's heat pumps and EVs, no solar; import side "
                 "(undervoltage and forward overloads of cables and the transformer)",
        "devices_text": {"ev": "the house's own uncontrolled home charging",
                         "hp": "the house's own heat pump", "flat": "a constant load of the charger rating"},
        "nodal": {"customers": cust["cid"].tolist(),
                  "timeseries_kw": np.round(nodal["timeseries_kw"], 2),
                  "static_kw": np.round(nodal["static_kw"], 2),
                  "static_hour": {"year": winter["year"], "hour": winter["hour"]}},
        "nodal_kw": {"timeseries": spread(nodal["timeseries_kw"]), "static": spread(nodal["static_kw"])},
        "devices": devices,
    })
    log.info("consumption: nodal median %.1f kW hourly vs %.1f kW static; EV chargers median %.0f",
             float(np.median(nodal["timeseries_kw"])), float(np.median(nodal["static_kw"])),
             devices["ev"]["adopters"]["median"])


# ------------------------------------------------------------------- what the phase record is worth

def phase_cost(order_index: int = 0, random_seeds: int = 20) -> None:
    """Static hosting capacity with the phase of every PV connection modelled, spread or unknown.

    The published figures come from the balanced model. This stage repeats the static search on
    the unbalanced one: with the inverter sizes the model produces, then with every connection
    forced single-phase and the phases either spread evenly or placed at random, which is what an
    operator without a phase column in its records is effectively assuming.
    """
    from . import phases
    from .netmodel3ph import NetModel3Ph
    from .run import background_masks, model
    m = model()
    hp, ev = background_masks(m.C)
    order = adoption_order(order_index, m.C)
    cap = doe.customer_caps()
    base3 = NetModel3Ph()
    everyone = np.zeros(base3.C)                 # every inverter at or below the single-phase limit
    cases = [("balanced", "balanced three-phase model (published figures)", m),
             ("unbalanced", "unbalanced model, inverter sizes as built", base3),
             ("single_spread", "every connection single-phase, phases spread evenly",
              NetModel3Ph(phase=phases.phase_of(base3.cust, everyone, "round_robin")))]
    rows = []
    for key, label, mdl in cases:
        k = hosting.static_adopters(mdl, order, hp, ev)
        rows.append({"key": key, "label": label, "adopters": int(k), "kw_ac": float(cap[order[:k]].sum())})
    # an unknown phase is a draw, not a number: report the spread over seeded draws
    rand = []
    for seed in range(random_seeds):
        mdl = NetModel3Ph(phase=phases.phase_of(base3.cust, everyone, "random", seed=seed))
        k = hosting.static_adopters(mdl, order, hp, ev)
        rand.append({"seed": seed, "adopters": int(k), "kw_ac": float(cap[order[:k]].sum())})
    kw = np.array([r["kw_ac"] for r in rand])
    rows.append({"key": "single_random", "label": "every connection single-phase, phases unknown (random)",
                 "kw_ac_median": float(np.median(kw)), "kw_ac_min": float(kw.min()), "kw_ac_max": float(kw.max()),
                 "adopters_median": float(np.median([r["adopters"] for r in rand])), "draws": rand})
    _write("phases.json", {"order": order_index,
                           "single_phase_limit_kva": A("pv_single_phase_max_kva"),
                           "single_phase_as_built": int(np.count_nonzero(base3.phase != phases.THREE_PHASE)),
                           "customers": int(base3.C), "cases": rows})


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
    doe_ok = (z <= z_star) and (unmet_pct <= A("ev_unserved_limit_pct")) and firm_imp == 0 and residual == 0
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
                              "z_star_pct": A("z_star_pct"), "ev_unmet_limit_pct": A("ev_unserved_limit_pct"), "cases": rows})


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
    res = m.pf.solve(pv - load, -load * tanphi, 1.0, strict=True)   # a comparison needs an answer
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
    v_nl = np.abs(m.solve(p, q, strict=True).v[m.check_rows])
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
