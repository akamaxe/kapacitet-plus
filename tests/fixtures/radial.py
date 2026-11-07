"""Random radial low-voltage grids in the import contract's CSV files, for the property tests.

A grid is described by a plain `Spec` (a tuple of tuples, so it can key a cache and Hypothesis can
shrink it): the transformer's rating and impedance, and a list of cabinets, each hanging from the
substation or from an earlier cabinet through a cable of a given type and length, with the
customers connected there through their own service cables. `write` puts it in the four contract
files (buses, lines, transformer, customers), the same files a DSO hands over and that
tests/fixtures/grid_tiny holds, so the grid reaches the engine through the real importer.

The grids are invented, like the tiny grid. Every cabinet hangs from exactly one earlier node, so
the result is a tree rooted at the substation: radial by construction.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import NamedTuple

ORIGIN = (18.0, 59.3)          # an arbitrary point, as in grid_tiny/make.py
MAIN_CABLES = ("NAYY 4x150 SE", "NAYY 4x120 SE", "NAYY 4x50 SE", "48-AL1/8-ST1A 0.4", "94-AL1/15-ST1A 0.4")
SERVICE_CABLES = ("NAYY 4x50 SE", "24-AL1/4-ST1A 0.4")
FUSES_A = (16, 20, 25, 35)


class Customer(NamedTuple):
    service_m: float
    service_cable: str
    fuse_a: int


class Cabinet(NamedTuple):
    parent: int                 # 0 = the substation, k = the k-th cabinet (1-based), always earlier
    length_m: float
    cable: str
    customers: tuple[Customer, ...]


class Spec(NamedTuple):
    sn_mva: float
    vk_percent: float
    vkr_percent: float
    cabinets: tuple[Cabinet, ...]

    @property
    def n_customers(self) -> int:
        return sum(len(c.customers) for c in self.cabinets)


def _lonlat(x_m: float, y_m: float) -> tuple[str, str]:
    lon0, lat0 = ORIGIN
    return (f"{lon0 + x_m / (111_320.0 * math.cos(math.radians(lat0))):.6f}",
            f"{lat0 + y_m / 110_540.0:.6f}")


def tables(spec: Spec) -> dict[str, list[dict[str, str]]]:
    """The four contract files of `spec` as rows of strings."""
    assert spec.n_customers > 0, "a grid needs a customer"
    lon, lat = _lonlat(0.0, 0.0)
    buses = [{"bus_id": "SS", "kind": "substation", "name": "Substation", "lon": lon, "lat": lat}]
    lines: list[dict[str, str]] = []
    customers: list[dict[str, str]] = []
    xy = {0: (0.0, 0.0)}
    for k, cab in enumerate(spec.cabinets, start=1):
        assert 0 <= cab.parent < k, "a cabinet hangs from the substation or an earlier cabinet"
        px, py = xy[cab.parent]
        angle = math.radians(37.0 * k)                    # spread the schematic layout out
        x, y = px + cab.length_m * math.sin(angle), py + cab.length_m * math.cos(angle)
        xy[k] = (x, y)
        name = f"K{k:02d}"
        clon, clat = _lonlat(x, y)
        buses.append({"bus_id": name, "kind": "cabinet", "name": f"Cabinet {name}", "lon": clon, "lat": clat})
        lines.append({"line_id": f"L{name}", "from_bus": "SS" if cab.parent == 0 else f"K{cab.parent:02d}",
                      "to_bus": name, "length_m": f"{cab.length_m:.1f}", "std_type": cab.cable, "parallel": "1"})
        for j, cu in enumerate(cab.customers):
            n = len(customers) + 1
            bus = f"S{n:03d}"
            slon, slat = _lonlat(x + cu.service_m * (1 if j % 2 else -1), y + 6.0 * j)
            buses.append({"bus_id": bus, "kind": "service", "name": f"House {n}", "lon": slon, "lat": slat})
            lines.append({"line_id": f"L{bus}", "from_bus": name, "to_bus": bus,
                          "length_m": f"{cu.service_m:.1f}", "std_type": cu.service_cable, "parallel": "1"})
            customers.append({"customer_id": f"C{n:03d}", "bus": bus, "fuse_a": str(cu.fuse_a), "phase": "",
                              "footprint_m2": "140.0", "annual_kwh": "14000.0"})
    transformer = {"lv_bus": "SS", "sn_mva": f"{spec.sn_mva:g}", "vn_hv_kv": "10.0", "vn_lv_kv": "0.4",
                   "vk_percent": f"{spec.vk_percent:.3f}", "vkr_percent": f"{spec.vkr_percent:.3f}",
                   "vector_group": "Dyn", "tap_pos": "0"}
    return {"buses": buses, "lines": lines, "transformer": [transformer], "customers": customers}


def write(spec: Spec, directory: str | Path) -> Path:
    """Write the contract files of `spec` into `directory` and return it."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in tables(spec).items():
        with open(directory / f"{name}.csv", "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    return directory
