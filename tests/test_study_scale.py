"""tools/study_scale.py: the synthetic study it makes and the table it prints.

The build it times is the study's own and has its own tests. What is the script's is which
networks it uses, where it puts them, and the reading of each station's record: checked here
without data, on a study of two stations and on made-up records.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "study_scale.py"
DOC = ROOT / "docs" / "study-scale.md"


def _load():
    spec = importlib.util.spec_from_file_location("study_scale", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["study_scale"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_networks_are_distinct_and_name_valid_stations():
    from kapacitet.study import valid_id

    m = _load()
    kinds = m.networks()
    names = [k[0] for k in kinds]
    assert len(kinds) == 13 == len(set(names))
    assert all(valid_id(f"s{k + 1:03d}-{name}") for k, name in enumerate(names))
    assert "thirteen" in DOC.read_text(encoding="utf-8")            # the document says how many
    # no two of sixty stations in the same place, the first ring 1.5 km out
    places = {m.place(k, 59.33, 17.94) for k in range(60)}
    assert len(places) == 60
    lat, lon = m.place(0, 59.33, 17.94)
    assert abs(((lat - 59.33) * 111_320.0) ** 2 + ((lon - 17.94) * 111_320.0 * 0.5101) ** 2 - 1500.0 ** 2) < 1500.0 ** 2 * 0.01


def test_a_study_of_two_stations_is_made_and_checked(tmp_path):
    from kapacitet.study import open_study

    m = _load()
    made = m.create(tmp_path / "scale", 2)
    assert made == ["s001-kerber-land-ol", "s002-kerber-land-kabel"]
    study = open_study(tmp_path / "scale")
    for sid in made:
        origin = json.loads((study.station_dir(sid) / "import" / "origin.json").read_text(encoding="utf-8"))
        assert origin["synthetic"] is True and "pandapower.networks." in origin["generator"]
        assert (study.station_dir(sid) / "import" / "customers.csv").is_file()


def test_the_table_reads_each_stations_own_record(tmp_path):
    m = _load()
    study = m.create(tmp_path / "scale", 1)
    folder = tmp_path / "scale" / "stations" / study[0]
    record = {"id": study[0], "status": "ready", "quick": True, "steps": [
        {"name": "import", "seconds": 2.8, "summary": {"customers": 26, "buses": 54}},
        {"name": "profiles", "seconds": 2.1}, {"name": "compute", "seconds": 123.1}]}
    (folder / "station.json").write_text(json.dumps(record), encoding="utf-8")
    doc = m.measure(tmp_path / "scale")
    row = doc["rows"][0]
    assert (row["customers"], row["buses"], row["compute_s"], row["profiles_s"], row["build_s"]) == (26, 54, 123.1, 2.1, 128.0)
    assert doc["stations"] == doc["ready"] == 1 and doc["build_s_sum"] == 128.0 and doc["wall"] is None
    assert row["disk_mb"] >= 0 and doc["study_disk_mb"] >= doc["disk_mb"]
    text = m.table(doc)
    assert f"| {study[0]} | 26 | 54 | ready | 2.1 | 123.1 | 128.0 |" in text and "1 of 1 stations ready, 26 customers" in text
    # a station that never built has no times, and says so with a dash
    (folder / "station.json").write_text(json.dumps({"id": study[0], "status": "failed", "steps": []}), encoding="utf-8")
    failed = m.measure(tmp_path / "scale")
    assert failed["ready"] == 0 and "| – | – | failed | – | – | – |" in m.table(failed)
    # the wall clock of `run`, when there is one
    (tmp_path / "scale" / "scale.json").write_text(json.dumps({
        "run": "2026Q4", "mode": "quick", "jobs": 2, "at": "x",
        "machine": {"platform": "P", "processor": "p", "cores": 12, "python": "3.12"},
        "steps": [{"command": "kapacitet study build d --quick --jobs 2", "seconds": 300.5, "exit": 0}]}), encoding="utf-8")
    assert "- `kapacitet study build d --quick --jobs 2`: 300.5 s, exit 0" in m.table(m.measure(tmp_path / "scale"))


def test_the_document_carries_no_timing_until_the_measurement_is_run():
    """The page says how to measure, and has no seconds or megabytes of its own to go stale."""
    import re

    text = DOC.read_text(encoding="utf-8")
    assert "has not been measured beyond three stations" in text and "has not been made" in text
    assert not re.search(r"\b\d+(?:[.,]\d+)?\s*(?:s|seconds|min|minutes|h|hours|MB)\b", text.split("## The measurement")[0])
    for command in ("create", "run", "measure"):
        assert f"tools/study_scale.py {command} " in text
