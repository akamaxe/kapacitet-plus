"""What the DSO hands over, split into one import folder per station, and checked.

A station's network arrives in the export contract (importer/contract.py), one folder per station
under `inputs/network/contract/<sid>/`. `split` copies each into `stations/<sid>/import/` and keeps
the station's record (station.json) in step with it: where it came from (`origin`) and, when the
network changed, that what was built from the old one is no longer current. `check` runs the
contract's data-quality report on every station and keeps it in the record.

A folder may carry an `origin.json` beside its CSV files, saying what produced it:
`{"format": "pandapower", "name": ..., "synthetic": true, "synthetic_parts": [...]}` for the
demo's networks; without it, the folder is the DSO's own export.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from ..importer.contract import TABLES
from ..importer.validate import Report, read_export, validate
from . import Study, StudyError, file_digest

CONTRACT_FILES = tuple(f"{t.name}.csv" for t in TABLES)


def contract_dir(study: Study) -> Path:
    return study.inputs / "network" / "contract"


def add_contract(study: Study, src: str | Path, sid: str | None = None, origin: dict | None = None) -> str:
    """Copy one station's contract CSV files (and nothing else) into the study's inputs."""
    src = Path(src)
    sid = sid or src.name
    study.station_dir(sid)                                   # the id is checked here
    found = [f for f in CONTRACT_FILES if (src / f).is_file()]
    if not found:
        raise FileNotFoundError(f"no contract files ({', '.join(CONTRACT_FILES)}) in {src}")
    dst = contract_dir(study) / sid
    dst.mkdir(parents=True, exist_ok=True)
    for f in CONTRACT_FILES:
        (dst / f).unlink(missing_ok=True)
    for f in found:
        shutil.copyfile(src / f, dst / f)
    given = origin if origin is not None else _read_origin(src)
    if given is not None:
        (dst / "origin.json").write_text(json.dumps(given, indent=1, ensure_ascii=False), encoding="utf-8")
    else:
        (dst / "origin.json").unlink(missing_ok=True)
    return sid


def _read_origin(folder: Path) -> dict | None:
    path = folder / "origin.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _names(study: Study) -> dict[str, str]:
    """Station names from inputs/stations.csv (station_id, name), when it has them."""
    path = study.inputs / "stations.csv"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;") if sample else csv.excel
        rows = list(csv.DictReader(fh, dialect=dialect))
    return {r["station_id"]: r["name"] for r in rows if r.get("station_id") and r.get("name")}


def split(study: Study) -> list[str]:
    """One import folder per station from inputs/network; returns the station ids, in order."""
    cgmes = study.inputs / "network" / "cgmes"
    if cgmes.is_dir() and any(p.is_file() for p in cgmes.rglob("*")):
        raise StudyError("cgmes_pending")
    folders = sorted(p for p in contract_dir(study).glob("*") if p.is_dir()) if contract_dir(study).is_dir() else []
    if not folders:
        raise StudyError("no_network", path=str(study.root))
    names = _names(study)
    out = []
    for folder in folders:
        sid = folder.name
        if study.building(sid):
            raise StudyError("building", id=sid)
        dst = study.station_dir(sid) / "import"
        dst.mkdir(parents=True, exist_ok=True)
        for f in CONTRACT_FILES:
            (dst / f).unlink(missing_ok=True)
            if (folder / f).is_file():
                shutil.copyfile(folder / f, dst / f)
        given = _read_origin(folder) or {}
        origin = {"format": given.get("format", "contract"), "trafo_mrid": given.get("trafo_mrid"),
                  "files": {f: file_digest(dst / f) for f in CONTRACT_FILES if (dst / f).is_file()}}
        for key in ("synthetic", "synthetic_parts", "generator"):
            if key in given:
                origin[key] = given[key]
        name = names.get(sid) or given.get("name") or sid
        rec = study.record(sid) if study.has(sid) else study.new_record(sid, name, origin)
        if rec.get("origin", {}).get("files") != origin["files"] and rec.get("status") not in (None, "imported"):
            rec["status"] = "imported"                    # what was built is from another network
        rec.update(name=name, origin=origin)
        study.save(rec)
        out.append(sid)
    return out


def check(study: Study, stations: list[str] | None = None) -> dict[str, Report]:
    """The contract's data-quality report for every station, kept in its record."""
    out = {}
    for sid in stations or study.station_ids():
        rec = study.record(sid)
        report = validate(read_export(study.station_dir(sid) / "import"))
        rec["quality"] = quality(report)
        study.save(rec)
        out[sid] = report
    return out


def quality(report: Report) -> dict:
    return {"report": report.to_dict(), "usable": report.usable,
            "warnings": [f.message for f in report.warnings]}
