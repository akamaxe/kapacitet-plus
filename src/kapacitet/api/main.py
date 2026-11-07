"""Kapacitet+ API (FastAPI). Run with: python -m kapacitet serve  →  http://127.0.0.1:8000

Every JSON response that carries results includes a `provenance` block: when its own files were
computed, the full run behind them, the data sources, and which inputs are real and which
synthetic. OpenAPI documentation is served at /docs.
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import gzip
import hashlib
import io
import json
import math
import logging
import os
import threading
import time
import uuid
import warnings
from collections.abc import Iterable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__, actor, geo
from ..cache import memo, on_clear
from ..config import assumptions, settings
from ..engine.summaries import RATES as SUMMARY_RATES
from . import jobs, records
from .auth import ERRORS as AUTH_ERRORS
from .guard import ERRORS as GUARD_ERRORS
from .guard import Guard
from .. import seals
from ..ingest.provenance import load_manifest
from ..ingest.sources import SOURCES
from ..paths import RAW, WEB_DIST
from ..paths import processed_ro as processed

# The engine's libraries announce API changes on every call; the server log is for requests and
# failures, so those two categories are quietened and everything else still shows.
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
log = logging.getLogger("kapacitet.api")

# Live connection requests: how many may run at once, how many may wait for a slot, how long one
# may take once it runs, and how long a finished one stays in memory (it is also saved to disk, see
# the requests section). api/jobs.py holds them to it.
MAX_JOBS = int(os.environ.get("KAPACITET_MAX_JOBS", "4"))
MAX_QUEUED = int(os.environ.get("KAPACITET_MAX_QUEUED", "8"))
JOB_TIMEOUT_S = float(os.environ.get("KAPACITET_JOB_TIMEOUT_S", "120"))
JOB_TTL_S = jobs.TTL_S
POOL = jobs.JobPool(MAX_JOBS, MAX_QUEUED, JOB_TIMEOUT_S, JOB_TTL_S)
# Old saved assessments are purged once a day when the DSO sets a retention (api/records.py).
RETENTION_EVERY_S = 24 * 3600.0
# A stream of progress events sends a comment line when it has been quiet this long, so a proxy in
# between (the codespace's port forwarding) does not take a long stage for a dead connection.
KEEPALIVE_S = 15.0
# Which start of the server this is: the setup page waits for a new one after loading a bundle.
BOOT = uuid.uuid4().hex[:12]
# Largest CSV accepted per table by the network-import check.
CHECK_MAX_BYTES = 5_000_000


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # with a database, its schema must be the one this code writes: `kapacitet db migrate` first
    from .. import store

    if store.is_pg():
        from ..store import migrate

        pending = migrate.status()
        if pending["pending"] or pending["changed"]:
            raise RuntimeError(f"the database at KAPACITET_DATABASE_URL needs `kapacitet db migrate` "
                               f"(pending {pending['pending']}, changed {pending['changed']})")
        log.info("server state kept in PostgreSQL, scope %s", store.scope())
        from ..store import postgres

        others = postgres.other_scopes_when_empty()
        if others:      # a moved or unpacked data folder: a new path: scope, so an empty queue and chain
            log.warning("scope %s has nothing in the database yet, while %s do; set KAPACITET_STORE_SCOPE "
                        "to one of them if this data folder was moved", store.scope(), ", ".join(others[:5]))
    # one server writes a data folder at a time: a second one on the same folder refuses to start
    lock = records.claim_server_lock()
    # a decision committed just before the server last stopped is sealed now, if it was not yet
    try:
        from ..engine import queue as qu

        qu.recover()
    except Exception:  # noqa: BLE001 - the queue says what is wrong when it is next read
        log.exception("could not recover the queue")
    # load the model and the request baselines in the background, so the first request is fast
    threading.Thread(target=_warm, daemon=True).start()
    # applications left waiting when the server last stopped are taken up again, in order
    inbox.start()
    retention = asyncio.create_task(_purge_daily()) if records.retention_days() else None
    try:
        yield
    finally:
        if retention is not None:
            retention.cancel()
        records.release_server_lock(lock)
        if store.is_pg():
            from ..store import db

            db.close_all()


async def _purge_daily() -> None:
    """With KAPACITET_RETENTION_DAYS set, purge what is older at startup and then once a day."""
    while True:
        days = records.retention_days()
        if days:
            try:
                out = await asyncio.to_thread(records.purge, days)
                if out["assessments"]:
                    log.info("retention: purged %d assessments older than %d days", out["assessments"], days)
            except Exception:  # noqa: BLE001 - tried again tomorrow
                log.exception("retention purge failed")
        await asyncio.sleep(RETENTION_EVERY_S)


app = FastAPI(
    lifespan=lifespan,
    title="Kapacitet+ API",
    version=__version__,
    description="Digital twin of a Swedish LV neighbourhood: static hosting capacity vs Dynamic Operating "
                "Envelopes. Every result is a simulation; the provenance block of each response says "
                "which of its inputs are real and which synthetic.",
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def etag(request, call_next):
    """A weak ETag on the API's JSON, from its bytes: a browser that already holds an answer gets a
    304 for it. Streams (events), binaries and writes pass untouched."""
    response = await call_next(request)
    if (request.method != "GET" or not request.url.path.startswith("/api/v1/") or response.status_code != 200
            or not response.headers.get("content-type", "").startswith("application/json")):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    tag = 'W/"' + hashlib.sha1(body).hexdigest()[:20] + '"'
    headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "etag")}
    headers.update(ETag=tag, **{"Cache-Control": "no-cache"})
    if tag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers={"ETag": tag, "Cache-Control": "no-cache"})
    return Response(content=body, status_code=200, headers=headers, media_type=response.media_type)


# Host, origin, content type and rate limit, in front of everything above (api/guard.py); added
# last, so it runs first, and inside CORSMiddleware, so a refusal still carries its CORS headers.
app.add_middleware(Guard)

from .combos import BASIS_PATTERN, POLICY_PATTERN, resolve  # noqa: E402 - the routers come after the app
from . import inbox  # noqa: E402
from .apply import router as apply_router  # noqa: E402
from .auth import router as auth_router  # noqa: E402
from .datasets import router as datasets_router  # noqa: E402
from .evidence import router as evidence_router  # noqa: E402
from .operation import router as operation_router  # noqa: E402
from .setup import closed_because as setup_closed_because  # noqa: E402
from .setup import router as setup_router  # noqa: E402

app.include_router(evidence_router)
app.include_router(operation_router)
app.include_router(inbox.router)
app.include_router(apply_router)
app.include_router(datasets_router)
app.include_router(setup_router)
app.include_router(auth_router)
# The interface is served from the same origin, so cross-origin access is off unless an operator
# lists the origins that may call the API (comma-separated).
_CORS = [o.strip() for o in os.environ.get("KAPACITET_CORS_ORIGINS", "").split(",") if o.strip()]
if _CORS:
    app.add_middleware(CORSMiddleware, allow_origins=_CORS, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["*"])

API = "/api/v1"


# ------------------------------------------------------------------------------ helpers

# Which command writes each results file, so a missing one can say exactly what to run.
_WRITTEN_BY = {
    "national.json": "compute --stage extra --only national",
    "scenarios.json": "compute --stage extra --only scenarios",
    "tornado.json": "compute --stage extra --only tornado",
    "mv_band.json": "compute --stage extra --only mvband",
    "foresight.json": "compute --stage extra --only foresight",
    "intraday.json": "compute --stage extra --only intraday",
    "overinstallation.json": "compute --stage extra --only overinstallation",
    "consumption.json": "compute --stage extra --only consumption",
    "phases.json": "compute --stage extra --only phases",
    "validation.json": "compute --stage extra --only validation",
    "forecast.json": "forecast",
    "grid/customers.parquet": "build",
    "protection.json": "compute --stage extra --only protection",
    "operation/index.json": "compute --stage extra --only operation",
    "operation/dayahead/import.json": "compute --stage extra --only operation",
    "redispatch.json": "redispatch-report",
    "redispatch_sv.txt": "redispatch-report",
}


# Refusals the interface translates: `{code, message, params}` in the detail, `err.<code>` in
# web/src/lib/i18n. tests/test_messages.py holds both languages to these templates.
API_ERRORS = {
    "not_in_contract": "not part of the contract: {tables}",
    "too_large": "larger than {mb} MB: {tables}",
    "unreadable_csv": "{table}.csv could not be read: {detail}",
    "queue_busy": "the queue is being updated; try again when it finishes",
    "queue_empty": "the queue is empty: choose today's grid or the envelope scenario",
    "nothing_requested": "nothing requested: ask for solar, an EV charger or a heat pump",
    "battery_needs_solar": "a battery here stores curtailed solar, so it needs a solar request",
    "has_heat_pump": "this house already has a heat pump",
    "unknown_customer": "unknown customer",
    "busy": "busy: {n} requests are already being evaluated, try again shortly",
    "cancelled": "this evaluation was cancelled",
    "already_finished": "this evaluation has already finished",
    "bad_phases": "a charger is single-phase (1) or three-phase (3)",
    **GUARD_ERRORS,
    **AUTH_ERRORS,
}


def api_error(status: int, code: str, **params) -> HTTPException:
    """A refusal with a code and its parameters next to the English sentence."""
    return HTTPException(status, {"code": code, "message": API_ERRORS[code].format(**params), "params": params})


def queue_refusal(status: int, exc) -> HTTPException:
    return HTTPException(status, {"code": exc.code, "message": str(exc), "params": exc.params})


def command_for(name: str) -> str:
    return f"python -m kapacitet {_WRITTEN_BY.get(name, 'compute')}"


def not_computed(name: str) -> HTTPException:
    """404 that says what is missing and the command that makes it (the interface shows both)."""
    return HTTPException(404, {"code": "not_computed", "file": name, "command": command_for(name),
                               "message": f"{name} has not been computed yet"})


def _read(name: str) -> Any:
    path = processed("results", name)
    if not path.exists():
        raise not_computed(name)
    return json.loads(path.read_text(encoding="utf-8"))


@memo(maxsize=64)
def _cached(name: str, mtime: float) -> Any:
    return _read(name)


def result(name: str) -> Any:
    path = processed("results", name)
    if not path.exists():
        raise not_computed(name)
    return _cached(name, path.stat().st_mtime)


def result_path(name: str) -> Path:
    """The file behind result(name): what a response passes to provenance(files=...)."""
    return processed("results", name)


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


@memo(maxsize=16)
def _json_version(path: str, stamp: float) -> dict:
    try:
        out = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _small_json(path: Path) -> dict:
    """A small JSON file (meta.json, grid/summary.json), read once per version; {} when absent.
    Shared between calls: read it, never change it."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    return _json_version(str(path), stamp)


