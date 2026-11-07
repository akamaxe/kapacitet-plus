"""The method dossier for Ei (kapacitet/dossier.py): every number in the Swedish text is a fact of
method_dossier.json, each fact's text is its value formatted, and the template holds no figure.
The demo is built on an empty data folder and on a stand-in results folder written here, and a
study on stand-in stations, so CI runs all of it; the last test reads the real results."""

from __future__ import annotations

import json
import re

import pytest

from conftest import needs_results
from kapacitet import dossier
from kapacitet.dossier import PLACEHOLDER, TEMPLATE, build, numbers, sv_number


def assert_every_number_is_a_fact(text: str, doc: dict) -> None:
    allowed: set[str] = set()
    for fact in doc["facts"].values():
        allowed.update(numbers(fact["text"]))
        if "decimals" in fact:     # a number's text is its value, formatted, and nothing else
            assert fact["text"] == sv_number(fact["value"], fact["decimals"], fact.get("fixed", False)), fact
        assert fact["source"].strip(), fact
    stray = sorted({n for n in numbers(text) if n not in allowed})
    assert not stray, f"numbers in the text that no fact holds: {stray}"
    assert "{{" not in text and "}}" not in text
    json.dumps(doc)


def test_the_template_has_no_digit_outside_its_placeholders():
    text = PLACEHOLDER.sub("", TEMPLATE.read_text(encoding="utf-8"))
    found = [(i + 1, line) for i, line in enumerate(text.splitlines()) if re.search(r"\d", line)]
    assert not found, f"figures typed into the template: {found}"


def test_numbers_are_written_the_swedish_way():
    assert sv_number(787.3958) == "787"
    assert sv_number(2.8341, 2) == "2,83"
    assert sv_number(3.0, 1) == "3"
    assert sv_number(1.0, 2, fixed=True) == "1,00"
    assert sv_number(54745.2) == "54 745"
    assert sv_number(1240) == "1240"
    assert sv_number(-0.2053, 1) == "−0,2"
    assert sv_number(None) == "–"
    assert numbers("54 745 kWh, 2,83 % och 1240 h") == ["54 745", "2,83", "1240"]


def test_on_an_empty_data_folder_every_section_says_what_is_missing(tmp_path):
    text, doc = build(processed_dir=tmp_path / "processed", raw_dir=tmp_path / "raw")
    assert_every_number_is_a_fact(text, doc)
    missing = {m["file"]: m["command"] for m in doc["missing"]}
    for name in ("hosting_summary.json", "operation/index.json", "foresight.json", "intraday.json",
                 "redispatch.json", "mv_band.json"):
        assert name in missing and missing[name] in text
    assert "*Saknas:*" in text and "kapacitet fetch" in text
    # Ei is cited where config/ei_conditions.toml cites it, and only as evidence
    assert doc["facts"]["ei.method_approved.source"]["text"] in text
    assert "aldrig en juridisk" in text


def _results(folder):
    """A stand-in of the results the dossier reads, with numbers no real run gives."""
    res = folder / "processed" / "results"
    (res / "operation").mkdir(parents=True)
    agg = [{"z_doe_pct": z, "z_doe_customer_max_pct": 6.4 + z,
            "z_doe_installation": {"customer_max_pct": 2.9, "customer_year_max_pct": 4.1 + z}} for z in (2.71, 2.93)]
    put = {
        "meta.json": {"computed_at": "2031-02-03T04:05:06+00:00", "site": "Provområdet", "reference_year": 2030},
        "hosting_summary.json": {
            "z_star_pct": 3.0, "years": [2027, 2028, 2029, 2030], "n_orders": 17, "customers": 133,
            "static_definition": {"months": [6, 7], "hours": [11, 15]}, "budget_basis": {"doe": "aggregate"},
            "measures": {k: {"kw_ac": {"median": v}} for k, v in
                         {"static": 321.4, "timeseries": 543.6, "doe": 765.4, "doe_installation": 701.2}.items()},
            "per_order": agg},
        "operation/index.json": {
            "year": 2030, "necessary_hours": 151,
            "combos": {b: {p: {"verified": True, "full_year_violation_hours": 0, "hours_limited": h,
                               "curtailed_kwh": 12345.6, "activations_unnecessary_hours": h - 151}
                           for p in ("maxmin", "prorata", "propfair", "maxtotal")}
                       for b, h in (("foresight", 151), ("dayahead", 1301))}},
        "foresight.json": {"window_days": 14, "median_kw": {"climatological": 598.7},
                           "installation": {"median_kw": {"climatological": 561.2}},
                           "exceedance": {"year": 2030, "pv_aggregate_hours_over": 2,
                                          "pv_worst_aggregate_ratio_unmargined": 1.0312,
                                          "load_aggregate_hours_under": 1, "load_customer_hours_under": 913}},
        "intraday.json": {"median_kw": 611.1, "scorecard": {
            "violation_hours": {"value": 0, "threshold": 0, "result": "pass"},
            "agreements_within_pct": {"value": 71.4, "threshold": 100.0, "justified_threshold": 90.0, "result": "fail"},
            "gain_over_hourly_pct": {"value": 0.4, "hourly_kw": 608.7, "threshold": 15.0, "result": "fail"},
            "result": "fail"}},
        "redispatch.json": {"year": 2030, "policy": "maxmin", "curtailed_mwh": 61.23, "hours": 1301, "hours_needed": 151,
                            "installations": 39, "agreements": 41, "share_of_potential_export_pct": 18.77,
                            "compensation_sek": 0.0, "compensation_entered": False, "verified_violations": 0},
        "mv_band.json": {"order": 7, "levels": [
            {"vm_pu": v, "static_kw": 300.0 + i, "timeseries_kw": 500.0 + i, "doe_kw": 700.0 + i,
             "published": {"violation_hours": 3 * i}} for i, v in enumerate((0.98, 1.0, 1.06))]},
    }
    for name, body in put.items():
        (res / name).write_text(json.dumps(body), encoding="utf-8")
    (folder / "raw").mkdir()
    (folder / "raw" / "manifest.json").write_text(json.dumps({
        "a": {"provider": "SMHI", "dataset": "STRÅNG", "license": "CC BY 4.0", "params": {"year": 2029},
              "attribution": "SMHI"},
        "b": {"provider": "SMHI", "dataset": "STRÅNG", "license": "CC BY 4.0", "params": {"year": 2030}}}),
        encoding="utf-8")
    return folder


