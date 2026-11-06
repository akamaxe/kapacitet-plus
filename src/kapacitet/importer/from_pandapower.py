"""A pandapower network, one secondary substation of it, written as the export contract.

`contract_tables(net, trafo_idx, customer_map)` takes the low-voltage island behind one
transformer and returns the contract's four tables (contract.py), ready for `validate()` and
`build_from_export()` exactly as an operator's own CSV files would be. Nothing downstream knows
the network came from pandapower: the contract stays the one way in.

The island is what an operator would draw for that station:

- the buses reachable from the transformer's LV bus without crossing a transformer, on
  `pandapower.topology.create_nxgraph(net, respect_switches=True)`;
- buses joined by a closed bus-bus switch are one bus (the contract has no switches);
- an open switch is an open point: the line behind it is left out, and so is whatever it feeds;
- out-of-service elements are left out.

Identifiers. A bus, line or load with an `origin_id` (what pandapower's CIM converter keeps,
the mRID) is written with it; otherwise with `B<index>`, `L<index>` and `C<index>`, unless the
customer map names the customer. Coordinates are written only when `net.bus["geo"]` (pandapower 3's
GeoJSON points) holds plausible WGS84 values.

`customer_map` gives, per load index, the contract's customer columns (`customer_id`, `fuse_a`,
`phase`, `footprint_m2`, `annual_kwh`). A pandapower load carries none of them; without a map the
customers table has no fuse rating, which `validate()` reports as the error it is. With a map, a
load the map does not cover is an error here, never a guess.

Electrical data are written as they are in the network, so the built network is the same one:

- a line's rating is `max_i_ka * df` (the contract has no derating factor) beside `parallel`, and
  its `std_type` is written only when the line still is that type of pandapower's library, since
  build.py takes a library type's own values over the given ones;
- the transformer's tap is written as the contract's position (steps of 2.5 % on the HV side,
  -2..2, neutral 0) that gives the same ratio, whatever the network's neutral, step and side; a
  ratio the contract cannot give is an error. As in pandapower 3, a transformer without a
  `tap_changer_type` has no tap changer, and so is at neutral.
"""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

import pandas as pd

from ..cache import memo

CUSTOMER_FIELDS = ("customer_id", "fuse_a", "phase", "footprint_m2", "annual_kwh")
# the transformer tap build.py creates: steps of this many percent on the HV side, neutral 0
CONTRACT_TAP_STEP_PCT = 2.5
CONTRACT_TAP_RANGE = (-2, 2)
LINE_VALUES = ("r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka")


class ContractError(ValueError):
    """The network cannot be written as the contract: the message says what is missing."""


