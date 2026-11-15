"""The studies in the workbench (api/studies.py, task 2.1 of the technical review of 14-11-2026).

A DSO creates, builds, reviews, signs and publishes a study of many stations from the app, without a
terminal. Every route calls the study module the command line calls, so what the app publishes is,
byte for byte, what `kapacitet study publish` publishes on the same study. These run without data:
the stations are given their results by hand, as tests/test_study_publish.py gives them, and the
build's process is a stand-in.
"""
from __future__ import annotations

import csv
import io
import json
import sys

import pytest
from fastapi.testclient import TestClient

from kapacitet import procs
from kapacitet.importer import templates

API = "/api/v1"
Q1, Q2 = "2026Q3", "2026Q4"


@pytest.fixture(scope="module")
def client():
    from kapacitet.api.main import app

    return TestClient(app)


@pytest.fixture
def studies(tmp_path, monkeypatch):
    """The studies' folder of this test."""
    folder = tmp_path / "studies"
    monkeypatch.setenv("KAPACITET_STUDY_DIR", str(folder))
    return folder


def _built(studies, name="Testnät AB"):
    """A study whose four stations have results, as test_study_publish builds them, in the folder."""
    from test_study_publish import STATIONS, put

    from kapacitet.study import init_study

    study = init_study(studies / "testnat-abc123", name)
    for sid, v in STATIONS.items():
        put(study, sid, v["customers"], v["lon"], v["lat"], v["gen"], v["cons"])
    return study


def test_the_demonstration_is_made_listed_and_opened(client, studies):
    r = client.post(f"{API}/studies", json={"kind": "demo", "name": "Demo Elnät AB"})
    assert r.status_code == 200, r.text
    made = r.json()["study"]
    assert made["stations"] == 3 and "synthetic" in made["name"] and not made["building"]
    listed = client.get(f"{API}/studies").json()
    assert listed["folder"] == str(studies) and [s["id"] for s in listed["items"]] == [made["id"]]
    one = client.get(f"{API}/studies/{made['id']}").json()
    assert len(one["stations"]) == 3 and one["runs"] == [] and one["current_run"]
    assert all(s["status"] == "imported" and s["generation_kw"] is None for s in one["stations"])
    # checking the stations again is the import of the command line
    reports = client.post(f"{API}/studies/{made['id']}/import", json={}).json()["reports"]
    assert set(reports) == {s["id"] for s in one["stations"]} and all(r["usable"] for r in reports.values())


