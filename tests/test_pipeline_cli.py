"""The command line routes its flags where they belong (pipeline.py), without running the engine.

Each step is replaced by a recorder, so nothing is fetched, built or computed: what is checked is
which step runs, with which `--only` and how many workers.
"""

from __future__ import annotations

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
