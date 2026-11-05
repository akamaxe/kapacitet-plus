"""A pandapower network written as the export contract (importer/from_pandapower.py).

Everything here builds its networks at test time, from pandapower's own LV benchmarks or by hand,
so it runs without the pipeline's output."""

from __future__ import annotations

import warnings

import numpy as np
import pandapower as pp
import pandapower.networks as pn
import pytest

from kapacitet.importer.build import build_from_export
from kapacitet.importer.from_pandapower import ContractError, contract_tables, write_contract
from kapacitet.importer.validate import ERROR, read_export, validate

warnings.filterwarnings("ignore")


def _map(net, fuse=25, footprint=110.0):
    return {int(i): {"fuse_a": fuse, "footprint_m2": footprint} for i in net.load.index}


@pytest.mark.parametrize("make", [pn.kb_extrem_landnetz_kabel_trafo, pn.kb_extrem_dorfnetz,
                                  lambda: pn.create_dickert_lv_network(feeders_range="middle", customer="multiple")])
def test_a_benchmark_network_passes_the_contract_check(make):
    net = make()
    tables = contract_tables(net, 0, _map(net))
    report = validate(tables)
    assert report.usable, report.text()
    assert len(tables["customers"]) == len(net.load)
    assert len(tables["lines"]) == len(net.line)
    assert len(tables["buses"]) == len(net.bus) - 1                 # the MV bus is not in the contract
    assert tables["buses"]["kind"].iloc[0] == "substation"
    assert tables["transformer"]["lv_bus"].iloc[0] == tables["buses"]["bus_id"].iloc[0]


def test_the_round_trip_is_electrically_the_same_network(tmp_path):
    net = pn.kb_extrem_landnetz_kabel_trafo()
    tables = contract_tables(net, 0, _map(net))
    write_contract(tables, tmp_path)
    network, applied = build_from_export(read_export(tmp_path))
    assert "cable type assumed" not in applied
    built = network.net

    p = 0.004
    net.load["p_mw"], net.load["q_mvar"] = p, 0.0
    net.ext_grid["vm_pu"] = 1.0
    net.trafo[["tap_pos", "tap_neutral"]] = 0
    pp.runpp(net)
    built.load["p_mw"], built.load["q_mvar"] = p, 0.0
    pp.runpp(built)

    contract_name = dict(zip(tables["buses"]["bus_id"], tables["buses"]["name"]))
    assert len(set(contract_name.values())) == len(contract_name)
    built_bus = dict(zip(network.buses["name"], network.buses["bus"]))
    diff = [abs(net.res_bus.vm_pu.at[int(bid[1:])] - built.res_bus.vm_pu.at[built_bus[name]])
            for bid, name in contract_name.items()]
    assert max(diff) < 1e-3
    assert net.res_bus.vm_pu.min() < 0.97                            # the case is not trivially flat


def _hand_made():
    """MV source, one transformer; LV bus A fused to the tie bus T by a closed bus-bus switch; T - B - C with
    an open switch on B - C (C is beyond the open point); an out-of-service line A - D; a second
    transformer with its own LV bus E."""
    net = pp.create_empty_network()
    mv = pp.create_bus(net, 10.0, name="MV")
    pp.create_ext_grid(net, mv)
    a, tie, b, c, d, e = (pp.create_bus(net, 0.4, name=n) for n in ("A", "T", "B", "C", "D", "E"))
    pp.create_transformer(net, mv, a, "0.25 MVA 10/0.4 kV")
    pp.create_transformer(net, mv, e, "0.25 MVA 10/0.4 kV")
    pp.create_switch(net, a, tie, et="b", closed=True)
    pp.create_line(net, tie, b, 0.1, "NAYY 4x150 SE")
    bc = pp.create_line(net, b, c, 0.1, "NAYY 4x50 SE")
    pp.create_switch(net, b, bc, et="l", closed=False)
    pp.create_line(net, a, d, 0.1, "NAYY 4x50 SE", in_service=False)
    for bus in (tie, b, c, d, e):
        pp.create_load(net, bus, p_mw=0.002)
    net.bus.loc[b, "origin_id"] = "mrid-b"
    return net


def test_the_island_stops_at_open_points_and_fuses_closed_bus_switches():
    net = _hand_made()
    tables = contract_tables(net, 0, {int(i): {"fuse_a": 20} for i in net.load.index})
    buses = tables["buses"]
    assert list(buses["name"]) == ["A", "B"]                         # T fused into A; C, D, E outside
    assert list(buses["bus_id"]) == ["B1", "mrid-b"]                 # an origin_id is kept as the id
    assert len(tables["lines"]) == 1 and tables["lines"]["from_bus"].iloc[0] == "B1"
    assert list(tables["customers"]["bus"]) == ["B1", "mrid-b"]      # the loads on T and B
    assert validate(tables).usable
    other = contract_tables(net, 1, {int(i): {"fuse_a": 20} for i in net.load.index})
    assert list(other["buses"]["name"]) == ["E"] and len(other["customers"]) == 1


def test_a_load_without_a_map_entry_is_an_error_never_a_guess():
    net = pn.kb_extrem_landnetz_kabel_trafo()
    partial = _map(net)
    partial.pop(int(net.load.index[3]))
    with pytest.raises(ContractError, match="no entry in the customer map"):
        contract_tables(net, 0, partial)


def test_without_a_map_the_missing_fuse_is_reported_by_the_check():
    net = pn.kb_extrem_landnetz_kabel_trafo()
    tables = contract_tables(net, 0)
    assert "fuse_a" not in tables["customers"].columns
    report = validate(tables)
    assert not report.usable
    assert any(f.severity == ERROR and f.code == "missing_column" for f in report.findings)


def test_coordinates_only_when_they_are_wgs84():
    net = pn.kb_extrem_landnetz_kabel_trafo()
    assert "lon" not in contract_tables(net, 0, _map(net))["buses"].columns
    net.bus_geodata = net.bus_geodata.reindex(net.bus.index)
    net.bus_geodata["x"] = 17.9 + np.arange(len(net.bus)) * 1e-4
    net.bus_geodata["y"] = 59.3
    buses = contract_tables(net, 0, _map(net))["buses"]
    assert buses["lon"].between(17.9, 18.0).all() and (buses["lat"] == 59.3).all()
    net.bus_geodata["x"] = 650000.0                                 # projected metres are not written
    assert "lon" not in contract_tables(net, 0, _map(net))["buses"].columns


def test_an_unknown_transformer_is_refused():
    with pytest.raises(ContractError):
        contract_tables(pn.kb_extrem_landnetz_kabel_trafo(), 7)
