"""Property tests of the four allocation rules of `doe.allocate`.

Hypothesis draws the linear problem an hour's allocation solves: non-negative sensitivities A
(rows: the limits, columns: the customers; a 0/1 incidence like the cables' or the
transformer's, or kW-per-kW coefficients like the voltage rows'), the verified capacity b of each
row (negative where a limit is already broken, which the rules read as zero) and each customer's
cap. Whatever the rule:

- no customer gets less than nothing or more than its cap;
- no limit gets more than its verified capacity (A E <= max(b, 0));
- more capacity never makes the rule's own aim worse: the total for `maxtotal`, the sorted
  allocation (leximin) for `maxmin`, and for `prorata` the same on the shares of the weights,
  and the sum of logs for `propfair`. With a single limit (the transformer alone) every customer's
  allocation is monotone for `maxmin` and `prorata`.

Several limits can lower one customer's max-min allocation when capacity is added (more room on
a shared limit lets a neighbour grow and freeze it earlier), so per-customer monotonicity is
asserted only where it holds: one limit.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import examples

POLICIES = ("maxmin", "prorata", "propfair", "maxtotal")
FAST = ("maxmin", "prorata")
TOL = 1e-6


@st.composite
def problems(draw, max_rows: int = 4, max_customers: int = 7, max_hours: int = 3, rows: int | None = None):
    K = rows or draw(st.integers(1, max_rows))
    N = draw(st.integers(1, max_customers))
    H = draw(st.integers(1, max_hours))
    coef = st.one_of(st.just(0.0), st.just(1.0), st.floats(0.01, 3.0))
    A = np.array(draw(st.lists(st.lists(coef, min_size=N, max_size=N), min_size=K, max_size=K)))
    b = np.array(draw(st.lists(st.lists(st.one_of(st.floats(-5.0, 0.0), st.floats(0.0, 60.0)),
                                        min_size=H, max_size=H), min_size=K, max_size=K)))
    ub = np.array(draw(st.lists(st.lists(st.one_of(st.just(0.0), st.floats(0.0, 20.0)),
                                         min_size=H, max_size=H), min_size=N, max_size=N)))
    w = np.array(draw(st.lists(st.one_of(st.just(0.0), st.floats(0.5, 20.0)), min_size=N, max_size=N)))
    more = np.array(draw(st.lists(st.lists(st.floats(0.0, 30.0), min_size=H, max_size=H),
                                  min_size=K, max_size=K)))
    return A, b, ub, w, more


def _allocate(policy, A, b, ub, w):
    from kapacitet.engine import doe

    return doe.allocate(policy, A, b, ub, weights=w)


def _scale(*arrays) -> float:
    return max(1.0, *(float(np.abs(a).max()) for a in arrays if np.size(a)))


def _assert_feasible(E, A, b, ub) -> None:
    tol = TOL * _scale(b, ub)
    assert E.shape == ub.shape
    assert np.isfinite(E).all()
    assert (E >= -tol).all(), "a customer got less than nothing"
    assert (E <= ub + tol).all(), "a customer got more than its cap"
    assert (A @ E <= np.maximum(b, 0.0) + tol * _scale(A)).all(), "a limit got more than its verified capacity"


def _leximin_not_worse(after: np.ndarray, before: np.ndarray, tol: float) -> bool:
    """`after` sorted is lexicographically at least `before` sorted, to `tol`."""
    for x, y in zip(np.sort(after), np.sort(before)):
        if x > y + tol:
            return True
        if x < y - tol:
            return False
    return True


def _shares(E, w):
    return E / np.maximum(w, 1e-6)[:, None]


def _assert_monotone(policy, A, b, ub, w, E0, E1) -> None:
    """E1 is the allocation with more capacity than E0's."""
    tol = 1e-5 * _scale(b, ub, E0, E1)
    if policy == "maxtotal":
        assert E1.sum(axis=0).min() >= 0 and (E1.sum(axis=0) >= E0.sum(axis=0) - tol).all(), \
            "more capacity gave a smaller total"
    elif policy == "propfair":
        live = ub > 1e-6
        f0 = np.where(live, np.log(E0 + 1e-3), 0.0).sum(axis=0)
        f1 = np.where(live, np.log(E1 + 1e-3), 0.0).sum(axis=0)
        assert (f1 >= f0 - 1e-4 * np.maximum(1.0, np.abs(f0))).all(), "more capacity lowered the sum of logs"
    else:
        x0, x1 = (E0, E1) if policy == "maxmin" else (_shares(E0, w), _shares(E1, w))
        live = ub > 1e-9
        for h in range(E0.shape[1]):
            m = live[:, h]
            assert _leximin_not_worse(x1[m, h], x0[m, h], tol), \
                f"more capacity made the max-min allocation of hour {h} worse"


@settings(max_examples=examples(60))
@given(problem=problems(), policy=st.sampled_from(FAST))
def test_the_max_min_rules_never_exceed_a_cap_or_a_limit_and_never_lose_from_more_capacity(problem, policy):
    A, b, ub, w, more = problem
    E0 = _allocate(policy, A, b, ub, w)
    _assert_feasible(E0, A, b, ub)
    E1 = _allocate(policy, A, b + more, ub, w)
    _assert_feasible(E1, A, b + more, ub)
    _assert_monotone(policy, A, b, ub, w, E0, E1)


@settings(max_examples=examples(15))
@given(problem=problems(max_rows=3, max_customers=5, max_hours=2), policy=st.sampled_from(("propfair", "maxtotal")))
def test_the_optimising_rules_never_exceed_a_cap_or_a_limit_and_never_lose_from_more_capacity(problem, policy):
    A, b, ub, w, more = problem
    E0 = _allocate(policy, A, b, ub, w)
    _assert_feasible(E0, A, b, ub)
    E1 = _allocate(policy, A, b + more, ub, w)
    _assert_feasible(E1, A, b + more, ub)
    _assert_monotone(policy, A, b, ub, w, E0, E1)


@settings(max_examples=examples(40))
@given(problem=problems(rows=1), policy=st.sampled_from(FAST))
def test_with_one_limit_every_customer_gains_from_more_capacity(problem, policy):
    A, b, ub, w, more = problem
    E0 = _allocate(policy, A, b, ub, w)
    E1 = _allocate(policy, A, b + more, ub, w)
    tol = 1e-6 * _scale(b, ub, E1)
    assert (E1 >= E0 - tol).all(), "with a single limit a customer lost from more capacity"
