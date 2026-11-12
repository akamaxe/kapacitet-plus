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


def _central_default() -> list[int]:
    from ..config import settings

    return list(settings().price_years)


def price_basis(years_range, years_central) -> dict:
    """Which price years a figure rests on and where each year's prices come from.

    `years_central` are the years the central figure is the mean of (the current market regime),
    `years_range` every weather year with prices. When they are the same years the range adds
    nothing beyond them, and `range_is_central` says so."""
    from ..ingest.prices import source_for_year

    rng, cen = sorted(int(y) for y in years_range), sorted(int(y) for y in years_central)
    sources = [{"year": y, "source": source_for_year(y)} for y in rng]
    return {"years_central": cen, "years_range": rng, "range_is_central": rng == cen,
            "sources": sources, "source_keys": sorted({s["source"] for s in sources})}


def spread(by_year: dict[int, float], central) -> dict | None:
    """A price-derived figure as its central value and its range over the price years.

    `central` is the mean over the central years that have a value (all years when none has);
    `range` is the min, max and p10-p90 over every year (linear interpolation between years)."""
    years = sorted(int(y) for y in by_year)
    if not years:
        return None
    vals = sorted(float(by_year[y]) for y in years)
    cen = [y for y in sorted({int(c) for c in central}) if y in by_year] or years

    def pct(q: float) -> float:
        pos = q * (len(vals) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(vals) - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)

    return {"central": sum(float(by_year[y]) for y in cen) / len(cen),
            "range": {"min": vals[0], "max": vals[-1], "p10": pct(0.10), "p90": pct(0.90)},
            "years_central": cen, "years_range": years, "range_is_central": cen == years,
            "by_year": [{"year": y, "value": float(by_year[y])} for y in years]}


