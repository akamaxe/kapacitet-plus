"""The phase-frame Z-bus solver must reproduce pandapower's own three-phase result.

The reference is the IEEE European Low Voltage Test Feeder, which ships with pandapower:
906 buses, 55 single-phase loads and published zero-sequence data, so it is genuinely
unbalanced and nobody has to invent an input. A small synthetic feeder covers the case of
a single-phase injection next to three-phase ones, which is what rooftop PV looks like.
"""

from __future__ import annotations

import numpy as np
import pandapower as pp
import pandapower.networks as pn
import pytest
from conftest import needs_grid

from kapacitet.engine.powerflow3ph import PHASES, PowerFlow3Ph, phase_frame, sequence_ybus

N_CUST = 4


def build_feeder() -> tuple[pp.pandapowerNet, np.ndarray]:
    """10/0.4 kV Dyn5 substation, one main cable and a service cable per customer."""
    net = pp.create_empty_network()
    mv = pp.create_bus(net, vn_kv=10.0, name="mv")
    lv = pp.create_bus(net, vn_kv=0.4, name="lv")
    pp.create_ext_grid(net, mv, vm_pu=1.0, s_sc_max_mva=50.0, rx_max=0.1, x0x_max=1.0, r0x0_max=0.1)
    pp.create_transformer(net, mv, lv, std_type="0.4 MVA 10/0.4 kV")
    # pandapower's three-phase flow accepts the bare vector group, without the clock number,
    # and needs the zero-sequence data that the standard type does not carry.
    net.trafo["vector_group"] = "Dyn"
    for col, val in (("vk0_percent", 4.0), ("vkr0_percent", 1.325), ("mag0_percent", 100.0),
                     ("mag0_rx", 0.0), ("si0_hv_partial", 0.9)):
        net.trafo[col] = val
    cust_bus, prev = [], lv
    for i in range(N_CUST):
        node = pp.create_bus(net, vn_kv=0.4, name=f"node{i}")
        pp.create_line_from_parameters(
            net, prev, node, length_km=0.045, r_ohm_per_km=0.208, x_ohm_per_km=0.080,
            c_nf_per_km=261.0, max_i_ka=0.27, r0_ohm_per_km=0.832, x0_ohm_per_km=0.320,
            c0_nf_per_km=261.0)
        house = pp.create_bus(net, vn_kv=0.4, name=f"house{i}")
        pp.create_line_from_parameters(
            net, node, house, length_km=0.025, r_ohm_per_km=0.642, x_ohm_per_km=0.083,
            c_nf_per_km=210.0, max_i_ka=0.142, r0_ohm_per_km=2.568, x0_ohm_per_km=0.332,
            c0_nf_per_km=210.0)
        cust_bus.append(house)
        prev = node
    return net, np.array(cust_bus)


def set_asymmetric(net: pp.pandapowerNet, cust_bus, p_kw, q_kvar, phase_idx, h: int) -> None:
    """Rewrite the asymmetric loads so pandapower sees the same injections as the solver."""
    net.asymmetric_load = net.asymmetric_load.iloc[0:0]
    for i, bus in enumerate(cust_bus):
        pa = pb = pc = qa = qb = qc = 0.0
        if phase_idx[i] < 0:
            pa = pb = pc = -p_kw[i, h] / 3000.0
            qa = qb = qc = -q_kvar[i, h] / 3000.0
        else:
            p, q = -p_kw[i, h] / 1000.0, -q_kvar[i, h] / 1000.0
            pa, pb, pc = [(p if k == phase_idx[i] else 0.0) for k in range(3)]
            qa, qb, qc = [(q if k == phase_idx[i] else 0.0) for k in range(3)]
        pp.create_asymmetric_load(net, bus, p_a_mw=pa, p_b_mw=pb, p_c_mw=pc,
                                  q_a_mvar=qa, q_b_mvar=qb, q_c_mvar=qc)


def test_phase_frame_is_the_inverse_transform():
    """A balanced network (y0 == y1 == y2) must come out diagonal in the phase frame."""
    y = np.array([[2.0 + 1j, -1.0], [-1.0, 1.5 - 0.5j]])
    out = phase_frame(y, y, y)
    assert np.allclose(out, np.kron(y, np.eye(3)))


