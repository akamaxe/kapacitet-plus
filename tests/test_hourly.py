"""The hourly study's deployment (engine/hourly.py), which the capacity screen and the map show where
the envelopes are off: the order whose hourly capacity is the median, the houses each study connects,
its year without control and what the next house would break. On the tiny grid, without data."""

from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from conftest import processed_at, synthetic_profiles


@pytest.fixture(scope="module")
def deployment(tiny_processed, tmp_path_factory):
    """A copy of the tiny grid's folder with its capacity computed for three orders, and the
    hourly deployment of it; the copy keeps the shared folder as it was."""
    from kapacitet.config import A
    from kapacitet.engine import hourly, run

    folder = tmp_path_factory.mktemp("hourly") / "processed"
    shutil.copytree(tiny_processed, folder)
    with processed_at(folder, synthetic_profiles.YEARS):
        _, summary = run.capacity(synthetic_profiles.YEARS, 3, A("z_star_pct"), workers=1)
        doc, state = hourly.compute(summary)
        yield folder, summary, doc, state


def test_the_median_order_of_the_hourly_study_is_taken(deployment):
    from kapacitet.engine import hourly

    _, summary, doc, _ = deployment
    kws = sorted(r["timeseries"]["kw_ac"] for r in summary["per_order"])
    picked = next(r for r in summary["per_order"] if r["order"] == doc["order"])
    assert picked["timeseries"]["kw_ac"] == kws[len(kws) // 2]
    assert hourly.pick({"per_order": []}) is None


def test_the_deployment_names_each_studys_houses_and_breaks_no_limit(deployment):
    _, _, doc, _ = deployment
    ms, mts = doc["static_adopters"], doc["timeseries_adopters"]
    assert len(doc["static_cids"]) == ms and len(doc["hourly_cids"]) == mts - ms
    assert not set(doc["static_cids"]) & set(doc["hourly_cids"])
    # the hourly study's own count: no hour of the reference year above a limit, without control
    assert doc["verification"]["violation_hours"] == 0
    assert doc["timeseries_kw_ac"] >= doc["static_kw_ac"]


def test_the_next_house_says_the_limit_it_breaks(deployment):
    _, _, doc, _ = deployment
    nxt = doc["next"]
    if doc["timeseries_adopters"] >= doc["customers"]:
        assert nxt == {"fits_all": True}
    else:
        assert nxt["fits_all"] is False and nxt["adopters"] == doc["timeseries_adopters"] + 1
        assert nxt["hours"] > 0 and nxt["binding"] in ("trafo", "line", "voltage")
        assert nxt["counts"][nxt["binding"]] == max(nxt["counts"].values())


def test_it_is_written_where_asked(deployment, tmp_path):
    from kapacitet.engine import hourly

    folder, _, doc, state = deployment
    with processed_at(folder, synthetic_profiles.YEARS):
        where = hourly.write(doc, state, tmp_path / "cache")
    assert {p.name for p in where.values()} == {"hourly.json", "state_hourly.json", "state_hourly.bin",
                                                 "state_hourly_static.json", "state_hourly_static.bin"}
    assert json.loads(where["doc"].read_text(encoding="utf-8"))["order"] == doc["order"]
    assert json.loads(where["hourly.json"].read_text(encoding="utf-8"))["name"] == "hourly"
    assert json.loads(where["hourly_static.json"].read_text(encoding="utf-8"))["name"] == "hourly_static"
    assert doc["static_violation_hours"] == 0


def test_the_server_computes_it_once_and_keeps_it_in_its_cache(deployment):
    """The reference network's published results predate the module: the server computes the
    deployment the first time it is asked, into requests/cache/hourly/, never into processed/."""
    from kapacitet.api.main import app

    folder, _, doc, _ = deployment
    with processed_at(folder, synthetic_profiles.YEARS):
        client = TestClient(app)
        r = client.get("/api/v1/hourly")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["order"] == doc["order"]
        cache = folder.parent / "requests" / "cache" / "hourly"
        assert (cache / "hourly.json").exists() and not (folder / "results" / "hourly.json").exists()
        assert client.get("/api/v1/state/hourly").json()["name"] == "hourly"
        assert client.get("/api/v1/state/hourly_static").json()["name"] == "hourly_static"
        houses = [f["properties"] for f in client.get("/api/v1/network").json()["features"]
                  if f["properties"].get("layer") == "house"]
        status = {h["cid"]: h["hourly_status"] for h in houses}
        assert {c for c, s in status.items() if s == "static"} == set(doc["static_cids"])
        assert {c for c, s in status.items() if s == "hourly"} == set(doc["hourly_cids"])
