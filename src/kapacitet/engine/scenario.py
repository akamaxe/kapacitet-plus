"""Assemble per-customer hourly power for a scenario (who has PV, heat pump, EV).

What a station already has (the existing_* columns that a DSO's equipment records add to
customers.parquet, importer/build.EXISTING) is part of every scenario: existing PV produces at its
own rating whatever the masks say, as firm generation (`existing`); a heat pump or an EV whose
owner's load is metered is already in that load, so it is not added again (`in_meter`). Without
those columns, as on the reference network, nothing changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..cache import memo
from ..config import A
from ..grid.builder import load_tables
from ..paths import processed
from ..profiles import household
from ..profiles.pv import load_ac_per_kwp


@memo(maxsize=1)
def customers() -> pd.DataFrame:
    _, _, cust = load_tables()
    roofs = pd.read_parquet(processed("pv", "roofs.parquet"))
    return cust.merge(roofs, on="cid", how="left")


@memo(maxsize=16)
def year_profiles(year: int, load_seed: int = 0) -> dict[str, np.ndarray]:
    """Per-customer hourly arrays (kW) for one weather year: base load, HP, EV, PV per kWp."""
    C = len(customers())
    return {
        "base": household.customer_base_load(year, C, load_seed).astype(np.float32),
        "hp": np.load(processed("heatpump", f"hp_el_{year}.npy")),
        "ev": np.load(processed("ev", f"ev_uncontrolled_{year}.npy")),
        "pv_per_kwp": load_ac_per_kwp(year),
    }


@memo(maxsize=1)
def existing() -> dict[str, np.ndarray]:
    """What is connected today by the DSO's records, per customer: "pv_kwp" and "pv_kw_ac" (0 without
    PV), "has_pv", "hp" (True, False, or None where the records say nothing: an object array),
    "ev" (a charger recorded) and "recorded" (whether the station has records at all)."""
    cust = customers()
    C = len(cust)

    def col(name: str) -> np.ndarray:
        return (np.nan_to_num(cust[name].to_numpy(dtype=float)) if name in cust.columns else np.zeros(C))

    kwp, kw_ac = col("existing_pv_kwp"), col("existing_pv_kw_ac")
    if "existing_heat_pump" in cust.columns:
        hp = np.array([None if pd.isna(v) else bool(v) for v in cust["existing_heat_pump"]], dtype=object)
    else:
        hp = np.full(C, None, dtype=object)
    has_pv = (kw_ac > 0) & (kwp > 0)
    return {"pv_kwp": np.where(has_pv, kwp, 0.0), "pv_kw_ac": np.where(has_pv, kw_ac, 0.0), "has_pv": has_pv,
            "hp": hp, "ev": col("existing_ev_charger_kw") > 0, "recorded": "existing_pv_kw_ac" in cust.columns}


def existing_pv_kw() -> np.ndarray:
    """(C,) the AC rating (kW) of each customer's existing PV, 0 without one."""
    return existing()["pv_kw_ac"]


def existing_hp_true() -> np.ndarray:
    """(C,) bool: the records say the customer has a heat pump."""
    return np.array([v is True for v in existing()["hp"]], dtype=bool)


@memo(maxsize=16)
def in_meter(year: int) -> np.ndarray:
    """(C,) the customers whose load in `year` is their metered load (profiles/metered.py), which
    already holds whatever heat pump or EV they have."""
    C = len(customers())
    path = household.metered_path(year)
    if not path.exists():
        return np.zeros(C, dtype=bool)
    rows = np.isfinite(np.load(path)).all(axis=1)
    return rows if len(rows) == C else np.zeros(C, dtype=bool)


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
    existing_pv: bool = True            # False: the existing PV is left out too (the no-solar state of consumption)

    def arrays(self) -> dict[str, np.ndarray]:
        """Hourly arrays (kW): load, pv (AC, inverter-capped), p_net (+ = export), q (kvar).

        A customer with existing PV produces with it, at its recorded kWp and AC rating, and a mask
        that names it adds nothing more; a heat pump or EV of a metered customer that the records
        say is there is not added on top of the metered load, which holds it already."""
        prof = year_profiles(self.year, self.load_seed)
        cust = customers()
        ex = existing()
        kwp = self.pv_kwp if self.pv_kwp is not None else cust["kwp"].to_numpy()
        ilr = A("pv_dc_ac_ratio")
        inv = np.minimum(kwp / ilr, cust["fuse_kva"].to_numpy())
        per_kwp = np.minimum(prof["pv_per_kwp"], 1.0 / ilr)
        new_pv = np.asarray(self.pv, dtype=bool) & ~ex["has_pv"]
        pv = np.where(new_pv[:, None], np.minimum(per_kwp * kwp[:, None], inv[:, None]), 0.0)
        if self.existing_pv and ex["has_pv"].any():
            own = np.minimum(np.minimum(prof["pv_per_kwp"], 1.0 / ilr) * ex["pv_kwp"][:, None], ex["pv_kw_ac"][:, None])
            pv = pv + np.where(ex["has_pv"][:, None], own, 0.0)
        base = prof["base"] * A("household_load_factor")
        metered = in_meter(self.year)
        hp = np.asarray(self.hp, dtype=bool) & ~(metered & existing_hp_true())
        ev = np.asarray(self.ev, dtype=bool) & ~(metered & ex["ev"])
        load = base + np.where(hp[:, None], prof["hp"], 0.0) + np.where(ev[:, None], prof["ev"], 0.0)
        if self.extra_load_kw is not None:
            load = load + self.extra_load_kw
        tanphi = np.tan(np.arccos(A("load_power_factor")))
        return {
            "load": load.astype(np.float32),
            "pv": pv.astype(np.float32),
            "p_net": (pv - load).astype(np.float32),
            "q": (-load * tanphi).astype(np.float32),
        }
