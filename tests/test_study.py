"""A study of many stations (study/): where it may live, the split per station, and the build of
each station in a process of its own that writes only its own folder.

Most of this runs without the pipeline's output: the study is the test's own folder, and the
station processes are stand-ins. The end-to-end build of the demo needs the reference network's
weather years and household pool, and is skipped without them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kapacitet import config, paths, procs
from kapacitet.study import StudyError, guard, init_study, open_study, resolve
from kapacitet.study import portfolio, station
from kapacitet.study.intake import add_contract, check, contract_dir, split


def _tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------- where a study may live

def test_a_study_inside_the_code_or_a_git_work_tree_is_refused(tmp_path):
    inside = paths.ROOT / "studies" / "nätbolaget"
    with pytest.raises(StudyError) as err:
        init_study(inside)
    assert err.value.code == "inside_repo" and not inside.exists()

    repo = tmp_path / "somebody-elses-repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(StudyError) as err:
        init_study(repo / "deep" / "study")
    assert err.value.code == "inside_git" and not (repo / "deep").exists()
    (repo / ".git").rmdir()
    (repo / ".git").write_text("gitdir: elsewhere")                   # a worktree's .git is a file
    with pytest.raises(StudyError):
        guard(repo / "study")
    assert init_study(repo / "study", allow_git=True).root == (repo / "study").resolve()


def test_the_command_line_refuses_it_too_and_writes_nothing(tmp_path):
    from kapacitet.pipeline import main

    inside = paths.ROOT / "studies" / "a-study-the-test-names"
    there = (paths.ROOT / "studies").exists()                     # a developer may keep one
    with pytest.raises(SystemExit) as code:
        main(["study", "init", str(inside)])
    assert code.value.code == 2 and not inside.exists() and (paths.ROOT / "studies").exists() == there


def test_a_bare_name_is_a_study_under_the_default_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("KAPACITET_STUDY_DIR", str(tmp_path / "studies"))
    assert resolve("nb-2026") == (tmp_path / "studies" / "nb-2026").resolve()
    assert resolve(tmp_path / "elsewhere") == (tmp_path / "elsewhere").resolve()


def test_init_writes_the_layout_once(tmp_path):
    study = init_study(tmp_path / "s", "Nätbolaget AB")
    for sub in ("inputs/network/contract", "inputs/meters", "raw", "stations", "evidence", "runs"):
        assert (study.root / sub).is_dir()
    assert open_study(study.root).config.dso.name == "Nätbolaget AB"
    with pytest.raises(StudyError) as err:
        init_study(tmp_path / "s")
    assert err.value.code == "exists"
    with pytest.raises(StudyError) as err:
        open_study(tmp_path / "nothing-here")
    assert err.value.code == "no_study"


# ---------------------------------------------------------------- the split per station

@pytest.fixture(scope="session")
def _demo_template(tmp_path_factory):
    from kapacitet.study.demo import create

    return create(tmp_path_factory.mktemp("template") / "demo").root


@pytest.fixture()
def demo(tmp_path, _demo_template):
    """The demo study, a fresh copy for every test (made once: pandapower builds its networks)."""
    import shutil

    shutil.copytree(_demo_template, tmp_path / "demo")
    return open_study(tmp_path / "demo")


def test_the_demo_is_three_synthetic_stations_ready_to_build(demo):
    assert demo.station_ids() == ["demo-dickert", "demo-dorfnetz", "demo-landnetz"]
    for sid in demo.station_ids():
        rec = demo.record(sid)
        assert rec["schema"] == "kapacitet.station/1" and rec["status"] == "imported"
        assert rec["origin"]["format"] == "pandapower" and rec["origin"]["synthetic"] is True
        assert rec["quality"]["usable"] is True
        assert set(rec["origin"]["files"]) == {"buses.csv", "lines.csv", "transformer.csv", "customers.csv"}
        assert (demo.station_dir(sid) / "import" / "customers.csv").is_file()
    assert {r: portfolio.reason(demo, r) for r in demo.station_ids()} == dict.fromkeys(demo.station_ids(), "never built")


def test_a_changed_network_makes_its_station_stale_and_only_it(demo):
    for sid in demo.station_ids():
        rec = demo.record(sid)
        rec.update(status="ready", inputs=demo.digests(sid), method_version="1.0.0", quick=True)
        demo.save(rec)
    assert set(portfolio.plan(demo, quick=True).values()) == {"unchanged"}
    lines = demo.inputs / "network" / "contract" / "demo-landnetz" / "lines.csv"
    lines.write_text(lines.read_text(encoding="utf-8").replace(",1\n", ",2\n", 1), encoding="utf-8")
    split(demo)
    assert portfolio.plan(demo, quick=True) == {"demo-dickert": "unchanged", "demo-dorfnetz": "unchanged",
                                                "demo-landnetz": "inputs changed"}
    assert demo.record("demo-landnetz")["status"] == "ready"           # its last build's status stays
    upstream = demo.inputs / "upstream.csv"                            # applied after the computation
    upstream.write_text("level;id;gen_headroom_kw\nsubstation;S1;100\n", encoding="utf-8")
    assert portfolio.plan(demo, quick=True)["demo-dickert"] == "unchanged"
    assert portfolio.plan(demo, quick=False)["demo-dickert"] == "mode changed"
    assert set(portfolio.plan(demo, force=True, quick=True).values()) == {"forced"}


def test_a_contract_folder_is_added_and_checked(tmp_path, demo):
    src = demo.station_dir("demo-landnetz") / "import"
    assert (src / "origin.json").is_file()                             # the origin travels with the files
    sid = add_contract(demo, src, "NS-0042")
    assert (demo.inputs / "network" / "contract" / "NS-0042" / "lines.csv").is_file()
    assert "NS-0042" in split(demo)
    rec = demo.record("NS-0042")
    assert rec["origin"]["format"] == "pandapower" and rec["origin"]["synthetic"] is True   # still synthetic
    assert check(demo, [sid])[sid].usable
    with pytest.raises(StudyError):
        add_contract(demo, src, "../escape")

    bare = tmp_path / "bare"                                           # a folder with no origin.json
    bare.mkdir()
    for f in ("buses.csv", "lines.csv", "transformer.csv", "customers.csv"):
        (bare / f).write_bytes((src / f).read_bytes())
    add_contract(demo, bare, "NS-0043")
    split(demo)
    origin = demo.record("NS-0043")["origin"]
    assert origin["declared"] is False and "synthetic" not in origin


def test_a_contract_folder_that_is_already_the_stations_input_is_left_as_it_is(demo):
    folder = contract_dir(demo) / "demo-dickert"
    before = {f.name: f.read_bytes() for f in folder.iterdir()}
    assert add_contract(demo, folder, "demo-dickert") == "demo-dickert"
    assert {f.name: f.read_bytes() for f in folder.iterdir()} == before
    assert not [p for p in contract_dir(demo).iterdir() if p.name.startswith(".")]   # no staging left behind


def test_a_station_id_is_a_folder_name_on_every_system(demo):
    src = demo.station_dir("demo-landnetz") / "import"
    for bad in ("CON", "nul", "Com1", "LPT9", "a.b", "-x"):
        with pytest.raises(StudyError) as err:
            add_contract(demo, src, bad)
        assert err.value.code == "bad_station"
    add_contract(demo, src, "NS-1")
    with pytest.raises(StudyError) as err:
        add_contract(demo, src, "ns-1")                                  # one folder on Windows
    assert err.value.code == "station_case" and err.value.params["other"] == "NS-1"


def test_a_check_leaves_the_record_of_a_station_being_built_alone(demo):
    lock = procs.lock_path(demo.station_dir("demo-dorfnetz"))
    lock.write_text(str(os.getpid()))                                    # this process is alive
    rec = demo.record("demo-dorfnetz") | {"status": "building", "quality": {"report": None}}
    demo.save(rec)
    reports = check(demo)
    assert reports["demo-dorfnetz"].usable                               # the report is still made...
    assert demo.record("demo-dorfnetz") == rec                           # ...and the record left to its build
    assert demo.record("demo-dickert")["quality"]["usable"] is True


def test_cgmes_files_are_not_imported_silently(demo):
    (demo.inputs / "network" / "cgmes").mkdir(parents=True)
    (demo.inputs / "network" / "cgmes" / "EQ.xml").write_text("<rdf/>", encoding="utf-8")
    with pytest.raises(StudyError) as err:
        split(demo)
    assert err.value.code == "cgmes_pending"


# ---------------------------------------------------------------- a station's build

def test_the_build_records_each_step_and_a_failure_stays_in_its_station(demo):
    ok = station.build(demo, "demo-dickert", steps={s: (lambda s=s: {"note": s}) for s in station.STEPS})
    assert ok["status"] == "ready" and [s["status"] for s in ok["steps"]] == ["done"] * len(station.STEPS)
    assert ok["inputs"] == demo.digests("demo-dickert") and ok["pid"] is None
    assert ok["kapacitet"]["version"] and not (demo.station_dir("demo-dickert") / "build.lock").exists()

    def boom():
        raise RuntimeError("profiles broke")

    before = {sid: demo.record(sid) for sid in ("demo-dickert", "demo-dorfnetz")}
    bad = station.build(demo, "demo-landnetz", steps={s: (lambda: {}) for s in station.STEPS} | {"profiles": boom})
    by = {s["name"]: s for s in bad["steps"]}
    assert bad["status"] == "failed" and by["profiles"]["status"] == "failed" and by["compute"]["status"] == "waiting"
    assert "profiles broke" in bad["error"]
    assert {sid: demo.record(sid) for sid in before} == before


def test_a_station_is_refused_outside_its_own_process_or_on_another_method(demo):
    with pytest.raises(StudyError) as err:
        station.build(demo, "demo-dickert")                        # this process's data folder is not its own
    assert err.value.code == "not_isolated"
    text = demo.toml.read_text(encoding="utf-8").replace('method_version = "1.0.0"', 'method_version = "0.9.0"')
    demo.toml.write_text(text, encoding="utf-8")
    with pytest.raises(StudyError) as err:
        station.build(open_study(demo.root), "demo-dickert", steps={})
    assert err.value.code == "method_version"
    lock = procs.lock_path(demo.station_dir("demo-dorfnetz"))
    lock.write_text(str(os.getpid()))                                # this process is alive
    with pytest.raises(StudyError) as err:
        station.build(demo, "demo-dorfnetz", steps={})
    assert err.value.code == "building"
    assert portfolio.reason(demo, "demo-dorfnetz") == "building"


def _dead_pid() -> int:
    proc = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    return int(proc.stdout.strip())


def test_a_stale_lock_is_an_interrupted_build_and_is_built_again(demo):
    folder = demo.station_dir("demo-dickert")
    procs.lock_path(folder).write_text(str(_dead_pid()))                # its process is gone
    demo.save(demo.record("demo-dickert") | {"status": "building"})
    assert not procs.building(folder) and portfolio.reason(demo, "demo-dickert") == "interrupted"
    out = portfolio.run(demo, stations=["demo-dickert"], make_command=lambda st, sid: [sys.executable, "-c", ""])
    assert out["built"]["demo-dickert"]["code"] == 0


def test_one_build_of_a_study_at_a_time_and_a_live_station_build_is_left_alone(demo):
    lock = portfolio.lock(demo)
    lock.write_text(str(os.getpid()))                                    # another `study build`, alive
    with pytest.raises(StudyError) as err:
        portfolio.run(demo, make_command=lambda st, sid: [sys.executable, "-c", ""])
    assert err.value.code == "study_building"
    lock.write_text(str(_dead_pid()))                                    # a stale one is taken over
    out = portfolio.run(demo, stations=["demo-dickert"], make_command=lambda st, sid: [sys.executable, "-c", ""])
    assert out["built"]["demo-dickert"]["code"] == 0 and not lock.exists()

    # a station another live process is building: its child refuses (exit 2) and nothing is marked
    folder = demo.station_dir("demo-dorfnetz")
    procs.lock_path(folder).write_text(str(os.getpid()))
    demo.save(demo.record("demo-dorfnetz") | {"status": "building"})
    rec = demo.record("demo-dorfnetz")
    outcome = portfolio._run_one(demo, "demo-dorfnetz", [sys.executable, "-c", "raise SystemExit(2)"], None)
    assert outcome["status"] == "building" and procs.lock_path(folder).is_file()
    assert demo.record("demo-dorfnetz") == rec


STAND_IN = r"""
import json, os, sys
from kapacitet import paths
sid = sys.argv[1]
(paths.PROCESSED / "results").mkdir(parents=True, exist_ok=True)
(paths.PROCESSED / "results" / "marker.json").write_text(json.dumps({
    "sid": sid, "data": str(paths.DATA), "home": str(paths.HOME), "raw": str(paths.RAW),
    "reference": str(paths.REFERENCE), "study": os.environ.get("KAPACITET_STUDY"),
    "inbox": os.environ.get("KAPACITET_INBOX_WORKER"), "site": os.environ.get("KAPACITET_SITE")}))