@memo(maxsize=4)
def _has_inventory(path: str, stamp: float) -> bool:
    """customers.parquet carries the operator's equipment (the importer's `existing_*` columns)."""
    try:
        import pyarrow.parquet as pq

        return any(name.startswith("existing_") for name in pq.read_schema(path).names)
    except Exception:  # noqa: BLE001 - an unreadable table simply has no inventory
        return False


def data_origin() -> dict[str, list[str]]:
    """Which inputs of this data folder are real and which synthetic, from what it holds: an
    operator's network (grid/summary.json says `imported`), metered household load (the
    temperature-corrected years, households/metered_<year>.npy) and an equipment inventory."""
    imported = _small_json(processed("grid", "summary.json")).get("imported") is True
    households = processed("households")
    metered = households.is_dir() and any(households.glob("metered_*.npy"))
    cust = processed("grid", "customers.parquet")
    try:
        inventory = _has_inventory(str(cust), cust.stat().st_mtime)
    except OSError:
        inventory = False
    real: list[str] = []
    synthetic: list[str] = []
    if imported:
        real.append("LV network topology, cables, cabinets, transformer and fuses (the operator's export)")
    else:
        real.append("buildings, addresses, streets and the substation's position (OpenStreetMap)")
        synthetic.append("LV network topology, cables, cabinets, transformer and fuses "
                         "(built on the OpenStreetMap streets)")
    real.append("weather (SMHI) and spot prices")
    if metered:
        real.append("household load (metered, temperature-corrected)")
    else:
        synthetic.append("household load (CREST/richardsonpy)")
    if inventory:
        real.append("existing PV, batteries, EV chargers and heat pumps (the operator's inventory)")
    else:
        synthetic.append("who has PV, a heat pump or an EV today (adoption shares, fixed seeds)")
    synthetic.append("heat-pump demand, EV charging and the sizing of new PV (modelled)")
    return {"real": real, "synthetic": synthetic}


def provenance(*sources: str, files: Iterable[Path] = ()) -> dict:
    """Where a response comes from.

    `computed_at` is the newest modification time among the response's own `files`, so a stage
    rerun alone (`compute --stage extra --only ...`) dates what it wrote; `run_id` is the same
    value under the name the interface already prints. Without files, both fall back to
    `full_run_at`: results/meta.json, which a compute without `--only` writes (`compute --stage
    extra` too, though it leaves capacity and the showcase as they were). `files` lists each file's
    own time, relative to the processed folder (or to the data folder, for the queue's), `sources`
    the attributions and `real`/`synthetic` the inputs.
    """
    full = _small_json(processed("results", "meta.json")).get("computed_at")
    root = processed()
    stamps: dict[str, float] = {}
    for f in files:
        f = Path(f)
        try:
            stamp = f.stat().st_mtime
        except OSError:
            continue
        for base in (root, root.parent):
            try:
                rel = f.relative_to(base).as_posix()
                break
            except ValueError:
                continue
        else:
            rel = f.name
        stamps[rel] = stamp
    computed = _iso(max(stamps.values())) if stamps else full
    return {
        "run_id": computed,
        "computed_at": computed,
        "full_run_at": full,
        "files": {rel: _iso(stamp) for rel, stamp in sorted(stamps.items())},
        "sources": [SOURCES[s].attribution for s in sources if s in SOURCES],
        **data_origin(),
    }


def with_prov(obj: Any, *sources: str, files: Iterable[Path] = ()) -> dict:
    return {"data": obj, "provenance": provenance(*sources, files=files)}


def _newest_under(folder: Path) -> float | None:
    newest = None
    if folder.is_dir():
        for f in folder.rglob("*"):
            try:
                if f.is_file():
                    stamp = f.stat().st_mtime
                    newest = stamp if newest is None or stamp > newest else newest
            except OSError:
                continue
    return newest


# ------------------------------------------------------------------------------ basics

# What each part of the interface needs on disk; /health reports which parts are ready.
_NEEDS = {
    "network": [("grid", "customers.parquet"), ("grid", "lines.parquet"), ("grid", "substation.json")],
    "results": [("results", "meta.json"), ("results", "showcase.json"), ("results", "hosting_summary.json"),
                ("results", "risk.json"), ("ui", "state_doe.json")],
    "extra": [("results", "scenarios.json"), ("results", "validation.json"), ("results", "national.json")],
    "requests": [("request", "static.json"), ("request", "doe.json")],
}


def results_ready() -> bool:
    """The results the screens need are on disk: the same test as /health's `results` part."""
    return all(processed(*f).exists() for f in _NEEDS["results"])


@app.get(f"{API}/health", tags=["system"])
def health() -> dict:
    """Liveness, the version, and which parts have their data (with the command for what is missing)."""
    parts = {}
    for part, files in _NEEDS.items():
        missing = ["/".join(f) for f in files if not processed(*f).exists()]
        parts[part] = {"ready": not missing, "missing": missing}
    commands = {"network": "python -m kapacitet build", "results": "python -m kapacitet compute",
                "extra": "python -m kapacitet compute --stage extra",
                "requests": "python -m kapacitet compute --stage extra --only requests"}
    for part, info in parts.items():
        if not info["ready"]:
            info["command"] = commands[part]
    from .. import datasets as ds

    return {"status": "ok", "version": __version__, "time": datetime.now(timezone.utc).isoformat(),
            "results_ready": parts["results"]["ready"], "parts": parts, "dataset": ds.current(), "boot": BOOT,
            # the setup page may take a data bundle from the browser (api/setup.py)
            "setup_upload": setup_closed_because() is None}


@memo(maxsize=2)
def _showcase_hour(stamp: float) -> int | None:
    """Hour of the reference year with the highest transformer loading without control (the demo's opening hour)."""
    p = processed("ui", "state_uncontrolled.json")
    if not p.exists():
        return None
    loading = json.loads(p.read_text(encoding="utf-8"))["aggregates"]["trafo_loading"]
    return int(max(range(len(loading)), key=loading.__getitem__))


