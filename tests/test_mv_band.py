"""The MV band (run_extra.mv_band, results/mv_band.json): the showcase order across the planning
band of the MV voltage, while the demo's envelopes stay at mv_voltage_pu.

The levels and the tornado's MV ends come from config/assumptions.toml; the stage itself runs on
the tiny grid with its synthetic profiles, in this process (workers=1). The contract on the
published file is in test_results_contract.py.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from conftest import synthetic_profiles, tiny_grid

CAPACITY = ("static_kw", "timeseries_kw", "doe_kw")


@pytest.fixture
def overridden():
    from kapacitet.config import set_overrides

    yield set_overrides
    set_overrides(None)


def test_the_levels_span_the_band_through_the_demos_own_level(overridden):
    from kapacitet.config import A
    from kapacitet.engine import run_extra

    assert A("mv_voltage_pu") == 1.00 and A("mv_voltage_band_pu") == [0.98, 1.06]
    assert run_extra.mv_band_levels() == [0.98, 1.00, 1.02, 1.04, 1.06]
    # the tornado's MV case is the band's two ends, and nothing else in it moves the MV
    mv = [case for case in run_extra.tornado_cases() if case[3].get("vm_slack") is not None]
    assert [(case[2], case[3]["vm_slack"]) for case in mv] == [("0.98 pu", 0.98), ("1.06 pu", 1.06)]
    assert all(case[0] == "mv_voltage" for case in mv)

    # a band whose ends are off the step keeps them, and still passes through the demo's level
    overridden({"mv_voltage_band_pu": [0.97, 1.05]})
    assert run_extra.mv_band_levels() == [0.97, 0.98, 1.00, 1.02, 1.04, 1.05]
    overridden({"mv_voltage_band_pu": [1.01, 1.06]})
    with pytest.raises(ValueError, match="outside"):
        run_extra.mv_band_levels()


@pytest.fixture(scope="module")
def band(tiny_processed):
    """mv_band.json for adoption order 0 of the tiny grid, with the deployment its capacity search
    settles on (run.analyze_order) as the showcase's: the stage's answer, what it wrote, and the
    order's own record. The file is removed after reading, so the shared folder stays as it was;
    the engine reads the tiny grid until the module's last test has run."""
    from kapacitet.config import A
    from kapacitet.engine import run, run_extra
    from kapacitet.paths import processed

    with tiny_grid(tiny_processed):
        z_star = A("z_star_pct")
        order = run.analyze_order((0, synthetic_profiles.YEARS, z_star))
        ms, k = order["static"]["adopters"], order["doe"]["adopters"]
        out = run_extra.mv_band(0, ms, k, synthetic_profiles.YEARS, z_star, workers=1)
        path = processed("results", "mv_band.json")
        written = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
        yield {"out": out, "written": written, "order": order, "z_star": z_star}


def _level(out: dict, vm_pu: float) -> dict:
    return next(r for r in out["levels"] if abs(r["vm_pu"] - vm_pu) < 1e-9)


def test_the_stage_writes_what_it_returns(band):
    from kapacitet.engine.run import _default

    out = band["out"]
    assert band["written"] == json.loads(json.dumps(out, default=_default))
    assert [r["vm_pu"] for r in out["levels"]] == [0.98, 1.00, 1.02, 1.04, 1.06]
    assert out["band_pu"] == [0.98, 1.06] and out["base_pu"] == 1.00
    assert (out["order"], out["years"], out["policy"]) == (0, list(synthetic_profiles.YEARS), "maxmin")
    assert out["year"] == synthetic_profiles.YEARS[-1]


