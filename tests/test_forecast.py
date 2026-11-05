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


@needs_results
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


@needs_results
def test_the_exceedances_are_counted_on_both_sides(case):
    _, show, _, hp, ev, _ = case
    audit = forecast.exceedance_audit(show["year"], hp, ev)
    assert audit["pv_bound_over_measured_energy"] > 1.0, "a bound above what the year produced"
    assert audit["load_bound_under_measured_energy"] < 1.0, "a floor below what it consumed"
    assert audit["pv_aggregate_hours_over"] <= 5, audit
    assert audit["pv_worst_aggregate_ratio"] < 1.1, audit
    assert audit["load_aggregate_hours_under"] == 0, audit
    # the margins only ever remove exceedances, and the bare record's ratio stays published
    bare = forecast.exceedance_audit(show["year"], hp, ev, pv_margin=0.0, load_margin=0.0)
    assert bare["pv_margin_pct"] == 0 and audit["pv_margin_pct"] == A("dayahead_pv_margin_pct")
    assert audit["pv_aggregate_hours_over"] <= bare["pv_aggregate_hours_over"]
    assert audit["pv_customer_hours_over"] <= bare["pv_customer_hours_over"]
    assert audit["load_customer_hours_under"] <= bare["load_customer_hours_under"]
    assert audit["pv_worst_aggregate_ratio_unmargined"] == pytest.approx(bare["pv_worst_aggregate_ratio"])


@needs_results
def test_a_wider_calendar_window_is_a_tighter_bound_on_the_record(case):
    """The window is the whole reason the bound works: one calendar hour has one sample per
    weather year, which bounds nothing."""
    _, show, _, hp, ev, _ = case
    narrow = forecast.exceedance_audit(show["year"], hp, ev, window_days=0)
    wide = forecast.exceedance_audit(show["year"], hp, ev, window_days=forecast.WINDOW_DAYS)
    assert narrow["pv_aggregate_hours_over"] > wide["pv_aggregate_hours_over"]
    assert wide["pv_bound_over_measured_energy"] > narrow["pv_bound_over_measured_energy"]
    assert wide["load_bound_under_measured_energy"] < narrow["load_bound_under_measured_energy"]


@needs_results
def test_the_bound_still_follows_the_calendar(case):
    """A bound that ignored the clock would be the static worst case, which carries nobody."""
    _, show, _, _, _, _ = case
    bound = forecast.pv_upper_bound(show["year"])
    assert (bound >= 0).all()
    assert bound.max() <= 1.0 / A("pv_dc_ac_ratio") + 1e-6, "never above the inverter rating"
    hourly = bound.mean(axis=0)
    assert hourly.min() == 0.0, "night is still night"
    assert hourly.max() > 0.5 / A("pv_dc_ac_ratio"), "and midsummer noon is still noon"


@needs_results
def test_envelopes_issued_against_the_bound_keep_the_limits(case):
    """The claim the scheme lives on, checked with the nonlinear flow on the real year."""
    model, show, pv, hp, ev, actual = case
    nonfirm = np.array(show["nonfirm_cids"], dtype=int)
    r = forecast.issue(model, forecast.climatological(show["year"], pv, hp, ev), actual, nonfirm,
                       "climatological")
    assert r.violation_hours == 0, "a dominating state leaves no hour unguarded"
    assert r.curtailed_kwh > 0, "and it is not free"
    assert 0 < r.curtailed_share_pct < 100
    assert r.exceedance["net_aggregate_hours_not_dominated"] == 0, r.exceedance


@needs_results
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
    assert r.exceedance["net_aggregate_hours_not_dominated"] > 0, "and its audit says why"
    err = forecast.forecast_error(actual, forecast.persistence(show["year"], pv, hp, ev))
    assert err["p95_underprediction_kw"] > 0, "it under-predicts export, which is the dangerous way"


@needs_results
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


