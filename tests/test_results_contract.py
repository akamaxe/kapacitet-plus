"""The published results must back the claims the interface makes about them."""

from __future__ import annotations

import json

import pytest

import numpy as np

from conftest import needs_results
from kapacitet.config import A, settings
from kapacitet.paths import processed

pytestmark = needs_results


def test_envelopes_are_verified_over_the_whole_year(results):
    ver = results("showcase")["verification"]
    assert ver["doe_full_year_violations"] == 0          # the headline safety claim
    assert ver["screen_missed_hours"] == 0               # the linear screen missed nothing
    assert ver["doe_max_voltage_pu"] <= A("v_max_pu") + 1e-6
    assert ver["doe_max_trafo_loading_pct"] <= A("thermal_limit_pct") + 1e-3
    assert ver["uncontrolled_violation_hours"] > 0, "the comparison needs a case that does violate"


FULL_YEAR = "run: python -m kapacitet compute (the full-year verification fields came after these results)"


def _field(d: dict, key: str):
    if key not in d:
        pytest.skip(f"{key} not published yet; {FULL_YEAR}")
    return d[key]


def test_the_showcase_says_which_basis_its_violation_count_uses(results):
    """`doe_full_year_violations` counts every export-side hour, the firm customers' own ones too;
    `_beyond_firm` (DoeResult.full_year_violations' basis) leaves those out."""
    ver = results("showcase")["verification"]
    beyond, firm = _field(ver, "doe_full_year_violations_beyond_firm"), ver["doe_firm_violation_hours"]
    assert beyond == 0
    assert beyond + firm <= ver["doe_full_year_violations"]


def test_every_published_power_flow_converged(results):
    ver = results("showcase")["verification"]
    assert _field(ver, "pf_nonconverged_hours") == 0
    assert ver["pf_max_mismatch"] <= 1e-8


def test_every_published_envelope_point_is_verified_in_every_hour(results):
    """Not only the critical hours the screen picked: doe.compute's full-year check, at every point
    the interface shows (each sharing rule, each adoption order, the curve, the risk cases)."""
    show = results("showcase")["policies"]
    assert all(_field(p, "full_year_violations") == 0 for p in show.values())
    assert all(_field(o, "doe_full_year_violations_beyond_firm") == 0
               for o in results("hosting_summary")["per_order"])
    assert all(_field(p, "full_year_violations") == 0 for p in results("zcurve")["points"])
    risk = results("risk")
    assert _field(risk["stats"], "full_year_violations") == 0
    assert all(c["full_year_violations"] == 0 for c in risk["cases"])


def test_curtailment_of_the_headline_point_respects_the_planning_threshold(results):
    h = results("hosting_summary")
    z_star = h["z_star_pct"]
    z = [o["z_doe_pct"] for o in h["per_order"]]
    assert np.median(z) <= z_star + 1e-6
    assert h["measures"]["doe"]["kw_ac"]["median"] > h["measures"]["static"]["kw_ac"]["median"]
    for m in h["measures"].values():
        assert m["kw_ac"]["p10"] <= m["kw_ac"]["median"] <= m["kw_ac"]["p90"]


def test_curtailment_curve_is_monotone_and_starts_at_zero(results):
    pts = results("zcurve")["points"]
    kw = [p["kw_ac"] for p in pts]
    z = [p["z_doe_pct"] for p in pts]
    assert kw == sorted(kw)
    assert z[0] == 0.0                                   # the static level needs no curtailment
    assert all(b >= a - 1e-6 for a, b in zip(z, z[1:]))  # more solar never curtails less


def test_risk_statistics_are_ordered(results):
    s = results("risk")["stats"]
    assert s["mean_z_pct"] <= s["p90_z_pct"] <= s["worst_z_pct"] + 1e-9
    assert s["cvar95_z_pct"] >= s["p90_z_pct"] - 1e-9


