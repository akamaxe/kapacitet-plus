"""Module 1's publication (study/review.py, publish.py, diff.py): review, corrections, sign-off and
the published files, on a study of stand-in stations whose capacity.json is written by the test.
No pipeline output is needed, so CI runs all of it."""

from __future__ import annotations

import csv
import io
import json

import pytest

from kapacitet import seals
from kapacitet.study import StudyError, init_study, open_study
from kapacitet.study import collect, csvio
from kapacitet.study.config import METHOD_VERSION
from kapacitet.study.diff import diff
from kapacitet.study.publish import DRAFT, SIGNED, publish, sign
from kapacitet.study.review import review

Q1, Q2 = "2026Q3", "2026Q4"
# three stations in Bromma, a few hundred metres apart, and one far away in Uppsala
STATIONS = {
    "S1": dict(customers=40, lon=17.955, lat=59.335, gen=120.0, cons=80.0),
    "S2": dict(customers=3, lon=17.958, lat=59.336, gen=30.0, cons=25.0),
    "S3": dict(customers=2, lon=17.960, lat=59.337, gen=50.0, cons=None),     # a consumption finding
    "S4": dict(customers=60, lon=17.640, lat=59.860, gen=200.0, cons=150.0),
}


def capacity(sid: str, gen: float | None, cons: float | None, doe: dict | None = None) -> dict:
    g = ({"status": "ok", "kw": gen, "binding_mv_pu": 1.06, "binding": {"limit": "voltage"}, "order": 3,
          "doe": doe or {"status": "not_approved", "detail": "[method] approval_reference is not set"}}
         if gen is not None else {"status": "baseline_violates", "kw": None, "baseline_violation_hours": {"2025": 4}})
    c = ({"status": "ok", "kw": cons, "consumption_device": "ev", "binding": {"limit": "transformer"}}
         if cons is not None else {"status": "baseline_violates", "kw": None, "consumption_device": "ev",
                                   "baseline_violation_hours": {"2025": 7}})
    return {"schema": "kapacitet.capacity/1", "station": sid, "computed_at": "2026-11-01T00:00:00+00:00",
            "method_version": METHOD_VERSION, "fingerprint": "f", "mv_band_pu": [0.98, 1.06],
            "years": [2024, 2025], "orders": 5, "percentile": 50.0, "generation": g, "consumption": c,
            "worst_point": {}, "verification": {"full_year_violations": 0, "nonconverged_hours": 0, "hours_checked": 1}}


def put(study, sid: str, customers: int, lon, lat, gen, cons, doe=None, network="n1") -> None:
    folder = study.station_dir(sid)
    (folder / "processed" / "results").mkdir(parents=True, exist_ok=True)
    (folder / "processed" / "grid").mkdir(parents=True, exist_ok=True)
    rec = study.new_record(sid, f"Station {sid}")
    rec.update(status="ready", summary={"customers": customers}, inputs={"network": network, "mv": None})
    study.save(rec)
    (folder / "processed" / "grid" / "substation.json").write_text(json.dumps({"lon": lon, "lat": lat}), encoding="utf-8")
    (folder / "processed" / "results" / "capacity.json").write_text(json.dumps(capacity(sid, gen, cons, doe)),
                                                                     encoding="utf-8")


@pytest.fixture
def study(tmp_path):
    s = init_study(tmp_path / "nb", "Testnät AB", allow_git=True)
    for sid, v in STATIONS.items():
        put(s, sid, v["customers"], v["lon"], v["lat"], v["gen"], v["cons"])
    collect.collect(s, Q2)
    return s


def rows_of(path) -> list[dict]:
    return csvio.read(path)


# ---------------------------------------------------------------- review and corrections

