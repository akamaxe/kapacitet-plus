"""A CGMES network, one station per LV transformer, written as the export contract (importer/cim.py),
and a study that imports it (`kapacitet study import <dir> --cim`).

The network is our own fixture, generated at test time (tests/fixtures/cgmes_tiny.py): an MV
source, one 10/0.4 kV transformer, two feeders behind fuses, an open point between them and eight
houses, plus an MV customer that belongs to no station. Nothing here needs the pipeline's output.
"""

from __future__ import annotations

import shutil
import warnings

import numpy as np
import pandapower as pp
import pandas as pd
import pytest
from fixtures import cgmes_tiny

from kapacitet import paths
from kapacitet.importer.build import build_from_export
from kapacitet.importer.cim import CimError, convert, mrid_key, read_mapping, stations
from kapacitet.importer.from_pandapower import write_contract
from kapacitet.importer.validate import ERROR, read_export, validate
from kapacitet.study import StudyError, init_study, open_study
from kapacitet.study.intake import CGMES_MARK, check, contract_dir, derived_id, import_cim, split

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    fx = cgmes_tiny.write(tmp_path_factory.mktemp("cgmes"))
    return fx, convert(fx.files, fx.mapping_file)


def _station(conversion):
    assert len(conversion.stations) == 1
    return conversion.stations[0]


# ---------------------------------------------------------------- the conversion

def test_one_low_voltage_station_with_the_mrids_kept(tiny):
    fx, conversion = tiny
    st = _station(conversion)
    assert not conversion.blocked and not conversion.findings.findings     # the MV customer is no one's
    assert st.trafo_mrid == fx.trafo and st.name == "Station 1"
    buses, lines, customers = st.tables["buses"], st.tables["lines"], st.tables["customers"]
    # the fuses fuse both feeder heads into the busbar; the tie node stays a node of its own
    assert len(buses) == 13 and buses["bus_id"].iloc[0] == fx.nodes["LV busbar"]
    assert buses.loc[buses["kind"] == "substation", "name"].tolist() == ["LV busbar"]
    assert set(buses["bus_id"]) == {fx.nodes[n] for n in ["LV busbar", "K1", "K2", "K3", "K3 tie",
                                                          *[h for h, _k, _xy in cgmes_tiny.HOUSES]]}
    assert set(lines["line_id"]) == set(fx.lines.values())
    assert list(customers["customer_id"]) == list(fx.mapping["customer_id"])
    by_bus = dict(zip(buses["bus_id"], buses["name"]))
    assert [by_bus[b] for b in customers["bus"]] == [h for h, _k, _xy in cgmes_tiny.HOUSES]
    trafo = st.tables["transformer"].iloc[0]
    assert (trafo["sn_mva"], trafo["vn_hv_kv"], trafo["vn_lv_kv"], trafo["vector_group"], trafo["tap_pos"]) == \
        (0.4, 10.0, 0.4, "Dyn", 0)
    assert abs(trafo["vk_percent"] - 4.0) < 1e-9 and abs(trafo["vkr_percent"] - 1.2) < 1e-9


def test_every_bus_is_placed_in_wgs84_k2_from_its_cables(tiny):
    fx, conversion = tiny
    buses = _station(conversion).tables["buses"].set_index("name")
    for name in ["LV busbar", "K1", "K2", "K3", "H1", "H5", "H8"]:
        lon, lat = fx.places[name]
        assert abs(buses.at[name, "lon"] - lon) < 1e-9 and abs(buses.at[name, "lat"] - lat) < 1e-9, name


def test_coordinates_in_another_reference_system_come_back_as_wgs84(tmp_path, tiny):
    _fx, conversion = tiny
    fx = cgmes_tiny.write(tmp_path, crs="EPSG:3006")                     # SWEREF 99 TM
    other = _station(convert(fx.files, fx.mapping_file)).tables["buses"]
    want = _station(conversion).tables["buses"]
    assert np.abs(other[["lon", "lat"]].to_numpy() - want[["lon", "lat"]].to_numpy()).max() < 1e-7


def test_the_contract_written_and_read_back_passes_the_check_and_is_radial(tmp_path, tiny):
    _fx, conversion = tiny
    st = _station(conversion)
    write_contract(st.tables, tmp_path)
    report = validate(read_export(tmp_path))
    assert report.usable, report.text()
    assert not report.errors and "meshed_network" not in {f.code for f in report.findings}
    network, applied = build_from_export(read_export(tmp_path))
    assert network.summary["feeders"] == 2 and network.summary["customers"] == 8
    assert "cable type assumed" not in applied