@needs_results
def test_the_leap_year_bound_covers_the_leap_day():
    leap = next((y for y in settings().weather_years if y % 4 == 0), None)
    if leap is None:
        pytest.skip("no leap year among the weather years")
    from kapacitet.timeindex import local_standard, year_index

    idx = year_index(leap)
    bound = forecast.pv_upper_bound(leap)
    assert bound.shape[1] == len(idx) == 8784
    local = local_standard(idx)
    feb29 = np.asarray((local.month == 2) & (local.day == 29))
    assert feb29.sum() == 24
    assert np.isfinite(bound[:, feb29]).all()
    # the leap day is bounded by 28 February hour by hour, not an hour late
    feb28 = np.asarray((local.month == 2) & (local.day == 28))
    assert np.array_equal(bound[:, feb29], bound[:, feb28])


def test_the_leap_day_is_the_study_calendars_and_keeps_its_hours():
    """29 February is masked on the UTC+1 calendar, so every hour comes back on its own hour.

    Masked on UTC's calendar instead, the leap day restored from 28 February ran an hour late,
    and the record at sunrise was the hour before sunrise: 2016 beat it by 91 kW.
    """
    from kapacitet.timeindex import hour_of_day, local_standard, year_index

    for year in (2016, 2025):
        hod = hour_of_day(year_index(year)).astype(float)[None, :]
        days = forecast._day_shaped(year, hod)
        assert days.shape == (1, 365, 24) and (days == np.arange(24)).all()
        assert np.array_equal(forecast._restore_leap(year, days), hod)
    local = local_standard(year_index(2016))
    leap = forecast._leap_day(2016)
    assert leap.sum() == 24 and (local[leap].day == 29).all()
    assert (local[leap].hour == np.arange(24)).all()


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


FORESIGHT = "run: python -m kapacitet compute --stage extra --only foresight"


def _foresight_with_margins(results) -> dict:
    """foresight.json, once it carries the floor guard and the margins; skipped before that."""
    try:
        f = results("foresight")
    except FileNotFoundError:
        pytest.skip(f"no foresight.json ({FORESIGHT})")
    if "within_budget_orders" not in f or "margins" not in f:
        pytest.skip(f"foresight.json predates the floor guard and the day-ahead margins; {FORESIGHT}")
    return f


@needs_results
def test_every_published_day_ahead_order_is_inside_the_budget(results):
    """The search used to assume its floor was acceptable; now it measures it."""
    f = _foresight_with_margins(results)
    over = [(r["order"], r["z_pct"]) for r in f["per_order"] if r["z_pct"] > f["z_star_pct"] + 1e-9]
    assert not over, over
    assert f["within_budget_orders"] == f["n_orders"]


@needs_results
def test_the_day_ahead_capacity_sits_between_static_and_foresight(results):
    f = _foresight_with_margins(results)
    for r in f["per_order"]:
        assert r["static_kw"] - 1e-9 <= r["kw_ac"] <= r["doe_kw"] + 1e-9, r["order"]
        assert r["kw_minus_timeseries"] == pytest.approx(r["kw_ac"] - r["timeseries_kw"], abs=1e-9)
        assert r["at_floor"] == (r["kw_ac"] <= r["timeseries_kw"] + 1e-9), r["order"]
    stats = f["kw_ac_stats"]["climatological"]
    assert stats["median"] == pytest.approx(f["median_kw"]["climatological"])
    assert stats["min"] <= stats["p10"] <= stats["median"] <= stats["p90"] <= stats["max"]


@needs_results
def test_the_margins_cover_the_record_of_the_reference_year(results):
    f = _foresight_with_margins(results)
    assert f["exceedance"]["pv_margin_pct"] == f["margins"]["pv_pct"]
    if f["margins"]["pv_pct"] > 0:
        assert f["exceedance"]["pv_aggregate_hours_over"] == 0, f["exceedance"]
    audit = f["plan_exceedance"]
    assert audit["total"] is not None and audit["total"]["audits"] > 0, "every issue is audited"
    assert set(audit["by_year"]) == {str(y) for y in f["years"]}
    assert sum(ex["audits"] for ex in audit["by_year"].values() if ex) == audit["total"]["audits"]


# ------------------------------------------------------------------- the search, without data

class _Grid:
    C = 120


MS = 50


def _z(k: int) -> float:
    """Curtailment by adopter count, as order 2 published it: z(72) = 2.96 and z(73) = 3.35
    around a 3 % budget, z(96) = 10 at the perfect-foresight ceiling, and zero at `MS`."""
    if k <= 72:
        return 2.96 * (k - MS) / (72 - MS)
    if k == 73:
        return 3.35
    return 3.35 + (10.0 - 3.35) * (k - 73) / (96 - 73)