def test_sequence_matrices_are_finite():
    net, _ = build_feeder()
    pp.runpp_3ph(net)
    s0, s1, s2, _ = sequence_ybus(net)
    y0, y1, y2 = s0["y"], s1["y"], s2["y"]
    assert y0.shape == y1.shape == y2.shape
    assert np.isfinite(y0).all() and np.isfinite(y1).all()
    assert np.allclose(y1, y2)                     # passive network
    assert not np.allclose(y0, y1)                 # the Dyn transformer blocks zero sequence
    # the branch matrices are padded so all three sequences index the same branches
    assert s0["yf"].shape == s1["yf"].shape == s2["yf"].shape


@pytest.mark.parametrize("vm_slack", [1.0, 1.03])
def test_matches_pandapower_synthetic(vm_slack):
    """Exact agreement when the MV source is stiff, which is what this solver assumes."""
    net, cust_bus = build_feeder()
    net.ext_grid["s_sc_max_mva"] = 1e7
    model = PowerFlow3Ph(net, cust_bus)
    rng = np.random.default_rng(3)
    H = 4
    p_kw = rng.uniform(-6.0, 8.0, size=(N_CUST, H))
    q_kvar = rng.uniform(-1.5, 1.5, size=(N_CUST, H))
    phase_idx = np.array([0, 1, 2, -1])            # three single-phase, one three-phase
    res = model.solve(p_kw, q_kvar, phase_idx, vm_slack)

    for h in range(H):
        net.ext_grid.loc[0, "vm_pu"] = vm_slack
        set_asymmetric(net, cust_bus, p_kw, q_kvar, phase_idx, h)
        pp.runpp_3ph(net)
        ref = net.res_bus_3ph[["vm_a_pu", "vm_b_pu", "vm_c_pu"]].to_numpy().ravel()
        assert np.abs(res.vm[:, h] - ref).max() < 1e-8


def test_stiff_source_error_is_bounded():
    """What treating the MV source as stiff costs, measured rather than assumed.

    pandapower puts the ext_grid short-circuit impedance in the negative and zero sequence
    networks, so the MV busbar itself becomes slightly unbalanced. This solver holds it
    balanced. At the 50 MVA short-circuit power of the test site the difference stays far
    below the planning margin, and it shrinks as the source gets stiffer.
    """
    rng = np.random.default_rng(3)
    p_kw = rng.uniform(-6.0, 8.0, size=(N_CUST, 1))
    q_kvar = rng.uniform(-1.5, 1.5, size=(N_CUST, 1))
    phase_idx = np.array([0, 1, 2, -1])
    errors = {}
    for s_sc in (50.0, 5e3):
        net, cust_bus = build_feeder()
        net.ext_grid["s_sc_max_mva"] = s_sc
        res = PowerFlow3Ph(net, cust_bus).solve(p_kw, q_kvar, phase_idx, 1.0)
        set_asymmetric(net, cust_bus, p_kw, q_kvar, phase_idx, 0)
        pp.runpp_3ph(net)
        ref = net.res_bus_3ph[["vm_a_pu", "vm_b_pu", "vm_c_pu"]].to_numpy().ravel()
        errors[s_sc] = np.abs(res.vm[:, 0] - ref).max()

    assert errors[50.0] < 5e-4, "stiff-source error grew beyond its documented bound"
    assert errors[5e3] < errors[50.0] / 10.0, "error must shrink with a stiffer source"


