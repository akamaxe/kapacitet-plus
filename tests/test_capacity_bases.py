"""The capacity searches on their other bases, on the tiny grid with its synthetic profiles.

- The curtailment budget per installation next to the aggregate one (run.curtailment,
  run.analyze_order, forecast.adopters): the second can only carry fewer adopters.
- Consumption hour by hour (hosting.nodal_import_hc_ts, hosting.ts_import_adopters): never more
  than the static winter snapshot allows, and checked here against the nonlinear flow of every
  hour, apart from the screen the search itself relies on.
- The static reference as parameters (static_pv_factor, static_load_rule).
"""

from __future__ import annotations

import contextlib

import numpy as np
import pytest
from conftest import synthetic_profiles, tiny_grid


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
def overridden(values: dict):
    """One input varied, as the tornado varies it, and put back after."""
    from kapacitet.config import set_overrides

    set_overrides(values)
    try:
        yield
    finally:
        set_overrides(None)


# ------------------------------------------------------------------ the budget per installation

def test_both_bases_come_from_the_same_run(setup):
    from kapacitet.engine import hosting, run
    from kapacitet.engine.delivery import share_pct
    from kapacitet.engine.scenario import adoption_order

    model, hp, ev = setup
    order = adoption_order(0, model.C)
    ms = hosting.static_adopters(model, order, hp, ev)
    years = synthetic_profiles.YEARS
    for method in ("doe", "fixed"):
        c = run.curtailment(order, ms, model.C, years, hp, ev, method=method, verify="critical")
        per = c["per_customer"]
        assert len(per["share_pct"]) == model.C - ms
        assert np.isclose(per["curtailed_kwh"].sum(), c["curtailed_kwh"], rtol=1e-5)
        assert np.isclose(per["potential_kwh"].sum(), c["potential_kwh"], rtol=1e-5)
        assert np.allclose(per["share_pct"], share_pct(per["curtailed_kwh"], per["potential_kwh"]))
        # the worst installation is at least the average, and its worst year at least its record
        assert c["z_customer_max_pct"] >= c["z_pct"] - 1e-6
        assert c["z_customer_year_max_pct"] >= c["z_customer_max_pct"] - 1e-9
        assert c["z_customer_max_pct"] == pytest.approx(per["share_pct"].max())
    empty = run.curtailment(order, ms, ms, years, hp, ev)
    assert empty["z_customer_max_pct"] == 0.0 and len(empty["per_customer"]["share_pct"]) == 0
    assert run.within_pct(empty, 3.0) == 100.0


def test_an_order_is_searched_on_both_bases(setup):
    from kapacitet.config import A
    from kapacitet.engine import run
    from kapacitet.engine.scenario import adoption_order

    model, hp, ev = setup
    z_star = A("z_star_pct")
    r = run.analyze_order((0, synthetic_profiles.YEARS, z_star))
    ts, agg, inst = r["timeseries"]["adopters"], r["doe"]["adopters"], r["doe_installation"]["adopters"]
    assert ts <= inst <= agg
    assert set(r["doe_installation"]) == set(r["doe"]) == {"adopters", "kw_ac", "kwp"}
    zi = r["z_doe_installation"]
    assert zi["customer_max_pct"] <= z_star + 1e-9
    assert zi["customer_year_max_pct"] >= zi["customer_max_pct"] - 1e-9     # only reported
    assert zi["full_year_violations"] == 0 and r["doe_full_year_violations_beyond_firm"] == 0
    assert r["z_doe_pct"] <= z_star + 1e-9
    assert r["z_doe_customer_max_pct"] >= r["z_doe_pct"] - 1e-6
    assert 0.0 <= r["customers_within_pct"] <= 100.0
    if inst < model.C:
        # one more installation breaks the per-installation budget (beyond `doe`, the aggregate too)
        nxt = run.curtailment(adoption_order(0, model.C), r["static"]["adopters"], inst + 1,
                              synthetic_profiles.YEARS, hp, ev, verify="critical")
        assert nxt["z_customer_max_pct"] > z_star