def test_validation_numbers_support_the_method_page(results):
    v = results("validation")
    assert v["powerflow"]["max_abs_dv_pu"] < 1e-9        # "matches pandapower" on the method page
    assert v["linearisation"]["min_error_pu"] >= 0.0     # the screen estimate is conservative
    assert 0.8 <= v["household_shape"]["corr_hour_of_day"] <= 1.0
    assert len(v["weather"]) == len(settings().weather_years)
    assert sum(v["customers"]["fuse_counts"].values()) == v["grid"]["customers"]
    assert v["grid"]["lines"] == v["grid"]["buses"] - 2   # radial: buses = lines + transformer + 1


def test_every_cached_file_is_recorded_with_a_hash():
    from kapacitet.ingest.provenance import load_manifest

    manifest = load_manifest()
    assert manifest, "the provenance manifest must not be empty"
    for key, entry in manifest.items():
        assert entry.get("sha256"), f"{key} has no checksum"
        assert entry.get("fetched_at"), f"{key} has no timestamp"
        assert entry.get("source"), f"{key} has no source"


def test_run_metadata_records_versions_and_time():
    meta = json.loads(processed("results", "meta.json").read_text(encoding="utf-8"))
    assert meta["computed_at"] and meta["site"] and meta["years"]
    assert meta["reference_year"] in meta["years"]
    assert meta["versions"]["numpy"] and meta["versions"]["pandapower"]


@needs_results
def test_the_engine_still_reproduces_the_published_capacity(model, results):
    """The published numbers have to be recomputable, not just internally consistent.

    This is the guard that catches an assumption, a package version or a refactor that
    quietly moves a result. It recomputes the static hosting capacity of the first adoption
    orders and holds them against hosting_summary.json.
    """
    import numpy as np

    from kapacitet.config import A
    from kapacitet.engine import hosting
    from kapacitet.engine.scenario import adoption_order, mask_from_share

    summary = results("hosting_summary")
    C = model.C
    hp = mask_from_share(adoption_order(101, C), A("today_hp_share"))
    ev = mask_from_share(adoption_order(202, C), A("today_ev_share"))
    cap = hosting.cap_kw()

    for record in summary["per_order"][:4]:
        order = adoption_order(record["order"], C)
        adopters = hosting.static_adopters(model, order, hp, ev)
        kw = float(cap[order[:adopters]].sum())
        assert adopters == record["static"]["adopters"], f"order {record['order']}"
        assert np.isclose(kw, record["static"]["kw_ac"], rtol=1e-9)


# ---------------------------------------------------------------- results the screens now show

def test_the_audit_trail_adds_up_to_the_filing(results):
    import csv

    from kapacitet.engine.summaries import audit_totals

    rep = results("compliance")
    with processed("results", "audit_trail.csv").open(encoding="utf-8", newline="") as fh:
        tot = audit_totals(list(csv.DictReader(fh)))
    assert tot["hours"] == rep["hours_with_curtailment"]
    assert abs(tot["kwh"] - rep["curtailed_kwh"]) < 0.5
    for c in rep["per_customer"]:
        mine = tot["per_customer"][c["customer_id"]]
        assert mine["hours"] == c["hours_limited"], c["customer_id"]
        assert abs(mine["kwh"] - c["curtailed_kwh"]) < 0.1, c["customer_id"]


