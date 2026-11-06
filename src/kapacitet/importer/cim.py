"""A DSO's CGMES 2.4.15 network, one station per secondary transformer, written as the export contract.

`convert(files, mapping)` reads the profiles (EQ, TP and SSH, and GL for the map) with pandapower's
own converter (cim2pp) and takes, for every in-service two-winding transformer whose LV side is at
most 1 kV, the low-voltage island behind it as one station: the buses reachable from its LV bus on
`pandapower.topology.create_nxgraph(net, respect_switches=True)` without crossing a transformer,
closed bus-bus switches fused and an open switch an open point (importer/from_pandapower.py). Each
station's tables then go through the same `validate()` and `build_from_export()` as an operator's
own CSV files: nothing downstream knows the network came from CGMES.

Done here, because the converter does not:

- CIM's other switch classes (Fuse, Jumper, Sectionaliser, Recloser, DisconnectingCircuitBreaker)
  are read as switches. The converter knows only Breaker, Disconnector, LoadBreakSwitch and Switch
  and drops the rest, which would cut every feeder that leaves its busbar through a fuse off it;
- the GL profile's coordinate system is honoured (any EPSG code pyproj knows; none given is WGS84),
  and a node without a place takes its EnergyConsumer's, else its substation's (what the converter
  gives), else the far end of a cable's line string from a neighbour already placed;
- the vector group is written as pandapower writes it (CIM's DYn is Dyn).

The customers. CGMES has no customer number, main fuse or phase, so a mapping file gives them per
EnergyConsumer (`cim_customers.csv`): `energyconsumer_mrid`, `customer_id` and `fuse_a`, and
optionally `phase`, `footprint_m2` and `annual_kwh`, comma separated, or semicolon separated with
decimal commas. An mRID matches with or without CGMES's leading underscore, in any letter case. An
EnergyConsumer in a station that the mapping does not name is an error finding
(`cim_unmapped_consumer`), never a guess, and no contract is written for that station.

Identifiers. The mRIDs are kept: a bus is written with its ConnectivityNode's (the first node of
those a closed switch fuses into it), a line with its ACLineSegment's, and the station carries its
transformer's; a customer is the mapping's customer_id, and the mapping is what keeps its
EnergyConsumer.

Findings. Each station has the conversion's findings and then validate()'s on its tables. The files
as a whole have their own: what the converter itself logged as an error (it drops every line, or
every switch, when one of them has the wrong number of terminals), no transformer at all, LV
consumers no station reaches, and mapping rows that name no consumer in any station. An error in
any of them means nothing should be written (`Conversion.blocked`). Three-winding transformers are
not stations.
"""

from __future__ import annotations

import io
import logging
import math
import re
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .from_pandapower import (ContractError, contract_tables, geo_point, geojson_coords, lv_graph, lv_island,
                              point_geojson)
from .validate import ERROR, NOTE, WARNING, Report, validate

LV_MAX_KV = 1.0
FILE_SUFFIXES = (".xml", ".rdf", ".zip")
EXTRA_SWITCHES = ("Fuse", "Jumper", "Sectionaliser", "Recloser", "DisconnectingCircuitBreaker")
MAPPING_REQUIRED = ("energyconsumer_mrid", "customer_id", "fuse_a")
MAPPING_OPTIONAL = ("phase", "footprint_m2", "annual_kwh")
WGS84 = "EPSG:4326"
_WINDING = re.compile(r"(YN|ZN|Y|Z|D|A|I)(YN|ZN|Y|Z|D|A|I)", re.IGNORECASE)


class CimError(ValueError):
    """The files or the mapping cannot be read at all: the message says what to fix."""


@dataclass
class Station:
    """One secondary transformer's LV island: its contract tables, when they could be written."""
    trafo: int                                   # the transformer's index in the converted network
    trafo_mrid: str
    name: str
    findings: Report                             # the conversion's own
    tables: dict[str, pd.DataFrame] | None = None
    check: Report | None = None                  # validate() on the tables
    sid: str = ""                                # the station id the caller gives it

    @property
    def usable(self) -> bool:
        return not self.findings.errors and self.check is not None and self.check.usable

    def report(self) -> Report:
        """The conversion's findings and the contract check's, as one report."""
        later = self.check.findings if self.check is not None else []
        return Report(findings=[*self.findings.findings, *later],
                      tables=dict(self.check.tables) if self.check is not None else {})


