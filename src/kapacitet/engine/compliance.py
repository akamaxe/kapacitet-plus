"""The paperwork a conditional connection agreement leaves behind.

Ei permits conditional agreements only where scarcity is real, on approved terms, with
curtailment as a last resort and annual reporting. Two of those four are evidence the
operator has to be able to produce afterwards, and nobody hands it to them today:

* an **audit trail**, one row per curtailment, saying which component was out of room and
  what it would have been loaded to had nobody been limited, and
* an **annual report**, the aggregate of that trail per customer and per year.

Both are derived from a computed year, never typed in. The trail is the expensive part of
proving "last resort": it is not enough to say a customer was curtailed, the operator has
to show the alternative was exceeding a rating.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import A
from ..timeindex import local_standard, year_index
from .doe import DoeResult
from .netmodel import NetModel

CONSTRAINT = {0: "voltage", 1: "cable", 2: "transformer"}
MIN_EVENT_KWH = 1e-3   # the resolution the trail is written to


@dataclass
class Trail:
    """Curtailment events and the loading that justified each one."""

    events: pd.DataFrame
    year: int
    policy: str

    @property
    def hours_with_curtailment(self) -> int:
        return int(self.events["hour"].nunique()) if len(self.events) else 0

    @property
    def energy_kwh(self) -> float:
        return float(self.events["curtailed_kwh"].sum()) if len(self.events) else 0.0


def audit_trail(model: NetModel, result: DoeResult, arrays: dict[str, np.ndarray],
                year: int, cids: np.ndarray | None = None) -> Trail:
    """One row per customer and hour where an envelope cut a customer's export.

    `uncontrolled_pct` is the load the binding component would have carried had the
    envelopes not been issued. It is the number that makes the curtailment defensible, so
    it is measured with the full nonlinear flow rather than estimated.
    """
    if result.binding is None:
        raise ValueError("this result predates the binding-constraint record; recompute it")

    hours = result.hours
    nonfirm = result.nonfirm
    if len(hours) == 0:
        return Trail(_empty_events(), year, str(result.info.get("policy", "")))

    # What the grid would have carried with nobody limited, on the same hours.
    p_free = arrays["p_net"][:, hours].astype(float).copy()
    p_free[nonfirm] = np.maximum(p_free[nonfirm], result.desired[:, hours])
    free = model.solve(p_free, arrays["q"][:, hours].astype(float))
    trafo_free = free.trafo_loading
    line_free = free.line_loading

    index = local_standard(year_index(year))
    cids = np.arange(model.C) if cids is None else np.asarray(cids)
    # The trail is written to 1 Wh. Anything below that is not a curtailment anybody can
    # act on, and a row reading "0.000 kWh" would only be noise in a filing.
    cut = np.maximum(result.desired[:, hours] - result.E, 0.0)      # (N, Hc) kW
    who, when = np.nonzero(cut >= MIN_EVENT_KWH)
    if len(who) == 0:
        return Trail(_empty_events(), year, str(result.info.get("policy", "")))

    binding = result.binding[when]
    line = result.binding_line[when]
    element = np.where(binding == 2, "transformer",
                       np.where(binding == 1, np.char.add("line ", line.astype(str)), "voltage"))
    loading = np.where(binding == 2, trafo_free[when],
                       np.where(binding == 1, line_free[np.clip(line, 0, None), when], np.nan))

    events = pd.DataFrame({
        "hour": hours[when],
        "timestamp": index[hours[when]],
        "customer_id": cids[nonfirm[who]],
        "desired_kw": np.round(result.desired[:, hours][who, when], 3),
        "granted_kw": np.round(result.E[who, when], 3),
        "curtailed_kwh": np.round(cut[who, when], 3),
        "binding_constraint": [CONSTRAINT[int(b)] for b in binding],
        "binding_element": element,
        "uncontrolled_pct": np.round(loading, 1),
        "rating_pct": model.thermal,
    }).sort_values(["hour", "customer_id"]).reset_index(drop=True)
    return Trail(events, year, str(result.info.get("policy", "")))


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(columns=["hour", "timestamp", "customer_id", "desired_kw", "granted_kw",
                                 "curtailed_kwh", "binding_constraint", "binding_element",
                                 "uncontrolled_pct", "rating_pct"])


def annual_report(trail: Trail, result: DoeResult, cids: np.ndarray | None = None) -> dict:
    """The filing: what was curtailed, from whom, why, and on what terms.

    Every figure is derived from the trail, so the report and the evidence behind it cannot
    drift apart.
    """
    events = trail.events
    nonfirm = result.nonfirm
    # `nonfirm` already holds customer indices; a mapping is only needed when the operator
    # has ids of their own.
    ident = (lambda c: int(c)) if cids is None else (lambda c: int(np.asarray(cids)[c]))
    # What the envelope limits is the export, not the production: a customer consuming its
    # own generation is never curtailed for it, so the export is the honest denominator.
    exported = result.desired.sum(axis=1)                           # kWh per non-firm customer
    curtailed = result.curtailed.sum(axis=1)

    per_customer = []
    for i, c in enumerate(nonfirm):
        rows = events[events["customer_id"] == ident(c)] if len(events) else events
        share = float(curtailed[i] / exported[i] * 100.0) if exported[i] > 0 else 0.0
        per_customer.append({
            "customer_id": ident(c),
            "hours_limited": int(len(rows)),
            "curtailed_kwh": round(float(curtailed[i]), 1),
            "own_export_kwh": round(float(exported[i]), 1),
            "share_of_own_export_pct": round(share, 2),
            "worst_hour_kwh": round(float(rows["curtailed_kwh"].max()), 3) if len(rows) else 0.0,
        })
    per_customer.sort(key=lambda r: -r["curtailed_kwh"])

    by_constraint = (events.groupby("binding_constraint")["hour"].nunique().to_dict()
                     if len(events) else {})
    worst = (events.loc[events["uncontrolled_pct"].idxmax()] if len(events)
             and events["uncontrolled_pct"].notna().any() else None)

    total_exported = float(exported.sum())
    return {
        "year": trail.year,
        "reporting_basis": "Ei2025:01, conditional connection agreements",
        "conditional_agreements": int(len(nonfirm)),
        "hours_with_curtailment": trail.hours_with_curtailment,
        "curtailed_kwh": round(trail.energy_kwh, 1),
        "non_firm_export_kwh": round(total_exported, 1),
        "curtailed_share_of_export_pct": round(trail.energy_kwh / total_exported * 100.0, 2) if total_exported else 0.0,
        "allocation_policy": trail.policy,
        "binding_constraint_hours": {k: int(v) for k, v in by_constraint.items()},
        "worst_uncontrolled": None if worst is None else {
            "timestamp": str(worst["timestamp"]),
            "element": str(worst["binding_element"]),
            "loading_pct": float(worst["uncontrolled_pct"]),
        },
        "verified_violations": int(result.verified_violations),
        "voltage_limits_pu": [A("v_min_pu"), A("v_max_pu")],
        "thermal_limit_pct": A("thermal_limit_pct"),
        "per_customer": per_customer,
    }


def report_text(report: dict) -> str:
    """The same figures as a page a person can read and file."""
    lines = [
        f"Conditional connection agreements, reporting year {report['year']}",
        "=" * 58,
        f"Basis: {report['reporting_basis']}",
        "",
        f"Agreements in force            {report['conditional_agreements']}",
        f"Hours with curtailment         {report['hours_with_curtailment']} of 8760",
        f"Energy curtailed               {report['curtailed_kwh']:,.0f} kWh"
        f"  ({report['curtailed_share_of_export_pct']} % of what those customers would have exported)",
        f"Allocation policy              {report['allocation_policy']}",
        "",
        "Curtailment was a last resort. The hours above are the hours in which a component",
        "would have been loaded beyond its rating had no envelope been issued:",
    ]
    for name, hours in sorted(report["binding_constraint_hours"].items(), key=lambda kv: -kv[1]):
        lines.append(f"   {name:<14} {hours} hours")
    worst = report["worst_uncontrolled"]
    if worst:
        lines += ["", f"Worst case without envelopes: {worst['element']} at "
                      f"{worst['loading_pct']:.0f} % of its rating on {worst['timestamp']}."]
    lines += [
        "",
        f"Every envelope was verified against a full nonlinear power flow. Violations "
        f"remaining after verification: {report['verified_violations']}.",
        f"Limits applied: {report['voltage_limits_pu'][0]}–{report['voltage_limits_pu'][1]} pu "
        f"and {report['thermal_limit_pct']} % of each rating.",
        "",
        "Per customer, the ten most curtailed:",
        f"   {'customer':<10}{'hours':>7}{'kWh':>10}{'% of export':>13}",
    ]
    for row in report["per_customer"][:10]:
        lines.append(f"   {row['customer_id']:<10}{row['hours_limited']:>7}"
                     f"{row['curtailed_kwh']:>10.1f}{row['share_of_own_export_pct']:>13.2f}")
    return "\n".join(lines)
