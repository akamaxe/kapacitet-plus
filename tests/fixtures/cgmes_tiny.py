"""A tiny CGMES 2.4.15 network, written at test time: our own, MIT like the rest of the repository.

    MV source (ExternalNetworkInjection, 10 kV) - closed Breaker - T1 10/0.4 kV, 400 kVA
    0.4 kV busbar - Fuse F1 - feeder 1: cabinet K1 (houses H1-H3) - cabinet K2 (H4, H5)
                  - Fuse F2 - feeder 2: cabinet K3 (H6-H8)
    a tie cable from K2 to a second node in K3, behind the open Disconnector D1 (the open point)
    an MV customer on the 10 kV busbar, which belongs to no low-voltage station

Four profiles, each a file with its own FullModel: EQ (equipment), TP (the topological nodes the
switch states give), SSH (switch states, loads, the source's voltage) and GL (WGS84 points for the
substation, K1, K3, every house and every cable; K2 has no location of its own, so its place comes
from the cables). The ids are rdf:IDs of the form "_<uuid5>", made from the element's name, so the
files are the same on every run; `mapping` is the `cim_customers.csv` that names the eight houses.

The transformer's no-load loss is the one build.py gives every imported transformer (0.95 kW and
0.2375 % no-load current, which for 400 kVA is all of it resistive), so the source network and
the imported station are the same network to rounding. Nothing here comes from a third-party
model: pandapower's bundled CGMES examples are under a non-commercial licence and are never used.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd

CIM = "http://iec.ch/TC57/2013/CIM-schema-cim16#"
NAMESPACES = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "cim": CIM,
              "md": "http://iec.ch/TC57/61970-552/ModelDescription/1#",
              "entsoe": "http://entsoe.eu/CIM/SchemaExtension/3/1#"}
PROFILES = {
    "EQ": ["http://entsoe.eu/CIM/EquipmentCore/3/1", "http://entsoe.eu/CIM/EquipmentOperation/3/1"],
    "TP": ["http://entsoe.eu/CIM/Topology/4/1"],
    "SSH": ["http://entsoe.eu/CIM/SteadyStateHypothesis/1/1"],
    "GL": ["http://entsoe.eu/CIM/GeographicalLocation/2/1"],
}
_NS = uuid.UUID("6f1c2f3e-6a43-4b8e-9d55-0b1a3c1d2e77")
ORIGIN = (17.95, 59.33)                                    # lon, lat of the secondary substation

# r, x (ohm/km), b (uS/km) and rating (A) of the two cable types
MAIN = (0.208, 0.080, 2 * math.pi * 50 * 261e-9 * 1e6, 270.0)
SERVICE = (0.642, 0.083, 2 * math.pi * 50 * 210e-9 * 1e6, 142.0)

# (name, cabinet, position in m east and north of the substation)
CABINETS = {"K1": (170.0, 60.0), "K2": (300.0, 70.0), "K3": (-210.0, 90.0)}
HOUSES = [("H1", "K1", (165.0, 88.0)), ("H2", "K1", (172.0, 31.0)), ("H3", "K1", (205.0, 78.0)),
          ("H4", "K2", (298.0, 92.0)), ("H5", "K2", (331.0, 55.0)),
          ("H6", "K3", (-205.0, 121.0)), ("H7", "K3", (-236.0, 77.0)), ("H8", "K3", (-170.0, 99.0))]
HOUSE_KW = 2.0
MV_CUSTOMER_KW = 50.0


def mrid(name: str) -> str:
    return f"_{uuid.uuid5(_NS, name)}"


@dataclass
class Fixture:
    folder: Path
    files: list[Path]
    mapping: pd.DataFrame                  # energyconsumer_mrid, customer_id, fuse_a, phase
    mapping_file: Path
    trafo: str                             # the transformer's mRID
    consumers: dict[str, str] = field(default_factory=dict)      # house name -> EnergyConsumer mRID
    lines: dict[str, str] = field(default_factory=dict)          # cable name -> ACLineSegment mRID
    nodes: dict[str, str] = field(default_factory=dict)          # node name -> ConnectivityNode mRID
    places: dict[str, tuple[float, float]] = field(default_factory=dict)   # node name -> lon, lat


class _Profile:
    """One profile's file: its FullModel and its objects, as RDF/XML."""

    def __init__(self, name: str, depends: list[str]):
        self.name = name
        self.model = f"urn:uuid:{uuid.uuid5(_NS, 'model ' + name)}"
        self.depends = depends
        self.objects: list[str] = []

    def add(self, cls: str, rid: str, fields: dict, own: bool) -> None:
        """An object: defined here (rdf:ID) or described here for one defined in EQ (rdf:about)."""
        head = f'<cim:{cls} rdf:ID="{rid}">' if own else f'<cim:{cls} rdf:about="#{rid}">'
        body = []
        for key, value in fields.items():
            prefix = "entsoe" if key.startswith("entsoe:") else "cim"
            key = key.removeprefix("entsoe:")
            if value is None:
                continue
            if isinstance(value, Ref):
                body.append(f'  <{prefix}:{key} rdf:resource="#{value.id}"/>')
            elif isinstance(value, Enum):
                body.append(f'  <{prefix}:{key} rdf:resource="{value.ns}{value.value}"/>')
            else:
                text = ("true" if value else "false") if isinstance(value, bool) else (
                    repr(float(value)) if isinstance(value, float) else str(value))
                body.append(f"  <{prefix}:{key}>{escape(text)}</{prefix}:{key}>")
        self.objects.append("\n".join([head, *body, f"</cim:{cls}>"]))

    def xml(self) -> str:
        ns = " ".join(f'xmlns:{k}="{v}"' for k, v in NAMESPACES.items())
        model = [f'<md:FullModel rdf:about="{self.model}">',
                 "  <md:Model.created>2026-01-01T00:00:00Z</md:Model.created>",
                 "  <md:Model.scenarioTime>2026-01-01T00:00:00Z</md:Model.scenarioTime>",
                 "  <md:Model.version>1</md:Model.version>",
                 "  <md:Model.modelingAuthoritySet>urn:kapacitet:test-fixture</md:Model.modelingAuthoritySet>",
                 *[f"  <md:Model.profile>{p}</md:Model.profile>" for p in PROFILES[self.name]],
                 *[f'  <md:Model.DependentOn rdf:resource="{d}"/>' for d in self.depends],
                 "</md:FullModel>"]
        return "\n".join(['<?xml version="1.0" encoding="UTF-8"?>', f"<rdf:RDF {ns}>", *model, *self.objects,
                          "</rdf:RDF>", ""])


