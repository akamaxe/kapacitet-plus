"""What a study of many stations costs: time and disk per station (docs/study-scale.md).

    python tools/study_scale.py create <dir> --stations 50
    python tools/study_scale.py run <dir> [--jobs 6] [--full] [--run 2026Q4]
    python tools/study_scale.py measure <dir> [--json FILE]
    python tools/study_scale.py all <dir> --stations 50 [--jobs 6] [--full]

``create`` makes a study of N synthetic stations in `<dir>`, which must be outside the repository.
The stations cycle through thirteen of pandapower's LV benchmark networks (the Kerber networks and
three of Dickert's, 12 to 192 customers), each with its own fuses, roof areas and place from a
seed of its own, written through the export contract exactly as `kapacitet study demo` writes its
three.

``run`` builds the stations (`kapacitet study build`, quick mode unless ``--full``), collects the
run and publishes its draft, and writes how long each step took to `<dir>/scale.json`.

``measure`` reads each station's own record of its build (the seconds of every step) and the size
of its folder, and prints the table of the document; ``--json`` also writes it.

Nothing here is new engine code: it is the study's own commands with a clock around them. The
figures are of the machine it ran on and of what else that machine was doing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

NAME = "Scale study (synthetic)"
SEED = 9100
RING_M = 1500.0          # stations sit on rings around the reference site, this far apart
PER_RING = 12


def networks() -> list[tuple[str, str, Callable]]:
    """(short name, how pandapower makes it, the maker): the product's example networks
    (importer/templates.py), every benchmark LV network that passes the contract's check with more
    than one customer."""
    from kapacitet.importer.templates import TEMPLATES

    return [(t.id, t.generator, t.make) for t in TEMPLATES]


def place(k: int, lat: float, lon: float) -> tuple[float, float]:
    """The k-th station's place: rings of twelve around the reference site."""
    ring, slot = k // PER_RING + 1, k % PER_RING
    angle = 2 * math.pi * (slot + 0.5 * (ring % 2)) / PER_RING
    r = RING_M * ring
    return (lat + r * math.sin(angle) / 111_320.0,
            lon + r * math.cos(angle) / (111_320.0 * math.cos(math.radians(lat))))


def create(path: Path, stations: int) -> list[str]:
    import tempfile
    import warnings

    from kapacitet.config import settings
    from kapacitet.importer.from_pandapower import contract_tables, write_contract
    from kapacitet.study import init_study
    from kapacitet.importer.templates import customer_map, layout, seeded
    from kapacitet.study.intake import add_contract, check, split

    warnings.filterwarnings("ignore")
    cfg = settings()
    ref = cfg.site
    study = init_study(path, NAME, site={"name": "Synthetic stations around the reference site", "lat": ref.center_lat,
                                         "lon": ref.center_lon, "temperature_station": cfg.temperature_station,
                                         "zone": ref.bidding_zone})
    kinds = networks()
    made = []
    for k in range(stations):
        short, generator, make = kinds[k % len(kinds)]
        sid = f"s{k + 1:03d}-{short}"
        with seeded(SEED + k):
            net = make()
        tables = contract_tables(net, 0, customer_map(net, seed=SEED + k))
        tables["buses"] = layout(tables, *place(k, ref.center_lat, ref.center_lon))
        origin = {"format": "pandapower", "name": f"{short} {k + 1} (synthetic)", "generator": generator,
                  "synthetic": True,
                  "synthetic_parts": ["fuses and roof areas: drawn from a fixed seed for the scale study",
                                      "coordinates: a schematic layout around the reference site"]}
        with tempfile.TemporaryDirectory() as tmp:
            write_contract(tables, tmp)
            add_contract(study, tmp, sid, origin=origin)
        made.append(sid)
    split(study)
    check(study)
    return made


def _timed(args: list[str]) -> dict:
    t0 = time.perf_counter()
    code = subprocess.run([sys.executable, "-m", "kapacitet", *args]).returncode
    return {"command": "kapacitet " + " ".join(args), "seconds": round(time.perf_counter() - t0, 1), "exit": code}


