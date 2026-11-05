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


def test_health_lists_each_part_and_what_to_run_for_it(client):
    body = client.get(f"{API}/health").json()
    assert body["version"]
    assert set(body["parts"]) >= {"network", "results", "extra", "requests"}
    for part in body["parts"].values():
        assert part["ready"] or part["command"].startswith("python -m kapacitet")


def test_cross_origin_access_is_off_by_default(client):
    r = client.get(f"{API}/health", headers={"Origin": "https://example.org"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_a_missing_result_names_the_command_that_makes_it():
    from kapacitet.api import main

    assert main.command_for("forecast.json") == "python -m kapacitet forecast"
    assert main.command_for("scenarios.json").endswith("--stage extra --only scenarios")
    err = main.not_computed("risk.json")
    assert err.status_code == 404 and err.detail["code"] == "not_computed"
    assert err.detail["command"] == "python -m kapacitet compute"


def test_the_network_says_it_is_not_built_rather_than_failing(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from kapacitet.api import main

    monkeypatch.setattr(main, "processed", lambda *parts: tmp_path.joinpath(*parts))
    try:
        main.network()
    except HTTPException as err:
        assert err.status_code == 404 and err.detail["code"] == "not_computed"
        assert err.detail["command"] == "python -m kapacitet build"
    else:
        raise AssertionError("an unbuilt network must be a 404 that names the command")


def test_requests_are_refused_when_the_engine_is_busy(client, monkeypatch):
    from kapacitet.api import main

    monkeypatch.setattr(main, "_check_cid", lambda cid: None)
    monkeypatch.setattr(main.POOL, "max_running", 0)
    monkeypatch.setattr(main.POOL, "max_queued", 0)
    r = client.post(f"{API}/requests", json={"cid": 0, "pv_kwp": 5.0})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "busy" and r.headers["retry-after"]


def test_a_job_that_runs_too_long_is_given_up_and_a_finished_one_is_forgotten(client):
    import time

    from kapacitet.api import main

    main._JOBS["5ccc00000000"] = {"status": "running", "events": [], "result": None,
                                  "created": time.time() - main.JOB_TIMEOUT_S - 5, "request": {}}
    main._JOBS["01d000000000"] = {"status": "done", "events": [], "result": {"x": 1},
                                  "created": time.time() - 2 * main.JOB_TTL_S,
                                  "finished": time.time() - main.JOB_TTL_S - 5, "request": {}}
    r = client.get(f"{API}/requests/5ccc00000000")
    assert r.status_code == 500 and "timed out" in r.text
    assert client.get(f"{API}/requests/01d000000000").status_code == 404


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
    assert names == ["buses", "lines", "transformer", "customers", "equipment"]
    assert [t["name"] for t in body["tables"] if t["optional"]] == ["equipment"]
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


def test_an_oversized_table_is_refused(client):
    big = "bus_id,kind\n" + "B,service\n" * 600_000
    r = client.post(f"{API}/check", json={"files": {"buses": big}})
    assert r.status_code == 413


# ---------------------------------------------------------------- saved assessments (stubbed engine)

def _stub_result(cid: int) -> dict:
    hours = 8760
    return {"request": {"cid": cid}, "baseline": "static", "customer": {"cid": cid, "address": "Testvägen 1"},
            "static": {}, "per_year": [], "verification": {}, "risk": {}, "economics": {},
            "profiles": {"year": 2025, "pv_kw": [0.5] * hours, "desired_kw": [0.2] * hours,
                         "envelope_kw": [1.0] * hours, "curtailed_kw": [0.0] * hours},
            "summary": {"pv_static_approved": True}, "seconds": 0.01}


@pytest.fixture()
def stubbed(client, monkeypatch, tmp_path):
    from kapacitet.api import main, records
    from kapacitet.engine import request as rq

    monkeypatch.setattr(main, "_check_cid", lambda cid: None)
    monkeypatch.setattr(main, "_engine", lambda: {"model": None, "hp": None, "ev": None})
    monkeypatch.setattr(rq, "evaluate", lambda model, req, hp, ev, emit=None, ctx=None: _stub_result(req.cid))
    monkeypatch.setattr(records, "folder", lambda: tmp_path)
    # the address is the network's, read when a record is read: this test's network has one street
    monkeypatch.setattr(records, "address_of", lambda cid: None if cid is None else "Testvägen 1")
    return main


def _finish(client, job_id):
    import time

    for _ in range(100):
        r = client.get(f"{API}/requests/{job_id}")
        if r.status_code == 200:
            return r.json()
        time.sleep(0.02)
    raise AssertionError("the stubbed job never finished")


def test_an_assessment_is_saved_and_survives_a_restart(client, stubbed, tmp_path):
    job_id = client.post(f"{API}/requests", json={"cid": 3, "pv_kwp": 6.0}).json()["id"]
    first = _finish(client, job_id)
    assert first["id"] == job_id and first["customer"]["address"] == "Testvägen 1"
    assert (tmp_path / f"{job_id}.json.gz").exists()
    stubbed._JOBS.clear()                                   # what a restart forgets
    again = client.get(f"{API}/requests/{job_id}")
    assert again.status_code == 200 and again.json()["summary"] == first["summary"]
    items = client.get(f"{API}/requests").json()["items"]
    assert items[0]["id"] == job_id and items[0]["request"]["pv_kwp"] == 6.0


def test_an_assessment_downloads_as_hourly_csv_and_as_a_record(client, stubbed):
    job_id = client.post(f"{API}/requests", json={"cid": 4, "pv_kwp": 5.0}).json()["id"]
    _finish(client, job_id)
    csv_text = client.get(f"{API}/requests/{job_id}/hourly.csv").text
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("hour,timestamp_utc+1,pv_kw")
    assert len(lines) == 8760 + 1
    r = client.get(f"{API}/requests/{job_id}", params={"download": True})
    assert "attachment" in r.headers["content-disposition"]
    assert r.json()["request"]["cid"] == 4 and r.json()["version"]


def test_malformed_assessment_ids_are_refused(client):
    for bad in ("..%2f..%2fpyproject", "ZZZ", "0123456789abcdef"):
        assert client.get(f"{API}/requests/{bad}").status_code == 404


# ---------------------------------------------------------------- the queue (no data needed)

def test_the_queue_reports_its_decisions_and_can_be_emptied(client, monkeypatch, tmp_path):
    from kapacitet.api import records
    from kapacitet.engine import queue as qu

    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "queue")
    monkeypatch.setattr(records, "folder", lambda: tmp_path)
    body = client.get(f"{API}/queue").json()
    assert body["items"] == [] and body["approved"] == 0 and body["updating"] is False
    r = client.post(f"{API}/queue", json={"assessment": "0123456789ab", "decision": "nonfirm"})
    assert r.status_code == 404
    bad = client.post(f"{API}/queue", json={"assessment": "0123456789ab", "decision": "maybe"})
    assert bad.status_code == 422
    assert client.request("DELETE", f"{API}/queue", json={}).json() == {"ok": True}


def test_a_request_against_an_empty_queue_is_refused(client, monkeypatch, tmp_path):
    from kapacitet.api import main
    from kapacitet.engine import queue as qu

    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "queue")
    monkeypatch.setattr(main, "_check_cid", lambda cid: None)
    r = client.post(f"{API}/requests", json={"cid": 0, "pv_kwp": 5.0, "baseline": "queue"})
    assert r.status_code == 409 and "queue is empty" in r.text


# ---------------------------------------------------------------- seals (stubbed engine)

def test_a_saved_assessment_is_sealed_and_verifies(client, stubbed, tmp_path):
    import gzip
    import json as _json

    job_id = client.post(f"{API}/requests", json={"cid": 5, "pv_kwp": 4.0}).json()["id"]
    body = _finish(client, job_id)
    assert len(body["seal"]["digest"]) == 64 and body["seal"]["seq"] >= 1
    v = client.get(f"{API}/seals/verify", params={"kind": "assessment", "id": job_id}).json()
    assert v["ok"] and v["record_ok"] and v["digest"] == body["seal"]["digest"]
    # someone edits the kept answer on disk: the seal says so
    path = tmp_path / f"{job_id}.json.gz"
    rec = _json.loads(gzip.decompress(path.read_bytes()))
    rec["result"]["summary"]["pv_static_approved"] = False
    path.write_bytes(gzip.compress(_json.dumps(rec).encode()))
    v = client.get(f"{API}/seals/verify", params={"kind": "assessment", "id": job_id}).json()
    assert not v["ok"] and not v["record_ok"] and v["copy_ok"]
    assert client.get(f"{API}/seals").json()["ok"]


def test_an_unsealed_record_is_a_404_and_bad_ids_are_refused(client):
    assert client.get(f"{API}/seals/verify", params={"kind": "assessment", "id": "ffffffffffff"}).status_code == 404
    assert client.get(f"{API}/seals/verify", params={"kind": "assessment", "id": "../x"}).status_code == 422
    assert client.get(f"{API}/seals/verify", params={"kind": "bogus", "id": "x"}).status_code == 422


def test_decision_log_lists_sealed_decisions_with_digests(client):
    from kapacitet import seals

    seals.seal("decision", "0123456789ab", {"assessment": "0123456789ab", "cid": 7, "address": "Testvägen 7",
                                             "decision": "nonfirm", "note": "pilot", "decided": "2026-11-01T10:00:00+00:00"})
    r = client.get(f"{API}/queue/log.csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = r.text.strip().splitlines()
    assert rows[0].startswith("decided,assessment,house,address,decision,note")
    assert "Testvägen 7" in rows[1] and "nonfirm" in rows[1] and len(rows[1].split(",")[-3]) == 64


# ---------------------------------------------------------------- the job pool (stubbed engine)

def _slow_engine(monkeypatch, release):
    """An evaluation that runs until `release` is set, checking for a cancel as the engine does."""
    import threading
    import time

    from kapacitet.engine import request as rq

    threads: dict[int, threading.Thread] = {}

    def slow(model, req, hp, ev, emit=None, ctx=None):
        threads[req.cid] = threading.current_thread()
        while not release.is_set():
            ctx.check()
            time.sleep(0.01)
        return _stub_result(req.cid)

    monkeypatch.setattr(rq, "evaluate", slow)
    return threads


def test_racing_requests_get_one_slot_two_places_in_line_and_two_refusals(client, stubbed, monkeypatch):
    import threading
    import time

    main = stubbed
    monkeypatch.setattr(main.POOL, "max_running", 1)
    monkeypatch.setattr(main.POOL, "max_queued", 2)
    release = threading.Event()
    threads = _slow_engine(monkeypatch, release)
    start = threading.Barrier(5)
    answers: dict[int, object] = {}

    def post(cid):
        start.wait()
        answers[cid] = client.post(f"{API}/requests", json={"cid": cid, "pv_kwp": 5.0})

    racers = [threading.Thread(target=post, args=(cid,)) for cid in range(5)]
    try:
        for t in racers:
            t.start()
        for t in racers:
            t.join(20)
        codes = sorted(r.status_code for r in answers.values())
        assert codes == [200, 200, 200, 429, 429]
        taken = {cid: r.json() for cid, r in answers.items() if r.status_code == 200}
        assert sorted(b["status"] for b in taken.values()) == ["queued", "queued", "running"]
        assert all(r.json()["detail"]["code"] == "busy" for r in answers.values() if r.status_code == 429)
        assert main.POOL.counts() == {"running": 1, "queued": 2}
        running = next(cid for cid, b in taken.items() if b["status"] == "running")
        waiting = [b["id"] for b in taken.values() if b["status"] == "queued"]
        assert all(main.POOL.jobs[j]["events"][0]["stage"] == "queued" for j in waiting)

        # a running job stops at the engine's next check, its thread ends, and the line moves up
        for _ in range(200):
            if running in threads:
                break
            time.sleep(0.01)
        r = client.request("DELETE", f"{API}/requests/{taken[running]['id']}", json={})
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        threads[running].join(5)
        assert not threads[running].is_alive()
        assert client.get(f"{API}/requests/{taken[running]['id']}").json()["detail"]["code"] == "cancelled"
        for _ in range(200):
            if main.POOL.counts() == {"running": 1, "queued": 1}:
                break
            time.sleep(0.01)
        assert main.POOL.counts() == {"running": 1, "queued": 1}
        moved = next(j for j in waiting if main.POOL.jobs[j]["status"] == "running")
        still = next(j for j in waiting if j != moved)
        stages = [e["stage"] for e in main.POOL.jobs[moved]["events"]]
        assert stages[0] == "queued" and "started" in stages
        assert main.POOL.jobs[still]["events"][-1]["payload"] == {"position": 1}      # moved up the line

        # one in line leaves it at once; a finished one cannot be cancelled
        r = client.request("DELETE", f"{API}/requests/{still}", json={})
        assert r.status_code == 200 and main.POOL.jobs[still]["status"] == "cancelled"
        release.set()
        assert _finish(client, moved)["id"] == moved
        r = client.request("DELETE", f"{API}/requests/{moved}", json={})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "already_finished"
        assert client.request("DELETE", f"{API}/requests/0123456789ab", json={}).status_code == 404
        # nothing of a cancelled job is saved
        from kapacitet.api import records

        assert records.load(taken[running]["id"]) is None and records.load(still) is None
    finally:
        release.set()
    for _ in range(200):
        if not main.POOL.busy():
            break
        time.sleep(0.01)
    assert not main.POOL.busy()


def test_the_portal_cancels_its_own_answer_and_the_sync_route_shares_the_pool(client, stubbed, monkeypatch):
    import threading

    main = stubbed
    monkeypatch.setattr(main.POOL, "max_running", 1)
    monkeypatch.setattr(main.POOL, "max_queued", 0)
    monkeypatch.setattr("kapacitet.api.apply._baseline", lambda: "static")
    release = threading.Event()
    _slow_engine(monkeypatch, release)
    try:
        first = client.post(f"{API}/apply/evaluate", json={"cid": 2, "pv_kwp": 5.0})
        assert first.status_code == 200
        # the one slot is taken and nobody may wait: the synchronous route is refused like the others
        r = client.post(f"{API}/evaluate", json={"cid": 3, "pv_kwp": 5.0})
        assert r.status_code == 429 and r.json()["detail"]["code"] == "busy"
        r = client.request("DELETE", f"{API}/apply/evaluate/{first.json()['id']}", json={})
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
    finally:
        release.set()
    for _ in range(300):
        if not main.POOL.busy():
            break
        threading.Event().wait(0.01)
    r = client.post(f"{API}/evaluate", json={"cid": 3, "pv_kwp": 5.0})
    assert r.status_code == 200 and r.json()["summary"] == {"pv_static_approved": True}


def test_the_portal_cannot_cancel_a_planners_job_and_a_swept_saved_one_is_finished(client, stubbed, monkeypatch):
    import threading

    main = stubbed
    release = threading.Event()
    _slow_engine(monkeypatch, release)
    try:
        dso = client.post(f"{API}/requests", json={"cid": 2, "pv_kwp": 5.0}).json()["id"]
        r = client.request("DELETE", f"{API}/apply/evaluate/{dso}", json={})
        assert r.status_code == 404
        assert main.POOL.jobs[dso]["status"] in ("queued", "running")
        d = client.request("DELETE", f"{API}/requests/{dso}", json={})
        assert d.json().get("status") == "cancelled", d.text
    finally:
        release.set()
    done = client.post(f"{API}/requests", json={"cid": 3, "pv_kwp": 5.0}).json()["id"]
    _finish(client, done)
    main.POOL.jobs.pop(done)                      # as the TTL sweep does
    assert client.request("DELETE", f"{API}/requests/{done}", json={}).status_code == 409
    assert client.request("DELETE", f"{API}/requests/{'a' * 12}", json={}).status_code == 404


def test_a_synchronous_answer_leaves_nothing_in_the_pool(client, stubbed):
    before = set(stubbed.POOL.jobs)
    r = client.post(f"{API}/evaluate", json={"cid": 3, "pv_kwp": 5.0})
    assert r.status_code == 200
    assert set(stubbed.POOL.jobs) == before


def test_no_sealed_or_logged_file_holds_an_address(client, stubbed, tmp_path, monkeypatch):
    import gzip

    from kapacitet import seals
    from kapacitet.api import records
    from kapacitet.engine import queue as qu

    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "queue")
    job_id = client.post(f"{API}/requests", json={"cid": 6, "pv_kwp": 4.0}).json()["id"]
    body = _finish(client, job_id)
    assert body["customer"]["address"] == "Testvägen 1"           # read from the network for the reader
    # a decision on it, by the queue (a refusal needs no engine)
    item = qu.decide(None, None, None, records.load(job_id), "declined", "no room this year")
    assert "address" not in item
    assert client.get(f"{API}/queue").json()["items"][0]["address"] == "Testvägen 1"
    assert seals.verify("assessment", job_id, records.load(job_id, resolve=False))["ok"]
    assert seals.verify("decision", job_id, item)["ok"]
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert any(p.suffix == ".gz" for p in written) and any(p.name == "seals.jsonl" for p in written)
    for path in written:
        data = path.read_bytes()
        if path.suffix == ".gz":
            data = gzip.decompress(data)
        assert b"Testv" not in data, f"{path.relative_to(tmp_path)} keeps the address"
    listed = client.get(f"{API}/requests").json()["items"]
    assert listed[0]["id"] == job_id and listed[0]["address"] == "Testvägen 1"



def test_an_approval_firm_or_with_an_envelope_writes_no_address(client, stubbed, tmp_path, monkeypatch, caplog):
    """The approve path (POST /queue -> queue.decide -> inbox.link_decision), offline: the engine is a
    stub whose answer carries the address, as the real one does, and nothing it writes may keep it."""
    import gzip
    import json
    import logging
    import time

    import numpy as np
    import pandas as pd

    from kapacitet import seals
    from kapacitet.api import inbox
    from kapacitet.config import settings
    from kapacitet.engine import queue as qu
    from kapacitet.engine import request as rq

    hours, n = 8760, 8
    years = list(settings().weather_years)[:2]
    street = "Testvägen 1"
    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "queue")
    monkeypatch.setattr(inbox, "log_path", lambda: tmp_path / "requests" / "applications.jsonl")
    monkeypatch.setattr(qu, "customers", lambda: pd.DataFrame(
        {"fuse_kva": [17.3] * n, "address": [street] * n}, index=pd.RangeIndex(n, name="cid")))

    class Ctx(rq.Context):                # the grid as the queue left it: the second request meets it
        def years(self, wanted):
            return years

        def pq(self, model, y, hp, ev):
            return np.zeros((n, hours)), np.zeros((n, hours))

    monkeypatch.setattr(qu, "context", lambda: Ctx("static", label={"version": qu.load()["version"]}))

    def run(model, req, hp, ev, ctx=None, verify_years=None):
        d = {"load": np.full(hours, 0.4), "hp": np.zeros(hours), "ev_want": np.zeros(hours),
             "pv": np.full(hours, 1.0)}
        out = {"request": dict(req.__dict__), "customer": {"cid": req.cid, "address": street},
               "verification": {"all_years": {"export_violations_after": 0}}, "summary": {"pv_static_approved": True}}
        return out, {y: d for y in verify_years}

    monkeypatch.setattr(rq, "run", run)
    monkeypatch.setattr(rq, "evaluate", lambda model, req, hp, ev, emit=None, ctx=None: {
        **_stub_result(req.cid), **({"queue": ctx.label} if ctx is not None else {})})
    monkeypatch.setattr(rq, "_injection", lambda d: d["pv"] - d["load"])
    monkeypatch.setattr(rq, "_reactive", lambda d: -0.3 * d["load"])
    monkeypatch.setattr(rq, "build_state", lambda model, p, q: {"pt": p.sum(axis=0).astype(np.float32)})
    caplog.set_level(logging.DEBUG)

    for cid, decision, baseline in ((2, "firm", "static"), (5, "nonfirm", "queue")):
        job_id = client.post(f"{API}/requests", json={"cid": cid, "pv_kwp": 4.0, "baseline": baseline}).json()["id"]
        assert _finish(client, job_id)["customer"]["address"] == street
        assert client.post(f"{API}/applications", json={"assessment": job_id}).status_code == 200
        r = client.post(f"{API}/queue", json={"assessment": job_id, "decision": decision, "note": "approved"})
        assert r.status_code == 200, r.text
        for _ in range(200):
            res = client.get(f"{API}/queue/jobs/{r.json()['id']}")
            if res.status_code != 202:
                break
            time.sleep(0.02)
        assert res.status_code == 200, res.text
        item = res.json()
        assert item["decision"] == decision and "address" not in json.dumps(item)
        assert seals.verify("decision", job_id, qu.load()["items"][-1])["ok"]

    q = client.get(f"{API}/queue").json()
    assert [it["decision"] for it in q["items"]] == ["firm", "nonfirm"] and q["approved"] == 2
    assert all(it["address"] == street for it in q["items"])          # read from the network for the reader
    assert sorted(a["status"] for a in inbox.applications().values()) == ["firm", "nonfirm"]
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    for part in ("queue.json", "applications.jsonl", "seals.jsonl", "index.jsonl"):
        assert any(p.name == part for p in written), f"no {part} was written"
    assert any(p.parent.name == "queue" and p.name.startswith("state_") for p in written)
    for path in written:
        data = path.read_bytes()
        if path.suffix == ".gz":
            data = gzip.decompress(data)
        assert b"Testv" not in data, f"{path.relative_to(tmp_path)} keeps the address"
    assert not any("Testv" in rec.getMessage() for rec in caplog.records), "a log line keeps the address"

