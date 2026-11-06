"""A network file is checked before pandapower builds anything from it (grid.builder.safe_load_net).

pp.from_json imports whatever module and calls whatever class a file names, and a net.json can come
from outside (a data bundle, an import). These run on a fresh clone: the files are written here.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pandapower as pp
import pytest

from conftest import needs_grid
from kapacitet.grid.builder import NET_JSON_OBJECTS, GridFileError, check_net_json, safe_load_net


def _small_net() -> pp.pandapowerNet:
    net = pp.create_empty_network(name="Smedslätten test")
    b0 = pp.create_bus(net, vn_kv=10.0)
    b1 = pp.create_bus(net, vn_kv=0.4, geodata=(17.95, 59.33))   # what the current pandapower writes
    b2 = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, b0)
    t = pp.create_transformer(net, b0, b1, std_type="0.4 MVA 10/0.4 kV")
    net.trafo.loc[t, "tap_pos"] = net.trafo.at[t, "tap_neutral"] + 1     # a Ratio tap above neutral
    pp.create_line(net, b1, b2, length_km=0.1, std_type="NAYY 4x50 SE")
    pp.create_load(net, b2, p_mw=0.01)
    return net


@pytest.fixture()
def net_file(tmp_path):
    path = tmp_path / "net.json"
    pp.to_json(_small_net(), str(path))
    return path


@pytest.fixture()
def no_imports(monkeypatch):
    """Any import from inside the loader fails the test: a refusal must come before pandapower runs."""
    def refuse(name, *args, **kwargs):
        raise AssertionError(f"the loader imported {name} before refusing the file")

    monkeypatch.setattr(importlib, "import_module", refuse)


def _net_with(tmp_path, change) -> object:
    """Our own small net.json, with one part of its tree changed."""
    path = tmp_path / "net.json"
    pp.to_json(_small_net(), str(path))
    tree = json.loads(path.read_text(encoding="utf-8"))
    change(tree)
    path.write_text(json.dumps(tree), encoding="utf-8")
    return path


def test_a_network_written_by_pandapower_loads(net_file):
    net = safe_load_net(net_file)
    assert len(net.bus) == 3 and len(net.line) == 1 and net.name == "Smedslätten test"
    assert float(net.load.p_mw.iloc[0]) == pytest.approx(0.01)
    assert net.trafo.at[0, "tap_pos"] == net.trafo.at[0, "tap_neutral"] + 1 and isinstance(net.bus.at[1, "geo"], str)


def test_a_file_naming_another_class_is_refused_before_any_import(tmp_path, no_imports):
    path = tmp_path / "net.json"
    path.write_text(json.dumps({"_module": "os", "_class": "system", "_object": "echo owned"}), encoding="utf-8")
    with pytest.raises(GridFileError, match="not a pandapower network"):
        safe_load_net(path)
    hostile = _net_with(tmp_path, lambda t: t["_object"].update(
        evil={"_module": "subprocess", "_class": "Popen", "_object": ["echo", "owned"]}))
    with pytest.raises(GridFileError, match="subprocess.Popen"):
        safe_load_net(hostile)


def test_an_object_hidden_in_a_tables_json_text_is_found(tmp_path, no_imports):
    """A table's rows are JSON text inside the file; pandapower rebuilds objects found in them too."""
    def hide(tree):
        table = tree["_object"]["controller"]
        rows = json.loads(table["_object"])
        rows["columns"] = ["object"]
        rows["index"] = [0]
        rows["data"] = [[{"_module": "builtins", "_class": "eval", "_object": "1"}]]
        table["_object"] = json.dumps(rows)

    with pytest.raises(GridFileError, match="builtins.eval"):
        safe_load_net(_net_with(tmp_path, hide))


def _hide_in_controller(cell: dict):
    """A change that puts one row, `cell`, in the object column of the controller table."""
    def hide(tree):
        table = tree["_object"]["controller"]
        rows = json.loads(table["_object"])
        rows["columns"] = ["object"]
        rows["index"] = [0]
        rows["data"] = [[cell]]
        table["_object"] = json.dumps(rows)
    return hide


SURROGATE = "\ud800"
SURROGATE_CELL = {"_module" + SURROGATE: "builtins", "_class" + SURROGATE: "print",
                  "_object" + SURROGATE: "the loader ran builtins.print"}


def test_a_key_that_only_pandas_reads_as_module_is_refused(tmp_path, no_imports, capsys):
    """Python's json keeps a lone surrogate escape ("_module\\ud800"); pandas, which parses a
    table's rows, drops it and finds exactly "_module", and pandapower then builds the object."""
    with pytest.raises(GridFileError):
        safe_load_net(_net_with(tmp_path, _hide_in_controller(SURROGATE_CELL)))
    assert "the loader ran" not in capsys.readouterr().out


def test_the_tables_are_checked_as_pandas_reads_them(tmp_path, no_imports, monkeypatch):
    """Without the surrogate and look-alike rules, walking pandas' own parse still finds the object."""
    from kapacitet.grid import netcheck

    monkeypatch.setattr(netcheck, "_SURROGATE", re.compile("(?!)"))
    monkeypatch.setattr(netcheck, "_INVISIBLE", re.compile("(?!)"))
    with pytest.raises(GridFileError, match="builtins.print"):
        safe_load_net(_net_with(tmp_path, _hide_in_controller(SURROGATE_CELL)))


