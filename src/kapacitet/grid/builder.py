"""Build a synthetic but realistic radial LV network on real OSM buildings and streets.

Steps
1. Project OSM buildings/streets to SWEREF 99 TM (EPSG:3006) and keep dwellings.
2. Street graph (networkx); each house is tapped onto its nearest street segment.
3. Root = the OSM-mapped nätstation (real position). Houses are assigned to it when it is the
   closest mapped substation along the street network (Voronoi cell), capped by house count
   and network distance.
4. Shortest-path tree from the root = radial feeders along the streets.
5. Cable cabinets (kabelskåp) group nearby services; mains run cabinet to cabinet.
6. Cables and the transformer are sized with Velander's formula and pandapower standard types.

Topology, cables, cabinets and fuses are synthetic; positions of buildings, streets and the
substation are real.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandapower as pp
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points
from shapely.strtree import STRtree

from ..config import A, settings
from ..ingest.osm import read_osm
from ..paths import processed

log = logging.getLogger(__name__)

TO_M = Transformer.from_crs(4326, 3006, always_xy=True)
TO_LL = Transformer.from_crs(3006, 4326, always_xy=True)

DWELLING_TAGS = {"house", "detached", "semi", "semidetached_house", "terrace", "villa", "bungalow"}
AMBIGUOUS_TAGS = {"yes", "residential"}
NON_RESIDENTIAL_HINTS = {"amenity", "shop", "office", "leisure", "industrial", "man_made", "power", "craft"}
ROAD_WEIGHT = {
    "residential": 1.0, "living_street": 1.0, "unclassified": 1.0, "tertiary": 1.05, "secondary": 1.1,
    "primary": 1.3, "service": 1.1, "pedestrian": 1.2, "footway": 1.25, "cycleway": 1.25, "path": 1.3,
    "track": 1.3, "tertiary_link": 1.1, "secondary_link": 1.2,
}
MAX_TAP_DISTANCE_M = 80.0


# ------------------------------------------------------------------------------------ OSM parsing

@dataclass
class Building:
    osm_id: str
    tag: str
    poly: Polygon
    tags: dict
    levels: float | None = None

    @property
    def area(self) -> float:
        return float(self.poly.area)

    @property
    def address(self) -> str:
        street, num = self.tags.get("addr:street"), self.tags.get("addr:housenumber")
        return f"{street} {num}" if street and num else (street or "")


def _project(geom: list[dict]) -> list[tuple[float, float]]:
    xs, ys = TO_M.transform([p["lon"] for p in geom], [p["lat"] for p in geom])
    return list(zip(xs, ys))


def _to_ll(coords) -> list[list[float]]:
    xs, ys = zip(*coords)
    lons, lats = TO_LL.transform(xs, ys)
    return [[round(a, 7), round(b, 7)] for a, b in zip(lons, lats)]


def parse_osm(data: dict) -> tuple[list[Building], list[Building], list[dict], list[dict]]:
    """Returns (dwellings, other_buildings, roads, substations) in EPSG:3006."""
    dwellings, others, roads, subs = [], [], [], []
    for e in data["elements"]:
        tags = e.get("tags", {})
        geom = e.get("geometry")
        if e["type"] == "way" and "building" in tags and geom and len(geom) >= 4:
            poly = Polygon(_project(geom))
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.area < 10:
                continue
            levels = None
            try:
                levels = float(tags.get("building:levels")) if tags.get("building:levels") else None
            except ValueError:
                levels = None
            b = Building(osm_id=f"way/{e['id']}", tag=tags["building"], poly=poly, tags=tags, levels=levels)
            is_dwelling = b.tag in DWELLING_TAGS or (
                b.tag in AMBIGUOUS_TAGS and 60 <= b.area <= 350 and not (NON_RESIDENTIAL_HINTS & tags.keys())
            )
            if tags.get("power"):
                is_dwelling = False
            (dwellings if is_dwelling else others).append(b)
        if e["type"] == "way" and tags.get("highway") in ROAD_WEIGHT and geom and len(geom) >= 2:
            roads.append({"id": f"way/{e['id']}", "highway": tags["highway"], "coords": _project(geom),
                          "name": tags.get("name", "")})
        if tags.get("power") == "substation" and tags.get("substation", "minor_distribution") == "minor_distribution":
            if "lat" in e:
                c = TO_M.transform(e["lon"], e["lat"])
                poly = None
            elif geom:
                pts = _project(geom)
                poly = Polygon(pts) if len(pts) >= 4 else None
                c = (poly.centroid.x, poly.centroid.y) if poly is not None else pts[0]
            else:
                continue
            subs.append({"osm_id": f"{e['type']}/{e['id']}", "xy": c, "poly": poly, "tags": tags})
    return dwellings, others, roads, subs


def dwellings_in(b: Building) -> int:
    """Number of dwellings (customers) in a building (team assumption, see README)."""
    if b.tag == "terrace":
        return int(min(8, max(1, round(b.area / 80.0))))
    if b.tag in ("semi", "semidetached_house"):
        return 2 if b.area >= 150 else 1
    return 1


# ------------------------------------------------------------------------------------ street graph

def _key(x: float, y: float) -> tuple[int, int]:
    return (int(round(x * 100)), int(round(y * 100)))  # 1 cm grid merges shared OSM nodes


def street_graph(roads: list[dict]) -> nx.Graph:
    g = nx.Graph()
    ids: dict[tuple[int, int], int] = {}

    def node(x, y):
        k = _key(x, y)
        if k not in ids:
            ids[k] = len(ids)
            g.add_node(ids[k], x=x, y=y)
        return ids[k]

    for r in roads:
        w = ROAD_WEIGHT[r["highway"]]
        pts = r["coords"]
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            a, b = node(x0, y0), node(x1, y1)
            if a == b:
                continue
            length = math.hypot(x1 - x0, y1 - y0)
            if g.has_edge(a, b) and g[a][b]["length"] <= length:
                continue
            g.add_edge(a, b, length=length, weight=length * w, highway=r["highway"])
    return g


@dataclass
class Tap:
    obj: object          # Building or substation dict
    point: tuple[float, float]
    edge: tuple[int, int]
    s: float             # position along the edge from edge[0]
    dist: float          # straight distance from the object to the street
    node: int | None = None


def insert_taps(g: nx.Graph, objects: list[tuple[object, object]]) -> list[Tap]:
    """Tap objects (geometry, payload) onto their nearest street segment, splitting edges."""
    edges = list(g.edges())
    lines = [LineString([(g.nodes[u]["x"], g.nodes[u]["y"]), (g.nodes[v]["x"], g.nodes[v]["y"])]) for u, v in edges]
    tree = STRtree(lines)
    taps: list[Tap] = []
    for geom, payload in objects:
        probe = geom.centroid if hasattr(geom, "centroid") else geom
        idx = int(tree.nearest(probe))
        line = lines[idx]
        d = float(line.distance(geom))
        if d > MAX_TAP_DISTANCE_M:
            continue
        s = float(line.project(probe))
        p = line.interpolate(s)
        taps.append(Tap(obj=payload, point=(p.x, p.y), edge=edges[idx], s=s, dist=d))

    by_edge: dict[tuple[int, int], list[Tap]] = defaultdict(list)
    for t in taps:
        by_edge[t.edge].append(t)
    next_id = max(g.nodes) + 1
    for (u, v), group in by_edge.items():
        data = g.get_edge_data(u, v)
        length, w, hw = data["length"], data["weight"] / max(data["length"], 1e-9), data["highway"]
        group.sort(key=lambda t: t.s)
        chain = [u]
        positions = [0.0]
        for t in group:
            if t.s < 0.5:
                t.node = u
            elif t.s > length - 0.5:
                t.node = v
            elif positions[-1] > 0 and abs(t.s - positions[-1]) < 0.5:
                t.node = chain[-1]
            else:
                g.add_node(next_id, x=t.point[0], y=t.point[1])
                t.node = next_id
                chain.append(next_id)
                positions.append(t.s)
                next_id += 1
        if len(chain) > 1:
            g.remove_edge(u, v)
            chain.append(v)
            positions.append(length)
            for a, b, sa, sb in zip(chain[:-1], chain[1:], positions[:-1], positions[1:]):
                seg = max(sb - sa, 0.01)
                g.add_edge(a, b, length=seg, weight=seg * w, highway=hw)
    return taps


# ------------------------------------------------------------------------------------ design helpers

def velander_kw(energy_kwh: float) -> float:
    """Velander (1952) peak power for a customer group with annual energy W (kWh)."""
    return A("velander_k1") * energy_kwh + A("velander_k2") * math.sqrt(max(energy_kwh, 0.0))


def fuse_for(floor_m2: float) -> int:
    lo, hi = A("fuse_thresholds_m2")
    return 16 if floor_m2 < lo else (20 if floor_m2 < hi else 25)


def line_imax_ka(std_type: str) -> float:
    net = pp.create_empty_network()
    return float(pp.load_std_type(net, std_type, "line")["max_i_ka"])


def trafo_sn_mva(std_type: str) -> float:
    net = pp.create_empty_network()
    return float(pp.load_std_type(net, std_type, "trafo")["sn_mva"])


# ------------------------------------------------------------------------------------ network build

@dataclass
class Network:
    net: pp.pandapowerNet
    buses: pd.DataFrame
    lines: pd.DataFrame
    customers: pd.DataFrame
    context: list[dict]
    substation: dict
    summary: dict = field(default_factory=dict)


def build() -> Network:
    cfg = settings()
    site = cfg.site
    dwellings, others, roads, subs = parse_osm(read_osm())
    log.info("OSM: %d dwellings, %d other buildings, %d road ways, %d mapped substations",
             len(dwellings), len(others), len(roads), len(subs))
    g = street_graph(roads)

    # Root: pinned OSM substation, else the nearest mapped one, else 1-median (synthetic).
    center = Point(*TO_M.transform(site.center_lon, site.center_lat))
    root_sub = None
    if site.substation_osm:
        root_sub = next((s for s in subs if s["osm_id"] == site.substation_osm), None)
    if root_sub is None and subs:
        near = min(subs, key=lambda s: Point(s["xy"]).distance(center))
        if Point(near["xy"]).distance(center) < 300:
            root_sub = near
    objects = [(b.poly, b) for b in dwellings] + [(Point(s["xy"]), s) for s in subs]
    taps = insert_taps(g, objects)
    house_taps = [t for t in taps if isinstance(t.obj, Building)]
    sub_taps = {t.obj["osm_id"]: t for t in taps if isinstance(t.obj, dict)}

    if root_sub is not None:
        root = sub_taps[root_sub["osm_id"]].node
        root_real = True
        root_xy = root_sub["xy"]
    else:
        # 1-median of the houses near the centre on the street graph (synthetic position).
        near_taps = sorted(house_taps, key=lambda t: Point(t.point).distance(center))[: site.max_houses]
        comp = nx.node_connected_component(g, near_taps[0].node)
        cand = [t.node for t in near_taps if t.node in comp]
        best, best_cost = None, math.inf
        for c in set(cand):
            dist = nx.single_source_dijkstra_path_length(g, c, weight="length")
            cost = sum(dist.get(n, 1e6) for n in cand)
            if cost < best_cost:
                best, best_cost = c, cost
        root = best
        root_real = False
        root_xy = (g.nodes[root]["x"], g.nodes[root]["y"])

    comp = nx.node_connected_component(g, root)
    dist_root = nx.single_source_dijkstra_path_length(g, root, weight="length")
    # Voronoi cell against the other mapped substations (street-network distance).
    other_dist = []
    for oid, t in sub_taps.items():
        if t.node == root or t.node not in comp:
            continue
        other_dist.append(nx.single_source_dijkstra_path_length(g, t.node, weight="length"))

    cand = []
    for t in house_taps:
        if t.node not in dist_root:
            continue
        d = dist_root[t.node] + t.dist
        if d > site.max_network_distance_m:
            continue
        if any(od.get(t.node, math.inf) + t.dist < d for od in other_dist):
            continue
        cand.append((d, t))
    cand.sort(key=lambda x: x[0])
    selected: list[Tap] = []
    n_dw = 0
    for d, t in cand:
        k = dwellings_in(t.obj)
        if n_dw + k > site.max_houses:
            break
        selected.append(t)
        n_dw += k
    log.info("Cell: %d buildings / %d dwellings selected (of %d candidates)", len(selected), n_dw, len(cand))

    # Radial tree = union of shortest paths from the root (street-weighted).
    _, paths = nx.single_source_dijkstra(g, root, weight="weight")
    tree = nx.Graph()
    tree.add_node(root)
    for t in selected:
        p = paths[t.node]
        nx.add_path(tree, p)
    for u, v in tree.edges():
        tree[u][v]["length"] = g[u][v]["length"]
    assert nx.is_tree(tree), "shortest-path union must be a tree"
    parent = {root: None}
    order = list(nx.bfs_tree(tree, root))
    for u, v in nx.bfs_edges(tree, root):
        parent[v] = u
    depth = {root: 0.0}
    for n in order[1:]:
        depth[n] = depth[parent[n]] + tree[parent[n]][n]["length"]

    # Customers per tap node.
    at_node: dict[int, list[Tap]] = defaultdict(list)
    for t in selected:
        at_node[t.node].append(t)

    # Cable cabinets: greedy grouping of taps onto an upstream cabinet within reach.
    reach, max_srv = A("cabinet_reach_m"), A("cabinet_max_services")
    cabinets: dict[int, int] = {}  # tree node -> services
    cab_of: dict[int, int] = {}
    for node in sorted(at_node, key=lambda n: depth[n]):
        n_cust = sum(dwellings_in(t.obj) for t in at_node[node])
        best = None
        a = node
        while a is not None and depth[node] - depth[a] <= reach:
            if a in cabinets and cabinets[a] + n_cust <= max_srv:
                best = a
                break
            a = parent[a]
        if best is None:
            best = node
            cabinets[best] = 0
        cabinets[best] += n_cust
        cab_of[node] = best

    # Feeder partition: every feeder is a separate cable from the busbar (feeders may share a
    # trench near the substation). Subtrees are cut so that no feeder exceeds the customer cap.
    cab_customers: dict[int, int] = defaultdict(int)
    for node, lst in at_node.items():
        cab_customers[cab_of[node]] += sum(dwellings_in(t.obj) for t in lst)
    children: dict[int, list[int]] = defaultdict(list)
    for n in order[1:]:
        children[parent[n]].append(n)
    sub_cnt: dict[int, int] = {}
    for n in reversed(order):
        sub_cnt[n] = cab_customers.get(n, 0) + sum(sub_cnt[c] for c in children[n])
    cap = A("feeder_max_customers")
    tops: list[tuple[int, bool]] = []  # (top node, whole subtree?)
    stack = list(reversed(children[root]))
    while stack:
        n = stack.pop()
        if sub_cnt[n] == 0:
            continue
        if sub_cnt[n] <= cap:
            tops.append((n, True))
            continue
        if cab_customers.get(n, 0) > 0:
            tops.append((n, False))  # the cabinet at n becomes a feeder of its own
        stack.extend(reversed(children[n]))
    rx, ry = g.nodes[root]["x"], g.nodes[root]["y"]
    tops.sort(key=lambda tb: (math.atan2(g.nodes[tb[0]]["y"] - ry, g.nodes[tb[0]]["x"] - rx), depth[tb[0]]))
    feeder_of_cab: dict[int, int] = {}
    for fid, (top, whole) in enumerate(tops):
        if whole:
            todo = [top]
            while todo:
                n = todo.pop()
                if n in cabinets and n not in feeder_of_cab:
                    feeder_of_cab[n] = fid
                todo.extend(children[n])
        else:
            feeder_of_cab[top] = fid
    if root in cabinets:
        feeder_of_cab[root] = -1  # services fed directly from the busbar

    # Electrical nodes per feeder: its cabinets plus branch points of the union of their paths.
    elec: dict[int, list[tuple[int, int | None, list[int], int]]] = {}  # fid -> [(node, parent, path, down)]
    for fid in range(len(tops)):
        members = [c for c, f in feeder_of_cab.items() if f == fid]
        in_tf: dict[int, int] = defaultdict(int)  # node -> customers of members below (in T_f)
        for c in members:
            x = c
            while x is not None:
                in_tf[x] += cab_customers[c]
                x = parent[x]
        kids_tf: dict[int, int] = defaultdict(int)
        for n in in_tf:
            if parent[n] is not None:
                kids_tf[parent[n]] += 1
        nodes = [n for n in order if n in in_tf and n != root and (n in members or kids_tf[n] >= 2)]
        node_set = set(nodes)
        rows = []
        for n in nodes:
            path = [n]
            a = parent[n]
            while a != root and a not in node_set:
                path.append(a)
                a = parent[a]
            path.append(a)
            path.reverse()
            rows.append((n, None if a == root else a, path, in_tf[n]))
        elec[fid] = rows

    # -------------------------------------------------------------- pandapower network
    household_kwh = A("household_kwh_per_year")
    cosphi = A("load_power_factor")
    main_type, serv_type = A("main_cable_type"), A("service_cable_type")
    imax_main = line_imax_ka(main_type)
    p_total = velander_kw(n_dw * household_kwh)
    s_total = p_total / cosphi / 1000.0  # MVA
    trafo_type = next((t for t in A("trafo_types") if trafo_sn_mva(t) >= A("trafo_design_margin") * s_total),
                      A("trafo_types")[-1])

    net = pp.create_empty_network(name=f"Kapacitet+ {site.name}", f_hz=50.0, sn_mva=1.0)
    b_mv = pp.create_bus(net, vn_kv=10.0, name="MV 10 kV")
    pp.create_ext_grid(net, b_mv, vm_pu=A("mv_voltage_pu"), s_sc_max_mva=A("mv_short_circuit_mva"),
                       rx_max=0.1, s_sc_min_mva=A("mv_short_circuit_mva") * 0.8, rx_min=0.1)
    b_lv = pp.create_bus(net, vn_kv=0.4, name="LV busbar")
    pp.create_transformer(net, b_mv, b_lv, std_type=trafo_type, name="T1", tap_pos=A("trafo_tap_pos"))

    bus_rows = []
    sub_ll = _to_ll([root_xy])[0]
    bus_rows.append(dict(bus=b_mv, name="MV 10 kV", kind="mv", x=root_xy[0], y=root_xy[1], lon=sub_ll[0],
                         lat=sub_ll[1], feeder=-1, dist_m=0.0, parent_bus=-1))
    root_ll = _to_ll([(rx, ry)])[0]
    bus_rows.append(dict(bus=b_lv, name="LV busbar", kind="lv_busbar", x=rx, y=ry, lon=root_ll[0], lat=root_ll[1],
                         feeder=-1, dist_m=0.0, parent_bus=b_mv))
    bus_of: dict[tuple[int, int], int] = {}
    line_rows = []
    for fid, rows in elec.items():
        for n, a, path, n_down in rows:
            kind = "cabinet" if n in cabinets else "junction"
            xy = (g.nodes[n]["x"], g.nodes[n]["y"])
            ll = _to_ll([xy])[0]
            b = pp.create_bus(net, vn_kv=0.4, name=f"F{fid + 1}-{kind[:3].upper()}-{n}", type="n")
            bus_of[(fid, n)] = b
            from_bus = b_lv if a is None else bus_of[(fid, a)]
            bus_rows.append(dict(bus=b, name=f"F{fid + 1} {kind} {n}", kind=kind, x=xy[0], y=xy[1], lon=ll[0],
                                 lat=ll[1], feeder=fid, dist_m=depth[n], parent_bus=from_bus))
            length_m = max(depth[n] - (0.0 if a is None else depth[a]), 1.0)
            p_down = velander_kw(n_down * household_kwh)
            i_down = p_down / (math.sqrt(3) * 0.4 * cosphi) / 1000.0  # kA
            parallel = max(1, math.ceil(i_down / (A("cable_design_utilisation") * imax_main)))
            coords = [(g.nodes[p]["x"], g.nodes[p]["y"]) for p in path]
            li = pp.create_line(net, from_bus, b, length_km=length_m / 1000.0, std_type=main_type,
                                name=f"F{fid + 1} main to {n}", parallel=parallel)
            line_rows.append(dict(line=li, from_bus=from_bus, to_bus=b, kind="main", std_type=main_type,
                                  length_m=length_m, parallel=parallel, feeder=fid, downstream=n_down,
                                  coords=json.dumps(_to_ll(coords))))

    cust_rows = []
    cid = 0
    for node, lst in sorted(at_node.items(), key=lambda kv: depth[kv[0]]):
        cab = cab_of[node]
        feeder = feeder_of_cab[cab]
        cab_bus = b_lv if cab == root else bus_of[(feeder, cab)]
        # path along the street from the cabinet to the tap
        street = [node]
        a = node
        while a != cab:
            a = parent[a]
            street.append(a)
        street.reverse()
        street_len = depth[node] - depth[cab]
        for t in lst:
            b: Building = t.obj
            facade, _ = nearest_points(b.poly.exterior, Point(t.point))
            stub = facade.distance(Point(t.point))
            length_m = max(street_len + stub, 5.0)
            k = dwellings_in(b)
            levels = b.levels or A("house_levels_default")
            floor = b.area / k * levels
            hb = pp.create_bus(net, vn_kv=0.4, name=f"C-{b.osm_id}", type="n")
            coords = [(g.nodes[p]["x"], g.nodes[p]["y"]) for p in street] + [(facade.x, facade.y)]
            li = pp.create_line(net, cab_bus, hb, length_km=length_m / 1000.0, std_type=serv_type,
                                name=f"service {b.osm_id}")
            line_rows.append(dict(line=li, from_bus=cab_bus, to_bus=hb, kind="service", std_type=serv_type,
                                  length_m=length_m, parallel=1, feeder=feeder, downstream=k,
                                  coords=json.dumps(_to_ll(coords))))
            hc = b.poly.centroid
            hll = _to_ll([(hc.x, hc.y)])[0]
            bus_rows.append(dict(bus=hb, name=f"house {b.osm_id}", kind="customer", x=hc.x, y=hc.y, lon=hll[0],
                                 lat=hll[1], feeder=feeder, dist_m=depth[node] + stub, parent_bus=cab_bus))
            for j in range(k):
                fuse = fuse_for(floor)
                pp.create_load(net, hb, p_mw=0.0, q_mvar=0.0, name=f"load C{cid}")
                pp.create_sgen(net, hb, p_mw=0.0, q_mvar=0.0, name=f"pv C{cid}")
                cust_rows.append(dict(
                    cid=cid, bus=hb, osm_id=b.osm_id, building=b.tag, address=b.address,
                    unit=j, dwellings=k, footprint_m2=round(b.area / k, 1), levels=levels,
                    floor_m2=round(floor, 1), fuse_a=fuse, fuse_kva=round(3 * 230 * fuse / 1000.0, 2),
                    lon=hll[0], lat=hll[1], x=hc.x, y=hc.y, feeder=feeder, dist_m=round(depth[node] + stub, 1),
                    roof_shape=b.tags.get("roof:shape", ""), roof_orientation=b.tags.get("roof:orientation", ""),
                    polygon=json.dumps(_to_ll(list(b.poly.exterior.coords))),
                ))
                cid += 1

    buses = pd.DataFrame(bus_rows).sort_values("bus").reset_index(drop=True)
    lines = pd.DataFrame(line_rows).sort_values("line").reset_index(drop=True)
    customers = pd.DataFrame(cust_rows)

    # Context: nearby buildings (not part of the modelled cell), for the map only.
    selected_ids = {t.obj.osm_id for t in selected}
    hull = Point(rx, ry).buffer(site.max_network_distance_m * 0.8)
    context = []
    for b in dwellings + others:
        if b.osm_id in selected_ids or not hull.intersects(b.poly):
            continue
        context.append({"osm_id": b.osm_id, "building": b.tag,
                        "polygon": _to_ll(list(b.poly.exterior.coords))})

    substation = {
        "osm_id": root_sub["osm_id"] if root_sub else "",
        "real_position": root_real,
        "address": (f"{root_sub['tags'].get('addr:street', '')} {root_sub['tags'].get('addr:housenumber', '')}".strip()
                    if root_sub else ""),
        "lon": sub_ll[0], "lat": sub_ll[1],
        "polygon": _to_ll(list(root_sub["poly"].exterior.coords)) if root_sub and root_sub["poly"] is not None else None,
        "trafo_type": trafo_type,
        "trafo_sn_mva": trafo_sn_mva(trafo_type),
        "velander_peak_kw": round(p_total, 1),
        "other_mapped_substations": [
            {"osm_id": s["osm_id"], "lonlat": _to_ll([s["xy"]])[0]} for s in subs
            if not root_sub or s["osm_id"] != root_sub["osm_id"]
        ],
    }
    summary = {
        "site": site.name,
        "customers": int(len(customers)),
        "buildings": int(len(selected)),
        "feeders": int(len(tops)),
        "cabinets": int(len(cabinets)),
        "junctions": int(sum(1 for r in bus_rows if r["kind"] == "junction")),
        "buses": int(len(net.bus)),
        "lines": int(len(net.line)),
        "main_cable_km": round(float(lines.loc[lines.kind == "main", "length_m"].sum()) / 1000.0, 3),
        "service_cable_km": round(float(lines.loc[lines.kind == "service", "length_m"].sum()) / 1000.0, 3),
        "max_network_distance_m": round(float(customers["dist_m"].max()), 1) if len(customers) else 0.0,
        "trafo_type": trafo_type,
        "velander_peak_kw": round(p_total, 1),
        "root_real_position": root_real,
    }
    log.info("Network: %s", summary)
    return Network(net=net, buses=buses, lines=lines, customers=customers, context=context,
                   substation=substation, summary=summary)


def save(nw: Network) -> None:
    pp.to_json(nw.net, str(processed("grid", "net.json")))
    nw.buses.to_parquet(processed("grid", "buses.parquet"), index=False)
    nw.lines.to_parquet(processed("grid", "lines.parquet"), index=False)
    nw.customers.to_parquet(processed("grid", "customers.parquet"), index=False)
    processed("grid", "context.json").write_text(json.dumps(nw.context), encoding="utf-8")
    processed("grid", "substation.json").write_text(json.dumps(nw.substation, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
    processed("grid", "summary.json").write_text(json.dumps(nw.summary, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")


def load_net() -> pp.pandapowerNet:
    return pp.from_json(str(processed("grid", "net.json")))


def load_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return (pd.read_parquet(processed("grid", "buses.parquet")),
            pd.read_parquet(processed("grid", "lines.parquet")),
            pd.read_parquet(processed("grid", "customers.parquet")))


def build_and_save() -> Network:
    nw = build()
    save(nw)
    return nw
