"""The built network and the screens that the engine relies on (needs `kapacitet build`)."""

from __future__ import annotations

import numpy as np
import pandapower as pp
import pandapower.topology as top
import pytest

from conftest import needs_grid

pytestmark = needs_grid


def test_network_is_radial_and_fully_connected(model):
    net = model.net
    graph = top.create_nxgraph(net, respect_switches=False)
    assert top.connected_components(graph, notravbuses=set()), "graph must have components"
    assert len(list(top.unsupplied_buses(net))) == 0
    # radial: one path from the substation to every bus (edges = buses - 1 in a tree)
    assert len(net.line) + len(net.trafo) == len(net.bus) - 1
    assert len(net.ext_grid) == 1 and len(net.trafo) == 1
    # every customer sits on its own bus, downstream of at least one cable
    assert model.phi.sum(axis=0).min() > 0
    assert len(set(model.cust["bus"])) == model.C


def test_base_case_is_within_limits(model):
    """With no PV and only the modelled loads at zero, voltages sit at the no-load profile."""
    zero = np.zeros((model.C, 1))
    res = model.solve(zero, zero)
    v = np.abs(res.v[model.check_rows])[:, 0]
    assert np.abs(v - model.v_noload).max() < 1e-9
    assert (v > model.v_min).all() and (v < model.v_max).all()


def test_zbus_matches_pandapower_on_the_real_network(model):
    rng = np.random.default_rng(4)
    p = rng.uniform(-6.0, 9.0, size=(model.C, 3))
    q = rng.uniform(-1.0, 1.0, size=(model.C, 3))
    res = model.solve(p, q)
    net = model.net
    cust_bus = model.cust["bus"].to_numpy()
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
        net.ext_grid.loc[net.ext_grid.index[0], "vm_pu"] = model.vm_slack
        pp.runpp(net, calculate_voltage_angles=False, init="flat", tolerance_mva=1e-10)
        assert np.abs(np.abs(res.v[:, h]) - net.res_bus["vm_pu"].to_numpy()).max() < 1e-8
        assert np.abs(res.line_loading[:, h] - net.res_line["loading_percent"].to_numpy()).max() < 1e-6


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_export_screen_never_misses_a_violation(model, seed):
    """The linear screen is conservative: every violating hour must be in its candidate set."""
    rng = np.random.default_rng(seed)
    H = 400
    p = rng.uniform(0.0, 1.0, size=(model.C, H)) * rng.uniform(2.0, 11.0, size=(model.C, 1))
    q = -0.05 * p
    cand = set(model.screen_export(p, q).tolist())
    viol = model.violations(model.solve(p, q), p).export_side
    assert set(np.flatnonzero(viol).tolist()) <= cand


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_import_screen_never_misses_a_violation(model, seed):
    rng = np.random.default_rng(100 + seed)
    H = 400
    p = -rng.uniform(0.0, 1.0, size=(model.C, H)) * rng.uniform(2.0, 12.0, size=(model.C, 1))
    q = 0.1 * p
    cand = set(model.screen_import(p, q).tolist())
    viol = model.violations(model.solve(p, q), p).import_side
    assert set(np.flatnonzero(viol).tolist()) <= cand


def test_sensitivities_are_conservative_for_export(model):
    """The clipped linear estimate must not underestimate the voltage rise it screens with."""
    rng = np.random.default_rng(9)
    p = rng.uniform(0.0, 8.0, size=(model.C, 60))
    v_nl = np.abs(model.solve(p, np.zeros_like(p)).v[model.check_rows])
    v_lin = model.v_noload[:, None] + model.dv_dp @ p
    assert (v_lin.max(axis=0) >= v_nl.max(axis=0) - 1e-3).all()
