"""Spot prices on the study-year index, the value of curtailed energy, and a house's solar at retail."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import A, settings
from ..ingest import prices
from ..paths import raw
from ..timeindex import year_index


@lru_cache(maxsize=16)
def hourly(year: int, zone: str | None = None) -> pd.DataFrame | None:
    """Hourly SEK/kWh and EUR/kWh for `year` (None if no price data for that year)."""
    zone = zone or settings().site.bidding_zone
    path = raw("prices", f"elpris_{zone}_{year}.jsonl")
    if not path.exists():
        return None
    df = prices.read_elpris(zone, year)
    return prices.hourly_prices(df, year_index(year))


def price_years() -> list[int]:
    return [y for y in settings().weather_years if hourly(y) is not None]


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


def owner_case(result: dict, years: int, rate_pct: float, start_year: int) -> dict | None:
    """What connecting now with the envelope is worth to the house, against waiting `years` years.

    Each year the house uses part of its solar itself, which saves buying it (the spot price plus
    the energy tax and the grid's transfer fee, all with VAT), and exports the rest (the spot price
    and the DSO's compensation, without VAT for a private person; the tax reduction only in the
    years it existed). Waiting gives none of that until the grid is reinforced. The values are the
    mean of the price years at today's taxes and fees; the solar system's own cost is the same
    either way and is left out.
    """
    econ = result.get("economics") or {}
    rows = econ.get("by_year") or []
    prof = result.get("profiles") or {}
    if not rows or not prof.get("desired_kw"):
        return None
    vat, tax, fee = A("vat_rate"), A("energy_tax_sek_kwh"), A("grid_transfer_fee_sek_kwh")
    desired = np.asarray(prof["desired_kw"], dtype=float)
    curtailed = np.asarray(prof.get("curtailed_kw") or np.zeros_like(desired), dtype=float)
    rate, peak_share = feed_in_rate(int(prof["year"]), desired - curtailed)

    def mean(key: str) -> float:
        return float(np.mean([r.get(key, 0.0) or 0.0 for r in rows]))

    kwh = {"self_use": mean("self_use_kwh"), "battery": mean("battery_discharged_kwh"),
           "export": mean("export_kwh"), "curtailed": mean("curtailed_kwh")}
    home = kwh["self_use"] + kwh["battery"]
    self_use_sek = (mean("self_use_spot_sek") + mean("battery_sek") + home * (tax + fee)) * (1 + vat)
    export_sek = mean("export_sek") + kwh["export"] * rate
    curtailed_sek = mean("curtailed_sek_net") + kwh["curtailed"] * rate
    h = rate_pct / 100.0
    calendar = [start_year + t for t in range(years)]
    reduction = [tax_reduction_sek(kwh["export"], y) for y in calendar]
    yearly = [self_use_sek + export_sek + red for red in reduction]
    discount = [(1 + h) ** -(t + 1) for t in range(years)]
    return {
        "years": years, "start_year": start_year, "rate_pct": rate_pct, "price_years": [r["year"] for r in rows],
        "kwh": kwh, "feed_in_sek_kwh": rate, "feed_in_peak_share": peak_share,
        "retail_sek_kwh": {"energy_tax": tax, "transfer_fee": fee, "vat": vat},
        "per_year": {"self_use_sek": self_use_sek, "export_sek": export_sek, "curtailed_sek": curtailed_sek,
                     "tax_reduction_sek": reduction[0] if reduction else 0.0},
        "tax_reduction": {"sek_kwh": A("micro_tax_reduction_sek_kwh"), "last_year": int(A("micro_tax_reduction_last_year")),
                          "years_applied": [y for y, red in zip(calendar, reduction) if red > 0]},
        "connect_now_sek": float(sum(v * d for v, d in zip(yearly, discount))),
        "undiscounted_sek": float(sum(yearly)),
        "curtailment_cost_sek": float(curtailed_sek * sum(discount)),
    }
