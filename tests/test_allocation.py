"""Envelope allocation: the fast water-filling must equal a textbook lexicographic max-min LP.

`waterfill` solves max-min fairness by progressive filling, vectorised over hours. Here it is
checked against a slow reference built from a sequence of linear programs, plus the bottleneck
certificate that characterises a max-min fair allocation.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.optimize import linprog

from kapacitet.engine.doe import allocate, waterfill

TOL = 1e-6


def lexmaxmin_lp(A: np.ndarray, b: np.ndarray, ub: np.ndarray) -> np.ndarray:
    """Reference max-min fair allocation: repeatedly lift the worst-off, then freeze it."""
    K, N = A.shape
    x = np.zeros(N)
    frozen = np.zeros(N, dtype=bool)
    for _ in range(N + 1):
        free = ~frozen
        if not free.any():
            break
        # maximise t subject to A x <= b, x_frozen fixed, x_free >= t, x <= ub
        nv = N + 1                                   # variables: x (N), t
        c = np.zeros(nv)
        c[-1] = -1.0
        A_ub = np.zeros((K + int(free.sum()), nv))
        b_ub = np.zeros(K + int(free.sum()))
        A_ub[:K, :N] = A
        b_ub[:K] = b
        for r, i in enumerate(np.flatnonzero(free)):  # t - x_i <= 0
            A_ub[K + r, i] = -1.0
            A_ub[K + r, -1] = 1.0
        bounds = [(x[i], x[i]) if frozen[i] else (0.0, ub[i]) for i in range(N)] + [(0.0, None)]
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        assert res.status == 0, res.message
        t = float(res.x[-1])
        # freeze everyone who cannot go above t without pushing someone else below it
        for i in np.flatnonzero(free):
            c2 = np.zeros(nv)
            c2[i] = -1.0
            bounds2 = list(bounds)
            bounds2[-1] = (t, None)
            r2 = linprog(c2, A_ub=A_ub, b_ub=b_ub, bounds=bounds2, method="highs")
            if r2.status != 0 or float(r2.x[i]) <= t + TOL:
                x[i] = min(t, ub[i])
                frozen[i] = True
        if not frozen[free].any():                   # numerical stall: freeze all at t
            x[free] = np.minimum(t, ub[free])
            frozen[free] = True
    return x


def random_instance(rng, K=4, N=5):
    A = rng.uniform(0.0, 1.0, size=(K, N)) * (rng.random((K, N)) < 0.7)   # some zero coefficients
    A[0] = 1.0                                                            # a transformer-like row
    b = rng.uniform(0.5, 6.0, size=K)
    ub = rng.uniform(0.0, 4.0, size=N)
    return A, b, ub


@pytest.mark.parametrize("seed", range(12))
def test_waterfill_matches_lp_reference(seed):
    rng = np.random.default_rng(seed)
    A, b, ub = random_instance(rng)
    got = waterfill(A, b[:, None], ub[:, None])[:, 0]
    want = lexmaxmin_lp(A, b, ub)
    assert np.abs(got - want).max() < 1e-5


@pytest.mark.parametrize("seed", range(25))
def test_waterfill_is_feasible_and_has_a_bottleneck(seed):
    rng = np.random.default_rng(1000 + seed)
    A, b, ub = random_instance(rng, K=rng.integers(2, 6), N=rng.integers(2, 9))
    E = waterfill(A, b[:, None], ub[:, None])[:, 0]
    assert (E >= -TOL).all() and (E <= ub + TOL).all()
    assert (A @ E <= b + 1e-7).all()
    for i in range(len(E)):
        if E[i] >= ub[i] - TOL:
            continue                                   # limited by its own cap
        rows = np.flatnonzero(A[:, i] > 0)
        tight = [k for k in rows if A[k] @ E >= b[k] - 1e-7]
        assert tight, "an unsaturated customer must sit on a tight constraint"
        # on one of them it must be among the largest allocations (max-min certificate)
        assert any(E[i] >= E[A[k] > 0].max() - 1e-6 for k in tight)


def test_hours_are_independent():
    """Solving hours in a batch must give the same answer as solving them one by one."""
    rng = np.random.default_rng(3)
    A, _, _ = random_instance(rng)
    b = rng.uniform(0.5, 6.0, size=(A.shape[0], 12))
    ub = rng.uniform(0.0, 4.0, size=(A.shape[1], 12))
    batch = waterfill(A, b, ub)
    for h in range(b.shape[1]):
        one = waterfill(A, b[:, [h]], ub[:, [h]])[:, 0]
        assert np.abs(batch[:, h] - one).max() < 1e-9


def test_policies_trade_fairness_for_total():
    """max-total gives at least as much energy as max-min; pro-rata equalises shares."""
    rng = np.random.default_rng(5)
    A, b, ub = random_instance(rng, K=3, N=6)
    b2, ub2 = b[:, None], ub[:, None]
    caps = np.maximum(ub, 0.1)
    mm = allocate("maxmin", A, b2, ub2, weights=caps)[:, 0]
    tot = allocate("maxtotal", A, b2, ub2, weights=caps)[:, 0]
    pr = allocate("prorata", A, b2, ub2, weights=caps)[:, 0]
    assert tot.sum() >= mm.sum() - 1e-6
    for x in (mm, tot, pr):
        assert (A @ x <= b + 1e-6).all() and (x <= ub + 1e-6).all()
    # pro-rata: everyone not capped by its own bound gets the same share of its rating
    share = pr / caps
    free = pr < ub - TOL
    if free.sum() > 1:
        assert share[free].std() < 1e-6


def test_zero_headroom_gives_zero():
    A = np.ones((1, 3))
    E = waterfill(A, np.zeros((1, 1)), np.full((3, 1), 5.0))
    assert np.abs(E).max() < 1e-12
