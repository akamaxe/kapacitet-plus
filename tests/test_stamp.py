"""engine/stamp.py: every results file says with what it was computed.

A stage can be run again alone after the code or a library has moved, so the results of one data
folder need not come from one run. Each file carries the block of the run that wrote it: when, the
commit of this code and the library versions. A file without one was written before the block
existed, and nothing fills it in afterwards.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import tiny_grid

from kapacitet import __version__
from kapacitet.engine import stamp

ENGINE = Path(stamp.__file__).parent


def test_the_block_names_when_the_code_and_the_libraries():
    block = stamp.computed()
    assert set(block) == {"at", "code", "versions"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", block["at"])
    assert block["code"]["version"] == __version__ and "commit" in block["code"]
    assert block["code"]["commit"] is None or re.fullmatch(r"[0-9a-f]{40}", block["code"]["commit"])
    assert block["versions"]["kapacitet"] == __version__
    assert {"numpy", "scipy", "pandas", "pandapower"} <= set(block["versions"])


def test_a_document_is_stamped_and_anything_else_is_left_alone():
    doc = {"year": 2025, "rows": [1, 2]}
    out = stamp.stamped(doc)
    assert out["year"] == 2025 and out["rows"] == [1, 2] and set(out["computed"]) == {"at", "code", "versions"}
    assert "computed" not in doc                                   # a copy: the caller's object is not touched
    assert stamp.stamped([1, 2]) == [1, 2] and stamp.stamped(None) is None
    json.dumps(out)                                                # plain JSON, as every writer needs


def test_every_writer_of_a_results_file_stamps_it():
    """No results JSON is written past the stamp: each `write_json_atomic` into results/ in the
    engine wraps its document in `stamped`, directly or through the two shared writers."""
    unstamped = []
    for path in sorted(ENGINE.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"write_json_atomic\((?P<args>[^\n]*)", text):
            args = m.group("args")
            into_results = 'processed("results"' in args or args.startswith(("js,", "path, stamped", "path,"))
            if into_results and "stamped(" not in args:
                unstamped.append(f"{path.name}: {args[:70]}")
    assert not unstamped, unstamped
    run = (ENGINE / "run.py").read_text(encoding="utf-8")
    assert 'write_json_atomic(processed("results", name), stamped(obj)' in run
    operation = (ENGINE / "operation.py").read_text(encoding="utf-8")
    assert "write_json_atomic(path, stamped(obj)" in operation


def test_a_stage_run_on_the_tiny_grid_writes_its_block(tiny_processed):
    from kapacitet.engine import protection
    from kapacitet.engine.run import _write
    from kapacitet.paths import processed

    with tiny_grid(tiny_processed):
        protection.write()
        out = json.loads(processed("results", "protection.json").read_text(encoding="utf-8"))
        assert out["computed"]["versions"]["pandapower"] and out["computed"]["code"]["version"] == __version__
        assert "feeders" in out and "summary" in out               # the document itself is as it was
        _write("stamp_probe.json", {"a": 1})
        probe = processed("results", "stamp_probe.json")
        assert json.loads(probe.read_text(encoding="utf-8"))["computed"]["versions"]["kapacitet"] == __version__
        probe.unlink()