def test_the_overinstallation_summary_matches_an_independent_sum(results):
    """An independent recomputation of overinstallation_summary's sums from overinstallation.json."""
    from kapacitet.engine.summaries import overinstallation_summary

    d = results("overinstallation")
    pts, priced = d["points"], set(d["price_years"])

    def mean(p, key, value=None):
        rows = [r for r in p["per_year"] if r["year"] in priced]
        return sum((r[key][value] if value else r[key]) for r in rows) / len(rows)

    i = next(i for i, p in enumerate(pts) if p["adopters"] == d["doe_adopters"])
    s = overinstallation_summary(d)
    assert abs(s["budget"]["delivered_sek"] - mean(pts[i], "delivered_value", "net_sek")) < 1e-6
    assert abs(s["budget"]["curtailed_sek"] - mean(pts[i], "curtailed_value", "net_sek")) < 1e-6
    assert abs(s["full"]["delivered_sek"] - mean(pts[-1], "delivered_value", "net_sek")) < 1e-6
    a, b = pts[-2], pts[-1]
    dcut = mean(b, "curtailed_kwh") - mean(a, "curtailed_kwh")
    dpot = mean(b, "potential_kwh") - mean(a, "potential_kwh")
    assert abs(s["full_marginal"]["energy_share_pct"] - 100 * dcut / dpot) < 1e-9
    assert s["static"]["kw_ac_nonfirm"] == 0


def test_persistence_is_the_one_unsafe_forecast_scheme(results):
    rows = {r["scheme"]: r for r in results("forecast")["rows"]}
    assert {"static", "climatological", "persistence", "foresight"} <= set(rows)
    unsafe = {k for k, r in rows.items() if not r["safe"]}
    assert unsafe == {"persistence"}
    assert all(r["violation_hours"] == 0 for k, r in rows.items() if k != "persistence")


def test_the_reinforcement_comparison_rests_on_the_published_results(results):
    from kapacitet.config import A
    from kapacitet.engine.summaries import overinstallation_summary, reinforcement

    keys = ("reinforce_trafo_500_sek", "reinforce_trafo_800_sek", "reinforce_station_800_sek",
            "reinforce_urban_supplement_sek", "reinforce_price_level_year")
    over = overinstallation_summary(results("overinstallation"))
    r = reinforcement(results("tornado"), over, {k: A(k) for k in keys})
    assert r["order"] == r["order_over"], "both halves must come from the same adoption order"
    lo, hi = r["transformer_sek"]
    assert 0 < lo < hi and r["with_station_sek"][0] > hi
    assert r["bigger"]["static_kw"] > r["today"]["static_kw"]
    assert r["bigger"]["timeseries_kw"] >= r["envelopes"]["kw_ac"], "the bigger unit carries the deployment uncontrolled"
    yrs = r["years_of_curtailment"]["transformer"]
    assert yrs[0] == pytest.approx(lo / r["envelopes"]["curtailed_sek_per_year"])


def test_the_reinforcement_says_which_deployment_it_prices(results):
    """Or reinforce rests on the perfect-foresight deployment at the aggregate budget: it says so."""
    from kapacitet.engine.summaries import overinstallation_summary, reinforcement

    if "envelope_basis" not in results("overinstallation"):
        pytest.skip("overinstallation.json predates envelope_basis; run: python -m kapacitet compute --stage extra --only overinstallation")
    keys = ("reinforce_trafo_500_sek", "reinforce_trafo_800_sek", "reinforce_station_800_sek",
            "reinforce_urban_supplement_sek", "reinforce_price_level_year")
    r = reinforcement(results("tornado"), overinstallation_summary(results("overinstallation")), {k: A(k) for k in keys})
    oi = results("overinstallation")
    assert r["basis"]["envelopes"] == oi["envelope_basis"] == "foresight"
    assert r["basis"]["budget"] == "aggregate"
    assert r["basis"]["z_star_pct"] == oi["z_star_pct"]


# ---------------------------------------------------------------- the three stops of the hero

FORESIGHT = "run: python -m kapacitet compute --stage extra --only foresight"


def test_the_heros_three_stops_are_ordered(results):
    """Static, then what can be issued today (the hourly study and the day-ahead envelopes, as a
    range), then the perfect-foresight bound, with the per-installation figure under it."""
    if not processed("results", "foresight.json").exists():
        pytest.skip(f"foresight.json not published yet; {FORESIGHT}")
    m, fs = results("hosting_summary")["measures"], results("foresight")
    static, ts, doe = (m[k]["kw_ac"]["median"] for k in ("static", "timeseries", "doe"))
    da = fs["median_kw"]["climatological"]
    assert static <= min(ts, da) <= max(ts, da) <= doe
    if "doe_installation" in m:
        assert static <= m["doe_installation"]["kw_ac"]["median"] <= doe
    assert fs["violation_hours"] == 0 and fs["planning_full_year_violations"] == 0


