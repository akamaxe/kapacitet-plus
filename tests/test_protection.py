"""Protection (engine/protection.py) and the single-phase device row of the phase check.

The rules run anywhere; a tiny network built here checks the fault currents and the device row
without the pipeline's data; the last tests read the computed results.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from conftest import HAS_GRID, needs_grid

RATINGS = [25, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630]
GATES = [110, 190, 250, 320, 425, 580, 715, 950, 1250, 1650, 2200, 2840, 3800, 5100]


def test_feeder_fuse_respects_the_1_45_rule():
    from kapacitet.engine.protection import feeder_fuse

    # NAYY 4x150 at 270 A: 250 A would need 1.6 x 250 = 400 A > 1.45 x 270 = 391.5 A
    assert feeder_fuse(270.0, RATINGS, 1.6, 1.45) == 200
    assert feeder_fuse(142.0, RATINGS, 1.6, 1.45) == 125
    assert feeder_fuse(10.0, RATINGS, 1.6, 1.45) is None


def test_a_fuse_the_table_does_not_list_takes_the_next_larger_gate():
    from kapacitet.engine.protection import gate_5s

    assert gate_5s(200, RATINGS, GATES) == (1250.0, False)
    assert gate_5s(16, RATINGS, GATES) == (110.0, True)        # melts before a 25 A fuse
    assert gate_5s(35, RATINGS, GATES) == (190.0, True)        # melts before a 40 A fuse
    with pytest.raises(ValueError):
        gate_5s(800, RATINGS, GATES)


def _tiny_net(length_km: float = 0.2, sections: int = 3):
    """10 kV source, a 0.4 MVA Dyn transformer, one feeder of NAYY 4x150 in equal sections."""
    import kapacitet  # noqa: F401  (pandapower's numpy shims)
    import pandapower as pp
    from kapacitet.grid.builder import add_zero_sequence

    net = pp.create_empty_network(sn_mva=1.0)
    mv = pp.create_bus(net, vn_kv=10.0)
    pp.create_ext_grid(net, mv, vm_pu=1.0, s_sc_max_mva=150.0, rx_max=0.1, s_sc_min_mva=120.0, rx_min=0.1)
    lv = pp.create_bus(net, vn_kv=0.4)
    pp.create_transformer(net, mv, lv, std_type="0.4 MVA 10/0.4 kV")
    buses, prev = [], lv
    for i in range(sections):
        b = pp.create_bus(net, vn_kv=0.4)
        pp.create_line(net, prev, b, length_km=length_km / sections, std_type="NAYY 4x150 SE")
        buses.append(b)
        prev = b
    add_zero_sequence(net)
    lines = pd.DataFrame({"line": net.line.index, "kind": "main", "feeder": 0,
                          "from_bus": net.line.from_bus.to_numpy(), "to_bus": net.line.to_bus.to_numpy()})
    cust = pd.DataFrame({"cid": [0, 1], "bus": [buses[0], buses[-1]], "fuse_a": [20, 25]})
    return net, lines, cust, buses


def test_fault_current_falls_along_the_feeder():
    from kapacitet.engine.protection import fault_currents

    net, _, _, buses = _tiny_net()
    ik = fault_currents(net)
    along = [float(ik[b]) for b in buses]
    assert all(a > b for a, b in zip(along, along[1:]))
    assert 100 < along[-1] < 20_000


def test_a_long_feeder_fails_and_a_short_one_passes():
    from kapacitet.engine.protection import assess

    short = assess(*_tiny_net(length_km=0.1)[:3])
    long_ = assess(*_tiny_net(length_km=1.5)[:3])
    assert short["feeders"][0]["ok"] and short["feeders"][0]["fuse_a"] == 200
    assert not long_["feeders"][0]["ok"]
    assert long_["feeders"][0]["ik1_min_a"] < short["feeders"][0]["ik1_min_a"]
    assert [h["bound"] for h in short["houses"]] == [True, False]   # 20 A bounded by 25 A; 25 A in the table
    assert short["summary"]["houses"] == 2


def test_the_device_row_equals_a_customer_on_that_phase():
    import kapacitet  # noqa: F401
    from kapacitet.engine.powerflow3ph import PowerFlow3Ph
    from kapacitet.engine.request import solve_with_device

    net, _, _, buses = _tiny_net()
    pf = PowerFlow3Ph(net, np.array([buses[0], buses[-1]]))
    model = SimpleNamespace(pf=pf, phase=np.array([-1, -1]), vm_slack=1.0)
    p = np.array([-2.0, -1.0])
    q = np.array([-0.3, -0.2])
    via_row = solve_with_device(model, p, q, 1, -3.7, 2)

    explicit = PowerFlow3Ph(net, np.array([buses[0], buses[-1], buses[-1]]))
    direct = explicit.solve(np.array([[-2.0], [-1.0], [-3.7]]), np.array([[-0.3], [-0.2], [0.0]]),
                            np.array([-1, -1, 2]), 1.0)
    assert np.allclose(via_row.vm, direct.vm, atol=1e-10)
    assert via_row.unbalance_pct.shape[0] == len(net.bus)


@needs_grid
def test_protection_results_cover_every_feeder_and_house():
    from kapacitet.paths import processed

    path = processed("results", "protection.json")
    if not path.exists():
        pytest.skip("run: python -m kapacitet compute --stage extra --only protection")
    out = json.loads(path.read_text(encoding="utf-8"))
    from kapacitet.grid.builder import load_tables

    _, lines, cust = load_tables()
    assert len(out["feeders"]) == lines.loc[lines["kind"] == "main", "feeder"].nunique()
    assert {h["cid"] for h in out["houses"]} == set(cust["cid"])
    for f in out["feeders"]:
        assert f["ok"] == (f["gate_5s_a"] is not None and f["ik1_min_a"] >= f["gate_5s_a"])
        assert 1.6 * f["fuse_a"] <= 1.45 * f["i_z_a"] + 1e-6


@pytest.mark.skipif(not HAS_GRID, reason="no built network")
def test_a_single_phase_charger_reports_its_best_and_worst_phase():
    from kapacitet.engine import request as rq
    from kapacitet.engine.netmodel import get_model
    from kapacitet.engine.run import background_masks
    from kapacitet.paths import processed

    if not processed("request", "doe.json").exists():
        pytest.skip("no request baselines")
    m = get_model()
    hp, ev = background_masks(m.C)
    out = rq.evaluate(m, rq.Request(cid=m.C - 1, ev_kw=3.7, baseline="static"), hp, ev)
    d = out["phases"]["devices"][0]
    assert d["device"] == "ev" and len(d["by_phase"]) == 3
    assert {r["phase"] for r in d["by_phase"]} == {"L1", "L2", "L3"}
    assert d["best"] in {"L1", "L2", "L3"} and d["worst"] in {"L1", "L2", "L3"}
    assert out["fuse"]["single_phase_kw"] == pytest.approx(3.7)
    three = rq.evaluate(m, rq.Request(cid=m.C - 1, ev_kw=11.0, baseline="static"), hp, ev)
    assert "phases" not in three