@app.get(f"{API}/meta", tags=["system"])
def meta() -> dict:
    cfg = settings()
    out = {"site": cfg.site.name, "center": [cfg.site.center_lon, cfg.site.center_lat],
           "reference_year": cfg.reference_year, "weather_years": list(cfg.weather_years),
           "bidding_zone": cfg.site.bidding_zone, "version": __version__}
    for name in ("meta.json",):
        p = processed("results", name)
        if p.exists():
            out["run"] = json.loads(p.read_text(encoding="utf-8"))
    # the last full run is run.computed_at; a stage rerun alone since then shows here
    newest = _newest_under(processed("results"))
    out["results_updated_at"] = _iso(newest) if newest is not None else None
    g = processed("grid", "summary.json")
    if g.exists():
        out["grid"] = json.loads(g.read_text(encoding="utf-8"))
    s = processed("grid", "substation.json")
    if s.exists():
        out["substation"] = json.loads(s.read_text(encoding="utf-8"))
    u = processed("ui", "state_uncontrolled.json")
    out["showcase_hour"] = _showcase_hour(u.stat().st_mtime if u.exists() else 0.0)
    # an imported network: its own grid, and the reference network's weather, prices and households
    from .. import datasets as ds

    current = ds.current()
    out["dataset"] = {"id": current, "imported": current != ds.REFERENCE}
    if current != ds.REFERENCE:
        try:
            out["dataset"]["name"] = ds.record(current)["name"]
            out["site"] = out["dataset"]["name"]
        except ds.DatasetError:
            pass
        out["dataset"]["shared_from_reference"] = ["weather", "prices", "households"]
    return out


@app.get(f"{API}/sources", tags=["provenance"])
def sources() -> dict:
    manifest = load_manifest()
    files: dict[str, list] = {}
    for entry in manifest.values():
        files.setdefault(entry["source"], []).append({k: entry[k] for k in
                                                      ("id", "url", "fetched_at", "rows", "bytes", "sha256", "notes")})
    return {"sources": [dict(s.as_dict(), files=sorted(files.get(s.key, []), key=lambda f: f["id"]))
                        for s in SOURCES.values()],
            "n_files": len(manifest)}


_STATUS_CACHE: dict[str, Any] = {"t": 0.0, "data": None}


@app.get(f"{API}/sources/status", tags=["provenance"])
async def sources_status() -> dict:
    """Live reachability of the external APIs (cached for 5 minutes)."""
    if _STATUS_CACHE["data"] and time.time() - _STATUS_CACHE["t"] < 300:
        return _STATUS_CACHE["data"]
    ua = {"User-Agent": settings().user_agent}

    async def probe(client: httpx.AsyncClient, key: str, url: str) -> dict:
        t0 = time.perf_counter()
        try:
            r = await client.get(url, headers=ua, timeout=6.0)
            return {"key": key, "ok": r.status_code < 500, "status": r.status_code,
                    "ms": round(1000 * (time.perf_counter() - t0))}
        except Exception as exc:  # offline or blocked
            return {"key": key, "ok": False, "status": None, "error": type(exc).__name__,
                    "ms": round(1000 * (time.perf_counter() - t0))}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        res = await asyncio.gather(*[probe(client, s.key, s.health_url) for s in SOURCES.values() if s.health_url])
    data = {"checked_at": datetime.now(timezone.utc).isoformat(), "results": res}
    _STATUS_CACHE.update(t=time.time(), data=data)
    return data


@app.get(f"{API}/assumptions", tags=["provenance"])
def get_assumptions() -> dict:
    return {"assumptions": [a.__dict__ for a in assumptions().values()]}


# ---------------------------------------------------------------- network export check

class ExportCheck(BaseModel):
    """The contract CSV files as text, one entry per table."""

    files: dict[str, str] = Field(
        description="table name to CSV contents, for example {'buses': 'bus_id,kind\n...'}")


@app.get(f"{API}/contract", tags=["import"])
def get_contract() -> dict:
    """What an operator's network export has to contain, and what each column is for."""
    from ..importer.contract import ALL_TABLES, OPTIONAL_TABLES, as_markdown

    return {
        "markdown": as_markdown(),
        "tables": [
            {"name": t.name, "description": t.description, "key": t.key, "optional": t in OPTIONAL_TABLES,
             "columns": [c.__dict__ for c in t.columns]}
            for t in ALL_TABLES
        ],
    }


@memo(maxsize=1)
def _example_files(stamp: float) -> dict[str, str]:
    from ..importer.contract import TABLES
    from ..importer.export import export_tables

    tables = export_tables()
    return {t.name: tables[t.name].to_csv(index=False) for t in TABLES}


def _example() -> dict[str, str]:
    path = processed("grid", "net.json")
    if not path.exists():
        raise not_computed("net.json")
    return _example_files(path.stat().st_mtime)


@app.get(f"{API}/contract/example", tags=["import"])
def contract_example() -> dict:
    """The reference network in the contract's four tables, as CSV text: a worked example."""
    return {"files": _example(),
            "provenance": provenance("osm", "synthetic_grid", files=[processed("grid", "net.json")])}


@app.get(f"{API}/contract/example.zip", tags=["import"])
def contract_example_zip() -> Response:
    """The same four CSV files and the contract as README.md, zipped, to fill with one's own network."""
    import zipfile

    from ..importer.export import readme

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in _example().items():
            z.writestr(f"{name}.csv", text)
        z.writestr("README.md", readme())
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="kapacitet-network-contract-example.zip"'})


