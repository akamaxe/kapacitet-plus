"""A heat pump in a connection request: static winter verdict, import envelope, verification."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import needs_results

pytestmark = needs_results


@pytest.fixture(scope="module")
def engine():
    from kapacitet.engine.run import background_masks, model

    m = model()
    hp, ev = background_masks(m.C)
    return m, hp, ev


def _house_without_hp(hp):
    return int(np.flatnonzero(~hp)[0])


def test_a_heat_pump_request_gets_a_winter_verdict_and_an_import_answer(engine):
    from kapacitet.engine import request as rq

    m, hp, ev = engine
    j = _house_without_hp(hp)
    out = rq.evaluate(m, rq.Request(cid=j, hp=True, baseline="doe"), hp, ev)
    st = out["static"]["hp"]
    assert st["reason_code"] in {"trafo", "under_v", "line", "ok_winter"}
    assert st["design_kw"] > 0
    for e in out["per_year"]:
        assert e["hp_kwh"] > 0
        assert 0 <= e["hp_unmet_kwh"] <= e["hp_kwh"] + 1e-6
    assert out["verification"]["import"]["violations_after"] == 0
    assert 0 <= out["summary"]["hp_unmet_pct"] <= 100
    prof = out["profiles"]
    assert len(prof["hp_kw"]) == len(prof["import_envelope_kw"])


def test_a_house_that_has_a_heat_pump_is_not_given_a_second(engine):
    from kapacitet.engine import request as rq

    m, hp, ev = engine
    j = int(np.flatnonzero(hp)[0])
    out = rq.evaluate(m, rq.Request(cid=j, pv_kwp=5.0, hp=True, baseline="static"), hp, ev)
    assert "hp" not in out["static"]
    assert "hp_kwh" not in out["per_year"][0]


def test_the_import_check_scales_down_whatever_the_power_flow_refuses(engine):
    """Force an import the grid cannot take; the verified answer must leave no violation behind."""
    from kapacitet.config import settings
    from kapacitet.engine import request as rq

    m, hp, ev = engine
    j = _house_without_hp(hp)
    y = settings().reference_year
    p0, q0 = rq.Context("static").pq(m, y, hp, ev)
    H = p0.shape[1]
    hrs = np.arange(4000, 4012)
    demand = np.zeros(H)
    demand[hrs] = 400.0                      # far beyond the transformer's rating
    d = {"pv": np.zeros(H), "load": np.zeros(H), "hp": demand, "ev_want": np.zeros(H),
         "head": np.full(H, 1000.0), "ev_s": np.zeros(H)}
    d["head"][hrs] = 380.0                   # an optimistic estimate, so the hours count as binding
    d["hp_s"] = np.minimum(d["hp"], d["head"])
    check = rq._verify_import(m, p0, q0, j, d)
    assert check["hours_checked"] == len(hrs)
    assert check["hours_tightened"] > 0
    assert check["violations_after"] == 0
    assert (d["hp_s"][hrs] < 380.0).all()


def test_the_api_refuses_a_heat_pump_for_a_house_that_has_one():
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app
    from kapacitet.engine.run import background_masks
    from kapacitet.engine.scenario import customers

    hp, _ = background_masks(len(customers()))
    j = int(np.flatnonzero(hp)[0])
    with TestClient(app) as c:
        r = c.post("/api/v1/evaluate", json={"cid": j, "pv_kwp": 0, "hp": True})
        assert r.status_code == 422 and "heat pump" in r.text
        r = c.post("/api/v1/evaluate", json={"cid": j, "pv_kwp": 0, "bat_kwh": 5})
        assert r.status_code == 422
