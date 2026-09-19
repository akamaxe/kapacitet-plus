"""Kapacitet+ API (FastAPI). Run with: python -m kapacitet serve  →  http://127.0.0.1:8000

Every JSON response that carries results includes a `provenance` block (run id, data sources,
synthetic parts). OpenAPI documentation is served at /docs.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import threading
import time
import uuid
import warnings
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
from ..ingest.provenance import load_manifest
from ..ingest.sources import SOURCES
from ..paths import PROCESSED, WEB_DIST, processed

warnings.filterwarnings("ignore")
log = logging.getLogger("kapacitet.api")

app = FastAPI(
    title="Kapacitet+ API",
    version=__version__,
    description="Digital twin of a Swedish LV neighbourhood: static hosting capacity vs Dynamic Operating "
                "Envelopes. KTH EI2600 course project. All results are simulations on a synthetic network "
                "built on real OpenStreetMap geometry and driven by real SMHI weather and SE3 prices.",
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

API = "/api/v1"


# ------------------------------------------------------------------------------ helpers

def _read(name: str) -> Any:
    path = processed("results", name)
    if not path.exists():
        raise HTTPException(404, f"{name} not computed yet — run `python -m kapacitet compute`")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=64)
def _cached(name: str, mtime: float) -> Any:
    return _read(name)


def result(name: str) -> Any:
    path = processed("results", name)
    if not path.exists():
        raise HTTPException(404, f"{name} not computed yet — run `python -m kapacitet compute`")
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

@app.get(f"{API}/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "version": __version__, "time": datetime.now(timezone.utc).isoformat(),
            "results_ready": processed("results", "showcase.json").exists()}


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

    from ..engine.scenario import customers
    cust = customers()
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


@app.get(f"{API}/state/{{name}}", tags=["twin"])
def state_header(name: str) -> dict:
    path = processed("ui", f"state_{name}.json")
    if not path.exists():
        raise HTTPException(404, f"unknown state {name}")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def _state_gz(name: str, stamp: float) -> bytes:
    """Gzip the uint8 grid state once per file version (4 MB raw, ~1.8 MB compressed)."""
    return gzip.compress(processed("ui", f"state_{name}.bin").read_bytes(), 6)


@app.get(f"{API}/state/{{name}}/bin", tags=["twin"])
def state_bin(name: str, request: Request) -> Response:
    path = processed("ui", f"state_{name}.bin")
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
    for name in ("tornado.json",):
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
    baseline: str = Field("static", pattern="^(static|doe)$",
                          description="static: today's approved grid; doe: the DOE scenario")


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


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_warm, daemon=True).start()


def _run_job(job_id: str, body: RequestIn) -> None:
    from ..engine import request as rq
    job = _JOBS[job_id]

    def emit(stage: str, payload: dict) -> None:
        job["events"].append({"stage": stage, "payload": payload, "t": time.time()})

    try:
        eng = _engine()
        req = rq.Request(cid=body.cid, pv_kwp=body.pv_kwp, ev_kw=body.ev_kw, baseline=body.baseline)
        job["result"] = rq.evaluate(eng["model"], req, eng["hp"], eng["ev"], emit=emit)
        job["status"] = "done"
    except Exception as exc:
        log.exception("request failed")
        job["status"] = "error"
        job["error"] = str(exc)
        job["events"].append({"stage": "error", "payload": {"error": str(exc)}, "t": time.time()})


@app.post(f"{API}/requests", tags=["requests"])
def create_request(body: RequestIn) -> dict:
    """Start a connection-request evaluation; follow it with GET /requests/{id}/events (SSE)."""
    from ..engine.scenario import customers
    if body.cid >= len(customers()):
        raise HTTPException(422, "unknown customer")
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"status": "running", "events": [], "result": None, "created": time.time(),
                     "request": body.model_dump()}
    threading.Thread(target=_run_job, args=(job_id, body), daemon=True).start()
    return {"id": job_id, "events": f"{API}/requests/{job_id}/events", "result": f"{API}/requests/{job_id}"}


@app.get(f"{API}/requests/{{job_id}}/events", tags=["requests"])
async def request_events(job_id: str) -> StreamingResponse:
    if job_id not in _JOBS:
        raise HTTPException(404, "unknown request")

    async def stream():
        sent = 0
        start = time.time()
        while True:
            job = _JOBS[job_id]
            while sent < len(job["events"]):
                ev = job["events"][sent]
                sent += 1
                yield f"event: {ev['stage']}\ndata: {json.dumps(ev['payload'], default=_json_default)}\n\n"
            if job["status"] != "running" and sent >= len(job["events"]):
                yield "event: end\ndata: {}\n\n"
                return
            if time.time() - start > 120:
                yield "event: timeout\ndata: {}\n\n"
                return
            await asyncio.sleep(0.05)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get(f"{API}/requests/{{job_id}}", tags=["requests"])
def request_result(job_id: str) -> JSONResponse:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown request")
    if job["status"] == "running":
        return JSONResponse({"status": "running"}, status_code=202)
    if job["status"] == "error":
        raise HTTPException(500, job.get("error", "failed"))
    body = dict(job["result"])
    body["provenance"] = provenance("smhi_strang", "smhi_metobs", "elprisetjustnu", "osm", "synthetic_grid")
    return JSONResponse(json.loads(json.dumps(body, default=_json_default)))


@app.post(f"{API}/evaluate", tags=["requests"])
def evaluate_sync(body: RequestIn) -> JSONResponse:
    """Synchronous variant of /requests (returns the full result)."""
    from ..engine import request as rq
    eng = _engine()
    req = rq.Request(cid=body.cid, pv_kwp=body.pv_kwp, ev_kw=body.ev_kw, baseline=body.baseline)
    out = rq.evaluate(eng["model"], req, eng["hp"], eng["ev"])
    out["provenance"] = provenance("smhi_strang", "smhi_metobs", "elprisetjustnu", "osm", "synthetic_grid")
    return JSONResponse(json.loads(json.dumps(out, default=_json_default)))


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
    target = WEB_DIST / path
    if path and target.is_file():
        return FileResponse(target)
    index = WEB_DIST / "index.html"
    if index.exists():
        # always revalidate the entry page so a rebuilt UI is picked up; assets are content-hashed
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"message": "Kapacitet+ API is running. Build the web UI with `npm run build` in web/, "
                                    "or open /docs for the API."})