@app.post(f"{API}/check", tags=["import"])
def post_check(body: ExportCheck) -> dict:
    """Data-quality report on an export, before anybody promises a capacity for it."""
    import io

    import pandas as pd

    from ..importer.contract import BY_NAME
    from ..importer.validate import validate

    unknown = set(body.files) - set(BY_NAME)
    if unknown:
        raise api_error(422, "not_in_contract", tables=", ".join(sorted(unknown)))
    big = [name for name, text in body.files.items() if len(text.encode("utf-8")) > CHECK_MAX_BYTES]
    if big:
        raise api_error(413, "too_large", mb=CHECK_MAX_BYTES // 1_000_000, tables=", ".join(sorted(big)))
    data = {}
    for name, text in body.files.items():
        try:
            data[name] = pd.read_csv(io.StringIO(text))
        except Exception as err:
            raise api_error(422, "unreadable_csv", table=name, detail=str(err)) from err
    return validate(data, phase_cost_pct=_phase_cost_pct()).to_dict()


def _phase_cost_pct() -> float | None:
    """What not knowing the phases costs on the reference grid: the static capacity with every
    connection single-phase on unknown phases (median), against the balanced model."""
    try:
        cases = {c["key"]: c for c in result("phases").get("cases", [])}
    except HTTPException:
        return None
    bal, rnd = cases.get("balanced", {}), cases.get("single_random", {})
    if not bal.get("kw_ac") or rnd.get("kw_ac_median") is None:
        return None
    return 100.0 * (1.0 - float(rnd["kw_ac_median"]) / float(bal["kw_ac"]))


# ------------------------------------------------------------------------------ live "now" strip

_NOW_CACHE: dict[str, Any] = {"t": 0.0, "data": None}


@app.get(f"{API}/now", tags=["live"])
async def now() -> dict:
    """Latest SMHI temperature at Bromma and today's SE3 spot prices (live, cached 10 min)."""
    if _NOW_CACHE["data"] and time.time() - _NOW_CACHE["t"] < 600:
        return _NOW_CACHE["data"]
    cfg = settings()
    ua = {"User-Agent": cfg.user_agent}
    out: dict[str, Any] = {"fetched_at": datetime.now(timezone.utc).isoformat(), "online": False}
    today = date.today()
    async with httpx.AsyncClient(timeout=6.0, headers=ua) as client:
        try:
            r = await client.get(
                "https://opendata-download-metobs.smhi.se/api/version/1.0/parameter/1/station/"
                f"{cfg.temperature_station}/period/latest-hour/data.json")
            v = r.json()["value"][-1]
            out["temperature"] = {"value_c": float(v["value"]),
                                  "time": datetime.fromtimestamp(v["date"] / 1000, timezone.utc).isoformat(),
                                  "station": r.json().get("station", {}).get("name", ""), "source": "SMHI"}
            out["online"] = True
        except Exception as exc:
            out["temperature_error"] = type(exc).__name__
        prices_out = {}
        for d in (today, today + timedelta(days=1)):
            try:
                r = await client.get(f"https://www.elprisetjustnu.se/api/v1/prices/{d.year}/"
                                     f"{d.month:02d}-{d.day:02d}_{cfg.site.bidding_zone}.json")
                if r.status_code == 200:
                    rows = r.json()
                    prices_out[d.isoformat()] = [{"start": x["time_start"], "sek_kwh": x["SEK_per_kWh"]} for x in rows]
                    out["online"] = True
            except Exception:
                pass
        out["prices"] = prices_out
        out["price_attribution"] = "Elpriser tillhandahålls av Elpriset just nu.se"
        rows_today = prices_out.get(today.isoformat()) or []
        if rows_today:
            vals = [r["sek_kwh"] for r in rows_today]
            out["today_range"] = {"min_sek_kwh": min(vals), "max_sek_kwh": max(vals), "intervals": len(vals)}
            now_utc = datetime.now(timezone.utc)
            started = [r for r in rows_today if datetime.fromisoformat(r["start"]) <= now_utc]
            if started:
                out["current"] = started[-1]
    _NOW_CACHE.update(t=time.time(), data=out)
    return out


# ------------------------------------------------------------------------------ geography

@app.get(f"{API}/network", tags=["twin"])
def network() -> dict:
    """GeoJSON of the modelled neighbourhood: houses, cables, cabinets, substation, context."""
    if not processed("grid", "customers.parquet").exists():
        raise not_computed("grid/customers.parquet")
    stamp = tuple(p.stat().st_mtime if p.exists() else 0.0
                  for p in (processed("grid", "customers.parquet"), processed("results", "showcase.json"),
                            processed("results", "nodal.json"), processed("results", "consumption.json")))
    # the provenance is dated afresh on every call: it reads files the cache's stamp does not cover
    return {**_network(stamp), "provenance": provenance("osm", "synthetic_grid", files=[
        *(processed("grid", n) for n in ("customers.parquet", "lines.parquet", "buses.parquet", "substation.json")),
        result_path("showcase.json"), result_path("nodal.json"), result_path("consumption.json")])}


# the GeoJSON helpers live in kapacitet.geo, which a study's publication uses too
_num, _xy, _point = geo.num, geo.xy, geo.point
_house_geometry, _line_geometry = geo.house_geometry, geo.line_geometry


@memo(maxsize=2)
def _network(stamp: tuple) -> dict:
    import pandas as pd

    from ..engine.run import background_masks
    from ..engine.scenario import customers
    cust = customers()
    has_hp, has_ev = background_masks(len(cust))
    lines = pd.read_parquet(processed("grid", "lines.parquet"))
    buses = pd.read_parquet(processed("grid", "buses.parquet"))
    sub = json.loads(processed("grid", "substation.json").read_text(encoding="utf-8"))
    context = json.loads(processed("grid", "context.json").read_text(encoding="utf-8"))
    showcase = result("showcase.json") if processed("results", "showcase.json").exists() else {}
    firm = set(showcase.get("firm_cids", []))
    nonfirm = set(showcase.get("nonfirm_cids", []))
    nodal = result("nodal.json") if processed("results", "nodal.json").exists() else None
    # the import side of each house checked over every hour of every weather year, beside the
    # static one of nodal.json (consumption.json, with its own customer order)
    cons = result("consumption.json").get("nodal") if processed("results", "consumption.json").exists() else None
    import_ts = dict(zip(map(int, cons["customers"]), cons["timeseries_kw"])) if cons else {}
    feats = []
    for c in cust.itertuples():
        feats.append({"type": "Feature", "geometry": _house_geometry(c.polygon, c.lon, c.lat),
                      "properties": {
                          "layer": "house", "cid": int(c.cid), "bus": int(c.bus), "address": c.address or None,
                          "osm_id": c.osm_id, "building": c.building, "fuse_a": int(c.fuse_a),
                          "fuse_kva": _num(c.fuse_kva), "floor_m2": _num(c.floor_m2), "levels": _num(c.levels),
                          "kwp": _num(c.kwp), "inverter_kw": _num(c.inverter_kw), "azimuth": _num(c.azimuth),
                          "tilt": _num(c.tilt), "east_west": bool(c.east_west), "feeder": int(c.feeder),
                          "dist_m": _num(c.dist_m), "lon": _num(c.lon), "lat": _num(c.lat),
                          "pv_status": "firm" if c.cid in firm else ("nonfirm" if c.cid in nonfirm else "none"),
                          "nodal_pv_kw": float(nodal["pv_kw"][int(c.cid)]) if nodal else None,
                          "nodal_import_kw": float(nodal["import_kw"][int(c.cid)]) if nodal else None,
                          "nodal_import_kw_ts": _num(import_ts.get(int(c.cid))),
                          # synthetic: who has a heat pump or an EV today (fixed seeds, engine/run.py)
                          "has_hp": bool(has_hp[int(c.cid)]), "has_ev": bool(has_ev[int(c.cid)]),
                      }})
    for ln in lines.itertuples():
        feats.append({"type": "Feature", "geometry": _line_geometry(getattr(ln, "coords", None)),
                      "properties": {"layer": "cable", "line": int(ln.line), "kind": ln.kind, "std_type": ln.std_type,
                                     "length_m": round(float(ln.length_m), 1), "parallel": int(ln.parallel),
                                     "feeder": int(ln.feeder), "downstream": int(ln.downstream),
                                     "from_bus": int(ln.from_bus), "to_bus": int(ln.to_bus)}})
    for b in buses.itertuples():
        if b.kind in ("cabinet", "junction"):
            feats.append({"type": "Feature", "geometry": _point(b.lon, b.lat),
                          "properties": {"layer": b.kind, "bus": int(b.bus), "feeder": int(b.feeder),
                                         "dist_m": round(float(b.dist_m), 1)}})
    feats.append({"type": "Feature", "geometry": _point(sub.get("lon"), sub.get("lat")),
                  "properties": {"layer": "substation", **{k: (_num(v) if isinstance(v, float) else v)
                                                           for k, v in sub.items() if k not in ("polygon",)}}})
    for c in context:
        ring = [_xy(p[0], p[1]) for p in c["polygon"]]
        if ring and all(ring):
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                          "properties": {"layer": "context", "building": c["building"]}})
    lons = cust["lon"].to_numpy(dtype=float)
    lats = cust["lat"].to_numpy(dtype=float)
    ok = np.isfinite(lons) & np.isfinite(lats)
    # a network without coordinates has no box to frame: the map says so instead of guessing
    bbox = [float(lons[ok].min()), float(lats[ok].min()), float(lons[ok].max()), float(lats[ok].max())] if ok.any() else None
    return {"type": "FeatureCollection", "features": feats,
            "bbox": bbox,
            "bus_order": buses["bus"].tolist()}


@app.get(f"{API}/zones", tags=["national"])
def zones() -> Response:
    path = RAW / "zones" / "se_bidding_zones.geojson"
    if not path.exists():
        raise HTTPException(404, "zones not fetched")
    return Response(path.read_bytes(), media_type="application/geo+json")


# ------------------------------------------------------------------------------ results

@app.get(f"{API}/national", tags=["national"])
def national() -> dict:
    return with_prov(result("national.json"), "esett", "elprisetjustnu", "zones", "capacity_maps",
                     files=[result_path("national.json")])


@app.get(f"{API}/series/{{year}}", tags=["twin"])
def series(year: int) -> dict:
    return with_prov(result(f"series_{year}.json"), "smhi_strang", "smhi_metobs", "elprisetjustnu",
                     files=[result_path(f"series_{year}.json")])


STATES = ("static", "uncontrolled", "doe")


def _state_name(name: str) -> str:
    if name not in STATES:
        raise HTTPException(404, f"unknown state {name}")
    return name


def _state_path(name: str, suffix: str, basis: str | None, policy: str | None):
    """A scenario's state file. The envelope scenario follows the rule and basis asked for (the DSO's
    rule by default); the other two do not depend on them."""
    name = _state_name(name)
    if name == "doe":
        return resolve(basis, policy).state(suffix)
    return processed("ui", f"state_{name}.{suffix}")


