"""Swedish cable types, and the table that translates a network information system's names.

A NIS names its cables its own way, which pandapower's library does not know (finding H1 of the
technical review of 14-11-2026). Task 2.2 answers with a catalogue of Swedish low-voltage cables,
each type with its manufacturer's datasheet, and an optional `cable_types.csv` in the export that
translates each name to a type of the catalogue or to its own data. Here, on pandapower's 12-house
Dickert network, so they run without data:

- every type of the catalogue cites its source, and one without a source does not enter;
- a network with Swedish names is built with the catalogue's values;
- a name the table, the catalogue and the library do not know is an error that names it.
"""

from __future__ import annotations

import csv
import io
import re

import pytest
from fastapi.testclient import TestClient

from kapacitet.importer import cables, templates
from kapacitet.importer.build import build_from_export
from kapacitet.importer.validate import ERROR, read_files, validate

BASE = "dickert-short"        # pandapower's Dickert LV network with short feeders: 12 houses
NIS = "N1XE-AR 4G150 (NIS-namn)"


def as_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    out = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    out.writeheader()
    out.writerows(rows)
    return buf.getvalue()


def export(names: list[str], table: list[dict] | None = None) -> dict[str, str]:
    """The example's tables, its sections named in turn from `names` and without electrical data
    of their own, and `table` as its cable_types.csv."""
    files = dict(templates.files(BASE))
    lines = list(csv.DictReader(io.StringIO(files["lines"])))
    for i, row in enumerate(lines):
        row["std_type"] = names[i % len(names)]
        for col in cables.FIELDS:
            row.pop(col, None)
    files["lines"] = as_text(lines)
    if table is not None:
        files["cable_types"] = as_text(table)
    return files


def built(files: dict[str, str]):
    network, _ = build_from_export(read_files(files))
    return network.net.line


# ------------------------------------------------------------------------------------ the catalogue

def test_every_type_of_the_catalogue_cites_its_source():
    with open(cables.CATALOGUE, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) >= 6
    for row in rows:
        name = row["type"]
        assert row["source"].strip() and row["rating"].strip(), name
        assert re.match(r"https://\S+$", row["url"]), name
        assert re.fullmatch(r"20\d\d-\d\d-\d\d", row["accessed"]), name
        values = {k: float(row[k]) for k in cables.FIELDS}
        assert values["r_ohm_per_km"] > 0 and values["x_ohm_per_km"] > 0 and values["max_i_ka"] > 0, name
        assert values["c_nf_per_km"] >= 0, name
    assert set(cables.catalogue()) == {r["type"] for r in rows}


@pytest.mark.parametrize("blank", ["source", "url", "accessed"])
def test_a_type_without_its_source_does_not_enter(tmp_path, monkeypatch, blank):
    row = {"type": "X 4x50", "r_ohm_per_km": "0.6", "x_ohm_per_km": "0.07", "c_nf_per_km": "0",
           "max_i_ka": "0.1", "source": "a datasheet", "url": "https://example.org/x.pdf",
           "accessed": "2026-11-15", blank: ""}
    path = tmp_path / "cable_types_se.csv"
    path.write_text(as_text([row]), encoding="utf-8")
    monkeypatch.setattr(cables, "CATALOGUE", path)
    cables.catalogue.cache_clear()
    try:
        with pytest.raises(cables.CatalogueError, match=blank):
            cables.catalogue()
    finally:
        cables.catalogue.cache_clear()


# ------------------------------------------------------------------- names built with its values

def test_a_catalogue_name_in_lines_csv_is_built_with_the_catalogue_values():
    axqj = cables.catalogue()["AXQJ 4x150/41"]
    files = export(["AXQJ 4x150/41"])
    assert validate(read_files(files)).usable
    line = built(files)
    for k in cables.FIELDS:
        assert line[k].tolist() == pytest.approx([axqj[k]] * len(line)), k


def test_a_nis_name_is_built_with_the_type_its_table_translates_it_to():
    files = export([NIS], [{"name": NIS, "type": "AXQJ 4x240/72"}])
    report = validate(read_files(files))
    assert report.usable, report.text()
    assert not [f for f in report.findings if f.code.startswith("cable_")]
    line = built(files)
    axqj = cables.catalogue()["AXQJ 4x240/72"]
    assert line["r_ohm_per_km"].tolist() == pytest.approx([axqj["r_ohm_per_km"]] * len(line))
    assert line["max_i_ka"].tolist() == pytest.approx([axqj["max_i_ka"]] * len(line))


def test_a_row_with_its_own_data_needs_no_catalogue_type_and_its_values_win():
    own = {"name": NIS, "type": "AXQJ 4x150/41", "r_ohm_per_km": "0.21", "x_ohm_per_km": "",
           "c_nf_per_km": "", "max_i_ka": "0.25"}
    line = built(export([NIS], [own]))
    axqj = cables.catalogue()["AXQJ 4x150/41"]
    assert line["r_ohm_per_km"].tolist() == pytest.approx([0.21] * len(line))
    assert line["x_ohm_per_km"].tolist() == pytest.approx([axqj["x_ohm_per_km"]] * len(line))
    assert line["max_i_ka"].tolist() == pytest.approx([0.25] * len(line))

    alone = {"name": NIS, "type": "", "r_ohm_per_km": "0.3", "x_ohm_per_km": "0.08", "c_nf_per_km": "0",
             "max_i_ka": "0.2"}
    assert built(export([NIS], [alone]))["r_ohm_per_km"].tolist() == pytest.approx([0.3] * len(line))


# ------------------------------------------------------------------- a missing name is named

def test_a_name_the_table_leaves_out_is_an_error_that_names_it():
    other = "KABEL 95 AL (NIS)"
    report = validate(read_files(export([NIS, other], [{"name": NIS, "type": "AXQJ 4x150/41"}])))
    assert not report.usable
    unknown = next(f for f in report.errors if f.code == "cable_type_unknown")
    assert other in unknown.params["types"] and NIS not in unknown.params["types"]
    assert [x["name"] for x in unknown.params["names"]] == [other]
    assert unknown.params["names"][0]["count"] == unknown.count


def test_a_translation_to_a_type_the_catalogue_lacks_is_an_error_that_names_both():
    report = validate(read_files(export([NIS], [{"name": NIS, "type": "AXQJ 4x999/99"}])))
    assert not report.usable
    wrong = next(f for f in report.errors if f.code == "cable_map_type")
    assert wrong.params == {"name": NIS, "type": "AXQJ 4x999/99"}
    # and the name it failed to translate is still named as unknown
    assert {f.code for f in report.errors} >= {"cable_type_unknown"}


# ------------------------------------------------------------------------------------ the API

@pytest.fixture(scope="module")
def client():
    from kapacitet.api.main import app

    return TestClient(app)


def test_the_api_lists_the_catalogue_with_each_source(client):
    body = client.get("/api/v1/cable-types").json()
    types = {t["type"]: t for t in body["types"]}
    assert set(types) == set(cables.catalogue())
    assert all(t["source"] and t["url"].startswith("https://") for t in types.values())
    assert types["AXQJ 4x150/41"]["r_ohm_per_km"] == pytest.approx(0.206)


def test_the_api_check_reads_the_translation_like_any_table(client):
    files = export([NIS])
    assert not client.post("/api/v1/check", json={"files": files}).json()["usable"]
    files["cable_types"] = f"name,type\n{NIS},AXQJ 4x150/41\n"
    body = client.post("/api/v1/check", json={"files": files}).json()
    assert body["usable"], body["text"]
    assert not [f for f in body["findings"] if f["severity"] == ERROR]
