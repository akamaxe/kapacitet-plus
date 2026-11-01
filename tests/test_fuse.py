"""The house's own limit: a request may not overload its main fuse on the import side.

Export is capped at the fuse already (an inverter is never sized above it); a charger and a heat
pump are not, so the request checks the busiest phase of the house's peak hour with them added.
The pure verdict runs anywhere; the evaluation test needs the computed data.
"""

from __future__ import annotations

import pytest

from conftest import needs_results

SIZES = [16, 20, 25, 35, 50, 63]


def test_fuse_verdict_flags_a_charger_and_heat_pump_over_a_16_a_fuse():
    from kapacitet.engine.request import fuse_verdict

    # 6 kVA of the house's own peak plus an 11 kW three-phase charger: 17 kVA over three phases
    v = fuse_verdict(6.0, 11.0, 0.0, 16, SIZES)
    assert not v["ok"]
    assert v["phase_current_a"] == pytest.approx(17000 / 3 / 230)
    assert v["fuse_needed_a"] == 25


def test_a_single_phase_charger_is_checked_against_the_phase_current():
    from kapacitet.engine.request import fuse_verdict

    # 3.7 kW on one phase on top of a third of 6 kVA: the busiest phase draws ~24.8 A
    v = fuse_verdict(6.0, 0.0, 3.7, 16, SIZES)
    assert not v["ok"] and v["fuse_needed_a"] == 25
    assert v["phase_current_a"] == pytest.approx((2.0 + 3.7) * 1000 / 230)
    # the same power spread over three phases fits the same fuse
    assert fuse_verdict(6.0, 3.7, 0.0, 16, SIZES)["ok"]


def test_a_fuse_that_holds_names_no_bigger_one():
    from kapacitet.engine.request import fuse_verdict

    v = fuse_verdict(3.0, 11.0, 0.0, 25, SIZES)
    assert v["ok"] and v["fuse_needed_a"] is None
    assert v["fuse_kva"] == pytest.approx(3 * 230 * 25 / 1000)


def test_beyond_the_largest_subscription_names_none():
    from kapacitet.engine.request import fuse_verdict

    v = fuse_verdict(30.0, 22.0, 0.0, 25, SIZES)
    assert not v["ok"] and v["fuse_needed_a"] is None and v["largest_a"] == 63


@needs_results
def test_an_evaluation_with_a_charger_reports_its_fuse_check():
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app

    with TestClient(app) as client:
        meta = client.get("/api/v1/meta").json()
        cid = int(meta["grid"]["customers"]) - 1
        r = client.post("/api/v1/evaluate", json={"cid": cid, "pv_kwp": 0.0, "ev_kw": 11.0, "baseline": "static"})
        assert r.status_code == 200, r.text
        body = r.json()
        fuse = body["fuse"]
        assert fuse["fuse_a"] == body["customer"]["fuse_a"]
        assert fuse["added_kw"] == pytest.approx(11.0) and fuse["single_phase_kw"] == 0.0
        assert fuse["house_peak_kva"] > 0
        assert body["summary"]["fuse_ok"] == fuse["ok"]
        # solar alone is capped at the fuse already, so it carries no import-side check
        r = client.post("/api/v1/evaluate", json={"cid": cid, "pv_kwp": 5.0, "baseline": "static"})
        assert "fuse" not in r.json()
