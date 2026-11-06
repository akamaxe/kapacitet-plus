"""Spot prices on the study-year index, the value of curtailed energy, and a house's solar at retail."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import A, settings
from ..ingest import prices
from ..paths import raw
from ..timeindex import year_index


@lru_cache(maxsize=32)
def hourly(year: int, zone: str | None = None) -> pd.DataFrame | None:
    """Hourly SEK/kWh and EUR/kWh for `year` (None if no price data for that year): elprisetjustnu
    where it has the year, else ENTSO-E's EUR prices at the ECB's daily rate when they were fetched."""
    zone = zone or settings().site.bidding_zone
    path = raw("prices", f"elpris_{zone}_{year}.jsonl")
    if path.exists():
        return prices.hourly_prices(prices.read_elpris(zone, year), year_index(year))
    return prices.entsoe_hourly(zone, year, year_index(year))


def price_years() -> list[int]:
    """The weather years that have prices: the range of every price-derived figure."""
    return [y for y in settings().weather_years if hourly(y) is not None]


def central_years() -> list[int]:
    """The years whose mean is the central figure: the current market regime ([prices] in site.toml)."""
    return list(settings().price_years)


def value_sek(energy_kwh_by_hour: np.ndarray, year: int) -> dict[str, float] | None:
    """Gross and net spot value (SEK) of an hourly energy series (…, H)."""
    p = hourly(year)
    if p is None:
        return None
    sek = p["sek_kwh"].to_numpy()
    e = np.asarray(energy_kwh_by_hour, dtype=float)
    total = e.sum(axis=tuple(range(e.ndim - 1))) if e.ndim > 1 else e
    return {
        "gross_sek": float((total * np.maximum(sek, 0.0)).sum()),   # value of the hours with positive price
        "net_sek": float((total * sek).sum()),                       # negative-price hours count as savings
        "negative_price_kwh": float(total[sek < 0].sum()),
        "mean_price_sek_kwh": float(sek.mean()),
    }


# ---------------------------------------------------------------- a house's solar at retail prices

def feed_in_rate(year: int, export_kw) -> tuple[float, float]:
    """The DSO's compensation for exported solar (SEK/kWh) weighted by when the house exports, and
    the share of its export in peak-load time (Ellevio: Monday to Friday 06-22, November to March)."""
    local = year_index(year).tz_convert("Europe/Stockholm")
    peak = np.asarray(local.month.isin([11, 12, 1, 2, 3]) & (local.dayofweek < 5) & (local.hour >= 6) & (local.hour < 22))
    e = np.clip(np.asarray(export_kw, dtype=float)[: len(local)], 0.0, None)
    share = float(e[peak[: len(e)]].sum() / e.sum()) if e.sum() > 0 else 0.0
    return A("feed_in_peak_sek_kwh") * share + A("feed_in_offpeak_sek_kwh") * (1 - share), share


def tax_reduction_sek(export_kwh: float, year: int) -> float:
    """The tax reduction a year's exported solar earned: only up to its last year, and capped."""
    if year > int(A("micro_tax_reduction_last_year")):
        return 0.0
    return A("micro_tax_reduction_sek_kwh") * min(max(export_kwh, 0.0), A("micro_tax_reduction_cap_kwh"))


def owner_case(result: dict, years: int, rate_pct: float, start_year: int,
               central: list[int] | None = None) -> dict | None:
    """What connecting now with the envelope is worth to the house, against waiting `years` years.

    Each year the house uses part of its solar itself, which saves buying it (the spot price plus
    the energy tax and the grid's transfer fee, all with VAT), and exports the rest (the spot price
    and the DSO's compensation, without VAT for a private person; the tax reduction only in the
    years it existed). Waiting gives none of that until the grid is reinforced. Taxes and fees are
    today's; the solar system's own cost is the same either way and is left out.

    The case is worked out for each price year (weather year y at the prices of year y). The headline
    is its mean over the central years (`central`, the current market regime: [prices] in site.toml),
    and `range` spreads each figure over every year with prices.
    """
    from .summaries import price_basis, spread

    econ = result.get("economics") or {}
    rows = econ.get("by_year") or []
    prof = result.get("profiles") or {}
    if not rows or not prof.get("desired_kw"):
        return None
    central = central_years() if central is None else list(central)
    vat, tax, fee = A("vat_rate"), A("energy_tax_sek_kwh"), A("grid_transfer_fee_sek_kwh")
    desired = np.asarray(prof["desired_kw"], dtype=float)
    curtailed = np.asarray(prof.get("curtailed_kw") or np.zeros_like(desired), dtype=float)
    rate, peak_share = feed_in_rate(int(prof["year"]), desired - curtailed)
    h = rate_pct / 100.0
    calendar = [start_year + t for t in range(years)]
    discount = [(1 + h) ** -(t + 1) for t in range(years)]

    def case(subset: list[dict]) -> dict:
        def mean(key: str) -> float:
            return float(np.mean([r.get(key, 0.0) or 0.0 for r in subset]))

        kwh = {"self_use": mean("self_use_kwh"), "battery": mean("battery_discharged_kwh"),
               "export": mean("export_kwh"), "curtailed": mean("curtailed_kwh")}
        home = kwh["self_use"] + kwh["battery"]
        self_use_sek = (mean("self_use_spot_sek") + mean("battery_sek") + home * (tax + fee)) * (1 + vat)
        export_sek = mean("export_sek") + kwh["export"] * rate
        curtailed_sek = mean("curtailed_sek_net") + kwh["curtailed"] * rate
        reduction = [tax_reduction_sek(kwh["export"], y) for y in calendar]
        yearly = [self_use_sek + export_sek + red for red in reduction]
        return {"kwh": kwh, "self_use_sek": self_use_sek, "export_sek": export_sek, "curtailed_sek": curtailed_sek,
                "reduction": reduction, "connect_now_sek": float(sum(v * d for v, d in zip(yearly, discount))),
                "undiscounted_sek": float(sum(yearly)), "curtailment_cost_sek": float(curtailed_sek * sum(discount))}

    in_central = [r for r in rows if r["year"] in set(central)]
    head = case(in_central or rows)
    each = {int(r["year"]): case([r]) for r in rows}
    used = sorted(r["year"] for r in (in_central or rows))
    keys = ("connect_now_sek", "undiscounted_sek", "curtailment_cost_sek", "self_use_sek", "export_sek", "curtailed_sek")
    reduction = head["reduction"]
    return {
        "years": years, "start_year": start_year, "rate_pct": rate_pct, "price_years": used,
        "kwh": head["kwh"], "feed_in_sek_kwh": rate, "feed_in_peak_share": peak_share,
        "retail_sek_kwh": {"energy_tax": tax, "transfer_fee": fee, "vat": vat},
        "per_year": {"self_use_sek": head["self_use_sek"], "export_sek": head["export_sek"],
                     "curtailed_sek": head["curtailed_sek"], "tax_reduction_sek": reduction[0] if reduction else 0.0},
        "tax_reduction": {"sek_kwh": A("micro_tax_reduction_sek_kwh"), "last_year": int(A("micro_tax_reduction_last_year")),
                          "years_applied": [y for y, red in zip(calendar, reduction) if red > 0]},
        "connect_now_sek": head["connect_now_sek"],
        "undiscounted_sek": head["undiscounted_sek"],
        "curtailment_cost_sek": head["curtailment_cost_sek"],
        "prices": price_basis(sorted(each), used),
        "range": {k: spread({y: c[k] for y, c in each.items()}, used) for k in keys},
    }