def test_closing_the_open_point_makes_the_feeders_a_ring(tmp_path):
    fx = cgmes_tiny.write(tmp_path, tie_open=False)
    st = _station(convert(fx.files, fx.mapping_file))
    assert len(st.tables["buses"]) == 12                                   # the tie node fused into K3
    codes = [f.code for f in st.check.findings]
    assert st.check.usable and codes.count("meshed_network") == 1


def test_the_engine_solves_the_station_as_pandapower_solves_the_cgmes_network(tmp_path, tiny, monkeypatch):
    """The imported station, built and loaded as the engine loads any network (NetModel), against
    pandapower's Newton-Raphson on the network the CGMES files describe: the same voltages."""
    from kapacitet.engine.netmodel import NetModel
    from kapacitet.grid.builder import save

    fx, conversion = tiny
    st = _station(conversion)
    write_contract(st.tables, tmp_path / "contract")
    network, _ = build_from_export(read_export(tmp_path / "contract"))
    monkeypatch.setattr(paths, "PROCESSED", tmp_path / "processed")
    save(network)
    model = NetModel()
    assert model.C == 8 and list(model.cust["osm_id"].astype(str)) == list(fx.mapping["customer_id"])

    rng = np.random.default_rng(11)
    hours = 6
    p_kw = rng.uniform(-9.0, 14.0, size=(model.C, hours))                  # positive = injection
    q_kvar = rng.uniform(-2.0, 2.0, size=(model.C, hours))
    res = model.solve(p_kw, q_kvar, vm_slack=1.0)
    vm = np.abs(res.v[model.cust_bus_row])

    src = conversion.net.deepcopy()
    load_of = {mrid_key(m): i for i, m in src.load["origin_id"].items()}
    rows = [load_of[mrid_key(m)] for m in fx.mapping["energyconsumer_mrid"]]
    built = model.pf.net
    for h in range(hours):
        src.load["p_mw"], src.load["q_mvar"] = 0.0, 0.0                    # the MV customer too
        src.load.loc[rows, "p_mw"] = -p_kw[:, h] / 1000.0
        src.load.loc[rows, "q_mvar"] = -q_kvar[:, h] / 1000.0
        pp.runpp(src, calculate_voltage_angles=False, init="flat", tolerance_mva=1e-10)
        source_vm = src.res_bus.vm_pu.loc[src.load.loc[rows, "bus"].astype(int)].to_numpy()
        assert np.abs(vm[:, h] - source_vm).max() < 1e-8

        built.load["p_mw"] = np.maximum(-p_kw[:, h], 0.0) / 1000.0
        built.load["q_mvar"] = np.maximum(-q_kvar[:, h], 0.0) / 1000.0
        built.sgen["p_mw"] = np.maximum(p_kw[:, h], 0.0) / 1000.0
        built.sgen["q_mvar"] = np.maximum(q_kvar[:, h], 0.0) / 1000.0
        pp.runpp(built, calculate_voltage_angles=False, init="flat", tolerance_mva=1e-10)
        assert np.abs(np.abs(res.v[:, h]) - built.res_bus.vm_pu.to_numpy()).max() < 1e-8
    assert np.ptp(vm) > 0.01 and vm.min() < 1.0 < vm.max()                # the case is not flat


def test_a_consumer_the_mapping_does_not_name_is_an_error_never_a_guess(tmp_path):
    fx = cgmes_tiny.write(tmp_path)
    fx.mapping.drop(index=3).to_csv(fx.mapping_file, index=False)        # H4
    conversion = convert(fx.files, fx.mapping_file)
    st = _station(conversion)
    assert conversion.blocked and not st.usable and st.tables is None
    [f] = st.findings.errors
    assert (f.code, f.count, f.params["mrid"]) == ("cim_unmapped_consumer", 1, fx.consumers["H4"])
    assert st.report().errors == [f]


def test_a_mapping_row_for_no_consumer_is_noted(tmp_path):
    fx = cgmes_tiny.write(tmp_path)
    extra = pd.DataFrame([{"energyconsumer_mrid": fx.consumers["MV customer"], "customer_id": "MV1",
                           "fuse_a": 400, "phase": ""}])
    pd.concat([fx.mapping, extra]).to_csv(fx.mapping_file, index=False)
    conversion = convert(fx.files, fx.mapping_file)
    assert not conversion.blocked
    [f] = conversion.findings.findings
    assert (f.code, f.severity, f.params["mrid"]) == ("cim_mapping_unused", "note", fx.consumers["MV customer"])


