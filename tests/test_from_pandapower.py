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
from kapacitet.importer.from_pandapower import (ContractError, contract_tables, contract_tap, transformer_id,
                                                write_contract, point_geojson)
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


def _rebuilt(tables, folder):
    write_contract(tables, folder)
    return build_from_export(read_export(folder))


def _same_voltages(net, tables, network, p: float = 0.004) -> float:
    """The largest difference of a bus voltage between the source and the rebuilt network, with the
    same load on every customer."""
    built = network.net
    net.load["p_mw"], net.load["q_mvar"] = p, 0.0
    net.ext_grid["vm_pu"] = 1.0
    pp.runpp(net)
    built.load["p_mw"], built.load["q_mvar"] = p, 0.0
    pp.runpp(built)
    contract_name = dict(zip(tables["buses"]["bus_id"], tables["buses"]["name"]))
    assert len(set(contract_name.values())) == len(contract_name)
    built_bus = dict(zip(network.buses["name"], network.buses["bus"]))
    return max(abs(net.res_bus.vm_pu.at[int(bid[1:])] - built.res_bus.vm_pu.at[built_bus[name]])
               for bid, name in contract_name.items())


def _ratings(net) -> list[float]:
    """Each line's thermal limit, as the engine reads it (powerflow.py), in line order."""
    return list(np.round(net.line["max_i_ka"] * net.line["df"] * net.line["parallel"], 6))


@pytest.mark.parametrize("make", [pn.kb_extrem_landnetz_kabel_trafo, pn.kb_extrem_dorfnetz])
def test_the_round_trip_is_electrically_the_same_network(tmp_path, make):
    net = make()                                                     # its taps as they are
    tables = contract_tables(net, 0, _map(net))
    network, applied = _rebuilt(tables, tmp_path)
    assert "cable type assumed" not in applied
    assert _ratings(network.net) == _ratings(net)
    assert _same_voltages(net, tables, network) < 1e-3
    assert net.res_bus.vm_pu.min() < 0.97                            # the case is not trivially flat


def test_a_derated_line_of_a_library_type_keeps_its_own_rating(tmp_path):
    net = pn.kb_extrem_dorfnetz()
    net.line["df"] = 0.8
    library = net.line["std_type"] == "NAYY 4x50 SE"                  # in pandapower's own library
    assert library.any()
    tables = contract_tables(net, 0, _map(net))
    lines = tables["lines"].set_index("line_id")
    assert lines.loc[[f"L{i}" for i in net.line.index[library]], "std_type"].isna().all()
    assert (lines.loc[[f"L{i}" for i in net.line.index[~library]], "std_type"] != "").all()
    network, _ = _rebuilt(tables, tmp_path)
    assert _ratings(network.net) == _ratings(net)                    # 0.8 x the type's rating, not the type's
    assert _same_voltages(net, tables, network) < 1e-3


@pytest.mark.parametrize("side, neutral, pos, step, want", [
    ("hv", 5, 5, 2.5, 0),                                            # at a neutral that is not 0
    ("hv", 5, 6, 2.5, 1),
    ("hv", 2, 3, 2.5, 1),
    ("lv", 5, 6, 2.5, -1),                                           # a raised LV winding lowers the ratio
    ("hv", 0, -2, 1.25, -1),                                         # another step size
    ("hv", 5, 9, 2.5, None),                                         # +10 %: beyond -2..2
    ("hv", 0, 1, 1.5, None),                                         # 1.5 %: between two of the contract's steps
])
def test_a_tap_is_written_as_the_contracts_position_with_the_same_ratio(tmp_path, side, neutral, pos, step, want):
    net = pn.kb_extrem_dorfnetz()
    net.trafo.loc[0, ["tap_side", "tap_min", "tap_max", "tap_neutral", "tap_pos", "tap_step_percent"]] = \
        [side, min(neutral, pos) - 1, max(neutral, pos) + 1, neutral, pos, step]
    if want is None:
        with pytest.raises(ContractError, match="cannot give"):
            contract_tables(net, 0, _map(net))
        return
    tables = contract_tables(net, 0, _map(net))
    assert int(tables["transformer"]["tap_pos"].iloc[0]) == want
    network, _ = _rebuilt(tables, tmp_path)
    assert _same_voltages(net, tables, network, p=0.0) < 2e-3        # the LV busbar at no load


