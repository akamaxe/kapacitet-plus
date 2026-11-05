"""The engine's writers replace each file whole (kapacitet.atomic): the server rereads a results
file or a map binary when its date changes, so it must never find one half written."""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import numpy as np
from conftest import tiny_grid

# The modules that write results/ and ui/. queue.py and request.py write the queue, the request
# baselines and the injection cache, and are not listed here.
WRITERS = ("run.py", "run_extra.py", "operation.py", "forecast.py", "national.py", "protection.py")


def _in_place(source: str) -> list[str]:
    """Calls that write a file in place: Path.write_text/write_bytes, np.save/savez to a path (not
    to the handle `fh` of kapacitet.atomic.atomic_file), DataFrame.to_csv to a path, json.dump."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr, base = node.func.attr, node.func.value
        first = node.args[0] if node.args else None
        on = base.id if isinstance(base, ast.Name) else None
        if (attr in ("write_text", "write_bytes")
                or (on == "np" and attr in ("save", "savez", "savez_compressed")
                    and not (isinstance(first, ast.Name) and first.id == "fh"))
                or (attr == "to_csv" and first is not None)
                or (on == "json" and attr == "dump")):
            found.append(f"line {node.lineno}: {ast.unparse(node)[:80]}")
    return found


def test_the_engine_writes_no_file_in_place():
    import kapacitet.engine as engine

    root = Path(engine.__file__).parent
    found = {name: _in_place((root / name).read_text(encoding="utf-8")) for name in WRITERS}
    assert not any(found.values()), found
    # the check itself sees each kind of write
    assert len(_in_place("p.write_text(s)\np.write_bytes(b)\nnp.save(p, a)\nnp.savez(p, x=a)\n"
                         "df.to_csv(p)\njson.dump(o, f)\n")) == 6
    assert _in_place("np.save(fh, a)\ns = df.to_csv(index=False)\n") == []


def test_the_writers_leave_whole_files_and_no_temporary_one(tiny_processed, tmp_path):
    """A results file, a map state (binary and header), a .npy and protection.json written on a
    copy of the tiny grid's folder: each reads back as written, and no temporary file is left."""
    from kapacitet.engine import protection, run
    from kapacitet.paths import processed

    folder = tmp_path / "processed"
    shutil.copytree(tiny_processed, folder)
    with tiny_grid(folder):
        m = run.model()
        p = np.linspace(-2.0, 5.0, m.C * 3).reshape(m.C, 3)
        q = np.zeros_like(p)
        res = m.solve(p, q)
        run.write_state("check", res, m.violations(res, p), p, np.clip(p, 0, None), np.clip(-p, 0, None), None)
        run.save_npy(processed("ui", "check.npy"), np.arange(4, dtype=np.float16))
        run._write("check.json", {"n": np.int64(3), "v": np.arange(3, dtype=float)})
        out = protection.write()

        assert json.loads(processed("results", "check.json").read_text(encoding="utf-8")) == {"n": 3, "v": [0, 1, 2]}
        assert np.array_equal(np.load(processed("ui", "check.npy")), np.arange(4))
        header = json.loads(processed("ui", "state_check.json").read_text(encoding="utf-8"))
        blob = processed("ui", "state_check.bin").read_bytes()
        assert header["hours"] == 3 and len(blob) == 3 * (header["n_bus"] + header["n_line"] + header["n_cust"])
        written = json.loads(processed("results", "protection.json").read_text(encoding="utf-8"))
        assert written == json.loads(json.dumps(out))
    assert not [f.name for f in folder.rglob("*.tmp")]