def test_a_second_transformer_on_the_same_lv_network_and_consumers_no_station_reaches(tiny):
    fx, conversion = tiny
    mapping = read_mapping(fx.mapping_file)
    net = conversion.net.deepcopy()
    k1 = int(net.bus.index[net.bus["origin_id"] == fx.nodes["K1"]][0])
    hv = int(net.trafo.at[0, "hv_bus"])
    pp.create_transformer(net, hv, k1, "0.4 MVA 10/0.4 kV", name="T2")
    net.trafo.loc[1, "origin_id"] = "_t2"
    out, overall = stations(net, mapping)
    assert [(f.code, f.params["mrid"]) for st in out for f in st.findings.errors] == \
        [("cim_shared_island", "_t2"), ("cim_shared_island", fx.trafo)]
    assert all(st.tables is None for st in out) and not overall.findings

    net = conversion.net.deepcopy()
    f2 = net.switch.index[net.switch["element"] == int(net.bus.index[net.bus["name"] == "Feeder 2 head"][0])]
    net.switch.loc[f2, "closed"] = False                                   # feeder 2's fuse taken out
    [st], overall = stations(net, mapping)
    assert len(st.tables["customers"]) == 5 and st.check.usable
    codes = {f.code: f for f in overall.findings}
    assert codes["cim_unreached_consumer"].count == 3 and codes["cim_unreached_consumer"].severity == "warning"
    assert codes["cim_mapping_unused"].count == 3                          # H6-H8 are named, but in no station

    net.trafo["in_service"] = False
    out, overall = stations(net, mapping)
    assert out == [] and [f.code for f in overall.errors] == ["cim_no_station"]


def test_the_mapping_matches_bare_mrids_and_reads_the_swedish_dialect(tmp_path):
    fx = cgmes_tiny.write(tmp_path)
    m = fx.mapping.assign(energyconsumer_mrid=fx.mapping["energyconsumer_mrid"].str.lstrip("_").str.upper(),
                          footprint_m2=["110,5"] * len(fx.mapping))
    fx.mapping_file.write_text("﻿" + m.to_csv(sep=";", index=False), encoding="utf-8")
    customers = _station(convert(fx.files, fx.mapping_file)).tables["customers"]
    assert (customers["footprint_m2"] == 110.5).all()
    assert customers["phase"].tolist()[2] == "a" and pd.isna(customers["phase"].tolist()[0])


@pytest.mark.parametrize("change, message", [
    (lambda m: m.drop(columns="fuse_a"), "has no fuse_a"),
    (lambda m: m.assign(fuse_a=["16"] * 7 + ["16.5"]), "not a whole number of amperes on line 9"),
    (lambda m: m.assign(customer_id=[""] + list(m["customer_id"][1:])), "no customer_id on line 2"),
    (lambda m: pd.concat([m, m.iloc[[0]]]), "energyconsumer_mrid repeated on line 2, 10"),
])
def test_a_mapping_that_cannot_be_read_says_which_lines_to_fix(tmp_path, change, message):
    fx = cgmes_tiny.write(tmp_path)
    change(fx.mapping).to_csv(fx.mapping_file, index=False)
    with pytest.raises(CimError, match=message):
        read_mapping(fx.mapping_file)


# ---------------------------------------------------------------- a study's import

@pytest.fixture()
def cim_study(tmp_path):
    study = init_study(tmp_path / "cim-study", "CGMES test DSO")
    fx = cgmes_tiny.write(tmp_path / "files")
    folder = study.inputs / "network" / "cgmes"
    folder.mkdir(parents=True)
    for f in fx.files:
        shutil.copyfile(f, folder / f.name)
    shutil.copyfile(fx.mapping_file, study.inputs / "cim_customers.csv")
    return study, fx


def test_a_study_converts_its_cgmes_files_and_never_skips_them(cim_study):
    study, fx = cim_study
    with pytest.raises(StudyError) as err:
        split(study)                                                       # not converted yet
    assert err.value.code == "cgmes_pending"

    conversion, removed = import_cim(study)
    sid = derived_id(fx.trafo)
    assert not conversion.blocked and removed == [] and sid == fx.trafo.lstrip("_")
    assert (contract_dir(study) / CGMES_MARK).is_file()
    assert split(study) == [sid]
    rec = study.record(sid)
    assert rec["origin"]["format"] == "cgmes" and rec["origin"]["trafo_mrid"] == fx.trafo
    assert rec["origin"]["declared"] and set(rec["origin"]["source"]["files"]) == {f.name for f in fx.files}
    assert rec["name"] == "Station 1" and check(study)[sid].usable

    mapping = study.inputs / "cim_customers.csv"
    fx.mapping.assign(fuse_a=25).to_csv(mapping, index=False)              # the mapping changed
    with pytest.raises(StudyError) as err:
        split(study)
    assert err.value.code == "cgmes_pending"
    import_cim(study)
    assert set(pd.read_csv(study.station_dir(sid) / "import" / "customers.csv")["fuse_a"]) == {16, 20, 25}
    split(study)
    assert set(pd.read_csv(study.station_dir(sid) / "import" / "customers.csv")["fuse_a"]) == {25}


