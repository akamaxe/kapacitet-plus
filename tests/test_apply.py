"""The applicant's portal (api/apply.py): only addresses, one's own answer, one's own state.

The refusals and the shape of a status run anywhere (the folders are the test's own); the list of
addresses needs a built network.
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import needs_grid

# 80 bits in Crockford's base32
NEW_CODE = re.compile(r"^KP-(?:[0-9A-HJKMNP-TV-Z]{4}-){3}[0-9A-HJKMNP-TV-Z]{4}$")


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
    assert sent["status"] == "pending" and NEW_CODE.match(sent["reference"])
    # the same key sends nothing new, and a retry whose answer was lost gets the same code back
    again = c.post("/api/v1/apply/submit", json={"assessment": "a0000000000a", "key": "household-key-2"}).json()
    assert again["reference"] == sent["reference"] and len(inbox.applications()) == 1

    st = c.get(f"/api/v1/apply/status/{sent['reference'].lower()}").json()
    assert st["reference"] == sent["reference"] and st["address"] == "Gatan 3"
    assert set(st) == {"reference", "status", "received", "decided", "by", "address", "request", "reasons"}
    # typed from paper: an O for a zero, an I or L for a one
    typed = sent["reference"].replace("0", "O").replace("1", "l")
    assert c.get(f"/api/v1/apply/status/{typed}").status_code == 200
    for bad in ("KP-0000-0000", "KP-0000-0000-0000-0000", "nonsense", "../inbox"):
        assert c.get(f"/api/v1/apply/status/{bad}").status_code == 404


def test_the_code_is_never_kept_and_the_dsos_view_sees_only_its_hint(client):
    c, records, inbox = client
    records.save({"id": "b0000000000b", "created": "2026-11-02T10:00:00+00:00",
                  "request": {"cid": 4, "pv_kwp": 6.0, "ev_kw": 0.0, "hp": False, "bat_kwh": 0.0, "ev_phases": 0,
                              "baseline": "static"},
                  "result": {"customer": {"address": "Gatan 4"}, "static": {"pv": {"approved": True}}}})
    sent = c.post("/api/v1/apply/submit", json={"assessment": "b0000000000b", "key": "household-key-3"}).json()
    code = sent["reference"]
    log = inbox.log_path().read_text(encoding="utf-8")
    assert code not in log and code.replace("-", "") not in log and "household-key-3" not in log
    received = json.loads(log.splitlines()[0])
    assert received["reference_hint"] == code[3:7] and len(received["reference_sha256"]) == 64
    listed = c.get("/api/v1/inbox").json()
    assert listed["total"] == 1 and listed["items"][0]["reference_hint"] == code[3:7]
    assert code not in json.dumps(listed)
    # an application already sent for the house is named by its hint, so a refusal gives nothing away
    records.save({"id": "c0000000000c", "created": "2026-11-02T10:01:00+00:00",
                  "request": {"cid": 4, "pv_kwp": 7.0, "ev_kw": 0.0, "hp": False, "bat_kwh": 0.0, "ev_phases": 0,
                              "baseline": "static"},
                  "result": {"customer": {"address": "Gatan 4"}, "static": {"pv": {"approved": True}}}})
    open_ = c.post("/api/v1/apply/submit", json={"assessment": "c0000000000c", "key": "household-key-4"})
    assert open_.status_code == 409 and open_.json()["detail"]["params"]["app"] == f"KP-{code[3:7]}-…"
    assert code not in open_.text
    twice = c.post("/api/v1/apply/submit", json={"assessment": "b0000000000b", "key": "household-key-5"})
    assert twice.status_code == 409 and code not in twice.text


def test_a_code_from_before_the_hashes_still_resolves(client):
    c, records, inbox = client
    inbox.append({"app": "0123456789ab", "event": "received", "reference": "KP-1A2B-3C4D", "assessment": "d0000000000d",
                  "cid": 9, "address": "Gatan 9", "source": "portal", "request": {"pv_kwp": 5.0}, "key": "old-key-123"})
    st = c.get("/api/v1/apply/status/kp-1a2b-3c4d")
    assert st.status_code == 200 and st.json()["reference"] == "KP-1A2B-3C4D" and st.json()["address"] == "Gatan 9"
    assert c.get("/api/v1/inbox").json()["items"][0]["reference_hint"] == "1A2B"
    assert "KP-1A2B-3C4D" not in c.get("/api/v1/inbox").text
    # and its key, kept in plain then, still finds it again
    records.save({"id": "d0000000000d", "created": "2026-11-01T10:00:00+00:00",
                  "request": {"cid": 9, "pv_kwp": 5.0, "ev_kw": 0.0, "hp": False, "bat_kwh": 0.0, "ev_phases": 0},
                  "result": {"customer": {"address": "Gatan 9"}}})
    again = c.post("/api/v1/apply/submit", json={"assessment": "d0000000000d", "key": "old-key-123"}).json()
    assert again["reference"] == "KP-1A2B-3C4D" and len(inbox.applications()) == 1


def test_eleven_lookups_in_a_minute_are_too_many(client):
    c, _, _ = client
    answers = [c.get("/api/v1/apply/status/KP-0000-0000-0000-0000") for _ in range(11)]
    assert [r.status_code for r in answers[:10]] == [404] * 10
    assert answers[10].status_code == 429 and answers[10].json()["detail"]["code"] == "rate_limited"
    assert int(answers[10].headers["retry-after"]) >= 1


@needs_grid
def test_the_portal_lists_only_addresses(client):
    c, _, _ = client
    body = c.get("/api/v1/apply/houses").json()
    assert body["items"] and all(set(h) == {"cid", "address"} for h in body["items"])
    assert set(body["form"]) == {"pv_min_kwp", "battery_max_kwh", "ev_kw"}
