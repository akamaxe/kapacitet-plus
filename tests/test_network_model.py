"""The network model and the screens that the engine relies on.

On the tiny grid of tests/fixtures/grid_tiny everywhere, and on the reference network too when
it is built (the `grid_model` fixture in conftest.py).
"""

from __future__ import annotations

import numpy as np
import pandapower as pp
import pandapower.topology as top
import pytest
from conftest import on_tiny


def test_network_is_radial_and_fully_connected(grid_model):
    net = grid_model.net
    graph = top.create_nxgraph(net, respect_switches=False)
    assert top.connected_components(graph, notravbuses=set()), "graph must have components"
    assert len(list(top.unsupplied_buses(net))) == 0
    # radial: one path from the substation to every bus (edges = buses - 1 in a tree)
    assert len(net.line) + len(net.trafo) == len(net.bus) - 1
    assert len(net.ext_grid) == 1 and len(net.trafo) == 1
    # every customer sits on its own bus, downstream of at least one cable
    assert grid_model.phi.sum(axis=0).min() > 0
    assert len(set(grid_model.cust["bus"])) == grid_model.C


def test_base_case_is_within_limits(grid_model):
    """With no PV and only the modelled loads at zero, voltages sit at the no-load profile."""
    zero = np.zeros((grid_model.C, 1))
    res = grid_model.solve(zero, zero)
    v = np.abs(res.v[grid_model.check_rows])[:, 0]
    assert np.abs(v - grid_model.v_noload).max() < 1e-9
    assert (v > grid_model.v_min).all() and (v < grid_model.v_max).all()


def test_zbus_matches_pandapower(grid_model):
    rng = np.random.default_rng(4)
    p = rng.uniform(-6.0, 9.0, size=(grid_model.C, 3))
    q = rng.uniform(-1.0, 1.0, size=(grid_model.C, 3))
    res = grid_model.solve(p, q)
    net = grid_model.net
    cust_bus = grid_model.cust["bus"].to_numpy()
    for h in range(p.shape[1]):
        net.load["p_mw"] = 0.0
        net.load["q_mvar"] = 0.0
        net.sgen["p_mw"] = 0.0
        net.sgen["q_mvar"] = 0.0
        for i, bus in enumerate(cust_bus):
            sg = net.sgen.index[net.sgen.bus == bus]
            ld = net.load.index[net.load.bus == bus]
            assert len(sg) == 1 and len(ld) == 1
            net.sgen.loc[sg[0], "p_mw"] = p[i, h] / 1000.0
            net.sgen.loc[sg[0], "q_mvar"] = q[i, h] / 1000.0
        net.ext_grid.loc[net.ext_grid.index[0], "vm_pu"] = grid_model.vm_slack
        pp.runpp(net, calculate_voltage_angles=False, init="flat", tolerance_mva=1e-10)
        assert np.abs(np.abs(res.v[:, h]) - net.res_bus["vm_pu"].to_numpy()).max() < 1e-8
        assert np.abs(res.line_loading[:, h] - net.res_line["loading_percent"].to_numpy()).max() < 1e-6


def _per_house_kw(model) -> float:
    """The transformer's rating shared out per house: the draws below scale with it, so they
    stress a grid of any size alike (on the reference network, 400 kVA over 120 houses, they are
    the 2-11 kW and 2-12 kW the screens were first tested with)."""
    return model.trafo_s_max_kw / model.C


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_export_screen_never_misses_a_violation(grid_model, seed, request):
    """The linear screen is conservative: every violating hour must be in its candidate set."""
    rng = np.random.default_rng(seed)
    H, per = 400, _per_house_kw(grid_model)
    p = rng.uniform(0.0, 1.0, size=(grid_model.C, H)) * rng.uniform(0.6, 3.3, size=(grid_model.C, 1)) * per
    q = -0.05 * p
    cand = set(grid_model.screen_export(p, q).tolist())
    found = grid_model.violations(grid_model.solve(p, q), p)
    viol = found.export_side
    assert viol.any(), "the draw must produce violating hours, or the test checks nothing"
    if on_tiny(request):
        assert found.over_v.any() and found.trafo_rev.any(), "on the tiny grid both voltage and transformer"
    assert set(np.flatnonzero(viol).tolist()) <= cand


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_import_screen_never_misses_a_violation(grid_model, seed, request):
    rng = np.random.default_rng(100 + seed)
    H, per = 400, _per_house_kw(grid_model)
    p = -rng.uniform(0.0, 1.0, size=(grid_model.C, H)) * rng.uniform(0.6, 3.6, size=(grid_model.C, 1)) * per
    q = 0.1 * p
    cand = set(grid_model.screen_import(p, q).tolist())
    found = grid_model.violations(grid_model.solve(p, q), p)
    viol = found.import_side
    assert viol.any(), "the draw must produce violating hours, or the test checks nothing"
    if on_tiny(request):
        assert found.under_v.any() and (found.trafo & ~found.trafo_rev).any(), "on the tiny grid both voltage and transformer"
    assert set(np.flatnonzero(viol).tolist()) <= cand


def test_sensitivities_are_conservative_for_export(grid_model):
    """The clipped linear estimate must not underestimate the voltage rise it screens with."""
    rng = np.random.default_rng(9)
    p = rng.uniform(0.0, 8.0, size=(grid_model.C, 60))
    v_nl = np.abs(grid_model.solve(p, np.zeros_like(p)).v[grid_model.check_rows])
    v_lin = grid_model.v_noload[:, None] + grid_model.dv_dp @ p
    assert (v_lin.max(axis=0) >= v_nl.max(axis=0) - 1e-3).all()