def test_stations_csv_names_the_station_and_the_old_folder_goes(cim_study):
    study, fx = cim_study
    import_cim(study)
    old = derived_id(fx.trafo)
    (study.inputs / "stations.csv").write_text(f"station_id;name;trafo_mrid\nNS-0042;Exempelgatan 4;{fx.trafo}\n",
                                               encoding="utf-8")
    conversion, removed = import_cim(study)
    assert removed == [old] and not (contract_dir(study) / old).exists()
    assert split(study) == ["NS-0042"] and study.record("NS-0042")["name"] == "Exempelgatan 4"


def test_a_blocked_conversion_writes_nothing_and_the_command_says_so(cim_study, capsys):
    from kapacitet.pipeline import main

    study, fx = cim_study
    fx.mapping.drop(index=0).to_csv(study.inputs / "cim_customers.csv", index=False)
    with pytest.raises(SystemExit) as code:
        main(["study", "import", str(study.root), "--cim"])
    out = capsys.readouterr().out
    assert code.value.code == 1 and "Nothing written" in out and "cim_customers.csv does not name" in out
    assert not any(contract_dir(study).iterdir()) and study.station_ids() == []

    fx.mapping.to_csv(study.inputs / "cim_customers.csv", index=False)
    main(["study", "import", str(study.root), "--cim"])
    out = capsys.readouterr().out
    assert "1 low-voltage stations" in out and "8 customers" in out and "usable" in out
    assert open_study(study.root).station_ids() == [derived_id(fx.trafo)]


def test_a_contract_folder_of_the_dsos_own_is_never_written_over(cim_study):
    study, fx = cim_study
    own = contract_dir(study) / derived_id(fx.trafo)
    own.mkdir(parents=True)
    (own / "buses.csv").write_text("bus_id,kind\nA,substation\n", encoding="utf-8")
    with pytest.raises(StudyError) as err:
        import_cim(study)
    assert err.value.code == "cim_overwrites"
    assert (own / "buses.csv").read_text(encoding="utf-8") == "bus_id,kind\nA,substation\n"


def test_without_a_mapping_file_the_import_says_what_it_needs(cim_study):
    study, _fx = cim_study
    (study.inputs / "cim_customers.csv").unlink()
    with pytest.raises(StudyError) as err:
        import_cim(study)
    assert err.value.code == "cim" and "customer_id and fuse_a" in str(err.value)


def test_a_stray_file_in_the_cgmes_folder_is_not_pending_input(cim_study):
    study, fx = cim_study
    import_cim(study)
    (study.inputs / "network" / "cgmes" / "README.txt").write_text("notes", encoding="utf-8")
    assert split(study)                                                    # not blocked by the note


def test_a_failure_while_writing_leaves_the_study_pending(cim_study, monkeypatch):
    study, fx = cim_study
    import_cim(study)
    mapping = study.inputs / "cim_customers.csv"
    fx.mapping.assign(fuse_a=25).to_csv(mapping, index=False)
    from kapacitet.study import intake

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(intake, "add_contract", boom)
    with pytest.raises(OSError):
        import_cim(study)
    monkeypatch.undo()
    with pytest.raises(StudyError) as err:
        split(study)
    assert err.value.code == "cgmes_pending"


def test_an_lv_lv_transformer_is_not_a_station():
    from kapacitet.importer.cim import lv_transformers

    net = pp.create_empty_network()
    hv, mid, lv = (pp.create_bus(net, vn_kv=v) for v in (10.0, 0.69, 0.4))
    pp.create_transformer_from_parameters(net, hv, mid, 0.63, 10.0, 0.69, 1.0, 4.0, 1.0, 0.1)
    pp.create_transformer_from_parameters(net, mid, lv, 0.4, 0.69, 0.4, 1.0, 4.0, 1.0, 0.1)
    assert lv_transformers(net) == [0]
