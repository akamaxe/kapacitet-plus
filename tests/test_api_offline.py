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
    monkeypatch.setattr(main, "MAX_JOBS", 0)
    r = client.post(f"{API}/requests", json={"cid": 0, "pv_kwp": 5.0})
    assert r.status_code == 429


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
    assert client.delete(f"{API}/queue").json() == {"ok": True}


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
