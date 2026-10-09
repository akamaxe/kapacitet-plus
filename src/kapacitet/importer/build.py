"""Turn a validated network export into the tables the engine already runs on.

The target is not a new format: it is exactly what `grid/builder.py` writes for the
reference neighbourhood, so once an operator's network is in, every stage after it, the
profiles, the hosting capacity, the envelopes, the API and the map, works unchanged.

Fallbacks are applied here, never silently. Each one is counted and returned so the caller
can print it next to the capacity it produced.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandapower as pp
import pandas as pd
from pyproj import Transformer

from ..config import A, settings
from ..grid.builder import Network, save
from .validate import Report, read_export, validate

TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3006", always_xy=True)
SUBSTATION_KINDS = {"substation"}
PHASE_ALIAS = {"l1": "a", "l2": "b", "l3": "c"}


class ImportError_(ValueError):
    """The export cannot support an answer. The report says why."""


def import_directory(directory: str | Path, save_to_disk: bool = True) -> tuple[Network, Report, dict]:
    data = read_export(directory)
    report = validate(data)
    if not report.usable:
        raise ImportError_(report.text())
    network, applied = build_from_export(data)
    if save_to_disk:
        save(network)
    return network, report, applied


def build_from_export(data: dict[str, pd.DataFrame]) -> tuple[Network, dict]:
    buses_in = data["buses"].copy()
    lines_in = data["lines"].copy()
    trafo_in = data["transformer"].iloc[0]
    cust_in = data["customers"].copy()
    applied: dict[str, int] = {}

    def note(key: str, n: int) -> None:
        if n:
            applied[key] = applied.get(key, 0) + int(n)

    lv_id = str(trafo_in["lv_bus"])
    ids = [lv_id] + [b for b in buses_in["bus_id"].astype(str) if b != lv_id]
    index = {b: i + 1 for i, b in enumerate(ids)}          # 0 is reserved for the MV bus

    net = pp.create_empty_network()
    vn_lv = float(trafo_in["vn_lv_kv"])
    vn_hv = float(trafo_in["vn_hv_kv"])
    pp.create_bus(net, vn_kv=vn_hv, name="MV")
    pp.create_ext_grid(net, 0, vm_pu=1.0, s_sc_max_mva=A("mv_short_circuit_mva"),
                       rx_max=0.1, x0x_max=1.0, r0x0_max=0.1)
    by_id = buses_in.set_index(buses_in["bus_id"].astype(str))
    for b in ids:
        row = by_id.loc[b]
        pp.create_bus(net, vn_kv=vn_lv, name=str(row.get("name") or b), type="n")
    pp.create_transformer_from_parameters(
        net, 0, index[lv_id], sn_mva=float(trafo_in["sn_mva"]), vn_hv_kv=vn_hv, vn_lv_kv=vn_lv,
        vk_percent=_get(trafo_in, "vk_percent", 4.0, note, "transformer vk assumed"),
        vkr_percent=_get(trafo_in, "vkr_percent", 1.325, note, "transformer vkr assumed"),
        pfe_kw=0.95, i0_percent=0.2375, shift_degree=150, name="T1",
        tap_side="hv", tap_neutral=0, tap_min=-2, tap_max=2, tap_step_percent=2.5,
        tap_pos=int(trafo_in.get("tap_pos") or 0))

    line_rows = _create_lines(net, lines_in, index, note)
    lines = pd.DataFrame(line_rows).sort_values("line").reset_index(drop=True)
    feeder, depth, parent = _walk(index[lv_id], lines)

    bus_rows = _bus_table(buses_in, index, lv_id, feeder, depth, parent, note)
    buses = pd.DataFrame(bus_rows).sort_values("bus").reset_index(drop=True)
    for row in line_rows:
        row["feeder"] = feeder.get(row["to_bus"], 0)
    lines = pd.DataFrame(line_rows).sort_values("line").reset_index(drop=True)

    customers = _customer_table(net, cust_in, index, buses, feeder, depth, note)
    lines["downstream"] = [
        int(((customers["dist_m"] >= 0) & _downstream_mask(customers, lines, r.line, parent)).sum())
        for r in lines.itertuples()
    ]

    summary = {
        "site": "imported",
        "customers": int(len(customers)),
        "buildings": int(len(customers)),
        "feeders": int(len({f for f in feeder.values() if f >= 0})),
        "cabinets": int((buses["kind"] == "cabinet").sum()),
        "junctions": 0,
        "buses": int(len(net.bus)),
        "lines": int(len(net.line)),
        "main_cable_km": round(float(lines["length_m"].sum()) / 1000.0, 3),
        "service_cable_km": 0.0,
        "max_network_distance_m": round(float(customers["dist_m"].max()), 1) if len(customers) else 0.0,
        "trafo_type": f"{float(trafo_in['sn_mva'])} MVA {vn_hv:g}/{vn_lv:g} kV",
        "velander_peak_kw": 0.0,
        "root_real_position": None,
        "imported": True,
        "fallbacks": applied,
    }
    substation = {
        "osm_id": "", "real_position": None, "address": "",
        "lon": float(buses.loc[buses["bus"] == index[lv_id], "lon"].iloc[0]),
        "lat": float(buses.loc[buses["bus"] == index[lv_id], "lat"].iloc[0]),
        "polygon": None, "trafo_type": summary["trafo_type"],
        "trafo_sn_mva": float(trafo_in["sn_mva"]), "velander_peak_kw": 0.0,
        "other_mapped_substations": [],
    }
    return Network(net=net, buses=buses, lines=lines, customers=customers, context=[],
                   substation=substation, summary=summary), applied


# ------------------------------------------------------------------------------- pieces
def _get(row, key, default, note, label):
    value = row.get(key)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        note(label, 1)
        return default
    return float(value)


def _create_lines(net, lines_in, index, note) -> list[dict]:
    default_type = A("service_cable_type")
    rows = []
    electrical = ("r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka")
    for r in lines_in.itertuples():
        f, t = index[str(r.from_bus)], index[str(r.to_bus)]
        km = float(r.length_m) / 1000.0
        parallel = int(getattr(r, "parallel", 1) or 1)
        std = getattr(r, "std_type", None)
        values = {c: getattr(r, c, None) for c in electrical}
        complete = all(v is not None and not (isinstance(v, float) and math.isnan(v))
                       for v in values.values())
        if isinstance(std, str) and std in pp.available_std_types(net, "line").index:
            li = pp.create_line(net, f, t, length_km=km, std_type=std, parallel=parallel)
            used = std
        elif complete:
            li = pp.create_line_from_parameters(
                net, f, t, length_km=km, parallel=parallel,
                r_ohm_per_km=float(values["r_ohm_per_km"]), x_ohm_per_km=float(values["x_ohm_per_km"]),
                c_nf_per_km=float(values["c_nf_per_km"]), max_i_ka=float(values["max_i_ka"]))
            used = "from parameters"
        else:
            note("cable type assumed", 1)
            li = pp.create_line(net, f, t, length_km=km, std_type=default_type, parallel=parallel)
            used = default_type
        rows.append(dict(line=li, from_bus=f, to_bus=t, kind="main", std_type=used,
                         length_m=float(r.length_m), parallel=parallel, feeder=0,
                         downstream=0, coords=None))
    return rows


def _walk(root: int, lines: pd.DataFrame):
    """Feeder number, distance from the substation and parent bus, by walking the tree."""
    children: dict[int, list[tuple[int, float]]] = {}
    for r in lines.itertuples():
        children.setdefault(r.from_bus, []).append((r.to_bus, r.length_m))
        children.setdefault(r.to_bus, []).append((r.from_bus, r.length_m))
    feeder, depth, parent = {root: -1}, {root: 0.0}, {root: -1}
    for fid, (first, length) in enumerate(children.get(root, [])):
        stack = [(first, root, length)]
        while stack:
            node, came, dist = stack.pop()
            if node in depth:
                continue
            feeder[node], depth[node], parent[node] = fid, dist, came
            for nxt, step in children.get(node, []):
                if nxt != came and nxt not in depth:
                    stack.append((nxt, node, dist + step))
    return feeder, depth, parent


def _bus_table(buses_in, index, lv_id, feeder, depth, parent, note) -> list[dict]:
    rows = []
    missing_xy = 0
    for r in buses_in.itertuples():
        bid = str(r.bus_id)
        i = index[bid]
        lon, lat = getattr(r, "lon", None), getattr(r, "lat", None)
        if lon is None or lat is None or (isinstance(lon, float) and math.isnan(lon)):
            lon = lat = float("nan")
            missing_xy += 1
            x = y = float("nan")
        else:
            x, y = TO_M.transform(float(lon), float(lat))
        kind = str(getattr(r, "kind", "cabinet") or "cabinet")
        rows.append(dict(bus=i, name=str(getattr(r, "name", None) or bid),
                         kind="lv_busbar" if bid == lv_id else
                              ("customer" if kind == "service" else "cabinet"),
                         x=x, y=y, lon=lon, lat=lat, feeder=feeder.get(i, -1),
                         dist_m=depth.get(i, 0.0), parent_bus=parent.get(i, -1)))
    note("bus without coordinates", missing_xy)
    rows.append(dict(bus=0, name="MV", kind="mv", x=float("nan"), y=float("nan"),
                     lon=float("nan"), lat=float("nan"), feeder=-1, dist_m=0.0, parent_bus=-1))
    return rows


def _customer_table(net, cust_in, index, buses, feeder, depth, note) -> pd.DataFrame:
    levels = A("house_levels_default")
    by_bus = buses.set_index("bus")
    rows = []
    no_roof = 0
    for cid, r in enumerate(cust_in.itertuples()):
        bus = index[str(r.bus)]
        pp.create_load(net, bus, p_mw=0.0, q_mvar=0.0, name=f"load C{cid}")
        pp.create_sgen(net, bus, p_mw=0.0, q_mvar=0.0, name=f"pv C{cid}")
        footprint = getattr(r, "footprint_m2", None)
        if footprint is None or (isinstance(footprint, float) and math.isnan(footprint)):
            footprint = 0.0
            no_roof += 1
        fuse = int(r.fuse_a)
        b = by_bus.loc[bus]
        rows.append(dict(
            cid=cid, bus=bus, osm_id=str(r.customer_id), building="imported", address="",
            unit=0, dwellings=1, footprint_m2=float(footprint), levels=float(levels),
            floor_m2=float(footprint) * float(levels), fuse_a=fuse,
            fuse_kva=round(3 * 230 * fuse / 1000.0, 2), lon=float(b["lon"]), lat=float(b["lat"]),
            x=float(b["x"]), y=float(b["y"]), feeder=int(feeder.get(bus, 0)),
            dist_m=round(float(depth.get(bus, 0.0)), 1),
            roof_shape="", roof_orientation="", polygon=None))
    note("customer without a roof area", no_roof)
    return pd.DataFrame(rows)


def _downstream_mask(customers: pd.DataFrame, lines: pd.DataFrame, line_id: int, parent: dict):
    """Customers fed through one section, found by walking up from each of them."""
    to_bus = int(lines.loc[lines["line"] == line_id, "to_bus"].iloc[0])
    out = np.zeros(len(customers), dtype=bool)
    for i, bus in enumerate(customers["bus"].to_numpy()):
        node = int(bus)
        while node in parent and node != -1:
            if node == to_bus:
                out[i] = True
                break
            node = parent[node]
    return pd.Series(out, index=customers.index)
