"""API guards that hold with or without the pipeline output (they run on a fresh clone)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import needs_grid

API = "/api/v1"


@pytest.fixture(scope="module")
def client():
    from kapacitet.api.main import app

    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", [
    "/%2e%2e/%2e%2e/pyproject.toml",
    "/..%2f..%2fpyproject.toml",
    "/%2e%2e%5c%2e%2e%5cpyproject.toml",
    "/assets/..%2f..%2f..%2fpyproject.toml",
])
def test_the_web_route_never_serves_files_outside_the_build(client, path):
    r = client.get(path)
    assert "[project]" not in r.text, f"{path} leaked a project file"


@pytest.mark.parametrize("name", ["nope", "..%2f..%2fmeta", "static.json"])
def test_unknown_grid_states_are_refused(client, name):
    assert client.get(f"{API}/state/{name}").status_code == 404
    assert client.get(f"{API}/state/{name}/bin").status_code == 404


def test_health_answers_without_results(client):
    r = client.get(f"{API}/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@needs_grid
@pytest.mark.parametrize("route", ["requests", "evaluate"])
def test_an_unknown_house_is_refused_before_any_work(client, route):
    r = client.post(f"{API}/{route}", json={"cid": 100_000, "pv_kwp": 5.0})
    assert r.status_code == 422


# ---------------------------------------------------------------- network export check (no data needed)

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
