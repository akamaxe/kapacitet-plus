"""Household base load (appliances + lighting, no electric heating) from richardsonpy.

richardsonpy implements the CREST stochastic demand model (Richardson et al., 2010): occupancy
Markov chains drive appliance switching at 1-minute resolution; lighting responds to outdoor
irradiance, here the real STRÅNG irradiance. `prev_heat_dev=True` excludes electric space and
water heating (heat pumps are modelled separately). The number of occupants of each household
is drawn from SCB's household-size distribution for owner-occupied småhus in Stockholm, and
the whole population is scaled once so its mean annual demand equals the cited Swedish value.

richardsonpy is GPL-3.0; it is only used here, as an offline generator of a profile pool.
"""

from __future__ import annotations

import json
import logging
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import date

import numpy as np
import pandas as pd

from ..config import A, settings
from ..ingest import scb
from ..paths import processed

log = logging.getLogger(__name__)
POOL_YEAR = 2025


def _generate(args: tuple[int, int, int]) -> tuple[int, np.ndarray]:
    """One household-year at 1-minute resolution (W), float32. Runs in a worker process."""
    seed, occupants, year = args
    warnings.filterwarnings("ignore")
    import richardsonpy.classes.electric_load as el
    import richardsonpy.classes.occupancy as oc

    from .weather import load_weather

    w = load_weather(year)
    cosz = np.cos(np.radians(w["zenith"].to_numpy())).clip(0.0)
    q_dir = np.nan_to_num(w["dni"].to_numpy() * cosz / 1000.0)
    q_dif = np.nan_to_num(w["dhi"].to_numpy() / 1000.0)
    n = len(q_dir)
    tq = np.arange(n) * 3600.0 + 1800.0
    tm = np.arange(n * 60) * 60.0 + 30.0
    q_dir_m = np.interp(tm, tq, q_dir)
    q_dif_m = np.interp(tm, tq, q_dif)
    nb_days = n // 24
    initial_day = date(year, 1, 1).isoweekday()
    np.random.seed(seed)
    occ = oc.Occupancy(number_occupants=occupants, initial_day=initial_day, nb_days=nb_days)
    load = el.ElectricLoad(occ_profile=occ.occupancy, total_nb_occ=occupants, q_direct=q_dir_m,
                           q_diffuse=q_dif_m, is_sfh=True, randomize_appliances=True, prev_heat_dev=True,
                           timestep=60, initial_day=initial_day)
    return seed, np.asarray(load.loadcurve, dtype=np.float32)


def generate_pool(n: int = 160, workers: int = 10, seed: int = 20260919) -> pd.DataFrame:
    """Generate `n` household-years for POOL_YEAR; save hourly and 10-minute profiles."""
    shares = scb.household_size_shares()
    rng = np.random.default_rng(seed)
    sizes = list(shares)
    occupants = rng.choice(sizes, size=n, p=[shares[k] for k in sizes])
    seeds = rng.integers(1, 2**31 - 1, size=n)
    jobs = [(int(s), int(o), POOL_YEAR) for s, o in zip(seeds, occupants)]
    minute: dict[int, np.ndarray] = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, (s, curve) in enumerate(ex.map(_generate, jobs, chunksize=1)):
            minute[s] = curve
            if (i + 1) % 10 == 0:
                log.info("households generated: %d/%d", i + 1, n)
    curves = np.stack([minute[int(s)] for s in seeds])          # (n, minutes) in W
    raw_kwh = curves.sum(axis=1) / 60.0 / 1000.0
    scale = A("household_kwh_per_year") / raw_kwh.mean()
    curves *= scale
    hourly = curves.reshape(n, -1, 60).mean(axis=2) / 1000.0     # kW
    tenmin = curves.reshape(n, -1, 10).mean(axis=2) / 1000.0     # kW
    np.save(processed("households", f"pool_hourly_{POOL_YEAR}.npy"), hourly.astype(np.float32))
    np.save(processed("households", f"pool_10min_{POOL_YEAR}.npy"), tenmin.astype(np.float32))
    meta = pd.DataFrame({
        "hh": np.arange(n), "seed": seeds, "occupants": occupants,
        "annual_kwh": hourly.sum(axis=1), "peak_hourly_kw": hourly.max(axis=1),
        "peak_10min_kw": tenmin.max(axis=1), "peak_1min_kw": curves.max(axis=1) / 1000.0,
    })
    meta.to_parquet(processed("households", "pool_meta.parquet"), index=False)
    processed("households", "pool_info.json").write_text(json.dumps({
        "year": POOL_YEAR, "n": n, "scale_to_mean_kwh": float(A("household_kwh_per_year")),
        "crest_raw_mean_kwh": float(raw_kwh.mean()), "scale_factor": float(scale),
        "occupant_shares": {str(k): v for k, v in shares.items()},
    }, indent=1), encoding="utf-8")
    log.info("pool: %d households, raw CREST mean %.0f kWh → scaled ×%.3f", n, raw_kwh.mean(), scale)
    return meta


def day_alignment(year: int) -> np.ndarray:
    """For each day of `year`, the POOL_YEAR day with the same weekday nearest in the calendar."""
    n_days = 366 if pd.Timestamp(f"{year}-12-31").dayofyear == 366 else 365
    wd_y = np.array([(date(year, 1, 1).toordinal() + d) % 7 for d in range(n_days)])
    wd_p = np.array([(date(POOL_YEAR, 1, 1).toordinal() + d) % 7 for d in range(365)])
    out = np.empty(n_days, dtype=int)
    for d in range(n_days):
        best = None
        for k in range(0, 4):
            for cand in (d - k, d + k):
                if 0 <= cand < 365 and wd_p[cand] == wd_y[d]:
                    best = cand
                    break
            if best is not None:
                break
        out[d] = best if best is not None else min(d, 364)
    return out


def pool_hourly(year: int) -> np.ndarray:
    """Pool profiles (n, H) re-aligned to the calendar of `year` (weekday-consistent)."""
    base = np.load(processed("households", f"pool_hourly_{POOL_YEAR}.npy"))
    if year == POOL_YEAR:
        return base
    days = day_alignment(year)
    b = base.reshape(base.shape[0], 365, 24)
    return b[:, days, :].reshape(base.shape[0], -1)


def assignment(n_customers: int, load_seed: int = 0) -> np.ndarray:
    """Pool index of each customer for a given load seed (seed 0 = reference assignment)."""
    n_pool = np.load(processed("households", f"pool_hourly_{POOL_YEAR}.npy"), mmap_mode="r").shape[0]
    rng = np.random.default_rng(1000 + load_seed)
    return rng.permutation(n_pool)[:n_customers]


def customer_base_load(year: int, n_customers: int, load_seed: int = 0) -> np.ndarray:
    """Hourly household base load (kW), shape (n_customers, H)."""
    return pool_hourly(year)[assignment(n_customers, load_seed)]


def ensure_pool(n: int = 160, workers: int = 10) -> None:
    if not processed("households", f"pool_hourly_{POOL_YEAR}.npy").exists():
        generate_pool(n=n, workers=workers)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings()
    generate_pool()
