"""Phase identification from meter voltages (ingest/phase_id.py), on voltages the unbalanced flow
computes for the tiny grid with phases known in advance.

Each customer draws its own random household load for two weeks of quarter-hours: a single-phase
one on its phase, a three-phase one on each phase separately (a real house is never balanced), and
the MV voltage wanders. The meters read the magnitude of their phases' voltages with the noise and
the 0.1 V resolution of a real meter; a three-phase meter may be wired in another phase order.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import tiny_grid

from kapacitet.ingest import phase_id

PHASES = "abc"
QUARTERS = 96 * 14
NOISE_V = 0.2                      # the default noise: Gaussian, plus the meter's 0.1 V resolution
V_BASE = 400.0 / np.sqrt(3)
WIRING = ("abc", "abc", "abc", "abc", "cab", "bac")   # the three-phase meters' orders: most a, b, c


@pytest.fixture(scope="module")
def flow(tiny_processed):
    """(customer ids, true phase or '' per customer, (n_bus·3, T) voltage magnitudes in V, the rows
    of each customer's bus, the LV busbar's row), from the three-phase flow on the tiny grid."""
    from kapacitet.engine.powerflow3ph import PowerFlow3Ph
    from kapacitet.grid.builder import load_net, load_tables

    rng = np.random.default_rng(7)
    with tiny_grid(tiny_processed):
        net = load_net()
        _, _, cust = load_tables()
    C = len(cust)
    three = np.zeros(C, dtype=bool)
    three[rng.choice(C, size=len(WIRING), replace=False)] = True
    truth = np.where(three, "", np.array(list(PHASES))[rng.integers(0, 3, size=C)])
    # one load per single-phase customer and three per three-phase one
    bus, phase, owner = [], [], []
    for i in range(C):
        for p in (range(3) if three[i] else [PHASES.index(truth[i])]):
            bus.append(int(cust["bus"].iloc[i]))
            phase.append(p)
            owner.append(i)
    n = len(bus)
    base = rng.uniform(0.2, 0.6, size=(n, 1))
    walk = np.abs(np.cumsum(rng.normal(0, 0.08, size=(n, QUARTERS)), axis=1)) % 1.5
    spikes = (rng.random((n, QUARTERS)) < 0.04) * rng.uniform(1.0, 3.0, size=(n, QUARTERS))
    load_kw = base + walk + spikes
    load_kw[three[np.array(owner)]] /= 2                   # a house's load spread over its phases
    vm_slack = 1.0 + 0.004 * np.sin(np.arange(QUARTERS) / 30.0) + np.cumsum(rng.normal(0, 3e-4, QUARTERS))
    pf = PowerFlow3Ph(net, np.array(bus))
    res = pf.solve_chunked(-load_kw, None, np.array(phase), vm_slack, flows=False)
    assert res.converged.all()
    vm = np.abs(res.v) * V_BASE
    pos = pd.Series(np.arange(len(net.bus)), index=net.bus.index)
    cust_row = pos[cust["bus"].to_numpy()].to_numpy()
    lv_row = int(pos[int(net.trafo["lv_bus"].iloc[0])])
    ids = cust["osm_id"].astype(str).to_list()
    return ids, truth, vm, cust_row, lv_row


START = pd.Timestamp("2025-03-03T00:00:00Z")


def _times() -> pd.DatetimeIndex:
    return pd.date_range(START, periods=QUARTERS, freq="15min")


def meters(flow, noise: float = NOISE_V, seed: int = 11, flat: str | None = None) -> pd.DataFrame:
    """The 15-minute voltage store (voltage_15min.parquet) the meters would give."""
    ids, truth, vm, cust_row, _ = flow
    rng = np.random.default_rng(seed)
    t = _times()
    frames, k3 = [], 0
    for i, cid in enumerate(ids):
        rows = 3 * cust_row[i]

        def read(p: int) -> np.ndarray:
            return np.round(vm[rows + p] + rng.normal(0, noise, QUARTERS), 1)

        f = pd.DataFrame({"customer_id": cid, "start": t})
        if truth[i]:
            f["v_l1"], f["v_l2"], f["v_l3"] = read(PHASES.index(truth[i])), np.nan, np.nan
            if cid == flat:
                f["v_l1"] = 231.0
        else:
            order = WIRING[k3]
            k3 += 1
            for k, ph in enumerate(order):
                f[f"v_l{k + 1}"] = read(PHASES.index(ph))
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


def station(flow, noise: float = 0.05) -> pd.DataFrame:
    _, _, vm, _, lv = flow
    rng = np.random.default_rng(3)
    return pd.DataFrame({"start": _times(), **{f"v_l{k + 1}": np.round(vm[3 * lv + k] + rng.normal(0, noise, QUARTERS), 1)
                                                for k in range(3)}})


def share_right(flow, out: pd.DataFrame) -> float:
    ids, truth, *_ = flow
    want = pd.Series(truth, index=ids)
    single = out[out["service"] == "single"].set_index("customer_id")
    return float((single["phase"] == want[single.index]).mean())


def test_single_phase_meters_are_identified_against_the_station_meter(flow):
    out, report = phase_id.identify(meters(flow), station(flow), threshold=0.1)
    assert share_right(flow, out) >= 0.95, out[["customer_id", "phase", "confidence", "corr_a", "corr_b", "corr_c"]]
    assert set(out.loc[out["service"] == "single", "reference"]) == {"station"}
    three = out[out["service"] == "three"]
    assert sorted(three["order"]) == sorted(WIRING)
    codes = {f.code for f in report.findings}
    assert {"phase_id_reference_station", "phase_id_identified", "phase_id_order"} <= codes
    assert set(phase_id.OUT_COLUMNS) == set(out.columns)


def test_without_a_station_meter_the_three_phase_meters_stand_in(flow):
    """The order most three-phase meters share is called a, b, c: here the network's own."""
    out, report = phase_id.identify(meters(flow), None, threshold=0.1)
    assert share_right(flow, out) >= 0.95
    assert set(out.loc[out["status"] == "identified", "reference"]) == {"three_phase_meters"}
    assert sorted(out.loc[out["service"] == "three", "order"]) == sorted(WIRING)
    assert "phase_id_reference_meters" in {f.code for f in report.findings}