def test_a_tap_pandapower_ignores_is_neutral_and_a_phase_shifting_one_is_refused():
    net = pn.kb_extrem_landnetz_kabel_trafo()                        # NaN tap: no tap changer
    assert contract_tap(net.trafo.loc[0]) == 0
    trafo = pn.kb_extrem_dorfnetz().trafo.loc[0].copy()
    trafo["tap_pos"] = 1
    assert contract_tap(trafo) == 1
    for shifter in ("Ideal", "Symmetrical"):                        # pandapower 3's phase shifters
        trafo["tap_changer_type"] = shifter
        with pytest.raises(ContractError, match="phase"):
            contract_tap(trafo)
    trafo["tap_changer_type"] = None                                 # no tap changer: at neutral
    assert contract_tap(trafo) == 0
    old = trafo.drop("tap_changer_type")                             # a network from before pandapower 3
    old["tap_phase_shifter"] = True
    with pytest.raises(ContractError, match="phase"):
        contract_tap(old)


def test_a_tap_from_the_characteristic_table_uses_the_tables_ratio():
    """pandapower 3 (and its CGMES converter) solves a transformer with tap_dependency_table set
    with the table's voltage_ratio and impedance, whatever the step says."""
    import pandas as pd

    net = pn.kb_extrem_dorfnetz()
    t = net.trafo.index[0]
    neutral = int(net.trafo.at[t, "tap_neutral"])
    net.trafo_characteristic_table = pd.DataFrame({
        "id_characteristic": [0, 0, 0], "step": [neutral - 1, neutral, neutral + 1],
        "voltage_ratio": [0.95, 1.0, 1.05], "angle_deg": [0.0, 0.0, 0.0],
        "vk_percent": [4.1, 4.2, 4.3], "vkr_percent": [1.1, 1.2, 1.3]})
    net.trafo["tap_dependency_table"] = net.trafo["tap_dependency_table"].astype(object)
    net.trafo.loc[t, ["tap_dependency_table", "id_characteristic_table", "tap_pos", "tap_side"]] =         [True, 0, neutral + 1, "hv"]
    trafo = net.trafo.loc[t]
    assert contract_tap(trafo, net.trafo_characteristic_table) == 2          # +5 %, not one 2.5 % step
    tables = contract_tables(net, 0, _map(net))
    row = tables["transformer"].iloc[0]
    assert (int(row["tap_pos"]), row["vk_percent"], row["vkr_percent"]) == (2, 4.3, 1.3)
    trafo = trafo.copy()
    trafo["tap_side"] = "lv"
    assert contract_tap(trafo, net.trafo_characteristic_table) == -2         # a raised LV winding
    with pytest.raises(ContractError, match="does not hold"):
        contract_tap(trafo)                                                   # the table is missing
    trafo["tap_pos"] = neutral + 5
    with pytest.raises(ContractError, match="no row"):
        contract_tap(trafo, net.trafo_characteristic_table)
    shifted = net.trafo_characteristic_table.assign(angle_deg=[-5.0, 0.0, 5.0])
    with pytest.raises(ContractError, match="phase"):
        contract_tap(net.trafo.loc[t], shifted)
    plain = pn.kb_extrem_dorfnetz().trafo.loc[0].copy()
    plain["tap_pos"], plain["tap_changer_type"] = plain["tap_neutral"] + 1, "Tabular"
    with pytest.raises(ContractError, match="tabular"):
        contract_tap(plain)                                                   # no table to read


def test_the_transformers_own_id_is_kept():
    net = pn.kb_extrem_dorfnetz()
    assert transformer_id(net, 0) is None
    net.trafo["origin_id"] = "mrid-trafo-1"
    assert transformer_id(net, 0) == "mrid-trafo-1"


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
    net.bus["geo"] = [point_geojson(17.9 + i * 1e-4, 59.3) for i in range(len(net.bus))]
    buses = contract_tables(net, 0, _map(net))["buses"]
    assert buses["lon"].between(17.9, 18.0).all() and (buses["lat"] == 59.3).all()
    net.bus["geo"] = [point_geojson(650000.0, 59.3) for _ in range(len(net.bus))]   # projected metres are not written
    assert "lon" not in contract_tables(net, 0, _map(net))["buses"].columns


def test_an_unknown_transformer_is_refused():
    with pytest.raises(ContractError):
        contract_tables(pn.kb_extrem_landnetz_kabel_trafo(), 7)