# ---------------------------------------------------------------- retention and the server's lock

def test_old_assessments_are_purged_and_their_seals_erased_but_not_what_a_decision_rests_on(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from kapacitet import seals
    from kapacitet.api import inbox, records
    from kapacitet.engine import queue as qu

    monkeypatch.setattr(records, "folder", lambda: tmp_path / "requests")
    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "requests" / "queue")
    monkeypatch.setattr(inbox, "log_path", lambda: tmp_path / "requests" / "applications.jsonl")
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)

    def saved(job_id, days_ago):
        rec = {"id": job_id, "created": (now - timedelta(days=days_ago)).isoformat(),
               "request": {"cid": 1, "pv_kwp": 5.0, "baseline": "static"},
               "result": {"customer": {"cid": 1}, "summary": {}, "static": {}}}
        records.save(rec)
        return rec

    saved("a00000000001", 40)
    decided = saved("a00000000002", 40)
    saved("a00000000003", 2)
    qu.decide(None, None, None, decided, "declined")
    assert records.purge(30, now=now, dry_run=True)["ids"] == ["a00000000001"]
    out = records.purge(30, now=now)
    assert out["ids"] == ["a00000000001"] and out["kept_for_decisions"] == 1
    assert records.load("a00000000001") is None and records.load("a00000000002") is not None
    assert [it["id"] for it in records.listing()] == ["a00000000003", "a00000000002"]
    v = seals.verify("assessment", "a00000000001")
    assert v["erased"] and v["ok"] and v["reason"].startswith("retention")
    assert seals.verify_chain()["ok"]
    assert records.purge(30, now=now)["assessments"] == 0          # nothing left to do, nothing broken