def test_the_demo_cites_its_results_and_nothing_else(tmp_path):
    folder = _results(tmp_path)
    text, doc = build(processed_dir=folder / "processed", raw_dir=folder / "raw")
    assert_every_number_is_a_fact(text, doc)
    assert doc["missing"] == [] and doc["kind"] == "demo"
    facts = doc["facts"]
    assert facts["capacity.doe_kw"]["value"] == 765.4 and facts["capacity.doe_kw"]["text"] == "765"
    assert facts["capacity.doe_kw"]["source"] == "results/hosting_summary.json: measures.doe.kw_ac.median"
    for shown in ("Provområdet", "2027–2030", "juni–juli", "kl. 11–15", "12 346 kWh", "1301", "1,0312",
                  "61,2 MWh", "39 av 41", "71,4 %", "0,98 p.u.", "1,00 p.u.", "CC BY 4.0", "2029, 2030"):
        assert shown in text, shown
    assert "*Saknas:*" not in text
    # the day-ahead section states the unneeded hours beside Ei's condition, and draws no conclusion
    assert facts["dayahead.unnecessary_hours"]["text"] == "1150"
    assert facts["ei.only_when_overloaded.source"]["text"] in text


def test_a_study_dossier_reads_the_studys_stations(tmp_path):
    from test_study_publish import STATIONS, put

    from kapacitet.study import init_study

    study = init_study(tmp_path / "nb", "Testnät AB", allow_git=True)
    for sid, v in STATIONS.items():
        put(study, sid, v["customers"], v["lon"], v["lat"], v["gen"], v["cons"])
    text, doc = build(study.root)
    assert_every_number_is_a_fact(text, doc)
    assert doc["kind"] == "study" and "Testnät AB" in text
    f = doc["facts"]
    assert f["study.stations"]["value"] == 4 and f["study.stations_computed"]["value"] == 4
    assert f["study.generation_ok"]["value"] == 4 and f["study.consumption_ok"]["value"] == 3
    assert f["study.binding_high"]["value"] == 4 and f["study.binding_low"]["value"] == 0
    assert "Villkorad kapacitet räknas inte" in text
    assert "Ingår inte i en studie" in text


def test_the_command_writes_both_files(tmp_path):
    from kapacitet.pipeline import main

    main(["method-dossier", "--out", str(tmp_path)])
    text = (tmp_path / dossier.TEXT_NAME).read_text(encoding="utf-8")
    doc = json.loads((tmp_path / dossier.JSON_NAME).read_text(encoding="utf-8"))
    assert doc["schema"] == dossier.SCHEMA
    assert_every_number_is_a_fact(text, doc)


@needs_results
def test_on_the_published_results_every_number_is_a_fact(results):
    text, doc = build()
    assert_every_number_is_a_fact(text, doc)
    hs = results("hosting_summary")
    assert doc["facts"]["capacity.doe_kw"]["text"] == sv_number(hs["measures"]["doe"]["kw_ac"]["median"])
    assert doc["facts"]["capacity.doe_kw"]["text"] in text
