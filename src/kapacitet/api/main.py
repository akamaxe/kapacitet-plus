"""Kapacitet+ API (FastAPI). Run with: python -m kapacitet serve  →  http://127.0.0.1:8000

Every JSON response that carries results includes a `provenance` block (run id, data sources,
synthetic parts). OpenAPI documentation is served at /docs.
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import io
import json
import math
import logging
import os
import threading
import time
import uuid
import warnings
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..config import assumptions, settings
from . import records
from .. import seals
from ..ingest.provenance import load_manifest
from ..ingest.sources import SOURCES
from ..paths import PROCESSED, RAW, WEB_DIST
from ..paths import processed_ro as processed

# The engine's libraries announce API changes on every call; the server log is for requests and
# failures, so those two categories are quietened and everything else still shows.
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
log = logging.getLogger("kapacitet.api")

# Live connection requests: how many may run at once, how long one may take, and how long a
# finished one stays in memory (it is also saved to disk, see the requests section).
MAX_JOBS = int(os.environ.get("KAPACITET_MAX_JOBS", "4"))
JOB_TIMEOUT_S = float(os.environ.get("KAPACITET_JOB_TIMEOUT_S", "120"))
JOB_TTL_S = 15 * 60
# Largest CSV accepted per table by the network-import check.
CHECK_MAX_BYTES = 5_000_000


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # load the model and the request baselines in the background, so the first request is fast
    threading.Thread(target=_warm, daemon=True).start()
    # applications left waiting when the server last stopped are taken up again, in order
    inbox.start()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Kapacitet+ API",
    version=__version__,
    description="Digital twin of a Swedish LV neighbourhood: static hosting capacity vs Dynamic Operating "
                "Envelopes. All results are simulations on a synthetic network "
                "built on real OpenStreetMap geometry and driven by real SMHI weather and SE3 prices.",
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

from .combos import BASIS_PATTERN, POLICY_PATTERN, resolve  # noqa: E402 - the routers come after the app
from . import inbox  # noqa: E402
from .apply import router as apply_router  # noqa: E402
from .datasets import router as datasets_router  # noqa: E402
from .evidence import router as evidence_router  # noqa: E402
from .operation import router as operation_router  # noqa: E402

app.include_router(evidence_router)
app.include_router(operation_router)
app.include_router(inbox.router)
app.include_router(apply_router)
app.include_router(datasets_router)
# The interface is served from the same origin, so cross-origin access is off unless an operator
# lists the origins that may call the API (comma-separated).
_CORS = [o.strip() for o in os.environ.get("KAPACITET_CORS_ORIGINS", "").split(",") if o.strip()]
if _CORS:
    app.add_middleware(CORSMiddleware, allow_origins=_CORS, allow_methods=["GET", "POST"], allow_headers=["*"])

API = "/api/v1"


# ------------------------------------------------------------------------------ helpers

# Which command writes each results file, so a missing one can say exactly what to run.
_WRITTEN_BY = {
    "national.json": "compute --stage extra --only national",
    "scenarios.json": "compute --stage extra --only scenarios",
    "tornado.json": "compute --stage extra --only tornado",
    "foresight.json": "compute --stage extra --only foresight",
    "overinstallation.json": "compute --stage extra --only overinstallation",
    "phases.json": "compute --stage extra --only phases",
    "validation.json": "compute --stage extra --only validation",
    "forecast.json": "forecast",
    "grid/customers.parquet": "build",
    "protection.json": "compute --stage extra --only protection",
    "operation/index.json": "compute --stage extra --only operation",
}


# Refusals the interface translates: `{code, message, params}` in the detail, `err.<code>` in
# web/src/lib/i18n. tests/test_messages.py holds the three languages to these templates.
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
    "bad_phases": "a charger is single-phase (1) or three-phase (3)",
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


@lru_cache(maxsize=64)
def _cached(name: str, mtime: float) -> Any:
    return _read(name)


def result(name: str) -> Any:
    path = processed("results", name)
    if not path.exists():
        raise not_computed(name)
    return _cached(name, path.stat().st_mtime)


def provenance(*sources: str) -> dict:
    meta = processed("results", "meta.json")
    run = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    return {
        "run_id": run.get("computed_at"),
        "sources": [SOURCES[s].attribution for s in sources if s in SOURCES],
        "synthetic": ["LV network topology, cables, cabinets, transformer and fuses",
                      "household load (CREST/richardsonpy), heat pumps, EV charging, PV sizing"],
    }


def with_prov(obj: Any, *sources: str) -> dict:
    return {"data": obj, "provenance": provenance(*sources)}


# ------------------------------------------------------------------------------ basics

# What each part of the interface needs on disk; /health reports which parts are ready.
_NEEDS = {
    "network": [("grid", "customers.parquet"), ("grid", "lines.parquet"), ("grid", "substation.json")],
    "results": [("results", "meta.json"), ("results", "showcase.json"), ("results", "hosting_summary.json"),
                ("results", "risk.json"), ("ui", "state_doe.json")],
    "extra": [("results", "scenarios.json"), ("results", "validation.json"), ("results", "national.json")],
    "requests": [("request", "static.json"), ("request", "doe.json")],
}


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
            "results_ready": parts["results"]["ready"], "parts": parts, "dataset": ds.current()}


@lru_cache(maxsize=2)
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
    from ..importer.contract import TABLES, as_markdown

    return {
        "markdown": as_markdown(),
        "tables": [
            {"name": t.name, "description": t.description, "key": t.key,
             "columns": [c.__dict__ for c in t.columns]}
            for t in TABLES
        ],
    }


@lru_cache(maxsize=1)
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
    return {"files": _example(), "provenance": provenance("osm", "synthetic_grid")}


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
    stamp = tuple(p.stat().st_mtime for p in (processed("grid", "customers.parquet"), processed("results", "showcase.json"),
                                               processed("results", "nodal.json")) if p.exists())
    return _network(stamp)


def _num(v) -> float | None:
    """A number JSON can carry: NaN and infinity become null."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _point(lon, lat) -> dict | None:
    x, y = _num(lon), _num(lat)
    return {"type": "Point", "coordinates": [x, y]} if x is not None and y is not None else None