@dataclass(frozen=True)
class Ref:
    id: str


@dataclass(frozen=True)
class Enum:
    value: str
    ns: str = CIM


def _lonlat(xy: tuple[float, float]) -> tuple[float, float]:
    lon0, lat0 = ORIGIN
    return (lon0 + xy[0] / (111_320.0 * math.cos(math.radians(lat0))), lat0 + xy[1] / 111_320.0)


def write(folder: str | Path, tie_open: bool = True, crs: str = "EPSG:4326") -> Fixture:
    """The four profiles in `folder` (EQ.xml, TP.xml, SSH.xml, GL.xml) and cim_customers.csv beside
    them. `tie_open=False` closes the open point, which makes the two feeders a ring; `crs` is the
    coordinate system of the GL profile (any EPSG code pyproj knows)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    eq = _Profile("EQ", [])
    tp = _Profile("TP", [eq.model])
    ssh = _Profile("SSH", [eq.model])
    gl = _Profile("GL", [eq.model])

    bv = {"10": mrid("BaseVoltage 10 kV"), "0.4": mrid("BaseVoltage 0.4 kV")}
    for kv, rid in bv.items():
        eq.add("BaseVoltage", rid, {"IdentifiedObject.name": f"{kv} kV", "BaseVoltage.nominalVoltage": float(kv)},
               True)
    region, sub_region = mrid("Region"), mrid("SubRegion")
    eq.add("GeographicalRegion", region, {"IdentifiedObject.name": "Test region"}, True)
    eq.add("SubGeographicalRegion", sub_region, {"IdentifiedObject.name": "Test area",
                                                 "SubGeographicalRegion.Region": Ref(region)}, True)

    # containers: the secondary substation with its two voltage levels, a station per cabinet
    containers: dict[str, str] = {}

    def substation(name: str, levels: list[str]) -> None:
        sid = mrid(f"Substation {name}")
        eq.add("Substation", sid, {"IdentifiedObject.name": name, "Substation.Region": Ref(sub_region)}, True)
        for kv in levels:
            vl = mrid(f"VoltageLevel {name} {kv}")
            eq.add("VoltageLevel", vl, {"IdentifiedObject.name": f"{name} {kv} kV",
                                        "VoltageLevel.BaseVoltage": Ref(bv[kv]),
                                        "VoltageLevel.Substation": Ref(sid)}, True)
            containers[f"{name} {kv}"] = vl
        containers[name] = sid

    substation("Station 1", ["10", "0.4"])
    for k in CABINETS:
        substation(k, ["0.4"])
    feeders = {}
    for f in ("Feeder 1", "Feeder 2"):
        feeders[f] = mrid(f"Line {f}")
        eq.add("Line", feeders[f], {"IdentifiedObject.name": f, "Line.Region": Ref(sub_region)}, True)

    # connectivity nodes, each with its container and its place (m from the substation)
    nodes: dict[str, str] = {}
    node_kv: dict[str, str] = {}
    node_in: dict[str, str] = {}
    node_xy: dict[str, tuple[float, float]] = {}

    def node(name: str, container: str, kv: str, xy: tuple[float, float]) -> str:
        nodes[name] = mrid(f"ConnectivityNode {name}")
        node_kv[name], node_in[name], node_xy[name] = kv, container, xy
        eq.add("ConnectivityNode", nodes[name], {"IdentifiedObject.name": name,
                                                 "ConnectivityNode.ConnectivityNodeContainer":
                                                     Ref(containers[container])}, True)
        return nodes[name]

    for name in ("MV source", "MV T1"):
        node(name, "Station 1 10", "10", (0.0, 0.0))
    for name in ("LV busbar", "Feeder 1 head", "Feeder 2 head"):
        node(name, "Station 1 0.4", "0.4", (0.0, 0.0))
    for k, xy in CABINETS.items():
        node(k, f"{k} 0.4", "0.4", xy)
    node("K3 tie", "K3 0.4", "0.4", CABINETS["K3"])
    for h, k, xy in HOUSES:
        node(h, f"{k} 0.4", "0.4", xy)

    # terminals: (equipment id, node name, sequence number)
    terminals: list[tuple[str, str, str, int]] = []

    def terminal(equipment: str, label: str, node_name: str, seq: int) -> str:
        tid = mrid(f"Terminal {label} {seq}")
        eq.add("Terminal", tid, {"IdentifiedObject.name": f"{label} T{seq}",
                                 "Terminal.ConductingEquipment": Ref(equipment),
                                 "Terminal.ConnectivityNode": Ref(nodes[node_name]),
                                 "ACDCTerminal.sequenceNumber": seq}, True)
        terminals.append((tid, equipment, node_name, seq))
        return tid

    # the MV source and its voltage control
    eni = mrid("ExternalNetworkInjection grid")
    rc = mrid("RegulatingControl grid")
    t_eni = terminal(eni, "grid", "MV source", 1)
    eq.add("RegulatingControl", rc, {"IdentifiedObject.name": "grid voltage",
                                     "RegulatingControl.mode": Enum("RegulatingControlModeKind.voltage"),
                                     "RegulatingControl.Terminal": Ref(t_eni)}, True)
    eq.add("ExternalNetworkInjection", eni, {
        "IdentifiedObject.name": "MV grid", "Equipment.EquipmentContainer": Ref(containers["Station 1 10"]),
        "ExternalNetworkInjection.governorSCD": 0.0, "ExternalNetworkInjection.maxP": 100.0,
        "ExternalNetworkInjection.minP": -100.0, "ExternalNetworkInjection.maxQ": 100.0,
        "ExternalNetworkInjection.minQ": -100.0, "ExternalNetworkInjection.maxInitialSymShCCurrent": 11547.0,
        "ExternalNetworkInjection.minInitialSymShCCurrent": 8660.0,
        "ExternalNetworkInjection.maxR1ToX1Ratio": 0.1, "ExternalNetworkInjection.minR1ToX1Ratio": 0.1,
        "ExternalNetworkInjection.maxR0ToX0Ratio": 0.1, "ExternalNetworkInjection.maxZ0ToZ1Ratio": 1.0,
        "RegulatingCondEq.RegulatingControl": Ref(rc)}, True)
    ssh.add("ExternalNetworkInjection", eni, {"ExternalNetworkInjection.referencePriority": 1,
                                              "ExternalNetworkInjection.p": 0.0, "ExternalNetworkInjection.q": 0.0,
                                              "RegulatingCondEq.controlEnabled": True}, False)
    ssh.add("RegulatingControl", rc, {"RegulatingControl.discrete": False, "RegulatingControl.enabled": True,
                                      "RegulatingControl.targetDeadband": 0.0,
                                      "RegulatingControl.targetValue": 10.0,
                                      "RegulatingControl.targetValueUnitMultiplier": Enum("UnitMultiplier.k")},
            False)

    # switches: (class, name, from node, to node, open, container)
    closed_pairs: list[tuple[str, str]] = []
    switches = [("Breaker", "B1", "MV source", "MV T1", False, "Station 1 10"),
                ("Fuse", "F1", "LV busbar", "Feeder 1 head", False, "Station 1 0.4"),
                ("Fuse", "F2", "LV busbar", "Feeder 2 head", False, "Station 1 0.4"),
                ("Disconnector", "D1", "K3 tie", "K3", tie_open, "K3 0.4")]
    for cls, name, a, b, is_open, container in switches:
        sw = mrid(f"{cls} {name}")
        eq.add(cls, sw, {"IdentifiedObject.name": name, "Equipment.EquipmentContainer": Ref(containers[container]),
                         "Switch.normalOpen": is_open, "Switch.retained": False}, True)
        ssh.add(cls, sw, {"Switch.open": is_open}, False)
        terminal(sw, name, a, 1)
        terminal(sw, name, b, 2)
        if not is_open:
            closed_pairs.append((a, b))

    # the transformer: impedance on the HV winding, the iron loss as a conductance
    trafo = mrid("PowerTransformer T1")
    eq.add("PowerTransformer", trafo, {"IdentifiedObject.name": "T1",
                                       "Equipment.EquipmentContainer": Ref(containers["Station 1"])}, True)
    s_mva, vk, vkr = 0.4, 0.04, 0.012
    z_base = 10.0 ** 2 / s_mva
    ends = [("1", "MV T1", 10.0, vkr * z_base, math.sqrt(vk ** 2 - vkr ** 2) * z_base, 0.95e-3 / 10.0 ** 2,
             "D", False, 0, bv["10"]),
            ("2", "LV busbar", 0.4, 0.0, 0.0, 0.0, "Yn", True, 5, bv["0.4"])]
    for num, node_name, u, r, x, g, kind, grounded, clock, base in ends:
        t = terminal(trafo, "T1", node_name, int(num))
        eq.add("PowerTransformerEnd", mrid(f"PowerTransformerEnd T1 {num}"), {
            "IdentifiedObject.name": f"T1 end {num}", "PowerTransformerEnd.PowerTransformer": Ref(trafo),
            "TransformerEnd.endNumber": int(num), "TransformerEnd.Terminal": Ref(t),
            "TransformerEnd.BaseVoltage": Ref(base), "TransformerEnd.grounded": grounded,
            "PowerTransformerEnd.ratedS": s_mva, "PowerTransformerEnd.ratedU": u,
            "PowerTransformerEnd.r": r, "PowerTransformerEnd.x": x, "PowerTransformerEnd.b": 0.0,
            "PowerTransformerEnd.g": g, "PowerTransformerEnd.r0": r, "PowerTransformerEnd.x0": x,
            "PowerTransformerEnd.b0": 0.0, "PowerTransformerEnd.g0": g,
            "PowerTransformerEnd.connectionKind": Enum(f"WindingConnection.{kind}"),
            "PowerTransformerEnd.phaseAngleClock": clock}, True)

    # cables: (name, from node, to node, km, type, feeder); the tie's geometry is drawn backwards
    limit_type = mrid("OperationalLimitType PATL")
    eq.add("OperationalLimitType", limit_type, {
        "IdentifiedObject.name": "PATL", "OperationalLimitType.direction":
            Enum("OperationalLimitDirectionKind.absoluteValue"),
        "entsoe:OperationalLimitType.limitType": Enum("LimitTypeKind.patl", NAMESPACES["entsoe"])}, True)
    cables = [("C F1-K1", "Feeder 1 head", "K1", 0.180, MAIN, "Feeder 1"),
              ("C K1-K2", "K1", "K2", 0.140, MAIN, "Feeder 1"),
              ("C F2-K3", "Feeder 2 head", "K3", 0.220, MAIN, "Feeder 2"),
              ("C K2-K3 tie", "K2", "K3 tie", 0.520, MAIN, "Feeder 1")]
    cables += [(f"S {h}", k, h, round(math.dist(CABINETS[k], xy) / 1000.0 + 0.004, 3), SERVICE,
                "Feeder 1" if k in ("K1", "K2") else "Feeder 2") for h, k, xy in HOUSES]
    lines: dict[str, str] = {}
    geometry: dict[str, list[tuple[float, float]]] = {}
    for name, a, b, km, (r, x, b_us, amps), feeder in cables:
        ln = mrid(f"ACLineSegment {name}")
        lines[name] = ln
        eq.add("ACLineSegment", ln, {
            "IdentifiedObject.name": name, "Equipment.EquipmentContainer": Ref(feeders[feeder]),
            "ConductingEquipment.BaseVoltage": Ref(bv["0.4"]), "Conductor.length": km,
            "ACLineSegment.r": r * km, "ACLineSegment.x": x * km, "ACLineSegment.bch": b_us * km * 1e-6,
            "ACLineSegment.gch": 0.0, "ACLineSegment.r0": 4 * r * km, "ACLineSegment.x0": 4 * x * km,
            "ACLineSegment.b0ch": b_us * km * 1e-6, "ACLineSegment.g0ch": 0.0,
            "ACLineSegment.shortCircuitEndTemperature": 160.0}, True)
        for seq, end in ((1, a), (2, b)):
            t = terminal(ln, name, end, seq)
            ols = mrid(f"OperationalLimitSet {name} {seq}")
            eq.add("OperationalLimitSet", ols, {"IdentifiedObject.name": f"{name} limits",
                                                "OperationalLimitSet.Terminal": Ref(t)}, True)
            eq.add("CurrentLimit", mrid(f"CurrentLimit {name} {seq}"), {
                "IdentifiedObject.name": "PATL", "OperationalLimit.OperationalLimitSet": Ref(ols),
                "OperationalLimit.OperationalLimitType": Ref(limit_type), "CurrentLimit.value": amps}, True)
        pts = [node_xy[a], node_xy[b]]
        geometry[ln] = pts[::-1] if name == "C K2-K3 tie" else pts

    # consumers: the eight houses and the MV customer
    consumers: dict[str, str] = {}
    for name, node_name, kw, container in [*[(h, h, HOUSE_KW, f"{k} 0.4") for h, k, _ in HOUSES],
                                           ("MV customer", "MV source", MV_CUSTOMER_KW, "Station 1 10")]:
        ec = mrid(f"EnergyConsumer {name}")
        consumers[name] = ec
        eq.add("EnergyConsumer", ec, {"IdentifiedObject.name": name,
                                      "ConductingEquipment.BaseVoltage": Ref(bv[node_kv[node_name]]),
                                      "Equipment.EquipmentContainer": Ref(containers[container])}, True)
        ssh.add("EnergyConsumer", ec, {"EnergyConsumer.p": kw / 1000.0, "EnergyConsumer.q": 0.0}, False)
        terminal(ec, name, node_name, 1)

    # SSH: every terminal connected; TP: the nodes the closed switches join
    for tid, *_ in terminals:
        ssh.add("Terminal", tid, {"ACDCTerminal.connected": True}, False)
    group = {n: n for n in nodes}

    def find(n: str) -> str:
        while group[n] != n:
            n = group[n]
        return n

    for a, b in closed_pairs:
        group[find(b)] = find(a)
    topo = {}
    for n in nodes:
        root = find(n)
        if root not in topo:
            topo[root] = mrid(f"TopologicalNode {root}")
            tp.add("TopologicalNode", topo[root], {"IdentifiedObject.name": root,
                                                   "TopologicalNode.BaseVoltage": Ref(bv[node_kv[n]]),
                                                   "TopologicalNode.ConnectivityNodeContainer":
                                                       Ref(containers[node_in[root]])}, True)
        tp.add("ConnectivityNode", nodes[n], {"ConnectivityNode.TopologicalNode": Ref(topo[root])}, False)
    for tid, _equipment, node_name, _seq in terminals:
        tp.add("Terminal", tid, {"Terminal.TopologicalNode": Ref(topo[find(node_name)])}, False)

    # GL: points in the asked coordinate system
    to_crs = _projector(crs)
    cs = mrid("CoordinateSystem")
    gl.add("CoordinateSystem", cs, {"IdentifiedObject.name": crs, "CoordinateSystem.crsUrn":
                                    f"urn:ogc:def:crs:{crs.replace(':', '::')}"}, True)

    def place(resource: str, label: str, pts: list[tuple[float, float]]) -> None:
        loc = mrid(f"Location {label}")
        gl.add("Location", loc, {"IdentifiedObject.name": label, "Location.PowerSystemResources": Ref(resource),
                                 "Location.CoordinateSystem": Ref(cs)}, True)
        for seq, xy in enumerate(pts, start=1):
            x, y = to_crs(*_lonlat(xy))
            gl.add("PositionPoint", mrid(f"PositionPoint {label} {seq}"), {
                "PositionPoint.Location": Ref(loc), "PositionPoint.sequenceNumber": seq,
                "PositionPoint.xPosition": repr(x), "PositionPoint.yPosition": repr(y)}, True)

    place(containers["Station 1"], "Station 1", [(0.0, 0.0)])
    for k in ("K1", "K3"):                                       # K2 has no location of its own
        place(containers[k], k, [CABINETS[k]])
    for name, ln in lines.items():
        place(ln, name, geometry[ln])
    for h, _k, xy in HOUSES:
        place(consumers[h], h, [xy])

    files = []
    for prof in (eq, tp, ssh, gl):
        path = folder / f"{prof.name}.xml"
        path.write_text(prof.xml(), encoding="utf-8")
        files.append(path)

    mapping = pd.DataFrame({
        "energyconsumer_mrid": [consumers[h] for h, _k, _xy in HOUSES],
        "customer_id": [f"7350000000000{i:05d}" for i in range(1, len(HOUSES) + 1)],
        "fuse_a": [16, 20, 25, 20, 16, 25, 20, 16],
        "phase": ["", "", "a", "", "", "b", "", ""],
    })
    mapping_file = folder / "cim_customers.csv"
    mapping.to_csv(mapping_file, index=False)
    places = {n: _lonlat(xy) for n, xy in node_xy.items()}
    return Fixture(folder=folder, files=files, mapping=mapping, mapping_file=mapping_file, trafo=trafo,
                   consumers=consumers, lines=lines, nodes=nodes, places=places)


def _projector(crs: str):
    if crs.upper() in ("EPSG:4326", "WGS84"):
        return lambda lon, lat: (lon, lat)
    from pyproj import Transformer

    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return tr.transform