def contract_tables(net, trafo_idx: int = 0,
                    customer_map: Mapping[int, Mapping[str, Any]] | pd.DataFrame | None = None,
                    graph=None) -> dict[str, pd.DataFrame]:
    """The LV island behind transformer `trafo_idx` as {buses, lines, transformer, customers}.
    `graph` is lv_graph(net), when the caller writes several transformers of one network."""
    if trafo_idx not in net.trafo.index:
        raise ContractError(f"no transformer {trafo_idx} in the network")
    trafo = net.trafo.loc[trafo_idx]
    if not bool(trafo.get("in_service", True)):
        raise ContractError(f"transformer {trafo_idx} is out of service")
    lv = int(trafo["lv_bus"])
    island = lv_island(net, trafo_idx, graph)

    rep = _fused(net, island)
    root = rep[lv]
    open_lines = _open_lines(net)

    bus_id = _ids(net.bus, "B")
    line_id = _ids(net.line, "L")

    load = net.load[net.load["in_service"].astype(bool) & net.load["bus"].isin(island)]
    service = {rep[int(b)] for b in load["bus"]}
    keep = sorted(set(rep.values()), key=lambda b: (b != root, b))
    lonlat = _lonlat(net)
    buses = pd.DataFrame({
        "bus_id": [bus_id[b] for b in keep],
        "kind": ["substation" if b == root else ("service" if b in service else "cabinet") for b in keep],
        "name": [_name(net.bus.at[b, "name"], bus_id[b]) for b in keep],
    })
    if lonlat is not None:
        buses["lon"] = [lonlat[0].get(b, math.nan) for b in keep]
        buses["lat"] = [lonlat[1].get(b, math.nan) for b in keep]

    rows = []
    near = net.line["from_bus"].isin(island) & net.line["to_bus"].isin(island)    # a region has many stations
    for idx, ln in net.line[near].iterrows():
        f, t = int(ln["from_bus"]), int(ln["to_bus"])
        if not bool(ln["in_service"]) or idx in open_lines or f not in island or t not in island:
            continue
        if rep[f] == rep[t]:
            continue
        df = ln.get("df", 1.0)
        df = float(df) if pd.notna(df) else 1.0
        rows.append({
            "line_id": line_id[idx], "from_bus": bus_id[rep[f]], "to_bus": bus_id[rep[t]],
            "length_m": round(float(ln["length_km"]) * 1000.0, 3),
            "std_type": _line_type(ln, df),
            "r_ohm_per_km": float(ln["r_ohm_per_km"]), "x_ohm_per_km": float(ln["x_ohm_per_km"]),
            "c_nf_per_km": float(ln["c_nf_per_km"]),
            "max_i_ka": float(ln["max_i_ka"]) * df,
            "parallel": int(ln.get("parallel", 1) or 1),
        })
    lines = pd.DataFrame(rows, columns=["line_id", "from_bus", "to_bus", "length_m", "std_type", "r_ohm_per_km",
                                        "x_ohm_per_km", "c_nf_per_km", "max_i_ka", "parallel"])

    transformer = pd.DataFrame([{
        "lv_bus": bus_id[root],
        "sn_mva": float(trafo["sn_mva"]),
        "vn_hv_kv": float(trafo["vn_hv_kv"]),
        "vn_lv_kv": float(trafo["vn_lv_kv"]),
        "vk_percent": float(trafo["vk_percent"]),
        "vkr_percent": float(trafo["vkr_percent"]),
        "vector_group": _vector_group(net, trafo),
        "tap_pos": contract_tap(trafo),
    }])

    customers = _customers(net, load, rep, bus_id, customer_map)
    return {"buses": buses, "lines": lines, "transformer": transformer, "customers": customers}


def lv_graph(net):
    """The network as pandapower's topology graph, switch states respected and no transformer
    crossed: what lv_island walks."""
    import pandapower.topology as top

    return top.create_nxgraph(net, respect_switches=True, include_trafos=False, include_trafo3ws=False,
                              include_impedances=False, include_dclines=False, include_tcsc=False, multi=True)


def lv_island(net, trafo_idx: int = 0, graph=None) -> set[int]:
    """The buses reachable from transformer `trafo_idx`'s LV bus without crossing a transformer or
    an open switch (out-of-service elements are not in the graph either)."""
    import networkx as nx

    graph = lv_graph(net) if graph is None else graph
    lv = int(net.trafo.at[trafo_idx, "lv_bus"])
    return set(nx.node_connected_component(graph, lv)) if lv in graph else {lv}


def transformer_id(net, trafo_idx: int = 0) -> str | None:
    """The transformer's own identifier (its mRID when the CIM converter made the network)."""
    value = net.trafo["origin_id"].get(trafo_idx) if "origin_id" in net.trafo.columns else None
    return str(value) if isinstance(value, str) and value else None


