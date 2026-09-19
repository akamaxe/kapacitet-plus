"""OpenStreetMap extract via the Overpass API (buildings, streets, mapped substations)."""

from __future__ import annotations

import json
from pathlib import Path

from ..config import settings
from ..paths import raw
from .provenance import FetchError, cached, http, record

QUERY = """
[out:json][timeout:180];
(
  way["building"](around:{r},{lat},{lon});
  relation["building"](around:{r},{lat},{lon});
  way["highway"](around:{r_road},{lat},{lon});
  nwr["power"~"^(substation|transformer)$"](around:{r_sub},{lat},{lon});
);
out tags geom;
"""


def osm_path() -> Path:
    key = settings().site.key
    return raw("osm", f"overpass_{key}.json")


def fetch_osm(*, refresh: bool = False) -> Path:
    cfg = settings()
    site = cfg.site
    dest = osm_path()
    entry = f"osm_{site.key}"
    if cached(entry, dest, refresh=refresh):
        return dest
    r = int(site.osm_radius_m)
    query = QUERY.format(r=r, r_road=r + 250, r_sub=r + 400, lat=site.center_lat, lon=site.center_lon)
    last: Exception | None = None
    for endpoint in cfg.overpass_endpoints:
        try:
            resp = http("POST", endpoint, data={"data": query}, timeout=240, retries=3, backoff=10)
            payload = resp.json()
            if not payload.get("elements"):
                raise FetchError("empty Overpass result")
            dest.write_text(json.dumps(payload), encoding="utf-8")
            record(entry, source="osm", path=dest, url=endpoint, params={"query": " ".join(query.split())},
                   rows=len(payload["elements"]),
                   notes=f"OSM base timestamp {payload.get('osm3s', {}).get('timestamp_osm_base', '?')}")
            return dest
        except Exception as exc:  # try the next mirror
            last = exc
    raise FetchError(f"All Overpass endpoints failed: {last}")


def read_osm() -> dict:
    return json.loads(osm_path().read_text(encoding="utf-8"))
