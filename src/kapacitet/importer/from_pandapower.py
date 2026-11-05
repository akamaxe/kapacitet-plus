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
customer map names the customer. Coordinates are written only when `net.bus_geodata` holds plausible WGS84 values.

`customer_map` gives, per load index, the contract's customer columns (`customer_id`, `fuse_a`,
`phase`, `footprint_m2`, `annual_kwh`). A pandapower load carries none of them; without a map the
customers table has no fuse rating, which `validate()` reports as the error it is. With a map, a
load the map does not cover is an error here, never a guess.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd

CUSTOMER_FIELDS = ("customer_id", "fuse_a", "phase", "footprint_m2", "annual_kwh")


class ContractError(ValueError):
    """The network cannot be written as the contract: the message says what is missing."""


def contract_tables(net, trafo_idx: int = 0,
                    customer_map: Mapping[int, Mapping[str, Any]] | pd.DataFrame | None = None
                    ) -> dict[str, pd.DataFrame]:
    """The LV island behind transformer `trafo_idx` as {buses, lines, transformer, customers}."""
    import networkx as nx
    import pandapower.topology as top

    if trafo_idx not in net.trafo.index:
        raise ContractError(f"no transformer {trafo_idx} in the network")
    trafo = net.trafo.loc[trafo_idx]
    if not bool(trafo.get("in_service", True)):
        raise ContractError(f"transformer {trafo_idx} is out of service")
    lv = int(trafo["lv_bus"])

    graph = top.create_nxgraph(net, respect_switches=True, include_trafos=False, include_trafo3ws=False,
                               include_impedances=False, include_dclines=False, include_tcsc=False,
                               multi=True)
    island = set(nx.node_connected_component(graph, lv)) if lv in graph else {lv}

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
    for idx, ln in net.line.iterrows():
        f, t = int(ln["from_bus"]), int(ln["to_bus"])
        if not bool(ln["in_service"]) or idx in open_lines or f not in island or t not in island:
            continue
        if rep[f] == rep[t]:
            continue
        std = ln.get("std_type")
        rows.append({
            "line_id": line_id[idx], "from_bus": bus_id[rep[f]], "to_bus": bus_id[rep[t]],
            "length_m": round(float(ln["length_km"]) * 1000.0, 3),
            "std_type": std if isinstance(std, str) and std else None,
            "r_ohm_per_km": float(ln["r_ohm_per_km"]), "x_ohm_per_km": float(ln["x_ohm_per_km"]),
            "c_nf_per_km": float(ln["c_nf_per_km"]),
            "max_i_ka": float(ln["max_i_ka"]) * float(ln.get("df", 1.0) if pd.notna(ln.get("df", 1.0)) else 1.0),
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
        "tap_pos": int(trafo["tap_pos"]) if pd.notna(trafo.get("tap_pos")) else 0,
    }])

    customers = _customers(net, load, rep, bus_id, customer_map)
    return {"buses": buses, "lines": lines, "transformer": transformer, "customers": customers}


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


def _lonlat(net) -> tuple[dict[int, float], dict[int, float]] | None:
    geo = getattr(net, "bus_geodata", None)
    if geo is None or not len(geo) or not {"x", "y"} <= set(geo.columns):
        return None
    x, y = geo["x"].astype(float), geo["y"].astype(float)
    if not (x.between(-180, 180).all() and y.between(-90, 90).all()):
        return None
    return {int(i): float(v) for i, v in x.items()}, {int(i): float(v) for i, v in y.items()}


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
