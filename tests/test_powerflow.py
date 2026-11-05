"""The batched Z-bus solver must reproduce pandapower's Newton-Raphson result exactly.

A small synthetic LV feeder is built here, so this test needs no pipeline output.
"""

from __future__ import annotations

import numpy as np
import pandapower as pp
import pytest
from pandapower.powerflow import LoadflowNotConverged

from kapacitet.engine.powerflow import PowerFlowModel, PowerFlowNotConverged

N_CUST = 6


def build_feeder() -> tuple[pp.pandapowerNet, np.ndarray]:
    """10/0.4 kV substation, one main cable and a service cable per customer."""
    net = pp.create_empty_network()
    mv = pp.create_bus(net, vn_kv=10.0, name="mv")
    lv = pp.create_bus(net, vn_kv=0.4, name="lv")
    pp.create_ext_grid(net, mv, vm_pu=1.0)
    pp.create_transformer(net, mv, lv, std_type="0.4 MVA 10/0.4 kV")
    cust_bus = []
    prev = lv
    for i in range(N_CUST):
        node = pp.create_bus(net, vn_kv=0.4, name=f"node{i}")
        pp.create_line(net, prev, node, length_km=0.045, std_type="NAYY 4x150 SE")
        house = pp.create_bus(net, vn_kv=0.4, name=f"house{i}")
        pp.create_line(net, node, house, length_km=0.025, std_type="NAYY 4x50 SE")
        pp.create_load(net, house, p_mw=0.0, q_mvar=0.0)
        pp.create_sgen(net, house, p_mw=0.0, q_mvar=0.0)
        cust_bus.append(house)
        prev = node
    return net, np.array(cust_bus)


@pytest.fixture(scope="module")
def feeder():
    net, cust_bus = build_feeder()
    return PowerFlowModel(net, cust_bus), net, cust_bus


@pytest.mark.parametrize("vm_slack", [0.96, 1.0, 1.04])
def test_matches_pandapower(feeder, vm_slack):
    model, net, cust_bus = feeder
    rng = np.random.default_rng(7)
    H = 8
    p_kw = rng.uniform(-12.0, 14.0, size=(N_CUST, H))          # positive = injection
    q_kvar = rng.uniform(-3.0, 3.0, size=(N_CUST, H))
    res = model.solve(p_kw, q_kvar, vm_slack)

    for h in range(H):
        net.ext_grid.loc[0, "vm_pu"] = vm_slack
        for i, bus in enumerate(cust_bus):
            load = net.load.index[net.load.bus == bus][0]
            sgen = net.sgen.index[net.sgen.bus == bus][0]
            net.load.loc[load, "p_mw"] = max(-p_kw[i, h], 0.0) / 1000.0
            net.load.loc[load, "q_mvar"] = max(-q_kvar[i, h], 0.0) / 1000.0
            net.sgen.loc[sgen, "p_mw"] = max(p_kw[i, h], 0.0) / 1000.0
            net.sgen.loc[sgen, "q_mvar"] = max(q_kvar[i, h], 0.0) / 1000.0
        pp.runpp(net, calculate_voltage_angles=False, init="flat", tolerance_mva=1e-10)
        assert np.abs(np.abs(res.v[:, h]) - net.res_bus["vm_pu"].to_numpy()).max() < 1e-8
        assert np.abs(res.line_loading[:, h] - net.res_line["loading_percent"].to_numpy()).max() < 1e-6
        assert abs(res.trafo_loading[h] - float(net.res_trafo["loading_percent"].iloc[0])) < 1e-6
        assert abs(res.trafo_p_mw[h] - float(net.res_trafo["p_hv_mw"].iloc[0])) < 1e-9


