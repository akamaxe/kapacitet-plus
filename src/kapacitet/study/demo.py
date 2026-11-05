"""A synthetic study to try the tools on: three stations from pandapower's LV benchmark networks.

The networks are built at run time by pandapower itself (`create_dickert_lv_network` and two of
the Kerber `kb_extrem_*` networks), written as the export contract through
importer/from_pandapower.py, and handed to the study exactly as a DSO's folders would be. The fuse
of each house, its roof area and the coordinates are drawn here from a fixed seed, so the demo is
the same everywhere; every station says it is synthetic in its provenance.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import Study, init_study
from .intake import add_contract, check, split

NAME = "Demo DSO (synthetic)"
FUSES = (16, 20, 25)
FUSE_SHARE = (0.3, 0.5, 0.2)


def _networks() -> list[tuple[str, str, str, Callable]]:
    import pandapower.networks as pn

    return [
        ("demo-dickert", "Dickert LV network, middle feeders",
         "pandapower.networks.create_dickert_lv_network(feeders_range='middle', customer='multiple', "
         "case='average', trafo_type_name='0.25 MVA 10/0.4 kV')",
         lambda: pn.create_dickert_lv_network(feeders_range="middle", linetype="cable", customer="multiple",
                                              case="average", trafo_type_name="0.25 MVA 10/0.4 kV")),
        ("demo-landnetz", "Kerber Landnetz, cable", "pandapower.networks.kb_extrem_landnetz_kabel_trafo()",
         pn.kb_extrem_landnetz_kabel_trafo),
        ("demo-dorfnetz", "Kerber Dorfnetz", "pandapower.networks.kb_extrem_dorfnetz()", pn.kb_extrem_dorfnetz),
    ]


def customer_map(net, seed: int) -> dict[int, dict]:
    """A fuse and a roof area for every load of the network, from a fixed seed."""
    rng = np.random.default_rng(seed)
    out = {}
    for idx in net.load.index:
        out[int(idx)] = {"customer_id": f"C{int(idx)}",
                         "fuse_a": int(rng.choice(FUSES, p=FUSE_SHARE)),
                         "footprint_m2": round(float(rng.uniform(80.0, 200.0)), 1)}
    return out


def layout(tables: dict[str, pd.DataFrame], lat: float, lon: float) -> pd.DataFrame:
    """Schematic coordinates: each feeder leaves the substation at its own angle and every section
    is drawn at its length, a branch turning off its parent's direction."""
    buses, lines = tables["buses"], tables["lines"]
    root = str(tables["transformer"]["lv_bus"].iloc[0])
    nbr: dict[str, list[tuple[str, float]]] = {}
    for r in lines.itertuples():
        nbr.setdefault(str(r.from_bus), []).append((str(r.to_bus), float(r.length_m)))
        nbr.setdefault(str(r.to_bus), []).append((str(r.from_bus), float(r.length_m)))
    pos = {root: (0.0, 0.0)}
    heading = {root: 0.0}
    first = nbr.get(root, [])
    stack = []
    for i, (b, length) in enumerate(first):
        stack.append((b, root, length, 2 * math.pi * i / max(len(first), 1)))
    while stack:
        bus, parent, length, angle = stack.pop()
        if bus in pos:
            continue
        x0, y0 = pos[parent]
        pos[bus] = (x0 + length * math.cos(angle), y0 + length * math.sin(angle))
        heading[bus] = angle
        kids = [(b, ln) for b, ln in nbr.get(bus, []) if b not in pos]
        for j, (b, ln) in enumerate(kids):
            turn = 0.0 if j == 0 else (1 if j % 2 else -1) * math.radians(60)
            stack.append((b, bus, ln, angle + turn))
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(lat))
    out = buses.copy()
    out["lon"] = [lon + pos.get(str(b), (0.0, 0.0))[0] / m_per_deg_lon for b in out["bus_id"]]
    out["lat"] = [lat + pos.get(str(b), (0.0, 0.0))[1] / m_per_deg_lat for b in out["bus_id"]]
    return out


def create(path: str | Path, allow_git: bool = False) -> Study:
    """A new study with the three synthetic stations imported and checked (not yet built)."""
    import tempfile

    from ..config import settings
    from ..importer.from_pandapower import contract_tables, write_contract

    ref = settings().site
    study = init_study(path, NAME, allow_git=allow_git,
                       site={"name": "Synthetic stations near the reference site", "lat": ref.center_lat,
                             "lon": ref.center_lon, "zone": ref.bidding_zone})
    for k, (sid, name, generator, make) in enumerate(_networks()):
        net = make()
        tables = contract_tables(net, 0, customer_map(net, seed=7100 + k))
        # the stations sit about 1.5 km apart, around the reference site
        angle = 2 * math.pi * k / 3
        lat = ref.center_lat + 1500.0 * math.sin(angle) / 111_320.0
        lon = ref.center_lon + 1500.0 * math.cos(angle) / (111_320.0 * math.cos(math.radians(ref.center_lat)))
        tables["buses"] = layout(tables, lat, lon)
        origin = {"format": "pandapower", "name": f"{name} (synthetic)", "generator": generator,
                  "synthetic": True,
                  "synthetic_parts": ["fuses and roof areas: drawn from a fixed seed for the demo",
                                      "coordinates: a schematic layout around the reference site"]}
        with tempfile.TemporaryDirectory() as tmp:
            write_contract(tables, tmp)
            add_contract(study, tmp, sid, origin=origin)
    split(study)
    check(study)
    return study
