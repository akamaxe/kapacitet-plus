"""Published envelopes must be safe: the claim the whole project rests on.

The envelopes are computed from a linear model and then verified with the nonlinear power
flow. Here that verification is repeated independently, on operating points chosen to be
hard (every customer wants to export close to its rating).

Every test runs on the tiny grid of tests/fixtures/grid_tiny, so it runs on every push, and on
the reference network too when it is built (the `grid_model` fixture in conftest.py).
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import on_tiny, stress_instance, violations_beyond_firm

from kapacitet.engine import doe
from kapacitet.engine.compliance import CONSTRAINT

# What `DoeResult.binding` says bound each critical hour, by name.
CODE = {name: code for code, name in CONSTRAINT.items()}


@pytest.fixture(scope="module")
def instance(grid_model):
    """The stressed scenario of conftest.stress_instance: the last third of the customers are non-firm."""
    return stress_instance(grid_model)


@pytest.mark.parametrize("policy", ["maxmin", "prorata"])
def test_envelopes_verify_with_the_nonlinear_power_flow(grid_model, instance, policy, request):
    arrays, nonfirm, cap = instance
    r = doe.compute(grid_model, arrays, nonfirm, policy=policy)
    assert r.verified_violations == 0
    assert r.info["critical_hours"] > 0, "the test scenario must actually stress the grid"
    # The tiny grid is shaped so the stress reaches both limits: the voltage at the end of its
    # long feeder in some hours, the transformer in others. The reference network is
    # transformer-bound: the same stress never makes its voltage the tightest limit.
    bound = np.bincount(r.binding, minlength=len(CONSTRAINT))
    assert bound[CODE["transformer"]] > 0
    if on_tiny(request):
        assert bound[CODE["voltage"]] > 0, f"the stress must bind the voltage too: {bound.tolist()}"
    # independent re-check: apply the published envelope in every hour and solve again
    assert violations_beyond_firm(grid_model, arrays, nonfirm, r) == 0


def test_envelopes_stay_within_what_the_customer_asked_for(grid_model, instance):
    arrays, nonfirm, cap = instance
    r = doe.compute(grid_model, arrays, nonfirm)
    desired = np.clip(arrays["p_net"][nonfirm], 0.0, None)
    env = r.envelope(arrays["p_net"].shape[1])
    assert (r.E >= -1e-9).all()
    assert (r.E <= desired[:, r.hours] + 1e-6).all()          # never more than asked
    assert (env[:, r.hours] <= cap[nonfirm][:, None] + 1e-6).all()
    outside = np.setdiff1d(np.arange(arrays["p_net"].shape[1]), r.hours)
    assert np.allclose(env[:, outside], cap[nonfirm][:, None])  # full capacity when nothing binds
    assert r.curtailed.min() >= 0.0


def test_firm_customers_are_never_curtailed(grid_model, instance):
    """Only non-firm customers appear in the result; firm ones keep their full export."""
    arrays, nonfirm, _ = instance
    r = doe.compute(grid_model, arrays, nonfirm)
    assert set(r.nonfirm.tolist()) == set(nonfirm.tolist())
    assert r.curtailed.shape[0] == len(nonfirm)


def test_more_customers_means_more_curtailment(grid_model, instance):
    """Sanity of the queue: adding non-firm customers cannot reduce the total curtailment."""
    arrays, nonfirm, _ = instance
    few = nonfirm[: len(nonfirm) // 2]
    z_few = doe.compute(grid_model, arrays, few).curtailed.sum()
    z_all = doe.compute(grid_model, arrays, nonfirm).curtailed.sum()
    assert z_all >= z_few - 1e-6