def contract_tap(trafo) -> int:
    """The contract's tap position with the transformer's voltage ratio: its offset from neutral
    in the network's own steps and side, as a whole number of 2.5 % steps on the HV side. A tap
    pandapower itself ignores (no tap changer type, position, step, neutral or side) is 0.

    pandapower 3 applies a tap only through `tap_changer_type`: "Ratio" changes the ratio, while
    "Symmetrical" and "Ideal" shift the phase as well. A network from before pandapower 3 has
    `tap_phase_shifter` instead, which pandapower still reads when the type column is missing."""
    def num(key: str) -> float:
        value = trafo.get(key)
        return float(value) if value is not None and pd.notna(value) else math.nan

    pos, neutral, step = num("tap_pos"), num("tap_neutral"), num("tap_step_percent")
    side = trafo.get("tap_side")
    typed = "tap_changer_type" in trafo.index if hasattr(trafo, "index") else "tap_changer_type" in trafo
    changer = trafo.get("tap_changer_type") if typed else None
    changer = changer if isinstance(changer, str) and changer else None
    if typed and changer is None:
        return 0                                           # no tap changer: pandapower applies no tap
    if any(math.isnan(v) for v in (pos, neutral, step)) or pos == neutral or step == 0.0 or side not in ("hv", "lv"):
        return 0                                           # at neutral, or a tap pandapower does not apply
    degree = num("tap_step_degree")
    shifts = changer != "Ratio" if typed else bool(trafo.get("tap_phase_shifter", False))
    if shifts or (not math.isnan(degree) and degree != 0.0):
        raise ContractError("the transformer's tap changer shifts the phase, which the contract cannot express")
    offset = (pos - neutral) * step / 100.0
    # a tap on the LV winding raises its voltage, which lowers the ratio
    ratio = 1.0 + offset if side == "hv" else 1.0 / (1.0 + offset)
    exact = (ratio - 1.0) * 100.0 / CONTRACT_TAP_STEP_PCT
    steps = round(exact)
    lo, hi = CONTRACT_TAP_RANGE
    if abs(exact - steps) * CONTRACT_TAP_STEP_PCT > 0.25 or not lo <= steps <= hi:
        raise ContractError(f"the transformer's tap (position {pos:g}, neutral {neutral:g}, {step:g} % per step on "
                            f"the {side} side) changes the ratio by {(ratio - 1.0) * 100.0:+.2f} %, which the "
                            f"contract's {lo}..{hi} steps of {CONTRACT_TAP_STEP_PCT:g} % cannot give")
    return int(steps)


def write_contract(tables: dict[str, pd.DataFrame], directory) -> None:
    """The four tables as the CSV files `read_export` reads."""
    from pathlib import Path

    from .contract import TABLES

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        tables[table.name].to_csv(directory / f"{table.name}.csv", index=False)


# ------------------------------------------------------------------------------- pieces
def _fused(net, island: set[int]) -> dict[int, int]:
    """Each island bus mapped to the one bus it is fused with through closed bus-bus switches
    (the smallest index of the group, so the result does not depend on the switch order)."""
    parent = {b: b for b in island}

    def find(b: int) -> int:
        while parent[b] != b:
            parent[b] = parent[parent[b]]
            b = parent[b]
        return b

    sw = net.switch
    if len(sw):
        bb = sw[(sw["et"] == "b") & sw["closed"].astype(bool)]
        for a, b in zip(bb["bus"].astype(int), bb["element"].astype(int)):
            if a in parent and b in parent:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    return {b: find(b) for b in island}


def _open_lines(net) -> set[int]:
    sw = net.switch
    if not len(sw):
        return set()
    return set(sw.loc[(sw["et"] == "l") & ~sw["closed"].astype(bool), "element"].astype(int))


def _ids(table: pd.DataFrame, prefix: str) -> dict[int, str]:
    origin = table["origin_id"] if "origin_id" in table.columns else pd.Series(None, index=table.index)
    out = {}
    for idx in table.index:
        value = origin.get(idx)
        out[int(idx)] = str(value) if isinstance(value, str) and value else f"{prefix}{int(idx)}"
    return out


def _name(value, fallback: str) -> str:
    return str(value) if isinstance(value, str) and value.strip() else fallback


