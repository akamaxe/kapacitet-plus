"""Envelopes re-issued during the day with the measurements (engine/intraday.py).

On the tiny grid with its synthetic profiles, so CI runs them: the plan never reads ahead of
what was measured, the envelopes it issues hold in the real state, and with a gate every hour
and no delay its capacity sits between the day-ahead one and perfect foresight. The contract
test on results/intraday.json skips until the stage has run.
"""

from __future__ import annotations

import contextlib
import json

import numpy as np
import pytest
from conftest import synthetic_profiles, tiny_grid

from kapacitet.paths import processed

INTRADAY = "run: python -m kapacitet compute --stage extra --only intraday"


@pytest.fixture(scope="module")
def tiny(tiny_processed):
    with tiny_grid(tiny_processed) as folder:
        yield folder


@pytest.fixture(scope="module")
def setup(tiny):
    from kapacitet.engine import run

    model = run.model()
    hp, ev = run.background_masks(model.C)
    return model, hp, ev


@contextlib.contextmanager
def gates(hours, lag: int):
    """Other gates and another delay, as the tornado varies an input, put back after."""
    from kapacitet import cache
    from kapacitet.config import set_overrides

    set_overrides({"intraday_gates_h": list(hours), "intraday_measurement_lag_h": lag})
    try:
        yield
    finally:
        set_overrides(None)
        cache.clear_all()


def test_each_hour_is_planned_from_a_measurement_before_it():
    from kapacitet.engine.intraday import horizons

    h = horizons((6, 9), 1)
    assert (h[:6] == 0).all(), "before the first gate the day-ahead envelope holds"
    assert h[6] == 2 and h[8] == 4, "the gate at 6 sees 04:00 at the latest"
    assert h[9] == 2 and h[23] == 16
    every = horizons(tuple(range(24)), 0)
    assert (every == 1).all(), "a gate every hour with no delay sees the hour before"
    a = np.arange(48, dtype=np.float32)[None, :]
    from kapacitet.engine.intraday import measured
    m = measured(a, np.tile(h, 2))
    t = np.arange(48)
    ok = ~np.isnan(m[0])
    assert (m[0][ok] < t[ok]).all(), "a measurement is always of an earlier hour"
    assert np.isnan(m[0][:6]).all()


def test_the_change_tables_leave_the_replayed_year_out(setup, monkeypatch):
    """Changing the replayed year's own profiles leaves its tables of changes untouched, and
    changing the other year's moves them: the replay reads no history of the year it plays."""
    from kapacitet import cache
    from kapacitet.engine import intraday

    model, hp, ev = setup
    years = synthetic_profiles.YEARS
    y, other = years[-1], years[0]
    g, lag = intraday.gates_and_lag()
    base_pv = intraday.pv_rise(y, g, lag).copy()
    base_load = intraday.load_fall(y, hp, ev, g, lag).copy()
    real_pv = intraday.load_ac_per_kwp
    real_load = intraday.forecast._total_load

    def scaled(year_changed):
        monkeypatch.setattr(intraday, "load_ac_per_kwp",
                            lambda yy: real_pv(yy) * (3.0 if yy == year_changed else 1.0))
        monkeypatch.setattr(intraday.forecast, "_total_load",
                            lambda yy, a, b: real_load(yy, a, b) * (3.0 if yy == year_changed else 1.0))
        cache.clear_all()
        return intraday.pv_rise(y, g, lag), intraday.load_fall(y, hp, ev, g, lag)

    pv, load = scaled(y)
    np.testing.assert_array_equal(pv, base_pv)
    np.testing.assert_array_equal(load, base_load)
    pv, load = scaled(other)
    assert not np.allclose(np.nan_to_num(pv), np.nan_to_num(base_pv))
    assert not np.allclose(np.nan_to_num(load), np.nan_to_num(base_load))
    monkeypatch.undo()
    cache.clear_all()


