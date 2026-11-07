"""How the envelopes would be run: for each sharing rule and each basis, issued, verified, filed.

The published capacity figures use perfect foresight (`foresight`): each hour's envelope knows
that hour's load and sun, which is the bound a real operator works towards. An envelope that can
actually be sent the day before (`dayahead`) has to hold against any day the same fortnight has
seen, the state of `forecast.climatological` meant to dominate it: no forecast in it, dearer, and
an empirical bound, so each issue counts the hours the real year beat it.

For the published deployment (showcase.json: the reference year, its firm and non-firm houses)
this stage computes every combination of the four sharing rules of `doe.allocate` and the two
bases, and for each one writes, under results/operation/<basis>/<rule>/:

- issued.json      what was issued and the full-year nonlinear check of the real year with every
                   non-firm house held to it (only a combination with zero violation hours may be
                   served), the binding components, and the control a house falls back to when it
                   hears nothing: the lower of its static share and the lowest limit it was issued;
                   on the day-ahead basis also `exceedance`, where the real year beat the planning
                   state (`forecast.plan_exceedance`);
- audit_trail.csv  the evidence row by row, with whether each hour needed limiting at all;
- compliance.json/.txt  the filing derived from that trail;
- days.json, days.npz   per-day aggregates and per-house days, for the season's replay (day-ahead:
                   with the exceedance counted per day);

and under ui/operation/<basis>/<rule>/ the envelopes and a map state in the binary layout of
engine/stateblob.py. results/operation/agreements.json holds the static evidence of each
agreement once (it depends on neither rule nor basis), and results/operation/index.json the
deployment and what each combination verified to. `compute --stage extra --only operation`.
"""

from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd

from ..atomic import savez_atomic, write_json_atomic, write_text_atomic
from ..config import A, settings
from ..paths import processed
from . import compliance, doe, forecast, hosting
from .doe import DoeResult
from .scenario import Scenario, adoption_order, customers

log = logging.getLogger(__name__)
BASES = ("foresight", "dayahead")
POLICIES = ("maxmin", "prorata", "propfair", "maxtotal")
SCHEME = {"foresight": "foresight", "dayahead": "climatological"}


def _write_json(path, obj) -> None:
    from .run import _default

    write_json_atomic(path, obj, indent=None, default=_default)


def _days(values: np.ndarray, H: int) -> np.ndarray:
    """Sum an hourly series (…, H) into days of the study's UTC+1 calendar (…, D)."""
    D = H // 24
    return values[..., : D * 24].reshape(*values.shape[:-1], D, 24).sum(axis=-1)


def agreements(model, order: np.ndarray, firm: int, adopters: int, cids: np.ndarray) -> list[dict]:
    """Why each agreement is conditional: the static study of the adoption order up to and including
    that house, at its single worst-case hour (hosting.summer_snapshot)."""
    out = []
    for pos in range(firm, adopters):
        mask = np.zeros(model.C, dtype=bool)
        mask[order[: pos + 1]] = True
        p, q = hosting.summer_snapshot(mask)
        res = model.solve(p[:, None], q[:, None])
        v = model.violations(res, p[:, None])
        out.append({"customer_id": int(cids[order[pos]]), "position": int(pos + 1),
                    "static_approved": bool(~v.export_side[0]),
                    "trafo_loading_pct": round(float(res.trafo_loading[0]), 1),
                    "max_voltage_pu": round(float(v.vmax[0]), 4),
                    "max_line_loading_pct": round(float(v.max_line_loading[0]), 1)})
    return out


