"""A home battery in a connection request stores only curtailed solar and never exports it."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import needs_results

pytestmark = needs_results


@pytest.fixture(scope="module")
def answers():
    from kapacitet.engine.run import background_masks, model
    from kapacitet.engine import request as rq

    m = model()
    hp, ev = background_masks(m.C)
    # the house the published year curtails most, where a battery has something to store
    req = dict(cid=84, pv_kwp=20.0, baseline="doe")
    plain, _ = rq.run(m, rq.Request(**req), hp, ev)
    with_bat, detail = rq.run(m, rq.Request(**req, bat_kwh=10.0), hp, ev)
    return plain, with_bat, detail, rq.battery_params(10.0)


def test_the_battery_leaves_the_export_and_the_envelope_untouched(answers):
    plain, bat, _, _ = answers
    a, b = plain["profiles"], bat["profiles"]
    assert a["envelope_kw"] == b["envelope_kw"]
    assert a["curtailed_kw"] == b["curtailed_kw"]
    assert plain["summary"]["pv_curtailment_ref_pct"] == bat["summary"]["pv_curtailment_ref_pct"]


def test_it_charges_only_from_curtailed_energy_and_discharges_only_into_the_house(answers):
    _, bat, detail, bp = answers
    for d in detail.values():
        assert (d["bat_ch"] <= d["curt"] + 1e-9).all()
        need = np.maximum(-(d["pv"] - d["load"] - d["hp_s"] - d["ev_s"]), 0)
        assert (d["bat_dis"] <= need + 1e-9).all()
        assert (d["bat_dis"] <= bp["kw"] + 1e-9).all() and (d["bat_ch"] <= bp["kw"] + 1e-9).all()
        assert (d["bat_soc"] >= -1e-9).all() and (d["bat_soc"] <= bp["kwh"] + 1e-9).all()
        assert not ((d["bat_ch"] > 1e-9) & (d["bat_dis"] > 1e-9)).any()


def test_its_energy_balances_with_the_measured_efficiencies(answers):
    _, _, detail, bp = answers
    for d in detail.values():
        stored = d["bat_ch"].sum() * bp["eta_in"] - d["bat_dis"].sum() / bp["eta_out"]
        assert abs(stored - d["bat_soc"][-1]) < 1e-6
    assert 0.8 < bp["eta_in"] * bp["eta_out"] < 0.9          # Orth et al. (2023): ~0.86 round trip


def test_its_discharge_hours_are_verified_and_its_value_reported(answers):
    _, bat, _, _ = answers
    v = bat["verification"]["battery"]
    assert v["violations_after"] == 0 and v["hours_checked"] > 0
    s = bat["summary"]
    assert 0 < s["bat_captured_ref_pct"] <= 100 + 1e-9
    assert s["curtailment_after_battery_ref_pct"] <= bat["summary"]["pv_curtailment_ref_pct"] + 1e-9
    assert bat["economics"]["battery_sek_mean"] >= 0