def test_review_sorts_by_margin_and_puts_the_stations_without_a_figure_first(study):
    out = review(study, Q2)
    order = [r["station_id"] for r in out["rows"]]
    assert order == ["S3", "S2", "S1", "S4"]       # S3 has no consumption figure, then 25, 80, 150 kW
    assert out["rows"][0]["margin_kw"] is None and "consumption_baseline_violation" in out["rows"][0]["flags"]
    sv_file, machine = out["paths"]
    raw = sv_file.read_bytes()
    assert raw.startswith("﻿".encode("utf-8")) and b";" in raw.splitlines()[0]
    assert not machine.read_bytes().startswith(b"\xef\xbb\xbf")
    # the same rows in both dialects
    assert [r["station_id"] for r in rows_of(sv_file)] == [r["station_id"] for r in rows_of(machine)] == order
    m = {r["station_id"]: r for r in csv.DictReader(io.StringIO(machine.read_text(encoding="utf-8")))}
    assert m["S2"]["published_generation_kw"] == "30"
    s = {r["station_id"]: r for r in csv.DictReader(io.StringIO(sv_file.read_text(encoding="utf-8-sig")), delimiter=";")}
    assert s["S1"]["binding_mv_pu"] == "1,06" and m["S1"]["binding_mv_pu"] == "1.06"


def test_a_correction_is_an_overlay_that_can_only_lower_a_figure(study):
    (study.runs / Q2 / "corrections.csv").write_text(
        "station_id;field;value;reason;by\n"
        "S1;generation_kw;100,5;known weak joint in cabinet K3;reviewer\n"
        "S1;consumption_kw;90;more than computed;reviewer\n"          # above 80: refused
        "S4;published_generation_kw;10;not a correctable field;reviewer\n"
        "S4;note;feeder works in 2027;planned works;reviewer\n"
        "S9;hidden;true;no such station;reviewer\n"
        "S2;generation_kw;10;;reviewer\n",                              # no reason: refused
        encoding="utf-8")
    out = review(study, Q2)
    by = {r["station_id"]: r for r in out["rows"]}
    assert by["S1"]["published_generation_kw"] == 120.0 and by["S1"]["corrected_generation_kw"] == 100.5
    assert by["S1"]["corrected_consumption_kw"] is None and by["S2"]["corrected_generation_kw"] is None
    assert by["S4"]["correction_note"] == "feeder works in 2027"
    assert len(out["findings"]) == 4
    assert any("can only lower" in f for f in out["findings"]) and any("reason" in f for f in out["findings"])
    # the computed value is still in the parquet and in the publication beside the correction
    pub = publish(study, Q2)
    caps = {r["cell_id"]: r for r in rows_of(pub["folder"] / "capacity.machine.csv")}
    assert caps["S1"]["generation_kw"] == "120" and caps["S1"]["corrected_generation_kw"] == "100.5"


# ---------------------------------------------------------------- publish, sign

