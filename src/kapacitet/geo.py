"""GeoJSON helpers, shared by the API's map (api/main.py) and a study's publication (study/publish.py),
and the SWEREF 99 TM grid a study's disclosure uses (study/disclosure.py).

Coordinates are WGS84 longitude/latitude, rounded to COORD_DECIMALS; a value that is not a finite
number gives no geometry rather than a broken one. SWEREF 99 TM (EPSG:3006) is the national grid
of Sweden, in metres, so a square of it is the same size wherever it is.
"""

from __future__ import annotations

import json
import math

from .cache import memo

COORD_DECIMALS = 6        # about 0.1 m on the ground, finer than any drawing on the map
SWEREF99TM = "EPSG:3006"
WGS84 = "EPSG:4326"


def num(v) -> float | None:
    """A number JSON can carry: NaN and infinity become null."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def xy(x, y) -> list | None:
    x, y = num(x), num(y)
    return [round(x, COORD_DECIMALS), round(y, COORD_DECIMALS)] if x is not None and y is not None else None


def point(lon, lat) -> dict | None:
    p = xy(lon, lat)
    return {"type": "Point", "coordinates": p} if p else None


def house_geometry(polygon, lon, lat) -> dict | None:
    """The roof outline when there is one; an imported network may only have the service point."""
    if isinstance(polygon, str) and polygon:
        try:
            ring = [xy(p[0], p[1]) for p in json.loads(polygon)]
        except (ValueError, TypeError, IndexError):
            ring = []
        if ring and all(ring):
            return {"type": "Polygon", "coordinates": [ring]}
    return point(lon, lat)


def line_geometry(coords) -> dict | None:
    try:
        pts = json.loads(coords) if isinstance(coords, str) else coords
    except ValueError:
        return None
    pts = [xy(p[0], p[1]) for p in (pts or []) if len(p) >= 2]
    if len(pts) < 2 or not all(pts):
        return None
    return {"type": "LineString", "coordinates": pts}


def feature(geometry: dict | None, properties: dict) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def collection(features: list[dict], **members) -> dict:
    return {"type": "FeatureCollection", "features": features, **members}


# ---------------------------------------------------------------- SWEREF 99 TM

@memo(maxsize=2)
def _transformer(src: str, dst: str):
    from pyproj import Transformer

    return Transformer.from_crs(src, dst, always_xy=True)


def to_sweref(lon, lat) -> tuple[float, float] | None:
    """(easting, northing) in metres, or None without a finite position."""
    lon, lat = num(lon), num(lat)
    if lon is None or lat is None:
        return None
    e, n = _transformer(WGS84, SWEREF99TM).transform(lon, lat)
    return (float(e), float(n)) if math.isfinite(e) and math.isfinite(n) else None


def to_wgs84(e: float, n: float) -> tuple[float, float]:
    lon, lat = _transformer(SWEREF99TM, WGS84).transform(float(e), float(n))
    return float(lon), float(lat)


def square_of(e: float, n: float, size_m: int) -> tuple[int, int]:
    """The south-west corner of the size_m square of SWEREF 99 TM that holds (e, n). Squares of
    twice the size, on the same origin, hold whole squares of this one."""
    return int(math.floor(e / size_m) * size_m), int(math.floor(n / size_m) * size_m)


def square_polygon(e0: int, n0: int, size_m: int) -> dict:
    """The square as a GeoJSON polygon in WGS84 (its four corners, closed)."""
    corners = [(e0, n0), (e0 + size_m, n0), (e0 + size_m, n0 + size_m), (e0, n0 + size_m), (e0, n0)]
    return {"type": "Polygon", "coordinates": [[xy(*to_wgs84(e, n)) for e, n in corners]]}


__all__ = ["COORD_DECIMALS", "SWEREF99TM", "collection", "feature", "house_geometry", "line_geometry", "num",
           "point", "square_of", "square_polygon", "to_sweref", "to_wgs84", "xy"]
