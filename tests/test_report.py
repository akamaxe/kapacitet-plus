"""The report a DSO files (src/kapacitet/report/, task 2.6 of the technical review).

The DOCX is written with the standard library; these open it again, check it is a package a word
processor reads, and check that each figure in the report is the one in its file.
"""
from __future__ import annotations

import io
import json
import zipfile
from xml.etree import ElementTree

import pytest

from conftest import needs_results
from kapacitet.report.build import project_report, sv
from kapacitet.report.docx import Document, text_of


def test_the_package_is_one_a_word_processor_opens():
    doc = Document("Rapport <&> \"test\"", header="Huvud", footer="Fot")
    doc.heading("Rubrik").paragraph("Rad ett\nrad två", italic=True).table(["A", "B"], [["1", None], ["Å", "ö"]])
    data = doc.to_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml", "word/header1.xml",
                "word/footer1.xml", "docProps/core.xml"} <= names
        for name in names:
            ElementTree.fromstring(z.read(name))          # every part is well-formed XML
    text = text_of(data).splitlines()
    assert text[0] == 'Rapport <&> "test"' and "Rubrik" in text and "Å" in text and "–" in text


def test_the_report_can_be_had_without_results_and_says_what_it_lacks(tmp_path):
    data = project_report(tmp_path).to_bytes()
    text = text_of(data)
    assert "Kapacitetsrapport" in text and "inte granskats" in text and "Väder och priser från referensplatsen." in text


def test_the_api_and_the_command_give_the_report(tmp_path):
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app
    from kapacitet.pipeline import main

    r = TestClient(app).get("/api/v1/report.docx")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert zipfile.is_zipfile(io.BytesIO(r.content))
    out = tmp_path / "rapport.docx"
    main(["report", "--out", str(out)])
    assert zipfile.is_zipfile(out) and "Kapacitetsrapport" in text_of(out.read_bytes())


@needs_results
def test_every_figure_of_the_report_is_the_one_in_its_file():
    from kapacitet.paths import processed_ro

    def read(*parts):
        return json.loads(processed_ro(*parts).read_text(encoding="utf-8"))

    lines = set(text_of(project_report().to_bytes()).splitlines())
    hosting, consumption, grid = read("results", "hosting_summary.json"), read("results", "consumption.json"), \
        read("grid", "summary.json")
    for key in ("static", "timeseries"):
        kw = hosting["measures"][key]["kw_ac"]
        assert sv(kw["median"]) in lines, key
        assert f"{sv(kw['p10'])}–{sv(kw['p90'])}" in lines, key
    for device in ("ev", "flat", "hp"):
        assert sv(consumption["devices"][device]["adopters"]["median"], 1) in lines, device
    assert sv(grid["customers"]) in lines and grid["trafo_type"] in lines
    shape = read("results", "validation.json")["household_shape"]
    assert any(sv(shape["corr_hour_of_day"], 3) in line for line in lines)
    assert f"{sv(hosting['static_definition']['pv_factor'], 2)} × AC-märkeffekt" in lines
