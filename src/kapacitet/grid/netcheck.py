"""What a net.json may hold before pandapower builds anything from it.

pp.from_json imports any module and calls any class a file names (CWE-502), and a net.json can
arrive from outside: in a data bundle, or written by the importer from an operator's export. The
check needs neither pandapower nor the rest of the grid package, so the bundle can run it on a file
while unpacking it.

Two parsers read a net.json. pandapower reads the file itself with Python's json, but the rows of
each table are JSON text inside it that pandas parses with its own (pd.read_json, which calls
ujson_loads), and the two disagree on some strings: Python keeps a lone surrogate escape, so
"_module\\ud800" is just another key to it, while pandas drops it and finds exactly "_module". So
each table's text is checked as both parsers read it, a string holding a lone surrogate is refused
outright, and so is a key that only an invisible character keeps apart from `_module`, `_class` or
`_object`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Ours hold exactly these two (one net and its DataFrames).
NET_JSON_OBJECTS = frozenset({("pandapower.auxiliary", "pandapowerNet"), ("pandas.core.frame", "DataFrame")})
# The keys pandapower's object builder reads.
_RESERVED = frozenset({"_module", "_class", "_object"})
# pandapower hands a table's other keys to pd.read_json as keyword arguments, so `engine` or `lines`
# would switch pandas to another parser: a table may hold only what pandapower writes.
_FRAME_KEYS = _RESERVED | {"orient", "dtype", "is_multiindex", "is_multicolumn",
                           "index_name", "column_name", "index_names", "column_names"}
_SURROGATE = re.compile("[\ud800-\udfff]")
# Characters that do not show: white space, controls, surrogates, the soft hyphen, zero-width and
# direction marks, the BOM. Written as escapes, each range once: the controls leave out the ones
# \s already holds (\t to \r, \x1c to \x1f, \x85).
_INVISIBLE = re.compile(r"[\s\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f\ud800-\udfff\xad\u200b-\u200f\u202a-\u202e"
                        r"\u2060-\u2064\ufeff]")


class GridFileError(ValueError):
    """A network file that holds something other than a pandapower net and its tables."""


def _pandas_loads():
    """The parser pd.read_json uses for a table's text (pandas 2.2: pandas.io.json._json.ujson_loads)."""
    try:
        from pandas.io.json import _json
    except ImportError:         # pragma: no cover - pandas is a dependency of pandapower
        return None
    return getattr(_json, "ujson_loads", None)


def _parses(text: str, what: str, name: str) -> list:
    """`text` as Python's json and as pandas read it. Either refusing it refuses the file: pandas
    would read text that is not JSON as the path of a file to open."""
    loads = _pandas_loads()
    if loads is None:
        raise GridFileError(f"{name}: this pandas parses tables in a way the check does not know")
    try:
        return [json.loads(text), loads(text, precise_float=True)]
    except (ValueError, RecursionError, OverflowError) as err:
        raise GridFileError(f"{name} holds a {what} that is not JSON") from err


def _check_string(s: str, name: str) -> None:
    if _SURROGATE.search(s):
        raise GridFileError(f"{name} holds a string with a lone UTF-16 surrogate, which no network file needs")


def _check_key(key: str, name: str) -> None:
    _check_string(key, name)
    if key not in _RESERVED and len(key) <= 64 and _INVISIBLE.sub("", key) in _RESERVED:
        raise GridFileError(f"{name} holds a key {key!r} disguised as {_INVISIBLE.sub('', key)}")


def check_net_json(tree, name: str = "net.json") -> None:
    """Refuse a parsed net.json that names any object outside NET_JSON_OBJECTS, anywhere in it.

    pandapower hands every JSON object with `_module` and `_class` to its object builder, also
    inside the JSON text of a table (`_object`), so those texts are parsed, with Python's json and
    with pandas' parser, and walked as well.
    """
    if not (isinstance(tree, dict) and (tree.get("_module"), tree.get("_class")) == ("pandapower.auxiliary", "pandapowerNet")):
        raise GridFileError(f"{name} is not a pandapower network")
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            for v in node:
                if isinstance(v, (dict, list)):
                    stack.append(v)
                elif isinstance(v, str):
                    _check_string(v, name)
        elif isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    raise GridFileError(f"{name} holds a key that is not text")
                _check_key(k, name)
                if isinstance(v, (dict, list)):
                    stack.append(v)
                elif isinstance(v, str):
                    _check_string(v, name)
            if "_module" in node or "_class" in node:
                mod, cls = node.get("_module"), node.get("_class")
                if not (isinstance(mod, str) and isinstance(cls, str)):
                    raise GridFileError(f"{name} names an object by something other than a module and a class")
                if (mod, cls) not in NET_JSON_OBJECTS:
                    raise GridFileError(f"{name} asks for {mod}.{cls}, which a network file never holds")
                if cls == "DataFrame" and not node.keys() <= _FRAME_KEYS:
                    raise GridFileError(f"{name} holds a table with {', '.join(sorted(node.keys() - _FRAME_KEYS))}, "
                                        "which pandapower never writes")
                inner = node.get("_object")
                if isinstance(inner, str):
                    stack.extend(_parses(inner, cls, name))


def check_net_file(path: str | Path) -> str:
    """Read and check a net.json; return its text, for pandapower to build the net from."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
        tree = json.loads(text)
    except (UnicodeDecodeError, ValueError, RecursionError) as err:
        raise GridFileError(f"{path.name} is not a JSON network file") from err
    check_net_json(tree, path.name)
    return text
