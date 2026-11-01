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
from ..ingest.provenance import load_manifest
from ..ingest.sources import SOURCES
from ..paths import PROCESSED, WEB_DIST
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
    "reinforcement.json": "compute --stage extra --only reinforcement",
    "forecast.json": "forecast",
}


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
    return {"status": "ok", "version": __version__, "time": datetime.now(timezone.utc).isoformat(),
            "results_ready": parts["results"]["ready"], "parts": parts}


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


@app.post(f"{API}/check", tags=["import"])
def post_check(body: ExportCheck) -> dict:
    """Data-quality report on an export, before anybody promises a capacity for it."""
    import io

    import pandas as pd

    from ..importer.contract import BY_NAME
    from ..importer.validate import validate

    unknown = set(body.files) - set(BY_NAME)
    if unknown:
        raise HTTPException(422, f"not part of the contract: {', '.join(sorted(unknown))}")
    big = [name for name, text in body.files.items() if len(text.encode("utf-8")) > CHECK_MAX_BYTES]
    if big:
        raise HTTPException(413, f"larger than {CHECK_MAX_BYTES // 1_000_000} MB: {', '.join(sorted(big))}")
    data = {}
    for name, text in body.files.items():
        try:
            data[name] = pd.read_csv(io.StringIO(text))
        except Exception as err:
            raise HTTPException(422, f"{name}.csv could not be read: {err}") from err
    return validate(data).to_dict()


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
    stamp = tuple(p.stat().st_mtime for p in (processed("grid", "customers.parquet"), processed("results", "showcase.json"),
                                               processed("results", "nodal.json")) if p.exists())
    return _network(stamp)


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
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [json.loads(c.polygon)]},
                      "properties": {
                          "layer": "house", "cid": int(c.cid), "bus": int(c.bus), "address": c.address,
                          "osm_id": c.osm_id, "building": c.building, "fuse_a": int(c.fuse_a),
                          "fuse_kva": float(c.fuse_kva), "floor_m2": float(c.floor_m2), "levels": float(c.levels),
                          "kwp": float(c.kwp), "inverter_kw": float(c.inverter_kw), "azimuth": float(c.azimuth),
                          "tilt": float(c.tilt), "east_west": bool(c.east_west), "feeder": int(c.feeder),
                          "dist_m": float(c.dist_m), "lon": float(c.lon), "lat": float(c.lat),
                          "pv_status": "firm" if c.cid in firm else ("nonfirm" if c.cid in nonfirm else "none"),
                          "nodal_pv_kw": float(nodal["pv_kw"][int(c.cid)]) if nodal else None,
                          "nodal_import_kw": float(nodal["import_kw"][int(c.cid)]) if nodal else None,
                          # synthetic: who has a heat pump or an EV today (fixed seeds, engine/run.py)
                          "has_hp": bool(has_hp[int(c.cid)]), "has_ev": bool(has_ev[int(c.cid)]),
                      }})
    for ln in lines.itertuples():
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": json.loads(ln.coords)},
                      "properties": {"layer": "cable", "line": int(ln.line), "kind": ln.kind, "std_type": ln.std_type,
                                     "length_m": round(float(ln.length_m), 1), "parallel": int(ln.parallel),
                                     "feeder": int(ln.feeder), "downstream": int(ln.downstream),
                                     "from_bus": int(ln.from_bus), "to_bus": int(ln.to_bus)}})
    for b in buses.itertuples():
        if b.kind in ("cabinet", "junction"):
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [b.lon, b.lat]},
                          "properties": {"layer": b.kind, "bus": int(b.bus), "feeder": int(b.feeder),
                                         "dist_m": round(float(b.dist_m), 1)}})
    feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [sub["lon"], sub["lat"]]},
                  "properties": {"layer": "substation", **{k: v for k, v in sub.items() if k not in ("polygon",)}}})
    for c in context:
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [c["polygon"]]},
                      "properties": {"layer": "context", "building": c["building"]}})
    lons = cust["lon"].to_numpy()
    lats = cust["lat"].to_numpy()
    return {"type": "FeatureCollection", "features": feats,
            "bbox": [float(lons.min()), float(lats.min()), float(lons.max()), float(lats.max())],
            "bus_order": buses["bus"].tolist(), "provenance": provenance("osm", "synthetic_grid")}


