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


# ---------------------------------------------------------------- network export check

GOOD_EXPORT = {
    "buses": "bus_id,kind,name,lon,lat\nSS1,substation,Nätstation,18.0,59.0\n"
             "S1,service,Villa 1,18.001,59.001\n",
    "lines": "line_id,from_bus,to_bus,length_m,std_type,parallel\n"
             "L1,SS1,S1,40,NAYY 4x50 SE,1\n",
    "transformer": "lv_bus,sn_mva,vn_hv_kv,vn_lv_kv,vk_percent,vkr_percent,vector_group,tap_pos\n"
                   "SS1,0.4,10,0.4,4.0,1.325,Dyn,0\n",
    "customers": "customer_id,bus,fuse_a,phase,footprint_m2,annual_kwh\nC1,S1,25,a,120,15000\n",
}


def test_the_contract_is_published(client):
    body = client.get(f"{API}/contract").json()
    assert "buses.csv" in body["markdown"]
    names = [t["name"] for t in body["tables"]]
    assert names == ["buses", "lines", "transformer", "customers"]
    buses = next(t for t in body["tables"] if t["name"] == "buses")
    assert any(c["name"] == "bus_id" and c["required"] for c in buses["columns"])


def test_a_good_export_checks_out_over_the_api(client):
    r = client.post(f"{API}/check", json={"files": GOOD_EXPORT})
    assert r.status_code == 200
    body = r.json()
    assert body["usable"] and body["score"] == 100
    assert body["tables"]["customers"] == 1
    assert "completeness" in body["text"]


def test_a_broken_export_is_reported_not_rejected(client):
    files = dict(GOOD_EXPORT)
    files["customers"] = files["customers"].replace(",S1,", ",S99,")
    body = client.post(f"{API}/check", json={"files": files}).json()
    assert not body["usable"]
    assert any(f["check"] == "orphan customer" for f in body["findings"])


def test_a_file_outside_the_contract_is_refused(client):
    r = client.post(f"{API}/check", json={"files": {"meters": "a,b\n1,2\n"}})
    assert r.status_code == 422


def test_unreadable_csv_is_refused(client):
    r = client.post(f"{API}/check", json={"files": {"buses": '"unterminated\n\x00'}})
    assert r.status_code == 422
