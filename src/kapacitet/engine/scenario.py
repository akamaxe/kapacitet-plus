"""Assemble per-customer hourly power for a scenario (who has PV, heat pump, EV)."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import A
from ..grid.builder import load_tables
from ..paths import processed
from ..profiles import household
from ..profiles.pv import load_ac_per_kwp


@lru_cache(maxsize=1)
def customers() -> pd.DataFrame:
    _, _, cust = load_tables()
    roofs = pd.read_parquet(processed("pv", "roofs.parquet"))
    return cust.merge(roofs, on="cid", how="left")


@lru_cache(maxsize=16)
def year_profiles(year: int, load_seed: int = 0) -> dict[str, np.ndarray]:
    """Per-customer hourly arrays (kW) for one weather year: base load, HP, EV, PV per kWp."""
    C = len(customers())
    return {
        "base": household.customer_base_load(year, C, load_seed).astype(np.float32),
        "hp": np.load(processed("heatpump", f"hp_el_{year}.npy")),
        "ev": np.load(processed("ev", f"ev_uncontrolled_{year}.npy")),
        "pv_per_kwp": load_ac_per_kwp(year),
    }


def adoption_order(seed: int, n: int | None = None) -> np.ndarray:
    """A random order in which customers adopt a technology (seeded, reproducible)."""
    n = n or len(customers())
    return np.random.default_rng(10_000 + seed).permutation(n)


def mask_from_share(order: np.ndarray, share: float) -> np.ndarray:
    m = np.zeros(len(order), dtype=bool)
    m[order[: int(round(share * len(order)))]] = True
    return m


@dataclass
class Scenario:
    year: int
    pv: np.ndarray                      # bool (C,) customers with PV
    hp: np.ndarray                      # bool (C,) customers with a heat pump
    ev: np.ndarray                      # bool (C,) customers with an EV
    load_seed: int = 0
    pv_kwp: np.ndarray | None = None    # override PV size (kWp) per customer
    extra_load_kw: np.ndarray | None = field(default=None, repr=False)  # (C, H) extra load, e.g. a request

    def arrays(self) -> dict[str, np.ndarray]:
        """Hourly arrays (kW): load, pv (AC, inverter-capped), p_net (+ = export), q (kvar)."""
        prof = year_profiles(self.year, self.load_seed)
        cust = customers()
        kwp = self.pv_kwp if self.pv_kwp is not None else cust["kwp"].to_numpy()
        ilr = A("pv_dc_ac_ratio")
        inv = np.minimum(kwp / ilr, cust["fuse_kva"].to_numpy())
        per_kwp = np.minimum(prof["pv_per_kwp"], 1.0 / ilr)
        pv = np.where(self.pv[:, None], np.minimum(per_kwp * kwp[:, None], inv[:, None]), 0.0)
        base = prof["base"] * A("household_load_factor")
        load = base + np.where(self.hp[:, None], prof["hp"], 0.0) + np.where(self.ev[:, None], prof["ev"], 0.0)
        if self.extra_load_kw is not None:
            load = load + self.extra_load_kw
        tanphi = np.tan(np.arccos(A("load_power_factor")))
        return {
            "load": load.astype(np.float32),
            "pv": pv.astype(np.float32),
            "p_net": (pv - load).astype(np.float32),
            "q": (-load * tanphi).astype(np.float32),
        }