@dataclass
class Conversion:
    net: Any
    stations: list[Station]
    findings: Report                             # about the files as a whole
    files: list[Path] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """Something no contract can say: nothing should be written."""
        return bool(self.findings.errors) or any(s.findings.errors for s in self.stations)


# ------------------------------------------------------------------------------- reading

def mrid_key(value) -> str:
    """An mRID as it is matched: no surrounding blanks, no leading '#' or '_', lower case."""
    return value.strip().lstrip("#").lstrip("_").lower() if isinstance(value, str) else ""


def cgmes_files(folder: str | Path) -> list[Path]:
    """The CGMES files under `folder` (XML, RDF or a ZIP of them), in name order."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in FILE_SUFFIXES)


def read_mapping(path: str | Path) -> pd.DataFrame:
    """cim_customers.csv, checked: one row per EnergyConsumer, each with a customer_id and a whole
    fuse_a. Returns the given columns plus `key` (mrid_key of energyconsumer_mrid)."""
    path = Path(path)
    if not path.is_file():
        raise CimError(f"{path} is missing: it names each EnergyConsumer's customer_id and fuse_a "
                       f"({', '.join(MAPPING_REQUIRED)}; optionally {', '.join(MAPPING_OPTIONAL)})")
    text = path.read_text(encoding="utf-8-sig")
    head = text.split("\n", 1)[0]
    sep = ";" if head.count(";") > head.count(",") else ","
    df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in MAPPING_REQUIRED if c not in df.columns]
    if missing:
        raise CimError(f"{path.name} has no {', '.join(missing)}")
    df = df[[c for c in (*MAPPING_REQUIRED, *MAPPING_OPTIONAL) if c in df.columns]].copy()
    for col in df.columns:
        df[col] = df[col].str.strip()
    problems = []

    def rows(mask) -> str:
        lines = [str(i + 2) for i in df.index[mask][:5]]          # the header is line 1
        return ", ".join(lines) + (" ..." if int(mask.sum()) > 5 else "")

    for col in MAPPING_REQUIRED:
        blank = df[col] == ""
        if blank.any():
            problems.append(f"no {col} on line {rows(blank)}")
    fuse = pd.to_numeric(df["fuse_a"], errors="coerce")
    bad = (df["fuse_a"] != "") & ~(fuse.notna() & (fuse > 0) & (fuse == fuse.round()))
    if bad.any():
        problems.append(f"fuse_a is not a whole number of amperes on line {rows(bad)}")
    df["key"] = df["energyconsumer_mrid"].map(mrid_key)
    for col, what in (("key", "energyconsumer_mrid"), ("customer_id", "customer_id")):
        twice = (df[col] != "") & df[col].duplicated(keep=False)
        if twice.any():
            problems.append(f"{what} repeated on line {rows(twice)}")
    if problems:
        raise CimError(f"{path.name}: " + "; ".join(problems))
    df["fuse_a"] = fuse.astype(int)
    for col in ("footprint_m2", "annual_kwh"):
        if col in df.columns:
            raw = df[col].str.replace(",", ".", regex=False) if sep == ";" else df[col]
            num = pd.to_numeric(raw, errors="coerce")
            bad = (df[col] != "") & num.isna()
            if bad.any():
                raise CimError(f"{path.name}: {col} is not a number on line {rows(bad)}")
            df[col] = num
    if "phase" in df.columns:
        df["phase"] = df["phase"].where(df["phase"] != "", None)
    return df.reset_index(drop=True)


@contextmanager
def _quiet():
    """The converter logs every step and pandas warns about its idioms: neither is for the DSO."""
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        logging.disable(previous)


def load_network(files: list[str | Path]):
    """The pandapower network of the CGMES files, with the steps the converter leaves out; returns
    (net, crs of the GL profile or None when it cannot be told)."""
    from pandapower.converter.cim import cim_classes
    from pandapower.converter.cim.cim2pp.from_cim import from_cim_dict

    if not files:
        raise CimError("no CGMES files (.xml, .rdf or .zip) to read")
    parser = cim_classes.CimParser()
    with _quiet():
        try:
            parser.parse_files(file_list=[str(f) for f in files])
        except Exception as err:                             # the parser raises bare Exceptions
            raise CimError(f"the CGMES files cannot be read: {err}") from None
        _fold_switches(parser.cim)
        crs = _crs(parser.cim)
        parser.prepare_cim_net()
        parser.set_cim_data_types()
        try:
            net = from_cim_dict(parser, create_tap_controller=False, use_GL_or_DL_profile="gl")
        except Exception as err:
            raise CimError(f"the CGMES files cannot be converted: {type(err).__name__}: {err}") from None
    _place(net, crs)
    _vector_groups(net)
    _plain_flags(net)
    return net, crs


def from_cim(files: list[str | Path]):
    """The pandapower network of the CGMES files (load_network without the coordinate system)."""
    return load_network(files)[0]


def _fold_switches(cim: dict) -> None:
    """Fuses, jumpers, sectionalisers and reclosers into the Switch tables the converter reads."""
    for profile in ("eq", "ssh"):
        tables = cim.get(profile)
        if not isinstance(tables, dict):
            continue
        extra = [tables.pop(k) for k in EXTRA_SWITCHES if isinstance(tables.get(k), pd.DataFrame)]
        extra = [df for df in extra if len(df)]
        if extra:
            have = tables.get("Switch")
            parts = ([have] if isinstance(have, pd.DataFrame) and len(have) else []) + extra
            tables["Switch"] = pd.concat(parts, ignore_index=True, sort=False)


def _crs(cim: dict) -> str | None:
    """The GL profile's coordinate system as 'EPSG:<code>': WGS84 when it names none, None when it
    names several or one that is not an EPSG code."""
    cs = (cim.get("gl") or {}).get("CoordinateSystem")
    if not isinstance(cs, pd.DataFrame) or not len(cs) or "crsUrn" not in cs.columns:
        return WGS84
    codes = set()
    for urn in cs["crsUrn"].dropna().astype(str):
        m = re.search(r"EPSG:+(?:[\d.]*:)?(\d+)\s*$", urn, re.IGNORECASE)
        if m is None:
            return None
        codes.add(m.group(1))
    if len(codes) != 1:
        return None if codes else WGS84
    return f"EPSG:{codes.pop()}"


def _place(net, crs: str | None) -> None:
    """net.bus["geo"] in WGS84, with a place for every bus the GL profile lets us place (see the
    module's docstring), and nothing for the rest. pandapower 3's converter writes GeoJSON text in
    each table's `geo` column: a Point for a bus (its substation's) and a load, a LineString for a
    line."""
    pos: dict[int, tuple[float, float]] = {}
    if crs is not None:
        if "geo" in net.load.columns:
            for bus, geo in zip(net.load["bus"], net.load["geo"]):
                pt = geo_point(geo)
                if pt is not None and pd.notna(bus):
                    pos.setdefault(int(bus), pt)
        if "geo" in net.bus.columns:
            for bus, geo in net.bus["geo"].items():
                pt = geo_point(geo)
                if pt is not None:
                    pos.setdefault(int(bus), pt)
        cables = []
        if "geo" in net.line.columns:
            for li, geo in net.line["geo"].items():
                coords = geojson_coords(geo)
                if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                    ends = (tuple(map(float, coords[0][:2])), tuple(map(float, coords[-1][:2])))
                    cables.append((int(net.line.at[li, "from_bus"]), int(net.line.at[li, "to_bus"]), ends))
        sw = net.switch
        joined = [] if not len(sw) else [
            (int(a), int(b)) for a, b in zip(sw.loc[(sw["et"] == "b") & sw["closed"].astype(bool), "bus"],
                                             sw.loc[(sw["et"] == "b") & sw["closed"].astype(bool), "element"])]
        changed = True
        while changed:
            changed = False
            for a, b in joined:
                if (a in pos) != (b in pos):
                    pos[b if a in pos else a] = pos[a if a in pos else b]
                    changed = True
            for f, t, ends in cables:
                if (f in pos) != (t in pos):
                    known, other = (f, t) if f in pos else (t, f)
                    pos[other] = max(ends, key=lambda p: math.dist(p, pos[known]))
                    changed = True
    if pos and crs != WGS84:
        from pyproj import Transformer

        to_wgs = Transformer.from_crs(crs, WGS84, always_xy=True)
        pos = {b: tuple(to_wgs.transform(x, y)) for b, (x, y) in pos.items()}
    net.bus["geo"] = pd.Series([point_geojson(*pos[b]) if b in pos else None for b in net.bus.index],
                               index=net.bus.index, dtype=object)


def _plain_flags(net) -> None:
    """pandapower 3's converter leaves the transformer flags (tap_dependency_table, oltc...) as
    nullable booleans, and create_transformer cannot extend such a column: plain bool, with NA as
    False, which is how pandapower reads a missing flag anyway."""
    flags = [c for c in net.trafo.columns if str(net.trafo[c].dtype) == "boolean"]
    if flags:
        net.trafo[flags] = net.trafo[flags].fillna(False).astype(bool)


def _vector_groups(net) -> None:
    """CIM's winding connections, DYn, as pandapower's vector group, Dyn."""
    if "vector_group" not in net.trafo.columns:
        return

    def norm(value):
        m = _WINDING.fullmatch(value) if isinstance(value, str) else None
        return f"{m.group(1).upper()}{m.group(2).lower()}" if m else value

    net.trafo["vector_group"] = net.trafo["vector_group"].map(norm)


# ------------------------------------------------------------------------------- stations

def lv_transformers(net) -> list[int]:
    """The in-service two-winding transformers that step down from above 1 kV to at most 1 kV (an
    LV/LV transformer, such as 0.69/0.4 kV, is not a station)."""
    t = net.trafo
    if not len(t):
        return []
    keep = (t["in_service"].astype(bool) & (t["vn_lv_kv"].astype(float) <= LV_MAX_KV)
            & (t["vn_hv_kv"].astype(float) > LV_MAX_KV))
    return [int(i) for i in t.index[keep]]


def _origin_id(table: pd.DataFrame, idx) -> str:
    value = table.at[idx, "origin_id"] if "origin_id" in table.columns else None
    return value if isinstance(value, str) and value else ""


def _names(net, trafos: list[int]) -> dict[int, str]:
    """A station's name: its substation's, with the transformer's when the substation has more."""
    zone = {i: net.bus.at[int(net.trafo.at[i, "lv_bus"]), "zone"] if "zone" in net.bus.columns else None
            for i in trafos}
    count: dict[str, int] = {}
    for z in zone.values():
        if isinstance(z, str) and z:
            count[z] = count.get(z, 0) + 1
    out = {}
    for i in trafos:
        own = net.trafo.at[i, "name"] if "name" in net.trafo.columns else None
        own = own if isinstance(own, str) and own.strip() else ""
        z = zone[i] if isinstance(zone[i], str) and zone[i] else ""
        out[i] = (f"{z} {own}".strip() if count.get(z, 0) > 1 else z) or own or _origin_id(net.trafo, i)
    return out


