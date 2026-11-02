"""The evidence pack for Ei: each of Ei's conditions for conditional connection agreements, beside
what this prototype can show for it.

The conditions and their sources are in config/ei_conditions.toml (Ei2025:01). For each one the
pack gives a state and the facts behind it:

    evidence        the simulation shows it, with its figures
    declared        the DSO has stated it (its settings); the app never states it for the DSO
    not_met         not met in this simulation, with the figures that say so
    not_applicable  does not apply to the basis being read

It reads published results, the DSO's settings and the business case; it computes nothing new and
draws no legal conclusion.
"""

from __future__ import annotations

import tomllib

from ..paths import CONFIG

STATES = ("evidence", "declared", "not_met", "not_applicable")


def conditions() -> dict:
    """The conditions and their source, as config/ei_conditions.toml writes them."""
    with open(CONFIG / "ei_conditions.toml", "rb") as fh:
        return tomllib.load(fh)


def _declared(text: str, code: str) -> tuple[str, dict]:
    text = (text or "").strip()
    if text:
        return "declared", {"code": code, "params": {"text": text}}
    return "not_met", {"code": "missing_declaration", "params": {}}


def assess(basis: str, policy: str, settings, index: dict, agreements: list[dict], report: dict | None,
           business: dict | None = None) -> dict:
    """The pack for one basis and sharing rule. `settings` are the DSO's (dso.DsoSettings)."""
    cfg = conditions()
    combo = index["combos"][basis][policy]
    unneeded = int(combo.get("activations_unnecessary_hours") or 0)
    rows = []
    for c in cfg["condition"]:
        key = c["key"]
        if key == "capacity_shortage":
            n = len(agreements)
            rejected = sum(1 for a in agreements if not a.get("static_approved"))
            state = "evidence" if n and rejected == n else "not_met"
            fact = {"code": "capacity_shortage", "params": {"n": n, "rejected": rejected}}
        elif key == "market_first":
            state, fact = _declared(settings.market_alternative_statement, "market_statement")
        elif key == "method_approved":
            state, fact = _declared(settings.method_approval_reference, "method_reference")
        elif key == "review_when_changed":
            state, fact = _declared(settings.review_statement, "review_statement")
        elif key == "terms_how_when":
            state = "evidence"
            at = (business or {}).get("at_horizon", {}).get("transformer")
            fact = {"code": "terms" if at else "terms_plain", "params": {"rule": policy, "hours": int(combo.get("hours_limited") or 0),
                                                "years": settings.deferral_years,
                                                **({"lo": at[0]["net_sek"], "hi": at[1]["net_sek"]} if at else {})}}
        elif key == "only_when_overloaded":
            state = "evidence" if unneeded == 0 else "not_met"
            fact = {"code": "necessary", "params": {"limited": int(combo.get("hours_limited") or 0),
                                                    "needed": int(index.get("necessary_hours") or 0),
                                                    "unneeded": unneeded}}
        elif key == "after_market_result":
            if basis == "foresight":
                state, fact = "not_applicable", {"code": "foresight_hindsight", "params": {}}
            else:
                state, fact = "not_met", {"code": "dayahead_climatology", "params": {"unneeded": unneeded}}
        elif key == "annual_report":
            state = "evidence" if report else "not_met"
            fact = {"code": "report", "params": {"hours": int((report or {}).get("hours_with_curtailment") or 0),
                                                 "kwh": float((report or {}).get("curtailed_kwh") or 0.0)}}
        else:  # a condition added to the file without an assessment here
            state, fact = "not_met", {"code": "unassessed", "params": {}}
        rows.append({"key": key, "evidence": c["evidence"], "state": state, "fact": fact, "source": c["source"],
                     "label": c["label"], "label_es": c["label_es"], "label_sv": c["label_sv"]})
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    return {"basis": basis, "policy": policy, "year": index.get("year"), "source": cfg["source"],
            "conditions": rows, "counts": counts}
