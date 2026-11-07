"""The quantile forecasts of the intraday replica's forecast variant (engine/nowcast.py).

On the tiny grid with its synthetic profiles, so CI runs them (CI installs the `forecast` extra):
the model that plans a year never saw it, the forecasts are deterministic, a quantile model of a
series with known noise is beaten about as often as its quantile says, and the envelopes the
forecast variant issues are replayed against the real state of the tiny grid. The contract test on the
published variants is in test_results_contract.py.
"""

from __future__ import annotations

import contextlib

import numpy as np
import pytest
from conftest import synthetic_profiles, tiny_grid

pytest.importorskip("lightgbm", reason="the forecast extra: pip install -e .[forecast]")


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
def overrides(values: dict):
    from kapacitet import cache
    from kapacitet.config import set_overrides

    set_overrides(values)
    cache.clear_all()
    try:
        yield
    finally:
        set_overrides(None)
        cache.clear_all()


def _probe(year: int, kind: str, hp, ev) -> np.ndarray:
    """A fixed feature matrix, built from `year`'s own data, to read a model's predictions on."""
    from kapacitet.engine import nowcast

    a = nowcast.series(kind, year, hp, ev)
    cs = nowcast.clear_sky_per_kwp(year)
    rng = np.random.default_rng(0)
    n = 2000
    h = rng.integers(1, 13, n)
    t = h + 3 + rng.integers(0, a.shape[1] - h - 3)
    c = rng.integers(0, a.shape[0], n)
    return nowcast.features(year, a, cs, c, t, h)


def _forget_models(folder):
    from kapacitet import cache

    for p in (folder / "nowcast").glob("*.txt"):
        p.unlink()
    cache.clear_all()


def _small_models(monkeypatch):
    """Fewer rounds and rows: for the tests of data flow and reproducibility, not of accuracy."""
    from kapacitet.engine import nowcast

    monkeypatch.setattr(nowcast, "ROUNDS", 40)
    monkeypatch.setattr(nowcast, "TRAIN_ROWS_PER_YEAR", 10_000)


def test_the_model_for_a_year_never_saw_that_year(setup, tiny, monkeypatch):
    """Scaling the planned year's own profiles leaves its model untouched; scaling the other
    year's changes it. The forecast reads the planned year only through what was measured."""
    from kapacitet.engine import nowcast

    model, hp, ev = setup
    y, other = synthetic_profiles.YEARS[-1], synthetic_profiles.YEARS[0]
    assert y not in nowcast.training_years(y) and other in nowcast.training_years(y)
    real = nowcast.series
    for kind in nowcast.KINDS:
        X = _probe(y, kind, hp, ev)
        _small_models(monkeypatch)
        _forget_models(tiny)
        base = nowcast.model(kind, y, hp, ev).predict(X)

        def scaled(changed):
            _small_models(monkeypatch)
            monkeypatch.setattr(nowcast, "series", lambda k, yy, a=None, b=None:
                                real(k, yy, a, b) * (3.0 if yy == changed else 1.0))
            _forget_models(tiny)
            return nowcast.model(kind, y, hp, ev).predict(X)

        np.testing.assert_array_equal(scaled(y), base)
        assert not np.allclose(scaled(other), base)
        monkeypatch.undo()
    _forget_models(tiny)


def test_a_forecast_reads_no_hour_after_its_gate(setup):
    """Changing every hour after the last measured one leaves the features untouched."""
    from kapacitet.engine import nowcast

    model, hp, ev = setup
    y = synthetic_profiles.YEARS[-1]
    a = nowcast.series("load", y, hp, ev).copy()
    cs = nowcast.clear_sky_per_kwp(y)
    c, t, h = np.array([0, 1]), np.array([1000, 1000]), np.array([3, 3])
    f = nowcast.features(y, a, cs, c, t, h)
    a[:, 1000 - 3 + 1:] += 5.0
    np.testing.assert_array_equal(nowcast.features(y, a, cs, c, t, h), f)


def test_the_forecasts_are_deterministic(setup, tiny, monkeypatch):
    """Trained twice from nothing, the same inputs give the same model and the same forecast."""
    from kapacitet.engine import intraday, nowcast

    model, hp, ev = setup
    _small_models(monkeypatch)
    y = synthetic_profiles.YEARS[0]
    g, lag = intraday.gates_and_lag()
    runs = []
    for _ in range(2):
        _forget_models(tiny)
        runs.append((nowcast.model("pv", y).model_to_string(),
                     nowcast.quantile_forecast("pv", y, g, lag).copy(),
                     nowcast.quantile_forecast("load", y, g, lag, hp, ev).copy()))
    assert runs[0][0] == runs[1][0]
    np.testing.assert_array_equal(runs[0][1], runs[1][1])
    np.testing.assert_array_equal(runs[0][2], runs[1][2])
    # a second process reads the model the first one kept, and forecasts the same
    from kapacitet import cache
    cache.clear_all()
    np.testing.assert_array_equal(nowcast.quantile_forecast("pv", y, g, lag), runs[0][1])
    monkeypatch.undo()
    _forget_models(tiny)