def test_matches_pandapower_ieee_european_lv():
    """The published unbalanced benchmark, solved in one batch."""
    net = pn.ieee_european_lv_asymmetric("on_peak_566")
    loads = net.asymmetric_load.copy()
    cust_bus = loads["bus"].to_numpy()
    pp.runpp_3ph(net)
    ref = net.res_bus_3ph[["vm_a_pu", "vm_b_pu", "vm_c_pu"]].to_numpy().ravel()

    model = PowerFlow3Ph(net, cust_bus)
    # one column per phase the benchmark actually loads, as a negative injection
    p = np.zeros((len(loads), 3))
    q = np.zeros_like(p)
    for k, col in enumerate("abc"):
        p[:, k] = -loads[f"p_{col}_mw"].to_numpy() * 1000.0
        q[:, k] = -loads[f"q_{col}_mvar"].to_numpy() * 1000.0
    phase_idx = np.argmax(np.abs(p), axis=1)
    single = np.count_nonzero(np.abs(p) > 0, axis=1) == 1
    assert single.all(), "the benchmark is expected to be single-phase throughout"
    p_kw = p[np.arange(len(loads)), phase_idx][:, None]
    q_kvar = q[np.arange(len(loads)), phase_idx][:, None]

    vm = float(net.ext_grid.vm_pu.iloc[0])
    res = model.solve(p_kw, q_kvar, phase_idx, vm_slack=vm)
    # as shipped the benchmark has a finite source impedance, so the residual is the
    # stiff-source difference measured in test_stiff_source_error_is_bounded
    assert np.abs(res.vm[:, 0] - ref).max() < 1e-5

    # With a stiff source and a tight pandapower tolerance the two agree to 1.6e-6 pu, not
    # to solver tolerance. OPEN ITEM: the synthetic feeder matches to 9e-10 under the same
    # conditions and the dense inverse is accurate to 4e-11 (cond(Ynn) = 4.5e7), so this is
    # a modelling difference on this benchmark and not conditioning or convergence. It is
    # 1.6e-6 pu against a 1e-2 pu planning margin, so it is tracked, not ignored.
    net.ext_grid["s_sc_max_mva"] = 1e7
    pp.runpp_3ph(net, tolerance_mva=1e-10, max_iteration=200)
    ref_stiff = net.res_bus_3ph[["vm_a_pu", "vm_b_pu", "vm_c_pu"]].to_numpy().ravel()
    res_stiff = PowerFlow3Ph(net, cust_bus).solve(p_kw, q_kvar, phase_idx, vm_slack=vm)
    assert np.abs(res_stiff.vm[:, 0] - ref_stiff).max() < 2e-6


def test_unbalance_is_reported_and_zero_when_balanced():
    net, cust_bus = build_feeder()
    model = PowerFlow3Ph(net, cust_bus)
    p = np.full((N_CUST, 1), 4.0)
    res_bal = model.solve(p, None, np.full(N_CUST, -1), 1.0)
    assert res_bal.unbalance_pct.max() < 1e-9

    # 4 kW on phase a at every house gives 0.40 % unbalance at the far end, which is the
    # kind of number the balanced model cannot produce at all. EN 50160 allows 2 %.
    res_unbal = model.solve(p, None, np.zeros(N_CUST, dtype=int), 1.0)
    unb = res_unbal.unbalance_pct[:, 0]
    assert 0.2 < unb[-1] < 2.0                       # last house, the far end of the feeder
    assert unb[0] < 1e-9                             # the MV source stays balanced
    assert unb[-1] > unb[2], "the far end must be more unbalanced than the first node"
    assert res_unbal.phase("a").max() > res_unbal.phase("b").max()


# --------------------------------------------------------------- the real network, when built
@needs_grid
def test_real_network_solves_unbalanced_and_matches_pandapower():
    """The Smedslätten model carries usable zero-sequence data and agrees with pandapower.

    `add_zero_sequence` is applied on load, so this also pins the vector group and the
    sequence ratios that the unbalanced engine will depend on.
    """
    from kapacitet.grid.builder import load_net, load_tables

    net = load_net()
    cust = load_tables()[2]["bus"].to_numpy()
    assert (net.line["r0_ohm_per_km"] > net.line["r_ohm_per_km"]).all()
    assert set(net.trafo["vector_group"]) == {"Dyn"}

    net.ext_grid["s_sc_max_mva"] = 1e7          # the solver holds the source stiff
    model = PowerFlow3Ph(net, cust)
    rng = np.random.default_rng(5)
    p_kw = rng.uniform(-5.0, 7.0, size=(len(cust), 1))
    phase_idx = rng.integers(0, 3, size=len(cust))
    res = model.solve(p_kw, None, phase_idx, 1.0)

    net.asymmetric_load = net.asymmetric_load.iloc[0:0]
    for i, bus in enumerate(cust):
        pa, pb, pc = [(-p_kw[i, 0] / 1000.0 if k == phase_idx[i] else 0.0) for k in range(3)]
        pp.create_asymmetric_load(net, int(bus), p_a_mw=pa, p_b_mw=pb, p_c_mw=pc)
    pp.runpp_3ph(net, tolerance_mva=1e-10, max_iteration=200)
    ref = net.res_bus_3ph[["vm_a_pu", "vm_b_pu", "vm_c_pu"]].to_numpy().ravel()
    assert np.abs(res.vm[:, 0] - ref).max() < 2e-6

    # random phases on a real feeder produce unbalance the balanced model cannot see
    assert res.unbalance_pct.max() > 0.05


