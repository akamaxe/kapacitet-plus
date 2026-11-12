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
                     "validation_report.sv.md", "manifest.json", "embed"}
    assert "disclosure_report.csv" not in names and (study.runs / Q2 / "disclosure_report.csv").is_file()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == DRAFT and manifest["signoff"] is None
    for name, f in manifest["files"].items():
        from kapacitet.study import file_digest
        assert file_digest(folder / name) == f["sha256"]
    from kapacitet.language import NOT_REVIEWED_SV

    for name in ("grunder_for_berakning.md", "validation_report.sv.md"):
        text = (folder / name).read_text(encoding="utf-8")
        assert DRAFT in text and NOT_REVIEWED_SV in text, name     # a draft says nobody native has read it
    assert "planeringsband, inte uppmätt" in (folder / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert "planning band" not in (folder / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert all(r["status"] == DRAFT for r in rows_of(folder / "capacity.csv"))
    gj = json.loads((folder / "capacity.geojson").read_text(encoding="utf-8"))
    assert gj["type"] == "FeatureCollection" and gj["status"] == DRAFT
    # without an approval there is no conditional column at all, and no three-phase one without a check
    assert "conditional_generation_kw" not in rows_of(folder / "capacity.machine.csv")[0]
    assert "three_phase_generation_kw" not in rows_of(folder / "capacity.machine.csv")[0]
    assert manifest["three_phase_columns"] is False
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
    from kapacitet.language import NOT_REVIEWED_SV

    # signing is where the DSO takes the text as its own: the note about its language goes with the draft
    assert NOT_REVIEWED_SV not in (out["folder"] / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert NOT_REVIEWED_SV not in (out["folder"] / "validation_report.sv.md").read_text(encoding="utf-8")
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


def _three_phase(study, sid: str, status: str, gen: float | None = None, cons: float | None = None) -> None:
    """A three-phase block in station `sid`'s capacity.json, as engine/station.three_phase_check writes it."""
    path = study.station_dir(sid) / "processed" / "results" / "capacity.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    side = lambda kw, balanced: ({"status": "ok" if kw == balanced else "limited", "kw": kw, "balanced_kw": balanced}
                                 if status != "phases_unknown" else {"status": "not_checked", "kw": None})
    doc["three_phase"] = {"status": status, "known_share": 0.5 if status == "phases_unknown" else 1.0,
                          "min_known_share": 0.8, "generation": side(gen, doc["generation"]["kw"]),
                          "consumption": side(cons, doc["consumption"]["kw"])}
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_the_three_phase_columns_appear_only_with_a_checked_figure(tmp_path):
    s = init_study(tmp_path / "nb", "Testnät AB", allow_git=True)
    put(s, "S1", 40, 17.955, 59.335, 120.0, 80.0)
    put(s, "S4", 60, 17.64, 59.86, 200.0, 150.0)
    _three_phase(s, "S4", "phases_unknown")
    collect.collect(s, Q2)
    pub = publish(s, Q2)
    assert "three_phase_generation_kw" not in rows_of(pub["folder"] / "capacity.machine.csv")[0]
    assert "Ingen trefaskontroll publiceras" in (pub["folder"] / "grunder_for_berakning.md").read_text(encoding="utf-8")

    _three_phase(s, "S1", "limited", gen=95.5, cons=80.0)
    collect.collect(s, Q2)
    pub = publish(s, Q2)
    caps = {r["cell_id"]: r for r in rows_of(pub["folder"] / "capacity.machine.csv")}
    assert caps["S1"]["three_phase_generation_kw"] == "95.5" and caps["S1"]["three_phase_consumption_kw"] == "80"
    assert caps["S1"]["generation_kw"] == "120"                # the balanced figure stays in its own column
    assert caps["S4"]["three_phase_generation_kw"] == "" and caps["S4"]["three_phase_consumption_kw"] == ""
    swedish = {r["cell_id"]: r for r in rows_of(pub["folder"] / "capacity.csv")}
    assert swedish["S1"]["three_phase_generation_kw"] == "95,5"
    text = (pub["folder"] / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert "## Trefaskontroll" in text and "three_phase_generation_kw" in text and "symmetrisk modell" in text
    assert "1 av 2 publicerade stationer har ett trefasvärde, varav 1 är lägre" in text and "minst 80 procent" in text
    manifest = json.loads((pub["folder"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["three_phase_columns"] is True


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


# ---------------------------------------------------------------- shared headroom, small cells, validation

def _with(study, old: str, new: str):
    """The study with one line of its study.toml changed."""
    text = study.toml.read_text(encoding="utf-8")
    assert old in text, old
    study.toml.write_text(text.replace(old, new, 1), encoding="utf-8")
    return open_study(study.root, allow_git=True)


def _table(study) -> dict:
    return {r["cell_id"]: r for r in rows_of(publish(study, Q2)["folder"] / "capacity.machine.csv")}


def test_a_headroom_two_stations_share_is_published_with_how_many_share_it(study):
    """Two stations under one feeder each show its 100 kW: the count says it is the same 100."""
    study.inputs.mkdir(parents=True, exist_ok=True)
    (study.inputs / "stations.csv").write_text("station_id,feeder_id\nS1,FS1\nS4,FS1\n", encoding="utf-8")
    (study.inputs / "upstream.csv").write_text(
        "level,id,parent_id,gen_headroom_kw,load_headroom_kw,source,as_of\n"
        "feeder,FS1,,100,,planning 2026,2026-11-01\n", encoding="utf-8")
    collect.collect(study, Q2)
    by = {r["station_id"]: r for r in review(study, Q2)["rows"]}
    assert by["S1"]["published_generation_shared_by"] == 2 and by["S1"]["published_consumption_shared_by"] is None
    out = publish(study, Q2)
    rows = {r["cell_id"]: r for r in rows_of(out["folder"] / "capacity.machine.csv")}
    for sid in ("S1", "S4"):
        assert float(rows[sid]["generation_kw"]) == 100.0 and rows[sid]["generation_limited_by"] == "feeder:FS1"
        assert int(rows[sid]["generation_shared_by_stations"]) == 2
        assert not rows[sid]["consumption_shared_by_stations"]          # its own figure: nobody shares it
    text = (out["folder"] / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert "generation_shared_by_stations" in text and "får inte summeras" in text
    gj = json.loads((out["folder"] / "capacity.geojson").read_text(encoding="utf-8"))
    shared = {f["properties"]["cell_id"]: f["properties"]["generation_shared_by_stations"] for f in gj["features"]}
    assert shared["S1"] == 2 and shared["S4"] == 2


def test_a_small_cells_figures_are_rounded_down_and_the_dso_sets_the_step(tmp_path):
    from kapacitet.study.publish import round_down

    assert round_down(127.4, 10.0) == 120.0 and round_down(120.0, 10.0) == 120.0 and round_down(9.9, 10.0) == 0.0
    assert round_down(-15.0, 10.0) == -20.0                      # down, never towards a higher figure
    assert round_down(None, 10.0) is None and round_down(127.4, None) == 127.4
    s = init_study(tmp_path / "nb", "Testnät AB", allow_git=True)
    put(s, "A", 12, 17.955, 59.335, 127.4, 83.9)                 # under 20 customers: a small cell
    put(s, "B", 25, 17.640, 59.860, 127.4, 83.9)
    collect.collect(s, Q2)
    out = publish(s, Q2)
    rows = {r["cell_id"]: r for r in rows_of(out["folder"] / "capacity.machine.csv")}
    assert (float(rows["A"]["generation_kw"]), float(rows["A"]["consumption_kw"])) == (120.0, 80.0)
    assert float(rows["A"]["rounded_to_kw"]) == 10.0
    assert (float(rows["B"]["generation_kw"]), float(rows["B"]["consumption_kw"])) == (127.4, 83.9)
    assert not rows["B"]["rounded_to_kw"]
    text = (out["folder"] / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert "färre än 20 kunder avrundas varje värde nedåt till närmaste 10 kW" in text and "rounded_to_kw" in text
    manifest = json.loads((out["folder"] / "manifest.json").read_text(encoding="utf-8"))
    assert (manifest["disclosure"]["round_kw"], manifest["disclosure"]["round_below_customers"]) == (10.0, 20)
    # the DSO's own step, and none at all
    rows = _table(_with(s, "round_kw = 10.0", "round_kw = 25.0"))
    assert (float(rows["A"]["generation_kw"]), float(rows["A"]["consumption_kw"])) == (125.0, 75.0)
    off = _with(s, "round_kw = 25.0", "round_kw = 0.0")
    rows = _table(off)
    assert float(rows["A"]["generation_kw"]) == 127.4 and not rows["A"]["rounded_to_kw"]
    assert "avrundas" not in (off.runs / Q2 / "publication" / "grunder_for_berakning.md").read_text(encoding="utf-8")


def _compared(study, sid: str, voltage_p95: float | None) -> None:
    """A validation_measured.json: a replay at the measured MV voltage, or one without readings."""
    block = lambda p95: {"n": 500, "bias": 0.001, "mae": p95 / 2, "rmse": p95 / 2, "p95": p95, "max": p95 * 1.5}  # noqa: E731
    doc = ({"measured": {"status": "no meter readings"}} if voltage_p95 is None else
           {"measured": {"hours_complete": 500, "mv": "measured", "metered_customers": 30,
                         "bases": {"measured": {"hours": 500, "nonconverged_hours": 0, "voltage_pu": block(voltage_p95),
                                                "transformer_pct": block(2.0)}}}})
    path = study.station_dir(sid) / "processed" / "results" / "validation_measured.json"
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_a_station_outside_the_dsos_tolerance_is_not_published_and_only_one_inside_is_validated(study):
    study = _with(study, "[validation]\n", '[validation]\nvoltage_p95_pu = 0.005\nsource = "valideringsbeslut 2026-11"\n')
    _compared(study, "S1", 0.012)                # outside the tolerance
    _compared(study, "S4", 0.003)                # inside it
    collect.collect(study, Q2)
    (study.runs / Q2 / "corrections.csv").write_text(
        "station_id;field;value;reason;by\nS1;generation_kw;50;known weak joint;reviewer\n", encoding="utf-8")
    by = {r["station_id"]: r for r in review(study, Q2)["rows"]}
    assert by["S1"]["validation_status"] == "outside" and "validation_outside" in by["S1"]["flags"]
    assert by["S4"]["validation_status"] == "within" and by["S2"]["validation_status"] == "not_validated"
    out = publish(study, Q2)
    rows = {r["cell_id"]: r for r in rows_of(out["folder"] / "capacity.machine.csv")}
    assert not rows["S1"]["generation_kw"] and not rows["S1"]["consumption_kw"]
    assert not rows["S1"]["corrected_generation_kw"]             # a correction never brings it back
    assert "validation: outside the DSO's tolerances" in rows["S1"]["finding"]
    assert float(rows["S4"]["generation_kw"]) == 200.0 and not rows["S4"]["finding"]
    report = (out["folder"] / "validation_report.sv.md").read_text(encoding="utf-8")
    assert "Bedömning: utanför nätföretagets toleranser, publiceras inte." in report
    assert "Bedömning: validerad: inom nätföretagets toleranser." in report
    assert "0,012 (högst 0,005): ej uppfyllt." in report and "0,003 (högst 0,005): uppfyllt." in report
    assert "Validerade, inom nätföretagets toleranser (valideringsbeslut 2026-11): 1." in report
    assert "Utanför toleranserna, publiceras inte: 1." in report
    manifest = json.loads((out["folder"] / "manifest.json").read_text(encoding="utf-8"))
    counts = manifest["validation"]["stations"]
    assert (counts["within"], counts["outside"], counts["not_validated"]) == (1, 1, 2)
    assert manifest["validation"]["criteria"]["voltage_p95_pu"] == 0.005
    assert "S1" not in json.dumps(manifest["validation"])        # counts, never which station
    text = (out["folder"] / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert "1 av 4 publicerade stationer är validerade mot mätningar inom dem" in text
    # the tolerance is the DSO's: widened, the same comparison is inside it and the figure is back
    wider = _with(study, "voltage_p95_pu = 0.005", "voltage_p95_pu = 0.02")
    assert float(_table(wider)["S1"]["generation_kw"]) == 120.0


def test_a_comparison_without_criteria_or_without_readings_is_never_called_validated(study):
    """The report used to count every station with the file as validated, readings or not."""
    _compared(study, "S1", None)                 # `study validate` ran, and the station has no readings
    _compared(study, "S4", 0.003)
    collect.collect(study, Q2)
    out = publish(study, Q2)
    report = (out["folder"] / "validation_report.sv.md").read_text(encoding="utf-8")
    assert "Bedömning: mätvärden saknas." in report and "Mätvärden: inga." in report
    assert "Bedömning: jämförd med mätningar, inget godkännandekriterium angivet." in report
    assert "Jämförda med mätningar: 1." in report and "Validerade: 0." in report
    assert "varav validerade" not in report and "no meter readings" not in report
    rows = {r["cell_id"]: r for r in rows_of(out["folder"] / "capacity.machine.csv")}
    assert float(rows["S1"]["generation_kw"]) == 120.0           # nothing is withheld without a criterion
    text = (out["folder"] / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert "ingen station redovisas som validerad" in text and "1 av 4 publicerade stationer har jämförts" in text