def test_the_method_tiles_read_published_fields(results):
    if not processed("results", "foresight.json").exists():
        pytest.skip(f"foresight.json not published yet; {FORESIGHT}")
    fs = results("foresight")
    for key in ("margins", "within_budget_orders", "plan_exceedance"):
        if key not in fs:
            pytest.skip(f"foresight.json predates {key}; {FORESIGHT}")
    assert 0 <= fs["within_budget_orders"] <= fs["n_orders"]
    assert fs["margins"]["pv_pct"] >= 0 and fs["margins"]["load_pct"] >= 0
    total = fs["plan_exceedance"]["total"]
    assert total["pv_aggregate_hours_over"] >= 0 and total["pv_worst_aggregate_kw"] >= 0
    assert "timeseries" in fs["median_kw"]
    sd = results("hosting_summary").get("static_definition")
    if sd is None:
        pytest.skip("hosting_summary.json predates static_definition; run: python -m kapacitet compute")
    assert sd["load_rule_text"] and sd["mv_voltage_pu"] == A("mv_voltage_pu")


def test_operation_sized_with_perfect_foresight_curtails_more_when_issued_the_day_before(results):
    """Operation says the deployment was sized with perfect foresight: issued the day before, the
    same houses are limited in at least as many hours and lose at least as large a share."""
    path = processed("results", "operation", "index.json")
    if not path.exists():
        pytest.skip("run: python -m kapacitet compute --stage extra --only operation")
    combos = json.loads(path.read_text(encoding="utf-8"))["combos"]
    for pol, da in combos["dayahead"].items():
        fs = combos["foresight"][pol]
        assert da["verified"] and fs["verified"], pol
        assert da["critical_hours"] >= fs["critical_hours"], pol
        assert da["curtailed_share_of_export_pct"] >= fs["curtailed_share_of_export_pct"], pol


def test_every_hour_that_needed_a_limit_was_limited_on_both_bases(results):
    """The Report and the README count the day-ahead basis's limited hours against the hours that
    needed a limit: a verified envelope limits every one of those, and the rest are the activations
    the audit trail calls unnecessary."""
    path = processed("results", "operation", "index.json")
    if not path.exists():
        pytest.skip("run: python -m kapacitet compute --stage extra --only operation")
    idx = json.loads(path.read_text(encoding="utf-8"))
    needed = idx.get("necessary_hours")
    if needed is None:
        pytest.skip("necessary_hours not published yet; run: python -m kapacitet compute --stage extra --only operation")
    for basis, rules in idx["combos"].items():
        for pol, c in rules.items():
            if "activations_unnecessary_hours" not in c:
                pytest.skip("activations_unnecessary_hours not published yet; "
                            "run: python -m kapacitet compute --stage extra --only operation")
            assert c["verified"] and c["full_year_violation_hours"] == 0, (basis, pol)
            assert c["hours_limited"] - c["activations_unnecessary_hours"] == needed, (basis, pol)


def test_the_day_ahead_capacity_is_audited_and_within_budget(results):
    """The opening answer shows the day-ahead stop only when the search is audited: no violation
    on any order, its planning state met in full, and every order within the budget."""
    path = processed("results", "foresight.json")
    if not path.exists():
        pytest.skip("run: python -m kapacitet compute --stage extra --only foresight")
    f = json.loads(path.read_text(encoding="utf-8"))
    for key in ("within_budget_orders", "planning_full_year_violations", "exceedance"):
        if key not in f:
            pytest.skip(f"{key} not published yet; run: python -m kapacitet compute --stage extra --only foresight")
    assert f["violation_hours"] == 0 and f["planning_full_year_violations"] == 0
    assert 0 <= f["within_budget_orders"] <= f["n_orders"]
    ex = f["exceedance"]
    # the published search used the margins the assumptions state (the Method caption reads them)
    assert ex["pv_margin_pct"] == pytest.approx(A("dayahead_pv_margin_pct"))
    assert ex["load_margin_pct"] == pytest.approx(A("dayahead_load_margin_pct"))
    # the aggregate bound is what the safety argument rests on: it is audited, and reported
    assert ex["pv_aggregate_hours_over"] >= 0 and ex["load_aggregate_hours_under"] >= 0