@app.get(f"{API}/zones", tags=["national"])
def zones() -> Response:
    path = PROCESSED.parent / "raw" / "zones" / "se_bidding_zones.geojson"
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


@app.get(f"{API}/state/{{name}}", tags=["twin"])
def state_header(name: str) -> dict:
    path = processed("ui", f"state_{_state_name(name)}.json")
    if not path.exists():
        raise HTTPException(404, f"unknown state {name}")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def _state_gz(name: str, stamp: float) -> bytes:
    """Gzip the uint8 grid state once per file version (4 MB raw, ~1.8 MB compressed)."""
    return gzip.compress(processed("ui", f"state_{name}.bin").read_bytes(), 6)


@app.get(f"{API}/state/{{name}}/bin", tags=["twin"])
def state_bin(name: str, request: Request) -> Response:
    path = processed("ui", f"state_{_state_name(name)}.bin")
    if not path.exists():
        raise HTTPException(404, f"unknown state {name}")
    headers = {"Cache-Control": "max-age=600"}
    if "gzip" in request.headers.get("accept-encoding", ""):
        body = _state_gz(name, path.stat().st_mtime)
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


# ---------------------------------------------------------------- annual filing and audit trail

@app.get(f"{API}/compliance", tags=["compliance"])
def compliance_report() -> dict:
    """The annual filing for conditional connection agreements, with the worst hour's index."""
    from ..engine.summaries import hour_of_timestamp

    rep = dict(result("compliance.json"))
    worst = rep.get("worst_uncontrolled") or {}
    if worst.get("timestamp"):
        rep["worst_uncontrolled"] = {**worst, "hour": hour_of_timestamp(worst["timestamp"], rep["year"])}
    return with_prov(rep, "smhi_strang", "smhi_metobs", "osm", "synthetic_grid")