@app.get(f"{API}/state/{{name}}", tags=["twin"])
def state_header(name: str, basis: str | None = Query(None, pattern=BASIS_PATTERN),
                 policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> dict:
    path = _state_path(name, "json", basis, policy)
    if not path.exists():
        raise HTTPException(404, f"unknown state {name}")
    return json.loads(path.read_text(encoding="utf-8"))


@memo(maxsize=12)
def _state_gz(path: str, stamp: float) -> bytes:
    """Gzip the uint8 grid state once per file version (4 MB raw, ~1.8 MB compressed)."""
    return gzip.compress(open(path, "rb").read(), 6)


@app.get(f"{API}/state/{{name}}/bin", tags=["twin"])
def state_bin(name: str, request: Request, basis: str | None = Query(None, pattern=BASIS_PATTERN),
              policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> Response:
    path = _state_path(name, "bin", basis, policy)
    if not path.exists():
        raise HTTPException(404, f"unknown state {name}")
    # the default follows the DSO's setting, so a cache must not outlive a change of it
    headers = {"Cache-Control": "no-cache"}
    if "gzip" in request.headers.get("accept-encoding", ""):
        body = _state_gz(str(path), path.stat().st_mtime)
        headers["Content-Encoding"] = "gzip"
    else:
        body = path.read_bytes()
    return Response(body, media_type="application/octet-stream", headers=headers)


@app.get(f"{API}/hosting", tags=["hosting"])
def hosting_summary() -> dict:
    names = ["hosting_summary.json", "zcurve.json", "nodal.json"]
    out = {"summary": result("hosting_summary.json"), "curve": result("zcurve.json"), "nodal": result("nodal.json")}
    # each optional part is left out until its stage has run, and the screens say which one to run
    for name in ("tornado.json", "foresight.json", "intraday.json", "mv_band.json", "consumption.json"):
        if processed("results", name).exists():
            out[name.split(".")[0]] = result(name)
            names.append(name)
    return with_prov(out, "smhi_strang", "smhi_metobs", "osm", "synthetic_grid", files=map(result_path, names))


@app.get(f"{API}/showcase", tags=["doe"])
def showcase(heatmaps: bool = Query(True, description="false: leave out the hour-by-month heatmaps (/showcase/heatmaps)")) -> dict:
    body = {"showcase": result("showcase.json")}
    names = ["showcase.json"]
    if heatmaps:
        body["heatmaps"] = result("heatmaps.json")
        names.append("heatmaps.json")
    return with_prov(body, "smhi_strang", "smhi_metobs", "elprisetjustnu", "osm", "synthetic_grid",
                     files=map(result_path, names))


@app.get(f"{API}/showcase/heatmaps", tags=["doe"])
def showcase_heatmaps() -> dict:
    """When the envelopes bind, hour by month: fetched by the section that draws it, when it nears."""
    return with_prov(result("heatmaps.json"), "smhi_strang", "smhi_metobs", "synthetic_grid", files=[result_path("heatmaps.json")])


@app.get(f"{API}/risk", tags=["doe"])
def risk() -> dict:
    return with_prov(result("risk.json"), "smhi_strang", "smhi_metobs", files=[result_path("risk.json")])


@app.get(f"{API}/scenarios", tags=["scenarios"])
def scenarios() -> dict:
    return with_prov(result("scenarios.json"), "smhi_strang", "smhi_metobs", "scb", files=[result_path("scenarios.json")])


@app.get(f"{API}/validation", tags=["provenance"])
def validation() -> dict:
    return with_prov(result("validation.json"), "smhi_strang", "smhi_metobs", "esett", "scb", files=[result_path("validation.json")])


@app.get(f"{API}/forecast", tags=["doe"])
def forecast_schemes() -> dict:
    """The published envelopes against three day-ahead schemes: what perfect foresight is worth."""
    return with_prov(result("forecast.json"), "smhi_strang", "smhi_metobs", "synthetic_grid", files=[result_path("forecast.json")])


@app.get(f"{API}/protection", tags=["hosting"])
def protection() -> dict:
    """Whether a fault at the far end of each feeder, and at each house, blows its fuse in time."""
    return with_prov(result("protection.json"), "osm", "synthetic_grid", files=[result_path("protection.json")])


@app.get(f"{API}/phases", tags=["hosting"])
def phases() -> dict:
    """Static capacity when single-phase inverters and unknown phases are modelled."""
    return with_prov(result("phases.json"), "osm", "synthetic_grid", files=[result_path("phases.json")])


def price_sources(prices: dict | None) -> tuple[str, ...]:
    """The attributions a price-derived figure owes: elprisetjustnu always, and ENTSO-E with the ECB's
    rate when its years are in the range (summaries.price_basis)."""
    keys = set((prices or {}).get("source_keys") or ())
    return ("elprisetjustnu", "entsoe", "ecb_exr") if "entsoe" in keys else ("elprisetjustnu",)


@app.get(f"{API}/overinstallation", tags=["hosting"])
def overinstallation() -> dict:
    """Spot value delivered and curtailed as solar grows past the static limit, and the last kW."""
    from ..engine.summaries import overinstallation_summary

    out = overinstallation_summary(result("overinstallation.json"))
    return with_prov(out, "smhi_strang", "smhi_metobs", *price_sources(out.get("prices")), "synthetic_grid",
                     files=[result_path("overinstallation.json")])


@app.get(f"{API}/reinforcement", tags=["hosting"])
def reinforcement() -> dict:
    """A bigger transformer at Ei's norm cost, against connecting the same houses with envelopes."""
    from ..config import A
    from ..engine.summaries import overinstallation_summary
    from ..engine.summaries import reinforcement as reinforce

    keys = ("reinforce_trafo_500_sek", "reinforce_trafo_800_sek", "reinforce_station_800_sek",
            "reinforce_urban_supplement_sek", "reinforce_price_level_year")
    over = overinstallation_summary(result("overinstallation.json"))
    return with_prov(reinforce(result("tornado.json"), over, {k: A(k) for k in keys}),
                     "smhi_strang", "smhi_metobs", *price_sources(over.get("prices")), "synthetic_grid",
                     files=[result_path("tornado.json"), result_path("overinstallation.json")])


# the rates /business also prices the horizon at: the one in force and the two Ei has put forward
# for 2028-2031 (assumptions.toml, with their sources), the same as `study options`
BUSINESS_RATES = SUMMARY_RATES


@app.get(f"{API}/business", tags=["hosting"])
def business(years: int | None = Query(None, ge=1, le=40)) -> dict:
    """The DSO's case for deferring the reinforcement with envelopes: what deferring is worth at Ei's
    real rate against what the curtailment costs the houses, for every horizon up to 20 years."""
    from .. import dso
    from ..config import A
    from ..engine.summaries import business as case

    horizon = years or dso.load().deferral_years
    reinf = reinforcement()["data"]
    rates = {k: A(k) for k in BUSINESS_RATES}
    return with_prov(case(reinf, A("ei_wacc_real_pct"), horizon, rates=rates),
                     *price_sources(reinf.get("prices")), "synthetic_grid",
                     files=[result_path("tornado.json"), result_path("overinstallation.json")])


# ---------------------------------------------------------------- annual filing and audit trail

def _combo_json(combo, name: str) -> dict:
    path = combo.result(name)
    if not path.exists():
        raise not_computed(name)
    return json.loads(path.read_text(encoding="utf-8"))


@app.get(f"{API}/compliance", tags=["compliance"])
def compliance_report(basis: str | None = Query(None, pattern=BASIS_PATTERN),
                      policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> dict:
    """The annual filing for conditional connection agreements, with the worst hour's index, for a
    sharing rule (the DSO's by default) and a basis (perfect foresight by default)."""
    from ..engine.summaries import hour_of_timestamp

    combo = resolve(basis, policy)
    rep = dict(_combo_json(combo, "compliance.json"))
    rep.setdefault("basis", combo.basis)
    rep["policy"] = combo.policy
    # the code of Ei's position alone, so the interface writes the words around it in its language
    # (filings computed before the field existed carry it inside the sentence)
    rep.setdefault("reporting_basis_code", str(rep.get("reporting_basis", "")).split(",")[0].strip())
    worst = rep.get("worst_uncontrolled") or {}
    if worst.get("timestamp"):
        rep["worst_uncontrolled"] = {**worst, "hour": hour_of_timestamp(worst["timestamp"], rep["year"])}
    return with_prov(rep, "smhi_strang", "smhi_metobs", "osm", "synthetic_grid",
                     files=[combo.result("compliance.json")])


@app.get(f"{API}/compliance.txt", tags=["compliance"])
def compliance_text(basis: str | None = Query(None, pattern=BASIS_PATTERN),
                    policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> Response:
    combo = resolve(basis, policy)
    path = combo.result("compliance.txt")
    if not path.exists():
        raise not_computed("compliance.txt")
    year = _combo_json(combo, "compliance.json").get("year", "")
    return Response(path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="kapacitet-filing-{year}-{combo.basis}-{combo.policy}.txt"'})


@app.get(f"{API}/redispatch", tags=["compliance"])
def redispatch() -> dict:
    """The year's non-market-based redispatch (Regulation (EU) 2019/943, art. 13.4): the solar the
    conditional agreements curtailed on the day-ahead basis that is issued, with the compensation
    paid and the market alternatives recorded. Written by `kapacitet redispatch-report`."""
    return with_prov(result("redispatch.json"), "smhi_strang", "smhi_metobs", "osm", "synthetic_grid",
                     files=[result_path("redispatch.json")])


@app.get(f"{API}/redispatch_sv.txt", tags=["compliance"])
def redispatch_text() -> Response:
    """The same report as a page in Swedish, to file."""
    path = result_path("redispatch_sv.txt")
    if not path.exists():
        raise not_computed("redispatch_sv.txt")
    year = _read("redispatch.json").get("year", "") if result_path("redispatch.json").exists() else ""
    return Response(path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="kapacitet-omdirigering-{year}.txt"'})


@app.get(f"{API}/kpis", tags=["compliance"])
def kpis() -> dict:
    """The investor metrics the product computes itself (kapacitet/metrics.py), from this data
    folder as it stands: each with its source file and its period, `not_available` with the
    command that writes a missing source, and the owner's own metrics listed, never filled in.
    The same document `kapacitet metrics` writes to results/metrics.json. A read: with sign-in
    on, any role."""
    from .. import metrics

    doc = metrics.build()
    sources = [processed().parent / m["source"] for m in doc["metrics"]
               if m["status"] == "ok" and "*" not in m["source"]]
    return with_prov(doc, *([] if doc["subject"].get("imported") else ["synthetic_grid"]), files=sources)


@memo(maxsize=4)
def _audit_table(path: str, stamp: float) -> tuple[list[str], list[dict]]:
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _audit_path(basis: str | None = None, policy: str | None = None) -> Path:
    path = resolve(basis, policy).result("audit_trail.csv")
    if not path.exists():
        raise not_computed("audit_trail.csv")
    return path


def _audit(cid: int | None, basis: str | None = None, policy: str | None = None) -> tuple[list[str], list[dict]]:
    path = _audit_path(basis, policy)
    cols, rows = _audit_table(str(path), path.stat().st_mtime)
    if cid is not None:
        rows = [r for r in rows if int(r["customer_id"]) == cid]
    return cols, rows


def _audit_value(key: str, value: str):
    if key == "necessary":
        return value == "True"
    if value == "" and key == "uncontrolled_pct":
        return None
    return _AUDIT_TYPES.get(key, str)(value)


_AUDIT_TYPES = {"hour": int, "customer_id": int, "desired_kw": float, "granted_kw": float,
                "curtailed_kwh": float, "uncontrolled_pct": float, "rating_pct": float}


@app.get(f"{API}/audit", tags=["compliance"])
def audit(cid: int | None = Query(None, ge=0), limit: int = Query(200, ge=1, le=5000),
          offset: int = Query(0, ge=0), basis: str | None = Query(None, pattern=BASIS_PATTERN),
          policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> dict:
    """Every curtailed customer-hour of the filing year, with the part of the grid that was full."""
    cols, rows = _audit(cid, basis, policy)
    page = [{k: _audit_value(k, v) for k, v in r.items()} for r in rows[offset:offset + limit]]
    return {"columns": cols, "total": len(rows), "offset": offset, "limit": limit, "rows": page,
            "provenance": provenance("smhi_strang", "osm", "synthetic_grid", files=[_audit_path(basis, policy)])}


@app.get(f"{API}/audit.csv", tags=["compliance"])
def audit_csv(cid: int | None = Query(None, ge=0), basis: str | None = Query(None, pattern=BASIS_PATTERN),
              policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> Response:
    cols, rows = _audit(cid, basis, policy)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    name = "kapacitet-audit-trail" + (f"-house-{cid}" if cid is not None else "") + ".csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get(f"{API}/envelopes/{{cid}}", tags=["doe"])
def envelope(cid: int, basis: str | None = Query(None, pattern=BASIS_PATTERN),
             policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> dict:
    combo = resolve(basis, policy)
    cids = combo.nonfirm_cids()
    prov = provenance("smhi_strang", "smhi_metobs", "synthetic_grid", files=[combo.envelopes])
    if cid not in cids:
        return {"cid": cid, "nonfirm": False, "basis": combo.basis, "policy": combo.policy, "provenance": prov}
    env = np.load(combo.envelopes, mmap_mode="r")
    row = np.asarray(env[cids.index(cid)], dtype=float)
    return {"cid": cid, "nonfirm": True, "basis": combo.basis, "policy": combo.policy,
            "envelope_kw": np.round(row, 2).tolist(), "provenance": prov}


# ------------------------------------------------------------------------------ live requests

class RequestIn(BaseModel):
    cid: int = Field(..., ge=0, description="customer (house) index")
    pv_kwp: float = Field(10.0, ge=0, le=40, description="PV system size, kWp DC")
    ev_kw: float = Field(0.0, ge=0, le=22, description="EV charger power, kW (0 = none)")
    hp: bool = Field(False, description="add a heat pump sized to the house (not for a house that has one)")
    bat_kwh: float = Field(0.0, ge=0, le=30, description="home battery, usable kWh (needs a PV request)")
    ev_phases: int = Field(0, ge=0, le=3, description="charger phases: 1 or 3; 0 for the usual (the small "
                                                       "charger single-phase, larger ones three-phase)")
    baseline: str = Field("static", pattern="^(static|doe|queue)$",
                          description="static: today's approved grid; doe: the DOE scenario; "
                                      "queue: either of them with the requests approved in the queue")

    def engine_request(self):
        from ..engine import request as rq
        return rq.Request(cid=self.cid, pv_kwp=self.pv_kwp, ev_kw=self.ev_kw, hp=self.hp, bat_kwh=self.bat_kwh,
                          ev_phases=self.ev_phases,
                          baseline=self.baseline)


_ENGINE: dict[str, Any] = {}
_ENGINE_LOCK = threading.Lock()
# the pool's jobs, by id (api/jobs.py): what the progress stream and the result read
_JOBS = POOL.jobs


@on_clear
def _forget_engine() -> None:
    """The network model and masks are read from data/processed: cache.clear_all() drops them."""
    with _ENGINE_LOCK:
        _ENGINE.clear()


def _engine() -> dict:
    with _ENGINE_LOCK:
        if not _ENGINE:
            from ..engine.run import background_masks, model
            m = model()
            hp, ev = background_masks(m.C)
            _ENGINE.update(model=m, hp=hp, ev=ev)
        return _ENGINE


def _warm() -> None:
    """Load the model and the request baselines in the background at startup."""
    if not processed("results", "showcase.json").exists():
        return   # nothing computed yet: /health and the setup screen say what to run
    try:
        for name in ("static", "uncontrolled", "doe"):        # compress the map payloads once
            path = processed("ui", f"state_{name}.bin")
            if path.exists():
                _state_gz(str(path), path.stat().st_mtime)
        eng = _engine()
        from ..engine import request as rq
        from ..engine.netmodel3ph import get_model3ph
        get_model3ph()          # the unbalanced model the phase check uses, built once
        cfg = settings()
        for name in rq.BASELINES:
            for y in cfg.weather_years:
                if processed("request", f"{name}_{y}.npz").exists():
                    rq._state(name, y)
        from ..engine.scenario import year_profiles
        for y in cfg.weather_years:
            year_profiles(y)
        rq.evaluate(eng["model"], rq.Request(cid=0, pv_kwp=5.0, baseline="static"), eng["hp"], eng["ev"])
        log.info("engine warm")
    except Exception as exc:  # pragma: no cover
        log.warning("warm-up failed: %s", exc)


def _evaluation(body: RequestIn, ctx=None, save: bool = True) -> jobs.Work:
    """An evaluation as the pool runs it: the engine with the job's check in its context, then the
    answer stored in the job and, for /requests, saved as an assessment. The `done` event is
    handed back to the pool, which adds it once the job is marked done, so the browser fetches the
    answer only after it is stored."""

    who = actor.current()       # the person who asked, carried into the job's thread for the seal

    def work(job: dict, check) -> list[dict]:
        from ..engine import request as rq

        held: list[dict] = []

        def emit(stage: str, payload: dict) -> None:
            ev = {"stage": stage, "payload": payload, "t": time.time()}
            (held if stage == "done" else job["events"]).append(ev)

        eng = _engine()
        here = rq.Context(body.baseline, check=check) if ctx is None else dataclasses.replace(ctx, check=check)
        result = rq.evaluate(eng["model"], body.engine_request(), eng["hp"], eng["ev"], emit=emit, ctx=here)
        check()       # stopped while the last stage ran: the late answer is dropped, nothing is saved
        job["result"] = result
        if save:
            try:
                with actor.acting_as(who):
                    records.save(_record(job["id"], job, result))
            except Exception:   # the answer still reaches the screen; only its saved copy is lost
                log.exception("could not save assessment %s", job["id"])
        return held

    return work


def _submit(body: RequestIn, ctx=None, save: bool = True, source: str = "dso") -> dict:
    """Admit an evaluation to the pool, or refuse it with 429 `busy` when every slot and the line are full."""
    try:
        return POOL.submit(uuid.uuid4().hex[:12], _evaluation(body, ctx, save), request=body.model_dump(),
                           source=source)
    except jobs.Busy as exc:
        raise HTTPException(429, {"code": "busy", "message": API_ERRORS["busy"].format(n=exc.waiting),
                                  "params": {"n": exc.waiting}}, headers={"Retry-After": "5"}) from exc


def _check_cid(cid: int) -> None:
    from ..engine.scenario import customers
    if cid >= len(customers()):
        raise api_error(422, "unknown_customer")


def _context_for(body: RequestIn):
    """The grid a request meets: None for a published baseline, the queue's context for `queue`."""
    if body.baseline != "queue":
        return None
    from ..engine import queue as qu
    if _queue_busy():
        raise api_error(409, "queue_busy")
    ctx = qu.context()
    if ctx is None:
        raise api_error(409, "queue_empty")
    if body.cid in ctx.overrides:
        raise queue_refusal(422, qu.QueueError("already_approved"))
    return ctx


def _check_request(body: RequestIn) -> None:
    """Refuse what the engine cannot answer honestly, before any work."""
    _check_cid(body.cid)
    if body.pv_kwp <= 0 and body.ev_kw <= 0 and not body.hp:
        raise api_error(422, "nothing_requested")
    if body.bat_kwh > 0 and body.pv_kwp <= 0:
        raise api_error(422, "battery_needs_solar")
    if body.hp and bool(_engine()["hp"][body.cid]):
        raise api_error(422, "has_heat_pump")
    if body.ev_phases == 2:
        raise api_error(422, "bad_phases")


def _sweep_jobs() -> None:
    """Forget finished jobs after JOB_TTL_S and give up on any that ran past JOB_TIMEOUT_S."""
    POOL.sweep()


_REQUEST_SOURCES = ("smhi_strang", "smhi_metobs", "elprisetjustnu", "osm", "synthetic_grid")


def _request_files(baseline: str | None = None) -> list[Path]:
    """What a live assessment is computed against: the request baselines and the showcase, and,
    against the queue, the queue's decisions and the grid states its approvals left."""
    files = [processed("request", "static.json"), processed("request", "doe.json"), result_path("showcase.json")]
    if baseline == "queue":
        from ..engine import queue as qu

        files += [qu.folder() / "queue.json", *qu.state_files()]
    return files


def make_record(job_id: str, created: float, request: dict, result: dict) -> dict:
    """records.make_record with the provenance of the grid the API evaluated it against."""
    prov = provenance(*_REQUEST_SOURCES, files=_request_files(request.get("baseline")))
    if request.get("baseline") == "queue":
        from ..engine import queue as qu

        held = qu.provenance_entry()      # the queue in the database: its row stands for its files
        if held is not None:
            prov["queue"] = held
            if held["decided_at"] and (prov["computed_at"] is None or held["decided_at"] > prov["computed_at"]):
                prov["computed_at"] = prov["run_id"] = held["decided_at"]
    return records.make_record(job_id, created, request, result, prov)


def _record(job_id: str, job: dict, result: dict) -> dict:
    return make_record(job_id, job["created"], job["request"], result)


def evaluate_record(request: dict) -> dict:
    """Evaluate a request (a RequestIn as a dict) against the grid it names, for the inbox's worker,
    which already holds the queue's lock: the queue's context is read directly."""
    from ..engine import queue as qu
    from ..engine import request as rq

    body = RequestIn(**request)
    ctx = qu.context() if body.baseline == "queue" else None
    eng = _engine()
    return rq.evaluate(eng["model"], body.engine_request(), eng["hp"], eng["ev"], ctx=ctx)


def _job_id(job_id: str) -> str:
    if not records.valid_id(job_id):
        raise HTTPException(404, "unknown request")
    return job_id


@app.get(f"{API}/requests", tags=["requests"])
def list_requests(limit: int = Query(30, ge=1, le=200)) -> dict:
    """The saved assessments, newest first: the request, the house and the verdicts."""
    return {"items": records.listing(limit)}


@app.post(f"{API}/requests", tags=["requests"])
def create_request(body: RequestIn, source: str = "dso") -> dict:
    """Start a connection-request evaluation; follow it with GET /requests/{id}/events (SSE).

    It starts at once when one of KAPACITET_MAX_JOBS slots is free, otherwise it waits in line
    (the stream says `queued` with its place); a full line is a 429 `busy`."""
    _check_request(body)
    ctx = _context_for(body)
    job = _submit(body, ctx, source=source)
    job_id = job["id"]
    return {"id": job_id, "status": job["status"], "events": f"{API}/requests/{job_id}/events",
            "result": f"{API}/requests/{job_id}"}


@app.delete(f"{API}/requests/{{job_id}}", tags=["requests"])
def cancel_request(job_id: str, source: str | None = None) -> dict:
    """Stop an evaluation that is waiting or running: it leaves the line at once, or stops at the
    engine's next check, and nothing of it is saved. A finished one is a 409 `already_finished`,
    also when it was already swept from memory but is saved. `source` limits the call to jobs
    started from that route (the portal cannot stop a planner's evaluation)."""
    jid = _job_id(job_id)
    job = POOL.jobs.get(jid)
    if job is not None and source is not None and job.get("source") != source:
        raise HTTPException(404, "unknown request")
    status = POOL.cancel(jid)
    if status is None:
        if records.load(jid, resolve=False) is not None:
            raise api_error(409, "already_finished")
        raise HTTPException(404, "unknown request")
    if status != "cancelled":
        raise api_error(409, "already_finished")
    return {"id": job_id, "status": status}


@app.get(f"{API}/requests/{{job_id}}/events", tags=["requests"])
async def request_events(job_id: str) -> StreamingResponse:
    if _job_id(job_id) not in _JOBS:
        raise HTTPException(404, "unknown request")
    return _stream(_JOBS, job_id, JOB_TIMEOUT_S)


def _stream(jobs: dict, job_id: str, timeout_s: float, keepalive_s: float | None = None) -> StreamingResponse:
    """Server-sent events for a background job: every stage as it finishes, then `end`. A quiet
    stretch gets a comment line every `keepalive_s` (EventSource ignores it)."""
    keepalive_s = KEEPALIVE_S if keepalive_s is None else keepalive_s

    async def stream():
        sent = 0
        start = last = time.time()
        while True:
            job = jobs[job_id]
            if job.get("status") == "queued":    # the time allowed counts from when it starts
                start = time.time()
            while sent < len(job["events"]):
                ev = job["events"][sent]
                sent += 1
                last = time.time()
                yield f"event: {ev['stage']}\ndata: {json.dumps(ev['payload'], default=_json_default)}\n\n"
            if job.get("closed") and sent >= len(job["events"]):
                yield "event: end\ndata: {}\n\n"
                return
            if time.time() - start > timeout_s:
                yield "event: timeout\ndata: {}\n\n"
                return
            if time.time() - last >= keepalive_s:
                last = time.time()
                yield ": keepalive\n\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _finished(job_id: str) -> dict:
    """A finished assessment, from memory while it is fresh and from disk afterwards."""
    _sweep_jobs()
    job = _JOBS.get(_job_id(job_id))
    if job is not None:
        if job["status"] in ("queued", "running"):
            raise HTTPException(202, job["status"])
        if job["status"] == "error":
            raise HTTPException(500, job.get("error", "failed"))
        if job["status"] == "cancelled":
            raise api_error(409, "cancelled")
    rec = records.load(job_id)
    if rec is not None:
        return rec
    if job is not None:   # finished but not saved (disk trouble): still answer from memory
        return _record(job_id, job, job["result"])
    raise HTTPException(404, "unknown request")


@app.get(f"{API}/requests/{{job_id}}", tags=["requests"])
def request_result(job_id: str, download: bool = Query(False)) -> JSONResponse:
    """The full answer; `download=true` returns the whole record as a file."""
    try:
        rec = _finished(job_id)
    except HTTPException as exc:
        if exc.status_code == 202:
            return JSONResponse({"status": exc.detail}, status_code=202)
        raise
    if download:
        return JSONResponse(rec, headers={"Content-Disposition": f'attachment; filename="kapacitet-assessment-{job_id}.json"'})
    body = dict(rec["result"])
    body.update(id=rec["id"], created=rec["created"], provenance=rec["provenance"], version=rec.get("version"))
    entry = seals.entry_for("assessment", job_id)
    if entry:
        body["seal"] = {k: entry[k] for k in ("digest", "at", "seq", "fingerprint")}
    return JSONResponse(body)


@app.get(f"{API}/requests/{{job_id}}/owner-case", tags=["requests"])
def request_owner_case(job_id: str, years: int | None = Query(None, ge=1, le=40)) -> dict:
    """Connect now with the envelope, or wait for the reinforcement: what the house's solar is worth
    during the wait at retail prices, from the saved answer (economics.owner_case)."""
    from .. import dso
    from ..config import A
    from ..engine.economics import owner_case

    try:
        rec = _finished(job_id)
    except HTTPException as exc:
        if exc.status_code == 202:
            raise HTTPException(409, "still running") from exc
        raise
    horizon = years or dso.load().deferral_years
    out = owner_case(rec["result"], horizon, A("household_discount_rate_pct"), datetime.now(timezone.utc).year)
    if out is None:
        raise HTTPException(404, "this assessment has no solar to value")
    return {"id": job_id, **out}


# hourly columns of an assessment's CSV, in the order a spreadsheet reader expects them
_HOURLY = (("pv_kw", "pv_kw"), ("desired_kw", "desired_export_kw"), ("envelope_kw", "export_envelope_kw"),
           ("curtailed_kw", "curtailed_kw"), ("import_envelope_kw", "import_envelope_kw"),
           ("hp_kw", "heat_pump_kw"), ("hp_unmet_kw", "heat_pump_above_envelope_kw"),
           ("battery_charge_kw", "battery_charge_kw"), ("battery_discharge_kw", "battery_discharge_kw"),
           ("battery_soc_kwh", "battery_stored_kwh"))


@app.get(f"{API}/requests/{{job_id}}/hourly.csv", tags=["requests"])
def request_hourly(job_id: str) -> Response:
    """Every hour of the reference year behind an assessment: production, export, envelope, curtailment."""
    try:
        rec = _finished(job_id)
    except HTTPException as exc:
        if exc.status_code == 202:
            raise HTTPException(409, "still running") from exc
        raise
    prof = rec["result"].get("profiles") or {}
    cols = [(k, name) for k, name in _HOURLY if isinstance(prof.get(k), list)]
    if not cols:
        raise HTTPException(404, "this assessment has no hourly profile")
    year = int(prof["year"])
    start = datetime(year, 1, 1)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["hour", "timestamp_utc+1", *[name for _, name in cols]])
    for h in range(len(prof[cols[0][0]])):
        w.writerow([h, (start + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M"), *[prof[k][h] for k, _ in cols]])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="kapacitet-assessment-{job_id}-{year}.csv"'})


@app.post(f"{API}/evaluate", tags=["requests"])
def evaluate_sync(body: RequestIn) -> JSONResponse:
    """Synchronous variant of /requests (returns the full result). It takes a slot of the same pool,
    waits in the same line and is refused with the same 429 `busy`; nothing is saved."""
    _check_request(body)
    ctx = _context_for(body)
    job = _submit(body, ctx, save=False)
    # the line ahead of it and its own run, each bounded by the pool's timeout
    job = POOL.wait(job["id"], JOB_TIMEOUT_S * (2 + POOL.max_queued))
    if job["status"] != "done":
        if job["status"] in ("queued", "running"):
            POOL.cancel(job["id"])
            raise HTTPException(504, f"no answer within {JOB_TIMEOUT_S * (2 + POOL.max_queued):.0f} s")
        if job["status"] == "cancelled":
            raise api_error(409, "cancelled")
        raise HTTPException(500, job.get("error", "failed"))
    out = dict(job["result"])
    POOL.forget(job["id"])       # nothing reads it again; do not keep the result for the TTL
    out["provenance"] = provenance(*_REQUEST_SOURCES, files=_request_files(body.baseline))
    return JSONResponse(json.loads(json.dumps(out, default=_json_default)))


# ---------------------------------------------------------------- the queue of decisions

class DecisionIn(BaseModel):
    assessment: str = Field(..., pattern="^[0-9a-f]{12}$", description="a saved assessment's id")
    decision: str = Field(..., pattern="^(firm|nonfirm|declined)$",
                          description="firm: connect without an envelope (only if the static study approves); "
                                      "nonfirm: connect with the verified envelope; declined: record a refusal")
    note: str = Field("", max_length=500, description="the planner's reason, kept with the decision")
    decided_by: str = Field("", max_length=120, description="who decided, as free text (a name, a role), kept "
                                                         "with the decision")
    application: str | None = Field(None, pattern="^[0-9a-f]{12}$",
                                    description="the application in review this decision answers (api/inbox.py)")


_QJOBS: dict[str, dict] = {}
QUEUE_TIMEOUT_S = 600.0


def _queue_busy() -> bool:
    """A decision is being written, by a person or by the inbox's rule: one at a time."""
    return inbox.busy() or any(j["status"] == "running" for j in _QJOBS.values())


@app.get(f"{API}/queue", tags=["queue"])
def get_queue() -> dict:
    """The decisions so far, the grid the queue started from, and whether an approval is running."""
    from ..engine import queue as qu

    q = qu.load()
    # the address is the network's, read now: the decisions keep only the house's number
    items = [{**it, "address": it.get("address") or records.address_of(it.get("cid"))} for it in q["items"]]
    return {**q, "items": items, "approved": len(qu.approved(q)), "updating": _queue_busy()}


def _run_decision(job_id: str, body: DecisionIn, rec: dict, who: dict | None = None) -> None:
    job = _QJOBS[job_id]
    with actor.acting_as(who):
        _decision(job, body, rec, who)


def _decision(job: dict, body: DecisionIn, rec: dict, who: dict | None) -> None:
    from ..engine import queue as qu

    def emit(stage: str, payload: dict) -> None:
        job["events"].append({"stage": stage, "payload": payload, "t": time.time()})

    try:
        eng = _engine()
        item = qu.decide(eng["model"], eng["hp"], eng["ev"], rec, body.decision, body.note, emit=emit,
                         decided_by=body.decided_by, actor=who)
        # the application this answers, if any, is decided with it
        inbox.link_decision(rec["id"], body.decision, float(item.get("pv_kw", 0.0) or 0.0), body.application)
        job.update(result=item, status="done", finished=time.time())
        job["events"].append({"stage": "done", "payload": {"decision": body.decision}, "t": time.time()})
    except qu.QueueError as exc:
        job.update(status="error", error=str(exc), code=409, refusal={"code": exc.code, "message": str(exc),
                                                                        "params": exc.params}, finished=time.time())
        job["events"].append({"stage": "failed", "payload": {"error": str(exc)}, "t": time.time()})
    except Exception as exc:
        log.exception("decision failed")
        job.update(status="error", error=str(exc), code=500, finished=time.time())
        job["events"].append({"stage": "failed", "payload": {"error": str(exc)}, "t": time.time()})
    finally:
        job["closed"] = True


@app.post(f"{API}/queue", tags=["queue"])
def post_decision(body: DecisionIn) -> dict:
    """Decide on a saved assessment. An approval re-verifies all weather years and updates the grid
    the next request meets; follow it with GET /queue/jobs/{id}/events."""
    from ..engine import queue as qu

    if _queue_busy():
        raise api_error(409, "queue_busy")
    rec = records.load(body.assessment)
    if rec is None:
        raise HTTPException(404, "unknown assessment")
    if body.application is not None:
        app_ = inbox.applications().get(body.application)
        if app_ is None:
            raise inbox.refusal(404, "unknown_application")
        if app_["status"] != "review" or app_["assessment"] != body.assessment:
            raise inbox.refusal(409, "not_in_review")
    try:
        qu._check(qu.load(), rec, body.decision)
    except qu.QueueError as exc:
        raise queue_refusal(409, exc) from exc
    job_id = uuid.uuid4().hex[:12]
    _QJOBS[job_id] = {"status": "running", "events": [], "result": None, "created": time.time()}
    # the signed-in approver, when sign-in is on: the decision and its seal record them
    threading.Thread(target=_run_decision, args=(job_id, body, rec, actor.current()), daemon=True).start()
    return {"id": job_id, "events": f"{API}/queue/jobs/{job_id}/events", "result": f"{API}/queue/jobs/{job_id}"}


@app.get(f"{API}/queue/jobs/{{job_id}}/events", tags=["queue"])
async def decision_events(job_id: str) -> StreamingResponse:
    if job_id not in _QJOBS:
        raise HTTPException(404, "unknown decision")
    return _stream(_QJOBS, job_id, QUEUE_TIMEOUT_S)


@app.get(f"{API}/queue/jobs/{{job_id}}", tags=["queue"])
def decision_result(job_id: str) -> JSONResponse:
    job = _QJOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown decision")
    if job["status"] == "running":
        return JSONResponse({"status": "running"}, status_code=202)
    if job["status"] == "error":
        raise HTTPException(job.get("code", 500), job.get("refusal") or job.get("error", "failed"))
    return JSONResponse(json.loads(json.dumps(job["result"], default=_json_default)))


@app.delete(f"{API}/queue", tags=["queue"])
def delete_queue() -> dict:
    """Empty the queue; the assessments stay saved."""
    from ..engine import queue as qu

    if _queue_busy():
        raise api_error(409, "queue_busy")
    qu.reset()
    return {"ok": True}


_json_default = records.json_default


# ------------------------------------------------------------------------------ web UI

if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str) -> Response:
    if path.startswith("api/"):
        raise HTTPException(404)
    dist = WEB_DIST.resolve()
    target = (WEB_DIST / path).resolve()
    # only files inside web/dist: `..` segments (also percent-encoded) must not reach the project
    if path and target.is_file() and target.is_relative_to(dist):
        return FileResponse(target)
    index = WEB_DIST / "index.html"
    if index.exists():
        # always revalidate the entry page so a rebuilt UI is picked up; assets are content-hashed
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"message": "Kapacitet+ API is running. Build the web UI with `npm run build` in web/, "
                                    "or open /docs for the API."})