def test_a_second_writer_of_the_same_folder_is_refused_and_a_crashed_one_leaves_no_lock(tmp_path, monkeypatch):
    from conftest import another_server
    from kapacitet.api import records

    monkeypatch.setattr(records, "folder", lambda: tmp_path / "requests")
    with another_server(tmp_path) as other:
        with pytest.raises(records.FolderInUse) as e:
            records.claim_server_lock(wait_s=0)
        assert e.value.pid == other and str(other) in str(e.value)
    # killed, not stopped: the OS let go of its lock, and the PID it left in the file means nothing
    assert records.server_lock_path().read_text() == str(other)
    lock = records.claim_server_lock()
    assert lock is not None and lock.read_text() == str(__import__("os").getpid())
    assert records.claim_server_lock() == lock            # a second app in this process holds it too
    records.release_server_lock(lock)
    assert lock.read_text() == str(__import__("os").getpid())    # held until the last release
    records.release_server_lock(lock)
    assert lock.read_text() == ""
    with another_server(tmp_path):                       # free again for whoever comes next
        pass


# ---------------------------------------------------------------- provenance (no data needed)

@pytest.fixture()
def data_folder(tmp_path, monkeypatch):
    """The API reading a data folder of the test's own."""
    from kapacitet.api import main

    monkeypatch.setattr(main, "processed", lambda *parts: tmp_path.joinpath(*parts))
    (tmp_path / "results").mkdir()
    return tmp_path