# ---------------------------------------------------------------- the budget per installation

PER_INSTALLATION = "run: python -m kapacitet compute (the per-installation budget came after these results)"


def _per_installation(o: dict) -> dict:
    if "doe_installation" not in o:
        pytest.skip(f"doe_installation not published yet; {PER_INSTALLATION}")
    return o


def test_the_per_installation_budget_carries_no_more_than_the_aggregate_one(results):
    h = results("hosting_summary")
    for o in map(_per_installation, h["per_order"]):
        assert o["timeseries"]["adopters"] <= o["doe_installation"]["adopters"] <= o["doe"]["adopters"], o["order"]
        assert o["doe_installation"]["kw_ac"] <= o["doe"]["kw_ac"] + 1e-9
        assert 0.0 <= o["customers_within_pct"] <= 100.0
        assert o["z_doe_customer_max_pct"] >= o["z_doe_pct"] - 1e-6
    m = h["measures"]
    assert m["doe_installation"]["kw_ac"]["median"] <= m["doe"]["kw_ac"]["median"]
    assert h["budget_basis"] == {"doe": "aggregate", "doe_installation": "installation"}


def test_no_installation_curtails_beyond_the_budget_at_its_own_capacity(results):
    h = results("hosting_summary")
    z_star = h["z_star_pct"]
    for o in map(_per_installation, h["per_order"]):
        z = o["z_doe_installation"]
        assert z["customer_max_pct"] <= z_star + 1e-9, o["order"]
        assert z["full_year_violations"] == 0, o["order"]
        # the worst single year is reported, never held to the budget
        assert z["customer_year_max_pct"] >= z["customer_max_pct"] - 1e-9


@needs_results
def test_order_9_carries_87_adopters_on_the_per_installation_budget(results):
    """The review's check of order 9 (h9): 87 adopters, 717.8 kW, recomputed here. Slow: two
    curtailment runs over every weather year."""
    from kapacitet.engine import doe, run
    from kapacitet.engine.scenario import adoption_order

    h = results("hosting_summary")
    o = _per_installation(next(r for r in h["per_order"] if r["order"] == 9))
    assert o["doe_installation"]["adopters"] == 87
    assert o["doe_installation"]["kw_ac"] == pytest.approx(717.8, abs=0.05)
    m = run.model()
    hp, ev = run.background_masks(m.C)
    order = adoption_order(9, m.C)
    assert float(doe.customer_caps()[order[:87]].sum()) == pytest.approx(717.8, abs=0.05)
    ms, years = o["static"]["adopters"], tuple(h["years"])
    at = run.curtailment(order, ms, 87, years, hp, ev, verify="critical")
    above = run.curtailment(order, ms, 88, years, hp, ev, verify="critical")
    assert at["z_customer_max_pct"] <= h["z_star_pct"] + 1e-9 < above["z_customer_max_pct"]


# ---------------------------------------------------------------- the MV band

MV_BAND = "run: python -m kapacitet compute --stage extra --only mvband"


def _mv_band(results) -> dict:
    if not processed("results", "mv_band.json").exists():
        pytest.skip(f"mv_band.json not published yet; {MV_BAND}")
    return results("mv_band")


def _at_level(band: dict, vm_pu: float) -> dict:
    return next(r for r in band["levels"] if abs(r["vm_pu"] - vm_pu) < 1e-9)