def run(path: Path, jobs: int | None, full: bool, run_id: str | None) -> dict:
    """Build, collect and publish with a clock around each; <dir>/scale.json keeps the result."""
    from kapacitet.study import open_study, runs

    study = open_study(path)
    q = run_id or runs.current(study.config.publication.cadence)
    build = ["study", "build", str(path)] + ([] if full else ["--quick"]) + (["--jobs", str(jobs)] if jobs else [])
    steps = [_timed(build)]
    if steps[0]["exit"] in (0, 1):                 # 1: some station failed; the others are still collected
        steps.append(_timed(["study", "collect", str(path), "--run", q]))
        if steps[-1]["exit"] == 0:
            steps.append(_timed(["study", "publish", str(path), "--run", q]))
    doc = {"run": q, "mode": "full" if full else "quick", "jobs": jobs, "steps": steps, "machine": machine(),
           "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (path / "scale.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def machine() -> dict:
    return {"platform": platform.platform(), "processor": platform.processor(), "cores": os.cpu_count(),
            "python": platform.python_version()}


def folder_bytes(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def measure(path: Path) -> dict:
    """Per station: its customers, how its build ended, the seconds of each step and its folder's size."""
    from kapacitet.study import open_study

    study = open_study(path)
    rows = []
    for folder in sorted(p for p in (path / "stations").iterdir() if (p / "station.json").exists()):
        rec = json.loads((folder / "station.json").read_text(encoding="utf-8"))
        steps = {s["name"]: s.get("seconds") for s in rec.get("steps") or []}
        first = next((s.get("summary") for s in rec.get("steps") or [] if s.get("summary")), None) or {}
        rows.append({"station": rec["id"], "customers": first.get("customers"), "buses": first.get("buses"),
                     "status": rec.get("status"), "quick": bool(rec.get("quick")),
                     "compute_s": steps.get("compute"), "profiles_s": steps.get("profiles"),
                     "build_s": round(sum(v for v in steps.values() if v), 1) if steps else None,
                     "disk_mb": round(folder_bytes(folder) / 1e6, 1)})
    scale = path / "scale.json"
    ready = [r for r in rows if r["status"] == "ready"]
    return {"study": study.id, "stations": len(rows), "ready": len(ready),
            "customers": sum(r["customers"] or 0 for r in rows),
            "build_s_sum": round(sum(r["build_s"] or 0.0 for r in ready), 1),
            "disk_mb": round(sum(r["disk_mb"] for r in rows), 1),
            "study_disk_mb": round(folder_bytes(path) / 1e6, 1),
            "wall": json.loads(scale.read_text(encoding="utf-8")) if scale.exists() else None, "rows": rows}


def table(doc: dict) -> str:
    def cell(v) -> str:
        return "–" if v is None else str(v)

    lines = ["| Station | Customers | Buses | Build | Profiles (s) | Compute (s) | Build (s) | Disk (MB) |",
             "|---|---|---|---|---|---|---|---|"]
    for r in doc["rows"]:
        lines.append(f"| {r['station']} | {cell(r['customers'])} | {cell(r['buses'])} | {r['status']} | "
                     f"{cell(r['profiles_s'])} | {cell(r['compute_s'])} | {cell(r['build_s'])} | {r['disk_mb']} |")
    lines += ["", f"{doc['ready']} of {doc['stations']} stations ready, {doc['customers']} customers; the builds add up "
                  f"to {doc['build_s_sum']} s of station time and the stations to {doc['disk_mb']} MB "
                  f"(the whole study folder: {doc['study_disk_mb']} MB)."]
    wall = doc.get("wall")
    if wall:
        m = wall["machine"]
        lines += ["", f"Wall clock ({wall['mode']} mode, {wall['jobs'] or 'default'} station processes at a time, "
                      f"{m['cores']} cores, {m['platform']}):", ""]
        lines += [f"- `{s['command']}`: {s['seconds']} s, exit {s['exit']}" for s in wall["steps"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("create", "run", "measure", "all"):
        p = sub.add_parser(name)
        p.add_argument("dir", type=Path)
        if name in ("create", "all"):
            p.add_argument("--stations", type=int, required=True)
        if name in ("run", "all"):
            p.add_argument("--jobs", type=int, default=None)
            p.add_argument("--full", action="store_true", help="every weather year and adoption order, not quick mode")
            p.add_argument("--run", dest="run_id", default=None)
        if name in ("measure", "all"):
            p.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)
    path = args.dir.resolve()
    if args.command in ("create", "all"):
        if args.stations < 1:
            ap.error("--stations must be at least 1")
        made = create(path, args.stations)
        print(f"{len(made)} stations in {path}")
    if args.command in ("run", "all"):
        doc = run(path, args.jobs, args.full, args.run_id)
        for s in doc["steps"]:
            print(f"{s['command']}: {s['seconds']} s, exit {s['exit']}")
    if args.command in ("measure", "all"):
        doc = measure(path)
        print(table(doc))
        if args.json:
            args.json.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
