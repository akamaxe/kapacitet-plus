"""tools/pf_crossval.py: the PowerFactory cross-check's own logic, without PowerFactory or data."""
import ast
import importlib.util
import sys
from pathlib import Path

import numpy as np

TOOL = Path(__file__).resolve().parents[1] / "tools" / "pf_crossval.py"


def _load():
    spec = importlib.util.spec_from_file_location("pf_crossval", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_level_imports_are_stdlib_only():
    """PowerFactory's interpreter has neither numpy nor pandapower nor Kapacitet+: the file must
    load there, so only standard-library modules may be imported at the top."""
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names <= set(sys.stdlib_module_names) | {"__future__"}, names


def test_pick_hours_puts_both_peaks_first_and_never_repeats():
    m = _load()
    total = np.sin(np.arange(200) / 7.0) * np.arange(200)
    hours = m.pick_hours(total, seed=7, others=8)
    hs = [h["hour"] for h in hours]
    assert hs[0] == int(np.argmax(total)) and hs[1] == int(np.argmin(total))
    assert len(hs) == 10 and len(set(hs)) == 10
    assert hs[2:] == sorted(hs[2:])
    assert hs == [h["hour"] for h in m.pick_hours(total, seed=7, others=8)]   # fixed seed


def test_compare_reports_max_and_mean_and_skips_unsolved_hours():
    m = _load()
    hours = [{"hour": h, "timestamp_utc": "2025-01-01 00:00:00+00:00", "why": "x", "sum_p_kw": 0.0}
             for h in range(3)]
    ref = {"vm_pu": [[1.0, 0.99], [1.0, 0.98], [1.0, 0.97]], "trafo_loading_pct": [10.0, 20.0, 30.0],
           "line_loading_pct": [[5.0], [6.0], [7.0]]}
    case = {"hours": hours, "kapacitet": ref, "pandapower_nr": ref}
    pf = {"ierr": [0, 0, 1],
          "vm_pu": [[1.0, 0.991], [1.0, 0.98], None],
          "trafo_loading_pct": [10.5, 20.0, None],
          "line_loading_pct": [[5.0], [6.25], None]}
    out = m.compare(case, pf)
    assert out["hours_solved"] == 2 and out["hours"] == 3
    k = out["kapacitet"]
    assert abs(k["v_pu"]["max"] - 0.001) < 1e-12 and k["v_pu"]["n"] == 4
    assert abs(k["v_pu"]["mean"] - 0.00025) < 1e-12
    assert k["trafo_loading_pct"]["max"] == 0.5 and k["line_loading_pct"]["max"] == 0.25
    assert out["meets_targets"] == {"v_pu": True, "trafo_loading_pct": True}
    assert len(out["per_hour"]) == 2
    assert "| Bus voltage (pu) |" in m.markdown(out)
