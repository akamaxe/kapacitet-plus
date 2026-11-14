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