def overinstallation_summary(d: dict, central=None) -> dict:
    """The value curve of over-installation.

    Net spot value (negative-price hours count against), as the mean over the central price years
    (`central`, [prices] in site.toml; every priced year when none of them has prices). Returns the
    curve, the figures at the static limit, at the curtailment budget and at full adoption, what
    each step of extra solar adds ("the last kW"), and the budget's figures for each price year.
    """
    pts = d["points"]
    every = set(d["price_years"])
    central = _central_default() if central is None else list(central)
    priced = (every & set(central)) or every
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
    # the budget point's value in each price year, for the range of every figure built on it
    rows = {r["year"]: r for r in pts[i_budget]["per_year"] if r["year"] in every}
    budget_spread = {"curtailed_sek": spread({y: r["curtailed_value"]["net_sek"] for y, r in rows.items()}, priced),
                     "delivered_sek": spread({y: r["delivered_value"]["net_sek"] for y, r in rows.items()}, priced)}
    return {
        "price_years": sorted(priced), "prices": price_basis(every, priced), "budget_spread": budget_spread,
        "z_star_pct": d["z_star_pct"], "value_basis": d.get("value_basis"),
        "envelope_basis": d.get("envelope_basis", "foresight"), "order": d.get("order"),
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


class NoReinforcementCase(LookupError):
    """The sensitivity holds no 0.63 MVA transformer case: the network's own unit is not the
    0.4 MVA one the norm costs on file were chosen for."""


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

    `basis` says what the envelope side rests on: the deployment the perfect-foresight search sized
    at the aggregate curtailment budget, which is the planning figure and not what can be issued
    the day before (that one curtails far more: results/operation).

    The costs bracket a 630 kVA unit, so the comparison exists only for a network whose sensitivity
    has that case (a 0.4 MVA transformer, as the reference network's): any other raises
    NoReinforcementCase rather than pricing a unit nobody costed.
    """
    big = next((c for c in tornado["cases"] if c["key"] == "trafo" and c["level"].startswith("0.63")), None)
    if big is None:
        raise NoReinforcementCase("no 0.63 MVA transformer case in the sensitivity")
    base = tornado["base"]
    t_lo, t_hi = cost["reinforce_trafo_500_sek"], cost["reinforce_trafo_800_sek"]
    station = cost["reinforce_station_800_sek"] + cost["reinforce_urban_supplement_sek"]
    budget = over["budget"]
    curt = budget["curtailed_sek"]

    def per_year(x: float) -> float | None:
        return years_of_curtailment(x, curt)
    return {
        "price_level_year": int(cost["reinforce_price_level_year"]),
        "transformer_sek": [t_lo, t_hi], "station_sek": station, "with_station_sek": [t_lo + station, t_hi + station],
        "today": {"static_kw": base["static_kw"], "timeseries_kw": base["timeseries_kw"], "doe_kw": base["doe_kw"]},
        "bigger": {"level": big["level"], "static_kw": big["static_kw"], "timeseries_kw": big["timeseries_kw"],
                   "doe_kw": big["doe_kw"]},
        "envelopes": {"kw_ac": budget["kw_ac"], "nonfirm_kw": budget["kw_ac_nonfirm"],
                      "curtailed_sek_per_year": curt, "delivered_sek_per_year": budget["delivered_sek"],
                      "price_years": over["price_years"], "spread": over.get("budget_spread")},
        "prices": over.get("prices"),
        "years_of_curtailment": {"transformer": [per_year(t_lo), per_year(t_hi)],
                                 "with_station": [per_year(t_lo + station), per_year(t_hi + station)]},
        "order": tornado.get("order"), "order_over": over.get("order"),
        "basis": {"envelopes": over.get("envelope_basis", "foresight"), "budget": "aggregate",
                  "z_star_pct": over.get("z_star_pct")},
    }


# Ei's real rates a deferral is priced at: the one in force and the two Ei has put forward for
# 2028-2031 (assumptions.toml, with their sources); /business and `study options` both use them
RATES = ("ei_wacc_real_pct", "ei_wacc_real_pct_2028_prelim", "ei_wacc_real_pct_2028_current_method")


def years_of_curtailment(cost: float | None, curtailed_sek_per_year: float | None) -> float | None:
    """How many years of curtailment a one-off cost would buy (undiscounted); None without both."""
    if cost is None or not curtailed_sek_per_year or curtailed_sek_per_year <= 0:
        return None
    return cost / curtailed_sek_per_year


def annuity(rate: float, years: int) -> float:
    """Present value of 1 a year for `years` years at `rate`, paid at the end of each year."""
    return (1 - (1 + rate) ** -years) / rate if rate else float(years)


def deferral_value(cost: float, rate: float, years: int) -> float:
    """Present value of paying `cost` `years` years later instead of now."""
    return cost * (1 - (1 + rate) ** -years)


def deferral_case(cost: float, curtailed_sek_per_year: float | None, rate: float, years: int) -> dict:
    """Deferring `cost` by `years` at `rate` against curtailing `curtailed_sek_per_year` meanwhile:
    {"years", "deferral_sek", "curtailment_sek", "net_sek"}; without the curtailment's value the
    last two are None. The one implementation behind business() and `study options`."""
    d = deferral_value(cost, rate, years)
    if curtailed_sek_per_year is None:
        return {"years": years, "deferral_sek": d, "curtailment_sek": None, "net_sek": None}
    c = curtailed_sek_per_year * annuity(rate, years)
    return {"years": years, "deferral_sek": d, "curtailment_sek": c, "net_sek": d - c}


def business(reinf: dict, rate_pct: float, horizon: int, max_years: int = 20,
             rates: dict[str, float] | None = None) -> dict:
    """The DSO's case for deferring the reinforcement with envelopes, from `reinforcement()`.

    Deferring a cost C by T years is worth C(1 - (1+r)^-T) today; the envelopes cost the houses
    their curtailed energy, k a year, worth k a(r, T) over the same years. The net,
    (1 - (1+r)^-T)(C - k/r), is positive for every horizon when C exceeds k/r, the value of the
    curtailment forever. r is Ei's real rate and C its norm values at their fixed price level, so the
    two are on the same real basis; k is the mean spot value of the curtailed energy over the central
    price years, in the money of each year (not deflated).

    The same case is worked out with each price year's k (weather year y at the prices of year y):
    `range` gives each figure at the horizon as its central value and its spread over the years.
    """
    r = rate_pct / 100.0
    k = reinf["envelopes"]["curtailed_sek_per_year"]
    lo, hi = reinf["transformer_sek"]
    slo, shi = reinf["with_station_sek"]

    def point(cost: float, t: int, k: float = k) -> dict:
        return deferral_case(cost, k, r, t)

    k_spread = (reinf["envelopes"].get("spread") or {}).get("curtailed_sek")
    rng = None
    if k_spread:
        ky = {e["year"]: e["value"] for e in k_spread["by_year"]}
        cen = k_spread["years_central"]

        def over_years(cost: float, field: str) -> dict:
            return spread({y: point(cost, horizon, kk)[field] for y, kk in ky.items()}, cen)

        rng = {"curtailed_sek_per_year": k_spread,
               "curtailment_sek": over_years(lo, "curtailment_sek"),
               "transformer": [over_years(lo, "net_sek"), over_years(hi, "net_sek")],
               "with_station": [over_years(slo, "net_sek"), over_years(shi, "net_sek")]}

    years = range(0, max_years + 1)
    # the same horizon at the other rates the regulator has put forward (assumptions.toml): what
    # deferring is worth depends on the rate as much as on the curtailment
    at_rates = {}
    for key, pct in (rates or {}).items():
        rr = pct / 100.0
        def at(cost: float, rr=rr) -> dict:
            return deferral_case(cost, k, rr, horizon)
        at_rates[key] = {"rate_pct": pct, "transformer": [at(lo), at(hi)], "with_station": [at(slo), at(shi)]}
    return {
        "rate_pct": rate_pct, "rate_basis": "real", "price_level_year": reinf["price_level_year"],
        "horizon_years": horizon, "price_years": reinf["envelopes"]["price_years"],
        "curtailed_sek_per_year": k, "delivered_sek_per_year": reinf["envelopes"]["delivered_sek_per_year"],
        "costs": {"transformer": [lo, hi], "with_station": [slo, shi]},
        "perpetual_curtailment_sek": k / r if r else None,
        "curves": {"low": [point(lo, t) for t in years], "high": [point(hi, t) for t in years]},
        "at_horizon": {"transformer": [point(lo, horizon), point(hi, horizon)],
                       "with_station": [point(slo, horizon), point(shi, horizon)]},
        "at_rates": at_rates, "basis": reinf.get("basis"),
        "prices": reinf.get("prices"), "range": rng,
    }