def _customer_fields(row: pd.Series, columns) -> dict:
    out = {"customer_id": row["customer_id"], "fuse_a": int(row["fuse_a"])}
    for col in MAPPING_OPTIONAL:
        if col in columns:
            value = row[col]
            out[col] = None if value is None or (isinstance(value, float) and math.isnan(value)) else value
    return out


def stations(net, mapping: pd.DataFrame) -> tuple[list[Station], Report]:
    """Every LV transformer's station, and the findings about the network as a whole."""
    overall = Report()
    trafos = lv_transformers(net)
    if not trafos:
        overall.add(ERROR, "no station", "cim_no_station", kv=f"{LV_MAX_KV:g}")
        return [], overall
    graph = lv_graph(net)
    islands = {i: lv_island(net, i, graph) for i in trafos}
    names = _names(net, trafos)
    by_key = {row["key"]: row for _, row in mapping.iterrows()}
    loads = net.load[net.load["in_service"].astype(bool) & net.load["bus"].notna()]
    in_a_station: set[int] = set()
    used: set[str] = set()
    out = []
    for i in trafos:
        island = islands[i]
        findings = Report()
        others = [j for j in trafos if j != i and int(net.trafo.at[j, "lv_bus"]) in island]
        if others:
            findings.add(ERROR, "shared low-voltage network", "cim_shared_island", len(others),
                         mrid=_origin_id(net.trafo, others[0]) or str(others[0]))
        here = loads[loads["bus"].astype(int).isin(island)]
        in_a_station |= {int(idx) for idx in here.index}
        keys = {int(idx): mrid_key(_origin_id(net.load, idx)) for idx in here.index}
        used |= {k for k in keys.values() if k in by_key}
        unmapped = [_origin_id(net.load, idx) or f"load {idx}" for idx, k in keys.items() if k not in by_key]
        if unmapped:
            findings.add(ERROR, "unmapped consumer", "cim_unmapped_consumer", len(unmapped), mrid=unmapped[0])
        st = Station(trafo=i, trafo_mrid=_origin_id(net.trafo, i), name=names[i], findings=findings)
        if not findings.errors:
            cmap = {idx: _customer_fields(by_key[k], mapping.columns) for idx, k in keys.items()}
            try:
                st.tables = contract_tables(net, i, cmap, graph)
            except ContractError as err:
                findings.add(ERROR, "contract", "cim_contract", message=str(err))
            else:
                st.check = validate(st.tables)
        out.append(st)

    vn = net.bus["vn_kv"].astype(float)
    low = loads[loads["bus"].astype(int).map(vn).fillna(0.0) <= LV_MAX_KV]
    lost = [idx for idx in low.index if int(idx) not in in_a_station]
    if lost:
        overall.add(WARNING, "consumer outside every station", "cim_unreached_consumer", len(lost),
                    mrid=_origin_id(net.load, lost[0]) or f"load {lost[0]}")
    unused = [m for m, k in zip(mapping["energyconsumer_mrid"], mapping["key"]) if k not in used]
    if unused:
        overall.add(NOTE, "mapping not used", "cim_mapping_unused", len(unused), mrid=unused[0])
    return out, overall


