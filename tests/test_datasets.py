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