def _house_geometry(polygon, lon, lat) -> dict | None:
    """The roof outline when there is one; an imported network may only have the service point."""
    if isinstance(polygon, str) and polygon:
        try:
            return {"type": "Polygon", "coordinates": [json.loads(polygon)]}
        except ValueError:
            pass
    return _point(lon, lat)


def _line_geometry(coords) -> dict | None:
    try:
        pts = json.loads(coords) if isinstance(coords, str) else coords
    except ValueError:
        return None
    pts = [[_num(p[0]), _num(p[1])] for p in (pts or []) if len(p) >= 2]
    if len(pts) < 2 or any(c is None for p in pts for c in p):
        return None
    return {"type": "LineString", "coordinates": pts}


@lru_cache(maxsize=2)
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
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [c["polygon"]]},
                      "properties": {"layer": "context", "building": c["building"]}})
    lons = cust["lon"].to_numpy(dtype=float)
    lats = cust["lat"].to_numpy(dtype=float)
    ok = np.isfinite(lons) & np.isfinite(lats)
    # a network without coordinates has no box to frame: the map says so instead of guessing
    bbox = [float(lons[ok].min()), float(lats[ok].min()), float(lons[ok].max()), float(lats[ok].max())] if ok.any() else None
    return {"type": "FeatureCollection", "features": feats,
            "bbox": bbox,
            "bus_order": buses["bus"].tolist(), "provenance": provenance("osm", "synthetic_grid")}


@app.get(f"{API}/zones", tags=["national"])
def zones() -> Response:
    path = RAW / "zones" / "se_bidding_zones.geojson"
    if not path.exists():
        raise HTTPException(404, "zones not fetched")
    return Response(path.read_bytes(), media_type="application/geo+json")


# ------------------------------------------------------------------------------ results

@app.get(f"{API}/national", tags=["national"])
def national() -> dict:
    return with_prov(result("national.json"), "esett", "elprisetjustnu", "zones", "capacity_maps")


@app.get(f"{API}/series/{{year}}", tags=["twin"])
def series(year: int) -> dict:
    return with_prov(result(f"series_{year}.json"), "smhi_strang", "smhi_metobs", "elprisetjustnu")


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


@lru_cache(maxsize=12)
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
    out = {"summary": result("hosting_summary.json"), "curve": result("zcurve.json"), "nodal": result("nodal.json")}
    for name in ("tornado.json", "foresight.json"):
        if processed("results", name).exists():
            out[name.split(".")[0]] = result(name)
    return with_prov(out, "smhi_strang", "smhi_metobs", "osm", "synthetic_grid")


@app.get(f"{API}/showcase", tags=["doe"])
def showcase() -> dict:
    return with_prov({"showcase": result("showcase.json"), "heatmaps": result("heatmaps.json")},
                     "smhi_strang", "smhi_metobs", "elprisetjustnu", "osm", "synthetic_grid")


@app.get(f"{API}/risk", tags=["doe"])
def risk() -> dict:
    return with_prov(result("risk.json"), "smhi_strang", "smhi_metobs")


@app.get(f"{API}/scenarios", tags=["scenarios"])
def scenarios() -> dict:
    return with_prov(result("scenarios.json"), "smhi_strang", "smhi_metobs", "scb")


