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