@pytest.fixture
def searched(monkeypatch):
    calls: list[tuple[int, bool]] = []

    def multiyear(model, order, ms, k, years, hp, ev, scheme, *, window_days, policy,
                  check_safety=False):
        calls.append((int(k), bool(check_safety)))
        # the worst installation curtails 1.4 times the average
        return {"adopters": int(k), "z_pct": _z(k), "z_customer_max_pct": 1.4 * _z(k), "violation_hours": 0}

    monkeypatch.setattr(forecast, "multiyear", multiyear)
    return calls


def _search(lo: int, hi: int | None, **kw):
    return forecast.adopters(_Grid(), np.arange(_Grid.C), MS, lo, (2025,), None, None,
                             "climatological", 3.0, hi=hi, **kw)


def test_the_installation_budget_reuses_the_counts_already_measured(searched):
    runs: dict = {}
    k, _ = _search(60, 96, memo=runs)
    first = [n for n, full in searched if not full]
    k_i, det = _search(60, k, budget="installation", memo=runs)
    trial = [n for n, full in searched if not full]
    assert (k, k_i) == (72, 65), "1.4 × z(k) <= 3 % up to 65 adopters"
    assert det["adopters"] == 65 and searched[-1] == (65, True)
    assert len(trial) == len(set(trial)), "a count the first search measured is not run again"
    assert len(trial) > len(first) and set(first) <= set(runs)
    with pytest.raises(ValueError):
        _search(60, 96, budget="per_house")


def test_the_search_measures_its_floor_and_goes_down_when_it_fails(searched):
    k, det = _search(73, 96)
    assert k == 72 and det["adopters"] == 72
    trial = [n for n, full in searched if not full]
    assert trial[0] == 73, "the floor is measured first, never assumed"
    assert 96 not in trial, "a floor over the budget never pays for the ceiling"
    assert MS not in trial, "nobody holds an envelope at ms, so z(ms) = 0 needs no run"
    assert len(trial) == len(set(trial)), "each count is evaluated once"
    assert searched[-1] == (72, True), "the point returned is checked in full"


def test_the_floor_guard_holds_when_the_ceiling_is_no_higher(searched):
    assert _search(73, 73)[0] == 72
    assert _search(73, 60)[0] == 72
    assert _search(72, 60)[0] == 72, "a floor inside the budget stays, whatever the ceiling"


def test_a_floor_inside_the_budget_still_climbs(searched):
    assert _search(60, 96)[0] == 72
    assert _search(60, 70)[0] == 70, "and stops at the ceiling when the ceiling passes"


# ------------------------------------------------------------------- the margins, without data

@pytest.fixture
def record(monkeypatch):
    """A six-house record in place of eleven years of profiles."""
    import pandas as pd

    rng = np.random.default_rng(4)
    C, H = 6, 48
    ilr = A("pv_dc_ac_ratio")
    kwp = np.array([3.0, 5.0, 8.0, 10.0, 12.0, 4.0])
    per = rng.uniform(0.0, 1.0 / ilr, (C, H))
    per[:, :6] = 0.0                       # night
    per[0, 10] = 1.0 / ilr                 # a house at its inverter rating
    floor = rng.uniform(0.2, 3.0, (C, H)).astype(np.float32)
    kept = per.copy(), floor.copy()
    monkeypatch.setattr(forecast, "customers",
                        lambda: pd.DataFrame({"kwp": kwp, "fuse_kva": np.full(C, 11.0)}))
    monkeypatch.setattr(forecast, "pv_upper_bound", lambda year, window_days=14: per)
    monkeypatch.setattr(forecast, "load_lower_bound", lambda year, hp, ev, window_days=14: floor)
    mask = np.ones(C, dtype=bool)
    mask[5] = False
    return mask, kwp, per, floor, kept