def build(only_policies: tuple[str, ...] = POLICIES, only_bases: tuple[str, ...] = BASES) -> dict:
    """Compute, verify and write every combination; returns the index."""
    from .run import _versions, background_masks, model, save_npy, write_state

    t_all = time.perf_counter()
    show = json.loads(processed("results", "showcase.json").read_text(encoding="utf-8"))
    m = model()
    C = m.C
    y = int(show["year"])
    cust = customers()
    cids = cust["cid"].to_numpy()
    order = adoption_order(int(show["order"]), C)
    pv = np.zeros(C, dtype=bool)
    pv[show["pv_cids"]] = True
    firm = np.zeros(C, dtype=bool)
    firm[show["firm_cids"]] = True
    nf = np.asarray(show["nonfirm_cids"], dtype=int)
    hp, ev = background_masks(C)
    arr = Scenario(year=y, pv=pv, hp=hp, ev=ev).arrays()
    H = int(arr["p_net"].shape[1])
    desired = np.clip(arr["p_net"][nf].astype(float), 0.0, None)

    # the hours in which the grid with nobody limited breaks a limit: what needs limiting at all
    res_unc = m.solve(arr["p_net"], arr["q"])
    v_unc = m.violations(res_unc, arr["p_net"])
    necessary = np.flatnonzero(v_unc.export_side)
    necessary_mask = v_unc.export_side.astype(bool)

    # the static share every non-firm inverter keeps in the worst case (doe.fixed_cap)
    p0, q0 = hosting.summer_snapshot(firm)
    kappa, _ = doe.fixed_cap(m, arr, nf, p0, q0)
    caps = doe.customer_caps()[nf]

    root = processed("results", "operation", "index.json").parent
    # what each non-firm house would export with no limit, hour by hour: the day's schedule is
    # read against it, and it does not depend on rule or basis
    save_npy(processed("ui", "operation", "desired.npy"), desired.astype(np.float16))
    agree = agreements(m, order, int(show["firm"]), int(show["adopters"]), cids)
    _write_json(root / "agreements.json", {"year": y, "order": int(show["order"]), "agreements": agree})

    index = {"year": y, "order": int(show["order"]), "firm_cids": show["firm_cids"], "nonfirm_cids": show["nonfirm_cids"],
             "pv_cids": show["pv_cids"], "bases": list(only_bases), "policies": list(only_policies),
             "necessary_hours": int(len(necessary)), "combos": {}}
    for basis in only_bases:
        plan = arr if basis == "foresight" else forecast.planning_state(SCHEME[basis], y, pv, hp, ev, arr)
        # a state issued ahead is a bet on the record: audit where the real year beat it (it does
        # not depend on the rule, so once per basis); perfect foresight has nothing to audit
        exceedance = exceed_days = None
        if basis != "foresight":
            exceedance = forecast.plan_exceedance(plan, arr, nf)
            hourly = forecast.exceedance_hours(plan, arr)
            exceed_days = {"exceedance_pv_customer_hours": _days(hourly["pv_customers"], H),
                           "exceedance_load_customer_hours": _days(hourly["load_customers"], H),
                           "exceedance_net_hours": _days(hourly["net_aggregate"].astype(int), H)}
        index["combos"][basis] = {}
        for pol in only_policies:
            t0 = time.perf_counter()
            r = doe.compute(m, plan, nf, policy=pol)
            published = np.repeat(r.cap[:, None], H, axis=1).astype(float)
            if len(r.hours):
                published[:, r.hours] = r.E
            granted = np.minimum(desired, published)
            curtailed = desired - granted

            # the real year with every non-firm house held to what was issued
            full = doe.verify_full_year(m, arr, r, keep_state=True)
            p, res, v = full["p"], full["result"], full["violations"]
            violations = full["export_violation_hours"]

            rr = DoeResult(nonfirm=nf, hours=r.hours, E=published[:, r.hours], desired=desired, cap=r.cap,
                           curtailed=curtailed, verified_violations=violations, binding=r.binding,
                           binding_line=r.binding_line, info={"policy": pol},
                           verification="full", full_year_violations=full["violation_hours"])
            trail = compliance.audit_trail(m, rr, arr, y, cids=cids, necessary_hours=necessary)
            report = compliance.annual_report(trail, rr, cids=cids, basis=basis)

            folder = processed("results", "operation", basis, pol, "issued.json").parent
            write_text_atomic(folder / "audit_trail.csv", trail.events.to_csv(index=False))
            _write_json(folder / "compliance.json", report)
            write_text_atomic(folder / "compliance.txt", compliance.report_text(report))

            limited = curtailed.sum(axis=0) > 1e-3
            per_day = {
                "curtailed_kwh": np.round(_days(curtailed.sum(axis=0), H), 3),
                "desired_kwh": np.round(_days(desired.sum(axis=0), H), 3),
                "hours_limited": _days(limited.astype(int), H),
                "hours_necessary_limited": _days((limited & necessary_mask).astype(int), H),
                "hours_unnecessary_limited": _days((limited & ~necessary_mask).astype(int), H),
                "violation_hours": _days(v.export_side.astype(int), H),
                "houses_limited_max": (curtailed > 1e-3).sum(axis=0)[: (H // 24) * 24].reshape(-1, 24).max(axis=1),
                **(exceed_days or {}),
            }
            _write_json(folder / "days.json", {"year": y, "days": H // 24, "tz": "UTC+1", **per_day})
            savez_atomic(folder / "days.npz", compressed=True, curtailed=_days(curtailed, H).astype(np.float32),
                         desired=_days(desired, H).astype(np.float32),
                         pv=_days(arr["pv"][nf].astype(float), H).astype(np.float32), cids=cids[nf])

            lowest = published.min(axis=1)
            default_kw = np.minimum(kappa * caps, lowest)
            issued = {
                "basis": basis, "policy": pol, "scheme": SCHEME[basis], "year": y,
                "critical_hours": int(len(r.hours)), "hours_limited": int(limited.sum()),
                "curtailed_kwh": round(float(curtailed.sum()), 1), "export_kwh": round(float(desired.sum()), 1),
                "curtailed_share_of_export_pct": round(float(curtailed.sum() / desired.sum() * 100.0), 3) if desired.sum() else 0.0,
                "necessary_hours": int(len(necessary)),
                "activations_necessary_hours": int((limited & necessary_mask).sum()),
                "activations_unnecessary_hours": int((limited & ~necessary_mask).sum()),
                "verification": {"full_year_violation_hours": violations, "max_voltage_pu": full["max_voltage_pu"],
                                 "max_trafo_loading_pct": full["max_trafo_loading_pct"],
                                 "max_line_loading_pct": full["max_line_loading_pct"],
                                 "pf_nonconverged_hours": full["nonconverged_hours"]},
                "verified": violations == 0,
                **({"exceedance": exceedance} if exceedance is not None else {}),
                "binding_hours": {compliance.CONSTRAINT[int(k)]: int(n) for k, n in
                                  zip(*np.unique(r.binding, return_counts=True))} if r.binding is not None and len(r.hours) else {},
                "default_control": {"kappa": float(kappa), "rule": "min(static share, lowest issued limit)",
                                    "cids": cids[nf].tolist(), "kw": np.round(default_kw, 3).tolist()},
                "seconds": round(time.perf_counter() - t0, 1),
            }
            _write_json(folder / "issued.json", issued)

            ui = processed("ui", "operation", basis, pol, "envelopes.npy").parent
            save_npy(ui / "envelopes.npy", published.astype(np.float16))
            pv_real = arr["pv"].astype(float).copy()
            pv_real[nf] -= curtailed
            write_state("doe", res, v, p, pv_real, arr["load"], curtailed.sum(axis=0),
                        folder=("ui", "operation", basis, pol))

            index["combos"][basis][pol] = {
                "verified": violations == 0, "full_year_violation_hours": violations,
                "curtailed_kwh": issued["curtailed_kwh"], "curtailed_share_of_export_pct": issued["curtailed_share_of_export_pct"],
                "hours_limited": issued["hours_limited"], "critical_hours": issued["critical_hours"],
                "activations_unnecessary_hours": issued["activations_unnecessary_hours"],
            }
            log.info("operation %s/%s: %.2f %% of export, %d h limited (%d unnecessary), %d violation h (%.1f s)",
                     basis, pol, issued["curtailed_share_of_export_pct"], issued["hours_limited"],
                     issued["activations_unnecessary_hours"], violations, issued["seconds"])

    if "dayahead" in only_bases:
        index["import"] = write_import(m, y, hp, ev, arr, cids)

    index["computed_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    index["seconds"] = round(time.perf_counter() - t_all, 1)
    index["versions"] = _versions()
    _write_json(root / "index.json", index)
    return index


def write_import(m, y: int, hp: np.ndarray, ev: np.ndarray, arr: dict[str, np.ndarray], cids: np.ndarray) -> dict:
    """The import envelopes issued the day before to every EV charger (doe_import.issue_day_ahead),
    on the import planning state (forecast.import_planning_state). They do not depend on the rule
    of the export envelopes, so they are written once, next to the day-ahead combinations:
    results/operation/dayahead/import.json and ui/operation/dayahead/import_envelopes.npy.
    Returns the summary the index keeps."""
    from . import doe_import
    from .run import save_npy
    from .scenario import year_profiles

    t0 = time.perf_counter()
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    plan = forecast.import_planning_state(y, hp, np.zeros_like(ev))
    ev_load = np.where(ev[:, None], year_profiles(y)["ev"], 0.0)
    actual = {"p_net": arr["p_net"].astype(float) + ev_load, "q": arr["q"].astype(float) + ev_load * tanphi}
    r = doe_import.issue_day_ahead(m, plan, actual, ev)
    _, lm = forecast.day_ahead_margins()
    summary = {
        "basis": "dayahead", "year": y, "rule": "maxmin", "cids": cids[r.flex].tolist(),
        "firm_kw": r.firm_kw, "critical_hours": r.critical_hours, "limited_hours": r.limited_hours,
        "firm_violation_hours": r.firm_violation_hours, "planning_violation_hours": r.planning_violation_hours,
        "actual_violation_hours": r.actual_violation_hours, "retreats": r.retreats, "verified": r.verified,
        "planning_state": {"scheme": "climatological ceiling", "pv": "none", "plugged": "every hour",
                           "window_days": forecast.WINDOW_DAYS, "load_margin_pct": round(lm * 100.0, 6)},
        "seconds": round(time.perf_counter() - t0, 1),
    }
    folder = processed("results", "operation", "dayahead", "import.json").parent
    save_npy(processed("ui", "operation", "dayahead", "import_envelopes.npy"), (np.floor(r.envelope.astype(float) * 1000.0 + 1e-9) / 1000.0).astype(np.float32))
    _write_json(folder / "import.json", summary)
    log.info("operation import: %d chargers, %d h limited, %d planning / %d real violation h (%.1f s)",
             len(r.flex), r.limited_hours, r.planning_violation_hours, r.actual_violation_hours, summary["seconds"])
    return {k: summary[k] for k in ("verified", "limited_hours", "planning_violation_hours", "actual_violation_hours")}
