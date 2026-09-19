"""Published envelopes must be safe: the claim the whole project rests on.

The envelopes are computed from a linear model and then verified with the nonlinear power
flow. Here that verification is repeated independently, on operating points chosen to be
hard (every customer wants to export close to its rating).
"""

from __future__ import annotations

import numpy as np
import pytest

from conftest import needs_grid
from kapacitet.engine import doe

pytestmark = needs_grid


@pytest.fixture(scope="module")
def instance(model):
    """A stressed scenario: the last third of the customers are non-firm."""
    rng = np.random.default_rng(42)
    H = 150
    cap = doe.customer_caps()
    want = cap[:, None] * rng.uniform(0.25, 1.0, size=(model.C, H))
    load = rng.uniform(0.2, 2.5, size=(model.C, H))
    p_net = want - load
    q = -0.05 * np.clip(p_net, 0.0, None)
    nonfirm = np.arange(model.C)[int(model.C * 2 / 3):]
    return {"p_net": p_net, "q": q}, nonfirm, cap


@pytest.mark.parametrize("policy", ["maxmin", "prorata"])
def test_envelopes_verify_with_the_nonlinear_power_flow(model, instance, policy):
    arrays, nonfirm, cap = instance
    r = doe.compute(model, arrays, nonfirm, policy=policy)
    assert r.verified_violations == 0
    assert r.info["critical_hours"] > 0, "the test scenario must actually stress the grid"

    # independent re-check: apply the published envelope in every hour and solve again
    p = arrays["p_net"].copy()
    env = r.envelope(p.shape[1])
    p[nonfirm] = np.minimum(p[nonfirm], env)
    viol = model.violations(model.solve(p, arrays["q"]), p).export_side
    # the firm state alone may violate in some hours; envelopes cannot fix those
    p_firm = arrays["p_net"].copy()
    p_firm[nonfirm] = np.minimum(p_firm[nonfirm], 0.0)
    firm_bad = model.violations(model.solve(p_firm, arrays["q"]), p_firm).export_side
    assert not (viol & ~firm_bad).any()


def test_envelopes_stay_within_what_the_customer_asked_for(model, instance):
    arrays, nonfirm, cap = instance
    r = doe.compute(model, arrays, nonfirm)
    desired = np.clip(arrays["p_net"][nonfirm], 0.0, None)
    env = r.envelope(arrays["p_net"].shape[1])
    assert (r.E >= -1e-9).all()
    assert (r.E <= desired[:, r.hours] + 1e-6).all()          # never more than asked
    assert (env[:, r.hours] <= cap[nonfirm][:, None] + 1e-6).all()
    outside = np.setdiff1d(np.arange(arrays["p_net"].shape[1]), r.hours)
    assert np.allclose(env[:, outside], cap[nonfirm][:, None])  # full capacity when nothing binds
    assert r.curtailed.min() >= 0.0


def test_firm_customers_are_never_curtailed(model, instance):
    """Only non-firm customers appear in the result; firm ones keep their full export."""
    arrays, nonfirm, _ = instance
    r = doe.compute(model, arrays, nonfirm)
    assert set(r.nonfirm.tolist()) == set(nonfirm.tolist())
    assert r.curtailed.shape[0] == len(nonfirm)


def test_more_customers_means_more_curtailment(model, instance):
    """Sanity of the queue: adding non-firm customers cannot reduce the total curtailment."""
    arrays, nonfirm, _ = instance
    few = nonfirm[: len(nonfirm) // 2]
    z_few = doe.compute(model, arrays, few).curtailed.sum()
    z_all = doe.compute(model, arrays, nonfirm).curtailed.sum()
    assert z_all >= z_few - 1e-6