def test_an_unsigned_publication_is_a_draft_and_writes_every_file(study):
    review(study, Q2)
    out = publish(study, Q2)
    assert out["status"] == DRAFT
    folder = out["folder"]
    names = {p.name for p in folder.iterdir()}
    assert names == {"capacity.csv", "capacity.machine.csv", "capacity.geojson", "grunder_for_berakning.md",
                     "validation_report.sv.md", "manifest.json"}
    assert "disclosure_report.csv" not in names and (study.runs / Q2 / "disclosure_report.csv").is_file()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == DRAFT and manifest["signoff"] is None
    for name, f in manifest["files"].items():
        from kapacitet.study import file_digest
        assert file_digest(folder / name) == f["sha256"]
    assert DRAFT in (folder / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert DRAFT in (folder / "validation_report.sv.md").read_text(encoding="utf-8")
    assert all(r["status"] == DRAFT for r in rows_of(folder / "capacity.csv"))
    gj = json.loads((folder / "capacity.geojson").read_text(encoding="utf-8"))
    assert gj["type"] == "FeatureCollection" and gj["status"] == DRAFT
    # without an approval there is no conditional column at all
    assert "conditional_generation_kw" not in rows_of(folder / "capacity.machine.csv")[0]
    text = (folder / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert "{{" not in text and "0,98" in text and "1,06" in text and "laddbox" in text


def test_signing_seals_the_outputs_in_the_studys_chain_and_a_change_makes_it_a_draft(study):
    review(study, Q2)
    with pytest.raises(StudyError) as err:
        sign(study, Q2, "only a name")
    assert err.value.code == "bad_signer"
    doc = sign(study, Q2, "Test Person, network manager")
    assert doc["seal"]["id"] == f"{Q2}.1" and doc["seal"]["kind"] == "publication"
    assert (study.evidence / "seals.jsonl").is_file()
    assert f"runs/{Q2}/stations.parquet" in doc["outputs"]
    assert "stations/S1/processed/results/capacity.json" in doc["outputs"]
    with seals.chain_at(study.evidence):
        assert seals.verify_chain()["ok"] and seals.verify("publication", f"{Q2}.1")["ok"]
    out = publish(study, Q2)
    assert out["status"] == SIGNED
    manifest = json.loads((out["folder"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["signoff"]["valid"] and manifest["signoff"]["by"] == "Test Person, network manager"
    assert DRAFT not in (out["folder"] / "grunder_for_berakning.md").read_text(encoding="utf-8")
    # the signoff's seal is signed, and the manifest carries the public key that checks it, never the private
    key = manifest["seal_key"]
    assert key["key_id"] == doc["seal"]["key_id"] == manifest["signoff"]["seal"]["key_id"]
    assert key["public_key"] == (study.evidence / seals.PUBLISHED).read_text(encoding="ascii")
    assert set(key["how_to_verify"]) == {"en", "sv"}
    with seals.chain_at(study.evidence):
        assert seals.verify_chain({key["key_id"]: seals.load_public(key["public_key"])})["signed"] >= 1
    assert not any(b"PRIVATE KEY" in f.read_bytes() for f in out["folder"].rglob("*") if f.is_file())

    # a station computed again after the signature: the publication is a draft until signed anew
    put(study, "S4", 60, 17.64, 59.86, 190.0, 150.0)
    collect.collect(study, Q2)
    out = publish(study, Q2)
    assert out["status"] == DRAFT and "stations/S4/processed/results/capacity.json" in out["changed"]
    again = sign(study, Q2, "Test Person, network manager")
    assert again["seal"]["id"] == f"{Q2}.2" and publish(study, Q2)["status"] == SIGNED


def test_a_study_pinned_to_another_method_is_not_published(study):
    study.toml.write_text(study.toml.read_text(encoding="utf-8").replace(
        f'method_version = "{METHOD_VERSION}"', 'method_version = "0.9.0"'), encoding="utf-8")
    other = open_study(study.root, allow_git=True)
    with pytest.raises(StudyError) as err:
        publish(other, Q2)
    assert err.value.code == "method_version"
    assert not (study.runs / Q2 / "publication").exists()


def test_a_station_computed_by_another_method_is_refused(study):
    path = study.station_dir("S1") / "processed" / "results" / "capacity.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**doc, "method_version": "0.9.0"}), encoding="utf-8")
    collect.collect(study, Q2)
    with pytest.raises(StudyError) as err:
        publish(study, Q2)
    assert err.value.code == "stale_method" and "S1" in str(err.value)


def test_an_uncollected_quarter_is_refused(study):
    with pytest.raises(StudyError) as err:
        publish(study, "2025Q1")
    assert err.value.code == "not_collected"


def test_the_conditional_column_appears_only_with_an_approval_and_a_verified_envelope(tmp_path):
    s = init_study(tmp_path / "nb", "Testnät AB", allow_git=True)
    s.toml.write_text(s.toml.read_text(encoding="utf-8").replace(
        "# approval_reference = \"...\"", 'approval_reference = "Board decision 2026-10"'), encoding="utf-8")
    s = open_study(s.root, allow_git=True)
    assert s.config.method.approval_reference
    put(s, "S1", 40, 17.955, 59.335, 120.0, 80.0, doe={"status": "ok", "kw": 210.0, "approval_reference": "Board decision 2026-10"})
    put(s, "S5", 30, 17.70, 59.40, 90.0, 60.0, doe={"status": "ok", "kw": 150.0, "approval_reference": "old reference"})
    put(s, "S6", 30, 17.80, 59.50, 90.0, 60.0,
        doe={"status": "ok", "kw": 150.0, "approval_reference": "Board decision 2026-10", "full_year_violations": 3})
    put(s, "S4", 60, 17.64, 59.86, 200.0, 150.0, doe={"status": "not_verified", "full_year_violations": 2})
    collect.collect(s, Q2)
    pub = publish(s, Q2)
    caps = {r["cell_id"]: r for r in rows_of(pub["folder"] / "capacity.machine.csv")}
    assert caps["S1"]["conditional_generation_kw"] == "210" and caps["S4"]["conditional_generation_kw"] == ""
    assert caps["S5"]["conditional_generation_kw"] == "" and caps["S6"]["conditional_generation_kw"] == ""
    text = (pub["folder"] / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert "Board decision 2026-10" in text


# ---------------------------------------------------------------- diff

def test_diff_tags_each_change_with_the_inputs_that_changed(study):
    collect.collect(study, Q1)          # the same figures in the quarter before
    put(study, "S1", 40, 17.955, 59.335, 110.0, 80.0, network="n2")          # the network changed
    put(study, "S4", 60, 17.64, 59.86, 190.0, 150.0)                        # no input changed
    put(study, "S5", 12, 17.70, 59.40, 70.0, 60.0)                          # a new station
    collect.collect(study, Q2)
    out = diff(study, Q1, Q2)
    by = {(r["station_id"], r["field"]): r for r in out["rows"]}
    s1 = by[("S1", "published_generation_kw")]
    assert s1["causes"] == "network" and s1["delta_kw"] == pytest.approx(-10.0)
    assert "från 120 kW till 110 kW" in s1["sentence_sv"] and "nätdata" in s1["sentence_sv"]
    assert by[("S4", "published_generation_kw")]["causes"] == "unexplained"
    assert by[("S5", "published_generation_kw")]["causes"] == "added"
    assert ("S2", "published_generation_kw") not in by
    assert (study.runs / Q2 / "changes.csv").is_file() and (study.runs / Q2 / "changes.machine.csv").is_file()
    with pytest.raises(StudyError):
        diff(study, Q2, Q2)


def test_the_command_line_runs_the_four_steps(study, capsys):
    from kapacitet.pipeline import main

    root = str(study.root)
    main(["study", "review", root, "--quarter", Q2, "--allow-git"])
    main(["study", "sign", root, "--quarter", Q2, "--by", "Test Person, reviewer", "--allow-git"])
    main(["study", "publish", root, "--quarter", Q2, "--allow-git"])
    collect.collect(study, Q1)
    main(["study", "diff", root, "--from", Q1, "--to", Q2, "--allow-git"])
    text = capsys.readouterr().out
    assert "least margin first" in text and f"{Q2}.1" in text and SIGNED in text and "changes in" in text


def test_the_published_manifest_names_no_station(tmp_path):
    s = init_study(tmp_path / "nb", "Testnät AB", allow_git=True)
    for sid, n in (("S1", 40), ("S2", 40), ("SECRET", 2)):
        put(s, sid, n, 17.9, 59.3, 100.0, 80.0)
    s.toml.write_text(s.toml.read_text(encoding="utf-8").replace("hidden = []", 'hidden = ["S2"]'), encoding="utf-8")
    s = open_study(s.root, allow_git=True)
    collect.collect(s, Q2)
    pub = publish(s, Q2)
    for f in pub["folder"].iterdir():
        if f.name == "manifest.json":
            text = f.read_text(encoding="utf-8")
            assert "SECRET" not in text and "S2" not in text and "stations/" not in text


def test_a_measured_mv_file_with_a_bom_and_a_decimal_comma_is_read_and_a_naive_time_is_refused(tmp_path):
    from kapacitet.engine import validation_measured as vm

    ok = tmp_path / "a.csv"
    ok.write_bytes(("\ufeffstart;vm_pu\n2025-03-30T03:00:00+02:00;1,02\n").encode("utf-8"))
    assert float(vm.read_mv(ok)["vm_pu"].iloc[0]) == pytest.approx(1.02)
    bad = tmp_path / "b.csv"
    bad.write_text("start;vm_pu\n2025-03-30T03:00:00;1,02\n", encoding="utf-8")
    with pytest.raises(ValueError, match="UTC offset"):
        vm.read_mv(bad)