def geojson_coords(value):
    """The `coordinates` of a pandapower 3 `geo` value (GeoJSON text, or the dict it parses to),
    or None when it holds none."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value.get("coordinates") if isinstance(value, dict) else None


def geo_point(value) -> tuple[float, float] | None:
    """The first finite point of a `geo` value: a Point's own, a LineString's first."""
    c = geojson_coords(value)
    if isinstance(c, (list, tuple)) and c and isinstance(c[0], (list, tuple)):
        c = c[0]
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        try:
            x, y = float(c[0]), float(c[1])
        except (TypeError, ValueError):
            return None
        if math.isfinite(x) and math.isfinite(y):
            return x, y
    return None


def point_geojson(x: float, y: float) -> str:
    """A GeoJSON Point as pandapower 3 keeps it in a `geo` column."""
    return json.dumps({"coordinates": [float(x), float(y)], "type": "Point"})


def _lonlat(net) -> tuple[dict[int, float], dict[int, float]] | None:
    if "geo" not in net.bus.columns:
        return None
    points = {int(i): geo_point(v) for i, v in net.bus["geo"].items()}
    points = {i: p for i, p in points.items() if p is not None}
    if not points:
        return None
    if not all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in points.values()):
        return None
    return {i: p[0] for i, p in points.items()}, {i: p[1] for i, p in points.items()}


@memo(maxsize=1)
def _library_lines() -> pd.DataFrame:
    """pandapower's own line types: the ones build.py looks a std_type up in."""
    import pandapower as pp

    return pp.available_std_types(pp.create_empty_network(), "line")


def _line_type(ln, df: float) -> str | None:
    """The line's std_type when build.py would rebuild it as written: a name outside pandapower's
    library (build.py then uses the values), or a library type whose values the line still has.
    A library name on a derated or edited line is left out, so its own values are used."""
    std = ln.get("std_type")
    if not isinstance(std, str) or not std:
        return None
    lib = _library_lines()
    if std not in lib.index:
        return std
    entry = lib.loc[std]
    same = df == 1.0 and all(math.isclose(float(ln[c]), float(entry[c]), rel_tol=1e-6, abs_tol=1e-9)
                             for c in LINE_VALUES)
    return std if same else None


def _vector_group(net, trafo) -> str | None:
    value = trafo.get("vector_group")
    if isinstance(value, str) and value:
        return value
    std = trafo.get("std_type")
    lib = net.std_types.get("trafo", {}) if hasattr(net, "std_types") else {}
    if isinstance(std, str) and std in lib:
        value = lib[std].get("vector_group")
        if isinstance(value, str) and value:
            return value
    return None


def _customers(net, load: pd.DataFrame, rep: dict[int, int], bus_id: dict[int, str],
               customer_map) -> pd.DataFrame:
    if isinstance(customer_map, pd.DataFrame):
        customer_map = {int(i): {k: v for k, v in row.items() if pd.notna(v)} for i, row in customer_map.iterrows()}
    load_id = _ids(net.load, "C")
    rows, missing = [], []
    for idx, ld in load.iterrows():
        idx = int(idx)
        row: dict[str, Any] = {"customer_id": load_id[idx], "bus": bus_id[rep[int(ld["bus"])]]}
        if customer_map is not None:
            given = customer_map.get(idx)
            if given is None:
                missing.append(idx)
                continue
            row.update({k: given[k] for k in CUSTOMER_FIELDS if k in given})
        rows.append(row)
    if missing:
        raise ContractError(f"{len(missing)} loads have no entry in the customer map, for example load {missing[0]}")
    columns = ["customer_id", "bus"] + [k for k in CUSTOMER_FIELDS[1:] if any(k in r for r in rows)]
    out = pd.DataFrame(rows, columns=columns)
    if "fuse_a" in out.columns and out["fuse_a"].notna().all():
        out["fuse_a"] = out["fuse_a"].astype(int)
    return out