def _converter_errors(net, report: Report) -> None:
    """What the converter logged as an error, once per message (its first line)."""
    from pandapower.converter.cim.other_classes import LogLevel

    container = net.get("report_container") if hasattr(net, "get") else None
    seen = set()
    for log in (container.get_logs() if container is not None else []):
        if log.level != LogLevel.ERROR:
            continue
        text = str(log.message).strip().splitlines()[0][:300] if str(log.message).strip() else ""
        if text and text not in seen:
            seen.add(text)
            report.add(ERROR, "converter", "cim_converter", message=text)


def convert(files: list[str | Path], mapping: str | Path | pd.DataFrame) -> Conversion:
    """The CGMES files as one Station per LV transformer (see the module's docstring)."""
    if not isinstance(mapping, pd.DataFrame):
        mapping = read_mapping(mapping)
    elif "key" not in mapping.columns:
        mapping = mapping.assign(key=mapping["energyconsumer_mrid"].map(mrid_key))
    net, _crs = load_network(files)
    out, overall = stations(net, mapping)
    found = Report()
    _converter_errors(net, found)
    found.findings += overall.findings
    return Conversion(net=net, stations=out, findings=found, files=[Path(f) for f in files])


__all__ = ["CimError", "Conversion", "Station", "cgmes_files", "convert", "from_cim", "load_network",
           "lv_transformers", "mrid_key", "read_mapping", "stations"]