sys.exit(3 if sid == "demo-dorfnetz" else 0)
"""


def test_three_stations_run_side_by_side_each_writing_only_its_own_folder(demo, monkeypatch):
    monkeypatch.setenv("KAPACITET_SITE", "olovslund")               # nothing of the parent's leaks in
    before = _tree(demo.root)
    out = portfolio.run(demo, jobs=3, make_command=lambda st, sid: [sys.executable, "-c", STAND_IN, sid])
    assert set(out["built"]) == set(demo.station_ids()) and not out["skipped"]
    new = _tree(demo.root) - before
    for sid in demo.station_ids():
        mine = {p for p in new if p.startswith(f"stations/{sid}/")}
        assert mine == {f"stations/{sid}/processed/results/marker.json", f"stations/{sid}/build.log"}
        seen = json.loads((demo.station_dir(sid) / "processed" / "results" / "marker.json").read_text())
        assert seen["sid"] == sid and Path(seen["data"]) == demo.station_dir(sid).resolve()
        assert Path(seen["home"]) == demo.root and Path(seen["raw"]) == demo.raw.resolve()
        assert Path(seen["study"]) == demo.root and Path(seen["reference"]) == paths.REFERENCE
        assert seen["inbox"] == "0" and seen["site"] is None
    assert new <= {f"stations/{sid}/{p}" for sid in demo.station_ids()
                   for p in ("processed/results/marker.json", "build.log", "station.json")}
    failed = demo.record("demo-dorfnetz")
    assert out["built"]["demo-dorfnetz"]["code"] == 3 and failed["status"] == "failed"
    assert "code 3" in failed["error"]
    assert demo.record("demo-dickert")["status"] == "imported"     # the stand-in left the others' records alone


def test_the_commands_on_the_demo(demo, capsys, monkeypatch):
    from types import SimpleNamespace

    from kapacitet.pipeline import main
    from kapacitet.study import cli

    main(["study", "status", str(demo.root)])
    out = capsys.readouterr().out
    assert "demo-landnetz: imported [never built], ? customers" in out
    main(["study", "check", str(demo.root), "--station", "demo-dickert"])
    assert "--- demo-dickert" in capsys.readouterr().out
    main(["study", "import", str(demo.root), "--contract", str(demo.station_dir("demo-landnetz") / "import"),
          "--station", "NS-0001"])
    assert "Added station NS-0001" in capsys.readouterr().out and demo.has("NS-0001")

    seen = {}

    def fake_run(cmd, env):
        seen.update(cmd=cmd, env=env)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setenv("KAPACITET_DATA_DIR", "somewhere-else")
    with pytest.raises(SystemExit) as code:
        main(["study", "fetch", str(demo.root)])
    assert code.value.code == 0 and seen["cmd"][-3:] == ["fetch", "--only", "smhi,prices"]
    env = seen["env"]
    assert Path(env["KAPACITET_RAW_DIR"]) == demo.raw and Path(env["KAPACITET_STUDY"]) == demo.root
    assert Path(env["KAPACITET_DATA_DIR"]) == demo.root                # never the reference network's folder

    bare = init_study(demo.root.parent / "no-place")                    # no [site] lat, lon of its own
    seen.clear()
    with pytest.raises(SystemExit) as code:
        main(["study", "fetch", str(bare.root)])
    assert code.value.code == 2 and not seen and "no place of its own" in capsys.readouterr().out


# ---------------------------------------------------------------- the weather and household profiles

ONE_YEAR = "[time]\nreference_year = 2025\nweather_first_year = 2025\nweather_last_year = 2025\n"


@pytest.fixture()
def placed(tmp_path, demo, monkeypatch):
    """The demo study with one weather year, a reference network of stand-in files, and this
    process's data folder on a station's."""
    ref = tmp_path / "reference"
    (ref / "processed" / "weather").mkdir(parents=True)
    (ref / "processed" / "weather" / "weather_2025.parquet").write_bytes(b"reference weather")
    monkeypatch.setattr(paths, "REFERENCE", ref)
    monkeypatch.setattr(paths, "PROCESSED", demo.station_dir("demo-dickert") / "processed")
    demo.toml.write_text(demo.toml.read_text(encoding="utf-8").replace("[time]\n", ONE_YEAR, 1), encoding="utf-8")
    return open_study(demo.root)


def _fetched(study, lat: float | None = None, lon: float | None = None) -> None:
    """What `study fetch` leaves in the study's raw/: the files and their manifest entries."""
    from kapacitet.config import study_settings
    from kapacitet.ingest import smhi

    cfg = study_settings(study.config)
    lat, lon = (lat, lon) if lat is not None else cfg.site.strang_point
    manifest = {}
    (study.raw / "smhi").mkdir(parents=True, exist_ok=True)
    for y in cfg.weather_years:
        for p in ("ghi", "dni"):
            param = cfg.strang[p]
            (study.raw / "smhi" / smhi.strang_name(cfg.site.key, param, y)).write_text("[]", encoding="utf-8")
            manifest[smhi.strang_entry(cfg.site.key, param, y)] = {"url": smhi.strang_url(param, lat, lon)}
    (study.raw / "smhi" / smhi.metobs_name(cfg.temperature_station)).write_text("", encoding="utf-8")
    manifest[smhi.metobs_entry(cfg.temperature_station)] = {"url": "metobs"}
    (study.raw / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_the_reference_weather_is_copied_and_never_written_through(placed, monkeypatch):
    study, notes = placed, []
    reference = paths.REFERENCE / "processed" / "weather" / "weather_2025.parquet"
    mine = paths.PROCESSED / "weather" / "weather_2025.parquet"
    assert station.place_weather(study, lambda k, t: notes.append((k, t)))["source"] == "reference"
    assert mine.read_bytes() == b"reference weather" and not os.path.samefile(mine, reference)
    assert notes[0][0] == "synthetic" and "has not fetched its own" in notes[0][1]

    mine.unlink()
    os.link(reference, mine)                                     # what an earlier build left: a hard link

    def in_place(years):                                         # a writer that writes through what is there
        for y in years:
            with open(paths.processed("weather", f"weather_{y}.parquet"), "wb") as fh:
                fh.write(b"study weather")

    monkeypatch.setattr("kapacitet.profiles.weather.build_weather", in_place)
    _fetched(study)
    notes.clear()
    assert station.place_weather(study, lambda k, t: notes.append((k, t)))["source"] == "study"
    assert mine.read_bytes() == b"study weather"
    assert reference.read_bytes() == b"reference weather"         # the reference network is untouched
    assert notes[0][0] == "real" and "SMHI STRÅNG at" in notes[0][1]


def test_fetching_weather_makes_a_built_station_stale(placed):
    study = placed
    study.save(study.record("demo-dickert") | {"status": "ready", "inputs": study.digests("demo-dickert"),
                                               "method_version": "1.0.0", "quick": False})
    assert portfolio.reason(study, "demo-dickert") == "unchanged"
    _fetched(study)
    assert station.weather_source(study)["source"] == "study"
    assert portfolio.reason(study, "demo-dickert") == "inputs changed"


def test_weather_fetched_for_another_place_is_not_the_studys(placed):
    import re

    study = placed
    _fetched(study, lat=63.8, lon=20.3)                          # [site] moved after the fetch
    assert station.weather_source(study)["source"] == "reference"
    _fetched(study)                                              # fetched again, for the study's point
    lat, lon = config.study_settings(study.config).site.strang_point
    assert station.weather_source(study)["point"] == (round(lat, 4), round(lon, 4))
    text = re.sub(r"(?m)^(lat|lon|temperature_station) = .*\n", "", study.toml.read_text(encoding="utf-8"))
    study.toml.write_text(text, encoding="utf-8")                # no place of its own: never its downloads
    bare = open_study(study.root)
    _fetched(bare)
    src = station.weather_source(bare)
    assert src["source"] == "reference" and "no place of its own" in src["reason"]


def test_a_download_is_fetched_again_when_its_request_changed(tmp_path, monkeypatch):
    from kapacitet.ingest import provenance, smhi

    dest = tmp_path / "strang.json"
    dest.write_text("[]", encoding="utf-8")
    here, there = smhi.strang_url(117, 59.3233, 17.9613), smhi.strang_url(117, 63.8, 20.3)
    monkeypatch.setattr(provenance, "load_manifest", lambda: {"e": {"url": here}})
    assert provenance.cached("e", dest, url=here) and provenance.cached("e", dest)
    assert not provenance.cached("e", dest, url=there) and not provenance.cached("e", dest, url=here, refresh=True)
    assert smhi.point_of(here) == (59.3233, 17.9613)


def test_the_household_pool_is_linked_or_scaled_to_the_studys_mean(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd

    from kapacitet.profiles.household import POOL_YEAR

    ref = tmp_path / "reference" / "processed" / "households"
    ref.mkdir(parents=True)
    hourly = f"pool_hourly_{POOL_YEAR}.npy"
    np.save(ref / hourly, np.full((2, 8760), 0.5, dtype=np.float32))
    np.save(ref / f"pool_10min_{POOL_YEAR}.npy", np.full((2, 52560), 0.5, dtype=np.float32))
    pd.DataFrame({"hh": [0, 1], "annual_kwh": [4380.0, 4380.0]}).to_parquet(ref / "pool_meta.parquet", index=False)
    (ref / "pool_info.json").write_text(json.dumps({"n": 2, "scale_to_mean_kwh": 4380.0, "scale_factor": 0.9}),
                                        encoding="utf-8")
    monkeypatch.setattr(paths, "REFERENCE", tmp_path / "reference")
    monkeypatch.setattr(paths, "PROCESSED", tmp_path / "station" / "processed")
    mine, notes = paths.PROCESSED / "households", []
    try:
        config.set_overrides({"household_kwh_per_year": 4380.0}, layer="study")
        station.place_households(lambda k, t: notes.append((k, t)))
        assert (mine / hourly).read_bytes() == (ref / hourly).read_bytes()     # the pool as it is

        config.set_overrides({"household_kwh_per_year": 5256.0}, layer="study")
        assert station.place_households(lambda k, t: notes.append((k, t)))["scale"] == pytest.approx(1.2)
        assert np.allclose(np.load(mine / hourly).sum(axis=1), 5256.0, rtol=1e-5)
        assert np.allclose(np.load(ref / hourly), 0.5)                         # the reference is untouched
        assert pd.read_parquet(mine / "pool_meta.parquet")["annual_kwh"].tolist() == pytest.approx([5256.0] * 2)
        info = json.loads((mine / "pool_info.json").read_text(encoding="utf-8"))
        assert info["scale_to_mean_kwh"] == 5256.0 and info["scale_factor"] == pytest.approx(1.08)
        assert "scaled from a mean of 4380 to the study's 5256 kWh/yr" in notes[-1][1]
    finally:
        config.set_overrides(None, layer="study")
        config.reload()


def test_the_network_and_its_phases_say_where_they_came_from():
    from kapacitet.importer.validate import WARNING, Report

    unrecorded = Report()
    unrecorded.add(WARNING, "phase not recorded", "phase_not_recorded", 3)
    synthetic = station.network_notes({"synthetic": True, "generator": "pandapower", "synthetic_parts": ["fuses"]},
                                      unrecorded)
    assert [k for k, _ in synthetic] == ["synthetic", "synthetic", "assumed"]
    assert "every service is taken as three-phase" in synthetic[-1][1]
    declared = station.network_notes({"declared": True, "format": "contract"}, Report())
    assert declared[0] == ("real", "network: the DSO's export (contract)") and declared[1][0] == "real"
    unknown = station.network_notes({"format": "contract"}, Report())
    assert unknown[0][0] == "assumed" and "unverified" in unknown[0][1]


# ---------------------------------------------------------------- equipment, meters and load

@pytest.fixture()
def imported(demo, monkeypatch):
    """demo-dickert's import step, run in this process: its network in its own processed/grid."""
    from kapacitet.importer.build import import_directory

    sid = "demo-dickert"
    monkeypatch.setattr(paths, "PROCESSED", demo.station_dir(sid) / "processed")
    import_directory(demo.station_dir(sid) / "import", equipment=False)
    return demo, sid


def _ids(study, sid) -> list[str]:
    import pandas as pd

    return pd.read_csv(study.station_dir(sid) / "import" / "customers.csv", dtype=str)["customer_id"].tolist()


def _record(study, sid):
    rec = study.record(sid)
    rec["provenance"] = {k: [] for k in ("real", "synthetic", "assumed")}
    rec["summary"] = {}
    return rec, (lambda kind, text: rec["provenance"][kind].append(text))


def test_the_equipment_step_takes_the_stations_rows_of_the_studys_records(imported):
    import pandas as pd

    study, sid = imported
    ids = _ids(study, sid)
    pd.DataFrame({"customer_id": [ids[0], ids[1], "elsewhere"], "pv_kwp": [6.0, 3.0, 9.0],
                  "pv_kw_ac": [5.0, None, 7.0], "pv_phase": ["abc", None, None],
                  "source": ["installer", "installer", "survey"]}).to_csv(
        study.inputs / "equipment.csv", sep=";", decimal=",", index=False)        # the Swedish Excel dialect
    rec, note = _record(study, sid)
    out = station.place_equipment(study, sid, rec, note)
    assert out["pv"] == 2 and rec["summary"]["existing_pv_kw"] == pytest.approx(7.5)
    cust = pd.read_parquet(paths.PROCESSED / "grid" / "customers.parquet").set_index("osm_id")
    assert cust.loc[ids[0], "existing_pv_kw_ac"] == 5.0 and cust.loc[ids[2], "existing_pv_kwp"] == 0.0
    assert rec["quality"]["equipment"]["usable"]
    assert any(t.startswith("existing equipment: the DSO's records (inputs/equipment.csv), 2 PV systems (7.5 kW AC)")
               for t in rec["provenance"]["real"])
    assert "existing equipment: PV AC rating taken from its kWp (1)" in rec["provenance"]["assumed"]
    # `study check` says the same before any build
    assert any(f.code == "equipment_summary" for f in check(study, [sid])[sid].findings)

    # a value it cannot read fails the step
    pd.DataFrame({"customer_id": [ids[0]], "pv_kwp": ["six"]}).to_csv(study.inputs / "equipment.csv", index=False)
    with pytest.raises(RuntimeError, match="pv_kwp"):
        station.place_equipment(study, sid, *_record(study, sid))
    # without records nothing existing is counted, and the columns are gone
    (study.inputs / "equipment.csv").unlink()
    rec, note = _record(study, sid)
    assert station.place_equipment(study, sid, rec, note) == {"rows": 0}
    assert not [c for c in pd.read_parquet(paths.PROCESSED / "grid" / "customers.parquet").columns
                if c.startswith("existing_")]
    assert any("no inventory" in t for t in rec["provenance"]["assumed"])


def test_the_meters_step_keeps_the_stations_readings_and_the_load_step_says_what_is_metered(imported, monkeypatch):
    import pandas as pd
    from fixtures import meters_tiny

    from kapacitet.profiles import metered

    study, sid = imported
    rec, note = _record(study, sid)
    assert station.place_meters(study, sid, rec, note) == {"files": 0} and "meters" not in rec["quality"]
    ids = _ids(study, sid)
    meters_tiny.write(study.inputs / "meters", meters_tiny.frame(customers=(ids[0], ids[1], "nobody")))
    out = station.place_meters(study, sid, rec, note)
    assert out["meters"] == 2 and out["files"] == 1
    hourly = pd.read_parquet(paths.PROCESSED / "meters" / "hourly.parquet")
    assert set(hourly["customer_id"]) == {ids[0], ids[1]}
    found = {f["code"] for f in rec["quality"]["meters"]["findings"]}
    assert {"meter_unmapped", "meter_no_readings"} <= found
    assert any(t.startswith(f"meter readings: 2 of {len(ids)} customers") for t in rec["provenance"]["real"])

    # the load step: the pool's note now covers only the customers without enough readings
    monkeypatch.setattr(metered, "build", lambda years=None: {
        "customers": {"total": len(ids), "fitted": 2, "too_few_days": 0, "without_readings": len(ids) - 2,
                      "export_seen": 1},
        "median": {"r2": 0.8123}, "base_temp_c": 17.0, "min_complete_days": 14})
    rec["provenance"]["synthetic"].append(station.POOL_NOTE)
    assert station.place_load(rec, note)["fitted"] == 2
    assert rec["provenance"]["synthetic"] == [f"household load of the {len(ids) - 2} customers without enough meter "
                                              "readings: the CREST profile pool (richardsonpy), not metered"]
    assert any(t.startswith(f"household load: metered at 2 of {len(ids)} customers") and "R² 0.81" in t
               for t in rec["provenance"]["real"])
    assert any("daytime import understates" in t for t in rec["provenance"]["assumed"])

    # readings it has to refuse fail the step, and the record says why
    bad = meters_tiny.frame(customers=(ids[0],))
    bad["start"] = bad["start"].str[:19]
    meters_tiny.write(study.inputs / "meters", bad)
    with pytest.raises(RuntimeError, match="UTC offset"):
        station.place_meters(study, sid, rec, note)
    assert rec["quality"]["meters"]["usable"] is False
    assert not (paths.PROCESSED / "meters" / "hourly.parquet").exists()


def test_the_new_inputs_make_a_built_station_stale(demo):
    from fixtures import meters_tiny

    station.build(demo, "demo-dickert", steps={s: (lambda: {}) for s in station.STEPS})
    assert portfolio.reason(demo, "demo-dickert") == "unchanged"
    (demo.inputs / "equipment.csv").write_text("customer_id,pv_kwp\nC1,4\n", encoding="utf-8")
    assert portfolio.reason(open_study(demo.root), "demo-dickert") == "inputs changed"
    station.build(demo, "demo-dickert", steps={s: (lambda: {}) for s in station.STEPS})
    meters_tiny.write(demo.inputs / "meters")
    assert portfolio.reason(open_study(demo.root), "demo-dickert") == "inputs changed"


def _has_reference_profiles() -> bool:
    from kapacitet.config import settings
    from kapacitet.profiles.household import POOL_YEAR

    ref = paths.REFERENCE / "processed"
    return ((ref / "households" / f"pool_hourly_{POOL_YEAR}.npy").is_file()
            and all((ref / "weather" / f"weather_{y}.parquet").is_file() for y in settings().weather_years))


needs_profiles = pytest.mark.skipif(not _has_reference_profiles(),
                                    reason="no reference weather and household pool (run: python -m kapacitet all)")


def _snapshot(root: Path) -> dict[str, int]:
    return {p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}


def _study_weather_from_the_reference(study) -> None:
    """The study's own downloads, made of the reference network's: the demo's place is the
    reference site's, so the requests are the same ones."""
    import shutil

    from kapacitet.config import settings, study_settings
    from kapacitet.ingest import smhi

    ref, cfg = settings(), study_settings(study.config)
    assert cfg.site.strang_point == ref.site.strang_point
    have = json.loads((paths.RAW / "manifest.json").read_text(encoding="utf-8"))
    manifest = {}
    (study.raw / "smhi").mkdir(parents=True, exist_ok=True)
    pairs = [(smhi.strang_name(ref.site.key, cfg.strang[p], y), smhi.strang_name(cfg.site.key, cfg.strang[p], y),
              smhi.strang_entry(ref.site.key, cfg.strang[p], y), smhi.strang_entry(cfg.site.key, cfg.strang[p], y))
             for y in cfg.weather_years for p in ("ghi", "dni")]
    station_id = cfg.temperature_station
    pairs.append((smhi.metobs_name(station_id), smhi.metobs_name(station_id), smhi.metobs_entry(station_id),
                  smhi.metobs_entry(station_id)))
    for src, dst, src_id, dst_id in pairs:
        shutil.copyfile(paths.RAW / "smhi" / src, study.raw / "smhi" / dst)
        manifest[dst_id] = dict(have[src_id], id=dst_id, path=f"smhi/{dst}")
    (study.raw / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@needs_profiles
def test_the_demo_builds_end_to_end_with_the_existing_pipeline(demo):
    reference = {p: p.stat().st_mtime_ns for p in (paths.REFERENCE / "processed").rglob("*") if p.is_file()}
    for sid in demo.station_ids():                     # one at a time: each process writes only its own folder
        before = _snapshot(demo.root)
        out = portfolio.run(demo, stations=[sid], quick=True, timeout=900)
        assert out["built"][sid]["status"] == "ready", procs.log_tail(demo.station_dir(sid), 30)
        after = _snapshot(demo.root)
        touched = {p for p, t in after.items() if before.get(p) != t} | (set(before) - set(after))
        assert touched and all(p.startswith(f"stations/{sid}/") for p in touched), touched
    for sid in demo.station_ids():
        rec = demo.record(sid)
        assert [s["status"] for s in rec["steps"]] == ["done"] * len(station.STEPS)
        assert rec["summary"]["customers"] == len((demo.station_dir(sid) / "import" / "customers.csv")
                                                  .read_text(encoding="utf-8").splitlines()) - 1
        from test_station_capacity import check_capacity

        cap = json.loads((demo.station_dir(sid) / "processed" / "results" / "capacity.json").read_text())
        check_capacity(cap)
        assert cap["station"] == sid and cap["mv_band_pu"] == [0.98, 1.06] and cap["orders"] == 5
        gen = cap["generation"]
        assert gen["status"] != "ok" or 0 <= gen["static_kw"] <= gen["kw"] + 1e-9
        assert rec["summary"]["generation_kw"] == gen.get("kw")
        synthetic = " ".join(rec["provenance"]["synthetic"])
        assert "weather: the reference site's SMHI years" in synthetic and "network: pandapower" in synthetic
        assert not rec["provenance"]["real"]
        assert any("phases: not recorded" in t for t in rec["provenance"]["assumed"])
        weather = demo.station_dir(sid) / "processed" / "weather" / "weather_2025.parquet"
        assert not os.path.samefile(weather, paths.REFERENCE / "processed" / "weather" / "weather_2025.parquet")
    assert {p: p.stat().st_mtime_ns for p in reference} == reference    # the reference data are untouched
    assert {p for p in (paths.REFERENCE / "processed").rglob("*") if p.is_file()} == set(reference)
    again = portfolio.run(demo, quick=True)
    assert again["built"] == {} and set(again["skipped"].values()) == {"unchanged"}

    # the study fetches weather of its own: the stations are stale, and a rebuild leaves the
    # reference network's files as they were
    reference_bytes = {p: p.read_bytes() for p in (paths.REFERENCE / "processed" / "weather").glob("*.parquet")}
    _study_weather_from_the_reference(demo)
    assert set(portfolio.plan(demo, quick=True).values()) == {"inputs changed"}
    out = portfolio.run(demo, stations=["demo-landnetz"], quick=True, timeout=900)
    assert out["built"]["demo-landnetz"]["status"] == "ready", procs.log_tail(demo.station_dir("demo-landnetz"), 30)
    rec = demo.record("demo-landnetz")
    assert any(t.startswith("weather: SMHI STRÅNG at") for t in rec["provenance"]["real"])
    assert {p: p.read_bytes() for p in reference_bytes} == reference_bytes
    assert {p: p.stat().st_mtime_ns for p in reference} == reference
