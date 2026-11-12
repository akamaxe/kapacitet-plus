"""Imported networks (datasets.py, api/datasets.py, the supervisor in pipeline.py) and the importer's
fixes. Everything here runs without the pipeline's output: folders are the test's own and the build's
steps are stand-ins; a real build is the local round trip in the README."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from argparse import Namespace

import numpy as np
import pandas as pd
import pytest


def test_the_paths_follow_the_environment_in_a_subprocess(tmp_path):
    code = "from kapacitet import paths; print(paths.DATA, paths.HOME, paths.RAW, paths.DATASETS, sep='|')"
    env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("KAPACITET_")}
    env.update(KAPACITET_DATA_DIR=str(tmp_path / "net"), KAPACITET_RAW_DIR=str(tmp_path / "raw"))
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.strip()
    data, home, raw, datasets = out.split("|")
    assert data == str((tmp_path / "net").resolve()) and raw == str((tmp_path / "raw").resolve())
    assert home == data and datasets == str((tmp_path / "net" / "datasets").resolve())
    env["KAPACITET_HOME"] = str(tmp_path / "home")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.strip()
    assert out.split("|")[1] == str((tmp_path / "home").resolve())


def test_the_supervisor_starts_the_server_again_when_it_exits_with_75(tmp_path):
    from kapacitet.datasets import RESTART
    from kapacitet.pipeline import supervise

    count = tmp_path / "runs.txt"
    script = (f"import pathlib, sys; p = pathlib.Path({str(count)!r}); n = int(p.read_text() or 0) + 1 if p.exists() else 1; "
              f"p.write_text(str(n)); sys.exit({RESTART} if n == 1 else 0)")
    seen = []

    def command(env):
        seen.append(env["KAPACITET_SUPERVISED"])
        return [sys.executable, "-c", script]

    assert supervise(Namespace(host="127.0.0.1", port=0, site=None), command=command) == 0
    assert count.read_text() == "2" and seen == ["1", "1"]


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet import datasets as ds
    from kapacitet.api import main

    monkeypatch.setattr(ds, "DATASETS", tmp_path / "datasets")
    monkeypatch.setattr(ds, "HOME", tmp_path)
    monkeypatch.setattr(ds, "launch", lambda *a, **k: 0)      # no build process in a test
    with TestClient(main.app) as c:
        yield c, ds, main


def test_switching_needs_the_supervisor_and_no_running_job(api, monkeypatch):
    c, ds, main = api
    monkeypatch.delenv("KAPACITET_SUPERVISED", raising=False)
    r = c.post("/api/v1/datasets/reference/activate", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_supervised"
    monkeypatch.setenv("KAPACITET_SUPERVISED", "1")
    monkeypatch.setitem(main._JOBS, "f00000000000", {"status": "running", "events": [], "created": 0})
    r = c.post("/api/v1/datasets/reference/activate", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "jobs_running"
    main._JOBS.pop("f00000000000")
    r = c.post("/api/v1/datasets/no-such-net/activate", json={})  # refused before anything restarts
    assert r.status_code == 404 and r.json()["detail"]["code"] == "unknown_dataset"
    assert not (ds.HOME / "active.json").exists()


def test_an_upload_is_validated_before_anything_is_written(api):
    c, ds, _ = api
    r = c.post("/api/v1/datasets", json={"name": "Bad", "files": {"buses": "bus_id\n1\n", "lines": "x\n"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_usable"
    assert not ds.DATASETS.exists() or not any(ds.DATASETS.iterdir())
    r = c.post("/api/v1/datasets", json={"name": "Odd", "files": {"meters": "a\n1\n"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_in_contract"


def _dataset(ds, ds_id="test-net-000000"):
    root = ds.DATASETS / ds_id
    (root / "import").mkdir(parents=True)
    rec = {"id": ds_id, "name": "Test", "created": "2026-11-02T00:00:00+00:00", "status": "uploaded", "quick": True,
           "steps": [{"name": s, "status": "waiting"} for s in ds.STEPS]}
    (root / "dataset.json").write_text(json.dumps(rec), encoding="utf-8")
    return ds_id


def test_the_build_records_each_step_and_where_it_stopped(tmp_path, monkeypatch):
    from kapacitet import datasets as ds

    monkeypatch.setattr(ds, "DATASETS", tmp_path / "datasets")
    ds_id = _dataset(ds)
    done = ds.build(ds_id, steps={s: (lambda s=s: {"note": s}) for s in ds.STEPS})
    assert done["status"] == "ready" and [s["status"] for s in done["steps"]] == ["done"] * len(ds.STEPS)
    assert done["steps"][0]["note"] == "import" and "seconds" in done["steps"][0]
    assert not (tmp_path / "datasets" / ds_id / "build.lock").exists()

    def fail():
        raise RuntimeError("profiles broke")

    steps = {s: (lambda: {}) for s in ds.STEPS} | {"profiles": fail}
    stopped = ds.build(ds_id, steps=steps)
    by = {s["name"]: s for s in stopped["steps"]}
    assert stopped["status"] == "failed" and by["profiles"]["status"] == "failed"
    assert "profiles broke" in by["profiles"]["error"] and by["compute"]["status"] == "waiting"
    assert not ds.building(ds_id)


def test_a_network_in_use_or_being_built_is_not_deleted(tmp_path, monkeypatch):
    import os

    from kapacitet import datasets as ds

    monkeypatch.setattr(ds, "DATASETS", tmp_path / "datasets")
    monkeypatch.setattr(ds, "HOME", tmp_path)
    ds_id = _dataset(ds)
    (tmp_path / "datasets" / ds_id / "build.lock").write_text(str(os.getpid()))   # this process is alive
    with pytest.raises(ds.DatasetError) as e:
        ds.delete(ds_id)
    assert e.value.code == "building"
    (tmp_path / "datasets" / ds_id / "build.lock").unlink()
    monkeypatch.setenv("KAPACITET_DATASET", ds_id)
    with pytest.raises(ds.DatasetError) as e:
        ds.delete(ds_id)
    assert e.value.code == "in_use"
    monkeypatch.delenv("KAPACITET_DATASET")
    ds.delete(ds_id)
    assert not (tmp_path / "datasets" / ds_id).exists()


class _Started:
    """What subprocess.Popen gives launch(): a process that is alive (this one), started nowhere."""

    def __init__(self, *args, **kwargs):
        import os

        self.pid = os.getpid()


def test_one_build_at_a_time_and_never_the_network_in_use(tmp_path, monkeypatch):
    from kapacitet import datasets as ds

    monkeypatch.setattr(ds, "DATASETS", tmp_path / "datasets")
    monkeypatch.setattr(ds, "HOME", tmp_path)
    monkeypatch.setattr(ds, "MAX_BUILDS", 1)
    monkeypatch.setattr(ds.subprocess, "Popen", _Started)
    first, second = _dataset(ds, "first-net-000000"), _dataset(ds, "second-net-00000")
    ds.launch(first)
    assert ds.builds_running() == [first]
    with pytest.raises(ds.DatasetError) as e:              # launched, though it has not taken its lock yet
        ds.launch(first)
    assert e.value.code == "building"
    with pytest.raises(ds.DatasetError) as e:
        ds.launch(second)
    assert e.value.code == "too_many_builds" and e.value.params == {"n": 1}
    monkeypatch.setattr(ds, "MAX_BUILDS", 2)
    monkeypatch.setenv("KAPACITET_DATASET", second)        # the server runs on it: never rebuilt under it
    with pytest.raises(ds.DatasetError) as e:
        ds.launch(second)
    assert e.value.code == "in_use"
    monkeypatch.delenv("KAPACITET_DATASET")
    (tmp_path / "active.json").write_text(json.dumps({"dataset": second}), encoding="utf-8")
    with pytest.raises(ds.DatasetError) as e:
        ds.launch(second)
    assert e.value.code == "in_use"


def test_a_build_is_cancelled_with_every_process_it_started(tmp_path, monkeypatch):
    import os
    import time

    from kapacitet import datasets as ds
    from kapacitet import procs

    monkeypatch.setattr(ds, "DATASETS", tmp_path / "datasets")
    monkeypatch.setattr(ds, "HOME", tmp_path)
    ds_id = _dataset(ds)
    kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"  # type: ignore[attr-defined]
              else {"start_new_session": True})
    # a build that has started a worker of its own, as compute's process pool does
    worker = tmp_path / "worker.pid"
    code = ("import pathlib, subprocess, sys, time; "
            "w = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"pathlib.Path({str(worker)!r}).write_text(str(w.pid)); time.sleep(60)")
    build = subprocess.Popen([sys.executable, "-c", code], **kwargs)
    try:
        for _ in range(500):
            if worker.exists() and worker.read_text():
                break
            time.sleep(0.02)
        rec = ds.record(ds_id)
        rec["steps"][1]["status"] = "running"
        (tmp_path / "datasets" / ds_id / "dataset.json").write_text(json.dumps({**rec, "status": "building"}))
        (tmp_path / "datasets" / ds_id / "build.lock").write_text(str(build.pid))
        assert ds.building(ds_id)
        done = ds.cancel(ds_id)
        build.wait(10)
        assert done["status"] == "failed" and done["cancelled"] and done["steps"][1]["status"] == "failed"
        assert not ds.building(ds_id) and not (tmp_path / "datasets" / ds_id / "build.lock").exists()
        with pytest.raises(ds.DatasetError) as e:
            ds.cancel(ds_id)
        assert e.value.code == "not_building"
        for _ in range(250):                      # the worker goes with it
            if not procs.alive(int(worker.read_text())):
                break
            time.sleep(0.02)
        assert not procs.alive(int(worker.read_text()))
    finally:
        if build.poll() is None:
            build.kill()
    assert not procs.alive(build.pid) and build.pid != os.getpid()


def test_a_build_past_its_time_is_stopped(tmp_path, monkeypatch):
    from kapacitet import datasets as ds

    monkeypatch.setattr(ds, "DATASETS", tmp_path / "datasets")
    monkeypatch.setattr(ds, "HOME", tmp_path)
    monkeypatch.setattr(ds, "BUILD_TIMEOUT_S", 60.0)
    ds_id = _dataset(ds)
    rec = ds.record(ds_id)
    rec.update(status="building", launched="2026-11-01T00:00:00+00:00", pid=__import__("os").getpid())
    (tmp_path / "datasets" / ds_id / "dataset.json").write_text(json.dumps(rec), encoding="utf-8")
    killed = []
    monkeypatch.setattr(ds.procs, "kill_tree", lambda pid: killed.append(pid) or True)
    assert ds.reap() == [ds_id] and killed == [__import__("os").getpid()]
    assert ds.record(ds_id)["error"] == "timed out after 60 s"


def test_the_household_assignment_is_unchanged_up_to_the_pools_size():
    from kapacitet.profiles.household import assignment

    first = np.random.default_rng(1000).permutation(160)
    assert (assignment(120, 0, n_pool=160) == first[:120]).all()
    big = assignment(400, 0, n_pool=160)
    assert len(big) == 400 and (big[:160] == first).all()
    assert np.bincount(big, minlength=160).min() >= 2


def test_a_house_without_a_roof_area_gets_no_solar():
    from kapacitet.profiles.pv import roof_geometry

    cust = pd.DataFrame({"cid": [0, 1], "polygon": [None, None], "footprint_m2": [0.0, 120.0], "fuse_kva": [13.8, 13.8],
                         "roof_shape": ["", ""], "roof_orientation": ["", ""]})
    roofs = roof_geometry(cust).set_index("cid")
    assert roofs.loc[0, "kwp"] == 0.0 and roofs.loc[0, "inverter_kw"] == 0.0
    assert roofs.loc[1, "kwp"] > 0.0


def test_the_network_map_is_valid_json_without_geometry():
    from kapacitet.api.main import _house_geometry, _line_geometry, _num, _point

    assert _num(float("nan")) is None and _num("x") is None and _num(2) == 2.0
    assert _house_geometry(None, float("nan"), float("nan")) is None
    assert _house_geometry(None, 18.0, 59.3) == {"type": "Point", "coordinates": [18.0, 59.3]}
    assert _line_geometry("[[NaN, NaN], [18.0, 59.3]]") is None
    feature = {"geometry": _house_geometry("", math.inf, 1.0), "properties": {"kwp": _num(float("nan"))}}
    json.dumps(feature, allow_nan=False)          # a NaN would raise here, as the API's encoder does


# ---------------------------------------------------------------- the networks as projects

def _computed(root, customers=27, static=62.6, hourly=77.5, quick=True):
    """The three small files a project list reads, as a build leaves them."""
    (root / "processed" / "grid").mkdir(parents=True, exist_ok=True)
    (root / "processed" / "results").mkdir(parents=True, exist_ok=True)
    (root / "processed" / "grid" / "summary.json").write_text(json.dumps(
        {"customers": customers, "feeders": 2, "buses": 56, "lines": 54, "trafo_type": "0.1 MVA 10/0.4 kV"}), encoding="utf-8")
    (root / "processed" / "results" / "meta.json").write_text(json.dumps(
        {"computed_at": "2026-11-12T10:00:00+00:00", "quick": quick, "reference_year": 2025, "years": [2023, 2024, 2025]}),
        encoding="utf-8")
    (root / "processed" / "results" / "hosting_summary.json").write_text(json.dumps(
        {"measures": {"static": {"kw_ac": {"median": static}}, "timeseries": {"kw_ac": {"median": hourly}}}}), encoding="utf-8")


def test_the_listing_says_what_each_network_is(api, tmp_path):
    """A project list shows a network's size, its transformer, when it was computed and the capacity
    of the static and of the hourly study: read from the network's own files, None when not there."""
    c, ds, _ = api
    empty = c.get("/api/v1/datasets").json()
    assert empty["items"] == [] and set(empty["reference"]["summary"].values()) == {None}
    _computed(tmp_path, customers=120, static=413.45, hourly=608.84, quick=False)
    ds_id = _dataset(ds)
    listed = c.get("/api/v1/datasets").json()
    ref = listed["reference"]["summary"]
    assert (ref["customers"], ref["feeders"], ref["trafo_type"]) == (120, 2, "0.1 MVA 10/0.4 kV")
    assert (ref["static_kw"], ref["hourly_kw"], ref["quick"]) == (413.45, 608.84, False)
    assert ref["years"] == [2023, 2025] and ref["n_years"] == 3 and ref["computed_at"].startswith("2026-11-12")
    item = listed["items"][0]
    assert item["id"] == ds_id and set(item["summary"].values()) == {None}      # uploaded, nothing computed
    _computed(ds.DATASETS / ds_id)
    item = c.get("/api/v1/datasets").json()["items"][0]
    assert item["summary"]["customers"] == 27 and item["summary"]["hourly_kw"] == 77.5
    # a file cut short is a network with nothing to show, never an error of the list
    (ds.DATASETS / ds_id / "processed" / "results" / "meta.json").write_text("{", encoding="utf-8")
    assert c.get("/api/v1/datasets").json()["items"][0]["summary"]["computed_at"] is None


