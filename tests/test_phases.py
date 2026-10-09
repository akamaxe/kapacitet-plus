"""Phase assignment: the rule has to be deterministic, honest about the limit, and the
round-robin case has to be measurably kinder to the grid than the random one."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import needs_grid

from kapacitet.config import A
from kapacitet.engine.phases import THREE_PHASE, phase_counts, phase_of, worst_phase_share


def customers(n: int = 30) -> pd.DataFrame:
    """Two feeders of houses at increasing distance from the substation."""
    half = n // 2
    return pd.DataFrame({
        "cid": np.arange(n),
        "feeder": np.array([0] * (n - half) + [1] * half),
        "dist_m": np.concatenate([np.linspace(20.0, 200.0, n - half),
                                  np.linspace(20.0, 200.0, half)]),
    })


def test_without_pv_everything_is_three_phase():
    assert (phase_of(customers(), None) == THREE_PHASE).all()


def test_the_limit_decides_single_or_three_phase():
    c = customers(6)
    limit = A("pv_single_phase_max_kva")
    inv = np.array([limit - 1.0, limit, limit + 0.01, 10.0, 2.0, 0.5])
    phase = phase_of(c, inv, rule="round_robin")
    assert (phase[[0, 1, 4, 5]] >= 0).all(), "at or below the limit is single-phase"
    assert (phase[[2, 3]] == THREE_PHASE).all(), "above the limit is three-phase"


def test_round_robin_spreads_and_is_deterministic():
    c = customers(30)
    inv = np.full(30, 3.0)
    first = phase_of(c, inv, rule="round_robin")
    assert (first == phase_of(c, inv, rule="round_robin")).all()
    counts = phase_counts(first)
    assert counts["a"] == counts["b"] == counts["c"] == 10
    assert worst_phase_share(first) == pytest.approx(1 / 3)


def test_random_is_reproducible_and_less_balanced():
    c = customers(30)
    inv = np.full(30, 3.0)
    r1 = phase_of(c, inv, rule="random", seed=7)
    assert (r1 == phase_of(c, inv, rule="random", seed=7)).all()
    assert not (r1 == phase_of(c, inv, rule="random", seed=8)).all()
    assert worst_phase_share(r1) > worst_phase_share(phase_of(c, inv, rule="round_robin"))


def test_unknown_rule_is_rejected():
    with pytest.raises(ValueError, match="unknown phase allocation"):
        phase_of(customers(3), np.full(3, 3.0), rule="alphabetical")


@needs_grid
def test_allocation_rule_changes_the_unbalance_on_the_real_grid():
    """Round-robin against random on Smedslätten, through the unbalanced power flow."""
    import pandapower as pp

    from kapacitet.engine.powerflow3ph import PowerFlow3Ph
    from kapacitet.grid.builder import load_net, load_tables

    net = load_net()
    net.ext_grid["s_sc_max_mva"] = 1e7
    cust = load_tables()[2]
    model = PowerFlow3Ph(net, cust["bus"].to_numpy())

    inv = np.full(len(cust), 3.5)                       # every villa below the limit
    p_kw = np.full((len(cust), 1), 3.5)                 # all exporting at once
    unbalance = {}
    for rule in ("round_robin", "random"):
        phase = phase_of(cust, inv, rule=rule, seed=1)
        unbalance[rule] = model.solve(p_kw, None, phase, 1.0).unbalance_pct.max()

    assert unbalance["round_robin"] < unbalance["random"], "spreading the phases must help"
    assert unbalance["random"] < 2.0, "EN 50160 allows 2 % unbalance"
