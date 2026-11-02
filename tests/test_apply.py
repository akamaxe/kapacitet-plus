"""The applicant's portal (api/apply.py): only addresses, one's own answer, one's own state.

The refusals and the shape of a status run anywhere (the folders are the test's own); the list of
addresses needs a built network.
"""

from __future__ import annotations

import pytest

from conftest import needs_grid


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet import dso
    from kapacitet.api import inbox, records
    from kapacitet.api.main import app
    from kapacitet.engine import queue as qu

    monkeypatch.setattr(records, "folder", lambda: tmp_path / "requests")
    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "requests" / "queue")
    monkeypatch.setattr(inbox, "log_path", lambda: tmp_path / "requests" / "applications.jsonl")
    monkeypatch.setattr(dso, "path", lambda: tmp_path / "requests" / "settings.json")
    with TestClient(app) as c:
        yield c, records, inbox


def test_an_application_needs_its_answer(client):
    c, _, _ = client
    r = c.post("/api/v1/apply/submit", json={"assessment": "0123456789ab", "key": "household-key-1"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "unknown_assessment"


def test_the_state_is_read_with_the_code_and_shows_nothing_of_the_dsos_view(client):
    c, records, inbox = client
    records.save({"id": "a0000000000a", "created": "2026-11-02T10:00:00+00:00",
                  "request": {"cid": 3, "pv_kwp": 8.0, "ev_kw": 0.0, "hp": False, "bat_kwh": 0.0, "ev_phases": 0,
                              "baseline": "static"},
                  "result": {"customer": {"address": "Gatan 3"}, "static": {"pv": {"approved": True}}}})
    sent = c.post("/api/v1/apply/submit", json={"assessment": "a0000000000a", "key": "household-key-2"}).json()
    assert sent["status"] == "pending" and sent["reference"].startswith("KP-")
    # the same key sends nothing new
    again = c.post("/api/v1/apply/submit", json={"assessment": "a0000000000a", "key": "household-key-2"}).json()
    assert again["reference"] == sent["reference"] and len(inbox.applications()) == 1

    st = c.get(f"/api/v1/apply/status/{sent['reference'].lower()}").json()
    assert st["reference"] == sent["reference"] and st["address"] == "Gatan 3"
    assert set(st) == {"reference", "status", "received", "decided", "by", "address", "request", "reasons"}
    for bad in ("KP-0000-0000", "nonsense", "../inbox"):
        assert c.get(f"/api/v1/apply/status/{bad}").status_code == 404


@needs_grid
def test_the_portal_lists_only_addresses(client):
    c, _, _ = client
    body = c.get("/api/v1/apply/houses").json()
    assert body["items"] and all(set(h) == {"cid", "address"} for h in body["items"])
    assert set(body["form"]) == {"pv_min_kwp", "battery_max_kwh", "ev_kw"}
