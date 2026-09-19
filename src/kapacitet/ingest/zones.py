"""Bidding-zone polygons SE1–SE4 (GeoJSON from the MIT-licensed entsoe-py project)."""

from __future__ import annotations

import json
from pathlib import Path

from ..paths import raw
from .provenance import cached, http, record

URL = "https://raw.githubusercontent.com/EnergieID/entsoe-py/master/entsoe/geo/geojson/{zone}.geojson"
ZONES = ["SE_1", "SE_2", "SE_3", "SE_4"]


def zones_path() -> Path:
    return raw("zones", "se_bidding_zones.geojson")


def fetch_zones(*, refresh: bool = False) -> Path:
    dest = zones_path()
    if cached("zones_se", dest, refresh=refresh):
        return dest
    features = []
    for z in ZONES:
        gj = http("GET", URL.format(zone=z)).json()
        feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
        for f in feats:
            f["properties"] = {"zone": z.replace("_", "")}
            features.append(f)
    dest.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    record("zones_se", source="zones", path=dest, url=URL.format(zone="SE_{1..4}"), rows=len(features))
    return dest


def read_zones() -> dict:
    return json.loads(zones_path().read_text(encoding="utf-8"))
