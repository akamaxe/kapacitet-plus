"""Published envelopes must be safe: the claim the whole project rests on.

The envelopes are computed from a linear model and then verified with the nonlinear power
flow. Here that verification is repeated independently, on operating points chosen to be
hard (every customer wants to export close to its rating).

Every test runs on the tiny grid of tests/fixtures/grid_tiny, so it runs on every push, and on
the reference network too when it is built (the `grid_model` fixture in conftest.py).
"""

from __future__ import annotations

import copy

import numpy as np
import pytest
from conftest import on_tiny, stress_instance, violations_beyond_firm

from kapacitet.dso import POLICIES
from kapacitet.engine import doe
from kapacitet.engine.compliance import CONSTRAINT

# What `DoeResult.binding` says bound each critical hour, by name.
CODE = {name: code for code, name in CONSTRAINT.items()}


@pytest.fixture(scope="module")
def instance(grid_model):
    """The stressed scenario of conftest.stress_instance: the last third of the customers are non-firm."""
    return stress_instance(grid_model)


@pytest.mark.parametrize("policy", POLICIES)
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
    # the full-year check doe.compute ran, and the same check written out from scratch
    assert r.verification == "full" and r.full_year_violations == 0
    assert r.info["full_year"]["hours_checked"] > r.info["critical_hours"]
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


# ------------------------------------------------------------------ the full-year check

def _by_hand(model, arrays: dict, r, vm_slack=None) -> int:
    """`doe.verify_full_year` written out again from scratch (conftest.violations_beyond_firm)."""
    return violations_beyond_firm(model, arrays, r.nonfirm, r, vm_slack)


def _losing_candidates(model):
    """A copy of `model` whose export screen drops every other candidate hour, as a wrong screen would."""
    broken = copy.copy(model)
    screen = model.screen_export
    broken.screen_export = lambda p, q, *args, **kwargs: screen(p, q, *args, **kwargs)[::2]
    return broken


def test_the_full_year_check_catches_the_hours_a_screen_missed(grid_model, instance):
    """The critical hours come from the screen, so checking only them proves nothing about the hours
    it missed: with a screen that loses candidates the critical-hour check still reads 0."""
    arrays, nonfirm, _ = instance
    broken = _losing_candidates(grid_model)
    r = doe.compute(broken, arrays, nonfirm)
    assert r.verified_violations == 0, "the critical hours it kept do verify"
    assert r.full_year_violations > 0, "the hours it lost do not, and the full-year check must say so"
    assert r.full_year_violations == _by_hand(grid_model, arrays, r)
    quick = doe.compute(broken, arrays, nonfirm, verify="critical")
    assert quick.verification == "critical" and quick.full_year_violations is None
    assert np.array_equal(quick.E, r.E), "the check reports; it does not change the envelopes"
    safe = doe.compute(grid_model, arrays, nonfirm)
    assert safe.full_year_violations == _by_hand(grid_model, arrays, safe) == 0


def test_envelopes_for_another_mv_voltage_verify_at_that_voltage(grid_model, instance):
    """doe.compute(vm_slack=1.06) on a model built for 1.00 pu: the screen used to look at 1.00 pu,
    so hours critical only at 1.06 pu were handed the inverter rating."""
    arrays, nonfirm, _ = instance
    r = doe.compute(grid_model, arrays, nonfirm, vm_slack=1.06)
    assert r.verified_violations == 0 and r.full_year_violations == 0
    assert _by_hand(grid_model, arrays, r, vm_slack=1.06) == 0
    at_one = doe.compute(grid_model, arrays, nonfirm, verify="critical")
    assert r.info["critical_hours"] > at_one.info["critical_hours"], "1.06 pu must make more hours critical"


def test_an_hour_without_an_answer_is_never_put_down_to_the_firm_state(grid_model, instance):
    """In hour 0 the firm customers draw twenty times their share of the transformer: the firm state
    has no power-flow solution. That is no excuse: the hour gets no envelope and counts as a
    violation, in the critical-hour check and in the full-year check alike."""
    arrays, nonfirm, _ = instance
    p = arrays["p_net"].copy()
    firm = np.setdiff1d(np.arange(grid_model.C), nonfirm)
    p[firm, 0] = -20.0 * grid_model.trafo_s_max_kw / grid_model.C
    stressed = {"p_net": p, "q": arrays["q"]}
    p_firm = p[:, [0]].copy()
    p_firm[nonfirm] = np.minimum(p_firm[nonfirm], 0.0)
    assert not grid_model.solve(p_firm, arrays["q"][:, [0]]).converged[0]

    critical = grid_model.export_critical_hours
    forced = copy.copy(grid_model)               # make hour 0 critical whatever the screen says
    forced.export_critical_hours = lambda pp, qq, vs=None: np.union1d(critical(pp, qq, vs), [0]).astype(int)
    r = doe.compute(forced, stressed, nonfirm)
    assert r.hours[0] == 0 and (r.E[:, 0] == 0).all()
    assert r.verified_violations >= 1
    assert r.full_year_violations >= 1 and r.info["full_year"]["nonconverged_hours"] >= 1
