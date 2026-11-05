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


def test_every_published_power_flow_converged(results):
    ver = results("showcase")["verification"]
    assert _field(ver, "pf_nonconverged_hours") == 0
    assert ver["pf_max_mismatch"] <= 1e-8


def test_every_published_envelope_point_is_verified_in_every_hour(results):
    """Not only the critical hours the screen picked: doe.compute's full-year check, at every point
    the interface shows (each sharing rule, each adoption order, the curve, the risk cases)."""
    show = results("showcase")["policies"]
    assert all(_field(p, "full_year_violations") == 0 for p in show.values())
    assert all(_field(o, "doe_full_year_violations") == 0 for o in results("hosting_summary")["per_order"])
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
