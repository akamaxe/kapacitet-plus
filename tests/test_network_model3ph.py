"""The unbalanced network model, and above all its screens.

The screens are the only place where the unbalanced engine trades exactness for speed, so
they get the same treatment as the balanced ones: random adoption patterns, and the rule
that every hour the full nonlinear flow calls a violation has to be in the candidate set.
The worst case for a screen is unbalance, so the hardest variant, every connection on one
phase chosen at random, is the one that is hunted over.
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import needs_grid

from kapacitet.engine.netmodel import get_model
from kapacitet.engine.netmodel3ph import NetModel3Ph
from kapacitet.engine.phases import THREE_PHASE, phase_of

pytestmark = needs_grid


@pytest.fixture(scope="module")
def m3():
    """Real inverter sizes: how the model actually runs."""
    return NetModel3Ph()


@pytest.fixture(scope="module")
def m3_single():
    """Every connection single-phase and randomly placed: the hardest case for a screen."""
    model = NetModel3Ph()
    phase = phase_of(model.cust, np.zeros(model.C), rule="random", seed=3)
    return NetModel3Ph(phase=phase)


def test_three_phase_customers_make_it_balanced(m3):
    """With the modelled inverter sizes almost everything is a three-phase connection."""
    three = int(np.count_nonzero(m3.phase == THREE_PHASE))
    assert three > 0.8 * m3.C
    p = np.full((m3.C, 1), 4.0)
    assert m3.unbalance(m3.solve(p, np.zeros_like(p))).worst_pct[0] < 0.5


def test_it_agrees_with_the_balanced_model_when_nothing_is_single_phase(m3):
    """Forcing every connection three-phase has to reproduce the balanced model."""
    balanced = get_model()
    model = NetModel3Ph(phase=np.full(m3.C, THREE_PHASE))
    rng = np.random.default_rng(9)
    p = rng.uniform(-5.0, 7.0, size=(model.C, 3))
    q = -0.05 * p
    v3 = model.violations(model.solve(p, q), p)
    vb = balanced.violations(balanced.solve(p, q), p)
    assert np.abs(v3.vmax - vb.vmax).max() < 1e-6
    assert np.abs(v3.vmin - vb.vmin).max() < 1e-6
    assert np.abs(v3.max_line_loading - vb.max_line_loading).max() < 1e-4
    assert np.abs(v3.trafo_loading - vb.trafo_loading).max() < 0.5


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_export_screen_never_misses_a_violation(m3_single, seed):
    rng = np.random.default_rng(seed)
    H = 300
    p = rng.uniform(0.0, 1.0, size=(m3_single.C, H)) * rng.uniform(2.0, 11.0, size=(m3_single.C, 1))
    q = -0.05 * p
    cand = set(m3_single.screen_export(p, q).tolist())
    viol = m3_single.violations(m3_single.solve(p, q), p).export_side
    assert set(np.flatnonzero(viol).tolist()) <= cand


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_import_screen_never_misses_a_violation(m3_single, seed):
    rng = np.random.default_rng(100 + seed)
    H = 300
    p = -rng.uniform(0.0, 1.0, size=(m3_single.C, H)) * rng.uniform(2.0, 12.0, size=(m3_single.C, 1))
    q = 0.1 * p
    cand = set(m3_single.screen_import(p, q).tolist())
    viol = m3_single.violations(m3_single.solve(p, q), p).import_side
    assert set(np.flatnonzero(viol).tolist()) <= cand


def test_the_screen_is_worth_running(m3_single):
    """A screen that returns everything is correct and useless. This one has to cut.

    Two regimes, because near the transformer rating every hour deserves a look and
    flagging them all is the right answer: around 180 kW of export the screen clears the
    year outright, and around 240 kW it still discards most of it.
    """
    rng = np.random.default_rng(5)
    quiet = rng.uniform(0.0, 3.0, size=(m3_single.C, 200))
    assert len(m3_single.screen_export(quiet, np.zeros_like(quiet))) == 0

    busy = rng.uniform(0.0, 4.0, size=(m3_single.C, 300))
    assert len(m3_single.screen_export(busy, np.zeros_like(busy))) < 0.7 * busy.shape[1]


def test_the_balanced_model_misses_violations_the_unbalanced_one_finds(m3_single):
    """The point of all this: single-phase PV breaks limits the balanced model cannot see."""
    rng = np.random.default_rng(5)
    p = rng.uniform(0.0, 6.0, size=(m3_single.C, 300))
    q = np.zeros_like(p)
    unbalanced = m3_single.violations(m3_single.solve(p, q), p).export_side.sum()
    balanced = get_model()
    missed = balanced.violations(balanced.solve(p, q), p).export_side.sum()
    assert unbalanced > 10 * max(missed, 1), "the gap is the reason the unbalanced model exists"


def test_voltage_sensitivities_are_conservative(m3_single):
    """The linear voltage estimate may overshoot the nonlinear result, never undershoot."""
    rng = np.random.default_rng(11)
    p = rng.uniform(0.0, 5.0, size=(m3_single.C, 40))
    v_lin = m3_single.v_noload[:, None] + m3_single.dv_dp @ p
    v_true = m3_single.solve(p, np.zeros_like(p), flows=False).vm[m3_single.check_rows]
    assert (v_lin.max(axis=0) >= v_true.max(axis=0) - 1e-9).all()


def test_unbalance_and_neutral_are_reported(m3_single):
    p = np.full((m3_single.C, 1), 5.0)
    u = m3_single.unbalance(m3_single.solve(p, np.zeros_like(p)))
    assert 0.0 < u.worst_pct[0] < m3_single.unbalance_limit
    assert u.neutral_a[0] > 1.0
    assert not u.over_limit[0]
