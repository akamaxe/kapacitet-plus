"""Example networks to start a project from: pandapower's low-voltage benchmark networks, written
as the export contract.

A project of the application is one network. Whoever has no export of their own can start from one
of these: the Kerber networks and three of Dickert's, built at run time by pandapower itself and
passed through `from_pandapower.contract_tables`, so they enter the product by the same door as a
DSO's four CSV files. What pandapower does not hold (each house's fuse, its roof area, where the
buses are) is drawn here from a fixed seed, and so is what pandapower itself draws: its Kerber cable
networks give each house one of two service cables at even odds, from Python's `random`, so two
builds of one network differ unless that draw is fixed (`seeded`). With both fixed a template is
the same network everywhere, and every network made from one says it is synthetic.

The catalogue states each network's customers and transformer so that listing the templates builds
nothing; `tests/test_templates.py` checks those figures against what pandapower builds.

`customer_map` and `layout` are also what the study demo (`study/demo.py`) and the scale study
(`tools/study_scale.py`) use.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

FUSES = (16, 20, 25)
FUSE_SHARE = (0.3, 0.5, 0.2)
SEED = 7300                       # a template's own draw of fuses and roof areas: SEED + its position


@contextmanager
def seeded(seed: int) -> Iterator[None]:
    """Build a pandapower benchmark network with its own random draw fixed: the Kerber generators
    choose each service cable with `random.random()`. The caller's random state is put back."""
    state = random.getstate()
    random.seed(seed)
    try:
        yield
    finally:
        random.setstate(state)


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


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    generator: str                 # how pandapower makes it, as someone would type it
    make: Callable[[], Any]        # builds the pandapower network (imports pandapower when called)
    customers: int
    trafo_kva: float


def _kerber(fn: str) -> Callable[[], Any]:
    def make():
        import pandapower.networks as pn

        return getattr(pn, fn)()
    return make


def _dickert(feeders: str) -> Callable[[], Any]:
    def make():
        import pandapower.networks as pn

        return pn.create_dickert_lv_network(feeders_range=feeders, linetype="cable", customer="multiple",
                                            case="average")
    return make


def _catalogue() -> tuple[Template, ...]:
    kerber = (
        ("kerber-land-ol", "Kerber Landnetz, overhead line", "kb_extrem_landnetz_freileitung", 26, 250.0),
        ("kerber-land-kabel", "Kerber Landnetz, cable", "kb_extrem_landnetz_kabel", 26, 250.0),
        ("kerber-land-ol-trafo", "Kerber Landnetz, overhead line, transformer case",
         "kb_extrem_landnetz_freileitung_trafo", 27, 100.0),
        ("kerber-land-kabel-trafo", "Kerber Landnetz, cable, transformer case",
         "kb_extrem_landnetz_kabel_trafo", 27, 100.0),
        ("kerber-dorf", "Kerber Dorfnetz", "kb_extrem_dorfnetz", 58, 400.0),
        ("kerber-dorf-trafo", "Kerber Dorfnetz, transformer case", "kb_extrem_dorfnetz_trafo", 117, 250.0),
        ("kerber-vorstadt-1", "Kerber Vorstadtnetz 1", "kb_extrem_vorstadtnetz_1", 145, 630.0),
        ("kerber-vorstadt-2", "Kerber Vorstadtnetz 2", "kb_extrem_vorstadtnetz_2", 145, 630.0),
        ("kerber-vorstadt-trafo-1", "Kerber Vorstadtnetz 1, transformer case",
         "kb_extrem_vorstadtnetz_trafo_1", 191, 250.0),
        ("kerber-vorstadt-trafo-2", "Kerber Vorstadtnetz 2, transformer case",
         "kb_extrem_vorstadtnetz_trafo_2", 192, 250.0),
    )
    out = [Template(tid, name, f"pandapower.networks.{fn}()", _kerber(fn), customers, kva)
           for tid, name, fn, customers, kva in kerber]
    for feeders, customers in (("short", 12), ("middle", 60), ("long", 120)):
        out.append(Template(
            f"dickert-{feeders}", f"Dickert LV network, {feeders} feeders",
            f"pandapower.networks.create_dickert_lv_network(feeders_range='{feeders}', linetype='cable', "
            "customer='multiple', case='average')", _dickert(feeders), customers, 400.0))
    return tuple(out)


TEMPLATES: tuple[Template, ...] = _catalogue()
BY_ID: dict[str, Template] = {t.id: t for t in TEMPLATES}
SYNTHETIC_PARTS = ("the network: a benchmark network of pandapower, not a real one",
                   "fuses and roof areas: drawn from a fixed seed",
                   "coordinates: a schematic layout around the reference site")


def get(template_id: str) -> Template:
    """The template of that id; KeyError when there is none."""
    return BY_ID[template_id]


def tables(template: Template, lat: float, lon: float, seed: int | None = None) -> dict[str, pd.DataFrame]:
    """The template as the contract's four tables, laid out around (lat, lon)."""
    from .from_pandapower import contract_tables

    seed = SEED + TEMPLATES.index(template) if seed is None else seed
    with seeded(seed):
        net = template.make()
    out = contract_tables(net, 0, customer_map(net, seed=seed))
    out["buses"] = layout(out, lat, lon)
    return out


def files(template_id: str) -> dict[str, str]:
    """The template as CSV text, one entry per table of the contract, around the reference site."""
    from ..config import settings
    from .contract import TABLES

    site = settings().site
    made = tables(get(template_id), site.center_lat, site.center_lon)
    # the same text on every system: pandas would end the lines as the machine does
    return {t.name: made[t.name].to_csv(index=False, lineterminator="\n") for t in TABLES}


def origin(template: Template) -> dict:
    """What a network made from this template says about where it comes from."""
    return {"kind": "template", "template": template.id, "name": template.name,
            "generator": template.generator, "synthetic": True, "synthetic_parts": list(SYNTHETIC_PARTS)}


def listing() -> list[dict]:
    """The catalogue as the API gives it; nothing is built to answer."""
    return [{"id": t.id, "name": t.name, "customers": t.customers, "trafo_kva": t.trafo_kva,
             "generator": t.generator, "synthetic": True} for t in TEMPLATES]


__all__ = ["BY_ID", "FUSES", "FUSE_SHARE", "SEED", "SYNTHETIC_PARTS", "TEMPLATES", "Template", "customer_map",
           "files", "get", "layout", "listing", "origin", "seeded", "tables"]
