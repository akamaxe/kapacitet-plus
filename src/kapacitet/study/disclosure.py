"""What a publication may show: the cells a study's stations are published in ([disclosure]).

A cell holds at least `min_customers` (k) customers, so a published figure never describes a
handful of households. Each station starts at the study's level and its cell is merged upward
until it holds k customers:

    station   the station itself, then the SWEREF 99 TM squares of grid_m that hold it, each
              doubled until it holds k (a square of twice the size holds whole squares of the one
              below, so the cells nest), then its zone, then the whole study
    grid      the squares of grid_m, doubled the same way, then the zone, then the whole study
    zone      the DSO's zone of the station (inputs/stations.csv `zone`), then the whole study

A station without a position skips the squares, and one without a zone skips the zone. Only the
stations that cannot reach k at their own level move up; the rest stay where they are, so a merged
cell holds only the stations that needed it. A cell that holds fewer than k customers even as the
whole study is suppressed: none of its figures are published.

Hidden stations ([disclosure] hidden, `hidden` in inputs/stations.csv, or a review correction) are
left out altogether: not published, and not counted in any cell.

A cell's figure is the lowest of its stations' (the capacity every one of them has, never a sum,
since stations can share an upstream headroom), and a cell with a station that has no figure has
none. `report` gives one row per station, which is kept in runs/<Q>/disclosure_report.csv and
never published: it says which station is in which cell.
"""

from __future__ import annotations

from collections import defaultdict

from .. import geo
from .config import Disclosure

DOUBLINGS = 6                  # grid_m up to 64 × grid_m, then the zone
LEVELS = ("station", "grid", "zone", "study")
REPORT_COLUMNS = ["station_id", "customers", "hidden", "hidden_by", "requested_level", "cell_id", "cell_level",
                  "cell_stations", "cell_customers", "merged", "suppressed"]


# the order in which cells are settled: the station, each square from grid_m upward, the zone, the study
_RANKS = ("station", *(f"grid{i}" for i in range(DOUBLINGS + 1)), "zone", "study")


def rank(level: str, info: dict) -> int:
    if level == "grid":
        return _RANKS.index(f"grid{info['doubling']}")
    return _RANKS.index(level)


def _square_key(size: int, e0: int, n0: int) -> str:
    return f"R{size}_{e0}_{n0}"


def chain(row: dict, cfg: Disclosure, study_id: str) -> list[tuple[str, str, dict]]:
    """The cells a station can be in, from its own level upward: (level, cell_id, geometry info)."""
    out: list[tuple[str, str, dict]] = []
    if cfg.level == "station":
        out.append(("station", row["station_id"], {"lon": row.get("lon"), "lat": row.get("lat")}))
    if cfg.level in ("station", "grid"):
        en = geo.to_sweref(row.get("lon"), row.get("lat"))
        if en is not None:
            for i in range(DOUBLINGS + 1):
                size = cfg.grid_m * 2 ** i
                e0, n0 = geo.square_of(en[0], en[1], size)
                out.append(("grid", _square_key(size, e0, n0), {"size_m": size, "e0": e0, "n0": n0, "doubling": i}))
    if row.get("zone"):
        out.append(("zone", f"Z_{row['zone']}", {"zone": row["zone"]}))
    out.append(("study", "ALL", {"study": study_id}))
    return out


def hidden_by(row: dict, cfg: Disclosure, fixes: dict) -> str | None:
    sid = row["station_id"]
    if sid in set(cfg.hidden):
        return "study.toml"
    if row.get("hidden"):
        return "stations.csv"
    if "hidden" in (fixes.get(sid) or {}):
        return "correction"
    return None


def assign(rows: list[dict], cfg: Disclosure, study_id: str, fixes: dict | None = None) -> dict:
    """{"cells": {cell_id: {"level", "geometry", "stations", "customers", "suppressed"}},
    "stations": {sid: {"cell", "hidden_by", ...}}}; see the module's docstring."""
    fixes = fixes or {}
    k = cfg.min_customers
    shown = [r for r in rows if hidden_by(r, cfg, fixes) is None]
    chains = {r["station_id"]: chain(r, cfg, study_id) for r in shown}
    customers = {r["station_id"]: int(r.get("customers") or 0) for r in shown}
    pos = {sid: 0 for sid in chains}
    # rank by rank, from the bottom: a cell is settled only once every station that can still
    # arrive in it from below has, so a station never passes a level before the others reach it
    for r in range(len(_RANKS)):
        cells: dict[str, list[str]] = defaultdict(list)
        for sid, i in pos.items():
            if rank(chains[sid][i][0], chains[sid][i][2]) == r:
                cells[chains[sid][i][1]].append(sid)
        for members in cells.values():
            if sum(customers[s] for s in members) < k:
                for s in members:
                    if pos[s] < len(chains[s]) - 1:
                        pos[s] += 1
    out_cells: dict[str, dict] = {}
    for sid, i in sorted(pos.items()):
        level, cid, info = chains[sid][i]
        c = out_cells.setdefault(cid, {"id": cid, "level": level, "info": info, "stations": [], "customers": 0})
        c["stations"].append(sid)
        c["customers"] += customers[sid]
    for c in out_cells.values():
        c["suppressed"] = c["customers"] < k
        c["geometry"] = geometry(c)
    stations = {}
    for r in rows:
        sid = r["station_id"]
        by = hidden_by(r, cfg, fixes)
        if by is not None:
            stations[sid] = {"cell": None, "hidden_by": by}
            continue
        level, cid, _ = chains[sid][pos[sid]]
        stations[sid] = {"cell": cid, "hidden_by": None, "level": level, "merged": pos[sid] > 0,
                         "suppressed": out_cells[cid]["suppressed"]}
    return {"level": cfg.level, "min_customers": k, "grid_m": cfg.grid_m, "cells": out_cells, "stations": stations}


def geometry(cell: dict) -> dict | None:
    """A station's point, a square's polygon (WGS84); a zone or the whole study has none."""
    info = cell["info"]
    if cell["level"] == "station":
        return geo.point(info.get("lon"), info.get("lat"))
    if cell["level"] == "grid":
        return geo.square_polygon(info["e0"], info["n0"], info["size_m"])
    return None


def report(rows: list[dict], result: dict) -> list[dict]:
    """One row per station: its cell, whether it was merged, hidden or suppressed. Never published."""
    out = []
    for r in rows:
        s = result["stations"][r["station_id"]]
        c = result["cells"].get(s["cell"]) if s["cell"] else None
        out.append({"station_id": r["station_id"], "customers": r.get("customers"), "hidden": s["hidden_by"] is not None,
                    "hidden_by": s["hidden_by"], "requested_level": result["level"],
                    "cell_id": s["cell"], "cell_level": c["level"] if c else None,
                    "cell_stations": len(c["stations"]) if c else None, "cell_customers": c["customers"] if c else None,
                    "merged": s.get("merged"), "suppressed": s.get("suppressed")})
    return out


def lowest(values: list) -> float | None:
    """A cell's figure: the lowest of its stations', or None when any of them has none."""
    if not values or any(v is None for v in values):
        return None
    return float(min(values))


__all__ = ["DOUBLINGS", "LEVELS", "REPORT_COLUMNS", "assign", "chain", "geometry", "hidden_by", "lowest", "report"]
