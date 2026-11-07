"""tools/dss_crossval.py: Kapacitet+'s power flow against OpenDSS on the tiny grid.

OpenDSS rebuilds the network from plain data (the same `case.json` the PowerFactory check
uses) and solves the same ten hours: the peak export hour, the peak import hour and eight drawn
with seed 7, every house with PV, a heat pump and an EV. The stated tolerances:

- with the transformer as modelled: 1e-4 pu in bus voltage and 0.01 percentage points in
  transformer and line loading. What is left at that level is the transformer's no-load branch,
  which OpenDSS and pandapower place differently: the mean voltage difference is the iron-loss
  current flowing through half the leakage resistance, p0 * vkr / 2 (docs/validation-opendss.md);
- with the no-load branch removed from both: 1e-7 pu and 1e-4 percentage points.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from conftest import needs_opendss, tiny_grid

TOOL = Path(__file__).resolve().parents[1] / "tools" / "dss_crossval.py"
V_TOL_PU = 1e-4
LOADING_TOL_PCT = 0.01
V_TOL_NO_MAGNETISING_PU = 1e-7
LOADING_TOL_NO_MAGNETISING_PCT = 1e-4


def _load():
    spec = importlib.util.spec_from_file_location("dss_crossval", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dss_crossval"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_transformer_command_carries_the_pandapower_data():
    """No OpenDSS needed: the transformer's script, from pandapower's data, is what the docstring
    says (leakage split equally, iron losses and magnetising current apart, the HV tap)."""
    m = _load()
    tr = {"sn_mva": 0.4, "vn_hv_kv": 10.0, "vn_lv_kv": 0.4, "vk_percent": 4.0, "vkr_percent": 1.325,
          "pfe_kw": 0.95, "i0_percent": 0.5, "tap_side": "hv", "tap_neutral": 0, "tap_pos": 1,
          "tap_step_percent": 2.5, "vector_group": "Dyn5", "hv_bus": 0, "lv_bus": 1}
    cmd = m.trafo_command(tr)
    assert "buses=[b0 b1]" in cmd and "conns=[delta wye]" in cmd and "kvas=[400.0 400.0]" in cmd
    assert "taps=[1.025 1.0]" in cmd and "%rs=[0.6625 0.6625]" in cmd
    xhl = float(cmd.split("xhl=")[1].split()[0])
    assert abs(xhl - (4.0 ** 2 - 1.325 ** 2) ** 0.5) < 1e-12
    p0 = 0.95 / 400 * 100
    assert abs(float(cmd.split("%noloadloss=")[1].split()[0]) - p0) < 1e-12
    assert abs(float(cmd.split("%imag=")[1].split()[0]) - (0.5 ** 2 - p0 ** 2) ** 0.5) < 1e-12
    with pytest.raises(ValueError, match="Dyn"):
        m.trafo_command({**tr, "vector_group": "Yy0"})


@needs_opendss
def test_tiny_grid_matches_opendss_within_the_stated_tolerances(tiny_processed, tmp_path):
    m = _load()
    with tiny_grid(tiny_processed):
        case_path = m.pf_crossval().export(tmp_path, seed=7, others=8)
        import json

        case = json.loads(case_path.read_text(encoding="utf-8"))
        out = m.solve_case(case)
        cmp = m.compare(case, out)
        bare = m.without_magnetising(case)

    assert out["ierr"] == [0] * 10, out["ierr"]
    assert cmp["all_hours_solved"] and cmp["meets_targets"] == {"v_pu": True, "trafo_loading_pct": True}
    k = cmp["kapacitet"]
    assert k["v_pu"]["n"] == 10 * len(case["network"]["buses"])
    assert k["v_pu"]["max"] <= V_TOL_PU, k
    assert k["trafo_loading_pct"]["max"] <= LOADING_TOL_PCT, k
    assert k["line_loading_pct"]["max"] <= LOADING_TOL_PCT, k
    # the residual is the no-load branch: iron-loss current (pu) through half the leakage resistance
    tr = case["network"]["trafo"]
    expected = tr["pfe_kw"] / (tr["sn_mva"] * 1000) * tr["vkr_percent"] / 100 / 2
    assert 0.7 * expected < k["v_pu"]["mean"] < 1.3 * expected, (k["v_pu"], expected)
    # balanced flow: the three phases agree, so the per-phase mean is the positive sequence
    assert cmp["max_phase_spread_pu"] < 1e-8
    # the hours span the range: the peaks are not a flat network
    assert max(case["kapacitet"]["trafo_loading_pct"]) > 50
    assert bare["v_pu"]["max"] <= V_TOL_NO_MAGNETISING_PU, bare
    assert bare["trafo_loading_pct"]["max"] <= LOADING_TOL_NO_MAGNETISING_PCT, bare
    assert bare["line_loading_pct"]["max"] <= LOADING_TOL_NO_MAGNETISING_PCT, bare
