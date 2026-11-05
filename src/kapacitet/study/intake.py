"""What the DSO hands over, split into one import folder per station, and checked.

A station's network arrives in the export contract (importer/contract.py), one folder per station
under `inputs/network/contract/<sid>/`. `split` copies each into `stations/<sid>/import/` and keeps
the station's record (station.json) in step with it: where it came from (`origin`); a changed
network shows as changed inputs (study.digests), the record's status left as the last build set
it. `check` runs the contract's data-quality report on every station and keeps it in the record.

Or the network arrives as CGMES 2.4.15 files in `inputs/network/cgmes/`, with
`inputs/cim_customers.csv` naming each EnergyConsumer's customer and main fuse. `import_cim`
converts them (importer/cim.py) into one contract folder per LV transformer, written only when no
station has an error the contract cannot carry, and leaves a note of what it converted
(`.cgmes.json`); `split` refuses CGMES files changed since, so they are never skipped silently.
A station's id is the one `inputs/stations.csv` gives its transformer (`station_id, name,
trafo_mrid`), else the transformer's mRID.

A folder may carry an `origin.json` beside its CSV files, saying what produced it:
`{"format": "pandapower", "name": ..., "synthetic": true, "synthetic_parts": [...]}` for the
demo's networks, `{"format": "contract", "synthetic": false}` for a DSO's own export. It travels
with the files into the station's import folder. Without one, the network is taken to be the
DSO's export, and the provenance says that is unverified.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from .. import procs
from ..importer.contract import ALL_TABLES
from ..importer.validate import Report, read_export, validate
from . import Study, StudyError, dir_digest, file_digest, valid_id

CONTRACT_FILES = tuple(f"{t.name}.csv" for t in ALL_TABLES)      # the optional equipment.csv too
CGMES_MARK = ".cgmes.json"
CGMES_SCHEMA = "kapacitet.cgmes-import/1"


def contract_dir(study: Study) -> Path:
    return study.inputs / "network" / "contract"


def cgmes_dir(study: Study) -> Path:
    return study.inputs / "network" / "cgmes"


def mapping_file(study: Study) -> Path:
    return study.inputs / "cim_customers.csv"


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


def _station_rows(study: Study) -> list[dict]:
    """The rows of inputs/stations.csv (station_id, name, and trafo_mrid for a CGMES network)."""
    path = study.inputs / "stations.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;") if sample else csv.excel
        rows = list(csv.DictReader(fh, dialect=dialect))
    return [{str(k).strip().lower(): (v or "").strip() for k, v in r.items() if k is not None} for r in rows]


def _names(study: Study) -> dict[str, str]:
    """Station names from inputs/stations.csv (station_id, name), when it has them."""
    return {r["station_id"]: r["name"] for r in _station_rows(study) if r.get("station_id") and r.get("name")}


# ---------------------------------------------------------------- a CGMES network

def cgmes_state(study: Study) -> dict:
    """The digests of what a CGMES import is made from."""
    return {"cgmes": dir_digest(cgmes_dir(study)), "mapping": file_digest(mapping_file(study)),
            "stations": file_digest(study.inputs / "stations.csv")}


def _cgmes_pending(study: Study) -> bool:
    """CGMES files that were never converted, or changed (or their mapping did) since."""
    folder = cgmes_dir(study)
    if not folder.is_dir() or not any(p.is_file() for p in folder.rglob("*")):
        return False
    try:
        mark = json.loads((contract_dir(study) / CGMES_MARK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    state = cgmes_state(study)
    return {k: mark.get(k) for k in state} != state


def derived_id(trafo_mrid: str) -> str:
    """A station id from a transformer's mRID: the mRID without CGMES's underscore, or a digest of
    it when that is not a folder name."""
    from ..importer.cim import mrid_key

    sid = re.sub(r"[^A-Za-z0-9_-]+", "-", mrid_key(trafo_mrid)).strip("-_")
    return sid if valid_id(sid) else "trafo-" + hashlib.sha256(trafo_mrid.encode("utf-8")).hexdigest()[:12]


def _assign_ids(study: Study, conversion) -> None:
    from ..importer.cim import mrid_key

    given = {mrid_key(r["trafo_mrid"]): r for r in _station_rows(study)
             if r.get("trafo_mrid") and r.get("station_id")}
    seen: dict[str, str] = {}
    for st in conversion.stations:
        row = given.get(mrid_key(st.trafo_mrid))
        sid = row["station_id"] if row else derived_id(st.trafo_mrid or f"trafo {st.trafo}")
        if row and row.get("name"):
            st.name = row["name"]
        study.station_dir(sid)                               # the id is checked here
        twin = seen.get(sid.lower())
        if twin is not None:
            raise StudyError("cim_station_twice", id=sid, a=twin, b=st.trafo_mrid)
        seen[sid.lower()] = st.trafo_mrid
        st.sid = sid


def import_cim(study: Study):
    """inputs/network/cgmes and inputs/cim_customers.csv as one contract folder per LV transformer
    (importer/cim.py). Returns (the conversion, the ids of the stations an earlier conversion wrote
    and this one no longer has, whose folders are removed). When the conversion is blocked nothing
    is written; a folder that did not come from a conversion is never written over."""
    from ..importer import cim
    from ..importer.from_pandapower import write_contract

    files = cim.cgmes_files(cgmes_dir(study))
    if not files:
        raise StudyError("no_cgmes", path=str(cgmes_dir(study)))
    try:
        conversion = cim.convert(files, mapping_file(study))
    except cim.CimError as err:
        raise StudyError("cim", message=str(err)) from None
    _assign_ids(study, conversion)
    if conversion.blocked:
        return conversion, []
    folder = contract_dir(study)
    for st in conversion.stations:
        dst = folder / st.sid
        if dst.is_dir() and (_read_origin(dst) or {}).get("format") != "cgmes" \
                and any((dst / f).is_file() for f in CONTRACT_FILES):
            raise StudyError("cim_overwrites", id=st.sid)
    state = cgmes_state(study)
    source = {"files": {f.relative_to(cgmes_dir(study)).as_posix(): file_digest(f) for f in files},
              "mapping": state["mapping"]}
    for st in conversion.stations:
        origin = {"format": "cgmes", "name": st.name, "trafo_mrid": st.trafo_mrid, "source": source}
        with tempfile.TemporaryDirectory() as tmp:
            write_contract(st.tables, tmp)
            add_contract(study, tmp, st.sid, origin=origin)
    keep = {st.sid for st in conversion.stations}
    removed = sorted(p.name for p in folder.iterdir() if p.is_dir() and p.name not in keep
                     and (_read_origin(p) or {}).get("format") == "cgmes")
    for sid in removed:
        shutil.rmtree(folder / sid)
    procs.write_json(folder / CGMES_MARK, {"schema": CGMES_SCHEMA, **state,
                                           "converted": {st.sid: st.trafo_mrid for st in conversion.stations}})
    return conversion, removed


def split(study: Study) -> list[str]:
    """One import folder per station from inputs/network; returns the station ids, in order."""
    if _cgmes_pending(study):
        raise StudyError("cgmes_pending", path=str(study.root))
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
        for key in ("synthetic", "synthetic_parts", "generator", "source"):
            if key in given:
                origin[key] = given[key]
        name = names.get(sid) or given.get("name") or sid
        # a changed network keeps the status its last build gave: the digests say it changed
        rec = study.record(sid) if study.has(sid) else study.new_record(sid, name, origin)
        rec.update(name=name, origin=origin)
        study.save(rec)
        out.append(sid)
    return out


def read_table(path: Path):
    """A CSV file a DSO hands over: comma or semicolon separated (with a decimal comma), UTF-8 with
    or without a BOM."""
    import pandas as pd

    with path.open(encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
    try:
        sep = csv.Sniffer().sniff(sample, delimiters=",;").delimiter if sample else ","
    except csv.Error:
        sep = ","
    return pd.read_csv(path, sep=sep, decimal="," if sep == ";" else ".", encoding="utf-8-sig",
                       dtype={"customer_id": str})


def equipment_file(study: Study) -> Path:
    return study.inputs / "equipment.csv"


def station_equipment(study: Study, sid: str, customers=None):
    """The equipment records of a station and where they come from: the rows of the study's
    inputs/equipment.csv that name the station's customers, or else the equipment.csv of the
    station's own contract folder; (None, "") when there is neither."""
    imp = study.station_dir(sid) / "import"
    path = equipment_file(study)
    if path.is_file():
        if customers is None:
            customers = read_export(imp).get("customers")
        eq = read_table(path)
        if customers is None or "customer_id" not in eq.columns or "customer_id" not in customers.columns:
            return eq, "inputs/equipment.csv"
        ids = set(customers["customer_id"].astype(str).str.strip())
        return eq[eq["customer_id"].astype(str).str.strip().isin(ids)].reset_index(drop=True), "inputs/equipment.csv"
    if (imp / "equipment.csv").is_file():
        return read_table(imp / "equipment.csv"), "the station's equipment.csv"
    return None, ""


def check(study: Study, stations: list[str] | None = None) -> dict[str, Report]:
    """The contract's data-quality report for every station, its equipment records included (the
    study's or its own), kept in its record. A station being built keeps its record as its build
    writes it (the build's import and equipment steps make the same findings)."""
    out = {}
    for sid in stations or study.station_ids():
        study.record(sid)                                    # an unknown station is an error
        data = read_export(study.station_dir(sid) / "import")
        data.pop("equipment", None)
        if "customers" in data:
            eq, _ = station_equipment(study, sid, data["customers"])
            if eq is not None:
                data["equipment"] = eq
        report = validate(data)
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
