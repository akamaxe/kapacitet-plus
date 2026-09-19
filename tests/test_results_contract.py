"""The published results must back the claims the interface makes about them."""

from __future__ import annotations

import json

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


def test_every_assumption_is_labelled_and_sourced():
    from kapacitet.config import assumptions

    assert len(assumptions()) > 20
    for key, a in assumptions().items():
        assert a.tag, f"{key} has no tag"
        assert a.source, f"{key} has no source"
        assert a.label, f"{key} has no label"
        if a.tag in ("sourced", "regulation", "library"):
            assert a.url, f"{key} is tagged {a.tag} but has no link"


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
