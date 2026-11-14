"""The twelve exports of the technical review of 14-11-2026, each with a defect real NIS exports have.

The review wrote them from the reference network (revision_kapacitet/anexos/scripts/dirty_exports.py)
and ran `kapacitet check` on each: five of the twelve passed when they should not have, or ended in a
Python traceback (findings H1-H5). Here they are written from one of pandapower's example networks,
so they run without data, and each goes through both readers: the file reader of `kapacitet check`
and `kapacitet import`, and POST /check with the text as the browser decodes it. Both must say the
same: a structural defect and a defect of meaning alike stop the import, with the defect named.
"""

from __future__ import annotations

import copy
import csv
import io
import re

import pytest
from fastapi.testclient import TestClient

from kapacitet.importer import templates
from kapacitet.importer.validate import CP1252, ERROR, WARNING, decode, read_export, validate

BASE = "dickert-short"        # pandapower's Dickert LV network with short feeders: 12 houses


@pytest.fixture(scope="module")
def base() -> dict[str, list[dict]]:
    return {name: list(csv.DictReader(io.StringIO(text))) for name, text in templates.files(BASE).items()}


def write(folder, tables, sep=",", dec=".", enc="utf-8", bom=False):
    """The tables as a DSO's tools would write them: the separator, the decimal sign, the encoding."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        cols = list(rows[0])
        buf = io.StringIO()
        out = csv.writer(buf, delimiter=sep, lineterminator="\r\n")
        out.writerow(cols)
        for row in rows:
            values = []
            for col in cols:
                value = "" if row.get(col) is None else str(row.get(col))
                if dec == "," and re.fullmatch(r"-?\d+\.\d+(e-?\d+)?", value):
                    value = value.replace(".", ",")
                values.append(value)
            out.writerow(values)
        raw = buf.getvalue().encode(enc)
        (folder / f"{name}.csv").write_bytes((b"\xef\xbb\xbf" if bom else b"") + raw)


def swedish_names(t):
    for i, bus in enumerate(t["buses"][:10]):
        bus["name"] = f"Kabelskåp Ängsvägen {i}"


def repeated_customer(t):
    t["customers"].append(dict(t["customers"][0]))


def blank_fuses(t):
    for row in t["customers"][:5]:
        row["fuse_a"] = ""


def nis_cable_names(t):
    for row in t["lines"]:
        row["std_type"] = "N1XE-AR 4G150 (NIS-namn)"
        for col in ("r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka"):
            row.pop(col, None)


def ring(t):
    service = [b["bus_id"] for b in t["buses"] if b["kind"] == "service"]
    t["lines"].append({**t["lines"][0], "line_id": "ring-1", "from_bus": service[0], "to_bus": service[-1],
                       "length_m": "120"})


def kilometres(t):
    for row in t["lines"]:
        row["length_m"] = f"{float(row['length_m']) / 1000:.4f}"


def sweref(t):
    for i, bus in enumerate(t["buses"]):
        bus["lon"], bus["lat"] = f"{670000 + 10 * i:.1f}", f"{6582000 + 7 * i:.1f}"


def swedish_headers(t):
    names = {"line_id": "kabel_id", "from_bus": "från_nod", "to_bus": "till_nod", "length_m": "längd_m"}
    t["lines"] = [{names.get(k, k): v for k, v in row.items()} for row in t["lines"]]


def orphan(t):
    t["customers"][0]["bus"] = "no-such-bus"


def second_meters(t):
    t["customers"].extend({**row, "customer_id": row["customer_id"] + "-B"} for row in list(t["customers"][:20]))


# name: (what is wrong, how to change the tables, how they are written, usable, codes it must name)
VARIANTS = {
    "v00_base": ("the clean export", None, {}, True, set()),
    "v01_excel_sv": ("Swedish Excel in UTF-8: semicolon, decimal comma, BOM", None,
                     {"sep": ";", "dec": ",", "bom": True}, True, set()),
    "v02_cp1252": ("Swedish Excel's legacy CSV: Windows-1252, with å, ä and ö in the names", swedish_names,
                   {"sep": ";", "dec": ",", "enc": "cp1252"}, True, {"encoding_cp1252"}),
    "v03_dup_customer": ("one customer_id repeated", repeated_customer, {}, False, {"duplicate_identifier"}),
    "v04_missing_fuse": ("fuse_a blank for five customers", blank_fuses, {}, False, {"required_blank"}),
    "v05_unknown_cable": ("every cable type named as a Swedish NIS names it, without r/x/c", nis_cable_names, {},
                          False, {"cable_type_unknown"}),
    "v06_ring": ("a closed ring between two service points", ring, {}, False, {"meshed_network"}),
    "v07_length_km": ("lengths written in km in length_m", kilometres, {}, False, {"cable_length_units"}),
    "v08_sweref99": ("SWEREF 99 TM metres in lon/lat", sweref, {}, False, {"coordinates_sweref"}),
    "v09_sv_headers": ("lines.csv with Swedish column names", swedish_headers, {}, False, {"missing_column"}),
    "v10_orphan": ("a customer on a bus that does not exist", orphan, {}, False, {"orphan_customer"}),
    "v11_two_meters": ("a second meter on the same service point", second_meters, {}, True, set()),
}


def variant(base, name, folder):
    _, change, how, _, _ = VARIANTS[name]
    tables = copy.deepcopy(base)
    if change:
        change(tables)
    write(folder / name, tables, **how)
    return folder / name


def codes(report) -> set[str]:
    return {f.code for f in report.findings}


@pytest.mark.parametrize("name", list(VARIANTS))
def test_each_export_is_judged_as_the_review_asks(base, tmp_path, name):
    """Read as `kapacitet check` reads a folder: a report every time, never a traceback, and the
    defect named."""
    _, _, _, usable, named = VARIANTS[name]
    report = validate(read_export(variant(base, name, tmp_path)))
    assert report.usable is usable, report.text()
    assert named <= codes(report), report.text()
    if not usable:
        assert report.errors and all(f.message for f in report.errors)


@pytest.mark.parametrize("name", ["v01_excel_sv", "v02_cp1252", "v11_two_meters"])
def test_the_legitimate_variants_change_nothing_but_the_note(base, tmp_path, name):
    clean = codes(validate(read_export(variant(base, "v00_base", tmp_path))))
    found = codes(validate(read_export(variant(base, name, tmp_path)))) - {"encoding_cp1252"}
    assert found == clean


def test_windows_1252_is_read_whole_and_said(base, tmp_path):
    data = read_export(variant(base, "v02_cp1252", tmp_path))
    assert data["buses"]["name"].iloc[0] == "Kabelskåp Ängsvägen 0"
    # only buses.csv has a byte that is not UTF-8 (the names); the other tables are plain ASCII
    note = next(f for f in validate(data).findings if f.code == "encoding_cp1252")
    assert note.params["tables"] == "buses.csv" and note.severity == "note"


def test_the_unknown_cable_types_are_named_with_their_sections(base, tmp_path):
    report = validate(read_export(variant(base, "v05_unknown_cable", tmp_path)))
    unknown = next(f for f in report.errors if f.code == "cable_type_unknown")
    n = len(base["lines"])
    assert unknown.count == n and unknown.params["types"] == f"N1XE-AR 4G150 (NIS-namn) ({n})"


def test_the_default_cable_is_an_explicit_choice_that_stays_a_warning(base, tmp_path):
    """The one way out the check offers: build the unknown types with the site's default cable,
    which the project then shows as an assumption (task 1.2)."""
    from kapacitet.config import A

    data = read_export(variant(base, "v05_unknown_cable", tmp_path))
    report = validate(data, default_cable=True)
    assert report.usable, report.text()
    assumed = next(f for f in report.findings if f.code == "cable_type_unknown_assumed")
    assert assumed.severity == WARNING and assumed.params["cable"] == A("service_cable_type")


def test_kilometres_and_sweref_say_what_they_look_like(base, tmp_path):
    km = next(f for f in validate(read_export(variant(base, "v07_length_km", tmp_path))).errors
              if f.code == "cable_length_units")
    assert float(km.params["median"]) < 1
    xy = next(f for f in validate(read_export(variant(base, "v08_sweref99", tmp_path))).errors
              if f.code == "coordinates_sweref")
    assert xy.params == {"e": "670000", "n": "6582000"}


def test_a_blank_fuse_is_an_error_with_an_example_and_never_reaches_the_build(base, tmp_path):
    from kapacitet.importer.build import ImportError_, import_directory

    folder = variant(base, "v04_missing_fuse", tmp_path)
    blank = next(f for f in validate(read_export(folder)).errors if f.code == "required_blank")
    assert blank.count == 5 and blank.params["column"] == "fuse_a"
    assert blank.params["example"] == base["customers"][0]["customer_id"]
    with pytest.raises(ImportError_):
        import_directory(folder, save_to_disk=False)


def test_text_in_a_number_column_is_named_not_a_traceback(base, tmp_path):
    tables = copy.deepcopy(base)
    tables["lines"][0]["length_m"] = "12 m"
    tables["transformer"][0]["sn_mva"] = "0,4"            # a decimal comma in a comma-separated file
    write(tmp_path / "words", tables)
    report = validate(read_export(tmp_path / "words"))
    named = {(f.params["table"], f.params["column"]) for f in report.errors if f.code == "not_a_number"}
    assert named == {("lines", "length_m"), ("transformer", "sn_mva")}


# ---------------------------------------------------------------- the browser's door, POST /check

@pytest.fixture(scope="module")
def client():
    from kapacitet.api.main import app

    return TestClient(app)


def as_the_browser_sends(folder) -> dict:
    """The tables as web/src/lib/decode.ts reads a chosen file: bytes, UTF-8 or else Windows-1252."""
    files, decoded = {}, {}
    for path in sorted(folder.glob("*.csv")):
        text, encoding = decode(path.read_bytes())
        files[path.stem] = text
        if encoding == CP1252:
            decoded[path.stem] = encoding
    return {"files": files, "decoded_as": decoded}


@pytest.mark.parametrize("name", list(VARIANTS))
def test_the_api_judges_each_export_as_the_command_does(base, tmp_path, client, name):
    """Before, POST /check read every table with commas and decimal points, so a semicolon export
    from Swedish Excel came out as one column (found while planning the fixes of 14-11-2026)."""
    folder = variant(base, name, tmp_path)
    r = client.post("/api/v1/check", json=as_the_browser_sends(folder))
    assert r.status_code == 200, r.text
    body = r.json()
    command = validate(read_export(folder))
    assert body["usable"] is command.usable
    # with results, the API prices the missing phases (phase_not_recorded_cost); the command cannot
    same = {"phase_not_recorded_cost": "phase_not_recorded"}
    assert {same.get(f["code"], f["code"]) for f in body["findings"]} == codes(command)


def test_the_api_takes_the_default_cable_as_an_option(base, tmp_path, client):
    payload = as_the_browser_sends(variant(base, "v05_unknown_cable", tmp_path))
    assert not client.post("/api/v1/check", json=payload).json()["usable"]
    body = client.post("/api/v1/check", json={**payload, "options": {"default_cable": True}}).json()
    assert body["usable"]
    assert {f["code"] for f in body["findings"] if f["severity"] == "warning"} >= {"cable_type_unknown_assumed"}
    assert not [f for f in body["findings"] if f["severity"] == ERROR]
