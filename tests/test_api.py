"""API contract: the shapes the web interface depends on (needs `kapacitet all`)."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import needs_results

pytestmark = needs_results
API = "/api/v1"


@pytest.fixture(scope="module")
def client():
    from kapacitet.api.main import app

    with TestClient(app) as c:
        yield c


def test_health_and_openapi(client):
    r = client.get(f"{API}/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.json()["results_ready"] is True
    schema = client.get("/openapi.json").json()
    assert f"{API}/hosting" in schema["paths"]


def test_meta_carries_the_site_and_the_showcase_hour(client):
    m = client.get(f"{API}/meta").json()
    assert m["bidding_zone"].startswith("SE")
    assert len(m["center"]) == 2
    assert m["grid"]["customers"] > 0
    assert 0 <= m["showcase_hour"] < 8784


def test_every_payload_states_where_it_comes_from(client):
    for path in ("hosting", "showcase", "risk", "national", "validation"):
        body = client.get(f"{API}/{path}").json()
        assert "provenance" in body, path
        assert body["provenance"]["sources"], path


def test_network_is_geojson_with_real_and_synthetic_layers(client):
    gj = client.get(f"{API}/network").json()
    assert gj["type"] == "FeatureCollection"
    layers = {f["properties"]["layer"] for f in gj["features"]}
    assert {"house", "cable", "substation"} <= layers
    houses = [f for f in gj["features"] if f["properties"]["layer"] == "house"]
    assert all("cid" in f["properties"] and "kwp" in f["properties"] for f in houses)
    assert len(gj["bbox"]) == 4


def test_grid_state_binary_matches_its_header(client):
    head = client.get(f"{API}/state/doe").json()
    raw = client.get(f"{API}/state/doe/bin").content
    n = head["hours"] * (head["n_bus"] + head["n_line"] + head["n_cust"])
    assert len(raw) == n, "the uint8 payload must match the declared layout"
    assert "time-major" in head["layout"]
    agg = head["aggregates"]
    assert len(agg["trafo_loading"]) == head["hours"]
    assert max(agg["vmax"]) < 1.10 + 1e-6                     # published state respects the limits


def test_hosting_waterfall_is_ordered(client):
    m = client.get(f"{API}/hosting").json()["data"]["summary"]["measures"]
    static, ts, doe = m["static"]["kw_ac"]["median"], m["timeseries"]["kw_ac"]["median"], m["doe"]["kw_ac"]["median"]
    assert static <= ts <= doe
    assert m["fixed_cap"]["kw_ac"]["median"] >= static


def test_live_request_is_evaluated_and_verified(client):
    cid = int(client.get(f"{API}/meta").json()["grid"]["customers"]) - 1
    r = client.post(f"{API}/evaluate", json={"cid": cid, "pv_kwp": 10.0, "ev_kw": 0.0, "baseline": "static"})
    assert r.status_code == 200
    res = r.json()
    assert res["verification"]["violations_after"] == 0
    assert res["static"]["pv"]["reason_code"] in {
        "trafo_rev", "over_v", "line_rev", "ok_summer"}
    assert 0 <= res["summary"]["pv_curtailment_ref_pct"] <= 100
    assert res["seconds"] < 30
    prof = res["profiles"]
    assert len(prof["envelope_kw"]) in (8760, 8784)
    assert np.all(np.asarray(prof["envelope_kw"]) >= -1e-6)


def test_bad_request_is_rejected(client):
    assert client.post(f"{API}/evaluate", json={"cid": -1}).status_code == 422
    assert client.post(f"{API}/evaluate", json={"cid": 0, "baseline": "nope"}).status_code == 422
    assert client.get(f"{API}/state/does-not-exist").status_code == 404


def test_the_filing_and_its_audit_trail_are_served(client):
    rep = client.get(f"{API}/compliance").json()["data"]
    assert 0 <= rep["worst_uncontrolled"]["hour"] < 8784
    top = rep["per_customer"][0]
    page = client.get(f"{API}/audit", params={"cid": top["customer_id"], "limit": 5000}).json()
    assert page["total"] == top["hours_limited"]
    assert all(r["customer_id"] == top["customer_id"] for r in page["rows"])
    r = client.get(f"{API}/audit.csv", params={"cid": top["customer_id"]})
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("hour,timestamp,customer_id")
    assert len(r.text.strip().splitlines()) == top["hours_limited"] + 1
    assert "attachment" in client.get(f"{API}/compliance.txt").headers["content-disposition"]


def test_the_extra_results_are_served(client):
    s = client.get(f"{API}/overinstallation").json()["data"]
    assert s["budget"]["kw_ac"] > s["static"]["kw_ac"]
    assert client.get(f"{API}/forecast").json()["data"]["rows"]
    assert client.get(f"{API}/phases").json()["data"]["cases"]


def test_houses_say_whether_they_have_a_heat_pump_or_an_ev(client):
    houses = [f for f in client.get(f"{API}/network").json()["features"] if f["properties"]["layer"] == "house"]
    assert all(isinstance(f["properties"]["has_hp"], bool) for f in houses)
    assert any(f["properties"]["has_hp"] for f in houses)


def test_the_reference_network_is_a_worked_example_of_the_contract(client):
    files = client.get(f"{API}/contract/example").json()["files"]
    assert set(files) == {"buses", "lines", "transformer", "customers"}
    body = client.post(f"{API}/check", json={"files": files}).json()
    assert body["usable"], body["text"]
    assert body["tables"]["customers"] > 0
    z = client.get(f"{API}/contract/example.zip")
    assert z.headers["content-type"] == "application/zip" and z.content[:2] == b"PK"
