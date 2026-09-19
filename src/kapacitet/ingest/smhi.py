"""SMHI open data: STRÅNG irradiance (point API) and station air temperature (metobs)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd

from ..config import settings
from ..paths import raw
from .provenance import cached, http, record

STRANG_URL = (
    "https://opendata-download-metanalys.smhi.se/api/category/strang1g/version/1/geotype/point/"
    "lon/{lon}/lat/{lat}/parameter/{param}/data.json"
)
METOBS_ARCHIVE_URL = (
    "https://opendata-download-metobs.smhi.se/api/version/1.0/parameter/{param}/station/{station}/"
    "period/corrected-archive/data.csv"
)
METOBS_LATEST_URL = (
    "https://opendata-download-metobs.smhi.se/api/version/1.0/parameter/{param}/station/{station}/"
    "period/{period}/data.json"
)


def strang_path(param: int, year: int) -> Path:
    return raw("smhi", f"strang_{settings().site.key}_p{param}_{year}.json")


def fetch_strang(param: int, year: int, *, refresh: bool = False) -> Path:
    """One call returns a full year of hourly values at the neighbourhood's coordinates.

    The window starts on 31 Dec of the previous year so the UTC+1 study year is fully covered.
    """
    site = settings().site
    dest = strang_path(param, year)
    entry = f"smhi_strang_{site.key}_p{param}_{year}"
    if cached(entry, dest, refresh=refresh):
        return dest
    url = STRANG_URL.format(lon=round(site.center_lon, 4), lat=round(site.center_lat, 4), param=param)
    params = {"from": f"{year - 1}-12-31", "to": f"{year + 1}-01-01", "interval": "hourly"}
    resp = http("GET", url, params=params)
    data = resp.json()
    if not isinstance(data, list) or not data:
        raise ValueError(f"Unexpected STRÅNG response for param {param} {year}")
    dest.write_text(json.dumps(data), encoding="utf-8")
    record(entry, source="smhi_strang", path=dest, url=url, params=params, rows=len(data),
           notes="Hourly values at the timestamp (instantaneous); converted to hour-beginning means.")
    return dest


def read_strang(param: int, year: int) -> pd.Series:
    data = json.loads(strang_path(param, year).read_text(encoding="utf-8"))
    idx = pd.to_datetime([d["date_time"] for d in data], utc=True)
    vals = pd.to_numeric(pd.Series([d["value"] for d in data], index=idx), errors="coerce")
    return vals.clip(lower=0.0).astype(float)


def metobs_path(station: int, param: int = 1) -> Path:
    return raw("smhi", f"metobs_s{station}_p{param}_corrected.csv")


def fetch_metobs(station: int, param: int = 1, *, refresh: bool = False) -> Path:
    dest = metobs_path(station, param)
    entry = f"smhi_metobs_s{station}_p{param}"
    if cached(entry, dest, refresh=refresh):
        return dest
    url = METOBS_ARCHIVE_URL.format(param=param, station=station)
    resp = http("GET", url, timeout=180)
    dest.write_bytes(resp.content)
    rows = resp.content.count(b"\n")
    record(entry, source="smhi_metobs", path=dest, url=url, rows=rows,
           notes="Corrected archive (quality-controlled). Air temperature, instantaneous value once per hour, UTC.")
    return dest


def read_metobs(station: int, param: int = 1) -> pd.Series:
    """Parse the semicolon CSV (Swedish headers). Returns a UTC-indexed float series."""
    text = metobs_path(station, param).read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Datum;Tid"))
    body = "\n".join(line.split(";;")[0] for line in lines[start:])
    df = pd.read_csv(io.StringIO(body), sep=";", usecols=[0, 1, 2, 3], dtype=str)
    df.columns = ["date", "time", "value", "quality"]
    ts = pd.to_datetime(df["date"] + " " + df["time"], utc=True, errors="coerce")
    vals = pd.to_numeric(df["value"], errors="coerce")
    s = pd.Series(vals.to_numpy(), index=ts).dropna()
    s = s[~s.index.duplicated(keep="first")].sort_index()
    return s.astype(float)


def fetch_latest(station: int, param: int = 1, period: str = "latest-hour") -> dict:
    """Most recent observation (used by the live 'now' strip; not cached)."""
    url = METOBS_LATEST_URL.format(param=param, station=station, period=period)
    return http("GET", url, timeout=20, retries=2, backoff=1).json()