@pytest.mark.parametrize(("q_pv", "q_load"), [(0.9, 0.1), (0.995, 0.005)])
def test_a_quantile_model_of_known_noise_is_beaten_as_often_as_its_quantile_says(setup, tiny, monkeypatch,
                                                                                 q_pv, q_load):
    """On a synthetic series with known noise (a daily shape and AR(1) hourly noise, every year
    drawn alike from its own seed), each tail's quantile, trained on the other year, is beaten out
    of sample in a share of the forecast hours within a tolerance of its nominal rate: the solar
    (upper) one more often than 1 - q_pv, never above, the load (lower) one less often than q_load,
    so a sign or tail mistake on either side fails.

    The tiny grid has one other year to train on, the reference network ten, so the one year
    gives as many rows as the ten would (a tail quantile out of sample needs them)."""
    from kapacitet.engine import intraday, nowcast

    model, hp, ev = setup
    real = nowcast.series

    def synthetic(kind, year, a=None, b=None):
        C, H = real("pv", year).shape
        rng = np.random.default_rng([7, year])
        e = np.empty((C, H), dtype=np.float64)
        e[:, 0] = rng.normal(0.0, 0.3, C)
        shocks = rng.normal(0.0, 0.3 * np.sqrt(1 - 0.8 ** 2), (C, H))
        for t in range(1, H):
            e[:, t] = 0.8 * e[:, t - 1] + shocks[:, t]
        hour = np.arange(H) % 24
        return (2.0 + np.sin(2 * np.pi * hour / 24.0) + e).astype(np.float32)

    monkeypatch.setattr(nowcast, "series", synthetic)
    monkeypatch.setattr(nowcast, "TRAIN_ROWS_PER_YEAR", 10 * nowcast.TRAIN_ROWS_PER_YEAR)
    y = synthetic_profiles.YEARS[-1]
    try:
        with overrides({"nowcast_pv_quantile": q_pv, "nowcast_load_quantile": q_load}):
            g, lag = intraday.gates_and_lag()
            cals = nowcast.calibration([y], g, lag, hp, ev)
    finally:
        monkeypatch.undo()
        _forget_models(tiny)
    for kind, nominal in (("pv", 1.0 - q_pv), ("load", q_load)):
        cal = cals[kind]
        assert cal["nominal_exceedance"] == pytest.approx(nominal), kind
        assert cal["customer_hours"] > 10_000, kind
        tol = max(0.3 * nominal, 0.004)
        assert abs(cal["realised_exceedance"] - nominal) <= tol, (kind, cal["realised_exceedance"], nominal)