def test_the_day_ahead_search_reports_both_bases(setup):
    """foresight.json's search (run_extra._foresight_case): the per-installation count is under
    the aggregate one and under the perfect-foresight count on its own basis."""
    from kapacitet.config import A
    from kapacitet.engine import forecast, run, run_extra
    from kapacitet.engine.scenario import adoption_order

    model, hp, ev = setup
    z_star = A("z_star_pct")
    r = run.analyze_order((0, synthetic_profiles.YEARS, z_star))
    ms, mts = r["static"]["adopters"], r["timeseries"]["adopters"]
    f = run_extra._foresight_case((0, ms, mts, r["doe"]["adopters"], r["doe_installation"]["adopters"],
                                   synthetic_profiles.YEARS, z_star, forecast.WINDOW_DAYS))
    inst = f["installation"]
    assert inst["adopters"] <= f["adopters"] and inst["adopters"] <= r["doe_installation"]["adopters"]
    assert inst["z_customer_max_pct"] <= z_star + 1e-9 and f["z_pct"] <= z_star + 1e-9
    assert inst["violation_hours"] == 0 == f["violation_hours"]
    assert f["planning_full_year_violations"] == 0 == inst["planning_full_year_violations"]
    assert f["timeseries_adopters"] == mts and f["at_floor"] == (f["adopters"] <= mts)
    # the count it settled on is the boundary, measured apart from the search: one more
    # installation breaks the per-installation budget
    order, k_i = adoption_order(0, model.C), inst["adopters"]
    hi = min(f["adopters"], r["doe_installation"]["adopters"])
    here = forecast.multiyear(model, order, ms, k_i, synthetic_profiles.YEARS, hp, ev, "climatological")
    assert here["z_customer_max_pct"] <= z_star + 1e-9
    assert here["z_customer_max_pct"] == pytest.approx(inst["z_customer_max_pct"])
    if k_i < hi:
        more = forecast.multiyear(model, order, ms, k_i + 1, synthetic_profiles.YEARS, hp, ev, "climatological")
        assert more["z_customer_max_pct"] > z_star


# ------------------------------------------------------------------ consumption, hour by hour

@pytest.fixture(scope="module")
def bare(setup):
    """The model with nobody's heat pump or EV today. The tiny grid's own background already sits
    at the voltage limit in one hour of 2025, which would leave every capacity next to zero."""
    model = setup[0]
    none = np.zeros(model.C, dtype=bool)
    return model, none, none.copy()


@pytest.fixture(scope="module")
def nodal(bare):
    from kapacitet.engine import hosting

    model, hp, ev = bare
    return hosting.nodal_import_hc_ts(model, hp, ev, synthetic_profiles.YEARS), hosting.nodal_import_hc(model, hp, ev)


def test_the_hourly_record_never_allows_more_load_than_the_static_snapshot(nodal):
    hourly, static = nodal
    assert (hourly > 1.0).all()
    # the snapshot hour is among the hours searched; the hourly profiles are float32, the snapshot
    # float64, which can move a bisection by a step (250 kW / 2**24)
    assert (hourly <= static + 1e-3).all()


def test_a_grid_that_already_breaks_a_limit_takes_nothing(setup, monkeypatch):
    from kapacitet.engine import hosting
    from kapacitet.engine.scenario import adoption_order

    model, hp, ev = setup
    monkeypatch.setattr(model, "import_critical_hours", lambda p, q, vm_slack=None: np.array([0]))
    assert (hosting.nodal_import_hc_ts(model, hp, ev, synthetic_profiles.YEARS) == 0).all()
    # and the zero is not a measured capacity: the output can say why
    assert hosting.import_baseline_violations(model, hp, ev, synthetic_profiles.YEARS) == \
        {int(y): 1 for y in synthetic_profiles.YEARS}
    assert hosting.ts_import_adopters(model, adoption_order(0, model.C), "ev", hp, ev, synthetic_profiles.YEARS) == 0