@app.get(f"{API}/validation", tags=["provenance"])
def validation() -> dict:
    return with_prov(result("validation.json"), "smhi_strang", "smhi_metobs", "esett", "scb")


@app.get(f"{API}/forecast", tags=["doe"])
def forecast_schemes() -> dict:
    """The published envelopes against three day-ahead schemes: what perfect foresight is worth."""
    return with_prov(result("forecast.json"), "smhi_strang", "smhi_metobs", "synthetic_grid")


@app.get(f"{API}/protection", tags=["hosting"])
def protection() -> dict:
    """Whether a fault at the far end of each feeder, and at each house, blows its fuse in time."""
    return with_prov(result("protection.json"), "osm", "synthetic_grid")


@app.get(f"{API}/phases", tags=["hosting"])
def phases() -> dict:
    """Static capacity when single-phase inverters and unknown phases are modelled."""
    return with_prov(result("phases.json"), "osm", "synthetic_grid")


@app.get(f"{API}/overinstallation", tags=["hosting"])
def overinstallation() -> dict:
    """Spot value delivered and curtailed as solar grows past the static limit, and the last kW."""
    from ..engine.summaries import overinstallation_summary

    return with_prov(overinstallation_summary(result("overinstallation.json")),
                     "smhi_strang", "smhi_metobs", "elprisetjustnu", "synthetic_grid")


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
                     "smhi_strang", "smhi_metobs", "elprisetjustnu", "synthetic_grid")


@app.get(f"{API}/business", tags=["hosting"])
def business(years: int | None = Query(None, ge=1, le=40)) -> dict:
    """The DSO's case for deferring the reinforcement with envelopes: what deferring is worth at Ei's
    real rate against what the curtailment costs the houses, for every horizon up to 20 years."""
    from .. import dso
    from ..config import A
    from ..engine.summaries import business as case

    horizon = years or dso.load().deferral_years
    reinf = reinforcement()["data"]
    return with_prov(case(reinf, A("ei_wacc_real_pct"), horizon), "elprisetjustnu", "synthetic_grid")


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
    return with_prov(rep, "smhi_strang", "smhi_metobs", "osm", "synthetic_grid")


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


@lru_cache(maxsize=4)
def _audit_table(path: str, stamp: float) -> tuple[list[str], list[dict]]:
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _audit(cid: int | None, basis: str | None = None, policy: str | None = None) -> tuple[list[str], list[dict]]:
    path = resolve(basis, policy).result("audit_trail.csv")
    if not path.exists():
        raise not_computed("audit_trail.csv")
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
            "provenance": provenance("smhi_strang", "osm", "synthetic_grid")}


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
    if cid not in cids:
        return {"cid": cid, "nonfirm": False, "basis": combo.basis, "policy": combo.policy}
    env = np.load(combo.envelopes, mmap_mode="r")
    row = np.asarray(env[cids.index(cid)], dtype=float)
    return {"cid": cid, "nonfirm": True, "basis": combo.basis, "policy": combo.policy,
            "envelope_kw": np.round(row, 2).tolist()}


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
_JOBS: dict[str, dict] = {}


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


def _run_job(job_id: str, body: RequestIn, ctx=None) -> None:
    from ..engine import request as rq
    job = _JOBS[job_id]

    held: list[dict] = []

    def emit(stage: str, payload: dict) -> None:
        ev = {"stage": stage, "payload": payload, "t": time.time()}
        # `done` tells the browser to fetch the result, so it waits until the result is stored
        (held if stage == "done" else job["events"]).append(ev)

    try:
        eng = _engine()
        result = rq.evaluate(eng["model"], body.engine_request(), eng["hp"], eng["ev"], emit=emit, ctx=ctx)
        if job["status"] != "running":   # gave up on by the watchdog: the late answer is dropped
            return
        job["result"] = result
        try:
            records.save(_record(job_id, job, result))
        except Exception:   # the answer still reaches the screen; only its saved copy is lost
            log.exception("could not save assessment %s", job_id)
        job["status"] = "done"
        job["finished"] = time.time()
        job["events"].extend(held)
    except Exception as exc:
        log.exception("request failed")
        job["status"] = "error"
        job["finished"] = time.time()
        job["error"] = str(exc)
        # not "error": EventSource fires its own `error` event for transport trouble, and
        # a stage of the same name is indistinguishable from the stream simply dropping.
        job["events"].append({"stage": "failed", "payload": {"error": str(exc)}, "t": time.time()})
    finally:
        # the stream ends only once every event, the last included, has been queued
        job["closed"] = True


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
    now = time.time()
    for jid, job in list(_JOBS.items()):
        if job["status"] == "running" and now - job["created"] > JOB_TIMEOUT_S:
            job["status"] = "error"
            job["error"] = f"timed out after {JOB_TIMEOUT_S:.0f} s"
            job["events"].append({"stage": "failed", "payload": {"error": job["error"]}, "t": now})
            job["closed"] = True
        elif job["status"] != "running" and now - job.get("finished", job["created"]) > JOB_TTL_S:
            _JOBS.pop(jid, None)