def test_where_a_network_came_from_is_kept_and_a_templates_facts_are_the_catalogues(api):
    from kapacitet.importer import templates

    c, ds, _ = api
    files = templates.files("dickert-short")
    made = c.post("/api/v1/datasets", json={"name": "Short feeders", "files": files, "origin": {
        "kind": "template", "template": "dickert-short", "name": "anything the page says", "synthetic": False}})
    assert made.status_code == 200, made.text
    rec = made.json()
    assert rec["origin"] == templates.origin(templates.get("dickert-short")) and rec["origin"]["synthetic"] is True
    assert rec["customers"] == 12
    plain = c.post("/api/v1/datasets", json={"name": "An export", "files": files}).json()
    assert plain["origin"] == {"kind": "csv"}
    opened = c.post("/api/v1/datasets", json={"name": "From a file", "files": files, "origin": {
        "kind": "project_file", "name": "  kapacitet-project-x.zip\n", "synthetic": True}}).json()
    assert opened["origin"] == {"kind": "project_file", "name": "kapacitet-project-x.zip", "synthetic": True}
    before = len(list(ds.DATASETS.iterdir()))
    bad = c.post("/api/v1/datasets", json={"name": "No such", "files": files, "origin": {"kind": "template", "template": "nope"}})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "unknown_template"
    assert c.post("/api/v1/datasets", json={"name": "x", "files": files, "origin": {"kind": "drawn"}}).status_code == 422
    assert len(list(ds.DATASETS.iterdir())) == before                      # nothing written for either


