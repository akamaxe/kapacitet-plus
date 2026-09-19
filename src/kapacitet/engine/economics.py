"""Spot prices on the study-year index and the value of curtailed energy."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import settings
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