def test_capacity_rises_while_a_rating_binds_and_falls_once_the_voltage_does(band):
    """The same power is less current at a higher voltage, so while the transformer or a cable
    binds, a capacity can only rise with the MV voltage, and once the voltage limit binds it can
    only fall: across the band each one is monotone on either side of its peak. On the tiny grid
    the 0.1 MVA transformer binds the hourly capacity at the bottom of the band, and the long
    feeder the voltage from the demo's level up, so there all three only fall."""
    out = band["out"]
    levels = out["levels"]
    base = [r["vm_pu"] for r in levels].index(out["base_pu"])
    for key in CAPACITY:
        v = np.array([r[key] for r in levels])
        peak = int(np.argmax(v))
        assert (np.diff(v[:peak + 1]) >= -1e-9).all() and (np.diff(v[peak:]) <= 1e-9).all(), (key, v)
        assert (np.diff(v[base:]) <= 1e-9).all() and v[-1] < v[base], (key, v)
    for r in levels:
        assert r["static_adopters"] <= r["timeseries_adopters"] <= r["doe_adopters"], r["vm_pu"]
        assert r["static_kw"] <= r["timeseries_kw"] <= r["doe_kw"], r["vm_pu"]


def test_the_demos_level_is_the_order_and_the_tornado_base(band):
    """The capacity at the demo's own level is the one published for the order (analyze_order,
    hosting_summary.json) and the tornado's base case: the same searches on the same model."""
    from kapacitet.engine import run_extra

    out, order = band["out"], band["order"]
    at = _level(out, out["base_pu"])
    for measure in ("static", "timeseries", "doe"):
        assert at[f"{measure}_adopters"] == order[measure]["adopters"], measure
        assert at[f"{measure}_kw"] == pytest.approx(order[measure]["kw_ac"], rel=1e-12), measure
    assert (out["firm"], out["adopters"]) == (order["static"]["adopters"], order["doe"]["adopters"])
    base = run_extra._tornado_case((("base", "Base case", "base", {}, {}), 0, synthetic_profiles.YEARS, band["z_star"]))
    assert {key: base[key] for key in run_extra._MV_CAPACITY} == {key: at[key] for key in run_extra._MV_CAPACITY}


def test_the_published_envelopes_hold_at_the_demos_level_only(band):
    """The envelopes computed at mv_voltage_pu verify there, and at the top of the band they break
    the voltage limit the tiny grid's long feeder binds; envelopes computed at each level verify
    at that level, at a cost in curtailment."""
    from kapacitet.config import A

    out = band["out"]
    at, top = _level(out, out["base_pu"]), out["levels"][-1]
    assert at["published"]["violation_hours"] == 0 and at["published"]["nonconverged_hours"] == 0
    assert at["published"]["hours_checked"] > 0
    assert top["published"]["violation_hours"] > 0
    assert top["published"]["max_voltage_pu"] > A("v_max_pu") - A("v_margin_pu")
    for r in out["levels"]:
        rc = r["recomputed"]
        assert rc["full_year_violations"] == 0, r["vm_pu"]
        assert r["published"]["nonconverged_hours"] == 0, r["vm_pu"]
        # what the firm customers alone break is the same whichever envelopes the others hold
        assert rc["firm_violation_hours"] == r["published"]["firm_violation_hours"], r["vm_pu"]
        # every hour of the year is solved, and every violating one counted, on either side
        pub = r["published"]
        assert pub["hours_checked"] == at["published"]["hours_checked"] >= 8760, r["vm_pu"]
        assert pub["violation_hours"] + pub["firm_violation_hours"] <= pub["export_violation_hours"], r["vm_pu"]
        assert pub["any_violation_hours"] >= max(pub["export_violation_hours"], pub["import_violation_hours"])
        assert rc["potential_kwh"] == pytest.approx(at["recomputed"]["potential_kwh"])
        assert 0.0 <= rc["z_pct"] <= rc["max_customer_z_pct"] + 1e-9 <= 100.0 + 1e-9
    assert top["recomputed"]["z_pct"] > at["recomputed"]["z_pct"]
    assert top["recomputed"]["critical_hours"] > at["recomputed"]["critical_hours"]