def _write(path, body: str, stamp: float):
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    os.utime(path, (stamp, stamp))
    return path


def test_a_result_is_dated_by_its_own_newest_file(data_folder):
    """A stage rerun on its own writes no meta.json: its results still carry their own date."""
    from datetime import datetime, timezone

    from kapacitet.api import main

    full = "2026-09-19T12:03:00+00:00"
    _write(data_folder / "results" / "meta.json", '{"computed_at": "%s"}' % full, 1_758_283_380)
    old = _write(data_folder / "results" / "tornado.json", "{}", 1_760_000_000)
    new = _write(data_folder / "results" / "overinstallation.json", "{}", 1_762_000_000)
    prov = main.provenance("elprisetjustnu", files=[old, new, data_folder / "results" / "missing.json"])
    newest = datetime.fromtimestamp(1_762_000_000, timezone.utc).isoformat()
    assert prov["run_id"] == prov["computed_at"] == newest
    assert prov["full_run_at"] == full
    assert set(prov["files"]) == {"results/tornado.json", "results/overinstallation.json"}
    # without files of its own, a response is dated by the last full run, as before
    assert main.provenance("osm")["run_id"] == full


def test_meta_says_when_the_results_last_changed(client, data_folder):
    from datetime import datetime, timezone

    _write(data_folder / "results" / "meta.json", '{"computed_at": "2026-09-19T12:03:00+00:00"}', 1_758_283_380)
    _write(data_folder / "results" / "operation" / "dayahead" / "maxmin" / "compliance.json", "{}", 1_762_040_460)
    body = client.get(f"{API}/meta").json()
    assert body["run"]["computed_at"] == "2026-09-19T12:03:00+00:00"
    assert body["results_updated_at"] == datetime.fromtimestamp(1_762_040_460, timezone.utc).isoformat()