@needs_grid
def test_branch_flows_match_pandapower():
    """Lines, neutral current and transformer power against pandapower on the real feeder.

    The transformer current deliberately does not match, and the test shows why: pandapower's
    res_trafo_3ph adds the magnitudes of the feeder currents, while Kirchhoff adds the
    phasors. Reproducing their number from our own currents is what proves the difference is
    a convention and not an error.
    """
    from kapacitet.grid.builder import load_net, load_tables

    net = load_net()
    net.ext_grid["s_sc_max_mva"] = 1e7
    cust = load_tables()[2]["bus"].to_numpy()
    model = PowerFlow3Ph(net, cust)
    rng = np.random.default_rng(4)
    p_kw = rng.uniform(-5.0, 7.0, size=(len(cust), 1))
    phase_idx = rng.integers(0, 3, size=len(cust))
    res = model.solve(p_kw, None, phase_idx, 1.0)

    net.asymmetric_load = net.asymmetric_load.iloc[0:0]
    for i, bus in enumerate(cust):
        pa, pb, pc = [(-p_kw[i, 0] / 1000.0 if k == phase_idx[i] else 0.0) for k in range(3)]
        pp.create_asymmetric_load(net, int(bus), p_a_mw=pa, p_b_mw=pb, p_c_mw=pc)
    pp.runpp_3ph(net, tolerance_mva=1e-10, max_iteration=200)

    ref_loading = net.res_line_3ph["loading_percent"].to_numpy()
    assert np.abs(res.line_loading[:, 0] - ref_loading).max() < 1e-3
    ref_neutral = net.res_line_3ph["i_n_from_ka"].to_numpy()
    assert np.abs(res.i_neutral_ka[:, 0] - ref_neutral).max() < 1e-5
    ref_p = -sum(float(net.res_trafo_3ph[f"p_{c}_lv_mw"].iloc[0]) for c in "abc")
    assert abs(res.trafo_p_mw[0] - ref_p) < 1e-3

    # Kirchhoff gives less than pandapower, and pandapower's value is reproducible as the
    # scalar sum of our own feeder magnitudes, to the last digit.
    ref_trafo = float(net.res_trafo_3ph["loading_percent"].iloc[0])
    assert res.trafo_loading[0] < ref_trafo
    v_all = np.empty((3 * model.n_bus, 1), dtype=complex)
    v_all[(3 * model.bus_ppc[:, None] + np.arange(3)).ravel()] = res.v
    rows = (3 * model.lv_from[:, None] + np.arange(3)).ravel()
    per_feeder = (model.Yf @ v_all)[rows].reshape(-1, 3, 1)
    i_base = model.i_base_to[model.trafo_row]
    scalar_sum = np.abs(per_feeder).sum(axis=0)[:, 0] * i_base
    assert np.isclose(100.0 * scalar_sum.max() / model.trafo_i_lv_rated, ref_trafo, rtol=1e-4)


@needs_grid
def test_transformer_loading_agrees_when_nothing_is_unbalanced():
    """With balanced injections the Kirchhoff sum matches the balanced solver."""
    from kapacitet.engine.powerflow import PowerFlowModel
    from kapacitet.grid.builder import load_net, load_tables

    cust = load_tables()[2]["bus"].to_numpy()
    p_kw = np.full((len(cust), 1), -4.0)

    net = load_net()
    net.ext_grid["s_sc_max_mva"] = 1e7
    three = PowerFlow3Ph(net, cust).solve(p_kw, None, np.full(len(cust), -1), 1.0)
    balanced = PowerFlowModel(load_net(), cust).solve(p_kw, np.zeros_like(p_kw), 1.0)

    assert np.isclose(three.line_loading[:, 0].max(), balanced.line_loading[:, 0].max(), rtol=1e-6)
    assert np.isclose(three.trafo_loading[0], balanced.trafo_loading[0], rtol=3e-3)