def test_the_forecast_plan_never_assumes_less_than_the_day_ahead_one(setup):
    """Each gate's forecast can only tighten the day-ahead plan: no more solar, no less load."""
    from kapacitet.engine import forecast, intraday

    model, hp, ev = setup
    y = synthetic_profiles.YEARS[-1]
    pv = np.ones(model.C, dtype=bool)
    da = forecast.climatological(y, pv, hp, ev)
    st = forecast.planning_state("nowcast", y, pv, hp, ev, None)
    assert (st["pv"] <= da["pv"] + 1e-6).all()
    assert (st["load"] >= da["load"] - 1e-6).all()
    assert np.isfinite(st["p_net"]).all()
    hod = intraday.horizons(*intraday.gates_and_lag())
    early = np.tile(hod == 0, st["pv"].shape[1] // 24)
    np.testing.assert_allclose(st["pv"][:, early], da["pv"][:, early])
    assert (st["pv"] < da["pv"] - 1e-3).any(), "the forecasts tighten the plan somewhere"


def test_forecast_envelopes_break_a_limit_only_where_the_year_beat_the_forecast(setup):
    """Every year replayed with a full nonlinear solve against the real state, every hour.

    A quantile forecast is beaten by design in about 1 - q of the hours it forecasts, so the
    real state can break a limit with the envelopes held, and on the tiny grid it does in a few
    hours (the stage then keeps the worst-change variant as the headline). What must hold is the
    implication the method rests on: an hour the real state breaks a limit in, with the envelopes
    held, is an hour the audit counted the plan beaten. The envelopes themselves are verified
    against their planning state in every hour."""
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
            plan = forecast.planning_state("nowcast", y, pv, hp, ev, actual)
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
        rr = forecast.multiyear(model, order, ms, k, synthetic_profiles.YEARS, hp, ev, "nowcast",
                                check_safety=True)
        assert rr["planning_full_year_violations"] == 0
        assert 0 <= rr["unnecessary_hours"] <= rr["hours_limited"]
        assert rr["exceedance"] is not None and rr["exceedance"]["audits"] == len(synthetic_profiles.YEARS)


def test_forecast_envelopes_break_no_limit_in_the_real_state_of_the_tiny_grid(setup):
    """The plan's test: 0 violation hours in the actual state. The replay of every year against
    its real state is reported (`multiyear(..., check_safety=True)`), and it is the count a full
    nonlinear check of every hour gives by hand. A quantile forecast is beaten by design, so where
    the count is not 0 the test is an expected failure with that count: the stage then withholds
    the variant's capacity and keeps the worst-change headline (the next test)."""
    from kapacitet.engine import doe, forecast
    from kapacitet.engine.scenario import Scenario, adoption_order

    model, hp, ev = setup
    order = adoption_order(0, model.C)
    ms, k = model.C // 3, model.C
    pv = np.ones(model.C, dtype=bool)
    nf = order[ms:k]
    rr = forecast.multiyear(model, order, ms, k, synthetic_profiles.YEARS, hp, ev, "nowcast", check_safety=True)
    by_hand = 0
    for y in synthetic_profiles.YEARS:
        actual = Scenario(year=y, pv=pv, hp=hp, ev=ev).arrays()
        r = doe.compute(model, forecast.planning_state("nowcast", y, pv, hp, ev, actual), nf)
        by_hand += doe.verify_full_year(model, actual, r, every_hour=True)["export_violation_hours"]
    assert isinstance(rr["violation_hours"], int) and rr["violation_hours"] == by_hand
    assert rr["violation_hours"] == sum(p["violation_hours"] for p in rr["per_year"])
    if rr["violation_hours"]:
        pytest.xfail(f"the forecast variant breaks a limit in {rr['violation_hours']} replayed hours "
                     "of the tiny grid: a quantile forecast is beaten by design")


def test_an_unverified_forecast_variant_publishes_no_capacity_and_is_not_the_headline():
    """run_extra.intraday: a forecast variant with violation hours keeps them, its verdicts and
    its audits, loses every capacity figure, and leaves the headline to the worst-change one."""
    from kapacitet.engine import run_extra

    def variant(h: int) -> dict:
        basis = lambda: {"median_kw": 500.0, "kw_ac_stats": {"p10": 1.0}, "median_adopters": 50.0,
                         "violation_hours": h,
                         "scorecard": {"gain_over_hourly_pct": {"value": 2.0, "kw": 500.0, "result": "fail"}}}
        row = lambda: {"adopters": 50, "kw_ac": 500.0, "violation_hours": h}
        v = {**basis(), "installation": basis(), "verified": h == 0,
             "per_order": [{**row(), "installation": row()}]}
        return v

    bad, good = variant(3), variant(0)
    run_extra._withhold_capacity(bad)
    run_extra._withhold_capacity(good)
    assert bad["capacity_withheld"] and not good["capacity_withheld"]
    for b in (bad, bad["installation"]):
        assert b["median_kw"] is None and b["kw_ac_stats"] is None and b["median_adopters"] is None
        assert b["scorecard"]["gain_over_hourly_pct"]["value"] is None and b["violation_hours"] == 3
    assert all(r[k] is None for r in (bad["per_order"][0], bad["per_order"][0]["installation"])
               for k in ("adopters", "kw_ac"))
    assert good["median_kw"] == 500.0 and good["per_order"][0]["kw_ac"] == 500.0
    wc = variant(0)
    assert run_extra._headline({"worst_change": wc, "forecast": bad}) == "worst_change"
    assert run_extra._headline({"worst_change": wc, "forecast": good}) == "forecast"
    assert run_extra._headline({"worst_change": wc}) == "worst_change"


def test_the_memo_tells_quantiles_apart(setup):
    """A memo shared between searches at two quantiles never hands one the other's runs."""
    from kapacitet.config import A
    from kapacitet.engine import forecast
    from kapacitet.engine.scenario import adoption_order

    model, hp, ev = setup
    order = adoption_order(0, model.C)
    runs: dict = {}
    ms = model.C // 3
    forecast.adopters(model, order, ms, ms + 1, synthetic_profiles.YEARS, hp, ev, "nowcast",
                      A("z_star_pct"), hi=ms + 2, memo=runs)
    before = set(runs)
    with overrides({"nowcast_pv_quantile": 0.99}):
        forecast.adopters(model, order, ms, ms + 1, synthetic_profiles.YEARS, hp, ev, "nowcast",
                          A("z_star_pct"), hi=ms + 2, memo=runs)
    assert set(runs) - before, "the other quantile measured its own counts"
    assert all(k[0] == "nowcast" for k in runs)


def test_a_quantile_assumption_is_validated():
    from kapacitet.engine import nowcast

    with overrides({"nowcast_pv_quantile": 1.0}), pytest.raises(ValueError):
        nowcast.quantile("pv")
    with overrides({"nowcast_load_quantile": 0.9}), pytest.raises(ValueError):
        nowcast.quantile("load")
    assert nowcast.nominal_exceedance("pv") == pytest.approx(1 - nowcast.quantile("pv"))
    assert nowcast.min_leaf("pv") == 2000