def test_real_and_synthetic_follow_what_the_data_folder_holds(data_folder):
    import numpy as np
    import pandas as pd

    from kapacitet.api import main

    prov = main.provenance()
    assert any("OpenStreetMap" in s for s in prov["real"])
    assert any(s.startswith("LV network topology") for s in prov["synthetic"])
    assert "household load (CREST/richardsonpy)" in prov["synthetic"]
    # an operator's network, with metered load and an inventory of what is connected today
    _write(data_folder / "grid" / "summary.json", '{"site": "imported", "imported": true}', 1_762_000_000)
    (data_folder / "households").mkdir()
    np.save(data_folder / "households" / "metered_2025.npy", np.zeros((2, 3)))
    pd.DataFrame({"cid": [0], "existing_pv_kw": [3.0]}).to_parquet(data_folder / "grid" / "customers.parquet")
    prov = main.provenance()
    assert any(s.startswith("LV network topology") for s in prov["real"])
    assert not any(s.startswith("LV network topology") for s in prov["synthetic"])
    assert "household load (metered, temperature-corrected)" in prov["real"]
    assert not any("CREST" in s for s in prov["synthetic"])
    assert any("inventory" in s for s in prov["real"])
    # what is modelled whatever the inputs
    assert any("EV charging" in s for s in prov["synthetic"])