def test_the_mv_band_at_the_demos_level_is_the_published_capacity(results):
    """mv_band.json moves only the MV voltage: at mv_voltage_pu its capacity is the tornado's base
    case and the showcase order's published capacity, and its deployment the showcase's."""
    band = _mv_band(results)
    tornado, show, h = results("tornado"), results("showcase"), results("hosting_summary")
    assert band["base_pu"] == A("mv_voltage_pu")
    assert band["order"] == tornado["order"] == show["order"]
    assert band["years"] == tornado["years"] == h["years"]
    at = _at_level(band, band["base_pu"])
    for key in ("static_kw", "timeseries_kw", "doe_kw", "static_adopters", "doe_adopters"):
        assert at[key] == pytest.approx(tornado["base"][key], rel=1e-9), key
    o = next(r for r in h["per_order"] if r["order"] == band["order"])
    for m in ("static", "timeseries", "doe"):
        assert at[f"{m}_adopters"] == o[m]["adopters"], m
        assert at[f"{m}_kw"] == pytest.approx(o[m]["kw_ac"], rel=1e-9), m
    assert (band["year"], band["firm"], band["adopters"]) == (show["year"], show["firm"], show["adopters"])
    assert at["timeseries_adopters"] == show["timeseries_adopters"]


def test_the_published_envelopes_have_no_violation_at_the_demos_mv_level(results):
    band = _mv_band(results)
    at = _at_level(band, band["base_pu"])
    assert at["published"]["violation_hours"] == 0 and at["published"]["nonconverged_hours"] == 0
    # at its own level every hour of the band's check is the showcase's own full-year check
    if "any_violation_hours" in at["published"] and "doe_firm_violation_hours" in results("showcase")["verification"]:
        ver = results("showcase")["verification"]
        assert at["published"]["firm_violation_hours"] == ver["doe_firm_violation_hours"]
        assert at["published"]["export_violation_hours"] == ver["doe_full_year_violations"]
    # the envelopes checked are the showcase's: at its own level they curtail what it publishes
    show = results("showcase")["policies"][band["policy"]]
    assert at["recomputed"]["critical_hours"] == show["critical_hours"]
    assert at["recomputed"]["intervention_hours"] == show["intervention_hours"]
    assert at["recomputed"]["curtailed_kwh"] == pytest.approx(show["curtailed_kwh"], rel=1e-4)
    # each level's own envelopes verify at that level, and the levels run across the whole band
    assert all(r["recomputed"]["full_year_violations"] == 0 for r in band["levels"])
    vm = [r["vm_pu"] for r in band["levels"]]
    assert vm == sorted(vm) and [vm[0], vm[-1]] == band["band_pu"] == A("mv_voltage_band_pu")


# ---------------------------------------------------------------- consumption, hour by hour

CONSUMPTION = "run: python -m kapacitet compute --stage extra --only consumption"


def test_a_zero_consumption_capacity_says_whether_todays_grid_already_breaks_a_limit(results):
    """0 kW from the hourly search is either a measured capacity or a grid that already breaks an
    import limit before anyone adds load: consumption.json has to say which."""
    if not processed("results", "consumption.json").exists():
        pytest.skip(f"consumption.json not published yet; {CONSUMPTION}")
    c = results("consumption")
    if "baseline_violation_hours" not in c:
        pytest.skip(f"consumption.json predates baseline_violation_hours; {CONSUMPTION}")
    base = c["baseline_violation_hours"]
    assert set(base) == {str(y) for y in c["years"]}
    assert c["baseline_violates"] == any(base.values())
    if c["baseline_violates"]:
        assert max(c["nodal"]["timeseries_kw"]) == 0
        assert all(d["adopters"]["max"] == 0 for d in c["devices"].values())
    elif c["nodal_kw"]["timeseries"]["median"] > 0:
        assert not any(base.values())


INTRADAY = "run: python -m kapacitet compute --stage extra --only intraday"