def test_converges_and_is_chunk_invariant(feeder):
    model, _, _ = feeder
    rng = np.random.default_rng(11)
    p_kw = rng.uniform(-10.0, 12.0, size=(N_CUST, 50))
    q_kvar = np.zeros_like(p_kw)
    whole = model.solve(p_kw, q_kvar, 1.0)
    chunked = model.solve_chunked(p_kw, q_kvar, 1.0, chunk=7)
    assert whole.max_mismatch < 1e-10
    # each chunk stops on its own worst column, so results agree to the iteration tolerance
    assert np.abs(np.abs(whole.v) - np.abs(chunked.v)).max() < 1e-11
    assert np.abs(whole.trafo_loading - chunked.trafo_loading).max() < 1e-8


def _with_an_overloaded_hour(H: int, bad: int) -> np.ndarray:
    """Ordinary hours, and in hour `bad` 400 kW of load per customer: 2.4 MW on a 0.4 MVA
    transformer, a state with no power-flow solution."""
    rng = np.random.default_rng(5)
    p = rng.uniform(-10.0, 12.0, size=(N_CUST, H))
    p[:, bad] = -400.0
    return p


def test_a_column_that_does_not_converge_is_flagged_alone(feeder):
    """The fixed point used to hand back its last iterate after max_iter as if it were an answer."""
    model, net, cust_bus = feeder
    H, bad = 20, 9
    p = _with_an_overloaded_hour(H, bad)
    q = np.zeros_like(p)
    whole = model.solve(p, q, 1.0)
    assert not whole.converged[bad] and whole.mismatch[bad] > 1e-3
    assert whole.converged[np.arange(H) != bad].all()
    assert whole.max_mismatch == whole.mismatch.max()

    # pandapower's Newton-Raphson gives up on the same state
    net.ext_grid.loc[0, "vm_pu"] = 1.0
    net.sgen["p_mw"] = 0.0
    net.sgen["q_mvar"] = 0.0
    net.load["q_mvar"] = 0.0
    for i, bus in enumerate(cust_bus):
        net.load.loc[net.load.index[net.load.bus == bus][0], "p_mw"] = -p[i, bad] / 1000.0
    with pytest.raises(LoadflowNotConverged):
        pp.runpp(net, calculate_voltage_angles=False, init="flat", tolerance_mva=1e-10)

    # the flags do not depend on how the hours are cut into chunks, and strict names the hour
    chunked = model.solve_chunked(p, q, 1.0, chunk=7)
    assert np.array_equal(chunked.converged, whole.converged)
    with pytest.raises(PowerFlowNotConverged) as err:
        model.solve_chunked(p, q, 1.0, chunk=7, strict=True)
    assert err.value.columns.tolist() == [bad]
    with pytest.raises(PowerFlowNotConverged):
        model.solve(p, q, 1.0, strict=True)
    model.solve(np.delete(p, bad, axis=1), np.delete(q, bad, axis=1), 1.0, strict=True)


@pytest.mark.parametrize("nan_at", [2, 9])
def test_a_non_finite_column_shows_in_the_mismatch_whichever_chunk_it_is_in(feeder, nan_at):
    """Python's max(1e-11, nan) is 1e-11 and max(nan, 1e-11) is nan: the aggregate must not depend on that."""
    model, _, _ = feeder
    rng = np.random.default_rng(13)
    p = rng.uniform(-10.0, 12.0, size=(N_CUST, 14))
    p[0, nan_at] = np.nan
    res = model.solve_chunked(p, np.zeros_like(p), 1.0, chunk=7)
    assert np.isnan(res.max_mismatch)
    assert res.converged.tolist() == [h != nan_at for h in range(14)]


def test_voltage_sensitivity_sign(feeder):
    """dV/dP at the injecting customer must be positive and larger than at the substation end."""
    model, _, _ = feeder
    assert (np.diag(model.dv_dp[model.customer_row]) > 0).all()
    # the far customer sees a bigger rise from its own injection than the near one
    own = np.diag(model.dv_dp[model.customer_row])
    assert own[-1] > own[0]
