"""Which parts of the product an installation shows (kapacitet/features.py, task 1.4 of the
technical review of 14-11-2026): the envelopes, the Sweden screen, the 2030 scenarios and the
applicant's portal are off unless KAPACITET_FEATURES names them, and a route of a part that is off
answers 404 `feature_off` rather than its data."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kapacitet import features


@pytest.fixture
def none_on(monkeypatch):
    monkeypatch.delenv("KAPACITET_FEATURES", raising=False)
    monkeypatch.delenv("KAPACITET_ENVELOPES", raising=False)


def test_every_part_is_off_by_default(none_on):
    assert features.enabled() == {"envelopes": False, "national": False, "scenarios": False, "portal": False}


def test_parts_are_named_or_all_and_the_envelopes_have_a_shortcut(monkeypatch, none_on):
    monkeypatch.setenv("KAPACITET_FEATURES", " Envelopes , portal ")
    assert features.enabled() == {"envelopes": True, "national": False, "scenarios": False, "portal": True}
    monkeypatch.setenv("KAPACITET_FEATURES", "all")
    assert all(features.enabled().values())
    monkeypatch.setenv("KAPACITET_FEATURES", "")
    monkeypatch.setenv("KAPACITET_ENVELOPES", "1")
    assert features.enabled()["envelopes"] and not features.enabled()["portal"]


def test_a_name_that_is_no_part_switches_nothing_on_and_is_reported(monkeypatch, none_on):
    monkeypatch.setenv("KAPACITET_FEATURES", "envelope,national")
    assert features.enabled() == {"envelopes": False, "national": True, "scenarios": False, "portal": False}
    assert features.unknown() == ["envelope"]
    with pytest.raises(KeyError):
        features.on("nonsense")


@pytest.fixture
def client():
    from kapacitet.api.main import app

    return TestClient(app)


GATED = [
    ("GET", "/api/v1/national", "national"),
    ("GET", "/api/v1/scenarios", "scenarios"),
    ("GET", "/api/v1/compliance", "envelopes"),
    ("GET", "/api/v1/redispatch", "envelopes"),
    ("GET", "/api/v1/business", "envelopes"),
    ("GET", "/api/v1/envelopes/0", "envelopes"),
    ("GET", "/api/v1/operation/index", "envelopes"),
    ("GET", "/api/v1/operation/shadow", "envelopes"),
    ("GET", "/api/v1/eipack", "envelopes"),
    ("GET", "/api/v1/state/doe", "envelopes"),
    ("GET", "/api/v1/apply/houses", "portal"),
    ("GET", "/api/v1/inbox", "portal"),
]


@pytest.mark.parametrize("method,path,part", GATED)
def test_a_route_of_a_part_that_is_off_says_so(client, none_on, method, path, part):
    r = client.request(method, path)
    assert r.status_code == 404, (path, r.status_code)
    assert r.json()["detail"] == {"code": "feature_off", "params": {"feature": part},
                                  "message": f"{part} is switched off on this installation (KAPACITET_FEATURES)"}


def test_the_server_says_which_parts_are_on(client, monkeypatch, none_on):
    monkeypatch.setenv("KAPACITET_FEATURES", "portal")
    assert client.get("/api/v1/health").json()["features"] == {
        "envelopes": False, "national": False, "scenarios": False, "portal": True}


def test_the_inputs_table_leaves_out_the_inputs_of_a_part_that_is_off(client, monkeypatch, none_on):
    keys = {a["key"] for a in client.get("/api/v1/assumptions").json()["assumptions"]}
    assert "z_star_pct" not in keys and "intraday_gates_h" not in keys and "scenario_pv_growth_per_year" not in keys
    assert "v_max_pu" in keys and "static_pv_factor" in keys
    monkeypatch.setenv("KAPACITET_FEATURES", "envelopes")
    keys = {a["key"] for a in client.get("/api/v1/assumptions").json()["assumptions"]}
    assert "z_star_pct" in keys and "scenario_pv_growth_per_year" not in keys


def test_a_request_cannot_ask_for_the_envelope_scenario_or_a_battery_where_the_envelopes_are_off(client, none_on):
    """A battery here stores the solar an envelope curtails, and the envelope scenario is a grid with
    envelopes: neither exists where the envelopes are off, and the refusal comes before any work."""
    for body in ({"cid": 0, "pv_kwp": 5.0, "baseline": "doe"}, {"cid": 0, "pv_kwp": 5.0, "bat_kwh": 5.0}):
        r = client.post("/api/v1/requests", json=body)
        assert r.status_code == 404 and r.json()["detail"]["code"] == "feature_off", (body, r.status_code)
        assert r.json()["detail"]["params"] == {"feature": "envelopes"}
    assert client.get("/api/v1/requests/000000000000/owner-case").json()["detail"]["code"] == "feature_off"


def test_the_battery_inputs_are_an_envelope_part(client, monkeypatch, none_on):
    keys = {a["key"] for a in client.get("/api/v1/assumptions").json()["assumptions"]}
    assert not {k for k in keys if k.startswith("battery_")}
    # a meter checked against the limit it was issued: an envelope's
    assert "meter_breach_tolerance_w" not in keys
    monkeypatch.setenv("KAPACITET_FEATURES", "envelopes")
    keys = {a["key"] for a in client.get("/api/v1/assumptions").json()["assumptions"]}
    assert "battery_max_kwh" in keys


def test_the_decision_on_an_envelope_needs_the_envelopes_and_the_hourly_one_does_not(client, none_on):
    r = client.post("/api/v1/queue", json={"assessment": "000000000000", "decision": "nonfirm"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "feature_off"
    # firm by the hourly study is a decision of its own; an unknown assessment is all that stops this one
    r = client.post("/api/v1/queue", json={"assessment": "000000000000", "decision": "hourly"})
    assert r.status_code == 404 and r.json()["detail"] == "unknown assessment"
