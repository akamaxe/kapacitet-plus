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
