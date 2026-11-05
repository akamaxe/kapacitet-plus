"""Write the tiny test grid: a synthetic low-voltage network in the import contract's CSV files.

    python tests/fixtures/grid_tiny/make.py        # rewrites the CSV files next to this script

The grid is invented, not taken from any map or operator. It exists so the safety tests run on
every push without the pipeline's output: small enough to build in well under a second, and
shaped so both export limits that matter actually bind under stress.

- One 0.1 MVA 10/0.4 kV transformer. Twenty customers can export about twice its rating, so the
  transformer binds.
- Feeder A is short, on NAYY 4x150 SE: its eight customers barely move the voltage.
- Feeder B is long and mostly NAYY 4x50 SE. Its twelve customers, listed last, raise the voltage at
  its far end to the limit before the transformer is full in some hours, so voltage binds too
  with the MV side at 1.00 pu; at 1.06 pu it is the voltage that binds in every stressed hour.
- Every customer hangs from its own service bus through a NAYY 4x50 SE service cable, with a
  main fuse of 20, 25 or 35 A.

Next to the four contract files (buses, lines, transformer, customers), `roofs.csv` gives each
customer's PV size in kWp (`cid` is the customer's position in customers.csv, as in the engine's
own tables). The coordinates are a schematic layout around an
arbitrary point, there only so the export is complete.

Nothing here is random, and the files are written with LF line endings and fixed number formats,
so running it again gives byte-equal files (tests/test_grid_tiny.py checks that).
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORIGIN = (18.0, 59.3)                   # lon, lat of the substation: an arbitrary point
SERVICE = "NAYY 4x50 SE"

# (cabinet, metres from the previous cabinet, cable, customers there)
FEEDERS = {
    "A": (0.0, [("KA1", 40.0, "NAYY 4x150 SE", 3),
                ("KA2", 40.0, "NAYY 4x150 SE", 3),
                ("KA3", 35.0, "NAYY 4x150 SE", 2)]),
    "B": (90.0, [("KB1", 70.0, "NAYY 4x150 SE", 2),
                 ("KB2", 130.0, "NAYY 4x50 SE", 3),
                 ("KB3", 130.0, "NAYY 4x50 SE", 2),
                 ("KB4", 130.0, "NAYY 4x50 SE", 3),
                 ("KB5", 120.0, "NAYY 4x50 SE", 2)]),
}
# per customer, in feeder order and one line per cabinet: (service cable m, fuse A, kWp, footprint m2, annual kWh)
CUSTOMERS = [
    (15.0, 25, 9.0, 140.0, 14000.0), (22.0, 20, 7.0, 120.0, 11000.0), (18.0, 25, 11.0, 160.0, 16000.0),
    (20.0, 35, 13.0, 180.0, 21000.0), (16.0, 20, 6.0, 110.0, 9000.0), (24.0, 25, 10.0, 150.0, 15000.0),
    (19.0, 20, 8.0, 125.0, 12000.0), (21.0, 25, 9.0, 135.0, 13000.0),
    (17.0, 20, 10.0, 115.0, 10000.0), (23.0, 35, 19.0, 190.0, 22000.0),
    (18.0, 25, 15.0, 150.0, 15000.0), (20.0, 20, 20.0, 130.0, 12000.0), (16.0, 25, 12.0, 140.0, 14000.0),
    (22.0, 25, 14.0, 145.0, 14500.0), (19.0, 20, 11.0, 120.0, 11500.0),
    (21.0, 35, 20.0, 200.0, 23000.0), (17.0, 25, 15.0, 155.0, 15500.0), (24.0, 20, 10.0, 110.0, 10500.0),
    (20.0, 25, 16.0, 165.0, 17000.0), (25.0, 35, 18.0, 175.0, 20000.0),
]
TRANSFORMER = {"lv_bus": "SS", "sn_mva": "0.1", "vn_hv_kv": "10.0", "vn_lv_kv": "0.4", "vk_percent": "4.0",
               "vkr_percent": "1.325", "vector_group": "Dyn", "tap_pos": "0"}


def _lonlat(x_m: float, y_m: float) -> tuple[str, str]:
    lon0, lat0 = ORIGIN
    lon = lon0 + x_m / (111_320.0 * math.cos(math.radians(lat0)))
    lat = lat0 + y_m / 110_540.0
    return f"{lon:.6f}", f"{lat:.6f}"


def tables() -> dict[str, list[dict[str, str]]]:
    """The five files as rows of strings, in the order they are written."""
    lon, lat = _lonlat(0.0, 0.0)
    buses = [{"bus_id": "SS", "kind": "substation", "name": "Substation", "lon": lon, "lat": lat}]
    lines: list[dict[str, str]] = []
    customers: list[dict[str, str]] = []
    roofs: list[dict[str, str]] = []
    data = iter(CUSTOMERS)
    for feeder, (bearing, sections) in FEEDERS.items():
        ux, uy = math.sin(math.radians(bearing)), math.cos(math.radians(bearing))
        prev, dist = "SS", 0.0
        for cabinet, length, cable, n in sections:
            dist += length
            x, y = ux * dist, uy * dist
            lon, lat = _lonlat(x, y)
            buses.append({"bus_id": cabinet, "kind": "cabinet", "name": f"Cabinet {cabinet}", "lon": lon, "lat": lat})
            lines.append({"line_id": f"L{cabinet}", "from_bus": prev, "to_bus": cabinet,
                          "length_m": f"{length:.1f}", "std_type": cable, "parallel": "1"})
            for k in range(n):
                service_m, fuse, kwp, footprint, kwh = next(data)
                cid = len(customers)
                bus = f"S{cid + 1:02d}"
                side = 1.0 if k % 2 == 0 else -1.0                   # alternate sides of the street
                along = 8.0 * (k // 2)
                sx = x + ux * along + side * uy * service_m
                sy = y + uy * along - side * ux * service_m
                slon, slat = _lonlat(sx, sy)
                buses.append({"bus_id": bus, "kind": "service", "name": f"House {cid + 1}", "lon": slon, "lat": slat})
                lines.append({"line_id": f"L{bus}", "from_bus": cabinet, "to_bus": bus,
                              "length_m": f"{service_m:.1f}", "std_type": SERVICE, "parallel": "1"})
                customers.append({"customer_id": f"C{cid + 1:02d}", "bus": bus, "fuse_a": str(fuse), "phase": "",
                                  "footprint_m2": f"{footprint:.1f}", "annual_kwh": f"{kwh:.1f}"})
                roofs.append({"cid": str(cid), "kwp": f"{kwp:.2f}"})
            prev = cabinet
    assert next(data, None) is None, "every customer row must be placed"
    return {"buses": buses, "lines": lines, "transformer": [TRANSFORMER], "customers": customers,
            "roofs": roofs}


def write(directory: str | Path = HERE) -> list[Path]:
    """Write the CSV files into `directory` and return their paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name, rows in tables().items():
        path = directory / f"{name}.csv"
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        out.append(path)
    return out


if __name__ == "__main__":
    for p in write(sys.argv[1] if len(sys.argv) > 1 else HERE):
        print(p)