def test_with_neither_the_single_phase_meters_are_grouped(flow):
    """Without any reference only the grouping is meaningful: right up to the names of the groups."""
    ids, truth, *_ = flow
    single = [cid for cid, ph in zip(ids, truth) if ph]
    volt = meters(flow)
    out, report = phase_id.identify(volt[volt["customer_id"].isin(single)], None, threshold=0.1)
    got = out.set_index("customer_id")["phase"]
    want = pd.Series(truth, index=ids)[got.index]
    best = max(float((got.map(dict(zip("abc", perm))) == want).mean())
               for perm in ("abc", "acb", "bac", "bca", "cab", "cba"))
    assert best >= 0.95
    assert "phase_id_reference_clusters" in {f.code for f in report.findings}


def test_the_default_threshold_identifies_at_the_default_noise(flow):
    out, _ = phase_id.identify(meters(flow), station(flow))
    single = out[out["service"] == "single"]
    assert (single["status"] == "identified").mean() >= 0.95
    assert share_right(flow, out) >= 0.95


def test_a_flat_voltage_stays_unidentified(flow):
    ids, truth, *_ = flow
    flat = next(cid for cid, ph in zip(ids, truth) if ph)
    out, report = phase_id.identify(meters(flow, flat=flat), station(flow))
    row = out.set_index("customer_id").loc[flat]
    assert row["status"] == "flat" and row["phase"] == ""
    assert "phase_id_flat" in {f.code for f in report.findings}


def test_noise_lowers_the_confidence_rather_than_guessing(flow):
    out, report = phase_id.identify(meters(flow, noise=40.0), station(flow))
    single = out[out["service"] == "single"]
    assert (single["status"] == "low_confidence").mean() >= 0.8
    assert (single.loc[single["status"] != "identified", "phase"] == "").all()
    assert "phase_id_low_confidence" in {f.code for f in report.findings}


def test_too_few_readings_say_so(flow):
    volt = meters(flow)
    volt = volt[volt["start"] < START + pd.Timedelta(days=2)]
    out, report = phase_id.identify(volt, None)
    assert set(out["status"]) == {"too_few"}
    assert "phase_id_too_few" in {f.code for f in report.findings}


def test_the_recorded_phase_wins_unless_the_identified_one_is_preferred():
    customers = pd.DataFrame({"osm_id": ["A", "B", "C", "D"], "phase": ["", "b", "a", ""]})
    ident = pd.DataFrame({"customer_id": ["A", "B", "C", "D"], "service": "single",
                          "phase": ["c", "c", "a", "b"],
                          "status": ["identified", "identified", "identified", "low_confidence"]})
    report = phase_id.Report()
    kept, counts = phase_id.apply(customers, ident, report=report)
    assert kept["phase"].to_list() == ["c", "b", "a", ""]
    assert counts == {"identified": 3, "written": 1, "agree": 1, "conflict": 1, "prefer_identified": False}
    assert [f.code for f in report.findings] == ["phase_id_conflict_recorded"]
    preferred, counts = phase_id.apply(customers, ident, prefer_identified=True)
    assert preferred["phase"].to_list() == ["c", "c", "a", ""]
    assert counts["written"] == 2


def test_the_pv_phase_marks_where_a_meter_order_matters(flow):
    ids, truth, *_ = flow
    threes = [cid for cid, ph in zip(ids, truth) if not ph]
    pv = pd.Series({threes[-1]: "a", threes[0]: "a"})          # wired 'bac', and 'abc'
    out, report = phase_id.identify(meters(flow), station(flow), pv_phase=pv)
    assert out.loc[out["matters"], "customer_id"].to_list() == [threes[-1]]
    assert "phase_id_order_matters" in {f.code for f in report.findings}


def test_the_station_meter_is_read_from_the_mv_file_in_either_dialect(tmp_path):
    (tmp_path / "s.csv").write_text("start;vm_pu;v_l1;v_l2;v_l3\n2025-03-03T01:00:00+01:00;1,01;231,2;230,9;232,0\n"
                                    "2025-03-03T00:15:00Z;1,01;231,0;230,8;231,7\n", encoding="utf-8-sig")
    ref = phase_id.read_reference(tmp_path / "s.csv")
    assert list(ref.columns) == ["start", "v_l1", "v_l2", "v_l3"]
    assert ref["start"].iloc[0] == START and ref["v_l1"].iloc[0] == pytest.approx(231.2)
    (tmp_path / "mv.csv").write_text("start,vm_pu\n2025-03-03T00:00:00Z,1.0\n", encoding="utf-8")
    assert phase_id.read_reference(tmp_path / "mv.csv") is None
    assert phase_id.read_reference(tmp_path / "missing.csv") is None


def test_the_identification_is_written_with_its_report(flow, tmp_path):
    out, report = phase_id.identify(meters(flow), station(flow))
    path = phase_id.write(out, report, tmp_path)
    back = pd.read_parquet(path)
    assert len(back) == len(out) and set(back.columns) == set(phase_id.OUT_COLUMNS)
    import json

    doc = json.loads((tmp_path / "phase_id.json").read_text(encoding="utf-8"))
    assert doc["reference"] == "station" and doc["report"]["findings"]