def test_each_house_passes_at_its_capacity_and_fails_half_a_kilowatt_above(bare, nodal):
    """Every hour of both years solved with the nonlinear flow, not through the linear screen."""
    from kapacitet.config import A
    from kapacitet.engine import hosting

    model, hp, ev = bare
    hourly, _ = nodal
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    states = [hosting.consumption_state(y, hp, ev) for y in synthetic_profiles.YEARS]

    def breaks(j: int, kw: float) -> bool:
        for p, q in states:
            p, q = p.copy(), q.copy()
            p[j] -= kw
            q[j] -= kw * tanphi
            if model.violations(model.solve(p, q), p).import_side.any():
                return True
        return False

    for j in (int(np.argmin(hourly)), int(np.argmax(hourly)), model.C // 2):
        assert not breaks(j, hourly[j]), f"house {j} breaks a limit at its own capacity"
        assert breaks(j, hourly[j] + 0.5), f"house {j} still fits 0.5 kW above its capacity"


@pytest.mark.parametrize("device", ["ev", "hp", "flat"])
def test_a_device_count_is_the_last_that_keeps_every_hour(bare, device):
    from kapacitet.config import A
    from kapacitet.engine import hosting
    from kapacitet.engine.scenario import adoption_order
    from kapacitet.timeindex import hours_in_year

    model, hp, ev = bare
    order = adoption_order(3, model.C)
    years = synthetic_profiles.YEARS
    k = hosting.ts_import_adopters(model, order, device, hp, ev, years)
    has = {"ev": ev, "hp": hp, "flat": np.zeros(model.C, dtype=bool)}[device]
    new = [c for c in order if not has[c]]
    assert 0 < k <= len(new)

    def fits(n: int) -> bool:
        add = np.zeros(model.C, dtype=bool)
        add[new[:n]] = True
        for y in years:
            extra = None
            if device == "flat":
                extra = np.zeros((model.C, hours_in_year(y)))
                extra[add] = A("ev_charger_kw")
            p, q = hosting.consumption_state(y, hp | add if device == "hp" else hp,
                                             ev | add if device == "ev" else ev, extra)
            if len(model.import_critical_hours(p, q)):
                return False
        return True

    assert fits(k)
    if k < len(new):
        assert not fits(k + 1)
    with pytest.raises(ValueError):
        hosting.ts_import_adopters(model, order, "sauna", hp, ev, years)


# ------------------------------------------------------------------ the static reference

def test_less_solar_in_the_static_snapshot_connects_at_least_as_many(setup):
    from kapacitet.engine import hosting
    from kapacitet.engine.scenario import adoption_order

    model, hp, ev = setup
    order = adoption_order(0, model.C)
    base = hosting.static_adopters(model, order, hp, ev)
    nodal = hosting.nodal_pv_hc(model)
    with overridden({"static_pv_factor": 0.8}):
        assert hosting.static_adopters(model, order, hp, ev) >= base
        assert (hosting.nodal_pv_hc(model) >= nodal - 1e-6).all()
        assert hosting.static_definition(model)["pv_factor"] == 0.8
    with overridden({"static_pv_factor": 1.0}):
        assert hosting.static_adopters(model, order, hp, ev) == base


def test_the_static_load_rule_is_named_and_checked(setup):
    from kapacitet.engine import hosting
    from kapacitet.engine.scenario import adoption_order

    model, hp, ev = setup
    order = adoption_order(0, model.C)
    d = hosting.static_definition(model)
    assert d["snapshot"] == "summer" and d["load_rule"] == "min_daytime_summer"
    assert d["months"] == [5, 8] and d["hours"] == [10, 16] and d["pv_factor"] == 1.0
    base = hosting.static_adopters(model, order, hp, ev)
    with overridden({"static_load_rule": "no_load"}):
        assert (hosting.min_daytime_load() == 0).all()
        assert hosting.static_adopters(model, order, hp, ev) <= base
        assert "months" not in hosting.static_definition(model)
    with overridden({"static_load_rule": "average_day"}):
        with pytest.raises(ValueError):
            hosting.summer_snapshot(np.zeros(model.C, dtype=bool))