_REQUEST_SOURCES = ("smhi_strang", "smhi_metobs", "elprisetjustnu", "osm", "synthetic_grid")


def make_record(job_id: str, created: float, request: dict, result: dict) -> dict:
    """What is kept of an assessment: the question, the full answer and what it was computed with."""
    return {"id": job_id, "created": datetime.fromtimestamp(created, timezone.utc).isoformat(),
            "request": request, "result": json.loads(json.dumps(result, default=_json_default)),
            "version": __version__, "provenance": provenance(*_REQUEST_SOURCES)}


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
def create_request(body: RequestIn) -> dict:
    """Start a connection-request evaluation; follow it with GET /requests/{id}/events (SSE)."""
    _check_request(body)
    ctx = _context_for(body)
    _sweep_jobs()
    if sum(j["status"] == "running" for j in _JOBS.values()) >= MAX_JOBS:
        raise api_error(429, "busy", n=MAX_JOBS)
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"status": "running", "events": [], "result": None, "created": time.time(),
                     "request": body.model_dump()}
    threading.Thread(target=_run_job, args=(job_id, body, ctx), daemon=True).start()
    return {"id": job_id, "events": f"{API}/requests/{job_id}/events", "result": f"{API}/requests/{job_id}"}


@app.get(f"{API}/requests/{{job_id}}/events", tags=["requests"])
async def request_events(job_id: str) -> StreamingResponse:
    if _job_id(job_id) not in _JOBS:
        raise HTTPException(404, "unknown request")
    return _stream(_JOBS, job_id, JOB_TIMEOUT_S)


def _stream(jobs: dict, job_id: str, timeout_s: float) -> StreamingResponse:
    """Server-sent events for a background job: every stage as it finishes, then `end`."""

    async def stream():
        sent = 0
        start = time.time()
        while True:
            job = jobs[job_id]
            while sent < len(job["events"]):
                ev = job["events"][sent]
                sent += 1
                yield f"event: {ev['stage']}\ndata: {json.dumps(ev['payload'], default=_json_default)}\n\n"
            if job.get("closed") and sent >= len(job["events"]):
                yield "event: end\ndata: {}\n\n"
                return
            if time.time() - start > timeout_s:
                yield "event: timeout\ndata: {}\n\n"
                return
            await asyncio.sleep(0.05)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _finished(job_id: str) -> dict:
    """A finished assessment, from memory while it is fresh and from disk afterwards."""
    _sweep_jobs()
    job = _JOBS.get(_job_id(job_id))
    if job is not None:
        if job["status"] == "running":
            raise HTTPException(202, "running")
        if job["status"] == "error":
            raise HTTPException(500, job.get("error", "failed"))
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
            return JSONResponse({"status": "running"}, status_code=202)
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
    """Synchronous variant of /requests (returns the full result)."""
    from ..engine import request as rq
    _check_request(body)
    ctx = _context_for(body)
    eng = _engine()
    out = rq.evaluate(eng["model"], body.engine_request(), eng["hp"], eng["ev"], ctx=ctx)
    out["provenance"] = provenance("smhi_strang", "smhi_metobs", "elprisetjustnu", "osm", "synthetic_grid")
    return JSONResponse(json.loads(json.dumps(out, default=_json_default)))


# ---------------------------------------------------------------- the queue of decisions

class DecisionIn(BaseModel):
    assessment: str = Field(..., pattern="^[0-9a-f]{12}$", description="a saved assessment's id")
    decision: str = Field(..., pattern="^(firm|nonfirm|declined)$",
                          description="firm: connect without an envelope (only if the static study approves); "
                                      "nonfirm: connect with the verified envelope; declined: record a refusal")
    note: str = Field("", max_length=500, description="the planner's reason, kept with the decision")
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
    return {**q, "approved": len(qu.approved(q)), "updating": _queue_busy()}


def _run_decision(job_id: str, body: DecisionIn, rec: dict) -> None:
    from ..engine import queue as qu
    job = _QJOBS[job_id]

    def emit(stage: str, payload: dict) -> None:
        job["events"].append({"stage": stage, "payload": payload, "t": time.time()})

    try:
        eng = _engine()
        item = qu.decide(eng["model"], eng["hp"], eng["ev"], rec, body.decision, body.note, emit=emit)
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
    threading.Thread(target=_run_decision, args=(job_id, body, rec), daemon=True).start()
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


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


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
