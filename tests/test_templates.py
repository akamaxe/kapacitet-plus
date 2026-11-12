"""The example networks a project can start from (importer/templates.py, GET /api/v1/templates).

pandapower builds them; nothing here needs the pipeline's output.
"""

from __future__ import annotations

import io
import re
import warnings

import pandas as pd
import pytest

from kapacitet.importer import templates

warnings.filterwarnings("ignore")


def test_the_catalogue_says_what_pandapower_builds():
    """Listing the templates builds nothing, so the customers and the transformer are written in
    the catalogue. They have to be the network's own."""
    assert len(templates.TEMPLATES) == 13 == len(templates.BY_ID)
    for t in templates.TEMPLATES:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]{2,23}", t.id), t.id          # fits a network's id
        assert t.generator.startswith("pandapower.networks.") and t.name
        net = t.make()
        assert len(net.load) == t.customers, t.id
        assert float(net.trafo["sn_mva"].iloc[0]) * 1000.0 == pytest.approx(t.trafo_kva), t.id
    assert templates.listing() == [
        {"id": t.id, "name": t.name, "customers": t.customers, "trafo_kva": t.trafo_kva, "generator": t.generator,
         "synthetic": True} for t in templates.TEMPLATES]


@pytest.mark.parametrize("template_id", [t.id for t in templates.TEMPLATES])
def test_every_template_enters_by_the_contract(template_id):
    from kapacitet.importer.contract import TABLES
    from kapacitet.importer.validate import validate

    files = templates.files(template_id)
    assert list(files) == [t.name for t in TABLES]
    data = {k: pd.read_csv(io.StringIO(v)) for k, v in files.items()}
    report = validate(data)
    assert report.usable, [f.code for f in report.errors]
    assert len(data["customers"]) == templates.get(template_id).customers
    # every house has a fuse and a roof area, and every bus a place
    assert data["customers"]["fuse_a"].isin(templates.FUSES).all()
    assert data["customers"]["footprint_m2"].between(80.0, 200.0).all()
    assert data["buses"][["lon", "lat"]].notna().all().all()


def test_a_template_is_the_same_network_every_time():
    from kapacitet.config import settings

    first, again = templates.files("kerber-land-kabel"), templates.files("kerber-land-kabel")
    assert first == again
    # two templates do not share a draw of fuses
    other = templates.files("kerber-land-ol")
    assert first["customers"] != other["customers"]
    buses = pd.read_csv(io.StringIO(first["buses"]))
    site = settings().site
    assert abs(buses["lat"].mean() - site.center_lat) < 0.02 and abs(buses["lon"].mean() - site.center_lon) < 0.04
    origin = templates.origin(templates.get("kerber-land-kabel"))
    assert origin["synthetic"] is True and origin["kind"] == "template" and len(origin["synthetic_parts"]) == 3


def test_the_demo_and_the_scale_study_draw_from_the_same_helpers():
    from kapacitet.study import demo

    assert demo.customer_map is templates.customer_map and demo.layout is templates.layout
    assert (demo.FUSES, demo.FUSE_SHARE) == (templates.FUSES, templates.FUSE_SHARE)


def test_the_api_lists_the_templates_and_gives_ones_tables():
    from fastapi.testclient import TestClient

    from kapacitet.api import main

    with TestClient(main.app) as c:
        listed = c.get("/api/v1/templates")
        assert listed.status_code == 200
        body = listed.json()
        assert [t["id"] for t in body["templates"]] == [t.id for t in templates.TEMPLATES]
        assert all(t["synthetic"] is True for t in body["templates"]) and body["synthetic_parts"]
        one = c.get("/api/v1/templates/dickert-short")
        assert one.status_code == 200
        doc = one.json()
        assert set(doc["files"]) == {"buses", "lines", "transformer", "customers"}
        assert doc["files"] == templates.files("dickert-short") and doc["origin"]["template"] == "dickert-short"
        missing = c.get("/api/v1/templates/no-such-network")
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "unknown_template"


def test_pandapowers_own_draw_is_fixed_too():
    """The Kerber cable networks give each house one of two service cables with `random.random()`:
    two builds of one network differed, so a template, the study demo and the scale study were not
    the same network twice. The draw is fixed while the network is built, and the caller's own
    random state is left as it was."""
    import random

    import pandapower.networks as pn

    def cables(seed):
        with templates.seeded(seed):
            return list(pn.kb_extrem_dorfnetz().line["std_type"])

    assert cables(7) == cables(7) and cables(7) != cables(8)
    random.seed(1)
    expected = random.random()
    random.seed(1)
    cables(99)
    assert random.random() == expected
    lines = [templates.files("kerber-dorf")["lines"] for _ in range(2)]
    assert lines[0] == lines[1]
