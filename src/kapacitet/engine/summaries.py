"""Small derived figures read from published results, shared by the API and the tests.

Nothing here runs the engine: these are the sums and ratios a screen states next to a result,
computed once in one place so the interface, the API and any report built on them cannot disagree.
"""

from __future__ import annotations

from datetime import datetime


def hour_of_timestamp(timestamp: str, year: int) -> int:
    """Hour index of a study-year timestamp ('YYYY-MM-DD HH:MM:SS', local standard time UTC+1).

    The results write timestamps in the same clock as the hour index (timeindex.py), so the
    index is the plain number of hours since 1 January 00:00.
    """
    t = datetime.fromisoformat(timestamp)
    return int((t - datetime(year, 1, 1)).total_seconds() // 3600)


def _mean_priced(point: dict, key: str, priced: set[int], value: str | None = None) -> float:
    rows = [r for r in point["per_year"] if r["year"] in priced]
    if value is None:
        return sum(r[key] for r in rows) / len(rows)
    return sum(r[key][value] for r in rows) / len(rows)


def overinstallation_summary(d: dict) -> dict:
    """The value curve of over-installation, as overinstallation_summary computes it.

    Mean over the years that have prices, net spot value (negative-price hours count against).
    Returns the curve, the figures at the static limit, at the curtailment budget and at full
    adoption, and what each step of extra solar adds ("the last kW").
    """
    pts = d["points"]
    priced = set(d["price_years"])
    curve = [{"adopters": p["adopters"], "kw_ac": p["kw_ac"], "kw_ac_nonfirm": p["kw_ac_nonfirm"],
              "delivered_sek": _mean_priced(p, "delivered_value", priced, "net_sek"),
              "curtailed_sek": _mean_priced(p, "curtailed_value", priced, "net_sek"),
              "potential_kwh": _mean_priced(p, "potential_kwh", priced),
              "curtailed_kwh": _mean_priced(p, "curtailed_kwh", priced)} for p in pts]
    marginal = []
    for a, b in zip(curve[:-1], curve[1:]):
        dpot = b["potential_kwh"] - a["potential_kwh"]
        dcut = b["curtailed_kwh"] - a["curtailed_kwh"]
        ddel = b["delivered_sek"] - a["delivered_sek"]
        dcv = b["curtailed_sek"] - a["curtailed_sek"]
        marginal.append({"mid_kw": (a["kw_ac"] + b["kw_ac"]) / 2,
                         "energy_share_pct": 100 * dcut / dpot if dpot else 0.0,
                         "value_share_pct": 100 * dcv / (ddel + dcv) if (ddel + dcv) else 0.0,
                         "value_per_kw_sek": ddel / (b["kw_ac"] - a["kw_ac"])})
    i_budget = next(i for i, p in enumerate(pts) if p["adopters"] == d["doe_adopters"])
    return {
        "price_years": sorted(priced), "z_star_pct": d["z_star_pct"], "value_basis": d.get("value_basis"),
        "order": d.get("order"),
        "static": curve[0], "budget": curve[i_budget], "full": curve[-1],
        "budget_marginal": marginal[i_budget - 1] if i_budget > 0 else None,
        "full_marginal": marginal[-1] if marginal else None,
        "value_per_kw_range_sek": [min(m["value_per_kw_sek"] for m in marginal),
                                   max(m["value_per_kw_sek"] for m in marginal)] if marginal else None,
        "curve": curve, "marginal": marginal,
    }


def audit_totals(rows: list[dict]) -> dict:
    """Totals of the hourly audit trail, to set against the annual filing."""
    hours = {int(r["hour"]) for r in rows}
    per: dict[int, dict] = {}
    for r in rows:
        c = per.setdefault(int(r["customer_id"]), {"hours": set(), "kwh": 0.0})
        c["hours"].add(int(r["hour"]))
        c["kwh"] += float(r["curtailed_kwh"])
    return {"hours": len(hours), "kwh": sum(float(r["curtailed_kwh"]) for r in rows),
            "per_customer": {cid: {"hours": len(v["hours"]), "kwh": v["kwh"]} for cid, v in per.items()}}


def reinforcement(tornado: dict, over: dict, cost: dict[str, float]) -> dict:
    """Reinforce the transformer, or connect the same houses with envelopes? Both from published results.

    - Cost: Ei's norm values (assumptions.toml). The list has no 630 kVA unit, so the transformer
      swap is bracketed between its 500 and 800 kVA codes; a new station (and the urban supplement)
      is added only if the existing one cannot take the bigger unit.
    - Capacity with a 0.63 MVA transformer: the tornado's case, for its adoption order.
    - What the envelopes cost instead: the spot value of the energy they curtail at the published
      deployment, and what the houses beyond the static limit deliver each year meanwhile
      (over-installation summary, the same adoption order).
    The ratio is undiscounted and compares a one-off cost with a yearly one; it says how many years
    of curtailment the reinforcement would buy, nothing more.
    """
    big = next(c for c in tornado["cases"] if c["key"] == "trafo" and c["level"].startswith("0.63"))
    base = tornado["base"]
    t_lo, t_hi = cost["reinforce_trafo_500_sek"], cost["reinforce_trafo_800_sek"]
    station = cost["reinforce_station_800_sek"] + cost["reinforce_urban_supplement_sek"]
    budget = over["budget"]
    curt = budget["curtailed_sek"]
    per_year = (lambda x: x / curt) if curt > 0 else (lambda x: None)
    return {
        "price_level_year": int(cost["reinforce_price_level_year"]),
        "transformer_sek": [t_lo, t_hi], "station_sek": station, "with_station_sek": [t_lo + station, t_hi + station],
        "today": {"static_kw": base["static_kw"], "timeseries_kw": base["timeseries_kw"], "doe_kw": base["doe_kw"]},
        "bigger": {"level": big["level"], "static_kw": big["static_kw"], "timeseries_kw": big["timeseries_kw"],
                   "doe_kw": big["doe_kw"]},
        "envelopes": {"kw_ac": budget["kw_ac"], "nonfirm_kw": budget["kw_ac_nonfirm"],
                      "curtailed_sek_per_year": curt, "delivered_sek_per_year": budget["delivered_sek"],
                      "price_years": over["price_years"]},
        "years_of_curtailment": {"transformer": [per_year(t_lo), per_year(t_hi)],
                                 "with_station": [per_year(t_lo + station), per_year(t_hi + station)]},
        "order": tornado.get("order"), "order_over": over.get("order"),
    }