def test_an_empty_study_waits_for_its_stations(client, studies):
    made = client.post(f"{API}/studies", json={"kind": "empty", "name": "Tom"}).json()["study"]
    assert made["stations"] == 0
    r = client.post(f"{API}/studies/{made['id']}/build", json={"quick": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "study_no_network"


def _station_tables() -> dict[str, str]:
    return dict(templates.files("dickert-short"))


def test_contract_folders_become_stations_checked_at_once(client, studies):
    body = {"kind": "contract", "name": "Från NIS", "stations": {"st-001": _station_tables(), "st-002": _station_tables()}}
    r = client.post(f"{API}/studies", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["study"]["stations"] == 2 and set(out["reports"]) == {"st-001", "st-002"}
    assert all(rep["usable"] for rep in out["reports"].values())


def test_a_bad_station_id_or_table_is_refused_and_nothing_is_made(client, studies):
    bad = client.post(f"{API}/studies", json={"kind": "contract", "stations": {"../x": _station_tables()}})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "study_bad_station"
    table = client.post(f"{API}/studies", json={"kind": "contract", "stations": {"s1": {"secret": "a,b\n"}}})
    assert table.status_code == 422 and table.json()["detail"]["code"] == "study_bad_table"
    assert not studies.exists() or not any(studies.iterdir())
    for path in ("/studies/..%2Fetc", "/studies/nope-000000"):
        assert client.get(f"{API}{path}").status_code in (404, 422)


def test_the_build_starts_its_own_process_and_says_it_is_building(client, studies, monkeypatch):
    from kapacitet.api import studies as mod

    made = client.post(f"{API}/studies", json={"kind": "demo"}).json()["study"]
    started = {}

    class Proc:
        pid = 4242

    def popen(cmd, **kwargs):
        started.update(cmd=cmd, cwd=kwargs.get("cwd"))
        return Proc()

    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    monkeypatch.setattr(mod.procs, "alive", lambda pid: pid == 4242)
    r = client.post(f"{API}/studies/{made['id']}/build", json={"quick": True, "force": True})
    assert r.status_code == 200, r.text
    assert started["cmd"][1:] == ["-m", "kapacitet", "study", "build", str(studies / made["id"]), "--quick", "--force"]
    assert client.get(f"{API}/studies/{made['id']}").json()["study"]["building"]
    again = client.post(f"{API}/studies/{made['id']}/build", json={"quick": True})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "study_building"
    killed = []
    monkeypatch.setattr(mod.procs, "kill_tree", lambda pid: killed.append(pid) or True)
    assert client.post(f"{API}/studies/{made['id']}/cancel", json={}).status_code == 200 and killed == [4242]
    monkeypatch.setattr(mod.procs, "alive", lambda pid: False)
    assert not client.get(f"{API}/studies/{made['id']}").json()["study"]["building"]
    gone = client.post(f"{API}/studies/{made['id']}/cancel", json={})
    assert gone.status_code == 409 and gone.json()["detail"]["code"] == "study_not_building"


def test_a_run_is_collected_reviewed_corrected_signed_published_and_compared(client, studies):
    study = _built(studies)
    base = f"{API}/studies/{study.id}"
    detail = client.get(base).json()
    s1 = next(s for s in detail["stations"] if s["id"] == "S1")
    assert (s1["status"], s1["generation_kw"], s1["consumption_kw"], s1["generation_limit"]) == ("ready", 120.0, 80.0, "voltage")
    assert s1["margin_kw"] == 80.0

    # a review before the run is collected is the study's own refusal, under its code
    early = client.post(f"{base}/runs/{Q2}/review", json={})
    assert early.status_code == 409 and early.json()["detail"]["code"] == "study_not_collected"

    for q in (Q1, Q2):
        assert client.post(f"{base}/runs/{q}/collect", json={}).json()["rows"] == 4
    reviewed = client.post(f"{base}/runs/{Q2}/review", json={}).json()
    assert [r["station_id"] for r in reviewed["rows"]][:1] == ["S3"]          # the one without a figure first
    fix = "station_id;field;value;reason;by\nS1;generation_kw;100;cable joint;planner\n"
    corrected = client.put(f"{base}/runs/{Q2}/corrections", json={"csv": fix}).json()
    assert next(r for r in corrected["rows"] if r["station_id"] == "S1")["corrected_generation_kw"] == 100.0

    bad = client.post(f"{base}/runs/{Q2}/sign", json={"by": "nobody"})
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "study_bad_signer"
    signed = client.post(f"{base}/runs/{Q2}/sign", json={"by": "Anna Planerare, nätplanerare"}).json()
    assert signed["by"] == "Anna Planerare, nätplanerare" and signed["outputs"] > 0
    published = client.post(f"{base}/runs/{Q2}/publish", json={}).json()
    assert published["status"] == "signerad" and published["cells"] > 0
    compared = client.post(f"{base}/runs/{Q2}/diff", json={}).json()
    assert compared["since"] == Q1
    runs = {r["run"]: r for r in compared["runs"]}
    assert runs[Q2] == {"run": Q2, "collected": True, "reviewed": True, "signed": True, "published": "signerad",
                        "compared": True}

    files = client.get(f"{base}/runs/{Q2}/files").json()["files"]
    paths = {f["path"] for f in files}
    assert {"review.csv", "signoff.json", "changes.csv", "publication/capacity.csv", "publication/manifest.json"} <= paths
    got = client.get(f"{base}/runs/{Q2}/files/publication/capacity.csv")
    assert got.status_code == 200 and got.content == (study.runs / Q2 / "publication" / "capacity.csv").read_bytes()
    for path in ("../../study.toml", "publication/../../../study.toml", "nothing.csv"):
        assert client.get(f"{base}/runs/{Q2}/files/{path}").status_code == 404


def test_the_app_publishes_byte_for_byte_what_the_command_line_publishes(client, studies):
    from kapacitet.pipeline import main

    study = _built(studies)
    base = f"{API}/studies/{study.id}"
    client.post(f"{base}/runs/{Q2}/collect", json={})
    client.post(f"{base}/runs/{Q2}/review", json={})
    client.post(f"{base}/runs/{Q2}/sign", json={"by": "Anna Planerare, nätplanerare"})
    assert client.post(f"{base}/runs/{Q2}/publish", json={}).status_code == 200
    folder = study.runs / Q2 / "publication"
    by_app = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    main(["study", "publish", str(study.root), "--run", Q2])
    by_cli = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert by_app.keys() == by_cli.keys() and len(by_app) > 3
    assert [p for p in by_app if by_app[p] != by_cli[p]] == []


def test_the_studies_router_writes_only_in_the_studies_folder(client, studies, tmp_path):
    before = {p for p in tmp_path.rglob("*") if p.is_file() and studies not in p.parents}
    made = client.post(f"{API}/studies", json={"kind": "demo"}).json()["study"]
    client.post(f"{API}/studies/{made['id']}/import", json={})
    after = {p for p in tmp_path.rglob("*") if p.is_file() and studies not in p.parents}
    assert after == before


def test_meter_readings_are_uploaded_to_a_study_and_checked_against_its_customers(client, studies):
    """Task 2.4: the study's inputs/meters, in local time here, checked against every station's customers."""
    from kapacitet.study import open_study

    made = client.post(f"{API}/studies", json={"kind": "demo"}).json()["study"]
    study = open_study(studies / made["id"])
    sid = study.station_ids()[0]
    import pandas as pd

    customer = str(pd.read_csv(study.station_dir(sid) / "import" / "customers.csv", dtype=str)["customer_id"].iloc[0])
    rows = "\n".join(f"{customer},2025-05-01T{h:02d}:{m:02d}:00,0.1" for h in range(24) for m in (0, 15, 30, 45))
    r = client.put(f"{API}/studies/{made['id']}/meters", json={"files": {"lokal.csv": f"customer_id,start,kwh_import\n{rows}\n"}})
    assert r.status_code == 200, r.text
    report = r.json()["report"]
    assert report["usable"] and "meter_local_time" in {f["code"] for f in report["findings"]}
    assert (study.inputs / "meters" / "lokal.csv").is_file()
