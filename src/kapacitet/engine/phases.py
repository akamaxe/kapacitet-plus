"""Which phase each connection sits on.

A Swedish villa has a three-phase service, so in this model the household load and the EV
wallbox are three-phase and contribute no unbalance. Rooftop PV is where unbalance comes
from: an inverter at or below the single-phase infeed limit is normally wired to one phase,
and once a street fills up with them the three phases stop carrying the same thing.

Phases are assigned here rather than in the network builder, so that changing the rule or
the limit costs nothing and needs no rebuild. A phase the DSO recorded wins over the rule: a
single-phase service (`customers.phase`) has everything on its phase, and an inverter's phase
in equipment.csv (`existing_pv_phase`) holds for it, three-phase or single (a single one as
long as the inverter is within the single-phase limit, since a larger one cannot be).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import A

THREE_PHASE = -1
PHASE_NAMES = ("a", "b", "c")


def _install_order(customers: pd.DataFrame) -> np.ndarray:
    """Positions in the order an installer would work: along each feeder, outwards."""
    keys = ["feeder", "dist_m", "cid"]
    present = [k for k in keys if k in customers.columns]
    return customers.sort_values(present).index.to_numpy()


UNRECORDED = -2
_LABELS = {"a": 0, "b": 1, "c": 2, "l1": 0, "l2": 1, "l3": 2, "abc": THREE_PHASE, "3": THREE_PHASE,
           "l1l2l3": THREE_PHASE, "three": THREE_PHASE}


def _recorded(customers: pd.DataFrame, column: str, three: bool) -> np.ndarray:
    """A recorded phase per customer: 0, 1 or 2, THREE_PHASE when `three` allows it, else UNRECORDED."""
    if column not in customers:
        return np.full(len(customers), UNRECORDED, dtype=int)
    labels = customers[column].astype("string").fillna("").str.strip().str.lower().str.replace(" ", "")
    out = np.array([_LABELS.get(x, UNRECORDED) for x in labels], dtype=int)
    if not three:
        out[out == THREE_PHASE] = UNRECORDED
    return out


def phase_of(customers: pd.DataFrame, inverter_kw: np.ndarray | None = None,
             rule: str | None = None, seed: int = 0) -> np.ndarray:
    """Phase index per customer: 0, 1 or 2 for single-phase, THREE_PHASE for the rest.

    `inverter_kw` is the AC rating of the customer's PV. Without it every connection is
    treated as three-phase, which is what the balanced model assumes, except a service the
    DSO recorded as single-phase. The recorded phases come first (the module's docstring);
    the rule places only the single-phase inverters nothing was recorded for.
    """
    n = len(customers)
    phase = np.full(n, THREE_PHASE, dtype=int)
    service = _recorded(customers, "phase", three=False)
    on_service = service != UNRECORDED
    phase[on_service] = service[on_service]
    if inverter_kw is None:
        return phase

    limit = A("pv_single_phase_max_kva")
    single = np.asarray(inverter_kw, dtype=float) <= limit
    pv = _recorded(customers, "existing_pv_phase", three=True)
    keep = ~on_service & ((pv == THREE_PHASE) | ((pv != UNRECORDED) & single))
    phase[keep] = pv[keep]
    single &= ~on_service & ~keep
    if not single.any():
        return phase

    rule = A("phase_allocation") if rule is None else rule
    order = _install_order(customers)
    order = order[single[order]]                     # keep the install order, single-phase only
    if rule == "round_robin":
        phase[order] = np.arange(len(order)) % 3
    elif rule == "random":
        phase[order] = np.random.default_rng(seed).integers(0, 3, size=len(order))
    else:
        raise ValueError(f"unknown phase allocation rule: {rule!r}")
    return phase


def phase_counts(phase: np.ndarray) -> dict[str, int]:
    """How many single-phase connections sit on each phase, plus the three-phase ones."""
    out = {name: int(np.count_nonzero(phase == i)) for i, name in enumerate(PHASE_NAMES)}
    out["three_phase"] = int(np.count_nonzero(phase == THREE_PHASE))
    return out


def worst_phase_share(phase: np.ndarray) -> float:
    """Share of the single-phase connections sitting on the busiest phase.

    One third is perfectly spread, one is everything on one phase. A quick scalar for how
    unbalanced an allocation is before any power flow is run.
    """
    counts = np.array([np.count_nonzero(phase == i) for i in range(3)], dtype=float)
    total = counts.sum()
    return float(counts.max() / total) if total else 1.0 / 3.0