def test_the_margins_dominate_the_bare_record(record):
    mask, kwp, per, floor, kept = record
    bare = forecast.climatological(2025, mask, None, None, pv_margin=0.0, load_margin=0.0)
    wide = forecast.climatological(2025, mask, None, None, pv_margin=0.03, load_margin=0.03)
    assert (wide["pv"] >= bare["pv"]).all() and (wide["load"] <= bare["load"]).all()
    assert (wide["p_net"] >= bare["p_net"]).all(), "more generation and less load: more export"
    assert (wide["pv"] > bare["pv"]).any() and (wide["load"] < bare["load"]).all()
    inv = np.minimum(kwp / A("pv_dc_ac_ratio"), 11.0)
    assert (wide["pv"] <= inv[:, None] + 1e-6).all(), "the margin goes on before the inverter cap"
    assert wide["pv"][0, 10] == pytest.approx(bare["pv"][0, 10]), "a house at its rating stays there"
    assert (wide["pv"][5] == 0).all(), "and a house without solar has none"
    assert np.array_equal(per, kept[0]) and np.array_equal(floor, kept[1]), "the cached record is untouched"
    default = forecast.climatological(2025, mask, None, None)
    pm, lm = A("dayahead_pv_margin_pct") / 100, A("dayahead_load_margin_pct") / 100
    explicit = forecast.climatological(2025, mask, None, None, pv_margin=pm, load_margin=lm)
    assert np.array_equal(default["pv"], explicit["pv"]) and np.array_equal(default["load"], explicit["load"])
    assert forecast.day_ahead_margins() == (pm, lm)
    with pytest.raises(ValueError):
        forecast.day_ahead_margins(load_margin=1.0)


# ------------------------------------------------------------------- the audit, without data

def _state(pv: np.ndarray, load: np.ndarray) -> dict:
    return {"pv": pv, "load": load, "p_net": pv - load}


def test_plan_exceedance_counts_where_the_year_beat_the_plan():
    C, H = 3, 6
    plan = _state(np.full((C, H), 2.0), np.full((C, H), 1.0))
    pv, load = plan["pv"].copy(), plan["load"].copy()
    pv[0, 1] = 2.5                          # one house 0.5 over: so is the total
    pv[1, 3], pv[2, 3] = 2.2, 1.5           # one house over, the total under
    load[2, 4] = 0.6                        # one house 0.4 under: so is the total
    load[0, 5], load[1, 5] = 0.9, 1.3       # one house under, the total over
    actual = _state(pv, load)
    ex = forecast.plan_exceedance(plan, actual, nonfirm=[0])
    assert (ex["pv_customer_hours_over"], ex["pv_aggregate_hours_over"]) == (2, 1)
    assert ex["pv_worst_customer_kw"] == pytest.approx(0.5) and ex["pv_worst_aggregate_kw"] == pytest.approx(0.5)
    assert (ex["load_customer_hours_under"], ex["load_aggregate_hours_under"]) == (2, 1)
    assert ex["load_worst_customer_kw"] == pytest.approx(0.4) and ex["load_worst_aggregate_kw"] == pytest.approx(0.4)
    assert ex["net_aggregate_hours_not_dominated"] == 2 and ex["net_worst_aggregate_kw"] == pytest.approx(0.5)
    assert ex["first_hours"] == {"pv": [1, 3], "load": [4, 5], "net": [1, 4]}
    assert ex["nonfirm_pv_customer_hours_over"] == 1 and ex["nonfirm_load_customer_hours_under"] == 1

    hourly = forecast.exceedance_hours(plan, actual)
    assert hourly["pv_customers"].tolist() == [0, 1, 0, 1, 0, 0]
    assert hourly["load_customers"].tolist() == [0, 0, 0, 0, 1, 1]
    assert hourly["net_aggregate"].tolist() == [False, True, False, False, True, False]
    assert int(hourly["pv_customers"].sum()) == ex["pv_customer_hours_over"]

    same = forecast.plan_exceedance(actual, actual)
    assert all(v == 0 for k, v in same.items() if k != "first_hours"), "perfect foresight counts nothing"
    assert same["first_hours"] == {"pv": [], "load": [], "net": []}

    total = forecast.total_exceedance([ex, None, {**ex, "year": 2024}])
    assert total["pv_customer_hours_over"] == 4 and total["net_aggregate_hours_not_dominated"] == 4
    assert total["pv_worst_customer_kw"] == pytest.approx(0.5) and total["audits"] == 2
    assert "first_hours" not in total and "year" not in total
    assert forecast.total_exceedance([None, None]) is None