def test_the_intraday_plan_never_assumes_less_than_the_day_ahead_one(setup):
    """Each gate can only tighten the day-ahead plan: no more solar, no less load."""
    from kapacitet.engine import forecast, intraday

    model, hp, ev = setup
    y = synthetic_profiles.YEARS[-1]
    pv = np.ones(model.C, dtype=bool)
    da = forecast.climatological(y, pv, hp, ev)
    st = intraday.planning_state(y, pv, hp, ev)
    assert (st["pv"] <= da["pv"] + 1e-6).all()
    assert (st["load"] >= da["load"] - 1e-6).all()
    assert np.isfinite(st["p_net"]).all()
    hod = intraday.horizons(*intraday.gates_and_lag())
    early = np.tile(hod == 0, st["pv"].shape[1] // 24)
    np.testing.assert_allclose(st["pv"][:, early], da["pv"][:, early])
    assert (st["pv"] < da["pv"] - 1e-3).any(), "the measurements tighten the plan somewhere"


def test_intraday_envelopes_hold_in_the_real_state_wherever_their_plan_held(setup):
    """Every year replayed with a full nonlinear solve against the real state, every hour.

    The plan is an empirical bound, and the tiny grid has a single other year to take it from,
    so the year does beat it in places (the day-ahead plan breaks there too). What must hold is
    the implication the method rests on: an hour the real state breaks a limit in, with the
    envelopes held, is an hour the audit (`forecast.exceedance_hours`) counted the plan beaten.
    The envelopes themselves are verified against their planning state in every hour.
    """
    from kapacitet.engine import doe, forecast
    from kapacitet.engine.scenario import Scenario, adoption_order

    model, hp, ev = setup
    order = adoption_order(0, model.C)
    ms = model.C // 3
    for k in (model.C * 2 // 3, model.C):
        pv = np.zeros(model.C, dtype=bool)
        pv[order[:k]] = True
        nf = order[ms:k]
        for y in synthetic_profiles.YEARS:
            actual = Scenario(year=y, pv=pv, hp=hp, ev=ev).arrays()
            plan = forecast.planning_state("intraday", y, pv, hp, ev, actual)
            r = doe.compute(model, plan, nf)
            assert r.full_year_violations == 0, "verified against the state it was allocated on"
            p = doe.held_injection(actual, r)
            v = model.violations(model.solve(p, np.asarray(actual["q"], dtype=float)), p)
            p_firm = np.asarray(actual["p_net"], dtype=float).copy()
            p_firm[nf] = np.minimum(p_firm[nf], 0.0)
            vf = model.violations(model.solve(p_firm, np.asarray(actual["q"], dtype=float)), p_firm)
            broken = v.export_side & ~vf.export_side
            ex = forecast.exceedance_hours(plan, actual)
            beaten = (ex["pv_customers"] > 0) | (ex["load_customers"] > 0)
            assert not (broken & ~beaten).any(), np.flatnonzero(broken & ~beaten)[:5]
        rr = forecast.multiyear(model, order, ms, k, synthetic_profiles.YEARS, hp, ev, "intraday",
                                check_safety=True)
        assert rr["planning_full_year_violations"] == 0
        assert 0 <= rr["unnecessary_hours"] <= rr["hours_limited"]
        assert rr["exceedance"] is not None and rr["exceedance"]["audits"] == len(synthetic_profiles.YEARS)


def test_intraday_envelopes_hold_in_a_real_state_their_plan_dominates(setup):
    """Where what happens stays inside the plan — no more sun, no less load — the envelopes the
    gates issued break no limit in any hour of the year."""
    from kapacitet.engine import doe, forecast
    from kapacitet.engine.scenario import Scenario, adoption_order

    model, hp, ev = setup
    order = adoption_order(0, model.C)
    pv = np.ones(model.C, dtype=bool)
    nf = order[model.C // 3:]
    y = synthetic_profiles.YEARS[0]
    actual = Scenario(year=y, pv=pv, hp=hp, ev=ev).arrays()
    plan = forecast.planning_state("intraday", y, pv, hp, ev, actual)
    dominated = forecast._arrays(np.minimum(actual["pv"], plan["pv"]), np.maximum(actual["load"], plan["load"]))
    r = forecast.issue(model, plan, dominated, nf, "intraday")
    assert r.violation_hours == 0 and r.planning_full_year_violations == 0
    assert r.exceedance["pv_customer_hours_over"] == 0 == r.exceedance["load_customer_hours_under"]


def test_a_gate_every_hour_lands_between_day_ahead_and_perfect_foresight(setup):
    """With no delay and a gate every hour the plan is the day-ahead one tightened by the hour
    just measured: it carries at least the day-ahead capacity, and at most perfect foresight's."""
    from kapacitet.config import A
    from kapacitet.engine import forecast, run
    from kapacitet.engine.scenario import adoption_order

    model, hp, ev = setup
    z_star = A("z_star_pct")
    years = synthetic_profiles.YEARS
    r = run.analyze_order((0, years, z_star))
    ms, mts, k_pf = r["static"]["adopters"], r["timeseries"]["adopters"], r["doe"]["adopters"]
    order = adoption_order(0, model.C)
    k_da, _ = forecast.adopters(model, order, ms, mts, years, hp, ev, "climatological", z_star)
    with gates(range(24), 0):
        k_in, det = forecast.adopters(model, order, ms, mts, years, hp, ev, "intraday", z_star)
    assert k_da <= k_in <= k_pf, (k_da, k_in, k_pf)
    assert det["planning_full_year_violations"] == 0 and det["z_pct"] <= z_star + 1e-9


def test_the_memo_tells_gates_apart(setup):
    """A memo shared between searches of two sets of gates never hands one the other's runs."""
    from kapacitet.config import A
    from kapacitet.engine import forecast
    from kapacitet.engine.scenario import adoption_order

    model, hp, ev = setup
    order = adoption_order(0, model.C)
    runs: dict = {}
    ms = model.C // 3
    forecast.adopters(model, order, ms, ms + 1, synthetic_profiles.YEARS, hp, ev, "intraday",
                      A("z_star_pct"), hi=ms + 2, memo=runs)
    before = set(runs)
    with gates((12,), 2):
        forecast.adopters(model, order, ms, ms + 1, synthetic_profiles.YEARS, hp, ev, "intraday",
                          A("z_star_pct"), hi=ms + 2, memo=runs)
    assert set(runs) - before, "the other gates measured their own counts"
    assert all(k[0] == "intraday" and isinstance(k[3], tuple) for k in runs)


def test_the_scorecard_judges_each_threshold():
    from kapacitet.engine.intraday import REPLICA_THRESHOLDS, scorecard

    ok = scorecard(720.0, 600.0, 0, [100.0, 100.0])
    assert ok["result"] == "pass" and ok["gain_over_hourly_pct"]["value"] == pytest.approx(20.0)
    assert scorecard(720.0, 600.0, 0, [100.0, 95.0])["result"] == "pass_if_justified"
    assert scorecard(720.0, 600.0, 1, [100.0])["violation_hours"]["result"] == "fail"
    low = scorecard(650.0, 600.0, 0, [100.0])
    assert low["gain_over_hourly_pct"]["result"] == "fail" and low["result"] == "fail"
    assert scorecard(720.0, 600.0, 0, [80.0])["agreements_within_pct"]["result"] == "fail"
    assert REPLICA_THRESHOLDS["gain_over_hourly_pct"] == 15.0


def test_a_new_gate_assumption_is_validated():
    from kapacitet.engine import intraday

    with gates((25,), 1), pytest.raises(ValueError):
        intraday.gates_and_lag()
    with gates((6,), -1), pytest.raises(ValueError):
        intraday.gates_and_lag()


# ---------------------------------------------------------------- the published replica

def _published() -> dict:
    path = processed("results", "intraday.json")
    if not path.exists():
        pytest.skip(f"intraday.json not published yet; {INTRADAY}")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_published_replica_backs_its_scorecard():
    from kapacitet.config import A

    d = _published()
    assert d["gates_h"] == sorted(A("intraday_gates_h")) and d["measurement_lag_h"] == A("intraday_measurement_lag_h")
    assert d["margins"]["pv_pct"] == pytest.approx(A("dayahead_pv_margin_pct"))
    assert len(d["per_order"]) == d["n_orders"]
    ref = d["median_kw_reference"]
    for basis in (d, d["installation"]):
        assert basis["violation_hours"] == 0 and basis["planning_full_year_violations"] == 0
        assert 0 <= basis["unnecessary_hours"] <= basis["hours_limited"]
        assert 0 <= basis["within_budget_orders"] <= d["n_orders"]
        sc = basis["scorecard"]
        assert sc["gain_over_hourly_pct"]["hourly_kw"] == pytest.approx(ref["timeseries"])
        assert sc["gain_over_hourly_pct"]["value"] == pytest.approx((basis["median_kw"] / ref["timeseries"] - 1) * 100)
        assert sc["violation_hours"]["value"] == basis["violation_hours"]
        assert {v["result"] for k, v in sc.items() if k != "result"} <= {"pass", "pass_if_justified", "fail"}
    # the intraday plan is never more pessimistic than the day-ahead one, and stops at foresight
    assert d["median_kw"] <= ref["doe"] + 1e-6
    assert d["installation"]["median_kw"] <= d["median_kw"] + 1e-6
    for r in d["per_order"]:
        assert r["installation"]["adopters"] <= r["adopters"]
        assert r["kw_ac"] <= r["doe_kw"] + 1e-6
        assert r["installation"]["z_customer_max_pct"] <= d["z_star_pct"] + 1e-9
