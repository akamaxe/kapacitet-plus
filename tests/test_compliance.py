"""The audit trail and the annual report an operator files.

The trail is evidence, so the tests are about whether it would survive being questioned:
does every row name a real component, does the loading it cites actually exceed the rating,
and do the totals match the result it was derived from.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from conftest import needs_results

from kapacitet.config import A
from kapacitet.engine import compliance, doe
from kapacitet.engine.netmodel import get_model
from kapacitet.engine.scenario import Scenario, adoption_order, mask_from_share
from kapacitet.paths import processed


@pytest.fixture(scope="module")
def case():
    """The published showcase year, recomputed so the trail has a result to describe."""
    show = json.loads(processed("results", "showcase.json").read_text(encoding="utf-8"))
    model = get_model()
    pv = np.zeros(model.C, dtype=bool)
    pv[show["pv_cids"]] = True
    hp = mask_from_share(adoption_order(101, model.C), A("today_hp_share"))
    ev = mask_from_share(adoption_order(202, model.C), A("today_ev_share"))
    arrays = Scenario(year=show["year"], pv=pv, hp=hp, ev=ev).arrays()
    result = doe.compute(model, arrays, np.array(show["nonfirm_cids"]), policy="maxmin")
    trail = compliance.audit_trail(model, result, arrays, show["year"])
    return model, result, trail, compliance.annual_report(trail, result), show


@needs_results
def test_the_engine_records_which_limit_bound_each_hour(case):
    _, result, _, _, _ = case
    assert result.binding is not None and len(result.binding) == len(result.hours)
    assert set(np.unique(result.binding)) <= {0, 1, 2}
    # on this grid the transformer is the binding component, not voltage or the cables
    assert (result.binding == 2).all()


@needs_results
def test_the_trail_accounts_for_every_curtailed_kilowatt_hour(case):
    _, result, trail, _, _ = case
    # The trail is written to 1 Wh and drops anything below that, so its total trails the
    # computed one slightly. A tenth of a per cent is the bound worth holding it to.
    assert np.isclose(trail.energy_kwh, float(result.curtailed.sum()), rtol=1e-3)
    assert trail.energy_kwh <= float(result.curtailed.sum()) + 1e-6, "the trail never overstates"
    assert trail.hours_with_curtailment == len(result.hours)
    assert (trail.events["curtailed_kwh"] > 0).all()
    assert (trail.events["granted_kw"] <= trail.events["desired_kw"] + 1e-6).all()


@needs_results
def test_every_event_names_a_component_that_was_actually_out_of_room(case):
    """The claim that curtailment was a last resort has to hold row by row."""
    _, _, trail, _, _ = case
    assert trail.events["binding_constraint"].isin(["voltage", "cable", "transformer"]).all()
    thermal = trail.events[trail.events["binding_constraint"] != "voltage"]
    assert len(thermal), "this case is thermally bound, so there is something to check"
    # without the envelopes that component would have been past its rating
    assert (thermal["uncontrolled_pct"] > thermal["rating_pct"]).all()


@needs_results
def test_the_report_totals_match_the_trail(case):
    _, result, trail, report, _ = case
    assert report["hours_with_curtailment"] == trail.hours_with_curtailment
    assert np.isclose(report["curtailed_kwh"], trail.energy_kwh, atol=0.1)
    assert report["conditional_agreements"] == len(result.nonfirm)
    assert sum(report["binding_constraint_hours"].values()) == trail.hours_with_curtailment
    per = sum(r["curtailed_kwh"] for r in report["per_customer"])
    assert np.isclose(per, report["curtailed_kwh"], rtol=1e-3)


@needs_results
def test_the_report_states_the_verification_and_the_limits(case):
    """A filing that does not say what was checked is not worth filing."""
    _, _, _, report, _ = case
    assert report["verified_violations"] == 0
    assert report["voltage_limits_pu"] == [A("v_min_pu"), A("v_max_pu")]
    assert report["thermal_limit_pct"] == A("thermal_limit_pct")
    assert report["allocation_policy"] == "maxmin"
    assert report["worst_uncontrolled"]["loading_pct"] > report["thermal_limit_pct"]


@needs_results
def test_the_share_is_of_export_not_of_production(case):
    """What an envelope limits is the export; calling it production would overstate nothing
    but would describe the wrong quantity."""
    _, result, _, report, _ = case
    exported = float(result.desired.sum())
    assert np.isclose(report["non_firm_export_kwh"], exported, rtol=1e-6)
    assert all("own_export_kwh" in r for r in report["per_customer"])


@needs_results
def test_the_text_filing_carries_the_numbers(case):
    _, _, _, report, _ = case
    text = compliance.report_text(report)
    assert str(report["hours_with_curtailment"]) in text
    assert "last resort" in text
    assert "nonlinear power flow" in text
    assert "transformer" in text


@needs_results
def test_a_year_with_no_curtailment_still_reports(case):
    """An operator with nothing to declare has to be able to declare nothing."""
    model, result, _, _, show = case
    empty = doe.DoeResult(nonfirm=result.nonfirm, hours=np.zeros(0, dtype=int),
                          E=np.zeros((len(result.nonfirm), 0)), desired=result.desired,
                          cap=result.cap, curtailed=np.zeros_like(result.curtailed),
                          binding=np.zeros(0, dtype=np.int8),
                          binding_line=np.zeros(0, dtype=np.int32),
                          info={"policy": "maxmin"})
    trail = compliance.Trail(compliance._empty_events(), show["year"], "maxmin")
    report = compliance.annual_report(trail, empty)
    assert report["hours_with_curtailment"] == 0
    assert report["curtailed_kwh"] == 0
    assert report["binding_constraint_hours"] == {}
    assert "0 of 8760" in compliance.report_text(report)


def _day_ahead_report():
    """A three-hour filing without data: the transformer bound two hours, voltage one, and
    one of the transformer's hours turned out to need nothing."""
    import pandas as pd

    events = pd.DataFrame({
        "hour": [10, 10, 11, 12], "timestamp": ["t10", "t10", "t11", "t12"], "customer_id": [7, 8, 7, 8],
        "desired_kw": [5.0, 4.0, 5.0, 4.0], "granted_kw": [4.0, 3.0, 4.5, 3.5],
        "curtailed_kwh": [1.0, 1.0, 0.5, 0.5],
        "binding_constraint": ["transformer", "transformer", "transformer", "voltage"],
        "binding_element": ["transformer", "transformer", "transformer", "voltage"],
        "uncontrolled_pct": [104.0, 104.0, 99.0, np.nan], "rating_pct": 100.0,
        "necessary": [True, True, False, True],
    })
    H = 24
    desired = np.zeros((2, H))
    desired[:, 10:13] = [[5.0, 5.0, 0.0], [4.0, 0.0, 4.0]]
    curtailed = np.zeros((2, H))
    curtailed[:, 10:13] = [[1.0, 0.5, 0.0], [1.0, 0.0, 0.5]]
    result = doe.DoeResult(nonfirm=np.array([0, 1]), hours=np.array([10, 11, 12]), E=np.zeros((2, 3)),
                           desired=desired, cap=np.full(2, 5.0), curtailed=curtailed,
                           binding=np.array([2, 2, 0], dtype=np.int8), binding_line=np.full(3, -1),
                           info={"policy": "maxmin"})
    trail = compliance.Trail(events, 2025, "maxmin")
    return compliance.annual_report(trail, result, cids=np.array([7, 8]), basis="dayahead")


def test_the_filing_names_the_component_of_each_needed_hour():
    report = _day_ahead_report()
    assert report["binding_constraint_hours"] == {"transformer": 2, "voltage": 1}
    assert report["binding_constraint_necessary_hours"] == {"transformer": 1, "voltage": 1}
    assert sum(report["binding_constraint_necessary_hours"].values()) == report["activations_necessary_hours"] == 2
    assert report["activations_unnecessary_hours"] == 1
    text = compliance.report_text(report)
    assert "transformer    2 hours, 1 of them needed" in text
    assert "voltage        1 hours, 1 of them needed" in text
    assert "last resort in those 2 hours only" in text


def test_a_filing_whose_every_hour_was_needed_lists_those_hours():
    report = _day_ahead_report()
    report.update(activations_unnecessary_hours=0, activations_necessary_hours=3,
                  binding_constraint_necessary_hours={"transformer": 2, "voltage": 1})
    text = compliance.report_text(report)
    assert "last resort: in every hour above" in text
    assert "transformer    2 hours" in text and "needed" not in text