def test_the_network_is_dated_afresh_though_its_geometry_is_cached(data_folder, monkeypatch):
    """The map's GeoJSON is cached by its own files; its provenance also reads meta.json and the
    grid tables, so it is not kept with it."""
    from functools import lru_cache

    from kapacitet.api import main

    built = []

    @lru_cache(maxsize=2)
    def geometry(stamp):
        built.append(stamp)
        return {"type": "FeatureCollection", "features": []}

    monkeypatch.setattr(main, "_network", geometry)
    _write(data_folder / "grid" / "customers.parquet", "", 1_760_000_000)
    _write(data_folder / "results" / "meta.json", '{"computed_at": "2026-09-19T12:03:00+00:00"}', 1_760_000_000)
    assert main.network()["provenance"]["full_run_at"] == "2026-09-19T12:03:00+00:00"
    _write(data_folder / "results" / "meta.json", '{"computed_at": "2026-11-01T08:00:00+00:00"}', 1_762_000_000)
    _write(data_folder / "grid" / "lines.parquet", "", 1_762_100_000)
    body = main.network()
    assert len(built) == 1, "the geometry itself is still cached"
    assert body["provenance"]["full_run_at"] == "2026-11-01T08:00:00+00:00"
    assert "grid/lines.parquet" in body["provenance"]["files"]


def test_an_assessment_against_the_queue_is_dated_by_the_queue_as_well(data_folder, monkeypatch):
    from datetime import datetime, timezone

    from kapacitet.api import main
    from kapacitet.engine import queue as qu

    def iso(stamp):
        return datetime.fromtimestamp(stamp, timezone.utc).isoformat()

    folder = data_folder / "requests" / "queue"
    monkeypatch.setattr(qu, "folder", lambda: folder)
    _write(data_folder / "results" / "showcase.json", "{}", 1_760_000_000)
    _write(folder / "queue.json", "{}", 1_762_000_000)
    _write(folder / "state_2025.npz", "", 1_761_000_000)
    queued = main.make_record("abc", 0.0, {"baseline": "queue"}, {})["provenance"]
    assert queued["computed_at"] == iso(1_762_000_000)
    assert {"requests/queue/queue.json", "requests/queue/state_2025.npz"} <= set(queued["files"])
    plain = main.make_record("abc", 0.0, {"baseline": "static"}, {})["provenance"]
    assert plain["computed_at"] == iso(1_760_000_000) and not any("queue" in f for f in plain["files"])
