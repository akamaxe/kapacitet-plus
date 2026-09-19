"""National view: production mix, consumption and prices of SE1–SE4 (eSett + elprisetjustnu)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..config import settings
from ..ingest import esett, prices
from ..paths import RAW, processed
from ..timeindex import local_standard, year_index

TYPES = ["hydro", "nuclear", "wind", "windOffshore", "solar", "thermal", "energyStorage", "other"]


def build() -> dict:
    cfg = settings()
    y = cfg.national_year
    idx = year_index(y)
    months = local_standard(idx).month.to_numpy()
    zones = {}
    for z in cfg.price_zones:
        prod = esett.read_json_table(RAW / "esett" / f"exp16_production_{z}_{y}.json").reindex(idx)
        cons = esett.read_json_table(RAW / "esett" / f"exp15_consumption_{z}_{y}.json").reindex(idx)
        mix = {t: float(prod[t].fillna(0).sum() / 1e6) for t in TYPES if t in prod}          # TWh
        total_prod = float(prod["total"].fillna(0).sum() / 1e6)
        total_cons = float(-cons["total"].fillna(0).sum() / 1e6)
        p = prices.hourly_prices(prices.read_elpris(z, y), idx)
        sek = p["sek_kwh"].to_numpy()
        monthly = []
        for mo in range(1, 13):
            sel = months == mo
            monthly.append({
                "month": mo,
                "production_twh": float(prod["total"].to_numpy()[sel].sum() / 1e6),
                "consumption_twh": float(-cons["total"].to_numpy()[sel].sum() / 1e6),
                "price_sek_kwh": float(np.nanmean(sek[sel])),
                "mix_twh": {t: float(prod[t].to_numpy()[sel].sum() / 1e6) for t in TYPES if t in prod},
            })
        zones[z] = {
            "production_twh": total_prod, "consumption_twh": total_cons, "balance_twh": total_prod - total_cons,
            "mix_twh": mix, "price_mean_sek_kwh": float(np.nanmean(sek)),
            "price_p05_sek_kwh": float(np.nanpercentile(sek, 5)), "price_p95_sek_kwh": float(np.nanpercentile(sek, 95)),
            "negative_price_hours": int((sek < 0).sum()),
            "profiled_share": float((cons["profiled"].sum() / cons["total"].sum())) if "profiled" in cons else None,
            "monthly": monthly,
        }
    out = {"year": y, "zones": zones,
           "capacity_links": [
               {"name": "Svenska kraftnät — national capacity map (June 2026)",
                "url": "https://www.energi.se/artiklar/2026/juni-2026/svenska-kraftnats-nya-karta-visar-kapacitetslaget-i-hela-landet/"},
               {"name": "Vattenfall Eldistribution — Kapacitetskarta",
                "url": "https://www.vattenfalleldistribution.se/var-verksamhet/om-elnatet/kapacitet/kapacitetskarta/"},
               {"name": "Ellevio — Elnätskapacitet i regionnätet",
                "url": "https://www.ellevio.se/om-oss/elnat/elnatskapacitet-i-ellevios-regionnat/gavleborg/"},
           ]}
    processed("results", "national.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out
