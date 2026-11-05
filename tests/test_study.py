"""A study of many stations (study/): where it may live, the split per station, and the build of
each station in a process of its own that writes only its own folder.

Most of this runs without the pipeline's output: the study is the test's own folder, and the
station processes are stand-ins. The end-to-end build of the demo needs the reference network's
weather years and household pool, and is skipped without them."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from kapacitet import paths, procs
from kapacitet.study import StudyError, guard, init_study, open_study, resolve
from kapacitet.study import portfolio, station
from kapacitet.study.intake import add_contract, check, split


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

    inside = paths.ROOT / "studies" / "x"
    with pytest.raises(SystemExit) as code:
        main(["study", "init", str(inside)])
    assert code.value.code == 2 and not (paths.ROOT / "studies").exists()


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
                                                "demo-landnetz": "never built"}
    assert portfolio.plan(demo, quick=False)["demo-dickert"] == "mode changed"
    assert set(portfolio.plan(demo, force=True, quick=True).values()) == {"forced"}


def test_a_contract_folder_is_added_and_checked(tmp_path, demo):
    src = demo.station_dir("demo-landnetz") / "import"
    sid = add_contract(demo, src, "NS-0042")
    assert (demo.inputs / "network" / "contract" / "NS-0042" / "lines.csv").is_file()
    assert "NS-0042" in split(demo)
    rec = demo.record("NS-0042")
    assert rec["origin"]["format"] == "contract" and "synthetic" not in rec["origin"]
    assert check(demo, [sid])[sid].usable
    with pytest.raises(StudyError):
        add_contract(demo, src, "../escape")


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


def _has_reference_profiles() -> bool:
    from kapacitet.config import settings
    from kapacitet.profiles.household import POOL_YEAR

    ref = paths.REFERENCE / "processed"
    return ((ref / "households" / f"pool_hourly_{POOL_YEAR}.npy").is_file()
            and all((ref / "weather" / f"weather_{y}.parquet").is_file() for y in settings().weather_years))


needs_profiles = pytest.mark.skipif(not _has_reference_profiles(),
                                    reason="no reference weather and household pool (run: python -m kapacitet all)")


@needs_profiles
def test_the_demo_builds_end_to_end_with_the_existing_pipeline(demo):
    reference = {p: p.stat().st_mtime_ns for p in (paths.REFERENCE / "processed").rglob("*") if p.is_file()}
    before = _tree(demo.root)
    out = portfolio.run(demo, jobs=3, quick=True, timeout=900)
    assert {sid: r["status"] for sid, r in out["built"].items()} == dict.fromkeys(demo.station_ids(), "ready"), \
        {sid: procs.log_tail(demo.station_dir(sid), 30) for sid in demo.station_ids()}
    for sid in demo.station_ids():
        rec = demo.record(sid)
        assert [s["status"] for s in rec["steps"]] == ["done"] * len(station.STEPS)
        assert rec["summary"]["customers"] == len((demo.station_dir(sid) / "import" / "customers.csv")
                                                  .read_text(encoding="utf-8").splitlines()) - 1
        summary = json.loads((demo.station_dir(sid) / "processed" / "results" / "hosting_summary.json").read_text())
        static, doe = (summary["measures"][k]["kw_ac"]["median"] for k in ("static", "doe"))
        assert 0 < static <= doe
        synthetic = " ".join(rec["provenance"]["synthetic"])
        assert "weather: the reference site's SMHI years" in synthetic and "network: pandapower" in synthetic
        assert not rec["provenance"]["real"]
    new = _tree(demo.root) - before
    assert all(p.startswith("stations/") for p in new)                 # nothing beside the stations' folders
    assert {p: p.stat().st_mtime_ns for p in reference} == reference    # the reference data are untouched
    assert {p for p in (paths.REFERENCE / "processed").rglob("*") if p.is_file()} == set(reference)
    again = portfolio.run(demo, quick=True)
    assert again["built"] == {} and set(again["skipped"].values()) == {"unchanged"}