@pytest.mark.parametrize("key", ["_module\x00", "​_module", "_mod﻿ule", "_module "], ids=["nul", "zwsp", "bom", "space"])
def test_a_key_disguised_as_a_reserved_one_is_refused(tmp_path, no_imports, key):
    with pytest.raises(GridFileError, match="disguised"):
        safe_load_net(_net_with(tmp_path, _hide_in_controller({key: "builtins", "_class": "print", "_object": "x"})))


@pytest.mark.parametrize("value", [["os"], {"m": "os"}, 3, None])
def test_a_module_named_by_anything_but_text_is_a_grid_file_error(value):
    """Not a TypeError from the allowlist lookup: callers catch GridFileError (a ValueError)."""
    tree = {"_module": "pandapower.auxiliary", "_class": "pandapowerNet",
            "_object": {"x": {"_module": value, "_class": "system"}}}
    with pytest.raises(GridFileError, match="module and a class"):
        check_net_json(tree)


@pytest.mark.parametrize("extra", [{"engine": "pyarrow", "lines": True}, {"compression": "gzip"}, {"typ": "series"}],
                         ids=["engine", "compression", "typ"])
def test_a_table_cannot_choose_how_pandas_reads_it(tmp_path, no_imports, extra):
    """pandapower passes a table's other keys to pd.read_json: `engine` would pick another parser."""
    with pytest.raises(GridFileError, match="never writes"):
        safe_load_net(_net_with(tmp_path, lambda t: t["_object"]["bus"].update(extra)))


def test_a_file_from_an_older_pandapower_is_still_converted(net_file, monkeypatch):
    """pp.from_json converted an older format by default; the checked load does the same."""
    seen = {}
    real = pp.from_json_string

    def spy(text, **kwargs):
        seen.update(kwargs)
        return real(text, **kwargs)

    monkeypatch.setattr(pp, "from_json_string", spy)
    safe_load_net(net_file)
    assert seen.get("convert") is True


def test_a_table_that_points_at_a_file_instead_of_holding_rows_is_refused(tmp_path):
    """pandas reads an absolute path ending in .json as a file to open: a file must hold its rows."""
    secret = tmp_path / "secret.json"
    secret.write_text('{"columns": ["x"], "index": [0], "data": [[1]]}', encoding="utf-8")

    def point(tree):
        tree["_object"]["bus"]["_object"] = str(secret)

    with pytest.raises(GridFileError, match="not JSON"):
        safe_load_net(_net_with(tmp_path, point))


@pytest.mark.parametrize("body", ["not json", "[" * 100_000 + "]" * 100_000], ids=["text", "deep"])
def test_a_file_that_is_not_json_says_so(tmp_path, body):
    path = tmp_path / "net.json"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(GridFileError):
        safe_load_net(path)


def test_the_check_walks_lists_and_nested_tables():
    good = {"_module": "pandapower.auxiliary", "_class": "pandapowerNet",
            "_object": {"bus": {"_module": "pandas.core.frame", "_class": "DataFrame", "_object": "{}"},
                        "std_types": {"line": {"x": {"r_ohm_per_km": 0.6}}}}}
    check_net_json(good)
    bad = json.loads(json.dumps(good))
    bad["_object"]["extra"] = [{"a": [{"_module": "pandas.io.pickle", "_class": "read_pickle"}]}]
    with pytest.raises(GridFileError, match="read_pickle"):
        check_net_json(bad)


@needs_grid
def test_the_built_network_loads_and_holds_only_the_allowed_objects():
    from kapacitet.grid.builder import load_net
    from kapacitet.paths import processed

    tree = json.loads(processed("grid", "net.json").read_text(encoding="utf-8"))
    check_net_json(tree)
    pairs = {(v["_module"], v["_class"]) for v in tree["_object"].values() if isinstance(v, dict) and "_module" in v}
    assert pairs <= NET_JSON_OBJECTS
    net = load_net()
    assert len(net.bus) > 0 and len(net.trafo) == 1


def test_a_network_pandapower_2_14_wrote_loads_as_pandapower_3_reads_it(no_imports):
    """The data bundles published before the move to pandapower 3 hold net.json files 2.14 wrote
    (tests/fixtures/net_pandapower_2_14.json is one, a tap above neutral and bus coordinates). The
    checked load converts them: the tap keeps its ratio through `tap_changer_type`, the coordinates
    move into the bus table's `geo`, and the flow is the one pandapower 2.14 solved."""
    from kapacitet.importer.from_pandapower import geo_point

    path = Path(__file__).parent / "fixtures" / "net_pandapower_2_14.json"
    check_net_json(json.loads(path.read_text(encoding="utf-8")), path.name)
    net = safe_load_net(path)
    assert net.trafo.at[0, "tap_changer_type"] == "Ratio" and net.trafo.at[0, "tap_pos"] == 1
    assert "bus_geodata" not in net and geo_point(net.bus.at[2, "geo"]) == (17.951, 59.331)
    pp.runpp(net, calculate_voltage_angles=False)
    # what pandapower 2.14.10 solved for the same file, before it was written
    assert net.res_bus.vm_pu.round(9).tolist() == [1.0, 0.974552166, 0.962038354]
