"""A network file is checked before pandapower builds anything from it (grid.builder.safe_load_net).

pp.from_json imports whatever module and calls whatever class a file names, and a net.json can come
from outside (a data bundle, an import). These run on a fresh clone: the files are written here.
"""

from __future__ import annotations

import importlib
import json

import pandapower as pp
import pytest

from conftest import needs_grid
from kapacitet.grid.builder import NET_JSON_OBJECTS, GridFileError, check_net_json, safe_load_net


def _small_net() -> pp.pandapowerNet:
    net = pp.create_empty_network(name="Smedslätten test")
    b0 = pp.create_bus(net, vn_kv=10.0)
    b1 = pp.create_bus(net, vn_kv=0.4)
    b2 = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, b0)
    pp.create_transformer(net, b0, b1, std_type="0.4 MVA 10/0.4 kV")
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
