"""What the DSO hands over, split into one import folder per station, and checked.

A station's network arrives in the export contract (importer/contract.py), one folder per station
under `inputs/network/contract/<sid>/`. `split` copies each into `stations/<sid>/import/` and keeps
the station's record (station.json) in step with it: where it came from (`origin`); a changed
network shows as changed inputs (study.digests), the record's status left as the last build set
it. `check` runs the contract's data-quality report on every station and keeps it in the record.

A folder may carry an `origin.json` beside its CSV files, saying what produced it:
`{"format": "pandapower", "name": ..., "synthetic": true, "synthetic_parts": [...]}` for the
demo's networks, `{"format": "contract", "synthetic": false}` for a DSO's own export. It travels
with the files into the station's import folder. Without one, the network is taken to be the
DSO's export, and the provenance says that is unverified.
"""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
from pathlib import Path

from ..importer.contract import TABLES
from ..importer.validate import Report, read_export, validate
from . import Study, StudyError, file_digest

CONTRACT_FILES = tuple(f"{t.name}.csv" for t in TABLES)


def contract_dir(study: Study) -> Path:
    return study.inputs / "network" / "contract"


def add_contract(study: Study, src: str | Path, sid: str | None = None, origin: dict | None = None) -> str:
    """Copy one station's contract CSV files and its origin.json (nothing else) into the study's
    inputs. The copy is made beside and then moved in, so a failure never leaves the station's
    folder half replaced; a folder that already is the station's input is left as it is."""
    src = Path(src)
    sid = sid or src.name
    study.station_dir(sid)                                   # the id is checked here
    other = study.same_but_case(sid)
    if other is not None:
        raise StudyError("station_case", id=sid, other=other)
    found = [f for f in CONTRACT_FILES if (src / f).is_file()]
    if not found:
        raise FileNotFoundError(f"no contract files ({', '.join(CONTRACT_FILES)}) in {src}")
    dst = contract_dir(study) / sid
    given = origin if origin is not None else _read_origin(src)
    if dst.is_dir() and src.resolve() == dst.resolve():
        if origin is not None:
            _write_origin(dst, origin)
        return sid
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dst.parent, prefix=f".{sid}-") as tmp:
        staged = Path(tmp)
        for f in found:
            shutil.copyfile(src / f, staged / f)
        if given is not None:
            _write_origin(staged, given)
        dst.mkdir(exist_ok=True)
        for f in (*CONTRACT_FILES, "origin.json"):
            if (staged / f).is_file():
                (staged / f).replace(dst / f)
            else:
                (dst / f).unlink(missing_ok=True)
    return sid


def _write_origin(folder: Path, origin: dict) -> None:
    (folder / "origin.json").write_text(json.dumps(origin, indent=1, ensure_ascii=False), encoding="utf-8")


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
    folders = (sorted(p for p in contract_dir(study).glob("*") if p.is_dir() and not p.name.startswith("."))
               if contract_dir(study).is_dir() else [])
    if not folders:
        raise StudyError("no_network", path=str(study.root))
    seen: dict[str, str] = {}
    for folder in folders:
        study.station_dir(folder.name)                       # the id is checked here
        twin = seen.get(folder.name.lower())
        if twin is not None:
            raise StudyError("station_case", id=folder.name, other=twin)
        seen[folder.name.lower()] = folder.name
        if study.stations_dir.is_dir():
            for p in study.stations_dir.iterdir():
                if p.is_dir() and p.name != folder.name and p.name.lower() == folder.name.lower():
                    raise StudyError("station_case", id=folder.name, other=p.name)
    names = _names(study)
    out = []
    for folder in folders:
        sid = folder.name
        if study.building(sid):
            raise StudyError("building", id=sid)
        dst = study.station_dir(sid) / "import"
        dst.mkdir(parents=True, exist_ok=True)
        for f in (*CONTRACT_FILES, "origin.json"):
            (dst / f).unlink(missing_ok=True)
            if (folder / f).is_file():
                shutil.copyfile(folder / f, dst / f)
        declared = _read_origin(folder)
        given = declared or {}
        origin = {"format": given.get("format", "contract"), "trafo_mrid": given.get("trafo_mrid"),
                  "files": {f: file_digest(dst / f) for f in CONTRACT_FILES if (dst / f).is_file()},
                  "declared": declared is not None}
        for key in ("synthetic", "synthetic_parts", "generator"):
            if key in given:
                origin[key] = given[key]
        name = names.get(sid) or given.get("name") or sid
        # a changed network keeps the status its last build gave: the digests say it changed
        rec = study.record(sid) if study.has(sid) else study.new_record(sid, name, origin)
        rec.update(name=name, origin=origin)
        study.save(rec)
        out.append(sid)
    return out


def check(study: Study, stations: list[str] | None = None) -> dict[str, Report]:
    """The contract's data-quality report for every station, kept in its record. A station being
    built keeps its record as its build writes it (the build's import step makes the same report)."""
    out = {}
    for sid in stations or study.station_ids():
        study.record(sid)                                    # an unknown station is an error
        report = validate(read_export(study.station_dir(sid) / "import"))
        out[sid] = report
        if study.building(sid):
            continue
        rec = study.record(sid)                              # read again, just before writing
        rec["quality"] = quality(report)
        study.save(rec)
    return out


def quality(report: Report) -> dict:
    return {"report": report.to_dict(), "usable": report.usable,
            "warnings": [f.message for f in report.warnings]}