@app.get(f"{API}/compliance.txt", tags=["compliance"])
def compliance_text() -> Response:
    path = processed("results", "compliance.txt")
    if not path.exists():
        raise not_computed("compliance.txt")
    year = result("compliance.json").get("year", "")
    return Response(path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="kapacitet-filing-{year}.txt"'})


@lru_cache(maxsize=2)
def _audit_table(stamp: float) -> tuple[list[str], list[dict]]:
    with processed("results", "audit_trail.csv").open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _audit(cid: int | None) -> tuple[list[str], list[dict]]:
    path = processed("results", "audit_trail.csv")
    if not path.exists():
        raise not_computed("audit_trail.csv")
    cols, rows = _audit_table(path.stat().st_mtime)
    if cid is not None:
        rows = [r for r in rows if int(r["customer_id"]) == cid]
    return cols, rows


_AUDIT_TYPES = {"hour": int, "customer_id": int, "desired_kw": float, "granted_kw": float,
                "curtailed_kwh": float, "uncontrolled_pct": float, "rating_pct": float}


@app.get(f"{API}/audit", tags=["compliance"])
def audit(cid: int | None = Query(None, ge=0), limit: int = Query(200, ge=1, le=5000),
          offset: int = Query(0, ge=0)) -> dict:
    """Every curtailed customer-hour of the filing year, with the part of the grid that was full."""
    cols, rows = _audit(cid)
    page = [{k: _AUDIT_TYPES.get(k, str)(v) for k, v in r.items()} for r in rows[offset:offset + limit]]
    return {"columns": cols, "total": len(rows), "offset": offset, "limit": limit, "rows": page,
            "provenance": provenance("smhi_strang", "osm", "synthetic_grid")}


@app.get(f"{API}/audit.csv", tags=["compliance"])
def audit_csv(cid: int | None = Query(None, ge=0)) -> Response:
    cols, rows = _audit(cid)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    name = "kapacitet-audit-trail" + (f"-house-{cid}" if cid is not None else "") + ".csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get(f"{API}/envelopes/{{cid}}", tags=["doe"])
def envelope(cid: int) -> dict:
    idx = result("envelopes_index.json")
    if cid not in idx["cids"]:
        return {"cid": cid, "nonfirm": False}
    env = np.load(processed("ui", "envelopes_doe.npy"), mmap_mode="r")
    row = np.asarray(env[idx["cids"].index(cid)], dtype=float)
    return {"cid": cid, "nonfirm": True, "envelope_kw": np.round(row, 2).tolist()}


# ------------------------------------------------------------------------------ live requests

class RequestIn(BaseModel):
    cid: int = Field(..., ge=0, description="customer (house) index")
    pv_kwp: float = Field(10.0, ge=0, le=40, description="PV system size, kWp DC")
    ev_kw: float = Field(0.0, ge=0, le=22, description="EV charger power, kW (0 = none)")
    hp: bool = Field(False, description="add a heat pump sized to the house (not for a house that has one)")
    bat_kwh: float = Field(0.0, ge=0, le=30, description="home battery, usable kWh (needs a PV request)")
    baseline: str = Field("static", pattern="^(static|doe|queue)$",
                          description="static: today's approved grid; doe: the DOE scenario; "
                                      "queue: either of them with the requests approved in the queue")

    def engine_request(self):
        from ..engine import request as rq
        return rq.Request(cid=self.cid, pv_kwp=self.pv_kwp, ev_kw=self.ev_kw, hp=self.hp, bat_kwh=self.bat_kwh,
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
    try:
        for name in ("static", "uncontrolled", "doe"):        # compress the map payloads once
            path = processed("ui", f"state_{name}.bin")
            if path.exists():
                _state_gz(name, path.stat().st_mtime)
        eng = _engine()
        from ..engine import request as rq
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
        raise HTTPException(422, "unknown customer")


def _context_for(body: RequestIn):
    """The grid a request meets: None for a published baseline, the queue's context for `queue`."""
    if body.baseline != "queue":
        return None
    from ..engine import queue as qu
    if _queue_busy():
        raise HTTPException(409, "the queue is being updated; try again when it finishes")
    ctx = qu.context()
    if ctx is None:
        raise HTTPException(409, "the queue is empty: choose today's grid or the envelope scenario")
    if body.cid in ctx.overrides:
        raise HTTPException(422, "this house is already approved in the queue")
    return ctx


def _check_request(body: RequestIn) -> None:
    """Refuse what the engine cannot answer honestly, before any work."""
    _check_cid(body.cid)
    if body.pv_kwp <= 0 and body.ev_kw <= 0 and not body.hp:
        raise HTTPException(422, "nothing requested: ask for solar, an EV charger or a heat pump")
    if body.bat_kwh > 0 and body.pv_kwp <= 0:
        raise HTTPException(422, "a battery here stores curtailed solar, so it needs a solar request")
    if body.hp and bool(_engine()["hp"][body.cid]):
        raise HTTPException(422, "this house already has a heat pump")


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


def _record(job_id: str, job: dict, result: dict) -> dict:
    """What is kept of an assessment: the question, the full answer and what it was computed with."""
    return {"id": job_id, "created": datetime.fromtimestamp(job["created"], timezone.utc).isoformat(),
            "request": job["request"], "result": json.loads(json.dumps(result, default=_json_default)),
            "version": __version__, "provenance": provenance(*_REQUEST_SOURCES)}


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
        raise HTTPException(429, f"busy: {MAX_JOBS} requests are already being evaluated, try again shortly")
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
    return JSONResponse(body)


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


_QJOBS: dict[str, dict] = {}
QUEUE_TIMEOUT_S = 600.0


def _queue_busy() -> bool:
    return any(j["status"] == "running" for j in _QJOBS.values())


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
        job.update(result=item, status="done", finished=time.time())
        job["events"].append({"stage": "done", "payload": {"decision": body.decision}, "t": time.time()})
    except qu.QueueError as exc:
        job.update(status="error", error=str(exc), code=409, finished=time.time())
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
        raise HTTPException(409, "the queue is being updated; try again when it finishes")
    rec = records.load(body.assessment)
    if rec is None:
        raise HTTPException(404, "unknown assessment")
    try:
        qu._check(qu.load(), rec, body.decision)
    except qu.QueueError as exc:
        raise HTTPException(409, str(exc)) from exc
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
        raise HTTPException(job.get("code", 500), job.get("error", "failed"))
    return JSONResponse(json.loads(json.dumps(job["result"], default=_json_default)))


@app.delete(f"{API}/queue", tags=["queue"])
def delete_queue() -> dict:
    """Empty the queue; the assessments stay saved."""
    from ..engine import queue as qu

    if _queue_busy():
        raise HTTPException(409, "the queue is being updated; try again when it finishes")
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
