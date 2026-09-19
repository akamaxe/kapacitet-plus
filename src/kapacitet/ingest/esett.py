"""eSett Open Data (Nordic imbalance settlement): production, consumption and load profiles.

Svk's Mimer points to eSett for production statistics; Mimer consumption data ends in 2023.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..paths import raw
from .provenance import cached, http, record

BASE = "https://api.opendata.esett.com"
MBA = {
    "SE1": "10Y1001A1001A44P",
    "SE2": "10Y1001A1001A45N",
    "SE3": "10Y1001A1001A46L",
    "SE4": "10Y1001A1001A47J",
}


def _fetch(entry: str, dest: Path, endpoint: str, params: dict, *, refresh: bool, notes: str) -> Path:
    if cached(entry, dest, refresh=refresh):
        return dest
    rows: list[dict] = []
    # One request per month keeps responses small and robust.
    start = pd.Timestamp(params.pop("start"))
    end = pd.Timestamp(params.pop("end"))
    cur = start
    while cur < end:
        nxt = min(cur + pd.offsets.MonthBegin(1), end)
        q = dict(params, start=cur.strftime("%Y-%m-%dT%H:%M:%S.000Z"), end=nxt.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        rows.extend(http("GET", f"{BASE}/{endpoint}", params=q, timeout=120).json())
        cur = nxt
    dest.write_text(json.dumps(rows), encoding="utf-8")
    record(entry, source="esett", path=dest, url=f"{BASE}/{endpoint}",
           params=dict(params, start=str(start), end=str(end)), rows=len(rows), notes=notes)
    return dest


def fetch_production(zone: str, year: int, *, refresh: bool = False) -> Path:
    dest = raw("esett", f"exp16_production_{zone}_{year}.json")
    return _fetch(f"esett_exp16_{zone}_{year}", dest, "EXP16/Aggregate",
                  {"mba": MBA[zone], "resolution": "hour",
                   "start": f"{year - 1}-12-31T23:00:00Z", "end": f"{year}-12-31T23:00:00Z"},
                  refresh=refresh, notes="Aggregated production per production-unit type, MWh per hour.")


def fetch_consumption(zone: str, year: int, *, refresh: bool = False) -> Path:
    dest = raw("esett", f"exp15_consumption_{zone}_{year}.json")
    return _fetch(f"esett_exp15_{zone}_{year}", dest, "EXP15/Aggregate",
                  {"mba": MBA[zone], "resolution": "hour",
                   "start": f"{year - 1}-12-31T23:00:00Z", "end": f"{year}-12-31T23:00:00Z"},
                  refresh=refresh, notes="Aggregated consumption (metered / profiled), MWh per hour, negative sign.")


def fetch_load_profile(zone: str, mga: str, year: int, *, refresh: bool = False) -> Path:
    dest = raw("esett", f"exp18_loadprofile_{mga}_{year}.json")
    return _fetch(f"esett_exp18_{mga}_{year}", dest, "EXP18/Aggregate",
                  {"mba": MBA[zone], "mga": mga, "resolution": "hour",
                   "start": f"{year - 1}-12-31T23:00:00Z", "end": f"{year}-12-31T23:00:00Z"},
                  refresh=refresh, notes="Load profile (profile-settled small customers) of the metering grid area.")


def read_json_table(path: Path) -> pd.DataFrame:
    df = pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
    df["ts"] = pd.to_datetime(df["timestampUTC"], utc=True)
    return df.drop_duplicates("ts").sort_values("ts").set_index("ts")
