"""Envelopes issued before the fact.

The published envelopes know the hour they apply to. These tests are about the one property
that lets an envelope be issued a day early and still be safe: the planning state has to
dominate what happens — at least as much generation, at most as much load. The tests check
the domination itself, the safety that follows from it, and the failure that follows from
dropping it.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from conftest import needs_results

from kapacitet.config import A, settings
from kapacitet.engine import forecast
from kapacitet.engine.netmodel import get_model
from kapacitet.engine.run import background_masks
from kapacitet.engine.scenario import Scenario, adoption_order
from kapacitet.paths import processed

pytestmark = needs_results


@pytest.fixture(scope="module")
def case():
    """The published showcase deployment: the one the compliance figures describe."""
    show = json.loads(processed("results", "showcase.json").read_text(encoding="utf-8"))
    model = get_model()
    pv = np.zeros(model.C, dtype=bool)
    pv[show["pv_cids"]] = True
    hp, ev = background_masks(model.C)
    actual = Scenario(year=show["year"], pv=pv, hp=hp, ev=ev).arrays()
    return model, show, pv, hp, ev, actual


def test_the_planning_state_dominates_what_happened(case):
    """The property everything else rests on, on the year the figures describe.

    Eleven years of history is a bound on the record, not a guarantee, so the exceptions are
    counted rather than asserted away. They are individual houses: what the transformer sees
    is the neighbourhood total, and that is dominated in every hour of the year.
    """
    _, show, pv, hp, ev, actual = case
    plan = forecast.climatological(show["year"], pv, hp, ev)
    agg_plan, agg_actual = plan["p_net"].sum(axis=0), actual["p_net"].sum(axis=0)
    assert (agg_plan >= agg_actual - 1e-3).all(), "the aggregate is what binds on this grid"
    loose = int((plan["p_net"] < actual["p_net"] - 1e-4).sum())
    assert loose / plan["p_net"].size < 1e-3, f"{loose} customer-hours not dominated"


def test_the_exceedances_are_counted_on_both_sides(case):
    _, show, _, hp, ev, _ = case
    audit = forecast.exceedance_audit(show["year"], hp, ev)
    assert audit["pv_bound_over_measured_energy"] > 1.0, "a bound above what the year produced"
    assert audit["load_bound_under_measured_energy"] < 1.0, "a floor below what it consumed"
    assert audit["pv_aggregate_hours_over"] <= 5, audit
    assert audit["pv_worst_aggregate_ratio"] < 1.1, audit
    assert audit["load_aggregate_hours_under"] == 0, audit


def test_a_wider_calendar_window_is_a_tighter_bound_on_the_record(case):
    """The window is the whole reason the bound works: one calendar hour has one sample per
    weather year, which bounds nothing."""
    _, show, _, hp, ev, _ = case
    narrow = forecast.exceedance_audit(show["year"], hp, ev, window_days=0)
    wide = forecast.exceedance_audit(show["year"], hp, ev, window_days=forecast.WINDOW_DAYS)
    assert narrow["pv_aggregate_hours_over"] > wide["pv_aggregate_hours_over"]
    assert wide["pv_bound_over_measured_energy"] > narrow["pv_bound_over_measured_energy"]
    assert wide["load_bound_under_measured_energy"] < narrow["load_bound_under_measured_energy"]


def test_the_bound_still_follows_the_calendar(case):
    """A bound that ignored the clock would be the static worst case, which carries nobody."""
    _, show, _, _, _, _ = case
    bound = forecast.pv_upper_bound(show["year"])
    assert (bound >= 0).all()
    assert bound.max() <= 1.0 / A("pv_dc_ac_ratio") + 1e-6, "never above the inverter rating"
    hourly = bound.mean(axis=0)
    assert hourly.min() == 0.0, "night is still night"
    assert hourly.max() > 0.5 / A("pv_dc_ac_ratio"), "and midsummer noon is still noon"


def test_envelopes_issued_against_the_bound_keep_the_limits(case):
    """The claim the scheme lives on, checked with the nonlinear flow on the real year."""
    model, show, pv, hp, ev, actual = case
    nonfirm = np.array(show["nonfirm_cids"], dtype=int)
    r = forecast.issue(model, forecast.climatological(show["year"], pv, hp, ev), actual, nonfirm,
                       "climatological")
    assert r.violation_hours == 0, "a dominating state leaves no hour unguarded"
    assert r.curtailed_kwh > 0, "and it is not free"
    assert 0 < r.curtailed_share_pct < 100


def test_a_point_forecast_does_not(case):
    """Documented as a failure because it is the reason for all of the above.

    Persistence looks cheaper than the published envelopes — it curtails less — and that is
    exactly the symptom: hours drop out of the critical set, so no envelope is issued and the
    customer runs to its inverter rating.
    """
    model, show, pv, hp, ev, actual = case
    nonfirm = np.array(show["nonfirm_cids"], dtype=int)
    r = forecast.issue(model, forecast.persistence(show["year"], pv, hp, ev), actual, nonfirm,
                       "persistence")
    assert r.violation_hours > 0
    err = forecast.forecast_error(actual, forecast.persistence(show["year"], pv, hp, ev))
    assert err["p95_underprediction_kw"] > 0, "it under-predicts export, which is the dangerous way"


def test_perfect_foresight_reproduces_the_published_curtailment(case):
    """This module recomputes the published search; if the two drift the comparison is void."""
    from kapacitet.engine import hosting, run

    model, _, _, hp, ev, _ = case
    years = (settings().reference_year,)
    order = adoption_order(0, model.C)
    ms = hosting.static_adopters(model, order, hp, ev)
    k = min(ms + 30, model.C)
    mine = forecast.multiyear(model, order, ms, k, years, hp, ev, "foresight")
    theirs = run.curtailment(order, ms, k, years, hp, ev, method="doe")
    assert mine["z_pct"] == pytest.approx(theirs["z_pct"], rel=1e-6, abs=1e-9)


def test_the_leap_year_bound_covers_the_leap_day():
    leap = next((y for y in settings().weather_years if y % 4 == 0), None)
    if leap is None:
        pytest.skip("no leap year among the weather years")
    from kapacitet.timeindex import year_index

    idx = year_index(leap)
    bound = forecast.pv_upper_bound(leap)
    assert bound.shape[1] == len(idx) == 8784
    feb29 = np.asarray((idx.month == 2) & (idx.day == 29))
    assert feb29.sum() == 24
    assert np.isfinite(bound[:, feb29]).all()


@needs_results
def test_the_published_ladder_is_ordered(results):
    """The forecast can only cost capacity, never add it."""
    try:
        f = results("foresight")
    except FileNotFoundError:
        pytest.skip("no foresight.json (run: python -m kapacitet compute --stage extra)")
    kw = f["median_kw"]
    assert kw["static"] < kw["climatological"] <= kw["doe"]
    assert f["foresight_premium_kw"] == pytest.approx(kw["doe"] - kw["climatological"], abs=1e-6)
    assert f["violation_hours"] == 0, "every rung of the ladder is verified, not just the published one"