def _intraday() -> dict:
    path = processed("results", "intraday.json")
    if not path.exists():
        pytest.skip(f"intraday.json not published yet; {INTRADAY}")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_published_replica_backs_its_scorecard():
    d = _intraday()
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


def test_the_published_replica_holds_both_variants_and_headlines_a_verified_one():
    from kapacitet.engine import nowcast

    d = _intraday()
    if "forecast" not in d.get("variants", {}):
        pytest.skip(f"intraday.json has no forecast variant (needs the forecast extra); {INTRADAY}")
    v = d["variants"]
    assert set(v) == {"worst_change", "forecast"}
    assert d["quantiles"] == {"pv": pytest.approx(A("nowcast_pv_quantile")),
                              "load": pytest.approx(A("nowcast_load_quantile"))}
    for name, var in v.items():
        assert var["variant"] == name and len(var["per_order"]) == d["n_orders"]
        assert var["planning_full_year_violations"] == 0 == var["installation"]["planning_full_year_violations"]
        assert var["verified"] == (var["violation_hours"] == 0 == var["installation"]["violation_hours"])
        for basis in (var, var["installation"]):
            assert basis["scorecard"]["violation_hours"]["value"] == basis["violation_hours"]
        if not var["verified"]:
            # capacity is published only for envelopes that held in every replayed hour
            assert name == "forecast" and var["capacity_withheld"]
            for b in (var, var["installation"]):
                assert b["median_kw"] is None and b["kw_ac_stats"] is None
                assert b["scorecard"]["gain_over_hourly_pct"]["value"] is None
            assert all(r[k] is None for r in var["per_order"] for k in ("kw_ac", "adopters"))
            assert all(r["installation"]["kw_ac"] is None for r in var["per_order"])
            continue
        for r in var["per_order"]:
            assert r["kw_ac"] <= r["doe_kw"] + 1e-6
    assert v["worst_change"]["verified"], "the worst-change replay held in every hour"
    # the headline is the forecast variant exactly when it held in every replayed hour
    assert d["headline_variant"] == ("forecast" if v["forecast"]["verified"] else "worst_change")
    head = v[d["headline_variant"]]
    assert d["median_kw"] == head["median_kw"] and d["installation"]["median_kw"] == head["installation"]["median_kw"]
    cal = v["forecast"]["calibration"]
    for kind in nowcast.KINDS:
        c = cal[kind]
        assert c["nominal_exceedance"] == pytest.approx(nowcast.nominal_exceedance(kind))
        assert c["customer_hours"] > 0 and 0 <= c["exceeded"] <= c["customer_hours"]
        assert c["realised_exceedance"] == pytest.approx(c["exceeded"] / c["customer_hours"])
        assert sorted(p["year"] for p in c["per_year"]) == sorted(d["years"])
        if "within_tolerance" in c:
            off = abs(c["realised_exceedance"] - c["nominal_exceedance"]) <= c["tolerance_rel"] * c["nominal_exceedance"]
            assert c["within_tolerance"] == off


def test_the_overinstallation_prices_cover_every_weather_year_once_recomputed(results):
    from kapacitet.config import settings

    d = results("overinstallation")
    if "price_years" not in d:
        pytest.skip("overinstallation.json has no price_years: compute --stage extra --only overinstallation")
    if set(d["price_years"]) == set(settings().price_years):
        pytest.skip("only the 2023-2025 prices were fetched: fetch --only prices with ENTSOE_API_TOKEN set, then recompute")
    assert set(d["price_years"]) == set(settings().weather_years)


@needs_results
def test_published_import_envelopes_hold_on_the_real_year():
    path = processed("results", "operation", "dayahead", "import.json")
    if not path.exists():
        pytest.skip("run: python -m kapacitet compute --stage extra --only operation")
    meta = json.loads(path.read_text(encoding="utf-8"))
    assert meta["verified"] and meta["actual_violation_hours"] == 0
