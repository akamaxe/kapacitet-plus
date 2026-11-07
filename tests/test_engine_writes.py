"""The engine's writers replace each file whole (kapacitet.atomic): the server rereads a results
file or a map binary when its date changes, so it must never find one half written."""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import numpy as np
from conftest import synthetic_profiles, tiny_grid

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


def test_the_stages_leave_whole_files_and_no_temporary_one(tiny_processed, tmp_path):
    """The showcase, the operation stage (one rule, both bases), the forecast ladder and the
    protection check, run on a copy of the tiny grid's folder: every file they write reads back,
    each map state is as long as its header says, and no temporary file is left behind."""
    from kapacitet.engine import forecast, hosting, operation, protection, run
    from kapacitet.engine.scenario import adoption_order

    folder = tmp_path / "processed"
    shutil.copytree(tiny_processed, folder)
    with tiny_grid(folder):
        m = run.model()
        hp, ev = run.background_masks(m.C)
        ms = hosting.static_adopters(m, adoption_order(0, m.C), hp, ev)
        run.showcase(0, ms, ms, m.C, synthetic_profiles.YEARS, quick=True)
        operation.build(only_policies=("maxmin",))
        forecast.write()
        protection.write()
    written = [f for f in folder.rglob("*") if f.is_file() and {"results", "ui"} & set(f.relative_to(folder).parts)]
    assert len(written) > 30
    for f in written:
        if f.suffix == ".json":
            json.loads(f.read_text(encoding="utf-8"))
        elif f.suffix == ".npy":
            np.load(f)
        elif f.suffix == ".npz":
            with np.load(f) as z:
                assert all(z[key].size for key in z.files), f
    for f in folder.rglob("state_*.json"):
        header = json.loads(f.read_text(encoding="utf-8"))
        n = header["n_bus"] + header["n_line"] + header["n_cust"]
        assert len(f.with_suffix(".bin").read_bytes()) == header["hours"] * n, f
    assert not [f.name for f in folder.rglob("*.tmp")]
    # the issued import envelopes: write_import's own output, field by field
    meta = json.loads((folder / "results" / "operation" / "dayahead" / "import.json").read_text(encoding="utf-8"))
    env = np.load(folder / "ui" / "operation" / "dayahead" / "import_envelopes.npy")
    assert meta["verified"] is True and meta["cids"]
    assert meta["planning_state"]["load_margin_pct"] > 0
    assert env.shape[0] == len(meta["cids"]) and env.shape[1] % 24 == 0
    assert (env >= 0).all() and (env <= meta["firm_kw"] + 1e-6).all()
    assert np.allclose(env * 1000.0, np.round(env * 1000.0), atol=1e-2)      # the 3-decimal floor
