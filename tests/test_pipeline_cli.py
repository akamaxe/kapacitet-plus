"""The command line routes its flags where they belong (pipeline.py), without running the engine.

Each step is replaced by a recorder, so nothing is fetched, built or computed: what is checked is
which step runs, with which `--only` and how many workers.
"""

from __future__ import annotations

import shutil

import pytest

from kapacitet import pipeline


@pytest.fixture()
def calls(monkeypatch):
    from kapacitet.engine import run

    seen: list[tuple] = []
    monkeypatch.setattr(pipeline, "cmd_fetch", lambda a: seen.append(("fetch", a.only)))
    monkeypatch.setattr(pipeline, "cmd_build", lambda a: seen.append(("build",)))
    monkeypatch.setattr(pipeline, "cmd_profiles", lambda a: seen.append(("profiles", a.quick)))
    monkeypatch.setattr(run, "compute_all", lambda quick=False, workers=6: seen.append(("all", quick, workers)))
    monkeypatch.setattr(run, "compute_extra",
                        lambda quick=False, t0=None, only=None, workers=4: seen.append(("extra", quick, only, workers)))
    return seen


def _exit_code(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as err:
        pipeline.main(argv)
    return err.value.code


def test_all_with_a_fetch_filter_computes_everything(calls):
    pipeline.main(["all", "--only", "prices"])
    assert calls == [("fetch", "prices"), ("build",), ("profiles", False), ("all", False, 6)]


def test_all_passes_its_workers_and_quick_on(calls):
    pipeline.main(["all", "--quick", "--workers", "2"])
    assert calls[-1] == ("all", True, 2)


def test_all_refuses_an_unknown_source_before_doing_anything(calls, capsys):
    assert _exit_code(["all", "--only", "foresight"]) == 2
    assert calls == []
    assert "osm,zones,smhi,esett,scb,prices" in capsys.readouterr().out


def test_compute_takes_its_workers_with_or_without_the_extra_stage(calls):
    pipeline.main(["compute", "--workers", "2"])
    pipeline.main(["compute"])
    pipeline.main(["compute", "--stage", "extra", "--only", "foresight", "--workers", "1"])
    pipeline.main(["compute", "--stage", "extra"])
    assert calls == [("all", False, 2), ("all", False, 6), ("extra", False, {"foresight"}, 1),
                     ("extra", False, None, 4)]


@pytest.mark.parametrize("argv", [["compute", "--workers", "0"], ["compute", "--stage", "extra", "--workers", "-2"],
                                  ["all", "--workers", "0"], ["all", "--workers", "many"]])
def test_a_number_of_workers_below_one_is_refused_before_anything_runs(calls, capsys, argv):
    """Not quietly six (0 was falsy), nor a traceback from the process pool after fetch and build."""
    assert _exit_code(argv) == 2
    assert calls == []
    assert "worker processes" in capsys.readouterr().err


def test_only_without_the_extra_stage_is_refused_instead_of_running_everything(calls, capsys):
    assert _exit_code(["compute", "--only", "foresight"]) == 2
    assert calls == []
    assert "--stage extra" in capsys.readouterr().out


def test_an_unknown_stage_is_refused(calls, capsys):
    from kapacitet.engine import run

    assert _exit_code(["compute", "--stage", "extra", "--only", "foresight,nope"]) == 2
    assert calls == []
    assert run.EXTRA_STAGES[0] in capsys.readouterr().out


def test_the_help_lists_every_stage_the_engine_has(capsys):
    from kapacitet.engine import run

    assert _exit_code(["compute", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert ",".join(run.EXTRA_STAGES) in text.replace(", ", ",")


def test_a_command_that_needs_no_stage_list_does_not_import_the_engine():
    """The stage list is filled in only when help is shown: `serve` starts without the engine."""
    import subprocess
    import sys

    code = ("import sys\nfrom kapacitet import pipeline\n"
            "try:\n    pipeline.main(['fetch', '--only', 'nope'])\nexcept SystemExit:\n    pass\n"
            "print('kapacitet.engine.run' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip().splitlines()[-1] == "False"


def test_bundle_pin_and_unbundle_sha256_check_the_same_digest(tmp_path, monkeypatch, capsys):
    """`bundle --pin` records the archive; `unbundle --sha256` takes it and refuses any other file."""
    import json

    from kapacitet import bundle

    data = tmp_path / "data"
    (data / "processed" / "results").mkdir(parents=True)
    (data / "processed" / "results" / "showcase.json").write_text('{"year": 2025}', encoding="utf-8")
    monkeypatch.setattr(bundle, "DATA", data)
    monkeypatch.setattr(bundle, "RELEASE", tmp_path / "config" / "data-release.json")
    out = tmp_path / "kapacitet-data.zip"
    pipeline.main(["bundle", "--out", str(out), "--pin"])
    rel = json.loads(bundle.RELEASE.read_text(encoding="utf-8"))
    assert rel["sha256"] == bundle.file_sha256(out) and rel["size"] == out.stat().st_size

    said = capsys.readouterr().out
    assert "gh release upload data" in said and "config/data-release.json" in said

    monkeypatch.setattr(bundle, "DATA", tmp_path / "elsewhere")
    pipeline.main(["unbundle", str(out), "--sha256"])
    assert (tmp_path / "elsewhere" / "processed" / "results" / "showcase.json").exists()
    # the flag before the file reads the same: --sha256 takes no value
    pipeline.main(["unbundle", "--sha256", "--force", str(out)])
    pipeline.main(["unbundle", "--expect-sha256", rel["sha256"].upper(), "--force", str(out)])
    capsys.readouterr()
    assert _exit_code(["unbundle", str(out), "--force", "--expect-sha256", "0" * 64]) == 2
    said = capsys.readouterr().out
    assert "not the pinned data release" in said and rel["sha256"] in said
    bundle.RELEASE.unlink()
    assert _exit_code(["unbundle", str(out), "--sha256"]) == 2


@pytest.mark.parametrize("argv", [["--expect-sha256", "kapacitet-data.zip"], ["--expect-sha256", "ab" * 31],
                                  ["--sha256", "--expect-sha256", "0" * 64]], ids=["file", "short", "both"])
def test_unbundle_refuses_a_digest_that_is_not_one(tmp_path, capsys, argv):
    assert _exit_code(["unbundle", str(tmp_path / "kapacitet-data.zip"), *argv]) == 2
    assert "usage:" in capsys.readouterr().err


def test_a_bundle_that_is_not_pinned_is_not_offered_for_publishing(tmp_path, monkeypatch, capsys):
    """The codespace and the setup page take only the pinned file: uploading this one would break them."""
    from kapacitet import bundle

    data = tmp_path / "data"
    (data / "processed" / "results").mkdir(parents=True)
    (data / "processed" / "results" / "showcase.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bundle, "DATA", data)
    monkeypatch.setattr(bundle, "RELEASE", tmp_path / "config" / "data-release.json")
    pipeline.main(["bundle", "--out", str(tmp_path / "mine.zip")])
    said = capsys.readouterr().out
    assert "gh release upload" not in said and "--pin" in said
    assert not bundle.RELEASE.exists()


@pytest.fixture()
def checkout(tmp_path, monkeypatch):
    """A git checkout with its data folder inside, as a fresh clone has it, on the reference network.

    `use(folder)` points the data folder there, `home(folder)` the folder that holds datasets/."""
    from kapacitet import paths

    if pipeline.git_working_tree(tmp_path) is not None:
        pytest.skip("the temporary folder is itself inside a git working tree")
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")  # a worktree's .git is a file

    def use(data):
        monkeypatch.setattr(paths, "DATA", data)
        monkeypatch.setattr(paths, "PROCESSED", data / "processed")
        return data

    def home(folder):
        monkeypatch.setattr(paths, "DATASETS", folder / "datasets")
        return folder

    monkeypatch.setattr(paths, "ROOT", root)
    monkeypatch.delenv("KAPACITET_DATASET", raising=False)
    use(root / "data")
    home(root / "data")
    return root, use, home


def _imported_by_the_cli(data) -> None:
    grid = data / "processed" / "grid"
    grid.mkdir(parents=True)
    (grid / "summary.json").write_text('{"imported": true}', encoding="utf-8")


def _network_of_the_app(home, ds_id: str = "abc123"):
    folder = home / "datasets" / ds_id
    (folder / "import").mkdir(parents=True)
    (folder / "dataset.json").write_text('{"id": "%s", "status": "ready"}' % ds_id, encoding="utf-8")
    return folder


def test_the_working_tree_is_the_nearest_folder_with_a_git(tmp_path):
    if pipeline.git_working_tree(tmp_path) is not None:
        pytest.skip("the temporary folder is itself inside a git working tree")
    assert pipeline.git_working_tree(tmp_path / "a" / "b") is None
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / ".git").mkdir()
    # a folder that does not exist yet (a data folder before its first run) is inside it all the same
    assert pipeline.git_working_tree(tmp_path / "a" / "b" / "data") == (tmp_path / "a").resolve()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    assert pipeline.git_working_tree(tmp_path / "a" / "b" / "data") == (tmp_path / "a" / "b").resolve()


def test_the_reference_network_in_the_checkout_is_not_warned_about(checkout):
    assert pipeline.data_in_checkout_warning() is None


def test_an_operators_network_inside_the_checkout_is_warned_about(checkout, monkeypatch):
    """Served as a network of the app (its folder under data/datasets/), or written over the
    reference network by `kapacitet import`: either way only .gitignore keeps it out of a commit."""
    root, use, home = checkout
    monkeypatch.setenv("KAPACITET_DATASET", "abc123")
    use(_network_of_the_app(root / "data"))
    said = pipeline.data_in_checkout_warning()
    assert said and "KAPACITET_HOME" in said and "KAPACITET_DATA_DIR" in said
    # one folder named for both: the network's own lies inside datasets/
    assert said.count(str(root / "data" / "datasets")) == 1

    monkeypatch.delenv("KAPACITET_DATASET")
    shutil.rmtree(root / "data" / "datasets")
    _imported_by_the_cli(use(root / "data"))
    said = pipeline.data_in_checkout_warning()
    assert said and str(root / "data") in said


def test_a_network_kept_in_the_checkout_is_warned_about_while_another_is_in_use(checkout):
    """Its import/ keeps the operator's four files whether it is in use or not."""
    root, use, home = checkout
    _network_of_the_app(root / "data")
    said = pipeline.data_in_checkout_warning()
    assert said and str(root / "data" / "datasets") in said


def test_a_project_inside_a_larger_repository_is_warned_about(checkout, tmp_path):
    """Vendored into another repository, or a folder of a monorepo: the .git is above the project."""
    root, use, home = checkout
    (root / ".git").unlink()
    (tmp_path / ".git").mkdir()
    _imported_by_the_cli(use(root / "data"))
    assert pipeline.data_in_checkout_warning()


def test_an_operators_network_outside_the_checkout_or_in_a_container_is_not(checkout, monkeypatch, tmp_path):
    root, use, home = checkout
    monkeypatch.setenv("KAPACITET_DATASET", "abc123")
    pilot = home(tmp_path / "pilot")
    _imported_by_the_cli(use(_network_of_the_app(pilot)))
    assert pipeline.data_in_checkout_warning() is None
    # the image has no .git: nothing to commit the data to
    home(root / "data")
    use(_network_of_the_app(root / "data"))
    (root / ".git").unlink()
    assert pipeline.data_in_checkout_warning() is None


def test_the_server_says_it_when_it_starts(checkout, monkeypatch, caplog):
    import argparse
    import logging

    import uvicorn

    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(k["port"]))
    monkeypatch.setattr(pipeline, "_wait_for_port", lambda host, port: None)
    monkeypatch.setenv("KAPACITET_DATASET", "abc123")
    with caplog.at_level(logging.WARNING, logger="kapacitet"):
        pipeline._serve_child(argparse.Namespace(host="127.0.0.1", port=8123))
    assert started == [8123]
    assert any("git working tree" in r.getMessage() for r in caplog.records)


def test_purge_needs_an_age_and_stays_away_from_a_running_server(tmp_path, monkeypatch, capsys):
    from conftest import another_server
    from kapacitet.api import records

    monkeypatch.setattr(records, "folder", lambda: tmp_path / "requests")
    monkeypatch.delenv("KAPACITET_RETENTION_DAYS", raising=False)
    assert _exit_code(["purge"]) == 2 and "--older-than" in capsys.readouterr().out
    assert _exit_code(["purge", "--older-than", "-1"]) == 2
    asked = []
    monkeypatch.setattr(records, "purge", lambda days, dry_run=False: asked.append((days, dry_run)) or {
        "assessments": 0, "cutoff": "2026-10-06T00:00:00+00:00", "kept_for_decisions": 0})
    monkeypatch.setenv("KAPACITET_RETENTION_DAYS", "30")
    pipeline.main(["purge"])
    pipeline.main(["purge", "--older-than", "7", "--dry-run"])
    assert asked == [(30, False), (7, True)]
    with another_server(tmp_path):
        assert _exit_code(["purge", "--older-than", "30"]) == 2
        assert "KAPACITET_RETENTION_DAYS" in capsys.readouterr().out and len(asked) == 2