def test_a_project_file_holds_the_tables_and_what_the_network_is(api):
    import io
    import zipfile

    from kapacitet.importer import templates

    c, ds, _ = api
    files = templates.files("dickert-short")
    rec = c.post("/api/v1/datasets", json={"name": "Short feeders", "files": files,
                                           "origin": {"kind": "template", "template": "dickert-short"}}).json()
    got = c.get(f"/api/v1/datasets/{rec['id']}/project.zip")
    assert got.status_code == 200 and got.headers["content-type"] == "application/zip"
    assert f'filename="kapacitet-project-{rec["id"]}.zip"' in got.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(got.content)) as z:
        assert sorted(z.namelist()) == ["README.md", "buses.csv", "customers.csv", "lines.csv", "project.json", "transformer.csv"]
        doc = json.loads(z.read("project.json"))
        assert doc["schema"] == ds.PROJECT_SCHEMA and doc["name"] == "Short feeders" and doc["customers"] == 12
        assert doc["origin"]["template"] == "dickert-short" and doc["origin"]["synthetic"] is True
        assert doc["tables"] == ["buses", "lines", "transformer", "customers"]
        # what goes out is what came in, so the file opens by the same door as any export
        assert {n: z.read(f"{n}.csv").decode("utf-8") for n in doc["tables"]} == files
        assert "bus_id" in z.read("README.md").decode("utf-8")
    # an export made on Windows ends its lines CR LF: it is stored as it came and leaves with one kind
    crlf = {k: v.replace("\n", "\r\n") for k, v in files.items()}
    win = c.post("/api/v1/datasets", json={"name": "From Windows", "files": crlf}).json()
    stored = (ds.DATASETS / win["id"] / "import" / "buses.csv").read_bytes()
    assert stored == crlf["buses"].encode("utf-8") and b"\r\r\n" not in stored
    assert ds.import_files(win["id"]) == files
    assert c.get("/api/v1/datasets/no-such-net/project.zip").status_code == 404
    assert c.get("/api/v1/datasets/..%2Fx/project.zip").status_code == 404
