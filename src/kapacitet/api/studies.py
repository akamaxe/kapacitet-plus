"""A DSO's studies over HTTP: the study of many stations inside the workbench, without a terminal
(task 2.1 of the technical review of 14-11-2026).

Every route calls what `kapacitet study` already does (study/), so the app and the command line write
the same files. The studies are the folders of KAPACITET_STUDY_DIR (by default
%USERPROFILE%\\kapacitet-studies), one per study, named by its id. A study is read from its folder,
so opening one never restarts the server.

    GET  /studies                                  the studies, each with how many stations are ready
    POST /studies                                  a new one: the demonstration (three synthetic stations),
                                                   an empty one, one from contract folders (one folder of
                                                   CSV tables per station, read from a .zip in the browser)
                                                   or one from CGMES files with their table of customers
    GET  /studies/{id}                             its stations (state, step, capacities, binding limits,
                                                   margin), its runs and whether it is building
    POST /studies/{id}/import                      inputs/network split into stations, each checked
    POST /studies/{id}/build                       `kapacitet study build` in a process of its own
    POST /studies/{id}/cancel                      stops that build and the station processes it started
    POST /studies/{id}/runs/{run}/collect          as `kapacitet study collect --run <run>`
    POST /studies/{id}/runs/{run}/review           as `kapacitet study review`
    PUT  /studies/{id}/runs/{run}/corrections      runs/<run>/corrections.csv, then the review again
    POST /studies/{id}/runs/{run}/sign             as `kapacitet study sign --by "name, role"`
    POST /studies/{id}/runs/{run}/publish          as `kapacitet study publish`
    POST /studies/{id}/runs/{run}/diff             as `kapacitet study diff`
    GET  /studies/{id}/runs/{run}/files            the run's files, to download
    GET  /studies/{id}/runs/{run}/files/{path}     one of them

This router and the builds it starts are the API's third place to write: the studies' folder. (The
first is data/requests/, the second data/datasets/; the setup page's bundle upload writes data/ of
a copy without results.) A refusal is {code, message, params}, the study module's codes with
`study_` before them (STUDY_ERRORS), so the interface says each in its language.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import procs
from ..paths import ROOT, inside
from ..study import ERRORS as _STUDY, Study, StudyError, default_root, open_study, valid_id

router = APIRouter(prefix="/api/v1", tags=["studies"])

MAX_TABLE_BYTES = 5_000_000           # a contract table, as for a project
MAX_CGMES_BYTES = 60_000_000          # all the CGMES files of a study together
LAUNCH = "build.launch.json"          # the build this router started: its process and when
BUILD_LOG = "study-build.log"         # what that build printed
LAUNCH_GRACE_S = 120.0                # a launch whose process has not yet taken the study's lock

# the study module's refusals the routes can meet, under the code the API gives them
REACHABLE = ("inside_repo", "inside_git", "no_study", "exists", "config", "bad_station", "station_case",
             "unknown_station", "building", "study_building", "method_version", "cgmes_pending", "no_cgmes",
             "cim", "cim_overwrites", "cim_station_twice", "no_network", "not_collected", "stale_method",
             "bad_signer", "no_ready_station")
STUDY_ERRORS = {
    **{("study_" + c if not c.startswith("study_") else c): _STUDY[c] for c in REACHABLE},
    "study_unknown": "no study {id} in {folder}",
    "study_bad_id": "{id} is not a study id: letters, digits, '_' and '-', at most 64",
    "study_not_building": "study {id} is not building",
    "study_no_run": "no run {run} in study {id}",
    "study_file": "{file} is not a file of run {run}",
    "study_bad_table": "{table} is not a table of the export contract",
    "study_too_large": "{what} is larger than {mb} MB",
    "study_no_stations": "the archive has no station folder with the contract's tables",
}


def _code(code: str) -> str:
    return code if code.startswith("study_") else f"study_{code}"


def refusal(status: int, code: str, **params) -> HTTPException:
    code = _code(code)
    message = STUDY_ERRORS[code].format(**params) if code in STUDY_ERRORS else code
    return HTTPException(status, {"code": code, "message": message, "params": params})


def _refused(err: StudyError, status: int = 409) -> HTTPException:
    code = _code(err.code)
    if code not in STUDY_ERRORS:            # a refusal this router never expects: said in English
        return HTTPException(status, {"code": "study_refused", "message": str(err), "params": {"detail": str(err)}})
    return HTTPException(status, {"code": code, "message": str(err), "params": err.params})


STUDY_ERRORS["study_refused"] = "{detail}"


# ---------------------------------------------------------------- where the studies are

def root() -> Path:
    """The folder of the studies (KAPACITET_STUDY_DIR, or the default under the user's home)."""
    return default_root()


def _study(study_id: str) -> Study:
    if not valid_id(study_id):
        raise refusal(422, "bad_id", id=str(study_id)[:60])
    try:
        folder = inside(root(), study_id)
    except ValueError:
        raise refusal(422, "bad_id", id=str(study_id)[:60]) from None
    if not (folder / "study.toml").is_file():
        raise refusal(404, "unknown", id=study_id, folder=str(root()))
    try:
        return open_study(folder)
    except StudyError as err:
        raise _refused(err) from err


def _launch(study: Study) -> dict:
    try:
        return json.loads((study.root / LAUNCH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build_pid(study: Study) -> int | None:
    """The live process building the study: the one holding its lock, or the one this router just
    started that has not taken it yet."""
    holder = procs.holder(procs.lock_path(study.root))
    if holder:
        return holder
    launch = _launch(study)
    pid = int(launch.get("pid") or 0)
    if pid and procs.alive(pid) and time.time() - float(launch.get("at") or 0) < LAUNCH_GRACE_S:
        return pid
    return None


def _steps(rec: dict) -> dict:
    steps = rec.get("steps") or []
    done = sum(1 for s in steps if s.get("status") == "done")
    running = next((s.get("name") for s in steps if s.get("status") == "running"), None)
    return {"done": done, "total": len(steps), "running": running,
            "seconds": round(sum(float(s.get("seconds") or 0) for s in steps), 1) or None}


def runs_of(study: Study) -> list[dict]:
    """Each run of the study and how far it went: collected, reviewed, signed, published, compared."""
    from ..study import runs as run_ids

    out = []
    if not study.runs.is_dir():
        return out
    for folder in sorted((p for p in study.runs.iterdir() if p.is_dir() and run_ids.valid(p.name)),
                         key=lambda p: run_ids.key(p.name), reverse=True):
        manifest = folder / "publication" / "manifest.json"
        status = None
        if manifest.is_file():
            try:
                status = json.loads(manifest.read_text(encoding="utf-8")).get("status")
            except (OSError, ValueError):
                status = None
        out.append({"run": folder.name, "collected": (folder / "stations.parquet").is_file(),
                    "reviewed": (folder / "review.csv").is_file(), "signed": (folder / "signoff.json").is_file(),
                    "published": status, "compared": (folder / "changes.csv").is_file()})
    return out


def summary(study: Study) -> dict:
    """What the list of studies shows of one."""
    counts: dict[str, int] = {}
    for sid in study.station_ids():
        status = study.record(sid).get("status") or "imported"
        counts[status] = counts.get(status, 0) + 1
    runs = runs_of(study)
    return {"id": study.id, "name": study.config.dso.name or study.id, "stations": sum(counts.values()),
            "states": counts, "building": build_pid(study) is not None,
            "method_version": study.config.dso.method_version, "last_run": runs[0]["run"] if runs else None,
            "site": study.config.site.name if study.config.site.given else None}


def stations_of(study: Study) -> list[dict]:
    """Each station as the study's table shows it: its record (state, step, seconds, memory) and,
    once built, its row of `study collect` (study/collect.row) with the review's margin."""
    from ..study import collect, review, upstream

    up = upstream.read(study)
    out = []
    for sid in study.station_ids():
        rec = study.record(sid)
        row, _cap = collect.row(study, sid, up)
        out.append({"id": sid, "name": rec.get("name") or sid, "status": rec.get("status") or "imported",
                    "error": rec.get("error"), "quick": rec.get("quick"), "customers": row.get("customers"),
                    "steps": _steps(rec), "peak_memory_mb": rec.get("peak_memory_mb"),
                    "suspended_s": rec.get("suspended_s"),
                    "generation_kw": row.get("published_generation_kw"), "generation_limit": row.get("generation_limit"),
                    "consumption_kw": row.get("published_consumption_kw"),
                    "consumption_limit": row.get("consumption_limit"),
                    "margin_kw": review.margin(row) if row.get("generation_status") else None,
                    "validation_status": row.get("validation_status")})
    return out


# ---------------------------------------------------------------- the list and a new study

@router.get("/studies")
def list_studies() -> dict:
    """The studies of the studies' folder, newest first."""
    folder = root()
    items = []
    if folder.is_dir():
        for path in sorted((p for p in folder.iterdir() if (p / "study.toml").is_file()),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            if not valid_id(path.name):
                continue
            try:
                items.append(summary(open_study(path)))
            except StudyError as err:
                items.append({"id": path.name, "name": path.name, "error": str(err)})
    return {"folder": str(folder), "items": items}


class NewStudy(BaseModel):
    kind: Literal["demo", "empty", "contract", "cgmes"]
    name: str = Field("", max_length=80, description="the DSO's name, which the study's documents carry")
    stations: dict[str, dict[str, str]] = Field(
        default_factory=dict, description="kind contract: station id to its contract tables as CSV text")
    cgmes: dict[str, str] = Field(default_factory=dict, description="kind cgmes: file name to its XML text")
    customers: str = Field("", description="kind cgmes: inputs/cim_customers.csv, each EnergyConsumer's "
                                           "customer_id and fuse_a")


def _new_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "study"
    return f"{slug}-{secrets.token_hex(3)}"


def _checked(study: Study, sids: list[str]) -> dict:
    from ..study.intake import check

    reports = check(study, sids)
    return {sid: r.to_dict() for sid, r in reports.items()}


@router.post("/studies")
def create_study(body: NewStudy) -> dict:
    """A new study in the studies' folder. The demonstration is ready to build; an empty one waits
    for its stations; one from contract folders or CGMES is imported and checked at once."""
    from ..importer.contract import BY_NAME
    from ..study import init_study
    from ..study.demo import create as demo
    from ..study.intake import add_contract, cgmes_dir, import_cim, mapping_file, split

    folder = root()
    study_id = _new_id(body.name or body.kind)
    if body.kind == "contract":
        if not body.stations:
            raise refusal(422, "no_stations")
        for sid, tables in body.stations.items():
            if not valid_id(sid):
                raise refusal(422, "bad_station", id=sid[:60])
            for table, text in tables.items():
                if table not in BY_NAME:
                    raise refusal(422, "bad_table", table=table[:40])
                if len(text.encode("utf-8")) > MAX_TABLE_BYTES:
                    raise refusal(413, "too_large", what=f"{sid}/{table}.csv", mb=MAX_TABLE_BYTES // 1_000_000)
    if body.kind == "cgmes":
        size = sum(len(t.encode("utf-8")) for t in body.cgmes.values()) + len(body.customers.encode("utf-8"))
        if size > MAX_CGMES_BYTES:
            raise refusal(413, "too_large", what="the CGMES files", mb=MAX_CGMES_BYTES // 1_000_000)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if body.kind == "demo":
            # under its own name, which says it is synthetic, whatever name was given
            return {"study": summary(demo(folder / study_id)), "reports": {}}
        study = init_study(folder / study_id, body.name)
        reports: dict = {}
        if body.kind == "contract":
            with tempfile.TemporaryDirectory() as tmp:
                for sid, tables in body.stations.items():
                    src = Path(tmp) / sid
                    src.mkdir()
                    for table, text in tables.items():
                        (src / f"{table}.csv").write_bytes(text.encode("utf-8"))
                    add_contract(study, src, sid)
            reports = _checked(study, split(study))
        elif body.kind == "cgmes":
            target = cgmes_dir(study)
            target.mkdir(parents=True, exist_ok=True)
            for name, text in body.cgmes.items():
                # by its own name only, never a folder the caller names
                inside(target, Path(name.replace("\\", "/")).name).write_bytes(text.encode("utf-8"))
            mapping_file(study).write_bytes(body.customers.encode("utf-8"))
            conversion, _removed = import_cim(study)
            if conversion.blocked:
                return {"study": summary(study), "reports": {}, "cim": [f.__dict__ for f in conversion.findings.findings]}
            reports = _checked(study, split(study))
        return {"study": summary(open_study(study.root)), "reports": reports}
    except StudyError as err:
        raise _refused(err, 422) from err


# ---------------------------------------------------------------- one study

@router.get("/studies/{study_id}")
def get_study(study_id: str) -> dict:
    study = _study(study_id)
    from ..study import runs as run_ids

    log = study.root / BUILD_LOG
    tail = log.read_bytes()[-6000:].decode("utf-8", errors="replace").splitlines()[-12:] if log.is_file() else []
    return {"study": summary(study), "folder": str(study.root), "stations": stations_of(study), "runs": runs_of(study),
            "current_run": run_ids.current(study.config.publication.cadence), "log": tail}


@router.post("/studies/{study_id}/import")
def import_study(study_id: str) -> dict:
    """inputs/network split into one import folder per station, and each station checked."""
    from ..study.intake import split

    study = _study(study_id)
    if build_pid(study):
        raise refusal(409, "study_building", pid=build_pid(study))
    try:
        return {"reports": _checked(study, split(study))}
    except StudyError as err:
        raise _refused(err) from err


class BuildIn(BaseModel):
    quick: bool = Field(True, description="one weather year and fewer adoption orders")
    force: bool = Field(False, description="build every station again, changed or not")


@router.post("/studies/{study_id}/build")
def build_study(study_id: str, body: BuildIn) -> dict:
    """`kapacitet study build` in a process of its own, detached from the server: it builds the
    stations that need it, as many at once as the free memory holds, and each station's record
    says how it is going."""
    study = _study(study_id)
    pid = build_pid(study)
    if pid:
        raise refusal(409, "study_building", pid=pid)
    if not study.station_ids():
        raise refusal(409, "no_network", path=str(study.root))
    cmd = [sys.executable, "-m", "kapacitet", "study", "build", str(study.root)]
    if body.quick:
        cmd.append("--quick")
    if body.force:
        cmd.append("--force")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    env.pop("KAPACITET_SUPERVISED", None)
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    with open(study.root / BUILD_LOG, "ab") as log:
        log.write(f"\n=== {procs.now()} {' '.join(cmd)}\n".encode("utf-8"))
        log.flush()
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kwargs)
    procs.write_json(study.root / LAUNCH, {"pid": proc.pid, "at": time.time(), "quick": body.quick,
                                           "force": body.force, "started": procs.now()})
    return {"pid": proc.pid, "study": summary(study)}


@router.post("/studies/{study_id}/cancel")
def cancel_study(study_id: str) -> dict:
    """Stop the study's build and every station process it started; each station that was building
    is built again next time (its record says it was interrupted)."""
    study = _study(study_id)
    pid = build_pid(study)
    if not pid:
        raise refusal(409, "not_building", id=study_id)
    procs.kill_tree(pid)
    procs.lock_path(study.root).unlink(missing_ok=True)
    (study.root / LAUNCH).unlink(missing_ok=True)
    return summary(study)


# ---------------------------------------------------------------- a run

def _run(study: Study, run: str) -> str:
    from ..study import runs as run_ids

    if not run_ids.valid(run):
        raise refusal(422, "config", message=f"{run!r} is not a run id: {run_ids.EXAMPLE}")
    return run


@router.post("/studies/{study_id}/runs/{run}/collect")
def collect_run(study_id: str, run: str) -> dict:
    from ..study.collect import collect

    study = _study(study_id)
    try:
        out = collect(study, _run(study, run))
    except StudyError as err:
        raise _refused(err) from err
    return {"rows": out["rows"], "rewritten": out["rewritten"], "runs": runs_of(study)}


def _review_rows(rows: list[dict]) -> list[dict]:
    keep = ("station_id", "name", "status", "customers", "margin_kw", "flags", "corrected_generation_kw",
            "corrected_consumption_kw", "corrected_hidden", "correction_note")
    return [{k: r.get(k) for k in keep} for r in rows]


@router.post("/studies/{study_id}/runs/{run}/review")
def review_run(study_id: str, run: str) -> dict:
    from ..study.review import review

    study = _study(study_id)
    try:
        out = review(study, _run(study, run))
    except StudyError as err:
        raise _refused(err) from err
    return {"rows": _review_rows(out["rows"]), "findings": out["findings"], "runs": runs_of(study)}


class CorrectionsIn(BaseModel):
    csv: str = Field(..., max_length=2_000_000, description="runs/<run>/corrections.csv as text")


@router.put("/studies/{study_id}/runs/{run}/corrections")
def put_corrections(study_id: str, run: str, body: CorrectionsIn) -> dict:
    """runs/<run>/corrections.csv, written whole, and the review again with it: what it could not
    apply comes back as findings."""
    from ..atomic import write_bytes_atomic
    from ..study.review import review, run_dir

    study = _study(study_id)
    try:
        folder = run_dir(study, _run(study, run))
        folder.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(folder / "corrections.csv", body.csv.encode("utf-8"))
        out = review(study, run)
    except StudyError as err:
        raise _refused(err) from err
    return {"rows": _review_rows(out["rows"]), "findings": out["findings"], "runs": runs_of(study)}


class SignIn(BaseModel):
    by: str = Field(..., min_length=3, max_length=120, description='who signs, as "name, role"')


@router.post("/studies/{study_id}/runs/{run}/sign")
def sign_run(study_id: str, run: str, body: SignIn) -> dict:
    from ..study.publish import sign

    study = _study(study_id)
    try:
        doc = sign(study, _run(study, run), body.by)
    except StudyError as err:
        raise _refused(err) from err
    return {"by": doc["by"], "outputs": len(doc["outputs"]), "seal": doc["seal"]["id"], "runs": runs_of(study)}


@router.post("/studies/{study_id}/runs/{run}/publish")
def publish_run(study_id: str, run: str) -> dict:
    from ..study.publish import publish

    study = _study(study_id)
    try:
        out = publish(study, _run(study, run))
    except StudyError as err:
        raise _refused(err) from err
    return {"cells": out["cells"], "status": out["status"], "changed": out["changed"], "findings": out["findings"],
            "runs": runs_of(study)}


class DiffIn(BaseModel):
    since: str | None = Field(None, description="the earlier run; the one before this run by default")


@router.post("/studies/{study_id}/runs/{run}/diff")
def diff_run(study_id: str, run: str, body: DiffIn | None = None) -> dict:
    from ..study.diff import diff

    study = _study(study_id)
    since = (body.since if body else None) or None
    try:
        out = diff(study, since and _run(study, since), _run(study, run))
    except StudyError as err:
        raise _refused(err) from err
    return {"changes": len(out["rows"]), "since": out["from"], "runs": runs_of(study)}


@router.get("/studies/{study_id}/runs/{run}/files")
def run_files(study_id: str, run: str) -> dict:
    """Every file of the run, by its path under runs/<run>, with its size."""
    study = _study(study_id)
    folder = study.runs / _run(study, run)
    if not folder.is_dir():
        raise refusal(404, "no_run", run=run, id=study_id)
    files = [{"path": p.relative_to(folder).as_posix(), "bytes": p.stat().st_size}
             for p in sorted(folder.rglob("*")) if p.is_file()]
    return {"run": run, "files": files}


@router.get("/studies/{study_id}/runs/{run}/files/{path:path}")
def run_file(study_id: str, run: str, path: str) -> FileResponse:
    study = _study(study_id)
    folder = study.runs / _run(study, run)
    try:
        target = inside(folder, *Path(path).parts)
    except ValueError:
        raise refusal(404, "file", file=path[:120], run=run) from None
    if not target.is_file():
        raise refusal(404, "file", file=path[:120], run=run)
    return FileResponse(target, filename=target.name)
